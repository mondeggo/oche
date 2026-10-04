"""The acme-dns API and Certbot's DNS authentication hook.

Credentials stay on the Oche server. The browser only receives the public CNAME.
"""

import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


DEFAULT_SERVER = "https://auth.acme-dns.io"


def server_url():
    value = os.environ.get("OCHE_ACME_DNS_URL", DEFAULT_SERVER).rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("OCHE_ACME_DNS_URL must be an HTTPS URL without credentials, query, or fragment.")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward validation credentials to a redirected host.
        return None


def api(server, endpoint, payload, credentials=None):
    headers = {"Content-Type": "application/json", "User-Agent": "Oche HTTPS"}
    if credentials:
        headers.update({"X-Api-User": credentials["username"], "X-Api-Key": credentials["password"]})
    request = urllib.request.Request(server + endpoint, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            result = json.loads(response.read(16385))
            if not isinstance(result, dict):
                raise ValueError("Invalid response")
            return result
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise RuntimeError("acme-dns rejected the saved registration. Check the service or prepare a replacement CNAME.") from None
        raise RuntimeError(f"The acme-dns service returned HTTP {error.code}. Try again later.") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not reach the acme-dns service or read its response. Try again later.") from None


def register():
    server = server_url()
    data = api(server, "/register", {})
    for key in ("username", "password", "subdomain"):
        if not isinstance(data.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", data[key]):
            raise RuntimeError("The acme-dns service returned an invalid registration.")
    if not isinstance(data.get("fulldomain"), str):
        raise RuntimeError("The acme-dns service returned an invalid validation address.")
    name = data["fulldomain"].lower().rstrip(".")
    if (len(name) > 253 or not name.startswith(data["subdomain"].lower() + ".")
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split("."))):
        raise RuntimeError("The acme-dns service returned an invalid validation address.")
    return {"server": server, "fulldomain": name,
            **{key: data[key] for key in ("username", "password", "subdomain")}}


def lookup(name, record_type):
    # Public DNS is required for CA validation; /etc/hosts and split DNS can hide
    # a missing public record. Import lazily while an older dev image is running.
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    try:
        answer = resolver.resolve(name, record_type, lifetime=10)
        if record_type == "TXT":
            return [b"".join(item.strings).decode("ascii", errors="replace") for item in answer]
        return [item.to_text().rstrip(".").lower() for item in answer]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return []
    except dns.exception.DNSException:
        raise RuntimeError("Public DNS could not be checked. Check Oche's internet connection and retry.") from None


def verify_records(domain, local_ip, credentials):
    name = "_acme-challenge." + domain
    if lookup(name, "CNAME") != [credentials["fulldomain"]]:
        raise RuntimeError("The CNAME is not visible in public DNS yet. Copy the record shown, keep it DNS only, and retry after propagation.")
    addresses = lookup(domain, "A")
    if local_ip not in addresses or len(addresses) != 1:
        raise RuntimeError("The A record must point only to the local IPv4 address shown. Check the record and set it to DNS only.")
    if lookup(domain, "AAAA"):
        raise RuntimeError("This setup uses IPv4. Remove the AAAA record for this Oche hostname so browsers reach the correct address.")


def publish(credentials, validation, timeout=180):
    data = api(credentials["server"], "/update",
               {"subdomain": credentials["subdomain"], "txt": validation}, credentials)
    if data.get("txt") != validation:
        raise RuntimeError("The acme-dns service did not confirm the verification record.")
    deadline = time.monotonic() + timeout
    while True:
        try:
            if validation in lookup(credentials["fulldomain"], "TXT"):
                return
        except RuntimeError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("The verification TXT record did not propagate in time. Retry the certificate request.")
        time.sleep(5)


def authenticate(path):
    data = json.loads(Path(path).read_text())
    if os.environ.get("CERTBOT_DOMAIN") != data.get("domain"):
        raise RuntimeError("The certificate request does not match the saved acme-dns registration.")
    validation = os.environ.get("CERTBOT_VALIDATION", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", validation):
        raise RuntimeError("Certbot did not supply a valid verification value.")
    publish(data, validation)


if __name__ == "__main__":
    try:
        authenticate(sys.argv[1])
    except Exception as error:
        # Never include provider responses, request headers, or local file content.
        print(str(error) if isinstance(error, RuntimeError) else "Could not run the acme-dns verification hook.", file=sys.stderr)
        sys.exit(1)
