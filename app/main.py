import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route

from app.config import get_settings
from app.routes import admin, paystack_webhook, view
from app.routes.health import health
from app.services import reconciler
from app.tools import front, owner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("wumi")

class McpRequestLogMiddleware(BaseHTTPMiddleware):
    """Log the JSON-RPC method and tool name of each MCP request, never arguments or headers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith("/mcp") or request.method != "POST":
            return await call_next(request)

        try:
            body = json.loads(await request.body())
            messages = body if isinstance(body, list) else [body]
            calls = [
                m.get("method", "?") + (f" {m['params'].get('name')}" if m.get("method") == "tools/call" else "")
                for m in messages
                if isinstance(m, dict)
            ]
        except (ValueError, AttributeError, TypeError):
            calls = ["<unparseable>"]
        response = await call_next(request)
        # Caller-identifying headers only; credentials never reach the log.
        shown = {
            k: v
            for k, v in request.headers.items()
            if (k.startswith("x-") or k == "user-agent") and not any(w in k for w in ("auth", "token", "key", "secret", "cookie"))
        }
        logger.info(
            "mcp request: %s %s session=%s status=%s headers=%s",
            request.url.path,
            ", ".join(calls) or "<empty>",
            "yes" if request.headers.get("mcp-session-id") else "no",
            response.status_code,
            shown,
        )
        return response


class McpBearerAuthMiddleware(BaseHTTPMiddleware):
    """/mcp/owner/ and /mcp/ each take their own token, so a front-desk token can never reach owner tools."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/mcp"):
            return await call_next(request)

        settings = get_settings()
        token = settings.mcp_owner_bearer_token if path.startswith("/mcp/owner") else settings.mcp_bearer_token
        if not token:
            return PlainTextResponse("MCP bearer token is not configured", status_code=503)

        provided = request.headers.get("authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(provided, expected):
            return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


def mcp_security() -> TransportSecuritySettings:
    public_url = get_settings().public_base_url
    host = public_url.host
    scheme = public_url.scheme
    return TransportSecuritySettings(
        allowed_hosts=[host, f"{host}:*"],
        allowed_origins=[f"{scheme}://{host}"],
    )


def http_app(server):
    return server.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        # No server-side sessions: a redeploy can't strand a session id Chord has cached.
        stateless_http=True,
        max_request_body_size=1_048_576,
        transport_security=mcp_security(),
    )


front_app = http_app(front)
owner_app = http_app(owner)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    settings = get_settings()
    task = None
    if settings.reconciler_enabled and settings.paystack_secret_key:
        task = asyncio.create_task(reconciler.run_forever())
    async with front.session_manager.run(), owner.session_manager.run():
        yield
    if task:
        task.cancel()


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/admin/reset", admin.reset, methods=["POST"]),
        Route("/admin/logs", admin.logs),
        Route("/webhooks/paystack", paystack_webhook.webhook, methods=["POST"]),
        Route("/paid", paystack_webhook.paid),
        Route("/v/customer/{id}", view.customer),
        Route("/v/order/{id}", view.order),
        # Owner first: Mount("/mcp") would otherwise swallow /mcp/owner/.
        Mount("/mcp/owner", app=owner_app),
        Mount("/mcp", app=front_app),
    ],
    middleware=[Middleware(McpRequestLogMiddleware), Middleware(McpBearerAuthMiddleware)],
    lifespan=lifespan,
)
