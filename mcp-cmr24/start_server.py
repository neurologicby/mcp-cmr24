"""ASGI application exposing stable compact and native MCP endpoints."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

import resources  # noqa: F401  Registers resources on both catalogues.
import tools  # noqa: F401  Registers compact and native tools.
from api_connector import close_api_client
from auth_context import authkey_var, caller_id_var, caller_scopes_var, request_id_var
from log_config import configure_logging
from mcp_server import NATIVE_MCP, SINGLE_MCP
from metrics import metrics
from settings import Settings, get_settings

settings = get_settings()
configure_logging()


def _allowed_service_request(request: Request, config: Settings) -> bool:
    if _trusted_proxy_request(request, config):
        return True
    if request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
        return any(
            address in ipaddress.ip_network(network, strict=False)
            for network in config.service_account_networks
        )
    except ValueError:
        return False


def _trusted_proxy_request(request: Request, config: Settings) -> bool:
    if not config.trusted_proxy_header or not config.trusted_proxy_value:
        return False
    supplied = request.headers.get(config.trusted_proxy_header)
    return supplied is not None and hmac.compare_digest(
        supplied, config.trusted_proxy_value
    )


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window: int, max_clients: int):
        self.limit = limit
        self.window = window
        self.max_clients = max_clients
        self._clients: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        started, count = self._clients.get(key, (now, 0))
        if now - started >= self.window:
            started, count = now, 0
        allowed = count < self.limit
        if allowed:
            count += 1
        self._clients[key] = (started, count)
        self._clients.move_to_end(key)
        while len(self._clients) > self.max_clients:
            self._clients.popitem(last=False)
        retry_after = max(1, int(self.window - (now - started)))
        return allowed, retry_after


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: Settings | None = None):
        super().__init__(app)
        self.config = config or get_settings()
        self.rate_limiter = SlidingWindowRateLimiter(
            self.config.rate_limit_requests,
            self.config.rate_limit_window,
            self.config.rate_limit_clients,
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        scheme, separator, credential = header.partition(" ")
        bearer = (
            credential.strip()
            if separator and scheme.casefold() == "bearer" and credential.strip()
            else None
        )
        if bearer and len(bearer) > 4096:
            return JSONResponse(
                {"ok": False, "code": "invalid_token", "message": "Invalid token"},
                status_code=400,
            )
        if self.config.auth_mode == "per_request" and not bearer:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "unauthorized",
                    "message": "Bearer token required",
                },
                status_code=401,
            )
        if self.config.auth_mode == "service_account" and not _allowed_service_request(
            request, self.config
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "code": "forbidden",
                    "message": "Service account mode is limited to trusted sources",
                },
                status_code=403,
            )

        trusted_identity = self.config.trust_scope_header and _trusted_proxy_request(
            request, self.config
        )
        scopes = self.config.default_scopes
        if trusted_identity:
            scopes = tuple(
                item
                for item in request.headers.get("X-CMR24-Scopes", "")
                .replace(",", " ")
                .split()
                if item
            )
        caller = request.headers.get("X-CMR24-Caller") if trusted_identity else None
        if not caller:
            caller = (
                "service-account"
                if self.config.auth_mode == "service_account"
                else "token:" + hashlib.sha256(bearer.encode()).hexdigest()[:16]
            )
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", caller):
            return JSONResponse(
                {"ok": False, "code": "invalid_caller", "message": "Invalid caller"},
                status_code=400,
            )
        if trusted_identity:
            rate_key = f"caller:{caller}"
        elif request.client is not None:
            rate_key = f"client:{request.client.host}"
        else:
            rate_key = f"caller:{caller}"
        allowed, retry_after = self.rate_limiter.allow(rate_key)
        if not allowed:
            metrics.inc("http_requests_total", result="rate_limited")
            return JSONResponse(
                {
                    "ok": False,
                    "code": "rate_limited",
                    "message": "Too many requests",
                    "retriable": True,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", request_id):
            request_id = uuid.uuid4().hex

        auth_token = authkey_var.set(
            bearer if self.config.auth_mode == "per_request" else None
        )
        caller_token = caller_id_var.set(caller)
        scopes_token = caller_scopes_var.set(frozenset(scopes))
        request_token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(request_token)
            caller_scopes_var.reset(scopes_token)
            caller_id_var.reset(caller_token)
            authkey_var.reset(auth_token)


async def health(_: Request):
    return JSONResponse({"status": "ok"})


async def readiness(_: Request):
    configured = settings.auth_mode == "per_request" or bool(settings.cmr24_authkey)
    return JSONResponse(
        {"status": "ready" if configured else "not_ready", "matcher": "ready"},
        status_code=200 if configured else 503,
    )


async def prometheus(_: Request):
    return PlainTextResponse(
        metrics.prometheus(), media_type="text/plain; version=0.0.4"
    )


allowed_hosts = list(settings.allowed_hosts)
for item in tuple(allowed_hosts):
    if ":" not in item or item.startswith("["):
        allowed_hosts.append(item + ":*")
security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts,
    allowed_origins=list(settings.allowed_origins),
)

single_app = SINGLE_MCP.streamable_http_app(
    streamable_http_path="/mcp/single", transport_security=security, host=settings.host
)
native_app = NATIVE_MCP.streamable_http_app(
    streamable_http_path="/mcp/native", transport_security=security, host=settings.host
)


@asynccontextmanager
async def lifespan(_: Starlette):
    async with (
        single_app.router.lifespan_context(single_app),
        native_app.router.lifespan_context(native_app),
    ):
        yield
    await close_api_client()


app = Starlette(
    routes=[
        Route("/healthz", health),
        Route("/readyz", readiness),
        Route("/metrics", prometheus),
        *single_app.routes,
        *native_app.routes,
    ],
    lifespan=lifespan,
)


def add_http_middlewares(application: Starlette, config: Settings) -> None:
    application.add_middleware(AuthMiddleware, config=config)
    if config.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.allowed_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
                "X-Request-ID",
            ],
            expose_headers=["Mcp-Session-Id", "X-Request-ID"],
        )


add_http_middlewares(app, settings)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
