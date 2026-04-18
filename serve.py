"""
HTTP entry point for EC2 deployment.

Serves two endpoints:
  /mcp              — Streamable-HTTP MCP server
  /add-grocery      — Webhook for Google Home / IFTTT voice triggers
                      GET /add-grocery?item=milk&token=<WEBHOOK_SECRET>
"""
import os
import asyncio
from typing import Any

_USERNAME = os.environ.get("COZI_USERNAME", "")
_PASSWORD = os.environ.get("COZI_PASSWORD", "")
_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
_GROCERIES_LIST_ID = "9b932345-a393-4557-a8da-8489bacd0035"

# Disable MCP SDK DNS-rebinding protection (nginx proxies with Host: localhost)
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings

def _patched_tsm_init(self, settings=None):
    self.settings = TransportSecuritySettings(enable_dns_rebinding_protection=False)

TransportSecurityMiddleware.__init__ = _patched_tsm_init

# Inject required Cozi API key on every request
import cozi_client as _cc

_orig_request = _cc.CoziClient._make_request

async def _patched_request(self, method, endpoint, data=None, params=None, require_auth=True):
    if params is None:
        params = {}
    params.setdefault("apikey", "coziwc|v256_production")
    return await _orig_request(self, method, endpoint, data=data, params=params, require_auth=require_auth)

_cc.CoziClient._make_request = _patched_request

# Create MCP server
from cozi_mcp.server import create_server, get_cozi_client
from mcp.server.fastmcp import Context

server = create_server()

# Inject env-var credentials into MCP context
class _EnvConfig:
    username = _USERNAME
    password = _PASSWORD

@property
def _env_session_config(self) -> Any:
    try:
        if (
            hasattr(self, "request_context")
            and hasattr(self.request_context, "request")
            and hasattr(self.request_context.request, "scope")
        ):
            scope = self.request_context.request.scope
            cfg = scope.get("session_config")
            if cfg is not None:
                return cfg
    except (AttributeError, KeyError):
        pass
    return _EnvConfig()

Context.session_config = _env_session_config

# Webhook middleware — intercepts /add-grocery before MCP handles the request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class GroceryWebhookMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path != "/add-grocery":
            return await call_next(request)

        token = request.query_params.get("token", "")
        if _WEBHOOK_SECRET and token != _WEBHOOK_SECRET:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        item = request.query_params.get("item", "").strip()
        if not item:
            return JSONResponse({"error": "item parameter is required"}, status_code=400)

        try:
            client = await get_cozi_client(_USERNAME, _PASSWORD)
            await client.add_item(_GROCERIES_LIST_ID, item)
            return JSONResponse({"ok": True, "item": item})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn

    mcp_app = server._fastmcp.streamable_http_app()
    mcp_app.add_middleware(GroceryWebhookMiddleware)
    uvicorn.run(mcp_app, host="0.0.0.0", port=8081)
