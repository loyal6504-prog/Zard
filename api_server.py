"""
api_server.py — WIZRAD Scan Live public data API.

Reads the SAME json files the bot already writes to (channels.json,
tracked_calls.json, sent_milestones.json, channel_points.json,
x_accounts.json, channel_meta.json) and serves them as clean JSON for
the website. Runs as its own aiohttp app, started from scanbot-release.py
next to session_web.py (see the snippet added to _startup_notify).

SECURITY NOTE: this file NEVER returns a raw Telegram file URL, because
that URL contains the bot token (https://api.telegram.org/file/bot<TOKEN>/...).
Avatars are downloaded once by channel_meta_job (in the bot process, which
already holds the token) and cached to disk under DATA_DIR/avatars/*.jpg.
This server only ever streams those already-downloaded local bytes.
"""

import os
import json
import time
from datetime import datetime
from aiohttp import web

DATA_DIR = os.environ.get("DATA_DIR", ".")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
API_PORT = int(os.environ.get("API_PORT", "8085"))

def _dp(name):
    return os.path.join(DATA_DIR, name)

CHANNELS_FILE       = _dp("channels.json")
TRACKED_FILE        = _dp("tracked_calls.json")
MILESTONES_FILE     = _dp("sent_milestones.json")
CHANNEL_POINTS_FILE = _dp("channel_points.json")
X_ACCOUNTS_FILE     = _dp("x_accounts.json")
CHANNEL_META_FILE   = _dp("channel_meta.json")   # written by channel_meta_job (see bot patch)
REMOVED_FILE        = _dp("removed_channels.json")

MIN_ALERT_X = float(os.environ.get("MIN_ALERT_X", "2"))  # matches the bot's own 2x alert threshold


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _channel_key(name):
    return str(name or "").lstrip("@").lower()


def _fmt_x(x):
    x = round(float(x), 2)
    return int(x) if x == int(x) else x


def _time_ago(iso_ts):
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        secs = max(0, (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds())
    except Exception:
        return ""
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _build_channel_scores():
    """For every tracked channel, compute best_x reached (max sent milestone)
    across all of that channel's tracked calls. Mirrors the bot's own
    leaderboard logic (best_x = highest milestone ever hit), simplified."""
    tracked    = _load(TRACKED_FILE, {})
    milestones = _load(MILESTONES_FILE, {})
    removed    = {_channel_key(c) for c in _load(REMOVED_FILE, [])}

    # channel -> {best_x, best_call, calls_count}
    scores = {}
    for call_key, call in tracked.items():
        ch = _channel_key(call.get("channel"))
        if not ch or ch in removed:
            continue
        levels = milestones.get(call_key, [])
        best_here = max(levels) if levels else 1
        entry = scores.setdefault(ch, {"best_x": 1, "best_call": None, "calls": 0})
        entry["calls"] += 1
        if best_here >= entry["best_x"]:
            entry["best_x"] = best_here
            entry["best_call"] = call
    return scores


def _channel_display_name(ch_key, meta):
    m = meta.get(ch_key, {})
    return m.get("title") or ch_key


def cors_middleware_factory():
    @web.middleware
    async def cors_mw(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    return cors_mw


async def leaderboard_handler(request):
    scores = _build_channel_scores()
    points = _load(CHANNEL_POINTS_FILE, {})
    x_accs = _load(X_ACCOUNTS_FILE, {})
    meta   = _load(CHANNEL_META_FILE, {})

    rows = []
    for ch, data in scores.items():
        if data["best_x"] < MIN_ALERT_X:
            continue  # bot only ever alerts at 2x+, keep the site consistent with that
        call = data["best_call"] or {}
        m = meta.get(ch, {})
        rows.append({
            "channel": _channel_display_name(ch, meta),
            "handle": f"@{ch}",
            "x": _fmt_x(data["best_x"]),
            "subscribers": m.get("subscribers"),          # null until channel_meta_job has run
            "avatar_url": f"/api/avatar/{ch}" if m.get("has_avatar") else None,
            "last_call_symbol": call.get("symbol") or "",
            "last_call_ca": (call.get("ca") or "")[:14],
            "x_handle": x_accs.get(ch, ""),
            "calls_tracked": data["calls"],
        })

    rows.sort(key=lambda r: r["x"], reverse=True)
    return web.json_response({"updated_at": datetime.utcnow().isoformat() + "Z", "channels": rows})


async def new_listings_handler(request):
    tracked    = _load(TRACKED_FILE, {})
    milestones = _load(MILESTONES_FILE, {})
    removed    = {_channel_key(c) for c in _load(REMOVED_FILE, [])}

    items = []
    for call_key, call in tracked.items():
        ch = _channel_key(call.get("channel"))
        if not ch or ch in removed:
            continue
        levels = milestones.get(call_key, [])
        best_here = max(levels) if levels else 1
        if best_here >= MIN_ALERT_X:
            continue  # already graduated to the leaderboard, not a "new" listing anymore
        items.append({
            "ticker": (call.get("symbol") or "").upper() or (call.get("ca", "")[:8] + "…"),
            "ca": (call.get("ca") or "")[:14],
            "chain": call.get("chain", ""),
            "channel": f"@{ch}",
            "time_ago": _time_ago(call.get("tracked_since", "")),
            "tracked_since": call.get("tracked_since", ""),
        })

    items.sort(key=lambda r: r["tracked_since"], reverse=True)
    return web.json_response({"updated_at": datetime.utcnow().isoformat() + "Z", "listings": items[:20]})


async def twitter_handler(request):
    scores = _build_channel_scores()
    x_accs = _load(X_ACCOUNTS_FILE, {})
    meta   = _load(CHANNEL_META_FILE, {})

    rows = []
    for ch, handle in x_accs.items():
        data = scores.get(ch, {"best_x": 1})
        if data["best_x"] < MIN_ALERT_X:
            continue
        m = meta.get(ch, {})
        rows.append({
            "handle": f"@{handle}",
            "linked_channel": f"@{ch}",
            "x": _fmt_x(data["best_x"]),
            "followers": m.get("x_followers"),   # null unless you also track X follower counts
        })

    rows.sort(key=lambda r: r["x"], reverse=True)
    return web.json_response({"updated_at": datetime.utcnow().isoformat() + "Z", "twitter": rows})


async def avatar_handler(request):
    ch = _channel_key(request.match_info.get("channel", ""))
    path = os.path.join(AVATAR_DIR, f"{ch}.jpg")
    if not ch or not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def health_handler(request):
    return web.json_response({"ok": True, "ts": time.time()})


def build_app():
    app = web.Application(middlewares=[cors_middleware_factory()])
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/leaderboard", leaderboard_handler)
    app.router.add_get("/api/new-listings", new_listings_handler)
    app.router.add_get("/api/twitter", twitter_handler)
    app.router.add_get("/api/avatar/{channel}", avatar_handler)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response())
    return app


async def start_api_server():
    """Called from scanbot-release.py's _startup_notify, same pattern as
    the existing session_web server on port 3002."""
    os.makedirs(AVATAR_DIR, exist_ok=True)
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    return runner


if __name__ == "__main__":
    # Allows running standalone for local testing: python api_server.py
    import asyncio

    async def _main():
        await start_api_server()
        print(f"WIZRAD API running on :{API_PORT}")
        while True:
            await asyncio.sleep(3600)

    asyncio.run(_main())
