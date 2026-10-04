import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from app.services import acme_dns


class ACMEDNSTests(unittest.TestCase):
    def setUp(self):
        self.credentials = {'server': acme_dns.DEFAULT_SERVER, 'username': 'test-user',
                            'password': 'private-secret', 'subdomain': 'test-id',
                            'fulldomain': 'test-id.auth.acme-dns.io'}

    def test_register_validates_response_and_drops_unneeded_fields(self):
        with patch.object(acme_dns, 'api', return_value={**self.credentials, 'unexpected': 'ignored'}):
            self.assertEqual(acme_dns.register(), self.credentials)
        for value in ('evil.example/path', 'unrelated.auth.acme-dns.io', 'test-id.bad\n.example'):
            with patch.object(acme_dns, 'api', return_value={**self.credentials, 'fulldomain': value}):
                with self.assertRaises(RuntimeError):
                    acme_dns.register()

    def test_dns_preflight_rejects_missing_cname_wrong_ip_and_ipv6(self):
        records = {'CNAME': [self.credentials['fulldomain']], 'A': ['192.168.1.24'], 'AAAA': []}
        with patch.object(acme_dns, 'lookup', side_effect=lambda name, kind: records[kind]):
            acme_dns.verify_records('oche.example.com', '192.168.1.24', self.credentials)
            for kind, bad, message in (('CNAME', [], 'CNAME'), ('A', ['203.0.113.1'], 'A record'),
                                       ('AAAA', ['::1'], 'AAAA')):
                previous = records[kind]
                records[kind] = bad
                with self.assertRaisesRegex(RuntimeError, message):
                    acme_dns.verify_records('oche.example.com', '192.168.1.24', self.credentials)
                records[kind] = previous

    def test_publish_waits_for_dns_and_times_out_safely(self):
        validation = 'x' * 43
        with patch.object(acme_dns, 'api', return_value={'txt': validation}) as api, \
             patch.object(acme_dns, 'lookup', side_effect=[[], [validation]]), \
             patch.object(acme_dns.time, 'sleep') as sleep:
            acme_dns.publish(self.credentials, validation)
            api.assert_called_once_with(acme_dns.DEFAULT_SERVER, '/update',
                {'subdomain': 'test-id', 'txt': validation}, self.credentials)
            sleep.assert_called_once_with(5)
        with patch.object(acme_dns, 'api', return_value={'txt': validation}), \
             patch.object(acme_dns, 'lookup', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'propagate'):
                acme_dns.publish(self.credentials, validation, timeout=0)

    def test_hook_only_updates_the_registered_domain_with_valid_challenge(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'credentials.json'
            path.write_text(json.dumps({**self.credentials, 'domain': 'oche.example.com'}))
            with patch.object(acme_dns, 'publish') as publish:
                for domain, value in (('another.example.com', 'x' * 43), ('oche.example.com', 'bad')):
                    with patch.dict(os.environ, CERTBOT_DOMAIN=domain, CERTBOT_VALIDATION=value):
                        with self.assertRaises(RuntimeError):
                            acme_dns.authenticate(path)
                publish.assert_not_called()
                with patch.dict(os.environ, CERTBOT_DOMAIN='oche.example.com', CERTBOT_VALIDATION='x' * 43):
                    acme_dns.authenticate(path)
                publish.assert_called_once()

    def test_api_errors_are_sanitized_and_credentials_are_not_redirected(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://service', 401, 'private-secret', {}, None)
        with patch.object(acme_dns.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(RuntimeError) as caught:
                acme_dns.api(acme_dns.DEFAULT_SERVER, '/update', {}, self.credentials)
            self.assertNotIn('private-secret', str(caught.exception))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'POST')
            self.assertNotIn('private-secret', request.full_url)
        self.assertIsNone(acme_dns.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_custom_server_requires_https_and_no_embedded_credentials(self):
        for value in ('http://example.com', 'https://user:secret@example.com', 'https://example.com?key=secret'):
            with patch.dict(os.environ, OCHE_ACME_DNS_URL=value):
                with self.assertRaises(RuntimeError):
                    acme_dns.server_url()


if __name__ == '__main__':
    unittest.main()
