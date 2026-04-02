from __future__ import annotations

import asyncio
import json
import secrets
import sys
from typing import Any

from aiohttp import web

from serverlens.auth.token_auth import TokenAuth
from serverlens.transport.base import MessageHandler, TransportInterface


class SseTransport(TransportInterface):
    def __init__(
        self,
        host: str,
        port: int,
        auth: TokenAuth | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth = auth
        self._handler: MessageHandler | None = None
        self._sessions: dict[str, web.StreamResponse] = {}

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    def start(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        app = web.Application()
        app.router.add_route("OPTIONS", "/{path:.*}", self._handle_cors)
        app.router.add_get("/sse", self._handle_sse_connect)
        app.router.add_post("/message", self._handle_message)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()

        print(
            f"[ServerLens] SSE transport listening on {self._host}:{self._port}",
            file=sys.stderr,
        )

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _handle_sse_connect(self, request: web.Request) -> web.StreamResponse:
        client_ip = _extract_client_ip(request)

        if self._auth:
            auth_header = request.headers.get("Authorization", "")
            if not self._auth.verify(auth_header, client_ip):
                return web.json_response({"error": "Unauthorized"}, status=401)

        session_id = secrets.token_hex(16)
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)
        self._sessions[session_id] = response

        print(f"[ServerLens] New SSE session: {session_id}", file=sys.stderr)

        endpoint = f"/message?sessionId={session_id}"
        await response.write(f"event: endpoint\ndata: {endpoint}\n\n".encode())

        try:
            while not response.task.done():
                await asyncio.sleep(30)
                try:
                    await response.write(b": keepalive\n\n")
                except (ConnectionResetError, ConnectionError):
                    break
        finally:
            self._sessions.pop(session_id, None)
            print(f"[ServerLens] Session closed: {session_id}", file=sys.stderr)

        return response

    async def _handle_message(self, request: web.Request) -> web.Response:
        session_id = request.query.get("sessionId")
        if not session_id or session_id not in self._sessions:
            return web.json_response(
                {"error": "Invalid or expired session"},
                status=400,
                headers=_JSON_HEADERS,
            )

        client_ip = _extract_client_ip(request)
        if self._auth:
            auth_header = request.headers.get("Authorization", "")
            if not self._auth.verify(auth_header, client_ip):
                return web.json_response(
                    {"error": "Unauthorized"},
                    status=401,
                    headers=_JSON_HEADERS,
                )

        try:
            body = await request.text()
            message = json.loads(body)
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400,
                headers=_JSON_HEADERS,
            )

        if not isinstance(message, dict):
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400,
                headers=_JSON_HEADERS,
            )

        assert self._handler is not None
        response_data = self._handler(message, client_ip)

        if response_data is not None and "id" in message:
            stream = self._sessions.get(session_id)
            if stream is not None:
                payload = json.dumps(response_data, ensure_ascii=False)
                try:
                    await stream.write(f"event: message\ndata: {payload}\n\n".encode())
                except (ConnectionResetError, ConnectionError):
                    self._sessions.pop(session_id, None)

        return web.Response(
            status=202,
            headers={**_JSON_HEADERS, "Content-Type": "application/json"},
        )

    async def _handle_cors(self, _request: web.Request) -> web.Response:
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "86400",
            },
        )


def _extract_client_ip(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername") if request.transport else None
    if peername:
        return peername[0]
    return "127.0.0.1"


_JSON_HEADERS = {"Access-Control-Allow-Origin": "*"}
