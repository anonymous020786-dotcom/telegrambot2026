"""Content policy: DRM streaming services, admin domain blocklist, and 18+ content opt-in."""

from __future__ import annotations

import json
from typing import Any

from ..utils import domain_of
from .netguard import check_url

# Subscription streaming services whose video is DRM-encrypted (Widevine/PlayReady/FairPlay).
# The bot never circumvents DRM, so links to these are refused up front with a clear explanation.
DRM_SERVICES: dict[str, str] = {
    "netflix.com": "Netflix",
    "primevideo.com": "Prime Video",
    "disneyplus.com": "Disney+",
    "hulu.com": "Hulu",
    "max.com": "Max",
    "hbomax.com": "Max",
    "tv.apple.com": "Apple TV+",
    "paramountplus.com": "Paramount+",
    "peacocktv.com": "Peacock",
    "sonyliv.com": "SonyLIV",
    "zee5.com": "ZEE5",
    "jiocinema.com": "JioCinema",
    "jiohotstar.com": "JioHotstar",
    "spotify.com": "Spotify",
    "music.apple.com": "Apple Music",
    "tidal.com": "Tidal",
    "deezer.com": "Deezer",
}

ADULT_MODES = ("off", "optin")


def _matches(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith("." + rule)


def drm_service(url: str) -> str | None:
    domain = domain_of(url)
    if "amazon." in domain and "/gp/video" in url:
        return "Prime Video"
    for rule, name in DRM_SERVICES.items():
        if _matches(domain, rule):
            return name
    return None


def normalize_domain(value: str) -> str | None:
    value = value.strip().lower()
    if "://" in value:
        value = domain_of(value)
    value = value.removeprefix("www.").strip("./")
    if not value or "." not in value or any(c in value for c in " /\\@:"):
        return None
    return value


def is_blocked(url: str, blocked: list[str]) -> str | None:
    domain = domain_of(url)
    return next((rule for rule in blocked if _matches(domain, rule)), None)


def drm_message(service: str) -> str:
    return (
        f"🔒 {service} is a DRM-protected (encrypted) streaming service. This bot does not bypass DRM, so it "
        "can't download from it. Free, DRM-free clips and trailers on public sites still work."
    )


def is_adult(info: dict[str, Any] | None) -> bool:
    """True when the site rates the media 18+ (yt-dlp reports age_limit for adult sites)."""
    return bool(info) and int(info.get("age_limit") or 0) >= 18


class Policy:
    """Reads admin-controlled switches from the database, with environment defaults."""

    def __init__(self, db, settings):
        self.db = db
        self.settings = settings

    async def blocked_domains(self) -> list[str]:
        stored = json.loads(await self.db.get_kv("blocked_domains", "[]") or "[]")
        env = [d for d in (normalize_domain(x) for x in self.settings.blocked_domains) if d]
        return sorted(set(stored) | set(env))

    async def set_blocked(self, domains: list[str]) -> None:
        await self.db.set_kv("blocked_domains", json.dumps(sorted(set(domains))))

    async def adult_mode(self) -> str:
        mode = await self.db.get_kv("adult_mode") or self.settings.adult_content
        return mode if mode in ADULT_MODES else "off"

    async def adult_allowed(self, user) -> bool:
        return await self.adult_mode() == "optin" and bool(user.pref("adult_ok"))

    async def refusal(self, url: str) -> str | None:
        """A message explaining why this URL can't be downloaded, or None if it's fine."""
        if service := drm_service(url):
            return drm_message(service)
        if rule := is_blocked(url, await self.blocked_domains()):
            return f"⛔ Downloads from {rule} are blocked by the bot's admin."
        return await check_url(url, self.settings.allow_private_urls)


ADULT_BLOCKED_MESSAGE = (
    "🔞 This media is rated 18+. Adult content is off for you. "
    "If the admin allows it, adults can turn it on with /setadult."
)
