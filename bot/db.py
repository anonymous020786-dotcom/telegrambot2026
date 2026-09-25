"""SQLite persistence (aiosqlite)."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',        -- user | admin | banned
    allowed INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER,                      -- NULL = global default, 0 = unlimited
    settings TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    kind TEXT,
    preset TEXT,
    size INTEGER,
    file_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    size INTEGER,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    preset TEXT NOT NULL,
    seen TEXT NOT NULL DEFAULT '[]',
    title TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_check REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    preset TEXT NOT NULL,
    run_at REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL,
    uses_left INTEGER NOT NULL,
    expires_at REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Defaults for per-user preferences (see /settings).
DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "quality": "best",  # best | 2160 | 1440 | 1080 | 720 | 480 | 360 | worst
    "container": "mp4",  # mp4 | mkv | webm
    "audio_format": "mp3",  # mp3 | m4a | opus | flac | wav | aac | ogg
    "audio_bitrate": 192,  # kbps for lossy audio
    "delivery": "auto",  # auto | telegram | split | link | s3
    "as_document": False,
    "subs": False,  # embed subtitles in videos
    "sub_lang": "en",
    "embed_thumbnail": True,
    "embed_metadata": True,
    "caption": "full",  # full | minimal | off
    "filename": "%(title).80B",
    "playlist_limit": 25,
    "timezone": "UTC",
}


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class User:
    id: int
    username: str | None
    first_name: str | None
    role: str
    allowed: bool
    daily_limit: int | None
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    last_seen: float = 0.0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_banned(self) -> bool:
        return self.role == "banned"

    def pref(self, key: str) -> Any:
        return self.settings.get(key, DEFAULT_USER_SETTINGS.get(key))

    @property
    def display(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.id)


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        first_name=row["first_name"],
        role=row["role"],
        allowed=bool(row["allowed"]),
        daily_limit=row["daily_limit"],
        settings={**DEFAULT_USER_SETTINGS, **json.loads(row["settings"] or "{}")},
        created_at=row["created_at"],
        last_seen=row["last_seen"],
    )


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    @property
    def c(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        return self.conn

    async def _one(self, sql: str, args: tuple = ()) -> aiosqlite.Row | None:
        async with self.c.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, args: tuple = ()) -> list[aiosqlite.Row]:
        async with self.c.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def _exec(self, sql: str, args: tuple = ()) -> int:
        """Run a write statement; returns the number of affected rows."""
        cur = await self.c.execute(sql, args)
        await self.c.commit()
        return cur.rowcount

    async def _insert(self, sql: str, args: tuple = ()) -> int:
        """Run an INSERT; returns the new row id."""
        cur = await self.c.execute(sql, args)
        await self.c.commit()
        return cur.lastrowid or 0

    # ------------------------------------------------------------------ users
    async def upsert_user(self, uid: int, username: str | None, first_name: str | None) -> User:
        now = time.time()
        await self.c.execute(
            """INSERT INTO users (id, username, first_name, created_at, last_seen) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name,
               last_seen=excluded.last_seen""",
            (uid, username, first_name, now, now),
        )
        await self.c.commit()
        user = await self.get_user(uid)
        assert user is not None
        return user

    async def get_user(self, uid: int) -> User | None:
        row = await self._one("SELECT * FROM users WHERE id=?", (uid,))
        return _row_to_user(row) if row else None

    async def find_user(self, ref: str) -> User | None:
        ref = ref.strip().lstrip("@")
        if ref.lstrip("-").isdigit():
            return await self.get_user(int(ref))
        row = await self._one("SELECT * FROM users WHERE lower(username)=lower(?)", (ref,))
        return _row_to_user(row) if row else None

    async def ensure_user(self, uid: int) -> User:
        user = await self.get_user(uid)
        if user:
            return user
        now = time.time()
        await self._exec("INSERT INTO users (id, created_at, last_seen) VALUES (?, ?, ?)", (uid, now, now))
        return await self.get_user(uid)  # type: ignore[return-value]

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        rows = await self._all("SELECT * FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?", (limit, offset))
        return [_row_to_user(r) for r in rows]

    async def count_users(self) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM users")
        return row["n"] if row else 0

    async def set_role(self, uid: int, role: str) -> None:
        await self.ensure_user(uid)
        await self._exec("UPDATE users SET role=? WHERE id=?", (role, uid))

    async def set_allowed(self, uid: int, allowed: bool) -> None:
        await self.ensure_user(uid)
        await self._exec("UPDATE users SET allowed=? WHERE id=?", (int(allowed), uid))

    async def set_daily_limit(self, uid: int, limit: int | None) -> None:
        await self.ensure_user(uid)
        await self._exec("UPDATE users SET daily_limit=? WHERE id=?", (limit, uid))

    async def update_settings(self, uid: int, **changes: Any) -> dict[str, Any]:
        user = await self.ensure_user(uid)
        stored = {k: v for k, v in user.settings.items() if DEFAULT_USER_SETTINGS.get(k) != v}
        stored.update(changes)
        stored = {k: v for k, v in stored.items() if DEFAULT_USER_SETTINGS.get(k) != v}
        await self._exec("UPDATE users SET settings=? WHERE id=?", (json.dumps(stored), uid))
        return {**DEFAULT_USER_SETTINGS, **stored}

    async def reset_settings(self, uid: int) -> None:
        await self._exec("UPDATE users SET settings='{}' WHERE id=?", (uid,))

    async def all_user_ids(self, only_allowed: bool = True) -> list[int]:
        sql = "SELECT id FROM users WHERE role != 'banned'"
        if only_allowed:
            sql += " AND (allowed=1 OR role='admin')"
        return [r["id"] for r in await self._all(sql)]

    # ------------------------------------------------------------------ usage / quota
    async def add_usage(self, uid: int, count: int = 1, size: int = 0) -> None:
        await self._exec(
            """INSERT INTO usage (user_id, day, count, bytes) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, day) DO UPDATE SET count=count+excluded.count, bytes=bytes+excluded.bytes""",
            (uid, today(), count, size),
        )

    async def usage_today(self, uid: int) -> tuple[int, int]:
        row = await self._one("SELECT count, bytes FROM usage WHERE user_id=? AND day=?", (uid, today()))
        return (row["count"], row["bytes"]) if row else (0, 0)

    async def usage_totals(self, uid: int | None = None) -> tuple[int, int]:
        if uid is None:
            row = await self._one("SELECT COALESCE(SUM(count),0) AS c, COALESCE(SUM(bytes),0) AS b FROM usage")
        else:
            row = await self._one(
                "SELECT COALESCE(SUM(count),0) AS c, COALESCE(SUM(bytes),0) AS b FROM usage WHERE user_id=?", (uid,)
            )
        return (row["c"], row["b"]) if row else (0, 0)

    # ------------------------------------------------------------------ history
    async def add_history(
        self,
        uid: int,
        url: str,
        *,
        title: str | None,
        kind: str | None,
        preset: dict[str, Any],
        status: str,
        size: int | None = None,
        file_id: str | None = None,
        error: str | None = None,
    ) -> int:
        return await self._insert(
            """INSERT INTO history (user_id, url, title, kind, preset, size, file_id, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, url, title, kind, json.dumps(preset), size, file_id, status, error, time.time()),
        )

    async def history(self, uid: int, limit: int = 10, offset: int = 0, favorites: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM history WHERE user_id=?"
        if favorites:
            sql += " AND favorite=1"
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        return await self._all(sql, (uid, limit, offset))

    async def count_history(self, uid: int, favorites: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM history WHERE user_id=?" + (" AND favorite=1" if favorites else "")
        row = await self._one(sql, (uid,))
        return row["n"] if row else 0

    async def history_item(self, uid: int, item_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM history WHERE id=? AND user_id=?", (item_id, uid))

    async def last_history(self, uid: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM history WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))

    async def search_history(self, uid: int, query: str, limit: int = 20) -> list[aiosqlite.Row]:
        like = f"%{query}%"
        return await self._all(
            "SELECT * FROM history WHERE user_id=? AND (title LIKE ? OR url LIKE ?) ORDER BY id DESC LIMIT ?",
            (uid, like, like, limit),
        )

    async def set_favorite(self, uid: int, item_id: int, fav: bool) -> bool:
        return await self._exec("UPDATE history SET favorite=? WHERE id=? AND user_id=?", (int(fav), item_id, uid)) > 0

    async def clear_history(self, uid: int, keep_favorites: bool = True) -> int:
        sql = "DELETE FROM history WHERE user_id=?" + (" AND favorite=0" if keep_favorites else "")
        cur = await self.c.execute(sql, (uid,))
        await self.c.commit()
        return cur.rowcount

    async def global_stats(self) -> dict[str, Any]:
        users = await self.count_users()
        row = await self._one(
            """SELECT COUNT(*) AS jobs, SUM(status='done') AS ok, SUM(status='failed') AS failed,
               COALESCE(SUM(CASE WHEN status='done' THEN size END),0) AS bytes FROM history"""
        )
        top = await self._all(
            """SELECT kind, COUNT(*) AS n FROM history WHERE status='done' GROUP BY kind ORDER BY n DESC"""
        )
        return {
            "users": users,
            "jobs": row["jobs"] or 0,
            "ok": row["ok"] or 0,
            "failed": row["failed"] or 0,
            "bytes": row["bytes"] or 0,
            "kinds": {r["kind"] or "?": r["n"] for r in top},
        }

    # ------------------------------------------------------------------ file_id cache
    async def cache_get(self, key: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM cache WHERE key=?", (key,))

    async def cache_put(self, key: str, file_id: str, kind: str, title: str | None, size: int | None) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO cache (key, file_id, kind, title, size, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, file_id, kind, title, size, time.time()),
        )

    async def cache_clear(self) -> int:
        cur = await self.c.execute("DELETE FROM cache")
        await self.c.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ watches
    async def add_watch(
        self, uid: int, chat_id: int, url: str, preset: dict[str, Any], title: str | None, seen: list[str]
    ) -> int:
        return await self._insert(
            "INSERT INTO watches (user_id, chat_id, url, preset, seen, title, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, chat_id, url, json.dumps(preset), json.dumps(seen), title, time.time()),
        )

    async def watches(self, uid: int | None = None) -> list[aiosqlite.Row]:
        if uid is None:
            return await self._all("SELECT * FROM watches WHERE active=1")
        return await self._all("SELECT * FROM watches WHERE user_id=? AND active=1 ORDER BY id", (uid,))

    async def update_watch_seen(self, watch_id: int, seen: list[str]) -> None:
        await self._exec(
            "UPDATE watches SET seen=?, last_check=? WHERE id=?", (json.dumps(seen[-500:]), time.time(), watch_id)
        )

    async def remove_watch(self, uid: int, watch_id: int) -> bool:
        return await self._exec("UPDATE watches SET active=0 WHERE id=? AND user_id=?", (watch_id, uid)) > 0

    # ------------------------------------------------------------------ schedules
    async def add_schedule(self, uid: int, chat_id: int, url: str, preset: dict[str, Any], run_at: float) -> int:
        return await self._insert(
            "INSERT INTO schedules (user_id, chat_id, url, preset, run_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, chat_id, url, json.dumps(preset), run_at, time.time()),
        )

    async def pending_schedules(self, uid: int | None = None) -> list[aiosqlite.Row]:
        if uid is None:
            return await self._all("SELECT * FROM schedules WHERE done=0 ORDER BY run_at")
        return await self._all("SELECT * FROM schedules WHERE done=0 AND user_id=? ORDER BY run_at", (uid,))

    async def finish_schedule(self, sched_id: int) -> None:
        await self._exec("UPDATE schedules SET done=1 WHERE id=?", (sched_id,))

    async def cancel_schedule(self, uid: int, sched_id: int) -> bool:
        return await self._exec("UPDATE schedules SET done=2 WHERE id=? AND user_id=? AND done=0", (sched_id, uid)) > 0

    # ------------------------------------------------------------------ invites
    async def create_invite(self, created_by: int, uses: int = 1, ttl_hours: float | None = 72) -> str:
        code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        expires = time.time() + ttl_hours * 3600 if ttl_hours else None
        await self._exec(
            "INSERT INTO invites (code, created_by, uses_left, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (code, created_by, uses, expires, time.time()),
        )
        return code

    async def invites(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM invites WHERE uses_left > 0 ORDER BY created_at DESC")

    async def redeem_invite(self, code: str, uid: int) -> bool:
        row = await self._one("SELECT * FROM invites WHERE code=?", (code.strip(),))
        if not row or row["uses_left"] <= 0 or (row["expires_at"] and row["expires_at"] < time.time()):
            return False
        await self._exec("UPDATE invites SET uses_left=uses_left-1 WHERE code=?", (row["code"],))
        await self.set_allowed(uid, True)
        return True

    async def revoke_invite(self, code: str) -> bool:
        return await self._exec("DELETE FROM invites WHERE code=?", (code.strip(),)) > 0

    # ------------------------------------------------------------------ feedback
    async def add_feedback(self, uid: int, text: str) -> int:
        return await self._insert(
            "INSERT INTO feedback (user_id, text, created_at) VALUES (?, ?, ?)", (uid, text, time.time())
        )

    async def feedback(self, limit: int = 20) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ key/value
    async def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_kv(self, key: str, value: str) -> None:
        await self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))

    # ------------------------------------------------------------------ backup
    def backup_to(self, dest: Path) -> Path:
        """Consistent copy of the database (runs synchronously; call in a thread)."""
        src = sqlite3.connect(self.path)
        try:
            out = sqlite3.connect(dest)
            with out:
                src.backup(out)
            out.close()
        finally:
            src.close()
        return dest
