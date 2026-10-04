"""Optional local TLS listener, sharing Oche's application and lifecycle."""

import asyncio
from contextlib import contextmanager, suppress
import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile

import psutil
import uvicorn

from app.config import DATA_DIR, load_config, save_config
from app.services.dns_certificate import DNSCertificates, bundle_path

logger = logging.getLogger(__name__)


def https_port():
    try:
        port = int(os.environ.get("OCHE_HTTPS_PORT", "443"))
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    raise RuntimeError("OCHE_HTTPS_PORT must be between 1 and 65535.")


def certificate_names(hostname):
    names = {"DNS:localhost", "IP:127.0.0.1", "IP:::1"}
    hosts = {hostname, socket.gethostname()}
    for addresses in psutil.net_if_addrs().values():
        hosts.update(address.address.split("%")[0] for address in addresses
                     if address.family in (socket.AF_INET, socket.AF_INET6))
    for host in hosts:
        if not host:
            continue
        try:
            names.add("IP:" + str(ipaddress.ip_address(host)))
        except ValueError:
            # Never allow request headers to add OpenSSL configuration syntax.
            if len(host) <= 253 and all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                                        for label in host.rstrip(".").split(".")):
                names.add("DNS:" + host.rstrip("."))
    return ",".join(sorted(names))


def ensure_certificate(hostname=None):
    """Keep one private PEM across restarts and toggles; renew near expiry."""
    executable = shutil.which("openssl")
    if executable is None:
        raise RuntimeError("OpenSSL is missing. Update the Oche image to enable local HTTPS.")
    folder = DATA_DIR / "https"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle = folder / "local.pem"

    def openssl(*args):
        return subprocess.run([executable, *map(str, args)], capture_output=True,
                              timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        if bundle.exists():
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(bundle)
                if openssl("x509", "-in", bundle, "-checkend", "604800", "-noout").returncode == 0:
                    return bundle
            except (ssl.SSLError, OSError):
                pass
        # The key and certificate are replaced together, so interrupted generation
        # cannot leave a mismatched pair or overwrite a working certificate.
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            key, cert = Path(temporary) / "key.pem", Path(temporary) / "cert.pem"
            result = openssl("req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                             "-days", "365", "-subj", "/CN=Oche local HTTPS",
                             "-addext", "subjectAltName=" + certificate_names(hostname),
                             "-keyout", key, "-out", cert)
            if result.returncode:
                raise RuntimeError("Could not generate the local HTTPS certificate.")
            combined = Path(temporary) / "local.pem"
            combined.write_bytes(cert.read_bytes() + key.read_bytes())
            combined.chmod(0o600)
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(combined)
            combined.replace(bundle)
        return bundle
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Could not create or read the HTTPS certificate in data/https.") from error


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The primary HTTP server owns process signals and the app lifespan.
        yield


class LocalHTTPS:
    def __init__(self):
        self.server = None
        self.task = None
        self.error = None
        self.lock = asyncio.Lock()
        self.acme = DNSCertificates(self)
        self.certificate = None

    @property
    def running(self):
        return bool(self.server and self.server.started and self.task and not self.task.done()
                    and not self.server.should_exit)

    async def start(self, app, hostname=None, certificate=None):
        if self.running:
            return
        await self.stop()
        port = https_port()
        if port == int(os.environ.get("OCHE_PORT", "8180")):
            raise RuntimeError("HTTPS needs a different port from OCHE_PORT (HTTP).")
        # Bind before generating a certificate; report conflicts without killing HTTP.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            sock.setblocking(False)
        except OSError as error:
            sock.close()
            raise RuntimeError(f"Cannot listen on HTTPS port {port}. Check that it is free and Oche can use it.") from error
        try:
            certificate = certificate or await asyncio.to_thread(ensure_certificate, hostname)
            self.server = LocalServer(uvicorn.Config(
                app, host="0.0.0.0", port=port, lifespan="off", ssl_certfile=str(certificate),
                timeout_graceful_shutdown=3, proxy_headers=False,
            ))
            self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("The local HTTPS server could not start.")
                    await asyncio.sleep(0.01)
            self.error = None
            self.certificate = certificate
        except BaseException:
            sock.close()
            await self.stop()
            raise

    async def stop(self):
        try:
            if self.server:
                self.server.should_exit = True
            if self.task:
                with suppress(asyncio.CancelledError):
                    await self.task
        finally:
            self.server = self.task = None

    async def use_certificate(self, app, certificate, domain=None):
        # Validate before replacing the context used for new TLS connections.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate)
        if domain:
            if not self.running:
                await self.start(app)
            # Keep the local IP's certificate stable while serving the trusted
            # certificate for its DNS name. The setup page can keep polling on IP.
            def select_certificate(connection, server_name, initial_context):
                if server_name and server_name.lower() == domain:
                    connection.context = context
            self.server.config.ssl.set_servername_callback(select_certificate)
            self.certificate = certificate
            self.error = None
            return
        if self.running:
            self.server.config.ssl.set_servername_callback(None)
            self.server.config.ssl.load_cert_chain(certificate)
            self.certificate = certificate
            self.error = None
        else:
            await self.start(app, certificate=certificate)

    async def configure(self, app, enabled, hostname=None, mode=None):
        if self.acme.busy:
            raise RuntimeError("A certificate update is in progress. Please wait for it to finish.")
        async with self.lock:
            try:
                if enabled:
                    config = load_config()
                    mode = mode or config.get("https_mode", "local")
                    if mode == "letsencrypt":
                        certificate = bundle_path(config.get("https_domain", ""))
                        if not certificate.is_file():
                            raise RuntimeError("Request a domain certificate first.")
                        await self.use_certificate(app, certificate, config.get("https_domain"))
                    else:
                        if self.running:
                            certificate = await asyncio.to_thread(ensure_certificate, hostname)
                            await self.use_certificate(app, certificate)
                        else:
                            await self.start(app, hostname)
                # Read again after certificate generation, preserving other settings
                # saved while the OpenSSL worker was running.
                config = load_config()
                config["https_enabled"] = enabled
                if mode:
                    config["https_mode"] = mode
                save_config(config)
                self.error = None
                if not enabled and self.server:
                    # Do not await our own server shutdown from an HTTPS request.
                    # Uvicorn drains this response before closing existing connections.
                    self.server.should_exit = True
            except (OSError, RuntimeError, TimeoutError) as error:
                self.error = str(error)
                if not load_config().get("https_enabled"):
                    if self.server:
                        self.server.should_exit = True
                raise RuntimeError(self.error) from error

    async def restore(self, app):
        config = load_config()
        if config.get("https_enabled"):
            try:
                if config.get("https_mode") == "letsencrypt":
                    await self.use_certificate(app, bundle_path(config.get("https_domain", "")), config.get("https_domain"))
                else:
                    await self.start(app)
            except (OSError, RuntimeError, TimeoutError) as error:
                self.error = str(error)
                logger.error("Local HTTPS unavailable: %s", error)
        self.acme.start_scheduler(app)

    async def close(self):
        await self.acme.close()
        await self.stop()
