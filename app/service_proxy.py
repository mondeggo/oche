"""Fixed-loopback HTTP and WebSocket transport for bundled service interfaces."""

import asyncio
from contextlib import suppress
from urllib.parse import urljoin, urlsplit

import aiohttp
import anyio
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from yarl import URL

from app.security import same_origin

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
_ASSET_TYPES = {
    "text/html", "text/css", "text/javascript", "application/javascript",
}


def _headers(headers, *, response=False):
    blocked = _HOP_HEADERS | {
        value.strip().lower() for value in headers.get("connection", "").split(",")
    }
    if response:
        # aiohttp decodes HTTP content encodings; Starlette supplies framing.
        blocked |= {"content-length", "content-encoding"}
    else:
        blocked |= {"host", "accept-encoding", "content-length"}
    return [(key, value) for key, value in headers.items() if key.lower() not in blocked]


def _session():
    return aiohttp.ClientSession(
        cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=300),
    )


class _ProxyStream(StreamingResponse):
    def __init__(self, upstream, session):
        super().__init__(upstream.content.iter_chunked(65536), status_code=upstream.status)
        self.upstream = upstream
        self.session = session

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Release sockets on normal completion, client disconnect and shutdown.
            with anyio.CancelScope(shield=True):
                self.upstream.close()
                await self.session.close()


def create_service_proxy(prefix, port, name, rewrite_asset, *, adjust_headers=None,
                         rewrite_socket=None, rebase_origin=False):
    """Build routes for one bundled service; the target host is never user-supplied."""
    router = APIRouter(prefix=prefix, include_in_schema=False)
    def _upstream_url(scope, scheme="http") -> URL:
        # Keep the authority fixed even for encoded slashes or absolute-looking paths.
        # raw_path preserves escaped filenames, query values and trailing slashes.
        raw_path = scope.get("raw_path", scope["path"].encode()).split(b"?", 1)[0]
        if not raw_path.startswith((prefix + "/").encode()):
            raise ValueError("Use the literal service proxy path")
        path = raw_path[len(prefix):] or b"/"
        query = scope.get("query_string", b"")
        return URL(
            f"{scheme}://127.0.0.1:{port()}" + path.decode("ascii")
            + ("?" + query.decode("ascii") if query else ""),
            encoded=True,
        )


    def _location(value: str, upstream: URL) -> str:
        try:
            target = urlsplit(urljoin(str(upstream), value))
            local = target.hostname in ("127.0.0.1", "localhost", "::1") and target.port == port()
        except ValueError:
            return value
        if target.scheme in ("http", "https") and local:
            return prefix + (target.path or "/") + ("?" + target.query if target.query else "") + ("#" + target.fragment if target.fragment else "")
        return value


    @router.get("")
    async def slash(request: Request):
        return RedirectResponse(prefix + "/" + ("?" + request.url.query if request.url.query else ""), status_code=307)


    @router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(request: Request, path: str):
        session = _session()
        upstream = None
        streaming = False
        try:
            target = _upstream_url(request.scope)
            headers = _headers(request.headers)
            headers.append(("accept-encoding", "identity"))
            if rebase_origin:
                # The upstream sees this loopback HTTP hop. Inherited proxy
                # metadata could make Uvicorn infer HTTPS and reject its Origin.
                headers = [(key, value) for key, value in headers
                           if key.lower() not in {"origin", "forwarded", "x-real-ip"}
                           and not key.lower().startswith("x-forwarded-")]
                if request.headers.get("origin"):
                    headers.append(("origin", f"http://127.0.0.1:{port()}"))
            # Always fetch complete UI assets; partial or cached pre-rewrite responses
            # would leave URLs pointing outside this proxy after an Oche update.
            if not path.startswith(("api/", "ws/")):
                headers = [(key, value) for key, value in headers if key.lower() not in
                           {"range", "if-range", "if-none-match", "if-modified-since"}]
            upstream = await session.request(
                request.method, target, headers=headers, allow_redirects=False,
                data=request.stream() if request.method not in ("GET", "HEAD") else None,
            )
            response_headers = _headers(upstream.headers, response=True)
            response_headers = [(key, _location(value, target) if key.lower() == "location" else value)
                                for key, value in response_headers]
            content_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
            rewrite = (content_type in _ASSET_TYPES and not path.startswith(("api/", "ws/"))
                       and "attachment" not in upstream.headers.get("content-disposition", "").lower())
            if rewrite:
                body = rewrite_asset(await upstream.read())
                response_headers = [(key, value) for key, value in response_headers if key.lower() not in
                                    {"etag", "last-modified", "accept-ranges", "content-range", "cache-control"}]
                response_headers.append(("cache-control", "no-store"))
                response = Response(body, status_code=upstream.status)
            else:
                response = _ProxyStream(upstream, session)
            if adjust_headers:
                response_headers = adjust_headers(response_headers)
            # Preserve duplicate headers such as Set-Cookie.
            response.raw_headers = [(key.lower().encode("latin-1"), value.encode("latin-1"))
                                    for key, value in response_headers]
            streaming = isinstance(response, _ProxyStream)
            return response
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return JSONResponse({"detail": f"{name} is unavailable. Start it in Supervisor and try again."}, status_code=502)
        except ValueError:
            return JSONResponse({"detail": f"Invalid {name} path."}, status_code=400)
        finally:
            if not streaming:
                with anyio.CancelScope(shield=True):
                    if upstream is not None:
                        upstream.close()
                    await session.close()


    @router.websocket("/{path:path}")
    async def socket_proxy(websocket: WebSocket, path: str):
        scheme = "https" if websocket.url.scheme == "wss" else "http"
        if not same_origin(websocket.headers.get("origin", ""), scheme, websocket.headers.get("host", "")):
            await websocket.close(code=1008)
            return
        tasks = []
        try:
            async with _session() as session:
                protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
                async with session.ws_connect(_upstream_url(websocket.scope, "ws"), protocols=protocols,
                                              max_msg_size=16 * 1024 * 1024,
                                              origin=f"http://127.0.0.1:{port()}" if rebase_origin else None) as upstream:
                    await websocket.accept(subprotocol=upstream.protocol)

                    async def from_browser():
                        while True:
                            frame = await websocket.receive()
                            if frame["type"] == "websocket.disconnect":
                                return
                            if frame.get("text") is not None:
                                await upstream.send_str(frame["text"])
                            elif frame.get("bytes") is not None:
                                await upstream.send_bytes(frame["bytes"])

                    async def from_upstream():
                        async for frame in upstream:
                            if frame.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(rewrite_socket(path, frame.data) if rewrite_socket else frame.data)
                            elif frame.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(frame.data)
                            elif frame.type == aiohttp.WSMsgType.ERROR:
                                raise aiohttp.ClientError(f"{name} live connection closed")

                    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(from_upstream())]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
        except ValueError:
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1008)
        except (aiohttp.ClientError, asyncio.TimeoutError, WebSocketDisconnect, OSError):
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1013)
        finally:
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(WebSocketDisconnect, RuntimeError):
                    await websocket.close()

    return router
