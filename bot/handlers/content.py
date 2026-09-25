"""Content policy and site-access commands: 18+ opt-in, domain blocklist, cookies, live site checks."""

from __future__ import annotations

import io
import os
import shutil

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode

from ..db import User
from ..registry import command
from ..services.downloader import supported_extractor
from ..services.pagevideo import find_videos, page_is_adult
from ..services.policy import normalize_domain
from ..services.sitecheck import check, default_targets
from ..services.siterules import apply_rules, rules_for
from ..utils import esc, extract_urls, human_duration, truncate
from .common import Ctx, arg_text, fetch_replied_file, reply, svc, usage

# ------------------------------------------------------------------ 18+ content


@command(
    "setadult",
    "settings",
    "Allow 18+ media for yourself (needs admin permission and age confirmation)",
    "/setadult [on|off]",
)
async def setadult(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    arg = arg_text(context).lower()
    if arg == "off":
        await s.db.update_settings(user.id, adult_ok=False)
        await reply(update, "🔞 Adult content is now off for you.")
        return
    if await s.policy.adult_mode() != "optin":
        await reply(update, "🔞 Adult content is disabled on this bot by the admin.")
        return
    state = "on" if user.pref("adult_ok") else "off"
    await reply(
        update,
        f"🔞 Adult content is <b>{state}</b> for you.\n\nTurning it on lets the bot download media that websites "
        "rate 18+. Only continue if you are an adult and this is legal where you live.",
        reply_markup=InlineKeyboardMarkup(
            [
                [B("✅ I am 18 or older, turn it on", callback_data="adult:confirm")],
                [B("Keep it off", callback_data="adult:off")],
            ]
        ),
    )


async def on_adult_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    s = svc(context)
    action = (query.data or "").split(":", 1)[1]
    if action == "confirm" and await s.policy.adult_mode() == "optin":
        await s.db.update_settings(user.id, adult_ok=True)
        await query.answer("Adult content on")
        await query.message.edit_text(
            "🔞 Adult content is now <b>on</b> for you. Send /setadult off to turn it off.", parse_mode=ParseMode.HTML
        )
    else:
        await s.db.update_settings(user.id, adult_ok=False)
        await query.answer("Adult content off")
        await query.message.edit_text("🔞 Adult content stays off.")


@command("adult", "admin", "Adult content for the whole bot: off, or opt-in per user", "/adult <off|optin>", admin=True)
async def adult(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    arg = arg_text(context).lower()
    if arg not in ("off", "optin"):
        await reply(
            update,
            f"Adult content is <b>{await s.policy.adult_mode()}</b>.\n{usage('adult')}\n"
            "<i>optin</i>: users who confirm they're 18+ with /setadult can download media sites "
            "rate 18+. <i>off</i>: it's refused for everyone.",
        )
        return
    await s.db.set_kv("adult_mode", arg)
    await reply(update, f"🔞 Adult content: <b>{arg}</b>.")


# ------------------------------------------------------------------ domain blocklist


@command("blocksite", "admin", "Block all downloads from a domain", "/blocksite <domain>", admin=True)
async def blocksite(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    domain = normalize_domain(arg_text(context))
    if not domain:
        await reply(update, usage("blocksite"))
        return
    blocked = await s.policy.blocked_domains()
    await s.policy.set_blocked([*blocked, domain])
    await reply(update, f"⛔ Blocked <b>{esc(domain)}</b> and its subdomains.")


@command("unblocksite", "admin", "Remove a domain from the blocklist", "/unblocksite <domain>", admin=True)
async def unblocksite(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    domain = normalize_domain(arg_text(context))
    stored = [d for d in await s.policy.blocked_domains() if d not in s.settings.blocked_domains]
    if not domain or domain not in stored:
        env_note = " (domains set in BLOCKED_DOMAINS must be removed from the config)" if domain else ""
        await reply(update, f"{esc(domain or '')} isn't in the blocklist{env_note}. {usage('unblocksite')}")
        return
    await s.policy.set_blocked([d for d in stored if d != domain])
    await reply(update, f"✅ Unblocked <b>{esc(domain)}</b>.")


@command("blockedsites", "admin", "Domains that are blocked", admin=True)
async def blockedsites(update: Update, context: Ctx, user: User) -> None:
    blocked = await svc(context).policy.blocked_domains()
    await reply(update, "⛔ <b>Blocked domains</b>\n" + ("\n".join(esc(d) for d in blocked) or "none"))


# ------------------------------------------------------------------ cookies


def parse_cookie_file(text: str) -> list[str]:
    """Validate a Netscape cookies.txt; returns the cookie domains. Raises ValueError if it's not one."""
    domains: set[str] = set()
    valid = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) != 7 or not parts[4].lstrip("-").isdigit():
            raise ValueError("this doesn't look like a Netscape-format cookies.txt (7 tab-separated columns)")
        domains.add(parts[0].removeprefix("#HttpOnly_").lstrip("."))
        valid += 1
    if not valid:
        raise ValueError("no cookies found in the file")
    return sorted(domains)


@command(
    "cookies",
    "admin",
    "Use your own logged-in cookies for sites that need a login (reply to cookies.txt)",
    "/cookies [clear]  (reply to a cookies.txt file)",
    admin=True,
)
async def cookies(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    target = s.settings.uploaded_cookies
    if arg_text(context).lower() == "clear":
        target.unlink(missing_ok=True)
        await reply(update, "🍪 Uploaded cookies deleted.")
        return
    src = await fetch_replied_file(update, context, ("file",))
    if src is None:
        active = s.settings.cookies_path()
        await reply(
            update,
            (
                f"🍪 Cookies: <b>{'active' if active else 'none'}</b>"
                + (f" ({len(parse_cookie_file(active.read_text(errors='replace')))} domains)" if active else "")
                + "\n\n"
                "Some sites (for example Instagram, or age-checked videos) need a logged-in session even for public "
                "posts. Export <b>your own</b> cookies with a browser extension such as “Get cookies.txt LOCALLY”, then "
                "reply to the file with /cookies. They are stored only on this server (readable by the bot only); "
                "/cookies clear removes them. Never use someone else's account."
            ),
        )
        return
    data = src.read_bytes()
    shutil.rmtree(src.parent, ignore_errors=True)
    try:
        domains = parse_cookie_file(data.decode(errors="replace"))
    except ValueError as exc:
        await reply(update, f"⚠️ {esc(exc)}")
        return
    target.write_bytes(data)
    os.chmod(target, 0o600)
    try:
        await update.effective_message.reply_to_message.delete()  # don't leave the cookies in the chat history
    except Exception:  # noqa: BLE001
        pass
    await reply(
        update,
        f"🍪 Cookies saved for {len(domains)} domains: {esc(', '.join(domains[:15]))}"
        f"{'…' if len(domains) > 15 else ''}",
    )


# ------------------------------------------------------------------ live site check


@command(
    "sitecheck", "admin", "Test right now which major platforms this server can read", "/sitecheck [url …]", admin=True
)
async def sitecheck(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    urls = extract_urls(arg_text(context))
    targets = [(u, u) for u in urls] or default_targets()
    status = await reply(update, f"🩺 Checking {len(targets)} sites…")
    results = await check(s.downloader, targets)
    ok = sum(r.ok for r in results)
    lines = [f"🩺 <b>Site check</b> · {ok}/{len(results)} working"]
    for r in results:
        mark = "✅" if r.ok else "❌"
        lines.append(
            f"{mark} <b>{esc(truncate(r.name, 30))}</b> · {human_duration(r.seconds)} · {esc(truncate(r.detail, 80))}"
        )
    lines.append("\nFailures are often fixed by /updateytdlp (then /restart), or by /cookies for login-walled sites.")
    if status:
        await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ------------------------------------------------------------------ integrating new sites


@command(
    "pagedebug",
    "admin",
    "Show what the bot can find on a page (streams, extractor, adult label) + its HTML",
    "/pagedebug <url>",
    admin=True,
)
async def pagedebug(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    urls = extract_urls(arg_text(context))
    if not urls:
        await reply(update, usage("pagedebug"))
        return
    url = urls[0]
    lines = [f"🔬 <b>Page debug</b> · {esc(truncate(url, 80))}"]
    extractor = supported_extractor(url)
    lines.append(f"yt-dlp extractor: <b>{esc(extractor or 'none (generic)')}</b>")
    if reason := await s.policy.refusal(url):
        lines.append(f"Policy: {esc(reason)}")
    try:
        html, final_url = await s.images.fetch_page(url)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"❌ Fetch failed: {esc(str(exc)[:200])}")
        await reply(update, "\n".join(lines))
        return
    lines.append(f"Final URL: {esc(truncate(final_url, 100))} · {len(html):,} bytes of HTML")
    lines.append(f"Labels itself adult: {'yes' if html and page_is_adult(html) else 'no'}")
    ruled = apply_rules(html, final_url, rules_for(final_url, await s.jobs.site_rules.all())) if html else []
    generic = find_videos(html, final_url, embed_supported=lambda u: supported_extractor(u) is not None) if html else []
    if ruled:
        lines.append(f"\n<b>Site-rule matches ({len(ruled)})</b>")
        lines += [f"• {c.kind} {c.height or '?'}p · <code>{esc(truncate(c.url, 150))}</code>" for c in ruled[:10]]
    lines.append(f"\n<b>Generic fallback candidates ({len(generic)})</b>")
    lines += [
        f"• {c.kind} {c.height or '?'}p ({c.source}) · <code>{esc(truncate(c.url, 150))}</code>" for c in generic[:10]
    ] or ["none: the stream is probably built by scripts, or DRM-protected"]
    lines.append(
        "\nIf the stream isn't listed, look for it in the attached HTML and add a rule with "
        "<code>/siterule add DOMAIN REGEX</code>."
    )
    await reply(update, "\n".join(lines))
    if html:
        await update.effective_message.reply_document(
            InputFile(io.BytesIO(html.encode()), filename=f"{normalize_domain(final_url) or 'page'}.html"),
            caption="Page HTML as the server received it",
        )


@command(
    "siterule",
    "admin",
    "Custom stream-extraction rules for sites without built-in support",
    "/siterule list | add <domain> <regex> | remove <domain> [n] | test <url>",
    admin=True,
)
async def siterule(update: Update, context: Ctx, user: User) -> None:
    rules = svc(context).jobs.site_rules
    args = context.args or []
    action = args[0].lower() if args else "list"
    if action == "list":
        data = await rules.all()
        if not data:
            await reply(update, "No site rules yet.\n" + usage("siterule"))
            return
        lines = ["🧩 <b>Site rules</b>"]
        for domain, patterns in sorted(data.items()):
            lines += [f"<b>{esc(domain)}</b>"] + [f"  {i}. <code>{esc(p)}</code>" for i, p in enumerate(patterns, 1)]
        await reply(update, "\n".join(lines))
    elif action == "add" and len(args) >= 3:
        domain = normalize_domain(args[1])
        pattern = " ".join(args[2:])
        if not domain:
            await reply(update, "That isn't a valid domain.")
            return
        try:
            n = await rules.add(domain, pattern)
        except ValueError as exc:
            await reply(update, f"⚠️ {esc(exc)}")
            return
        await reply(
            update,
            f"🧩 Rule {n} added for <b>{esc(domain)}</b>. Check it with "
            f"<code>/siterule test https://{esc(domain)}/…</code>",
        )
    elif action == "remove" and len(args) >= 2:
        domain = normalize_domain(args[1]) or ""
        index = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        n = await rules.remove(domain, index)
        await reply(update, f"🗑 Removed {n} rule{'s' if n != 1 else ''}." if n else "No matching rule.")
    elif action == "test" and len(args) >= 2:
        url = args[1]
        s = svc(context)
        try:
            html, final_url = await s.images.fetch_page(url)
        except Exception as exc:  # noqa: BLE001
            await reply(update, f"❌ Fetch failed: {esc(str(exc)[:200])}")
            return
        found = await rules.candidates(html, final_url)
        if not found:
            await reply(update, "No rule matched on that page.")
            return
        await reply(
            update,
            f"✅ {len(found)} stream{'s' if len(found) != 1 else ''} matched:\n"
            + "\n".join(
                f"• {c.kind} {c.height or '?'}p · <code>{esc(truncate(c.url, 150))}</code>" for c in found[:10]
            ),
        )
    else:
        await reply(
            update,
            usage("siterule") + '\nExample: <code>/siterule add example.com "(https?://[^"]+\\.m3u8[^"]*)"</code>',
        )
