"""Vercel entry point. Serves the MCP server at /<MCP_SECRET>/mcp.

The secret path is the only access control, so keep the URL private.
Vercel functions can write only to /tmp, and /tmp is lost when an instance stops.
seed.db (a copy of a full cache) gives each new instance the nutrition data, so a
cold start fetches only the menu pages of the requested day. Refresh it monthly:
nutrition older than 30 days is refetched, which can exceed the 60 second limit.

    UCSD_DINING_DB=api/seed.db uv run ucsd-dining --refresh
    vercel deploy --prod
"""

import hmac
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

DB = Path("/tmp/dining.db")
SEED = HERE / "seed.db"
os.environ["UCSD_DINING_DB"] = str(DB)
if not DB.exists() and SEED.exists():
    shutil.copy(SEED, DB)

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402

from ucsd_dining.server import mcp  # noqa: E402

# Stateless JSON mode: each request can reach a different instance, so no sessions.
# DNS rebinding protection is for localhost servers. It rejects the Vercel Host header.
_mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
_WANTED = f"{os.environ['MCP_SECRET']}/mcp"


async def app(scope, receive, send):
    """Check the secret path, then pass the request to the MCP app.

    The rewrite in vercel.json changes the path to /api/index and puts the
    original path in the "p" query parameter.
    """
    if scope["type"] == "http":
        given = parse_qs(scope["query_string"].decode()).get("p", [""])[0]
        if not hmac.compare_digest(given.encode(), _WANTED.encode()):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        scope = {**scope, "path": "/mcp", "raw_path": b"/mcp", "query_string": b""}
    await _mcp_app(scope, receive, send)
