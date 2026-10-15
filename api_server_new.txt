"""
api_server.py — WIZRAD Scan Live public data API.

Reads the SAME json files the bot already writes to (channels.json,
tracked_calls.json, sent_milestones.json, channel_points.json,
x_accounts.json, channel_meta.json) and serves them as clean JSON for
the website. Started from runtime.py (start_api_server).

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

CHANNELS_FILE = _dp("channels.json")
TRACKED_FILE = _dp("tracked_calls.json")
MILESTONES_FILE = _dp("sent_milestones.json")
CHANNEL_POINTS_FILE = _dp("channel_points.json")
X_ACCOUNTS_FILE = _dp("x_accounts.json")
CHANNEL_META_FILE = _dp("channel_meta.json")  # written by channel_meta_job (see bot patch)
REMOVED_FILE = _dp("removed_channels.json")
BOT_CONFIG_FILE = _dp("bot_config.json")

MIN_ALERT_X = float(os.environ.get("MIN_ALERT_X", "2"))  # matches the bot's own 2x alert threshold
POINTS_FOR_CHAMPION = int(os.environ.get("POINTS_FOR_CHAMPION", "100"))

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
    across all of that channel's tracked calls."""
    tracked = _load(TRACKED_FILE, {})
    milestones = _load(MILESTONES_FILE, {})
    removed = {_channel_key(c) for c in _load(REMOVED_FILE, [])}
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

# website photo/name lookup is case-sensitive -> map lowercase handle to the website's exact spelling
CANON_HANDLES = {
"ahri_calls": "Ahri_Calls",
"batmansafucalls": "BatmanSafuCalls",
"big_whale_call": "Big_Whale_call",
"bigbangcall": "BigBangCall",
"capricalls": "CapriCalls",
"casinoinsiderhere": "Casinoinsiderhere",
"ch1ro0": "Ch1ro0",
"cobragems": "Cobragems",
"cook_dev_il": "COOK_DEV_IL",
"crypto_millennial": "Crypto_millennial",
"cryptoengineercalls": "CRYPTOENGINEERCALLS",
"cryptogemmcalls": "Cryptogemmcalls",
"cryptosteelann": "CryptoSteelANN",
"cryptowolvescall": "Cryptowolvescall",
"dcacalls": "DCACalls",
"defiapegems": "DefiApeGems",
"defidoxxcalls": "Defidoxxcalls",
"doxxedguy": "DoxxedGuy",
"doxxedguygambles": "DoxxedGuyGambles",
"earlycallsalert": "EarlyCallsAlert",
"forgecapitallabs": "Forgecapitallabs",
"genesischainlog": "GenesisChainLog",
"globalexplorecalls": "GlobalExploreCalls",
"jemsdegens": "Jemsdegens",
"knight_call": "knight_call",
"krishnacall": "KrishnaCall",
"maybac_whales": "Maybac_whales",
"misselenacalls": "Misselenacalls",
"ninjagowhale": "Ninjagowhale",
"officialapescells": "Officialapescells",
"officialfuwhail": "OFFICIALFUWHAIL",
"pandacallslonge3": "Pandacallslonge3",
"phillipsgemscalls": "PHILLIPSGEMSCALLS",
"pumpwatch1": "PumpWatch1",
"pythoncall": "PythonCall",
"ramjofficialcalls": "RamJOfficiaLCALLS",
"rugspy": "RugSpy",
"safualpha": "SafuAlpha",
"sinatrending": "Sinatrending",
"spawnscalls": "spawnscalls",
"teslacallsofficial": "Teslacallsofficial",
"thesolitaireprestige": "TheSolitairePrestige",
"tomdegeninsights": "TomDegenInsights",
"twobrogems": "TwoBroGems",
"vortex_calls": "Vortex_Calls",
"vortex_gamble": "Vortex_Gamble",
"waltoncalls": "WaltonCalls",
"whalespumpbank": "WHALESPUMPBANK"
}

# ── Champion KOLs: mirrors the bot's champion_total_x() / build_champions_text() ──
#   * channel needs >= 100 points AND total X >= 2
#   * total X = SUM of each qualifying call's best X (2X + 2X = 4X); calls < 2X skipped
#   * frozen / deleted calls skipped; reset snapshot + excluded keys from bot_config.json honoured
#   * order: (total X desc, points desc), top 10
def _champion_rows():
    tracked = _load(TRACKED_FILE, {})
    milestones = _load(MILESTONES_FILE, {})
    points = _load(CHANNEL_POINTS_FILE, {})
    meta = _load(CHANNEL_META_FILE, {})
    removed = {_channel_key(c) for c in _load(REMOVED_FILE, [])}
    cfg = _load(BOT_CONFIG_FILE, {})
    snap = cfg.get("champion_milestone_snapshot", {})
    excl = set(cfg.get("champion_excluded_call_keys", []))

    per = {}
    for key, call in tracked.items():
        ch = _channel_key(call.get("channel"))
        if not ch or ch in removed:
            continue
        d = per.setdefault(ch, {"calls": 0, "wins": 0, "total": 0, "best": 0, "last": None,
                                "disp": str(call.get("channel") or ch).lstrip("@"), "hist": []})
        d["calls"] += 1
        if call.get("frozen") or call.get("post_deleted"):
            continue
        lv = milestones.get(key, [])
        try:
            hx = max(lv) if lv else float(call.get("last_ratio") or 1)
        except Exception:
            hx = 1
        d["hist"].append((str(call.get("tracked_since", "")), (call.get("symbol") or "").upper(), round(float(hx), 2)))
        if key in snap:
            lv = [m for m in lv if m > snap[key]]
        elif key in excl:
            continue
        if not lv:
            continue
        x = max(lv)
        if x < MIN_ALERT_X:
            continue
        d["wins"] += 1
        d["total"] += x
        d["best"] = max(d["best"], x)
        if not d["last"] or str(call.get("tracked_since", "")) > str(d["last"].get("tracked_since", "")):
            d["last"] = dict(call, _x=x)

    rows = []
    for ch, d in per.items():
        pts = (points.get(ch) or {}).get("points", 0)
        if pts < POINTS_FOR_CHAMPION or d["total"] < 2:
            continue
        lc = d["last"] or {}
        m = meta.get(ch, {})
        hist = [{"symbol": sy, "x": hx} for _, sy, hx in sorted(d["hist"])[-4:] if sy]
        try:
            t_ms = int(datetime.fromisoformat(str(lc.get("tracked_since", "")).replace("Z", "")).timestamp() * 1000)
        except Exception:
            t_ms = 0
        rows.append({
            "handle": CANON_HANDLES.get(ch, d["disp"]),  # original case: website photo/name lookup is case-sensitive
            "name": m.get("title") or ch,
            "calls": d["calls"],
            "winRate": round(100 * d["wins"] / d["calls"]) if d["calls"] else 0,
            "avgX": round(d["total"] / d["wins"], 2) if d["wins"] else 0,
            "bestX": d["best"],
            "points": pts,
            "history": hist,
            "score": d["total"] * 1000 + pts,  # website sorts by score desc = bot order
            "lastCall": {
                "symbol": (lc.get("symbol") or "").upper(),
                "chain": lc.get("chain", ""),
                "x": lc.get("_x", 0),
                "peakX": lc.get("_x", 0),
                "entryMc": lc.get("entry_mc", 0),
                "t": t_ms,
                "post": lc.get("msg_id") or "",
            } if lc else None,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:10]

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

async def champions_handler(request):
    return web.json_response({"updated_at": datetime.utcnow().isoformat() + "Z",
                              "champions": _champion_rows()})

async def leaderboard_handler(request):
    scores = _build_channel_scores()
    points = _load(CHANNEL_POINTS_FILE, {})
    x_accs = _load(X_ACCOUNTS_FILE, {})
    meta = _load(CHANNEL_META_FILE, {})
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
            "subscribers": m.get("subscribers"),  # null until channel_meta_job has run
            "avatar_url": f"/api/avatar/{ch}" if m.get("has_avatar") else None,
            "last_call_symbol": call.get("symbol") or "",
            "last_call_ca": (call.get("ca") or "")[:14],
            "x_handle": x_accs.get(ch, ""),
            "calls_tracked": data["calls"],
        })
    rows.sort(key=lambda r: r["x"], reverse=True)
    return web.json_response({"updated_at": datetime.utcnow().isoformat() + "Z", "channels": rows})

async def new_listings_handler(request):
    tracked = _load(TRACKED_FILE, {})
    milestones = _load(MILESTONES_FILE, {})
    removed = {_channel_key(c) for c in _load(REMOVED_FILE, [])}
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
    meta = _load(CHANNEL_META_FILE, {})
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
            "followers": m.get("x_followers"),  # null unless you also track X follower counts
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
    app.router.add_get("/api/champions", champions_handler)
    app.router.add_get("/api/leaderboard", leaderboard_handler)
    app.router.add_get("/api/new-listings", new_listings_handler)
    app.router.add_get("/api/twitter", twitter_handler)
    app.router.add_get("/api/avatar/{channel}", avatar_handler)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response())
    return app

async def start_api_server():
    """Called from runtime.py's post-ready startup block."""
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
