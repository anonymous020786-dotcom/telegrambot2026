"""Media tools that work on a file you send or reply to (ffmpeg / Pillow)."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from ..db import User
from ..registry import command
from ..services import media
from ..utils import esc, human_duration, human_size, parse_size, parse_timestamp
from .common import Ctx, fetch_replied_file, need_media, reply, svc

AV = ("video", "audio")
VIDEO = ("video",)
IMAGE = ("image",)


async def run_tool(
    update: Update,
    context: Ctx,
    user: User,
    want: tuple[str, ...],
    work: Callable[[Path], Awaitable[Path]],
    label: str,
    force_kind: str | None = None,
) -> None:
    src = await fetch_replied_file(update, context, want)
    if src is None:
        what = {VIDEO: "a video", AV: "a video or audio file"}.get(
            want, "a photo or image file" if "image" in want else "a file"
        )
        await need_media(update, what)
        return
    status = await reply(update, f"⚙️ {esc(label)}…")
    try:
        out = await work(src)
        s = svc(context)
        caption = f"✅ {esc(label)} · {human_size(out.stat().st_size)}"
        await s.delivery.deliver(
            context.bot,
            update.effective_chat.id,
            out,
            caption,
            user_id=user.id,
            preferred=user.pref("delivery"),
            force_kind=force_kind,
            reply_to=update.effective_message.message_id,
        )
        if status:
            await status.delete()
    except media.MediaError as exc:
        if status:
            await status.edit_text(f"⚠️ {esc(exc)}")
    finally:
        shutil.rmtree(src.parent, ignore_errors=True)


def _arg(context: Ctx, index: int = 0) -> str | None:
    args = context.args or []
    return args[index] if len(args) > index else None


@command(
    "convert",
    "tools",
    "Convert a file: mp4 mkv webm mov gif mp3 m4a flac wav opus ogg jpg png webp",
    "/convert <format>  (reply to a file)",
    needs_ffmpeg=True,
)
async def convert(update: Update, context: Ctx, user: User) -> None:
    target = (_arg(context) or "").lower().lstrip(".")
    allowed = media.VIDEO_TARGETS | media.AUDIO_TARGETS | media.IMAGE_TARGETS
    if target not in allowed:
        await reply(
            update, "Usage: reply to a file with <code>/convert mp4</code>\nFormats: " + ", ".join(sorted(allowed))
        )
        return
    await run_tool(
        update,
        context,
        user,
        ("video", "audio", "image", "file"),
        lambda p: media.convert(p, target),
        f"Converted to {target.upper()}",
    )


@command(
    "compress",
    "tools",
    "Shrink a video (quality 18–40, or a target size like 20MB)",
    "/compress [28 | 20MB]  (reply to a video)",
    needs_ffmpeg=True,
)
async def compress(update: Update, context: Ctx, user: User) -> None:
    arg = (_arg(context) or "").lower()
    target_bytes, crf = None, 28
    try:
        if arg.isdigit() and 18 <= int(arg) <= 40:
            crf = int(arg)
        elif arg:
            target_bytes = parse_size(arg)
    except ValueError:
        await reply(update, "Usage: <code>/compress</code>, <code>/compress 32</code> or <code>/compress 20MB</code>")
        return
    await run_tool(update, context, user, VIDEO, lambda p: media.compress(p, target_bytes, crf), "Compressed")


@command(
    "trim",
    "tools",
    "Cut a video/audio between two times",
    "/trim <start> [end]  e.g. /trim 0:30 1:15",
    needs_ffmpeg=True,
)
async def trim(update: Update, context: Ctx, user: User) -> None:
    try:
        start = parse_timestamp(_arg(context, 0) or "")
        end_arg = _arg(context, 1)
        end = parse_timestamp(end_arg) if end_arg else None
    except ValueError:
        await reply(update, "Usage: reply to a file with <code>/trim 0:30 1:15</code>")
        return
    label = f"Trimmed {human_duration(start)}–{human_duration(end) if end else 'end'}"
    await run_tool(update, context, user, AV, lambda p: media.trim(p, start, end), label)


@command(
    "extractaudio",
    "tools",
    "Extract the soundtrack of a video",
    "/extractaudio [mp3|m4a|flac|wav|opus]",
    needs_ffmpeg=True,
)
async def extractaudio(update: Update, context: Ctx, user: User) -> None:
    fmt = (_arg(context) or user.pref("audio_format")).lower()
    if fmt not in media.AUDIO_TARGETS:
        fmt = "mp3"
    await run_tool(
        update,
        context,
        user,
        AV,
        lambda p: media.extract_audio(p, fmt, int(user.pref("audio_bitrate"))),
        f"Audio ({fmt.upper()})",
    )


@command("mute", "tools", "Remove the sound from a video", "/mute  (reply to a video)", needs_ffmpeg=True)
async def mute(update: Update, context: Ctx, user: User) -> None:
    await run_tool(update, context, user, VIDEO, media.mute, "Muted")


@command("speed", "tools", "Change playback speed (0.25–4)", "/speed <factor>  e.g. /speed 1.5", needs_ffmpeg=True)
async def speed(update: Update, context: Ctx, user: User) -> None:
    try:
        factor = float((_arg(context) or "").rstrip("x"))
    except ValueError:
        await reply(update, "Usage: reply with <code>/speed 1.5</code> (0.25–4)")
        return
    await run_tool(update, context, user, AV, lambda p: media.speed(p, factor), f"Speed ×{factor:g}")


@command("rotate", "tools", "Rotate a video or image by 90/180/270°", "/rotate <90|180|270>", needs_ffmpeg=True)
async def rotate(update: Update, context: Ctx, user: User) -> None:
    arg = _arg(context) or "90"
    if arg.lstrip("-") not in ("90", "180", "270"):
        await reply(update, "Usage: reply with <code>/rotate 90</code> (90, 180 or 270)")
        return
    deg = int(arg)
    await run_tool(update, context, user, ("video", "image"), lambda p: media.rotate(p, deg), f"Rotated {deg}°")


@command(
    "resize", "tools", "Resize a video or image to a height", "/resize <height>  e.g. /resize 720", needs_ffmpeg=True
)
async def resize(update: Update, context: Ctx, user: User) -> None:
    arg = (_arg(context) or "").rstrip("p")
    if not arg.isdigit():
        await reply(update, "Usage: reply with <code>/resize 720</code>")
        return
    await run_tool(update, context, user, ("video", "image"), lambda p: media.resize(p, int(arg)), f"Resized to {arg}p")


@command("togif", "tools", "Turn a video into a GIF (up to 30 s)", "/togif [fps] [width]", needs_ffmpeg=True)
async def togif(update: Update, context: Ctx, user: User) -> None:
    fps = int(_arg(context, 0)) if (_arg(context, 0) or "").isdigit() else 12
    width = int(_arg(context, 1)) if (_arg(context, 1) or "").isdigit() else 480
    await run_tool(
        update, context, user, VIDEO, lambda p: media.to_gif(p, min(max(fps, 1), 30), min(max(width, 64), 1280)), "GIF"
    )


@command("frame", "tools", "Grab a still image from a video", "/frame [time]  e.g. /frame 1:23", needs_ffmpeg=True)
async def frame(update: Update, context: Ctx, user: User) -> None:
    try:
        at = parse_timestamp(_arg(context) or "1")
    except ValueError:
        await reply(update, "Usage: reply to a video with <code>/frame 1:23</code>")
        return
    await run_tool(update, context, user, VIDEO, lambda p: media.frame(p, at), f"Frame at {human_duration(at)}")


@command("volume", "tools", "Make audio louder or quieter (e.g. 1.5 or 0.5)", "/volume <factor>", needs_ffmpeg=True)
async def volume(update: Update, context: Ctx, user: User) -> None:
    try:
        factor = float((_arg(context) or "").rstrip("x"))
    except ValueError:
        await reply(update, "Usage: reply with <code>/volume 1.5</code>")
        return
    await run_tool(update, context, user, AV, lambda p: media.volume(p, factor), f"Volume ×{factor:g}")


@command("reverse", "tools", "Play a clip backwards (up to 60 s)", "/reverse", needs_ffmpeg=True)
async def reverse(update: Update, context: Ctx, user: User) -> None:
    await run_tool(update, context, user, AV, media.reverse, "Reversed")


@command("tovoice", "tools", "Convert audio/video to a Telegram voice message", "/tovoice", needs_ffmpeg=True)
async def tovoice(update: Update, context: Ctx, user: User) -> None:
    await run_tool(update, context, user, AV, media.to_voice, "Voice message", force_kind="voice")


@command("tonote", "tools", "Convert a video to a round video message (≤ 60 s)", "/tonote", needs_ffmpeg=True)
async def tonote(update: Update, context: Ctx, user: User) -> None:
    await run_tool(update, context, user, VIDEO, media.to_video_note, "Video note", force_kind="video_note")


@command(
    "mediainfo",
    "tools",
    "Technical details: codecs, resolution, bitrate…",
    "/mediainfo (reply to a file)",
    needs_ffmpeg=True,
)
async def mediainfo(update: Update, context: Ctx, user: User) -> None:
    src = await fetch_replied_file(update, context, ("video", "audio", "file"))
    if src is None:
        await need_media(update, "a video or audio file")
        return
    try:
        info = media.summarize_probe(await media.probe(src))
    except media.MediaError as exc:
        await reply(update, f"⚠️ {esc(exc)}")
        return
    finally:
        shutil.rmtree(src.parent, ignore_errors=True)
    rows = [
        ("Container", info["container"]),
        ("Duration", human_duration(info["duration"]) if info["duration"] else None),
        ("Size", human_size(info["size"]) if info["size"] else None),
        ("Bitrate", f"{info['bitrate'] // 1000} kb/s" if info["bitrate"] else None),
        ("Resolution", f"{info['width']}×{info['height']}" if info["width"] else None),
        ("FPS", info["fps"]),
        ("Video codec", info["vcodec"]),
        ("Audio codec", info["acodec"]),
        ("Sample rate", f"{info['sample_rate']} Hz" if info["sample_rate"] else None),
        ("Channels", info["channels"]),
        ("Streams", info["streams"]),
    ]
    await reply(update, "🔬 <b>Media info</b>\n" + "\n".join(f"<b>{k}:</b> {esc(v)}" for k, v in rows if v))


@command("imgconvert", "tools", "Convert an image: jpg png webp bmp gif tiff", "/imgconvert <format>")
async def imgconvert(update: Update, context: Ctx, user: User) -> None:
    target = (_arg(context) or "").lower()
    if target not in media.IMAGE_TARGETS:
        await reply(
            update,
            "Usage: reply to an image with <code>/imgconvert png</code>\nFormats: "
            + ", ".join(sorted(media.IMAGE_TARGETS)),
        )
        return

    async def work(p: Path) -> Path:
        return await asyncio.to_thread(media.image_convert, p, target)

    await run_tool(update, context, user, ("image", "file"), work, f"Image as {target.upper()}")


@command("imgresize", "tools", "Resize an image to a height (keeps aspect ratio)", "/imgresize <height>")
async def imgresize(update: Update, context: Ctx, user: User) -> None:
    arg = (_arg(context) or "").rstrip("p")
    if not arg.isdigit() or not 16 <= int(arg) <= 10000:
        await reply(update, "Usage: reply to an image with <code>/imgresize 1080</code>")
        return

    async def work(p: Path) -> Path:
        return await asyncio.to_thread(media.image_resize, p, int(arg))

    await run_tool(update, context, user, ("image", "file"), work, f"Resized to {arg}px")


@command("imginfo", "tools", "Image size, format and EXIF data", "/imginfo (reply to an image file)")
async def imginfo(update: Update, context: Ctx, user: User) -> None:
    src = await fetch_replied_file(update, context, ("image", "file"))
    if src is None:
        await need_media(update, "an image (send it as a file to keep EXIF data)")
        return
    try:
        info = await asyncio.to_thread(media.image_info, src)
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Not a readable image: {esc(str(exc)[:200])}")
        return
    finally:
        shutil.rmtree(src.parent, ignore_errors=True)
    lines = [
        f"🖼 <b>{info['format']}</b> · {info['width']}×{info['height']} · {info['mode']} · "
        f"{human_size(info['size'])}" + (f" · {info['frames']} frames" if info["frames"] > 1 else "")
    ]
    for key in ("Make", "Model", "DateTime", "Software", "LensModel", "ExposureTime", "FNumber", "ISOSpeedRatings"):
        if key in info["exif"]:
            lines.append(f"<b>{key}:</b> {esc(info['exif'][key])}")
    if len(lines) == 1:
        lines.append("No EXIF data (Telegram strips it from photos; send the image as a file to keep it).")
    await reply(update, "\n".join(lines))


# ------------------------------------------------------------------ quick actions for files sent without a command

QUICK_ACTIONS = {
    "mp3": ("🎵 MP3", AV),
    "compress": ("🗜 Compress", VIDEO),
    "gif": ("🎞 GIF", VIDEO),
    "mute": ("🔇 Mute", VIDEO),
    "voice": ("🎙 Voice", AV),
    "note": ("⭕ Round", VIDEO),
    "info": ("🔬 Info", AV),
    "png": ("🖼 PNG", IMAGE),
    "jpg": ("🖼 JPG", IMAGE),
    "webp": ("🖼 WebP", IMAGE),
}


def quick_keyboard(kind: str) -> InlineKeyboardMarkup:
    keys = (
        ["png", "jpg", "webp"]
        if kind == "image"
        else (["mp3", "voice", "info"] if kind == "audio" else ["mp3", "compress", "gif", "mute", "note", "info"])
    )
    buttons = [B(QUICK_ACTIONS[k][0], callback_data=f"tool:{k}") for k in keys]
    return InlineKeyboardMarkup([buttons[i : i + 3] for i in range(0, len(buttons), 3)])


async def on_media_message(update: Update, context: Ctx, user: User) -> None:
    msg = update.effective_message
    if msg.photo or (msg.document and (msg.document.mime_type or "").startswith("image/")):
        kind = "image"
    elif msg.audio or msg.voice:
        kind = "audio"
    else:
        kind = "video"
    await msg.reply_text(
        "What should I do with this file?", reply_markup=quick_keyboard(kind), parse_mode=ParseMode.HTML
    )


async def on_tool_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    action = (query.data or "").split(":", 1)[1]
    await query.answer()
    if action not in QUICK_ACTIONS:
        return
    context.args = []
    handlers = {
        "mp3": lambda: run_tool(update, context, user, AV, lambda p: media.extract_audio(p, "mp3"), "MP3"),
        "compress": lambda: run_tool(update, context, user, VIDEO, lambda p: media.compress(p), "Compressed"),
        "gif": lambda: run_tool(update, context, user, VIDEO, media.to_gif, "GIF"),
        "mute": lambda: run_tool(update, context, user, VIDEO, media.mute, "Muted"),
        "voice": lambda: run_tool(update, context, user, AV, media.to_voice, "Voice message", force_kind="voice"),
        "note": lambda: run_tool(
            update, context, user, VIDEO, media.to_video_note, "Video note", force_kind="video_note"
        ),
        "info": lambda: mediainfo(update, context, user),
    }
    for fmt in ("png", "jpg", "webp"):
        handlers[fmt] = lambda f=fmt: run_tool(
            update,
            context,
            user,
            ("image", "file"),
            lambda p: asyncio.to_thread(media.image_convert, p, f),
            f"Image as {f.upper()}",
        )
    await handlers[action]()
