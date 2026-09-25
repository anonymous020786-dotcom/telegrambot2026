import inspect
from pathlib import Path

from bot.app import load_handlers
from bot.registry import GROUPS, REGISTRY, by_group, menu_commands

load_handlers()


def test_at_least_100_real_commands():
    user_cmds = [c for c in REGISTRY.values() if not c.admin]
    assert len(REGISTRY) >= 150
    assert len(user_cmds) >= 100


def test_every_command_is_well_formed():
    for name, cmd in REGISTRY.items():
        assert name == cmd.name, name
        assert name.islower() or name[0].isdigit(), name
        assert 1 <= len(name) <= 32 and all(c.isalnum() or c == "_" for c in name), name
        assert cmd.group in GROUPS, name
        assert 3 <= len(cmd.description) <= 256, name
        assert cmd.usage.startswith(f"/{name}"), name
        assert inspect.iscoroutinefunction(cmd.handler), name
        params = list(inspect.signature(cmd.handler).parameters)
        assert params[:3] == ["update", "context", "user"], name


def test_groups_and_menus():
    groups = by_group(include_admin=True)
    assert set(groups) == set(GROUPS)
    assert all(not c.admin for cmds in by_group().values() for c in cmds)
    assert len(menu_commands()) <= 100 and len(menu_commands(admin=True)) <= 100
    public = {c.name for c in REGISTRY.values() if c.public}
    assert {"start", "help", "id", "redeem", "request"} <= public
    assert not any(REGISTRY[n].admin for n in public)


def test_commands_doc_is_up_to_date():
    from scripts.gen_commands import render

    doc = Path(__file__).resolve().parents[1] / "COMMANDS.md"
    assert doc.read_text(encoding="utf-8") == render(), "Run: python -m scripts.gen_commands"
