"""Admin-defined extraction rules for sites that no built-in extractor handles.

A rule is a regular expression applied to a page's HTML (with JavaScript-escaped slashes undone). Its first
capture group (or the whole match) is a stream URL. Rules are tried before the generic page-video fallback,
so a new site can be integrated from Telegram with /siterule, without changing code.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from ..utils import domain_of
from .pagevideo import VideoCandidate, _height, _kind

MAX_PATTERN = 400
MAX_RULES_PER_DOMAIN = 10


def validate_pattern(pattern: str) -> re.Pattern[str]:
    if not pattern or len(pattern) > MAX_PATTERN:
        raise ValueError(f"the pattern must be 1–{MAX_PATTERN} characters")
    try:
        compiled = re.compile(pattern, re.I)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc
    if compiled.groups > 1:
        raise ValueError("use at most one capture group (the stream URL)")
    return compiled


def rules_for(url: str, rules: dict[str, list[str]]) -> list[str]:
    domain = domain_of(url)
    return [p for d, patterns in rules.items() if domain == d or domain.endswith("." + d) for p in patterns]


def apply_rules(html: str, page_url: str, patterns: list[str]) -> list[VideoCandidate]:
    text = html.replace("\\/", "/").replace("&amp;", "&")
    found: dict[str, VideoCandidate] = {}
    for pattern in patterns:
        try:
            compiled = validate_pattern(pattern)
        except ValueError:
            continue
        for m in compiled.finditer(text):
            raw = m.group(1) if compiled.groups else m.group(0)
            url = urljoin(page_url, raw.strip())
            if urlparse(url).scheme in ("http", "https") and url not in found:
                window = text[max(0, m.start() - 60) : m.end() + 60]
                found[url] = VideoCandidate(url, "rule", _kind(url), _height(url, window))
    return sorted(found.values(), key=lambda c: (c.height, c.kind == "hls"), reverse=True)


class SiteRules:
    def __init__(self, db):
        self.db = db

    async def all(self) -> dict[str, list[str]]:
        return json.loads(await self.db.get_kv("site_rules", "{}") or "{}")

    async def add(self, domain: str, pattern: str) -> int:
        validate_pattern(pattern)
        rules = await self.all()
        patterns = rules.setdefault(domain, [])
        if pattern in patterns:
            return len(patterns)
        if len(patterns) >= MAX_RULES_PER_DOMAIN:
            raise ValueError(f"at most {MAX_RULES_PER_DOMAIN} rules per domain")
        patterns.append(pattern)
        await self.db.set_kv("site_rules", json.dumps(rules))
        return len(patterns)

    async def remove(self, domain: str, index: int | None = None) -> int:
        rules = await self.all()
        if domain not in rules:
            return 0
        if index is None:
            removed = len(rules.pop(domain))
        else:
            if not 1 <= index <= len(rules[domain]):
                return 0
            rules[domain].pop(index - 1)
            removed = 1
            if not rules[domain]:
                rules.pop(domain)
        await self.db.set_kv("site_rules", json.dumps(rules))
        return removed

    async def candidates(self, html: str, page_url: str) -> list[VideoCandidate]:
        patterns = rules_for(page_url, await self.all())
        return apply_rules(html, page_url, patterns) if patterns else []
