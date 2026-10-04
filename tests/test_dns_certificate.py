import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.config import load_config, save_config
from app.main import app
from app.services import dns_certificate as dns
from app.services import acme_dns
import test_local_https


@unittest.skipUnless(test_local_https.OPENSSL, "OpenSSL is required")
class DNSCertificateTests(unittest.TestCase):
    def setUp(self):
        test_local_https.LocalHTTPSTests.setUp(self)
        self.commands = []
        self.failure = False
        self.domain = "oche.example.com"
        self.token = "fake_acmedns_secret_for_tests_only_123456789"
        self.registration = {'server': acme_dns.DEFAULT_SERVER, 'username': 'test-user',
                             'password': self.token, 'subdomain': 'test-id',
                             'fulldomain': 'test-id.auth.acme-dns.io'}
        for patcher in (patch.object(acme_dns, 'register', return_value=self.registration.copy()),
                        patch.object(acme_dns, 'verify_records'), patch.object(dns, 'client_available', return_value=True)):
            mock = patcher.start()
            if patcher.attribute == 'register':
                self.register = mock
            self.addCleanup(patcher.stop)
        command = patch.object(dns.DNSCertificates, 'command', new=AsyncMock(side_effect=self.issue))
        command.start()
        self.addCleanup(command.stop)

    def pem(self, domain):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
                .sign(key, hashes.SHA256()))
        return (cert.public_bytes(serialization.Encoding.PEM), key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    async def issue(self, args):
        self.commands.append(args)
        if self.failure:
            raise RuntimeError("DNS verification failed. Check the CNAME.")
        name = args[args.index('--cert-name') + 1]
        config = Path(args[args.index('--config-dir') + 1])
        domain = next(json.loads(path.read_text())['domain'] for path in self.data.joinpath('https').glob('oche-*.json')
                      if path.stem == name)
        cert, key = self.pem(domain)
        live = config / 'live' / name
        live.mkdir(parents=True, exist_ok=True)
        (live / 'fullchain.pem').write_bytes(cert)
        (live / 'privkey.pem').write_bytes(key)
        renewal = config / 'renewal'
        renewal.mkdir(exist_ok=True)
        (renewal / (name + '.conf')).write_text('test fixture')

    def prepare(self, client, domain=None, replace=False):
        response = client.post('/config/https/prepare', json={
            'domain': domain or self.domain, 'email': 'owner@example.com',
            'local_ip': '192.168.1.24', 'terms_accepted': True, 'replace_registration': replace,
        })
        self.assertEqual(response.status_code, 202, response.text)
        self.wait_job(client)
        return client.get('/config/https/status').json()

    def wait_job(self, client):
        async def wait():
            await app.state.local_https.acme.job
        client.portal.call(wait)

    def request(self, client, domain=None):
        self.prepare(client, domain)
        response = client.post('/config/https/certificate', json={'domain': domain or self.domain})
        self.assertEqual(response.status_code, 202, response.text)
        self.wait_job(client)
        return client.get('/config/https/status').json()

    def test_prepare_from_http_is_idempotent_and_does_not_enable_https(self):
        with TestClient(app, base_url='http://192.168.1.24:8180') as client:
            status = self.prepare(client)
            self.assertFalse(status['enabled'])
            self.assertFalse(status['running'])
            self.assertTrue(status['acme_dns']['prepared'])
            self.assertEqual(status['acme_dns']['cname_target'], self.registration['fulldomain'])
            self.prepare(client)
            self.register.assert_called_once()
            self.assertEqual(self.commands, [])
        with TestClient(app) as client:
            self.prepare(client)
            self.register.assert_called_once()
            self.prepare(client, replace=True)
            self.assertEqual(self.register.call_count, 2)

    def test_registration_failure_can_be_retried_without_enabling_https(self):
        self.register.side_effect = RuntimeError('The acme-dns service returned HTTP 503. Try again later.')
        with TestClient(app) as client:
            status = self.prepare(client)
            self.assertFalse(status['acme_dns']['prepared'])
            self.assertIn('HTTP 503', status['acme_dns']['error'])
            self.assertFalse(status['enabled'])
            self.register.side_effect = None
            self.assertTrue(self.prepare(client)['acme_dns']['prepared'])

    def test_missing_cname_blocks_issuance_and_retains_current_certificate(self):
        with TestClient(app) as client:
            client.post('/config/https/status', json={'enabled': True, 'mode': 'local'})
            before = self.peer_certificate('127.0.0.1')
            with patch.object(acme_dns, 'verify_records', side_effect=RuntimeError('CNAME is not visible yet.')):
                status = self.request(client)
            self.assertIn('CNAME', status['acme_dns']['error'])
            self.assertEqual(status['mode'], 'local')
            self.assertEqual(self.peer_certificate('127.0.0.1'), before)
            self.assertEqual(self.commands, [])

    def peer_certificate(self, hostname):
        context = ssl._create_unverified_context()
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as connection:
                return connection.getpeercert(binary_form=True)

    def test_issue_and_renew_reload_domain_certificate_without_changing_ip_certificate(self):
        with TestClient(app, base_url='https://127.0.0.1') as client:
            self.assertEqual(client.post('/config/https/status', json={'enabled': True}).status_code, 200)
            local = self.peer_certificate('127.0.0.1')
            status = self.request(client)
            self.assertTrue(status['running'])
            self.assertEqual(status['mode'], 'letsencrypt')
            self.assertEqual(status['domain'], self.domain)
            self.assertIsNone(status['acme_dns']['error'])
            before = self.peer_certificate(self.domain)
            self.assertNotEqual(before, local)
            self.assertEqual(self.peer_certificate('127.0.0.1'), local)
            self.assertIn('--manual-auth-hook', self.commands[0])
            self.assertIn('app.services.acme_dns', ' '.join(self.commands[0]))
            self.assertEqual(self.commands[0][1], 'certonly')
            self.assertNotIn(self.token, ' '.join(self.commands[0]))
            status = self.request(client)
            self.assertEqual(self.commands[-1][1], 'renew')
            self.assertNotEqual(self.peer_certificate(self.domain), before)
            self.assertEqual(self.peer_certificate('127.0.0.1'), local)
            self.assertEqual(client.post('/config/https/status', json={'enabled': True, 'mode': 'local'}).status_code, 200)
            self.assertEqual(load_config()['https_mode'], 'local')
            self.assertEqual(self.peer_certificate(self.domain), local)

    def test_failed_change_keeps_active_domain_renewable_and_startup_uses_saved_certificate(self):
        with TestClient(app, base_url='https://127.0.0.1') as client:
            self.request(client)
            active = self.peer_certificate(self.domain)
            self.failure = True
            status = self.request(client, domain='new.example.com')
            self.assertIn('failed', status['acme_dns']['error'])
            self.assertEqual(load_config()['https_domain'], self.domain)
            self.assertEqual(self.peer_certificate(self.domain), active)
            self.failure = False
            client.portal.call(app.state.local_https.acme.run, app, False, self.domain)
            self.assertEqual(self.commands[-1][self.commands[-1].index('--cert-name') + 1], dns.lineage(self.domain))
            self.assertIn(self.token, dns.credentials_path(self.domain).read_text())
        count = len(self.commands)
        with TestClient(app, base_url='https://127.0.0.1') as client:
            async def wait():
                if app.state.local_https.acme.job:
                    await app.state.local_https.acme.job
            client.portal.call(wait)
            self.assertGreater(len(self.commands), count)
            self.assertEqual(self.commands[-1][1], 'renew')
            self.assertTrue(client.get('/config/https/status').json()['running'])

    def test_credentials_are_private_and_never_returned_or_echoed_on_validation_failure(self):
        with TestClient(app, base_url='https://127.0.0.1') as client:
            self.request(client)
            for url in ('/config/data', '/config/https/status', '/config/https'):
                self.assertNotIn(self.token, client.get(url).text)
            credentials = dns.credentials_path(self.domain)
            self.assertIn(self.token, credentials.read_text())
            for path in self.data.rglob('*.json'):
                if not path.name.endswith('.credentials.json'):
                    self.assertNotIn(self.token, path.read_text())
            response = client.post('/config/https/prepare', json=[{'api_token': self.token}])
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.token, response.text)
            response = client.post('/config/https/prepare', json={
                'domain': '../bad', 'email': 'owner@example.com', 'api_token': self.token, 'terms_accepted': True})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.token, response.text)

    def test_cross_origin_missing_terms_and_concurrent_requests_are_rejected(self):
        with TestClient(app, base_url='http://192.168.1.24') as client:
            response = client.post('/config/https/certificate', json={'api_token': self.token})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(client.get('/config/https/status').json()['local_ip'], '192.168.1.24')
        with TestClient(app, base_url='https://127.0.0.1') as client:
            body = {'domain': self.domain, 'email': 'owner@example.com', 'local_ip': '192.168.1.24', 'terms_accepted': False}
            self.assertEqual(client.post('/config/https/prepare', json=body).status_code, 422)
            body['terms_accepted'] = True
            self.assertEqual(client.post('/config/https/prepare', json=body,
                headers={'Origin': 'https://another.example'}).status_code, 403)
            async def hold():
                await asyncio.sleep(30)
            async def start_busy():
                app.state.local_https.acme.job = asyncio.create_task(hold())
            client.portal.call(start_busy)
            self.assertEqual(client.post('/config/https/prepare', json=body).status_code, 409)
            self.assertEqual(client.post('/config/https/status', json={'enabled': False}).status_code, 503)
        self.assertEqual(self.commands, [])

    def test_bad_certificate_is_not_activated(self):
        self.pem = lambda domain: (b'not a certificate', b'not a key')
        with TestClient(app, base_url='https://127.0.0.1') as client:
            client.post('/config/https/status', json={'enabled': True})
            before = self.peer_certificate('127.0.0.1')
            status = self.request(client)
            self.assertEqual(status['mode'], 'local')
            self.assertIsNotNone(status['acme_dns']['error'])
            self.assertEqual(self.peer_certificate('127.0.0.1'), before)


class CertificateWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_automatic_check_retries_after_one_hour(self):
        worker = dns.DNSCertificates(Mock())
        worker.run = AsyncMock()
        worker.error = 'Temporary service outage'
        config = {'https_enabled': True, 'https_mode': 'letsencrypt', 'https_domain': 'oche.example.com'}
        with patch.object(dns, 'load_config', return_value=config), \
             patch.object(dns.asyncio, 'sleep', side_effect=asyncio.CancelledError) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker.renew_loop(None)
            sleep.assert_called_once_with(3600)

    async def test_periodic_checks_only_run_for_enabled_domain_method(self):
        worker = dns.DNSCertificates(Mock())
        worker.run = AsyncMock()
        for config, expected in (({}, 0), ({'https_enabled': True, 'https_mode': 'local'}, 0),
                                 ({'https_enabled': True, 'https_mode': 'letsencrypt', 'https_domain': 'oche.example.com'}, 1)):
            worker.run.reset_mock()
            with patch.object(dns, 'load_config', return_value=config), \
                 patch.object(dns.asyncio, 'sleep', side_effect=asyncio.CancelledError) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await worker.renew_loop(None)
                self.assertEqual(worker.run.await_count, expected)
                sleep.assert_called_once_with(12 * 60 * 60)

    async def test_subprocess_errors_never_echo_provider_output(self):
        worker = dns.DNSCertificates(Mock())
        process = Mock(returncode=1)
        process.communicate = AsyncMock(return_value=(None, b'invalid token: SECRET_VALUE'))
        with patch.object(dns.asyncio, 'create_subprocess_exec', return_value=process):
            with self.assertRaises(RuntimeError) as caught:
                await worker.command(['certbot', 'certonly'])
        self.assertNotIn('SECRET_VALUE', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
