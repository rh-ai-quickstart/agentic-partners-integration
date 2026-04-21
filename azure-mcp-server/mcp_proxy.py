"""HTTP-to-stdio MCP proxy for the Azure MCP Server.

The Azure MCP Server's ``--dangerously-disable-http-incoming-auth`` flag
crashes on all available container builds (MCR, quay.io).  This proxy
works around the issue by:

1. Starting the Azure MCP binary in **stdio** mode (no HTTP auth).
2. Connecting to it via the MCP Python SDK's ``stdio_client``.
3. Exposing an **unauthenticated** HTTP endpoint that proxies
   ``tools/list`` and ``tools/call`` to the stdio session.

The ARO partner agent connects here instead of directly to the .NET binary.
"""

import asyncio
import logging
import os
import signal

from aiohttp import web
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mcp_proxy")

MCP_BINARY = os.getenv("MCP_BINARY", "/mcp-server/server-binary")
PORT = int(os.getenv("PORT", "8080"))


def _build_mcp_args() -> list[str]:
    """Assemble CLI arguments for the Azure MCP binary."""
    args = ["server", "start"]

    strategy = os.getenv(
        "MCP_OUTGOING_AUTH_STRATEGY", "UseHostingEnvironmentIdentity"
    )
    args.extend(["--outgoing-auth-strategy", strategy])

    namespaces = os.getenv("MCP_NAMESPACES", "")
    if namespaces:
        for ns in namespaces.split(","):
            args.extend(["--namespace", ns.strip()])

    mode = os.getenv("MCP_MODE", "")
    if mode:
        args.extend(["--mode", mode])

    if os.getenv("MCP_READ_ONLY", "").lower() in ("true", "1", "yes"):
        args.append("--read-only")

    return args


class McpProxy:
    """Bridges HTTP requests to the Azure MCP Server stdio session."""

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._tools: list = []

    async def connect(self) -> None:
        args = _build_mcp_args()
        logger.info("Starting Azure MCP binary: %s %s", MCP_BINARY, " ".join(args))

        env = {k: v for k, v in os.environ.items()}
        env.setdefault("HOME", "/tmp")
        env.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
        env.setdefault("DOTNET_BUNDLE_EXTRACT_BASE_DIR", "/tmp/.net")

        params = StdioServerParameters(command=MCP_BINARY, args=args, env=env)

        self._transport_cm = stdio_client(params)
        read_stream, write_stream = await self._transport_cm.__aenter__()

        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

        result = await self._session.list_tools()
        self._tools = result.tools
        logger.info(
            "Connected — discovered %d tools: %s",
            len(self._tools),
            [t.name for t in self._tools],
        )

    async def close(self) -> None:
        if hasattr(self, "_session_cm"):
            await self._session_cm.__aexit__(None, None, None)
        if hasattr(self, "_transport_cm"):
            await self._transport_cm.__aexit__(None, None, None)
        logger.info("MCP session closed")

    def tools_as_dicts(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": (
                    t.inputSchema
                    if t.inputSchema
                    else {"type": "object", "properties": {}}
                ),
            }
            for t in self._tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if self._session is None:
            raise RuntimeError("MCP session not connected")
        result = await self._session.call_tool(name, arguments=arguments)
        content = []
        for block in result.content:
            if hasattr(block, "text"):
                content.append({"type": "text", "text": block.text})
            else:
                content.append({"type": "text", "text": str(block)})
        return {
            "content": content,
            "isError": bool(result.isError) if result.isError is not None else False,
        }


async def handle_mcp_post(request: web.Request) -> web.Response:
    """Handle MCP Streamable HTTP POST (JSON-RPC)."""
    proxy: McpProxy = request.app["proxy"]

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status=400,
        )

    method = body.get("method", "")
    msg_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "azure-mcp-proxy", "version": "1.0.0"},
            },
        })

    if method == "notifications/initialized":
        return web.Response(status=202)

    if method == "tools/list":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": proxy.tools_as_dicts()},
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = await proxy.call_tool(tool_name, arguments)
            return web.json_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            })
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            return web.json_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            })

    if method == "ping":
        return web.json_response({"jsonrpc": "2.0", "id": msg_id, "result": {}})

    return web.json_response({
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })


async def handle_health(request: web.Request) -> web.Response:
    proxy: McpProxy = request.app["proxy"]
    return web.json_response({
        "status": "ok",
        "service": "azure-mcp-proxy",
        "tools_count": len(proxy._tools),
        "tools": [t.name for t in proxy._tools],
    })


async def start_proxy(app: web.Application) -> None:
    proxy = McpProxy()
    app["proxy"] = proxy
    try:
        await proxy.connect()
    except Exception:
        logger.exception("Failed to start Azure MCP subprocess")
        raise


async def stop_proxy(app: web.Application) -> None:
    await app["proxy"].close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/", handle_mcp_post)
    app.router.add_get("/health", handle_health)
    app.on_startup.append(start_proxy)
    app.on_cleanup.append(stop_proxy)
    return app


if __name__ == "__main__":
    logger.info("Starting Azure MCP Proxy on port %d", PORT)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
