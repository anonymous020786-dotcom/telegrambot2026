"""Generate COMMANDS.md from the command registry.

python -m scripts.gen_commands          # write COMMANDS.md
python -m scripts.gen_commands --botfather   # print the list for @BotFather /setcommands
"""

from __future__ import annotations

import sys
from pathlib import Path

from bot.app import load_handlers
from bot.registry import GROUPS, REGISTRY, by_group, menu_commands

ROOT = Path(__file__).resolve().parents[1]


def render() -> str:
    load_handlers()
    groups = by_group(include_admin=True)
    users = sum(1 for c in REGISTRY.values() if not c.admin)
    admins = len(REGISTRY) - users
    lines = [
        "# Command reference",
        "",
        f"{len(REGISTRY)} commands: {users} for users and {admins} for admins.",
        "This file is generated from the command registry; run `python -m scripts.gen_commands` after changes.",
        "",
        "Tips: paste any link to get a preview with buttons. Media tools work when you reply to a file, or send",
        "the file with the command as its caption.",
        "",
    ]
    for group, cmds in groups.items():
        lines += [f"## {GROUPS[group]} ({len(cmds)})", "", "| Command | What it does | Usage |", "| --- | --- | --- |"]
        for c in cmds:
            usage = c.usage.replace("|", "\\|")
            desc = c.description.replace("|", "\\|")
            lines.append(f"| `/{c.name}` | {desc} | `{usage}` |")
        lines.append("")
    return "\n".join(lines)


def botfather() -> str:
    load_handlers()
    return "\n".join(f"{c.name} - {c.description}" for c in menu_commands())


if __name__ == "__main__":
    if "--botfather" in sys.argv:
        print(botfather())
    else:
        (ROOT / "COMMANDS.md").write_text(render(), encoding="utf-8")
        print(f"Wrote COMMANDS.md ({len(REGISTRY)} commands)")
