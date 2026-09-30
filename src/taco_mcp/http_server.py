"""HTTP entrypoint for deploying the TACO MCP server."""

from __future__ import annotations

import contextlib
import os
import secrets
from collections.abc import AsyncIterator, Callable

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from .server import app as mcp_server

# The remote deployment is intentionally read-only. The stdio entrypoint keeps
# its existing behavior because this setting only applies in this process.
os.environ["TACO_MCP_READ_ONLY"] = "1"


def _csv_env(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable."""
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def _required_token() -> str:
    """Read the bearer token and fail closed when it is not configured."""
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "MCP_AUTH_TOKEN must be configured before starting the HTTP server"
        )
    return token


class BearerTokenMiddleware:
    """Protect MCP requests with a bearer token without buffering streams."""

    def __init__(self, app: Callable, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
        expected = f"Bearer {self.token}".encode("utf-8")
        if not secrets.compare_digest(authorization, expected):
            response = JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def health(_: Request) -> Response:
    """Return a lightweight health response for Heroku's router."""
    return JSONResponse({"status": "ok", "service": "taco-mcp"})


class MCPHandler:
    """Adapt the session manager's ASGI handler to an exact Starlette route."""

    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)


allowed_hosts = _csv_env(
    "MCP_ALLOWED_HOSTS",
    "127.0.0.1:*,localhost:*,[::1]:*",
)
allowed_origins = _csv_env("MCP_ALLOWED_ORIGINS")
security_settings = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts,
    allowed_origins=allowed_origins,
)
session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=True,
    stateless=True,
    security_settings=security_settings,
)
mcp_handler = MCPHandler(session_manager)


@contextlib.asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    """Manage the Streamable HTTP session manager for the ASGI app."""
    async with session_manager.run():
        yield


starlette_app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/mcp", mcp_handler),
        Route("/mcp/", mcp_handler),
    ],
    lifespan=lifespan,
)

app = BearerTokenMiddleware(starlette_app, _required_token())

