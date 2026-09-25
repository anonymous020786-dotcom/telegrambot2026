"""Single source of truth for commands: drives /help, the Telegram menu, docs and tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Handler = Callable[..., Awaitable[Any]]

# Display order and titles of command groups.
GROUPS: dict[str, str] = {
    "general": "🏠 General",
    "download": "⬇️ Download",
    "audio": "🎵 Audio",
    "extras": "🧾 Media details",
    "images": "🖼 Images",
    "tools": "🛠 Media tools",
    "search": "🔎 Search",
    "queue": "📋 Queue",
    "library": "📚 History & favorites",
    "settings": "⚙️ Settings",
    "account": "👤 Account",
    "watch": "⏰ Subscriptions & schedules",
    "utils": "🧰 Utilities",
    "admin": "🛡 Admin",
}


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    description: str
    handler: Handler
    usage: str = ""
    admin: bool = False
    public: bool = False  # usable before access is granted
    menu: bool = True  # include in Telegram's "/" menu (max 100 per scope)
    needs_ffmpeg: bool = False


REGISTRY: dict[str, Command] = {}


def command(
    name: str,
    group: str,
    description: str,
    usage: str = "",
    *,
    admin: bool = False,
    public: bool = False,
    menu: bool = True,
    needs_ffmpeg: bool = False,
) -> Callable[[Handler], Handler]:
    if group not in GROUPS:
        raise ValueError(f"unknown group {group}")

    def deco(fn: Handler) -> Handler:
        if name in REGISTRY:
            raise ValueError(f"duplicate command /{name}")
        REGISTRY[name] = Command(name, group, description, fn, usage or f"/{name}", admin, public, menu, needs_ffmpeg)
        return fn

    return deco


def by_group(include_admin: bool = False) -> dict[str, list[Command]]:
    out: dict[str, list[Command]] = {g: [] for g in GROUPS}
    for cmd in REGISTRY.values():
        if cmd.admin and not include_admin:
            continue
        out[cmd.group].append(cmd)
    return {g: cmds for g, cmds in out.items() if cmds}


def menu_commands(admin: bool = False, limit: int = 100) -> list[Command]:
    """Commands for Telegram's command menu (Telegram allows at most 100 per scope)."""
    cmds = [c for c in REGISTRY.values() if c.menu and not c.admin]
    if admin:
        cmds += [c for c in REGISTRY.values() if c.admin and c.menu]
    return cmds[:limit]
