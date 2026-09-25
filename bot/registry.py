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
    seq: int = 0  # registration order within the process (tie-breaker)


REGISTRY: dict[str, Command] = {}

# Handler modules in display order; keeps /help, menus and COMMANDS.md stable regardless of import order.
MODULE_ORDER = (
    "general",
    "download",
    "images",
    "tools",
    "library",
    "settings",
    "content",
    "watch",
    "utilities",
    "admin",
)


def sort_key(cmd: Command) -> tuple[int, int, int]:
    module = cmd.handler.__module__.rsplit(".", 1)[-1]
    index = MODULE_ORDER.index(module) if module in MODULE_ORDER else len(MODULE_ORDER)
    return index, cmd.handler.__code__.co_firstlineno, cmd.seq


def ordered() -> list[Command]:
    return sorted(REGISTRY.values(), key=sort_key)


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
        REGISTRY[name] = Command(
            name, group, description, fn, usage or f"/{name}", admin, public, menu, needs_ffmpeg, len(REGISTRY)
        )
        return fn

    return deco


def by_group(include_admin: bool = False) -> dict[str, list[Command]]:
    out: dict[str, list[Command]] = {g: [] for g in GROUPS}
    for cmd in ordered():
        if cmd.admin and not include_admin:
            continue
        out[cmd.group].append(cmd)
    return {g: cmds for g, cmds in out.items() if cmds}


def menu_commands(admin: bool = False, limit: int = 100) -> list[Command]:
    """Commands for Telegram's command menu (Telegram allows at most 100 per scope)."""
    cmds = [c for c in ordered() if c.menu and not c.admin]
    if admin:
        cmds += [c for c in ordered() if c.admin and c.menu]
    return cmds[:limit]
