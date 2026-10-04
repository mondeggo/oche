from typing import Literal, Optional
import ipaddress
import os
import re
import socket

from fastapi import APIRouter, HTTPException, Request
from app.templating import templates
from pydantic import BaseModel, Field, ValidationError, field_validator
from fastapi.responses import JSONResponse
import psutil

from app.config import load_config, save_config
from app.services.local_https import https_port
from app.services.dns_certificate import certificate_info

router = APIRouter(prefix="/config", tags=["config"])


class ConfigUpdate(BaseModel):
    # All optional: callers (the Settings page, and the Supervisor page's
    # inline toggles) only send the fields they're changing.
    autostart_autodarts: Optional[bool] = None
    autostart_autoglow: Optional[bool] = None
    autohide_navbar_on_play: Optional[bool] = None
    autohide_navbar_on_autodarts: Optional[bool] = None
    autohide_navbar_on_autoglow: Optional[bool] = None
    autohide_navbar_on_panels: Optional[bool] = None
    show_play_in_navbar: Optional[bool] = None
    show_autodarts_in_navbar: Optional[bool] = None
    show_autoglow_in_navbar: Optional[bool] = None
    show_panels_in_navbar: Optional[bool] = None


@router.get("")
async def page(request: Request):
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "config": load_config(), "active_nav": "config"},
    )


@router.get("/data")
async def get_data():
    return load_config()


@router.post("/data")
async def update_data(update: ConfigUpdate):
    config = load_config()
    config.update(update.model_dump(exclude_none=True))
    save_config(config)
    return config


class HTTPSUpdate(BaseModel):
    enabled: bool
    mode: Optional[Literal["local", "letsencrypt"]] = None


class DomainName(BaseModel):
    model_config = {"extra": "forbid"}
    domain: str = Field(max_length=253)

    @field_validator("domain")
    @classmethod
    def domain_name(cls, value):
        value = value.strip().rstrip(".").lower()
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("Invalid domain")
        if len(value) > 253 or "." not in value or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ) or value.endswith((".local", ".localhost", ".internal", ".test", ".invalid")):
            raise ValueError("Use a public DNS name")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        raise ValueError("Use a domain instead of an IP address")


class DomainSetup(DomainName):
    email: str = Field(max_length=254)
    local_ip: str
    terms_accepted: Literal[True]
    replace_registration: bool = False

    @field_validator("local_ip")
    @classmethod
    def local_address(cls, value):
        address = ipaddress.IPv4Address(value.strip())
        if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
            raise ValueError("Use Oche's reachable local IPv4 address")
        return str(address)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value


def local_addresses(request):
    addresses = []
    candidates = [request.url.hostname, (request.scope.get("server") or (None,))[0]]
    for interface in psutil.net_if_addrs().values():
        candidates.extend(address.address for address in interface if address.family == socket.AF_INET)
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
            if address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified):
                if str(address) not in addresses:
                    addresses.append(str(address))
        except ValueError:
            continue
    return addresses


def remember_http_port(request):
    if request.url.scheme == "http":
        config = load_config()
        config["https_http_port"] = request.url.port or 80
        save_config(config)


def https_service(request):
    service = getattr(request.app.state, "local_https", None)
    if service is None:
        raise HTTPException(status_code=503, detail="The local HTTPS service is not available.")
    return service


@router.get("/https")
async def https_page(request: Request):
    return templates.TemplateResponse("https.html", {"request": request, "active_nav": "config"})


@router.get("/https/status")
async def https_status(request: Request):
    config = load_config()
    service = getattr(request.app.state, "local_https", None)
    try:
        port, error = https_port(), service.error if service else None
    except RuntimeError as exc:
        port, error = None, str(exc)
    addresses = local_addresses(request)
    certificate = certificate_info(service.certificate) if service and service.certificate else None
    result = {
        "enabled": config.get("https_enabled", False),
        "running": bool(service and service.running),
        "port": port,
        "http_port": config.get("https_http_port", int(os.environ.get("OCHE_PORT", "8180"))),
        "error": error,
        "mode": config.get("https_mode", "local"),
        "domain": config.get("https_domain", ""),
        "local_ip": addresses[0] if addresses else "",
        "local_ips": addresses,
        "expires": certificate["expires"] if certificate else None,
        "acme_dns": service.acme.status() if service else {"available": False, "busy": False},
    }
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/https", include_in_schema=False)
@router.post("/https/status")
async def update_https(request: Request, update: HTTPSUpdate):
    service = https_service(request)
    try:
        await service.configure(request.app, update.enabled, request.url.hostname, update.mode)
        remember_http_port(request)
    except (RuntimeError, OSError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return await https_status(request)


@router.post("/https/prepare", status_code=202)
async def prepare_certificate(request: Request):
    service = https_service(request)
    try:
        setup = DomainSetup.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="Enter a valid domain, email, and local IPv4 address, and accept the Let's Encrypt terms.")
    try:
        service.acme.prepare(setup.domain, setup.email, setup.local_ip, setup.replace_registration)
        remember_http_port(request)
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error) if isinstance(error, RuntimeError) else "Could not save certificate settings.") from error
    return JSONResponse({"started": True}, status_code=202, headers={"Cache-Control": "no-store"})


@router.post("/https/certificate", status_code=202)
@router.post("/https/renew", status_code=202)
async def request_certificate(request: Request):
    try:
        setup = DomainName.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="Choose the prepared domain to verify and activate.")
    try:
        https_service(request).acme.begin(request.app, setup.domain)
        remember_http_port(request)
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error) if isinstance(error, RuntimeError) else "Could not start the certificate check.") from error
    return JSONResponse({"started": True}, status_code=202, headers={"Cache-Control": "no-store"})
