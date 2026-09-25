from __future__ import annotations

import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from bot.config import Settings
from bot.db import Database

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(bot_token="123:TEST", admin_ids=[1], data_dir=tmp_path / "data", link_secret="s3cret")
    s.ensure_dirs()
    return s


@pytest.fixture
async def db(settings: Settings):
    database = Database(settings.db_path)
    await database.connect()
    yield database
    await database.close()


# ---------------------------------------------------------------- local web server

SITE_HTML = """<!doctype html><html><head><title>Fixture gallery</title>
<meta property="og:image" content="/img/og.png"><link rel="icon" href="/img/icon.png">
<script type="application/ld+json">{"@type":"Product","image":["/img/ld.png"]}</script>
<style>.hero{background:url('/img/bg.png')}</style></head><body>
<img src="/img/small.png" srcset="/img/small.png 100w, /img/big.png 800w" alt="big">
<img src="data:image/gif;base64,R0lGOD" data-src="/img/lazy.png" alt="lazy">
<picture><source srcset="/img/pic.png 2x"><img src="/img/pic-fallback.png"></picture>
<div style="background-image:url('/img/inline.png')"></div>
<a href="/img/linked.jpg">full</a>
<img src="/hotlink/protected.png" alt="protected">
<img src="javascript:alert(1)">
</body></html>"""


def _png(width: int, height: int, color=(200, 30, 90)) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


class _Handler(SimpleHTTPRequestHandler):
    root: Path

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/gallery.html"):
            return self._send(SITE_HTML.encode(), "text/html; charset=utf-8")
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/gallery.html")
            self.end_headers()
            return None
        if path.startswith("/tube/"):
            # A "site" yt-dlp has no extractor for: the stream URL only appears in a player config script.
            port = self.server.server_port
            rating = '<meta name="RATING" content="RTA-5042-1996-1400-1577-RTA">' if "adult" in path else ""
            body = (
                f"<html><head><title>Clip</title>{rating}</head><body><div id=player></div>"
                f'<script>var player = new Player({{sources: [{{src: "http:\\/\\/127.0.0.1:{port}\\/sample.mp4",'
                f' label: "720p"}}], poster: "/img/poster.png"}});</script></body></html>'
            )
            return self._send(body.encode(), "text/html; charset=utf-8")
        if path.startswith("/hotlink/"):
            ok = (self.headers.get("Referer") or "").startswith(f"http://127.0.0.1:{self.server.server_port}")
            return self._send(
                _png(640, 480) if ok else b"forbidden", "image/png" if ok else "text/plain", 200 if ok else 403
            )
        if path.startswith("/img/"):
            size = (800, 600) if "big" in path else (120, 90) if "small" in path or "icon" in path else (400, 300)
            return self._send(_png(*size), "image/png")
        media = self.root / path.lstrip("/")
        if media.is_file() and self.root in media.resolve().parents:
            ctype = "video/mp4" if media.suffix == ".mp4" else "application/octet-stream"
            return self._send(media.read_bytes(), ctype)
        return self._send(b"not found", "text/plain", 404)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def sample_video(media_dir: Path) -> Path:
    """4-second 320x240 H.264/AAC clip with a test pattern and a tone."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = media_dir / "sample.mp4"
    if not out.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=25:duration=4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out),
            ],
            check=True,
        )
    return out


@pytest.fixture(scope="session")
def web(media_dir: Path, sample_video: Path):
    handler = type("H", (_Handler,), {"root": media_dir})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


# ---------------------------------------------------------------- fake Telegram objects


class FakeMessage(SimpleNamespace):
    _ids = iter(range(1000, 10**9))

    def __init__(self, bot: FakeBot, chat_id: int, **kw: Any):
        fields: dict[str, Any] = {
            "message_id": next(FakeMessage._ids),
            "chat_id": chat_id,
            "bot": bot,
            "text": None,
            "caption": None,
            "photo": None,
            "video": None,
            "audio": None,
            "document": None,
            "animation": None,
            "voice": None,
            "video_note": None,
            "entities": [],
            "caption_entities": [],
            "reply_to_message": None,
            "chat": SimpleNamespace(id=chat_id, type="private"),
        }
        fields.update(kw)
        super().__init__(**fields)

    async def reply_text(self, text: str, **kw: Any) -> FakeMessage:
        return await self.bot.send_message(self.chat_id, text, **kw)

    async def reply_document(self, document: Any, **kw: Any) -> FakeMessage:
        return await self.bot.send_document(chat_id=self.chat_id, document=document, **kw)

    async def reply_photo(self, photo: Any, **kw: Any) -> FakeMessage:
        return await self.bot.send_photo(chat_id=self.chat_id, photo=photo, **kw)

    async def edit_text(self, text: str, **kw: Any) -> FakeMessage:
        self.bot.calls.append(("edit_text", text))
        self.text = text
        return self

    async def delete(self) -> None:
        self.bot.calls.append(("delete", self.message_id))


class FakeBot:
    """Records every call; returns objects shaped like telegram.Message."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.sent_files: list[tuple[str, str]] = []
        self.username = "test_bot"

    async def get_me(self):
        return SimpleNamespace(username=self.username, id=42)

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> FakeMessage:
        self.calls.append(("send_message", text))
        return FakeMessage(self, chat_id, text=text)

    async def edit_message_text(self, text: str, chat_id: int, message_id: int, **kw: Any) -> None:
        self.calls.append(("edit_message_text", text))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.calls.append(("delete_message", message_id))

    def _media(self, kind: str, payload: Any, chat_id: int, **kw: Any) -> FakeMessage:
        name = getattr(payload, "name", None) or kw.get("filename") or str(payload)
        if hasattr(payload, "read"):
            payload.read()  # consume like the real upload would
        self.sent_files.append((kind, str(name)))
        self.calls.append((f"send_{kind}", name))
        obj = SimpleNamespace(file_id=f"FILE_{kind}_{len(self.sent_files)}")
        msg = FakeMessage(self, chat_id)
        if kind == "photo":
            msg.photo = [obj]
        else:
            setattr(msg, kind, obj)
        return msg

    async def send_video(self, chat_id: int, video: Any, **kw: Any) -> FakeMessage:
        return self._media("video", video, chat_id, **kw)

    async def send_audio(self, chat_id: int, audio: Any, **kw: Any) -> FakeMessage:
        return self._media("audio", audio, chat_id, **kw)

    async def send_document(self, chat_id: int, document: Any, **kw: Any) -> FakeMessage:
        return self._media("document", document, chat_id, **kw)

    async def send_photo(self, chat_id: int, photo: Any, **kw: Any) -> FakeMessage:
        return self._media("photo", photo, chat_id, **kw)

    async def send_animation(self, chat_id: int, animation: Any, **kw: Any) -> FakeMessage:
        return self._media("animation", animation, chat_id, **kw)

    async def send_voice(self, chat_id: int, voice: Any, **kw: Any) -> FakeMessage:
        return self._media("voice", voice, chat_id, **kw)

    async def send_video_note(self, chat_id: int, video_note: Any, **kw: Any) -> FakeMessage:
        return self._media("video_note", video_note, chat_id, **kw)

    async def send_media_group(self, chat_id: int, media: list, **kw: Any) -> list[FakeMessage]:
        self.calls.append(("send_media_group", len(media)))
        for _ in media:
            self.sent_files.append(("album", "item"))
        return [FakeMessage(self, chat_id) for _ in media]

    def texts(self) -> list[str]:
        return [str(arg) for name, arg in self.calls if name in ("send_message", "edit_text", "edit_message_text")]


def make_update(bot: FakeBot, user_id: int = 1, text: str = "", username: str = "tester"):
    msg = FakeMessage(bot, user_id, text=text)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username=username, first_name="Test"),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=user_id, type="private"),
        callback_query=None,
    )


def make_context(services, bot: FakeBot, args: list[str] | None = None):
    app = SimpleNamespace(bot_data={"svc": services}, job_queue=None)
    return SimpleNamespace(application=app, args=args or [], bot=bot)
