"""Small Streamable HTTP client for NSE's public MCP endpoints with session reuse."""
from __future__ import annotations

import json
import os
import threading
from typing import Any

import httpx

BHAVCOPY_URL = os.getenv("NSE_BHAVCOPY_MCP_URL", "https://mcp.nseindia.in/bhavcopy/cm/mcp")
LIVE_URL = os.getenv("NSE_CM_MCP_URL", "https://mcp.nseindia.in/cmmkt/mcp")
PROTOCOL_VERSION = "2025-03-26"


class NSEError(RuntimeError):
    pass


class _SessionPool:
    """Reuse one MCP session per endpoint; recreate on failure."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, httpx.Client] = {}
        # MCP sessions are reused, but a single streamable HTTP session must
        # not receive overlapping requests from the poller and API handlers.
        self._request_locks: dict[str, threading.Lock] = {}

    def get(self, url: str) -> httpx.Client:
        with self._lock:
            client = self._clients.get(url)
            if client is not None:
                return client
            client = self._open_session(url)
            self._clients[url] = client
            return client

    def drop(self, url: str) -> None:
        with self._lock:
            client = self._clients.pop(url, None)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def request_lock(self, url: str) -> threading.Lock:
        with self._lock:
            return self._request_locks.setdefault(url, threading.Lock())

    def _open_session(self, url: str) -> httpx.Client:
        client = httpx.Client(timeout=45, follow_redirects=True)
        init, sid, _ = _rpc(
            url,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "trading-assistant", "version": "1.0"},
            },
            client,
        )
        del init
        client.mcp_session_id = sid
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if sid:
            headers["Mcp-Session-Id"] = sid
        initialized = client.post(
            url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers
        )
        initialized.raise_for_status()
        return client


_POOL = _SessionPool()


def _rpc(
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    session: httpx.Client | None = None,
) -> tuple[dict[str, Any], str | None, httpx.Client]:
    client = session or httpx.Client(timeout=45, follow_redirects=True)
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if session is not None:
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if getattr(session, "mcp_session_id", None):
            headers["Mcp-Session-Id"] = session.mcp_session_id
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        sid = response.headers.get("mcp-session-id") or getattr(client, "mcp_session_id", None)
        if sid:
            client.mcp_session_id = sid
        payload = response.text.strip()
        if payload.startswith("event:") or "\ndata:" in payload:
            payload = "\n".join(line[5:].strip() for line in payload.splitlines() if line.startswith("data:"))
        message = json.loads(payload)
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        if session is None:
            client.close()
        raise NSEError(f"NSE MCP request failed: {exc}") from exc
    if message.get("error"):
        if session is None:
            client.close()
        raise NSEError(message["error"].get("message", "NSE MCP returned an error"))
    if "result" not in message:
        if session is None:
            client.close()
        raise NSEError("NSE MCP returned an unrecognized response")
    return message["result"], sid, client


def list_tools(url: str) -> list[dict[str, Any]]:
    try:
        with _POOL.request_lock(url):
            client = _POOL.get(url)
            result, _, _ = _rpc(url, "tools/list", session=client)
        return result.get("tools", [])
    except Exception:
        _POOL.drop(url)
        with _POOL.request_lock(url):
            client = _POOL.get(url)
            result, _, _ = _rpc(url, "tools/list", session=client)
        return result.get("tools", [])


def call_tool(url: str, name: str, arguments: dict[str, Any]) -> Any:
    def _once() -> Any:
        with _POOL.request_lock(url):
            client = _POOL.get(url)
            result, _, _ = _rpc(url, "tools/call", {"name": name, "arguments": arguments}, client)
        if result.get("isError"):
            text = " ".join(block.get("text", "") for block in result.get("content", []))
            raise NSEError(text or f"NSE tool {name} failed")
        content = result.get("content", [])
        texts = [block.get("text", "") for block in content if block.get("type") == "text"]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return texts[0]
        return result.get("structuredContent") or texts or result

    try:
        return _once()
    except Exception:
        _POOL.drop(url)
        return _once()
