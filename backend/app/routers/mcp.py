"""MCP over Streamable HTTP, for agents holding a workspace API key.

Muse is the first caller: it speaks MCP natively, so a hosted endpoint +
a bearer key is the whole integration (custom connectors aren't reviewed
by Meta).  Endpoint: ``POST {PUBLIC_ORIGIN}/mcp``.

Stateless on purpose: every request carries its bearer key and resolves
its own workspace — no session to keep or expire, nothing two agents can
share, and an API restart can't strand a connector mid-conversation.  We
answer with plain JSON (never open an SSE stream), which the Streamable
HTTP transport allows for request/response servers like this one.

Tools (app/mcp/tools.py) dispatch through the normal API routes
in-process with the caller's key, so auth, tenant scoping, RLS and every
route's own validation apply exactly as for the dashboard.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import ratelimit
from app.auth.deps import authenticate_api_key, bearer_api_key
from app.database import get_session_factory
from app.mcp.tools import TOOLS, TOOLS_BY_NAME, ToolContext, ToolError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "email-blaster", "title": "Email Blaster", "version": "1.0.0"}
INSTRUCTIONS = (
    "Email Blaster runs this workspace's cold-outreach: campaigns send personalised email "
    "sequences (plus LinkedIn steps), replies land in an inbox with AI sentiment, and a CRM "
    "tracks leads, deals, tasks and owners. Start with daily_brief. You can read everything, "
    "update the CRM (leads, notes, tasks, deals, owners) and pause or resume campaigns. You "
    "cannot send email or LinkedIn messages, launch campaigns, or delete anything — tell the "
    "user to do those in the Email Blaster dashboard. 'me' in owner fields means the person "
    "whose API key this is."
)


def _rpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": message},
        headers={"WWW-Authenticate": 'Bearer realm="emailblaster"'},
    )


async def _handle(msg: dict, ctx: ToolContext) -> dict | None:
    """One JSON-RPC message → response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notification (e.g. notifications/initialized)
        return None

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _rpc_result(msg_id, {
            "protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [t.listing() for t in TOOLS]})
    if method == "tools/call":
        tool = TOOLS_BY_NAME.get(params.get("name"))
        if tool is None:
            return _rpc_error(msg_id, -32602, f"Unknown tool: {params.get('name')!r}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object")
        try:
            result = await tool.run(ctx, args)
        except (ToolError, KeyError, ValueError, TypeError) as exc:
            # Returned as tool output, not a protocol error: the agent can read
            # it, explain it to the user, and retry.
            msg_text = f"missing argument {exc}" if isinstance(exc, KeyError) else str(exc)
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": f"Email Blaster refused this: {msg_text}"}],
                "isError": True,
            })
        text = json.dumps(result, indent=2, default=str)
        payload: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(result, dict):
            payload["structuredContent"] = result
        return _rpc_result(msg_id, payload)
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> Response:
    token = bearer_api_key(request)
    if token is None:
        return _unauthorized("An Email Blaster API key is required (Settings → Agent access).")
    client_ip = request.client.host if request.client else "unknown"
    try:
        identity = await authenticate_api_key(token, request, factory)
    except Exception:  # noqa: BLE001 — invalid/revoked key
        await ratelimit.hit(f"mcp-badkey:{client_ip}", limit=20, window_seconds=600)
        return _unauthorized("Invalid or revoked API key.")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, "Parse error"))

    # Tools call the regular API in-process with the same key.
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://emailblaster.internal",
        timeout=60.0,
    ) as client:
        ctx = ToolContext(
            authorization=request.headers["authorization"],
            user_id=identity.user_id,
            client=client,
        )
        if isinstance(body, list):
            responses = [r for r in [await _handle(m, ctx) for m in body] if r is not None]
            if not responses:
                return Response(status_code=202)
            return JSONResponse(responses)
        response = await _handle(body, ctx)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


@router.get("/mcp")
async def mcp_no_stream() -> JSONResponse:
    # Streamable HTTP lets a server offer a server-initiated SSE stream.  This
    # one is stateless and has none; saying so beats a hanging connection.
    return JSONResponse(status_code=405, content={"error": "This MCP server does not open server-sent streams"},
                        headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_no_session() -> JSONResponse:
    return JSONResponse(status_code=405, content={"error": "This MCP server is stateless; nothing to delete"},
                        headers={"Allow": "POST"})
