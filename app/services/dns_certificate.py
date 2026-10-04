"""Delegated acme-dns validation and certificate renewal through Certbot."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import ssl
import subprocess
import sys
import tempfile

from app.config import DATA_DIR, load_config, save_config
from app.services import acme_dns

CHECK_INTERVAL = 12 * 60 * 60
RETRY_INTERVAL = 60 * 60
ACME_SERVER = "https://acme-v02.api.letsencrypt.org/directory"


def now():
    return datetime.now(timezone.utc).isoformat()


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        Path(temporary).chmod(0o600)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_settings():
    try:
        return json.loads((DATA_DIR / "https" / "acme-dns.json").read_text())
    except (OSError, ValueError):
        return {}


def write_settings(settings):
    private_write(DATA_DIR / "https" / "acme-dns.json", json.dumps(settings).encode())


def domain_settings(domain):
    try:
        return json.loads((DATA_DIR / "https" / (lineage(domain) + ".json")).read_text())
    except (OSError, ValueError):
        return {}


def save_domain_settings(settings):
    private_write(DATA_DIR / "https" / (lineage(settings["domain"]) + ".json"), json.dumps(settings).encode())
    if read_settings().get("domain") == settings["domain"]:
        write_settings(settings)


def credentials_path(domain):
    return DATA_DIR / "https" / (lineage(domain) + ".credentials.json")


def read_credentials(domain):
    try:
        return json.loads(credentials_path(domain).read_text())
    except (OSError, ValueError):
        return {}


def client_available():
    return shutil.which("certbot") is not None and importlib.util.find_spec("dns") is not None


def certificate_info(path):
    """Read public certificate metadata without including the private key."""
    try:
        from cryptography import x509
    except ImportError:
        # A development bind mount may load this code before the image is rebuilt.
        return None

    try:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
        domains = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return {
            "domains": domains.get_values_for_type(x509.DNSName),
            "expires": certificate.not_valid_after_utc.isoformat(),
        }
    except (OSError, ValueError, x509.ExtensionNotFound):
        return None


def lineage(domain):
    # Never use an untrusted request value as a filesystem path.
    return "oche-" + hashlib.sha256(domain.encode()).hexdigest()[:20]


def bundle_path(domain):
    return DATA_DIR / "https" / (lineage(domain) + ".pem")


class DNSCertificates:
    def __init__(self, https):
        self.https = https
        self.job = None
        self.scheduler = None
        self.next_check = None
        self.error = None
        self.phase = None

    @property
    def busy(self):
        return bool(self.job and not self.job.done())

    def status(self):
        settings = read_settings()
        domain = settings.get("domain", "")
        info = certificate_info(bundle_path(domain)) if domain else None
        credentials = read_credentials(domain) if domain else {}
        configuration_error = None
        try:
            server = credentials.get("server") or acme_dns.server_url()
        except RuntimeError as error:
            server, configuration_error = "", str(error)
        return {
            "available": client_available(),
            "domain": domain,
            "email": settings.get("email", ""),
            "local_ip": settings.get("local_ip", ""),
            "prepared": bool(credentials),
            "cname_name": "_acme-challenge." + domain if credentials else "",
            "cname_target": credentials.get("fulldomain", ""),
            "server": server,
            "busy": self.busy,
            "phase": self.phase if self.busy else None,
            "last_attempt": settings.get("last_attempt"),
            "last_success": settings.get("last_success"),
            "error": configuration_error or self.error or settings.get("error"),
            "next_check": self.next_check,
            "expires": info["expires"] if info else None,
            "renewal_error": domain_settings(load_config().get("https_domain", "")).get("error"),
        }

    def ready(self):
        if self.busy or self.https.lock.locked():
            raise RuntimeError("An HTTPS change is already in progress. Please wait for it to finish.")
        if not client_available():
            raise RuntimeError("The certificate client is missing. Update or rebuild Oche to include Certbot and DNS support.")

    def prepare(self, domain, email, local_ip, replace=False):
        self.ready()
        self.phase = "preparing"
        self.job = asyncio.create_task(self.prepare_registration(domain, email, local_ip, replace))

    async def prepare_registration(self, domain, email, local_ip, replace):
        async with self.https.lock:
            settings = domain_settings(domain)
            settings.update(domain=domain, email=email, local_ip=local_ip, error=None)
            try:
                write_settings(settings)
                credentials = read_credentials(domain)
                if not credentials or replace:
                    credentials = await asyncio.to_thread(acme_dns.register)
                    credentials["domain"] = domain
                    private_write(credentials_path(domain), json.dumps(credentials).encode())
                self.error = None
            except Exception as error:
                self.error = str(error) if isinstance(error, RuntimeError) else "Could not save the acme-dns registration. Check Oche's writable data folder."
                settings["error"] = self.error
            finally:
                with suppress(OSError):
                    save_domain_settings(settings)

    def begin(self, app, domain=None):
        self.ready()
        settings = read_settings()
        if domain != settings.get("domain") or not settings.get("email") or not read_credentials(domain):
            raise RuntimeError("Prepare this domain first, then add the DNS records shown.")
        settings["error"] = None
        save_domain_settings(settings)
        self.error = None
        self.phase = "issuing"
        self.job = asyncio.create_task(self.run(app, activate=True))

    async def command(self, args):
        # Arguments include only a credentials file path, never its contents.
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            _, output = await asyncio.wait_for(process.communicate(), timeout=600)
        except (asyncio.CancelledError, TimeoutError):
            if process.returncode is None:
                # Allow Certbot to finish its bookkeeping before exiting.
                if os.name == "nt":
                    process.terminate()
                else:
                    process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.communicate(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.communicate()
            raise
        if process.returncode:
            # Do not expose arbitrary provider output, which may contain credentials.
            message = (output or b"").decode(errors="replace").lower()
            if "rate" in message and "limit" in message:
                raise RuntimeError("Let's Encrypt rate limit reached. Wait before retrying; check the private Certbot log for the retry time.")
            if "acme-dns rejected" in message:
                raise RuntimeError("acme-dns rejected the saved registration. Check the service or prepare a replacement CNAME.")
            if any(word in message for word in ("unauthorized", "authentication", "verification", "hook")):
                raise RuntimeError("DNS verification failed. Check the CNAME, wait for DNS propagation, and retry. The acme-dns service may be temporarily unavailable.")
            raise RuntimeError("Certificate request failed. Check DNS access and the private log in data/https/acme/logs, then retry.")

    async def run(self, app, activate=False, domain=None):
        async with self.https.lock:
            settings = domain_settings(domain) if domain else read_settings()
            settings.update(last_attempt=now(), error=None)
            try:
                save_domain_settings(settings)
                domain = settings["domain"]
                credentials = read_credentials(domain)
                if not credentials:
                    raise RuntimeError("Prepare acme-dns for this domain first to enable automatic renewal.")
                if activate:
                    await asyncio.to_thread(acme_dns.verify_records, domain, settings["local_ip"], credentials)
                name = lineage(domain)
                root = DATA_DIR / "https" / "acme"
                for folder in (root, root / "config", root / "work", root / "logs"):
                    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                executable = shutil.which("certbot")
                if not executable:
                    raise RuntimeError("Certbot is missing. Rebuild the Oche image.")
                hook_args = [sys.executable, "-m", "app.services.acme_dns", str(credentials_path(domain).resolve())]
                hook = subprocess.list2cmdline(hook_args) if os.name == "nt" else shlex.join(hook_args)
                common = ["--non-interactive", "--config-dir", str(root / "config"),
                          "--work-dir", str(root / "work"), "--logs-dir", str(root / "logs"),
                          "--cert-name", name, "--server", ACME_SERVER,
                          "--authenticator", "manual", "--preferred-challenges", "dns",
                          "--manual-auth-hook", hook]
                if (root / "config" / "renewal" / (name + ".conf")).exists():
                    args = [executable, "renew", *common, "--no-random-sleep-on-renew"]
                else:
                    args = [executable, "certonly", *common, "--agree-tos", "--email", settings["email"],
                            "--domains", domain, "--keep-until-expiring"]
                await self.command(args)
                live = root / "config" / "live" / name
                certificate = live / "fullchain.pem"
                key = live / "privkey.pem"
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(certificate, key)
                info = certificate_info(certificate)
                if not info or domain not in info["domains"] or datetime.fromisoformat(info["expires"]) <= datetime.now(timezone.utc):
                    raise RuntimeError("The returned certificate is expired or does not match the configured domain.")
                bundle = bundle_path(domain)
                private_write(bundle, certificate.read_bytes() + key.read_bytes())
                config = load_config()
                if activate or (config.get("https_enabled") and config.get("https_mode") == "letsencrypt"
                                and config.get("https_domain") == domain):
                    await self.https.use_certificate(app, bundle, domain)
                    config = load_config()
                    config.update(https_enabled=True, https_mode="letsencrypt", https_domain=domain)
                    save_config(config)
                    if self.https.error:
                        self.https.error = None
                settings.update(last_success=now(), error=None)
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Only our own sanitized messages go to the browser. Raw library
                # errors and configuration contents are deliberately not returned.
                self.error = str(error) if isinstance(error, RuntimeError) else "Could not update the certificate. Check DNS access and Oche's writable data folder."
                settings["error"] = self.error
            finally:
                with suppress(OSError):
                    if settings.get("domain"):
                        save_domain_settings(settings)

    async def renew_loop(self, app):
        while True:
            config = load_config()
            if config.get("https_enabled") and config.get("https_mode") == "letsencrypt" and not self.busy:
                # Renew only the active domain; a failed attempt to change domains
                # must not stop renewal of the certificate already in use.
                if config.get("https_domain"):
                    self.phase = "renewing"
                    self.job = asyncio.create_task(self.run(app, domain=config["https_domain"]))
                    await self.job
            delay = RETRY_INTERVAL if self.error else CHECK_INTERVAL
            self.next_check = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, timezone.utc).isoformat()
            await asyncio.sleep(delay)

    def start_scheduler(self, app):
        self.scheduler = asyncio.create_task(self.renew_loop(app))

    async def close(self):
        tasks = [task for task in (self.scheduler, self.job) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
