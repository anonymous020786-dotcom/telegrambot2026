"""Record WebRTC live streams.

WebRTC has no file or manifest to download: a viewer negotiates a session and receives the media in real time. Two
ways in, both as an ordinary viewer:

- WHEP (WebRTC-HTTP Egress Protocol, RFC 9725), the open standard for watching WebRTC streams, used by Cloudflare
  Stream, Dolby/Millicast, MediaMTX, Janus, Ant Media and others. We POST an SDP offer to the WHEP URL, receive the
  answer, and record the incoming tracks.
- Pages whose player uses its own signalling are recorded by the headless browser instead (see browser.record_page).

Live streams have no end, so recordings stop after a time limit (or when the broadcaster stops).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .netguard import request_hook

log = logging.getLogger(__name__)

WHEP_PATH = re.compile(r"/whep/?(?:$|[?#])|[?&]whep\b", re.I)


class WebRTCError(Exception):
    pass


def looks_like_whep(url: str) -> bool:
    return bool(WHEP_PATH.search(url))


class Recorder:
    """Write incoming WebRTC tracks to MP4 (H.264/AAC) at the stream's own resolution.

    aiortc's MediaRecorder encodes video at a fixed 640x480; this sizes the encoder from the first real frame. MP4
    can't gain a stream once writing has started, so every track's first frame arrives before the file is set up.
    """

    def __init__(self, path: Path):
        import av

        self.path = path
        self.container = av.open(str(path), mode="w", format="mp4", options={"movflags": "+faststart"})
        self.lock = asyncio.Lock()
        self.streams: dict[str, object] = {}
        self.first: dict[str, object] = {}
        self.kinds: set[str] = set()
        self.tasks: list[asyncio.Task] = []
        self.ready = asyncio.Event()
        self.started = asyncio.Event()

    def add(self, track) -> None:
        self.kinds.add(track.kind)
        self.tasks.append(asyncio.create_task(self._consume(track)))

    def _setup(self) -> None:
        if self.ready.is_set():
            return
        if video := self.first.get("video"):
            stream = self.container.add_stream("libx264", rate=30)
            stream.width, stream.height = video.width - video.width % 2, video.height - video.height % 2
            stream.pix_fmt = "yuv420p"
            stream.options = {"preset": "veryfast", "crf": "20"}
            self.streams["video"] = stream
        if "audio" in self.first:
            self.streams["audio"] = self.container.add_stream("aac", rate=48000)
        self.ready.set()

    def _encode(self, kind: str, frame) -> None:
        stream = self.streams.get(kind)
        if stream is None:
            return  # this track's first frame came too late to get a stream
        if kind == "video":
            frame = frame.reformat(width=stream.width, height=stream.height, format="yuv420p")
        frame.pts = None
        self.container.mux(stream.encode(frame))
        self.started.set()

    async def _consume(self, track) -> None:
        from aiortc.mediastreams import MediaStreamError

        try:
            frame = await track.recv()
            self.first[track.kind] = frame
            if self.kinds <= set(self.first):
                self._setup()
            else:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.ready.wait(), 5)
                self._setup()  # a track that never delivers must not hold up the others
            async with self.lock:
                self._encode(track.kind, frame)
            while True:
                frame = await track.recv()
                async with self.lock:
                    self._encode(track.kind, frame)
        except MediaStreamError:
            return
        except Exception:
            log.exception("WebRTC %s track recording failed", track.kind)
            raise

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        async with self.lock:
            for stream in self.streams.values():
                with contextlib.suppress(Exception):
                    self.container.mux(stream.encode(None))  # flush the encoders
            self.container.close()


async def _wait_ice(pc, gathered: asyncio.Event, timeout: float = 10.0) -> None:
    """Non-trickle WHEP: send the offer once ICE gathering has finished, so it carries every candidate."""
    if pc.iceGatheringState != "complete":
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(gathered.wait(), timeout)


async def record_whep(
    url: str,
    out_path: Path,
    seconds: float,
    user_agent: str,
    token: str | None = None,
    allow_private: bool = False,
    progress=None,
) -> Path:
    """Watch a WHEP stream for up to `seconds` and save it to `out_path` (.mp4 re-encoded to H.264/AAC)."""
    try:
        from aiortc import RTCPeerConnection, RTCSessionDescription
    except ImportError as exc:
        raise WebRTCError("WebRTC recording needs the aiortc package") from exc

    headers = {"User-Agent": user_agent, "Content-Type": "application/sdp", "Accept": "application/sdp"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    recorder = Recorder(out_path)
    tracks: list = []
    ended = asyncio.Event()
    got_track = asyncio.Event()
    gathered = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def on_gathering() -> None:
        if pc.iceGatheringState == "complete":
            gathered.set()

    @pc.on("track")
    def on_track(track) -> None:
        tracks.append(track)
        recorder.add(track)
        got_track.set()

        @track.on("ended")
        def on_ended() -> None:
            ended.set()

    @pc.on("connectionstatechange")
    async def on_state() -> None:
        if pc.connectionState in ("failed", "closed"):
            ended.set()

    resource: str | None = None
    hooks = {"request": [request_hook(allow_private)]}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, event_hooks=hooks) as client:
        try:
            await pc.setLocalDescription(await pc.createOffer())
            await _wait_ice(pc, gathered)
            res = await client.post(url, content=pc.localDescription.sdp, headers=headers)
            if res.status_code in (401, 403):
                raise WebRTCError("The WHEP server refused to play this stream (it needs a viewer token).")
            if res.status_code == 404:
                raise WebRTCError("No live stream at this WHEP address (it may have ended).")
            if res.status_code not in (200, 201) or "v=0" not in res.text:
                raise WebRTCError(f"This address didn't answer like a WHEP server (HTTP {res.status_code}).")
            if location := res.headers.get("location"):
                resource = urljoin(str(res.url), location)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=res.text, type="answer"))

            # Wait for the media to arrive, then record until the limit or until the stream ends.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(got_track.wait(), 15)
            if not tracks:
                raise WebRTCError("Connected, but the stream sent no audio or video.")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(recorder.started.wait(), 15)
            began = time.monotonic()
            while time.monotonic() - began < seconds and not ended.is_set():
                await asyncio.sleep(0.5)
                if progress:
                    progress(time.monotonic() - began, seconds)
        finally:
            with contextlib.suppress(Exception):
                await recorder.stop()
            with contextlib.suppress(Exception):
                await pc.close()
            if resource:  # tell the server we left (RFC 9725 §4.3)
                with contextlib.suppress(Exception):
                    await client.delete(resource, headers={"User-Agent": user_agent})
    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise WebRTCError("The WebRTC session produced no media.")
    return out_path
