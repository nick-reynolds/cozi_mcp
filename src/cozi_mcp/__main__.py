"""Entry point for running cozi_mcp as a local stdio MCP server.

Patches session_config to read credentials from COZI_USERNAME / COZI_PASSWORD
environment variables so the server works without Smithery cloud infrastructure.
"""
import os
from typing import Any

_USERNAME = os.environ.get("COZI_USERNAME", "")
_PASSWORD = os.environ.get("COZI_PASSWORD", "")


class _LocalConfig:
    username = _USERNAME
    password = _PASSWORD


# Patch cozi_client to include the required API key on every request.
# The web app sends ?apikey=coziwc|v256_production on all calls; without it the
# server returns 401 even with valid credentials.
import cozi_client as _cozi_client_module

_original_make_request = _cozi_client_module.CoziClient._make_request

async def _patched_make_request(self, method, endpoint, data=None, params=None, require_auth=True):
    if params is None:
        params = {}
    params.setdefault("apikey", "coziwc|v256_production")
    return await _original_make_request(self, method, endpoint, data=data, params=params, require_auth=require_auth)

_cozi_client_module.CoziClient._make_request = _patched_make_request

# Import and create server (this triggers SmitheryFastMCP + ensure_context_patched)
from cozi_mcp.server import create_server

server = create_server()

# Override session_config on the patched Context class to return env var credentials.
# In HTTP/Smithery mode the middleware injects real config; in stdio mode it falls
# back to EmptyConfig which has no username/password.  We replace that fallback.
from mcp.server.fastmcp import Context


@property
def _local_session_config(self) -> Any:
    try:
        if (
            hasattr(self, "request_context")
            and hasattr(self.request_context, "request")
            and hasattr(self.request_context.request, "scope")
        ):
            scope = self.request_context.request.scope
            config = scope.get("session_config")
            if config is not None:
                return config
    except (AttributeError, KeyError):
        pass
    return _LocalConfig()


Context.session_config = _local_session_config

server.run()
