"""Boots the real bot process against a fake Telegram Bot API server and talks to it."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.commands: dict[str, int] = {}
        self.pending = [
            self._update(1, 1, "/start"),
            self._update(2, 1, "/ping"),
            self._update(3, 777, "https://example.com/video"),  # stranger → private-bot message
        ]
        self._msg_id = 100

    @staticmethod
    def _update(update_id: int, user_id: int, text: str) -> dict:
        user = {"id": user_id, "is_bot": False, "first_name": "Test", "username": f"u{user_id}"}
        entities = (
            [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
        )
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": int(time.time()),
                "chat": {"id": user_id, "type": "private"},
                "from": user,
                "text": text,
                "entities": entities,
            },
        }

    async def handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        self.calls.append(method)
        try:
            data = await request.post() if request.content_type != "application/json" else await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        data = dict(data)
        result: object = True
        if method == "getMe":
            result = {"id": 42, "is_bot": True, "first_name": "Bot", "username": "test_bot"}
        elif method == "getUpdates":
            offset = int(data.get("offset") or 0)
            ready = [u for u in self.pending if u["update_id"] >= offset]
            if not ready:
                await asyncio.sleep(0.2)
            result = ready
        elif method == "setMyCommands":
            commands = data.get("commands")
            if isinstance(commands, str):
                commands = json.loads(commands)
            scope = data.get("scope") or "default"
            self.commands[str(scope)] = len(commands or [])
        elif method in ("sendMessage", "editMessageText"):
            self._msg_id += 1
            self.sent.append(data)
            chat_id = int(data.get("chat_id") or 0)
            result = {
                "message_id": self._msg_id,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
                "text": data.get("text", ""),
            }
        return web.json_response({"ok": True, "result": result})


async def test_real_bot_process_boots_and_answers(tmp_path, unused_tcp_port):
    fake = FakeTelegram()
    app = web.Application()
    app.router.add_route("*", "/bot{token}/{method}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()

    env = {
        **os.environ,
        "BOT_TOKEN": "123456:TESTTOKEN",
        "ADMIN_IDS": "1",
        "DATA_DIR": str(tmp_path / "data"),
        "BOT_API_BASE_URL": f"http://127.0.0.1:{unused_tcp_port}/bot",
        "BOT_API_BASE_FILE_URL": f"http://127.0.0.1:{unused_tcp_port}/file/bot",
        "LOG_LEVEL": "INFO",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "bot", cwd=ROOT, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        for _ in range(150):
            texts = " ".join(str(m.get("text", "")) for m in fake.sent)
            if "Hi Test" in texts and "Pong" in texts and "private bot" in texts:
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.1)
        texts = " ".join(str(m.get("text", "")) for m in fake.sent)
        assert "Hi Test" in texts, (texts, fake.calls)  # /start welcome for the admin
        assert "Pong" in texts  # /ping answered (edited in place)
        assert "private bot" in texts and "777" in texts  # stranger blocked, told their ID
        assert fake.commands, "command menu was not registered"
        assert max(fake.commands.values()) <= 100
        assert (tmp_path / "data" / "bot.db").exists()
    finally:
        proc.terminate()
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 20)
        except TimeoutError:
            proc.kill()
            out = b""
        await runner.cleanup()
    log = out.decode(errors="replace")
    assert "ready" in log and "Traceback" not in log, log[-3000:]
