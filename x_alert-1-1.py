# ===== x_alert.py — X alerts (milestones), templates, send_alert, true-X, monitoring, Hot Streak =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).

# ─── Alert building ───────────────────────────────────────────────────────────
# 🔮 placeholders in order:
#   ≤99x  : chain, crystal, teer, kol, bot      (5 emojis)
#   100-999x: chain, crystal, teer, kol, bot    (5 emojis)
#   1000x+: chain, crystal(champ), crystal(entry), teer, kol, bot  (6 emojis)
DEFAULT_TEMPLATE = (
    "<b>🔮 @{channel} KOL Hit {x}X+</b>\n"
    "<b>🔮 {entry}    🔮    {current}</b>\n\n"
    "Ca: <code>{ca}</code>\n\n"
    '🔮<a href="{mae_link}">MAE</a>\n'
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="{bot_link}">BOT</a>'
)

# X-alert (buttons wali post) ka paragraph template.
# Champion / Leaderboard KOL badge isi paragraph ke aakhir me inline lagta hai.
DEFAULT_X_TEMPLATE = (
    "${symbol} {chain} play called at {entry} market cap. "
    "Current Market Cap stands at {current}. "
    "Clean execution. Tracking for the next move."
)

# X alerts ke tier-wise paragraphs — purane TIERED_TEMPLATES wali lines,
# sirf paragraph (header / MC line / buttons code se lagte hain).
# /editxtemplate se set kiya hua x_alert_template in sab par override karta hai.
X_TIER_PARAGRAPHS = [
    (2, 99,
        "${symbol} {chain} play called at {entry} market cap. "
        "Current Market Cap stands at {current}. "
        "Clean execution. Tracking for the next move."
    ),
    (100, 499,
        "@{channel} delivered {x}X. ${symbol} was called at a {entry}. "
        "Current MC stands at {current}. "
        "Massive move delivered. Eyes on the next milestone."
    ),
    (500, 999,
        "@{channel} printed {x}X. ${symbol} continues to exceed expectations, "
        "climbing from {entry} to {current}. "
        "Another milestone secured. Based KOL of Wizard Scan."
    ),
    (1000, 1999,
        "@{channel} nailed {x}X. ${symbol} was called at a {entry} and has "
        "now climbed to {current}. "
        "A truly elite performance with exceptional returns."
    ),
    (2000, 2999,
        "@{channel} crushed {x}X. ${symbol} was called at a {entry} and has "
        "now reached {current}. "
        "An extraordinary run that stands among the rarest performances."
    ),
    (3000, 4999,
        "@{channel} smashed {x}X. From {entry} to {current}, ${symbol} "
        "delivered a historic performance. "
        "One of the biggest moves ever tracked."
    ),
    (5000, 9999,
        "@{channel} hit {x}X. The results speak for themselves. ${symbol} "
        "climbed from {entry} to {current}, once again delivering "
        "exceptional returns. Another historic call added to the record."
    ),
    (10000, 999999999,
        "@{channel} printed {x}X. ${symbol} on {chain}, from {entry} to "
        "{current}. This is the rarest of rare calls. "
        "This is what legends are made of."
    ),
]

def _get_x_tier_paragraph(x_val):
    """X alert ka paragraph us X range ke hisab se (2-99X, 100-499X, ...)."""
    for lo, hi, p in X_TIER_PARAGRAPHS:
        if lo <= x_val <= hi:
            return p
    return DEFAULT_X_TEMPLATE

def _migrate_remove_x_from_templates():
    """Fix stored templates to remove X/Twitter.
    Strategy:
      - x_alert_template: if it has X lines OR is missing {mae_link} → delete it
        entirely so DEFAULT_X_TEMPLATE (MAE/KOL/BOT order) is used automatically.
      - alert_template: strip X lines in-place (owner may have other customisations).
      - milestone_templates: strip X lines in-place.
    """
    import re as _re
    _x_link_pat  = _re.compile(r"\n🔮[^\n]*(?:twitter|x)\.com[^\n]*", _re.IGNORECASE)
    _x_plain_pat = _re.compile(r"\n🔮\s*X(?:[^a-zA-Z]|$)", _re.IGNORECASE)
    cfg     = load_config()
    changed = False

    # ── x_alert_template: reset entirely if broken ──────────────────────────
    x_tmpl = cfg.get("x_alert_template", "")
    if x_tmpl:
        has_x_line = bool(_x_link_pat.search(x_tmpl) or _x_plain_pat.search(x_tmpl))
        # NOTE: mae_link ab X alert text me nahi hota (Trade button ban gaya),
        # is liye "missing mae_link" ab koi kharabi nahi hai.
        if has_x_line:
            cfg.pop("x_alert_template", None)
            changed = True
            logger.info("Migration: deleted x_alert_template (had X line or missing MAE) "
                        "→ will use DEFAULT_X_TEMPLATE (MAE/KOL/BOT)")

    # ── alert_template: strip X lines only (keep other customisations) ──────
    a_tmpl = cfg.get("alert_template", "")
    if a_tmpl:
        cleaned = _x_link_pat.sub("", a_tmpl)
        cleaned = _x_plain_pat.sub("", cleaned)
        if cleaned != a_tmpl:
            cfg["alert_template"] = cleaned
            changed = True

    # ── milestone_templates: strip X lines ──────────────────────────────────
    for ms_key, tmpl in cfg.get("milestone_templates", {}).items():
        if not tmpl:
            continue
        cleaned = _x_link_pat.sub("", tmpl)
        cleaned = _x_plain_pat.sub("", cleaned)
        if cleaned != tmpl:
            cfg["milestone_templates"][ms_key] = cleaned
            changed = True

    if changed:
        save_config(cfg)
        logger.info("Migration: X/Twitter cleanup done")

async def _update_prices_for_chain_tokens(chain_tokens: dict) -> dict:
    """Fetch fresh MC prices for cached tokens without replacing the token list.
    Extracts CA from dex_url and queries DexScreener. Only mc_fmt is updated."""
    result = {}
    for chain_key, tokens in chain_tokens.items():
        if not tokens:
            result[chain_key] = tokens
            continue
        updated = [dict(t) for t in tokens]
        ca_index: dict = {}
        for i, t in enumerate(updated):
            dex_url = t.get("dex_url", "")
            ca = dex_url.rstrip("/").split("/")[-1] if dex_url else ""
            if ca and len(ca) >= 20:
                ca_index[ca] = i
        if not ca_index:
            result[chain_key] = updated
            continue
        try:
            cas = list(ca_index.keys())[:30]
            r = await asyncio.to_thread(
                requests.get,
                f"https://api.dexscreener.com/latest/dex/tokens/{','.join(cas)}",
                timeout=15,
                headers=HEADERS
            )
            if r.status_code == 200:
                pairs_data = r.json().get("pairs") or []
                best_mc: dict = {}
                for pair in pairs_data:
                    ca_key = pair.get("baseToken", {}).get("address", "")
                    mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
                    if ca_key and mc > 0 and (ca_key not in best_mc or mc > best_mc[ca_key]):
                        best_mc[ca_key] = mc
                for ca, idx in ca_index.items():
                    if ca in best_mc:
                        updated[idx]["mc_fmt"] = fmt_mc(best_mc[ca])
        except Exception as _pe:
            logger.warning(f"Price update {chain_key}: {_pe}")
        result[chain_key] = updated
    return result

# ─── Tiered templates (100X–9999X) ────────────────────────────────────────────
TIERED_TEMPLATES = [
    (2, 99,
        "<b>🔮 @{channel} KOL Hit {x}X+</b>\n\n"
        "${symbol} {chain} play called at {entry} market cap. "
        "Current Market Cap stands at {current}. "
        "Clean execution. Tracking for the next move.\n\n"
        "<b>🔮 {entry}    🔮    {current}</b>\n\n"
        "Ca: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (100, 499,
        "<b>🔮 SOLID KOL @{channel}</b>\n\n"
        "@{channel} delivered {x}X. ${symbol} was called at a {entry}. Current MC stands at {current}. "
        "Massive move delivered. Eyes on the next milestone.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (500, 999,
        "<b>🔮 Apex KOL @{channel}</b>\n\n"
        "@{channel} printed {x}X. ${symbol} continues to exceed expectations, climbing from {entry} to {current}. "
        "Another milestone secured. Based KOL of Wizard Scan.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (1000, 1999,
        "<b>🔮 ELITE KOL @{channel}</b>\n\n"
        "@{channel} nailed {x}X. ${symbol} was called at a {entry} and has now climbed to {current}. "
        "A truly elite performance with exceptional returns.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (2000, 2999,
        "<b>🔮 RARE KOL @{channel}</b>\n\n"
        "@{channel} crushed {x}X. ${symbol} was called at a {entry} and has now reached {current}. "
        "An extraordinary run that stands among the rarest performances.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (3000, 4999,
        "<b>🔮 EPIC KOL @{channel}</b>\n\n"
        "@{channel} smashed {x}X. From {entry} to {current}, ${symbol} delivered a historic performance. "
        "One of the biggest moves ever tracked.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (5000, 9999,
        "<b>🔮 LEGENDARY KOL @{channel}</b>\n\n"
        "@{channel} hit {x}X. The results speak for themselves. ${symbol} climbed from {entry} to {current}, "
        "once again delivering exceptional returns. Another historic call added to the record.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
]

CHAMPION_TEMPLATE = (
    "<b>🔮 HALL OF FAME @{channel}</b>\n\n"
    "@{channel} printed {x}X. ${symbol} on {chain}, from {entry} to {current}. "
    "This is the rarest of rare calls.\n\n"
    "@{channel} didn't just call it. They owned it. A masterclass in execution.\n\n"
    "This is what legends are made of. Welcome to the Hall of Fame.\n\n"
    "<b>🔮 {entry}  🔮  {current}</b>\n\n"
    "CA: <code>{ca}</code>\n\n"
    '🔮<a href="{mae_link}">MAE</a>\n'
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="{bot_link}">BOT</a>'
)

def _get_alert_pack():
    """Return current EMOJI_PACK.
    If the owner has locked a specific pack (via /setemojipack), always use that —
    this avoids the pack silently jumping around whenever channel_post_count
    changes (e.g. after a config reset). Otherwise falls back to auto-rotation
    every 10 posts based on post count."""
    config = load_config()
    locked = config.get("locked_emoji_pack")
    if locked:
        for pack in EMOJI_PACKS:
            if pack["name"] == locked:
                return pack
    count = config.get("channel_post_count", 0)
    return EMOJI_PACKS[(count // 10) % len(EMOJI_PACKS)]

def _pack_by_name(name):
    for pack in EMOJI_PACKS:
        if pack["name"] == name:
            return pack
    if name == X76_PACK["name"]:
        return X76_PACK
    return None

def _pack_teer(pack):
    """Pack ki apni teer emoji, warna locked default."""
    try:
        return int((pack or {}).get("teer") or TEER_EMOJI_ID)
    except Exception:
        return TEER_EMOJI_ID

def _pack_crystal(pack):
    """Pack ki apni crystal-ball (entry MC) emoji, warna locked default."""
    try:
        return int((pack or {}).get("crystal") or MC_ENTRY_EMOJI_ID)
    except Exception:
        return MC_ENTRY_EMOJI_ID

def _custom_x_pack(x_val):
    """Owner ki khud set ki hui x-range pack (config["x_emoji_packs"]).
    If more than one saved range covers x_val (e.g. an old wide 11-99 range
    plus a newer, more specific 51-75), the NARROWEST one wins — otherwise
    whichever range was saved first always shadows a more specific one added
    later, which is why a newly-confirmed-saved pack could still never
    actually show up on real alerts."""
    try:
        best = None
        for r in load_config().get("x_emoji_packs", []):
            low, high = int(r["low"]), int(r["high"])
            if low <= int(x_val) <= high:
                if best is None or (high - low) < (int(best["high"]) - int(best["low"])):
                    best = r
        if best:
            pack = dict(best.get("pack") or {})
            if pack:
                return pack
    except Exception as _e:
        logger.warning(f"custom x emoji pack read failed: {_e}")
    return None

def _get_alert_pack_for_x(x_val):
    """X value ke hisaab se emoji pack.
    Priority: owner ki custom range -> built-in X_PACK_RULES (2-10 red,
    11+ blue) -> /setemojipack lock -> pehla pack."""
    try:
        xv = int(x_val)
    except Exception:
        return _get_alert_pack()
    custom = _custom_x_pack(xv)
    if custom:
        return custom
    for lo, hi, name in X_PACK_RULES:
        if lo <= xv <= hi:
            pack = _pack_by_name(name)
            if pack:
                return pack
    return _get_alert_pack()

def _get_alert_emoji_ids(x_val, chain, pack=None):
    """Build ordered emoji_ids list for _build_premium_entities injection.
    Order matches 🔮 placeholder positions in each template tier:
      ≤99x   : [crystal, teer, kol, bot]
      100-999x: [chain, crystal, teer, kol, bot]
      1000x+  : [chain, crystal(champ), crystal(entry), teer, kol, bot]
    Extra post links (if any) append their emoji IDs at the end.
    RH chain emoji is read from config (set via /setrobinemoji).
    """
    if pack is None:
        pack = _get_alert_pack_for_x(x_val)
    chain_key = (chain or "").upper()

    # ── Robinhood chain: config override first, then hardcoded pack emoji ─
    if chain_key == "RH":
        rh_emojis   = load_config().get("rh_chain_emojis", {})
        rh_emoji_id = rh_emojis.get(pack["name"], 0)
        # Priority: /setrobinemoji override → hardcoded pack["chain"]["RH"] → BASE fallback
        chain_emoji = (rh_emoji_id
                       or pack["chain"].get("RH")
                       or pack["chain"].get("BASE")
                       or pack["chain"].get("SOL"))
    elif chain_key == "EVM":
        # FIX: EVM (unknown EVM chain) → ETH emoji, not SOL
        chain_emoji = pack["chain"].get("ETH") or pack["chain"].get("SOL")
    else:
        chain_emoji = pack["chain"].get(chain_key) or pack["chain"].get("ETH") or pack["chain"].get("SOL")

    crystal    = _pack_crystal(pack)   # pack ki apni crystal ball (blue/red/custom)
    teer       = _pack_teer(pack)       # pack ki apni teer
    # NOTE: kol and bot_em are intentionally excluded — _build_premium_entities
    # special-cases 🔮KOL / 🔮BOT and injects pack.kol / pack.bot directly.
    # Including them here would shift badge emoji IDs into the wrong positions.
    #
    # ALL tiers use exactly 3 regular 🔮 placeholders:
    #   🔮 (header) → chain_emoji
    #   🔮 (entry MC) → crystal
    #   🔮 (separator between MCs) → teer  ← always TEER_EMOJI_ID (5346105514575025401)
    #                                         even if template is edited by owner
    # Previously 1000x+ had [chain, crystal, crystal, teer] (4 items) which caused
    # the badge 🔮 to consume teer instead of lb_star — breaking LEADERBOARD KOL label.
    base = [chain_emoji, crystal, teer]
    # NOTE: the promo-link 🔮 is NOT listed here. It is matched by text
    # (promo text follows it) inside _build_premium_entities and always gets
    # PROMO_EMOJI_ID, so positional drift can never give it the wrong emoji.

    # Append emoji IDs for extra post links (owner-configured via /addpostlink)
    extra_links = load_config().get("extra_post_links", [])
    for lnk in extra_links:
        eid = lnk.get("emoji_id")
        if eid:
            base.append(int(eid))
    return base

def _xem_build_pack(low, high, vals):
    """Owner ki di hui IDs se ek mukammal emoji pack banao.
    Jo ID owner ne /skip ki, wo blue pack se le li jaati hai."""
    blue  = _pack_by_name("blue") or EMOJI_PACKS[0]
    chain = dict(blue.get("chain", {}))
    for ck in ("BNB", "ETH", "SOL", "TON", "BASE", "RH", "ARC"):
        v = vals.get(f"chain.{ck}")
        if v:
            chain[ck] = int(v)
    chain["BSC"] = chain.get("BNB", chain.get("BSC"))
    def pick(key, fallback_key=None):
        return int(vals.get(key) or blue.get(fallback_key or key) or 0)
    caller  = pick("caller",  "caller")
    trade   = pick("trade",   "trade")
    details = pick("details", "details")
    return {
        "name":    f"x{low}_{high}",
        "crystal": pick("crystal"),
        "teer":    pick("teer"),
        "kol":     caller,
        "mae":     trade,
        "bot":     details,
        "caller":  caller,
        "trade":   trade,
        "details": details,
        "twitter": pick("twitter"),
        "dex":     pick("dex"),
        "dev":     pick("dev"),
        "chain":   chain,
    }

def _xem_pack_summary(pack):
    """Pack ki IDs ka chhota HTML summary."""
    ch = pack.get("chain", {})
    rows = [
        ("BNB", ch.get("BNB")), ("ETH", ch.get("ETH")), ("SOL", ch.get("SOL")),
        ("TON", ch.get("TON")), ("BASE", ch.get("BASE")), ("RH", ch.get("RH")),
        ("ARC", ch.get("ARC")),
        ("Crystal", pack.get("crystal")), ("Teer", pack.get("teer")),
        ("Caller", pack.get("caller")), ("Twitter", pack.get("twitter")),
        ("Trade", pack.get("trade")), ("DEX", pack.get("dex")),
        ("Details", pack.get("details")), ("DEV", pack.get("dev")),
    ]
    return "\n".join(f"• {n}: <code>{v}</code>" for n, v in rows if v)

def _get_tiered_template(x_val):
    """Return tiered template string for x_val range, or None."""
    if x_val >= 10000:
        return CHAMPION_TEMPLATE
    for lo, hi, tmpl in TIERED_TEMPLATES:
        if lo <= x_val <= hi:
            return tmpl
    return None

def _get_range_template(x_val, config=None):
    """Return owner-defined custom range template for x_val, or None.
    Ranges are set via /setrangetemplate LOW HIGH and stored in config['range_templates']
    as a list of {"low":..,"high":..,"template":..}. Checked before the hardcoded
    TIERED_TEMPLATES so the owner's own per-range text always wins.
    If more than one saved range covers x_val (e.g. an old 2-9999 catch-all
    plus a newer, more specific 100-499), the NARROWEST one wins — otherwise
    whichever range happened to be saved first would always shadow a more
    specific one added later, showing its emojis/text instead."""
    config = config or load_config()
    best = None
    for r in config.get("range_templates", []):
        try:
            low, high = int(r["low"]), int(r["high"])
            if low <= x_val <= high:
                if best is None or (high - low) < (int(best["high"]) - int(best["low"])):
                    best = r
        except (KeyError, ValueError, TypeError):
            continue
    return best["template"] if best else None

def _get_kol_language(channel, config=None):
    """Language tag set for this KOL/channel via /setkollang, or 'english' (default).
    Purely a label the owner uses to organise which KOLs have custom-language
    templates set up — untouched channels fall through to the normal English
    default/tiered templates exactly like before this feature existed."""
    config = config or load_config()
    ch = (channel or "").lstrip("@").strip().lower()
    return config.get("channel_language", {}).get(ch, "english")

def _get_kol_range_template(channel, x_val, config=None):
    """Return this KOL-group's own custom range template for x_val, or None.
    Set via /setkoltemplate CHANNEL LOW HIGH. If CHANNEL is language-tagged
    (see /setkollang), the template is saved/read from the SHARED per-language
    store (config['lang_range_templates'][lang]) so setting it once applies to
    every KOL tagged with that language — not just this one channel. Untagged
    (English) channels keep the old per-channel store
    (config['channel_range_templates'][channel]) for backward compatibility.
    Checked BEFORE the global /setrangetemplate so a KOL-specific template
    always wins over the global one."""
    config = config or load_config()
    ch = (channel or "").lstrip("@").strip().lower()
    lang = (config.get("channel_language", {}) or {}).get(ch)
    if lang and lang != "english":
        source = config.get("lang_range_templates", {}).get(lang, [])
    else:
        source = config.get("channel_range_templates", {}).get(ch, [])
    best = None
    for r in source:
        try:
            low, high = int(r["low"]), int(r["high"])
            if low <= x_val <= high:
                if best is None or (high - low) < (int(best["high"]) - int(best["low"])):
                    best = r
        except (KeyError, ValueError, TypeError):
            continue
    return best["template"] if best else None

def _is_real_template(t) -> bool:
    """A saved template is only usable when it actually looks like a template.

    BUG FIX (900X): /setmedia 900 ke saath agar caption me sirf "900" ya koi
    chhota word bheja gaya to woh milestone_templates["900"] me save ho jata
    tha aur alert ka pura text sirf "900" ban jata tha. Ab aise degenerate
    entries ignore hoti hain aur bot range/tier template use karta hai."""
    if not isinstance(t, str):
        return False
    t = t.strip()
    if not t:
        return False
    if "{" in t and "}" in t:      # has real placeholders → genuine template
        return True
    if t.replace("X", "").replace("x", "").replace(",", "").strip().isdigit():
        return False               # just a number like "900" / "900X"
    return len(t) >= 40            # plain text must at least be a real sentence

_X_LINK_LINE_RE = re.compile(r"\n?\s*🔮\s*<a href=\"\{(?:mae|kol|bot)_link\}\">[^<]*</a>")

def _clean_x_template(tmpl):
    """Owner ke purane x_alert_template me se MAE/KOL/BOT link lines hata do —
    ab wo 6 inline buttons ban chuke hain (Trade / Caller / Details)."""
    if not tmpl:
        return None
    cleaned = _X_LINK_LINE_RE.sub("", tmpl).strip()
    return cleaned or None

# ─── 🏆 Champion KOL X-alert paragraph (2X–99X only) ──────────────────────────
# Sirf Champion KOL ki X alert (2X se 99X) is paragraph se banti hai. {rank} =
# KOL ki Champion rank (badge["rank"]) lafzon me: 1 = One, 2 = Two ...
# Baaki sab KOLs / baaki X ranges purane templates hi use karte hain.
CHAMP_X_RANK_WORDS = {
    "english":    ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"],
    "chinese":    ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"],
    "vietnamese": ["một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười"],
    "turkish":    ["birinci", "ikinci", "üçüncü", "dördüncü", "beşinci",
                   "altıncı", "yedinci", "sekizinci", "dokuzuncu", "onuncu"],
    "russian":    ["первое", "второе", "третье", "четвёртое", "пятое",
                   "шестое", "седьмое", "восьмое", "девятое", "десятое"],
}
CHAMP_X_PARAGRAPHS = {
    "english": (
        "${symbol} {chain_l} called at {entry}. Now at {current}. "
        "Clean Execution. Tracking For The Next Move."
    ),
    "chinese": (
        "${symbol} {chain} 在 {entry} 市值时喊单。现价市值 {current}。"
        "操作漂亮！该 KOL 在冠军 KOL 中排名第{rank}。"
    ),
    "vietnamese": (
        "${symbol} {chain} Được gọi ở vốn hóa {entry}. Hiện tại {current}. "
        "Thực hiện xuất sắc! KOL này xếp hạng số {rank} trong các KOL Champion."
    ),
    "turkish": (
        "${symbol} {chain} {entry} piyasa değerinde çağrıldı. Şu an {current}. "
        "Mükemmel uygulama! Bu KOL, Şampiyon KOL'lar arasında {rank} sırada."
    ),
    "russian": (
        "${symbol} {chain} Вызов сделан на капитализации {entry}. Сейчас {current}. "
        "Чистое исполнение! Этот KOL занимает {rank} место среди KOL-чемпионов."
    ),
}

def _champion_x_paragraph(channel, badge, x_val, config=None):
    """Paragraph for a Champion KOL's X alert (2X–99X), else None."""
    try:
        if not badge or badge.get("type") != "champions":
            return None
        if not (2 <= int(x_val) <= 99):
            return None
        lang = _get_kol_language(channel, config)
        if lang not in CHAMP_X_PARAGRAPHS:
            lang = "english"
        words = CHAMP_X_RANK_WORDS[lang]
        try:
            r = int(badge.get("rank") or 0)
        except (TypeError, ValueError):
            r = 0
        rank_txt = words[r - 1] if 1 <= r <= len(words) else (str(r) if r else "")
        return CHAMP_X_PARAGRAPHS[lang].replace("{rank}", rank_txt)
    except Exception:
        return None

def build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol, badge=None, x_style=False):
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    # Priority: 1) specific milestone template  2) KOL-specific range template
    #           3) owner-defined global range template  4) global custom (owner set via /settemplate)
    #           5) hardcoded tiered range fallback (incl. champion at 10000X)  6) default
    # NOTE: global custom (alert_template) intentionally ranks ABOVE tiered so that
    # /settemplate always overrides the built-in 100x-9999x tiered texts.
    _ms_tmpl = config.get("milestone_templates", {}).get(str(x_val))
    if not _is_real_template(_ms_tmpl):
        if _ms_tmpl:
            logger.warning(f"Ignoring broken {x_val}X template ({_ms_tmpl!r}) — using range/tier template")
        _ms_tmpl = None

    # Badge resolved up-front — used both as the {badge} template placeholder
    # (full custom templates) and for the legacy auto-wrap path below.
    if badge is None:
        badge = _get_kol_badge(channel)
    badge_html = ""
    _champ_badge_line = ""
    if badge:
        if badge["type"] == "leaderboard":
            badge_html = f' <b><a href="https://t.me/WizardScan/136">LEAD KOL</a></b> - Rank # 🔮'
        else:
            # Champion KOL: X alert me badge ab post ke END me alag line par jata hai
            # (links ke neeche 1 khali line, phir "🔮 CHAMP KOL - Rank # 🔮").
            _cw = globals().get("CHAMPIONS_WEB_LINK") or "https://wizardscanlive.com/#champions"
            if x_style:
                _champ_badge_line = f'🔮 <b><a href="{_cw}">CHAMP KOL</a></b> - Rank # 🔮'
            else:
                badge_html = f' <b><a href="{_cw}">CHAMP KOL</a></b> - Rank # 🔮'

    _is_full = False
    if x_style:
        # X alert (buttons wali post). Owner ka template ab DO tarah ka ho sakta hai:
        #   a) FULL template — agar usme {caller_link}/{twitter_link}/{trade_link}/
        #      {dex_link}/{details_link}/{dev_link} me se koi placeholder ho, to
        #      poora post (header/badge/entry-current/links samet) wahi template
        #      decide karta hai — kuch bhi auto-add/wrap nahi hota.
        #   b) sirf paragraph (purana/legacy style) — is case me header, entry/current
        #      line, aur 6-link block neeche automatically wrap ho jate hain.
        # Priority: milestone template > KOL-specific range (/setkoltemplate) >
        #           /setrangetemplate (global per-range) > /editxtemplate (global) >
        #           hardcoded tier paragraph.
        _champ_para = _champion_x_paragraph(channel, badge, x_val, config)
        template = (_champ_para
                    or _clean_x_template(_ms_tmpl)
                    or _clean_x_template(_get_kol_range_template(channel, x_val, config))
                    or _clean_x_template(_get_range_template(x_val, config))
                    or _clean_x_template(config.get("x_alert_template")))
        if _champ_para:
            _is_full = False        # champion template = paragraph only, wrapper adds the rest
        elif template:
            _is_full = any(ph in template for ph in
                           ("{caller_link}", "{twitter_link}", "{trade_link}",
                            "{dex_link}", "{details_link}", "{dev_link}"))
        else:
            template = _get_x_tier_paragraph(x_val)
    else:
        template = (_ms_tmpl
                    or _get_kol_range_template(channel, x_val, config)
                    or _get_range_template(x_val, config)
                    or config.get("alert_template")
                    or _get_tiered_template(x_val)
                    or DEFAULT_TEMPLATE)
    mae_link     = f"{MAESTRO_REFLINK_BASE}{ca}{MAESTRO_REF_SUFFIX}" if ca else f"{MAESTRO_REFLINK_BASE}r-wizard_scan"
    caller_link  = _x_alert_caller_link(channel, msg_id) or f"https://t.me/{channel}"
    twitter_link = _x_alert_twitter_link(channel)
    trade_link   = _x_alert_trade_link(ca)
    dex_link     = _x_alert_dex_link(ca, chain) or "https://dexscreener.com"
    details_link = f"{BOT_LINK}?start=details_{_register_xalert_details(channel, ca, x_val)}" if ca else f"{BOT_LINK}?start=details"
    dev_link     = WIZARD_DEV_LINK
    kwargs = dict(channel=_display_handle(channel), x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=_mc_no_label(entry_fmt), current=_mc_no_label(current_fmt), ca=ca,
                  kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK,
                  mae_link=mae_link, badge=badge_html,
                  caller_link=caller_link, twitter_link=twitter_link, trade_link=trade_link,
                  dex_link=dex_link, details_link=details_link, dev_link=dev_link)
    kwargs["chain_l"] = str(chain or "").lower()
    _hdr_ch = channel
    if x_style and badge and badge.get("type") != "leaderboard":
        _cap = lambda h: (h[:1].upper() + h[1:]) if h else h
        kwargs["channel"] = _cap(str(kwargs["channel"]))
        _hdr_ch = _cap(str(channel))
    try:
        text = safe_format(template, **kwargs)
    except Exception:
        text = safe_format(DEFAULT_X_TEMPLATE if x_style else DEFAULT_TEMPLATE, **kwargs)

    if x_style:
        if _is_full:
            # Owner's own full template already has header/badge/entry-current/links —
            # use exactly as typed, nothing auto-added or re-appended.
            pass
        else:
            # Legacy paragraph-only template → auto-wrap with the fixed structure
            #   1) header:  🔮 @channel KOL Hit xX+
            #   2) paragraph + badge inline
            #   3) 🔮 entry    🔮    current
            #   4) 🔮Caller 🔮Twitter 🔮Trade / 🔮DEX 🔮Details 🔮DEV
            links_block = (
                f'🔮<a href="{caller_link}">Caller</a>    '
                f'🔮<a href="{twitter_link}">Twitter</a>  '
                f'🔮<a href="{trade_link}">Trade</a>\n'
                f'🔮<a href="{dex_link}">DEX</a>      '
                f'🔮<a href="{details_link}">Details</a>   '
                f'🔮<a href="{dev_link}">DEV</a>'
            )
            text = (f"<b>🔮 @{_hdr_ch} KOL Hit {x_val}X+</b>\n\n"
                    f"{text}{badge_html}\n\n"
                    f"<b>🔮{'  ' if _champ_para else ' '}{_mc_no_label(entry_fmt)}    🔮    {_mc_no_label(current_fmt)}</b>\n\n"
                    f"{links_block}")
        # 🏆 Champion KOL badge — post ke aakhir me (social links ke neeche, 2 khali lines chhor kar)
        if _champ_badge_line and "CHAMP KOL" not in text:
            text += "\n\n\n" + _champ_badge_line
    elif badge_html:
        text += badge_html

    # Append owner-configured extra post links
    extra_links = config.get("extra_post_links", [])
    for lnk in extra_links:
        lnk_text = html.escape(lnk.get("text", ""))
        lnk_url  = lnk.get("url", "")
        if lnk_text and lnk_url:
            text += f'\n🔮<a href="{lnk_url}">{lnk_text}</a>'

    # ── 48-hour promo link — ALWAYS the very last line ───────────────────────
    # Champion KOL / Leaderboard KOL badge aur extra links ke BAAD, ek khali
    # line chhod kar, taake post saaf aur piyari lage.
    if 2 <= x_val <= 50:
        promo = config.get("promo_link")
        if promo and promo.get("url") and promo.get("text") and promo.get("set_at"):
            try:
                if datetime.utcnow() - datetime.fromisoformat(promo["set_at"]) < timedelta(hours=48):
                    text += f'\n\n🔮 <a href="{promo["url"]}">{html.escape(promo["text"])}</a>'
            except Exception:
                pass

    return text

def build_x_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol):
    """Build alert text for the X/Twitter channel (@WizardscanX)."""
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    template = config.get("x_alert_template") or DEFAULT_X_TEMPLATE
    mae_link = f"{MAESTRO_REFLINK_BASE}{ca}{MAESTRO_REF_SUFFIX}" if ca else f"{MAESTRO_REFLINK_BASE}r-wizard_scan"
    kwargs = dict(channel=_display_handle(channel), x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=_mc_no_label(entry_fmt), current=_mc_no_label(current_fmt), ca=ca,
                  kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK,
                  mae_link=mae_link)
    try:
        return safe_format(template, **kwargs)
    except Exception:
        return safe_format(DEFAULT_X_TEMPLATE, **kwargs)

def _x_alert_caller_link(channel, msg_id):
    """Us post ka direct link jo track ki gayi thi."""
    ch = (channel or "").lstrip("@").strip()
    if not ch:
        return None
    if msg_id:
        return f"https://t.me/{ch}/{msg_id}"
    return f"https://t.me/{ch}"

def _x_alert_twitter_link(channel):
    """KOL ka X link (agar /addx se set hai), warna WizardScan ka X."""
    try:
        handle = _get_channel_x_handle(channel)
    except Exception:
        handle = None
    if handle:
        return f"https://x.com/{handle.lstrip('@')}"
    return WIZARD_X_FALLBACK_LINK

def _x_alert_trade_link(ca):
    if ca:
        return f"{MAESTRO_REFLINK_BASE}{ca}{MAESTRO_REF_SUFFIX}"
    return MAESTRO_PLAIN_REFLINK

def _x_alert_dex_link(ca, chain):
    if not ca:
        return None
    path = CHAIN_TO_DEXPATH.get((chain or "").upper(), "solana")
    return f"https://dexscreener.com/{path}/{ca}"

def build_x_alert_keyboard(channel, msg_id, ca, chain, x_val=None):
    """Caller/Twitter/Trade/DEX/Details/DEV ab inline KEYBOARD buttons nahi hain —
    ye 6 links ab build_alert() ke andar hi post TEXT me 🔮 emoji ke saath render
    hote hain (Telegram button labels premium emoji support nahi karte, is liye
    text hi sahi jagah hai). Ye function ab sirf owner ke /addbutton wale
    milestone-specific extra buttons (agar hon) return karta hai."""
    rows = []
    if x_val is not None:
        extra = load_config().get("milestone_buttons", {}).get(str(x_val), [])
        for b in extra:
            if b.get("text") and b.get("url"):
                rows.append([InlineKeyboardButton(b["text"], url=b["url"])])
    return InlineKeyboardMarkup(rows) if rows else None

_WIZRAD_INTEL_EMOJI = {
    "title":       6044119257308995249,   # 📔 <caller> — X.Xx SIGNAL
    "caller":      5816483001323757774,   # 🔮 Caller:
    "subscribers": 5846214053436859823,   # 👥 Subscribers:
    "view":        5846232354292507296,   # 📈 View Original Call:
    "token":       6044189703362584375,   # 🏆 Token:
    "peak":        5845744321453631630,   # 🚀 Peak Reached:
    "entry":       5846193940105012285,   # 🔎 Call Entry:
    "sim100":      5845931908445250313,   # 🏆 $100 SIMULATION
    "simval":      5809928529042743187,   # 🔮 Current: / 🔮 Peak: (same id, both rows)
    "top":         5471908280655686858,   # 👨‍👩‍👦 TOP Callers on this Token
    "footer":      6043914791095902818,   # 🐳 Wizard Scan — Tracking Winners
}

def _wtge(key, glyph):
    """One tg-emoji tag for the WIZRAD INTELLIGENCE panel."""
    return f'<tg-emoji emoji-id="{_WIZRAD_INTEL_EMOJI[key]}">{glyph}</tg-emoji>'

def _wizrad_global_stats():
    """Bot-wide signal stats, computed LIVE from whatever is currently in
    tracked_calls — never hardcoded, so it reflects the real current state
    (and naturally starts back at zero after /resettracking, same as
    everything else that reads from tracked_calls)."""
    total = len(tracked_calls)
    ratios, entries = [], []
    for c in tracked_calls.values():
        r = call_peak_ratio(c)
        if r > 0:
            ratios.append(r)
        e = float(c.get("entry_mc", 0) or 0)
        if e > 0:
            entries.append(e)
    avg_upside = (sum(ratios) / len(ratios)) if ratios else 0.0
    avg_entry  = (sum(entries) / len(entries)) if entries else 0.0
    return total, avg_upside, avg_entry

def _wizrad_callers_for_ca(ca: str):
    """Every tracked call for this exact token (any channel), best X first."""
    out = []
    ca_l = (ca or "").lower()
    if not ca_l:
        return out
    for c in tracked_calls.values():
        if (c.get("ca") or "").lower() == ca_l:
            out.append((c.get("channel", "?"), call_peak_ratio(c)))
    out.sort(key=lambda t: t[1], reverse=True)
    return out

def _dollar_short(value):
    """$42.3K / $1.24M style — used in MARKET DATA rows."""
    value = float(value or 0)
    if value <= 0: return "N/A"
    if value >= 1_000_000_000: return f"${value/1_000_000_000:.2f}B"
    if value >= 1_000_000:     return f"${value/1_000_000:.2f}M"
    if value >= 1_000:         return f"${value/1_000:.1f}K"
    return f"${value:.0f}"

def _lower_short(value):
    """100k / 1.2m style (no $ sign) — used in the 'entry to peak' line."""
    value = float(value or 0)
    if value <= 0: return "0"
    if value >= 1_000_000_000: return f"{value/1_000_000_000:.1f}b"
    if value >= 1_000_000:     return f"{value/1_000_000:.1f}m"
    if value >= 1_000:         return f"{value/1_000:.0f}k"
    return f"{value:.0f}"

async def get_channel_subscribers(bot, channel: str) -> int:
    """Live subscriber count for a channel. Uses the channel_meta.json cache
    (refreshed every ~10 min by channel_meta_job) first — falls back to a
    direct Telegram lookup only if nothing cached yet, so this stays fast
    and doesn't add extra rate-limited calls on every Details tap."""
    key = str(channel or "").lstrip("@").lower()
    if not key:
        return 0
    try:
        meta = load_json(CHANNEL_META_FILE, {})
        v = meta.get(key, {}).get("subscribers")
        if v:
            return int(v)
    except Exception:
        pass
    try:
        return await bot.get_chat_member_count(f"@{key}")
    except Exception:
        return 0

def build_wizrad_intelligence_text(call: dict, x_val=None, subscribers: int = 0) -> str:
    """The signal-details breakdown shown when someone taps 'View Details' on
    an X alert. Every number here is computed live from the actual
    tracked-call data — nothing is hardcoded. Sent as a VIDEO caption
    (VID_X_ALERT_DETAILS), so this must stay under Telegram's 1024-char
    caption limit — kept short and 'Top Callers' capped at 8 rows."""
    channel  = call.get("channel", "?")
    ca       = call.get("ca", "")
    chain    = (call.get("chain") or "SOL").upper()
    symbol   = (call.get("symbol") or "TOKEN").upper()
    msg_id   = call.get("msg_id")

    entry_mc   = float(call.get("entry_mc", 0) or 0)
    peak_ratio = call_peak_ratio(call)
    peak_mc    = float(call.get("peak_mc", 0) or 0) or (entry_mc * peak_ratio if entry_mc > 0 else 0.0)
    last_ratio = float(call.get("last_ratio", 0) or 0)

    caller_post = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    x_shown     = x_val if x_val is not None else (round(peak_ratio, 1) if peak_ratio > 0 else 0)

    callers   = _wizrad_callers_for_ca(ca)
    top8      = callers[:8]
    top_lines = "\n".join(f"• @{ch} — {r:.1f}x" for ch, r in top8) if top8 else "• No other callers yet"

    peak_sim = 100.0 * peak_ratio if peak_ratio > 0 else 100.0
    cur_sim  = 100.0 * last_ratio if last_ratio > 0 else peak_sim

    return (
        f'{_wtge("title","📔")} <b>@{html.escape(channel)} — {x_shown:g}x SIGNAL</b>\n\n\n'
        f'{_wtge("caller","🔮")} Caller: @{html.escape(channel)}\n'
        f'{_wtge("subscribers","👥")} Subscribers: {subscribers:,}\n'
        f'{_wtge("view","📈")} View Original Call: <a href="{caller_post}">Link</a>\n\n\n'
        f'{_wtge("token","🏆")} Token: ${html.escape(symbol)} — {html.escape(chain)}\n'
        f'{_wtge("peak","🚀")} Peak Reached: ${_lower_short(peak_mc)}\n'
        f'{_wtge("entry","🔎")} Call Entry: ${_lower_short(entry_mc)}\n\n\n'
        f'{_wtge("sim100","🏆")} $100 SIMULATION\n'
        f'{_wtge("simval","🔮")} Current: ${cur_sim:,.0f}\n'
        f'{_wtge("simval","🔮")} Peak: ${peak_sim:,.0f}\n\n\n'
        f'{_wtge("top","👨‍👩‍👦")} TOP Callers on this Token\n\n'
        f'{top_lines}\n\n\n'
        f'{_wtge("footer","🐳")} <b>Wizard Scan — Tracking Winners</b>'
    )

async def cb_x_alert_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Details button filhal khali hai — sirf ek chhota notice."""
    try:
        await update.callback_query.answer("📄 Details soon.", show_alert=False)
    except Exception:
        pass

def build_milestone_keyboard(x_val):
    buttons = load_config().get("milestone_buttons",{}).get(str(x_val),[])
    if not buttons: return None
    rows = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in buttons if b.get("text") and b.get("url")]
    return InlineKeyboardMarkup(rows) if rows else None


# ═══════════════════════════════════════════════════════════════════════════
# REAL-TIME ALERT DISPATCHER
# One global FIFO queue for BOTH "Dropped a Call" posts and milestone (X)
# alerts. Every event is queued the exact moment it is detected and posted in
# that same order — so X alerts never wait behind a batch of dropped-call
# posts, and dropped calls never wait behind a batch of X alerts.
# ═══════════════════════════════════════════════════════════════════════════
_RT_QUEUE     = None
_RT_WORKER    = None
_RT_INFLIGHT  = set()
_RT_SEQ       = 0
RT_POST_GAP   = float(os.environ.get("RT_POST_GAP", "0.12") or 0.12)   # seconds between posts (Telegram flood safety)
# Multiple RT workers run in parallel (RT_WORKER_COUNT), so a small per-worker
# gap does NOT cap the combined send rate to the channel — 3 workers x a 0.05s
# gap can burst ~60 sends/sec into one channel, which Telegram silently stalls
# (this showed up as "rt_worker milestone send timeout (20s)" once a large
# backlog started flushing all at once). Give milestones their own,
# env-tunable gap instead of a hardcoded 0.05s.
RT_MILESTONE_GAP = float(os.environ.get("RT_MILESTONE_GAP", "0.3") or 0.3)
RT_PRIO_X     = 0      # X / milestone alerts — HIGHEST priority, kabhi wait nahi
RT_PRIO_DROP  = 5      # "Dropped a Call" posts

def _rt_queue():
    global _RT_QUEUE
    if _RT_QUEUE is None:
        # PriorityQueue: X alerts hamesha dropped-call posts se aage jump karti hain.
        _RT_QUEUE = asyncio.PriorityQueue()
    return _RT_QUEUE

async def _rt_put(priority, kind, data):
    global _RT_SEQ
    _RT_SEQ += 1
    await _rt_queue().put((priority, _RT_SEQ, kind, data))

# Above this many seconds sitting in the real-time queue, a milestone is
# treated as stale rather than late: by the time it would post, the token has
# very likely already rugged/moved on, so posting it would show a fake-looking
# "just hit 8X" for a token that crashed 2-3 days ago. This only fires if a
# single item wedges the serial worker (see the wait_for below) long enough
# for a real backlog to form — normal ticks clear in well under a second.
RT_MAX_QUEUE_AGE_SECONDS = int(os.environ.get("RT_MAX_QUEUE_AGE_SECONDS", "600") or 600)
# A single send hanging (stuck userbot call, dead Telegram connection, etc.)
# must not wedge every other queued alert behind it — including special/
# priority-KOL milestones, which flow through this exact same serial worker.
RT_SEND_TIMEOUT_SECONDS = int(os.environ.get("RT_SEND_TIMEOUT_SECONDS", "20") or 20)
# A disconnected Telethon client can remain non-None while its send coroutine
# hangs.  Give the userbot only a short window, then continue to the Bot API
# fallback instead of letting the whole milestone hit RT_SEND_TIMEOUT_SECONDS.
USERBOT_SEND_TIMEOUT_SECONDS = float(os.environ.get("USERBOT_SEND_TIMEOUT_SECONDS", "6") or 6)
# Media (video/photo) upload ke liye alag, lamba timeout. Ye sirf UPPER LIMIT hai:
# upload jaise hi complete hota hai wait foran khatam ho jata hai (wait_for
# early return karta hai), is liye jaldi hone par koi delay nahi aata.
# Fresh upload (cache miss / stale file reference) 10s se zyada le sakta hai.
USERBOT_MEDIA_TIMEOUT_SECONDS = float(os.environ.get("USERBOT_MEDIA_TIMEOUT_SECONDS", "30") or 30)
# Outer rt_worker timeout is limit se chhota nahi hona chahiye warna poora
# send beech mein cancel ho jata hai.
RT_SEND_TIMEOUT_SECONDS = max(RT_SEND_TIMEOUT_SECONDS, int(USERBOT_MEDIA_TIMEOUT_SECONDS) + 20)
# Agar phir bhi non-premium post chali jaye to userbot usay edit karke premium
# emojis lagata hai (max itni koshishein), phir BOT owner ko forward karta hai.
EMOJI_FIX_ATTEMPTS = max(1, int(os.environ.get("EMOJI_FIX_ATTEMPTS", "3") or 3))
# Ek slow send poori queue ko block na kare: kai workers parallel chalte hain,
# priority queue ki wajah se X alerts phir bhi dropped-call posts se aage hain.
RT_WORKER_COUNT = max(1, int(os.environ.get("RT_WORKER_COUNT", "3") or 3))
RT_MAX_SEND_RETRIES = max(0, int(os.environ.get("RT_MAX_SEND_RETRIES", "2") or 2))

# GLOBAL PACING: RT_WORKER_COUNT parallel workers can each finish their own
# item within milliseconds of each other (e.g. 3 different KOL channels all
# calling the same token, or several tokens catching up right after posting
# permission is restored). Each was a genuinely distinct, correct alert —
# never a duplicate — but 3 posts landing in the channel <200ms apart reads
# as a spam burst to anyone watching it live. This lock serializes the
# ACTUAL Telegram send across every worker so posts always trickle out at
# least RT_GLOBAL_MIN_GAP seconds apart, no matter how many genuinely fire
# at once — nothing is dropped or delayed for long, it just never machine-
# guns the channel.
RT_GLOBAL_MIN_GAP    = float(os.environ.get("RT_GLOBAL_MIN_GAP", "3.0") or 3.0)
_RT_GLOBAL_POST_LOCK = asyncio.Lock()
_RT_LAST_POST_TS     = [0.0]

# X alerts get their OWN (much smaller) global gap. Before this, every X alert
# had to wait RT_GLOBAL_MIN_GAP (3s) after ANY previous post, so when several
# KOLs hit the same token together (or several milestones queued at once) the
# last alert in the burst went out 6-9s late. "Dropped a Call" posts keep the
# original RT_GLOBAL_MIN_GAP, so their behaviour is unchanged.
RT_MILESTONE_GLOBAL_GAP = float(os.environ.get("RT_MILESTONE_GLOBAL_GAP", "0.5") or 0.5)

async def _rt_global_pace(gap=None):
    _gap = RT_GLOBAL_MIN_GAP if gap is None else gap
    async with _RT_GLOBAL_POST_LOCK:
        wait = _gap - (time.time() - _RT_LAST_POST_TS[0])
        if wait > 0:
            await asyncio.sleep(wait)
        _RT_LAST_POST_TS[0] = time.time()

RT_ORDER_WAIT_SECONDS = float(os.environ.get("RT_ORDER_WAIT_SECONDS", "8") or 8)   # was 30s: a retrying 2X delayed 5X by up to 30s

def _rt_lower_inflight(call_key, ms):
    """True if a LOWER milestone of the same call is still queued/sending/retrying."""
    prefix = f"{call_key}|"
    try:
        cur = float(ms)
    except (TypeError, ValueError):
        return False
    for tag in list(_RT_INFLIGHT):
        if isinstance(tag, str) and tag.startswith(prefix):
            try:
                if float(tag[len(prefix):]) < cur:
                    return True
            except (TypeError, ValueError):
                continue
    return False

async def _rt_worker_loop(bot):
    q = _rt_queue()
    while True:
        _prio, _seq, kind, data = await q.get()
        posted = None
        requeued = False
        try:
            age = time.time() - data.get("queued_at", time.time())
            if age > RT_MAX_QUEUE_AGE_SECONDS:
                logger.error(
                    f"⏳ Dropping stale {kind} alert (queued {age:.0f}s ago, "
                    f"limit {RT_MAX_QUEUE_AGE_SECONDS}s): "
                    f"{data.get('symbol','?')} @{data.get('channel','?')} — "
                    f"token has likely already moved on/rugged since this was detected."
                )
                continue
            if kind == "milestone":
                call_key = data["call_key"]
                ms       = data["ms"]
                if not data.get("record_only", False):
                    # ORDER FIX (2026-10-05): parallel RT workers could post a
                    # high X (e.g. 70X) before the lower ones (2X..50X) of the
                    # SAME call when a lower one was slow/retried. Wait here
                    # until every lower milestone of this call has finished
                    # (sent or given up). Capped so a stuck item can't wedge us.
                    _ow = 0.0
                    while _rt_lower_inflight(call_key, ms) and _ow < RT_ORDER_WAIT_SECONDS:
                        await asyncio.sleep(0.25)
                        _ow += 0.25
                    await _rt_global_pace(RT_MILESTONE_GLOBAL_GAP)
                posted = await asyncio.wait_for(send_alert(
                    bot, data["channel"], data["msg_id"], ms, data["chain"],
                    data["entry_fmt"], data["cur_fmt"], data["ca"], data["symbol"],
                    force=data.get("force", False),
                    record_only=data.get("record_only", False)), timeout=RT_SEND_TIMEOUT_SECONDS)
                if posted:
                    sent_milestones[call_key].add(ms)
                    _save_milestones()
                    logger.info(f"🚀 {data.get('symbol','?')} @{data['channel']} {ms}X! (real-time)")
                    # Refresh rankings immediately after every verified 2x+ post.
                    # Leaderboard eligibility never waits for points or the 90s job.
                    if ms >= 2:
                        asyncio.create_task(_update_leaderboard_with_premium_emojis(bot))
                        asyncio.create_task(_update_champions_with_premium_emojis(bot))
                        asyncio.create_task(_hs_on_call_hit(bot, data["channel"], call_key, ms))
                else:
                    # send_alert() returned False — all 4 send paths (bot API
                    # media, userbot media, bot media fallback, userbot/bot
                    # text) failed WITHOUT raising an exception (e.g. Telegram
                    # flood-wait on the channel, userbot disconnected + bot not
                    # admin). This used to be a silent PERMANENT drop even
                    # though the old log line claimed "will retry" — nothing
                    # ever actually re-queued it. Now bounded-retry it exactly
                    # like the TimeoutError path below.
                    tries = int(data.get("_tries", 0)) + 1
                    if tries <= RT_MAX_SEND_RETRIES:
                        data["_tries"] = tries
                        data["queued_at"] = time.time()
                        logger.warning(
                            f"⚠️ send_alert() returned False — re-queued "
                            f"(try {tries}/{RT_MAX_SEND_RETRIES}): "
                            f"{ms}X {call_key}")
                        asyncio.create_task(_rt_put(_prio, kind, data))
                        requeued = True
                    else:
                        logger.error(
                            f"❌ send_alert() failed x{tries} — giving up on this "
                            f"milestone: {ms}X {call_key}. Likely cause: bot not "
                            f"admin in {TARGET_CHANNEL}, userbot disconnected, or "
                            f"Telegram flood-wait on the channel.")
                        if OWNER_ID:
                            try:
                                asyncio.create_task(bot.send_message(
                                    OWNER_ID,
                                    f"⚠️ <b>Milestone alert permanently failed</b> after "
                                    f"{tries} tries: <b>{ms}X</b> for {data.get('symbol','?')} "
                                    f"@{data.get('channel','?')}.\n"
                                    f"Check: bot admin rights in {TARGET_CHANNEL}, userbot "
                                    f"session (/userbotlogin), or Telegram flood-wait.",
                                    parse_mode="HTML"))
                            except Exception:
                                pass
            elif kind == "dropped":
                await _rt_global_pace()
                await asyncio.wait_for(send_dropped_alert(
                    bot, data["channel"], data["msg_id"], data["ca"],
                    data.get("chain", "SOL"), data["entry_fmt"], data["symbol"]),
                    timeout=RT_SEND_TIMEOUT_SECONDS)
                logger.info(f"📣 Dropped call posted (real-time): {data.get('symbol','?')} @{data['channel']}")
        except asyncio.TimeoutError:
            # Pehle yahan item chhod diya jata tha — isi wajah se kuch channels ki
            # X alert kabhi post nahi hoti thi. Ab item wapas queue me jata hai
            # (bounded retries), aur queue behind it phir bhi block nahi hoti.
            tries = int(data.get("_tries", 0)) + 1
            if tries <= RT_MAX_SEND_RETRIES:
                data["_tries"] = tries
                data["queued_at"] = time.time()
                logger.warning(
                    f"⏱️ rt_worker {kind} send timeout ({RT_SEND_TIMEOUT_SECONDS}s) — "
                    f"re-queued (try {tries}/{RT_MAX_SEND_RETRIES}): "
                    f"{data.get('symbol','?')} @{data.get('channel','?')}")
                asyncio.create_task(_rt_put(_prio, kind, data))
                requeued = True
            else:
                logger.error(
                    f"⏱️ rt_worker {kind} send hard-timeout ({RT_SEND_TIMEOUT_SECONDS}s) x{tries} — "
                    f"giving up on this item: {data.get('symbol','?')} @{data.get('channel','?')}")
        except Exception as e:
            logger.error(f"rt_worker {kind} failed: {e}")
        finally:
            if not requeued:
                if kind == "milestone":
                    _RT_INFLIGHT.discard(f"{data.get('call_key')}|{data.get('ms')}")
                else:
                    _RT_INFLIGHT.discard(f"drop|{data.get('channel')}|{data.get('ca')}")
            q.task_done()
        # Only pause when something was actually sent to Telegram.
        # X alerts ke baad extra gap nahi — agla alert foran jaye.
        if posted != "silent":
            await asyncio.sleep(RT_MILESTONE_GAP if kind == "milestone" else RT_POST_GAP)

_RT_WORKERS = []

def _rt_ensure_worker(bot):
    """Ek se zyada worker chalao taake ek slow/hanging send baqi X alerts ko
    rok na sake (yahi wajah thi ke kuch channels ki alert miss hoti thi)."""
    global _RT_WORKER, _RT_WORKERS
    _RT_WORKERS = [t for t in _RT_WORKERS if not t.done()]
    while len(_RT_WORKERS) < RT_WORKER_COUNT:
        t = asyncio.create_task(_rt_worker_loop(bot),
                                name=f"wizard-realtime-alerts-{len(_RT_WORKERS)+1}")
        _RT_WORKERS.append(t)
    _RT_WORKER = _RT_WORKERS[0]

#  2X and 3X must NEVER sit silent waiting on media — owner wants these
#  posted the instant they happen, media or no media (text-only fallback
#  is fine). See rt_enqueue_milestone below.
#  Owner requirement: har X alert (2X, 3X, 5X, 10X...) INSTANT jaye — media ho
#  ya na ho, kabhi hold na ho. Isliye ye set khali chhorne ke bajaye "sab" hai.
INSTANT_ALERT_MILESTONES = "ALL"

def _is_instant_milestone(ms) -> bool:
    if INSTANT_ALERT_MILESTONES == "ALL":
        return True
    try:
        return ms in INSTANT_ALERT_MILESTONES
    except Exception:
        return False

# Small delay between milestones from the SAME price update (same token
# instantly crossing multiple X levels, e.g. jumps straight to 20X and 19X+
# fire in the same tick). Without this, each pending milestone was enqueued
# at delay=0 and picked up by whichever of the parallel rt-workers happened
# to be free — a later/higher milestone (20X) could finish sending before an
# earlier/lower one (19X) if the 19X worker hit a retry, making the channel
# look like it posted 20X then 19X. A 2s gap between each milestone's own
# dispatch keeps them in the correct ascending order (owner requirement).
MS_STAGGER_SECONDS = float(os.environ.get("MS_STAGGER_SECONDS", "2.0") or 2.0)

# Early entry re-base guard (see monitoring tick). Set EARLY_REBASE_ENABLED=0 to disable.
EARLY_REBASE_ENABLED    = str(os.environ.get("EARLY_REBASE_ENABLED", "1")).strip().lower() in ("1", "true", "yes")
EARLY_REBASE_CHAINS     = {c.strip().upper() for c in os.environ.get("EARLY_REBASE_CHAINS", "RH").split(",") if c.strip()}
EARLY_REBASE_MIN_RATIO  = float(os.environ.get("EARLY_REBASE_MIN_RATIO", "2") or 2)
EARLY_REBASE_WINDOW_MIN = float(os.environ.get("EARLY_REBASE_WINDOW_MIN", "45") or 45)

async def _purge_points_for_milestones(call, xs):
    """Early-rebase purge: reverse the champion points that fake (now-purged)
    milestones had awarded for this call. Recomputes the call's tier total so
    the incremental awards net out to exactly what was actually given."""
    try:
        channel = (call.get("channel") or "").lower()
        call_key = f"{channel}_{call.get('ca','')}"
        async with _points_lock:
            pts = load_channel_points()
            entry = pts.get(channel)
            if not entry:
                return
            awarded = entry.get("awarded_tiers", {})
            lst = list(awarded.get(call_key, []))
            _before = sum(tp for tt, tp in POINT_TIERS if tt in lst)
            _gone = {get_point_tier_reward(int(x))[0] for x in xs}
            lst = [t for t in lst if t not in _gone]
            _after = sum(tp for tt, tp in POINT_TIERS if tt in lst)
            awarded[call_key] = lst
            entry["awarded_tiers"] = awarded
            _delta = _before - _after
            if _delta > 0:
                entry["points"] = max(0, entry.get("points", 0) - _delta)
            pts[channel] = entry
            save_channel_points(pts)
            if _delta > 0:
                logger.info(f"🔙 Reversed {_delta} pts @{channel} for purged fake milestones {sorted(xs)}")
    except Exception as e:
        logger.warning(f"purge points failed: {e}")

async def rt_enqueue_milestone(bot, call_key, call, ms, cur_fmt, record_only=False, delay=0.0):
    """Queue an X alert the instant it is detected (optionally after `delay` s)."""
    tag = f"{call_key}|{ms}"
    if tag in _RT_INFLIGHT:
        return
    # Reserve immediately so the next monitoring tick never double-queues a
    # milestone that is still waiting for its staggered slot.
    _RT_INFLIGHT.add(tag)
    _record_milestone_time(call_key, ms)
    _rt_ensure_worker(bot)
    if delay and delay > 0:
        asyncio.create_task(
            _rt_delayed_milestone(bot, call_key, call, ms, cur_fmt, record_only, tag, delay))
        return
    await _rt_dispatch_milestone(bot, call_key, call, ms, cur_fmt, record_only, tag)


async def _rt_delayed_milestone(bot, call_key, call, ms, cur_fmt, record_only, tag, delay):
    try:
        await asyncio.sleep(delay)
        await _rt_dispatch_milestone(bot, call_key, call, ms, cur_fmt, record_only, tag)
    except Exception as e:
        _RT_INFLIGHT.discard(tag)
        logger.error(f"delayed milestone {tag} failed: {e}")


async def _rt_dispatch_milestone(bot, call_key, call, ms, cur_fmt, record_only, tag):
    try:
        asyncio.create_task(_ex_premium_dm(bot, call_key, call, ms, cur_fmt, record_only))
    except Exception:
        pass
    force_instant = _is_instant_milestone(ms) and not record_only
    payload = {
        "call_key":  call_key,
        "ms":        ms,
        "channel":   call.get("channel", ""),
        "msg_id":    call.get("msg_id", 0),
        "chain":     call.get("chain", "SOL"),
        "entry_fmt": call.get("entry_fmt", "N/A"),
        "cur_fmt":   cur_fmt,
        "ca":        call.get("ca", ""),
        "symbol":    call.get("symbol", ""),
        "force":     force_instant,
        "record_only": record_only,
        "queued_at": time.time(),
    }
    # Milestones with NO media never hit Telegram — they only record X-Ray +
    # points. Running them inline keeps the queue free so real (media) X alerts
    # post instantly instead of waiting behind silent ones.
    if record_only or (not force_instant and not milestone_has_media(ms)):
        try:
            res = await send_alert(
                bot, payload["channel"], payload["msg_id"], ms, payload["chain"],
                payload["entry_fmt"], payload["cur_fmt"], payload["ca"], payload["symbol"],
                record_only=record_only)
            if res:
                sent_milestones[call_key].add(ms)
                _save_milestones()
                if ms >= 2:
                    asyncio.create_task(_update_leaderboard_with_premium_emojis(bot))
                    asyncio.create_task(_update_champions_with_premium_emojis(bot))
                    asyncio.create_task(_hs_on_call_hit(bot, payload["channel"], call_key, ms))
        except Exception as e:
            logger.error(f"silent milestone record failed: {e}")
        finally:
            _RT_INFLIGHT.discard(tag)
        return
    await _rt_put(RT_PRIO_X, "milestone", payload)


# Bot @WizardScan ka admin nahi hai to send_photo/send_video har dafa 403 deta
# hai. Ye flag wo nakaam raasta ek dafa ke baad band kar deta hai.
_BOT_CHANNEL_FORBIDDEN = {"v": False}

def _ptb_keyboard_to_tl(keyboard):
    """PTB InlineKeyboardMarkup -> Telethon buttons (url buttons only)."""
    if not keyboard:
        return None
    try:
        from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonUrl
        rows = []
        for row in keyboard.inline_keyboard:
            btns = [KeyboardButtonUrl(text=b.text, url=b.url) for b in row if b.url]
            if btns:
                rows.append(KeyboardButtonRow(buttons=btns))
        return ReplyInlineMarkup(rows=rows) if rows else None
    except Exception:
        return None


async def _fix_alert_emojis_then_forward(bot, channel, x_val, post_id, text, emoji_id,
                                         pack, keyboard):
    """Post Bot API se (bina premium emoji) gayi thi -> userbot usay EDIT karke
    premium emojis lagata hai. Edit ho jane ke BAAD bot (userbot nahi) post ko
    channel owner / subscribers ko forward karta hai. Edit fail ho to bhi forward
    hota hai taake koi alert miss na ho."""
    fixed = False
    try:
        from telethon.extensions.html import parse as tl_html_parse
        _txt, _explicit = prepare_owner_emojis(text)
        plain_text, base_ents = tl_html_parse(_txt)
        all_ents = _build_premium_entities(plain_text, base_ents, emoji_id,
                                           forced_pack=pack, explicit=_explicit)
        tl_buttons = _ptb_keyboard_to_tl(keyboard)

        # Kuch send paths mein bot ka message_id 0 report hota hai (logs: msg_id=0)
        # -> channel ki recent posts mein se wahi post dhoondo (text match).
        if not post_id and userbot_client:
            try:
                recent = await asyncio.wait_for(
                    userbot_client.get_messages(TARGET_CHANNEL, limit=10), timeout=15)
                for m in recent or []:
                    if (getattr(m, "message", "") or "").strip() == plain_text.strip():
                        post_id = m.id
                        logger.info(f"🔎 Emoji-fix: bot post id resolved via userbot: {post_id}")
                        break
            except Exception as e_find:
                logger.warning(f"Emoji-fix: post id lookup failed: {e_find}")

        if post_id and userbot_client:
            for attempt in range(1, EMOJI_FIX_ATTEMPTS + 1):
                try:
                    await asyncio.wait_for(
                        _locked_userbot_edit(
                            TARGET_CHANNEL, int(post_id), plain_text,
                            formatting_entities=all_ents,
                            buttons=tl_buttons, link_preview=False),
                        timeout=20)
                    fixed = True
                    logger.info(f"✅ Emoji-fix: premium emojis added by userbot edit "
                                f"(post {post_id}, {x_val}x @{channel}, attempt {attempt})")
                    break
                except Exception as e_edit:
                    # "message not modified" = emojis pehle se lage hain
                    if "not modified" in str(e_edit).lower():
                        fixed = True
                        break
                    logger.warning(f"Emoji-fix edit attempt {attempt}/{EMOJI_FIX_ATTEMPTS} "
                                   f"failed (post {post_id}): {type(e_edit).__name__}: {e_edit}")
                    await asyncio.sleep(2 * attempt)
        elif not post_id:
            logger.warning(f"Emoji-fix skipped: post id unknown ({x_val}x @{channel})")
    except Exception as e:
        logger.error(f"Emoji-fix failed unexpectedly: {e}")

    if not fixed:
        logger.error(f"❌ Emoji-fix could not add premium emojis to post {post_id} "
                     f"({x_val}x @{channel}) — forwarding as-is")
    # Edit ke baad BOT forward karta hai (forward_message, userbot kabhi nahi).
    if post_id:
        await _forward_alert_recipients(bot, channel, x_val, post_id)
    else:
        logger.error(f"Forward skipped: post id unknown ({x_val}x @{channel})")


# ─── 🎬 CHAMPION KOL HD VIDEO (champion_video.py) ─────────────────────────────
# Sirf Champion KOL (badge type "champions") ki X alert par. Lead KOL aur baaqi
# KOLs bilkul pehle jaisi. Video fail / slow (12s timeout) ho to (False, None, False)
# wapas aata hai aur send_alert NORMAL alert bhejta hai.
def _champion_video_stats(channel, badge):
    calls = get_call_history(channel)
    total = len(calls)
    alpha = sum(
        1 for c in calls
        if (c.get("current_x", c.get("highest_x", 0)) or 0) >= 2
        or (c.get("highest_x", 0) or 0) >= 2
    )
    win = round((alpha / total) * 100) if total else 0
    return {
        "rank":     badge.get("rank", 1),
        "points":   get_channel_points(channel),
        "calls":    total,
        "win_rate": f"{win}%",
    }

async def _champion_video_try_send(bot, channel, x_val, chain, entry_fmt, current_fmt,
                                   ca, symbol, badge, text, keyboard, emoji_id, pack):
    """Returns (posted, post_id, needs_emoji_fix). Never raises."""
    path = None
    try:
        import champion_video
        stats = _champion_video_stats(channel, badge)
        _av = os.path.join(_dp("avatars"), f"{str(channel).lstrip('@').lower()}.jpg")
        avatar = _av if os.path.exists(_av) else None
        path = await champion_video.champion_alert_video(
            channel=channel, x_val=x_val, chain=chain, entry_fmt=entry_fmt,
            cur_fmt=current_fmt, ca=ca, symbol=symbol, stats=stats,
            avatar_path=avatar)
    except Exception as e:
        logger.warning(f"🎬 Champion video build error @{channel} {x_val}x: {e}")
        path = None
    if not path or not os.path.exists(path):
        logger.warning(f"🎬 Champion video unavailable @{channel} {x_val}x — normal alert will be sent")
        return False, None, False

    # 1) Userbot: video + premium emojis + buttons ek hi send me (no edit/pencil)
    if userbot_client and emoji_id:
        try:
            from telethon.extensions.html import parse as tl_html_parse
            from telethon.tl.types import DocumentAttributeVideo
            _txt, _explicit = prepare_owner_emojis(text)
            plain_text, base_ents = tl_html_parse(_txt)
            if len(plain_text) <= 1024:
                all_ents = _build_premium_entities(plain_text, base_ents, emoji_id,
                                                   forced_pack=pack, explicit=_explicit)
                sent = await asyncio.wait_for(
                    userbot_client.send_file(
                        TARGET_CHANNEL, path, caption=plain_text,
                        formatting_entities=all_ents,
                        buttons=_ptb_keyboard_to_tl(keyboard),
                        attributes=[DocumentAttributeVideo(
                            duration=7, w=1920, h=820, supports_streaming=True)],
                        supports_streaming=True, force_document=False),
                    timeout=USERBOT_MEDIA_TIMEOUT_SECONDS)
                logger.info(f"🎬 Champion video sent by userbot: @{channel} {x_val}x "
                            f"rank={badge.get('rank')} msg_id={sent.id}")
                return True, sent.id, False
        except Exception as e:
            logger.warning(f"🎬 Champion video userbot send failed @{channel}: "
                           f"{type(e).__name__}: {e} — trying Bot API")

    # 2) Bot API fallback (premium emoji baad me userbot edit se lagte hain)
    if not _BOT_CHANNEL_FORBIDDEN["v"]:
        try:
            with open(path, "rb") as fh:
                m = await bot.send_video(
                    TARGET_CHANNEL, video=fh, caption=text, parse_mode="HTML",
                    reply_markup=keyboard, width=1920, height=820, duration=7,
                    supports_streaming=True)
            logger.info(f"🎬 Champion video sent by bot: @{channel} {x_val}x "
                        f"rank={badge.get('rank')} msg_id={m.message_id}")
            return True, m.message_id, True
        except Exception as e:
            if "not a member" in str(e).lower() or "forbidden" in str(e).lower():
                _BOT_CHANNEL_FORBIDDEN["v"] = True
            logger.warning(f"🎬 Champion video bot send failed @{channel}: {e}")
    return False, None, False


async def send_alert(bot, channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol,
                     force=False, record_only=False):
    config   = load_config()
    media    = config.get("milestone_media",{}).get(str(x_val)) or config.get("milestone_media",{}).get("global")
    # Deleted-post call (owner has not approved): never post an X alert, and
    # never feed points / Champions / Leaderboard. Still recorded in history.
    _blocked_deleted = bool(ca) and _is_deleted_blocked(tracked_calls.get(f"{channel}_{ca}"))
    if _blocked_deleted:
        record_only = True
    # ── REAL-TIME POST RULE ───────────────────────────────────────────────────
    # X alerts must NEVER be held just because media is missing. If media is
    # configured, use it; otherwise send the same alert as text immediately.
    # This is important on Railway/restarts where a missing media file must not
    # turn a verified X into a delayed alert. record_only remains silent by design.
    if record_only:
        _call_key = f"{channel}_{ca}" if ca else None
        try:
            record_xray_milestone(channel, x_val, {
                "symbol":  symbol or "TOKEN",
                "chain":   chain or "",
                "entry":   entry_fmt or "?",
                "ms_mc":   current_fmt or "?",
                "ca":      ca or "",
                "post_id": None,
                "at":      datetime.utcnow().isoformat(),
            })
        except Exception as _e_xr:
            logger.warning(f"xray archive write failed (silent): {_e_xr}")
        if _call_key and not _blocked_deleted:
            try:
                await award_points_for_milestone(channel, _call_key, x_val)
            except Exception as _e_pt:
                logger.warning(f"points award failed (silent): {_e_pt}")
        # Ranking refresh: recorded-only milestones bhi leaderboard/champions me
        # foran count hone chahiye (owner requirement).
        if x_val >= 2 and not _blocked_deleted:
            try:
                asyncio.create_task(_update_leaderboard_with_premium_emojis(bot))
                asyncio.create_task(_update_champions_with_premium_emojis(bot))
            except Exception:
                pass
        logger.info(f"⏸️ {x_val}X @{channel} recorded only — no channel post")
        return "silent"

    # Compute badge once, pass to build_alert + emoji list
    badge    = _get_kol_badge(channel)
    text     = build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol,
                           badge=badge, x_style=True)
    keyboard = build_x_alert_keyboard(channel, msg_id, ca, chain, x_val=x_val)
    # Pack-based premium emojis — X value decide karti hai kaun sa pack:
    #   2X-10X -> red (default) · 11X+ -> blue · owner ki custom ranges upar.
    pack     = _get_alert_pack_for_x(x_val)
    emoji_id = list(_get_alert_emoji_ids(x_val, chain, pack))
    # Append badge premium emoji ID — ab sirf 1 (rank numbering emoji),
    # kyunki naya badge format "Champion KOL - Rank # 🔮" hai (koi star 🔮 nahi).
    if badge:
        if badge["type"] == "leaderboard":
            rank_emoji_id = LEADERBOARD_PREMIUM_EMOJIS.get(badge["rank"], LEADERBOARD_PREMIUM_EMOJIS[1])
            emoji_id += [rank_emoji_id]
        else:
            rank_emoji_id = CHAMPIONS_PREMIUM_EMOJIS.get(badge["rank"], CHAMPIONS_PREMIUM_EMOJIS[1])
            emoji_id += [globals().get("CHAMP_BADGE_LEAD_EMOJI_ID") or 5823636561413021540,
                         rank_emoji_id]
    logger.info(f"Alert emoji_id for {x_val}x chain={chain} pack={pack['name']}: {emoji_id} | userbot={userbot_client is not None}")

    # ── Send to main channel ─────────────────────────────────────────────────────
    # PATTERN: Single send — NO edit after posting.
    # Edit = Telegram shows pencil "edited" mark, which we do NOT want on alerts.
    # Priority:
    #   1. Userbot sends media directly with premium emojis (no edit, no pencil)
    #   2. Bot sends media without emojis  (no edit, no pencil)
    #   3. Userbot sends text with emojis  (no edit, no pencil)
    #   4. Bot sends text only             (no edit, no pencil)
    posted         = False
    wizard_post_id = None
    call_key       = f"{channel}_{ca}" if ca else None
    # True jab post Bot API se (bina premium emoji) gayi — tab userbot edit karke
    # emojis lagayega aur uske baad BOT forward karega.
    needs_emoji_fix = False

    # ── 🎬 Champion KOL HD video (sirf badge type "champions") ───────────────
    # Lead KOL / baaqi KOLs yahan se guzar jaate hain, kuch nahi badalta.
    # Video na bane to posted False rehta hai -> neeche normal alert jati hai.
    if badge and badge.get("type") == "champions":
        _cv_ok, _cv_id, _cv_fix = await _champion_video_try_send(
            bot, channel, x_val, chain, entry_fmt, current_fmt, ca, symbol,
            badge, text, keyboard, emoji_id, pack)
        if _cv_ok:
            posted          = True
            wizard_post_id  = _cv_id
            needs_emoji_fix = _cv_fix

    # `force=True` is the real-time path.  Keep media in realtime alerts, but
    # use the Telegram Bot API/file_id first.  This reuses Telegram's stored
    # media instantly and avoids the slow userbot download -> re-upload path.
    # Normal alerts still prefer the userbot so premium emoji entities remain.
    if not posted and media and media.get("file_id"):
        fid, ftype = media["file_id"], media.get("type", "photo")

        # Instant path: Bot API can send an existing file_id without copying
        # the file through Railway.  This preserves media and keeps latency low.
        # NOTE: only take this path when userbot/emoji isn't available — bot
        # accounts can't attach premium emoji entities, and editing afterward
        # to add them shows Telegram's pencil "edited" mark. When userbot +
        # emoji_id ARE available we skip straight to the userbot media-send
        # path below, which attaches the emojis in the original send (no edit,
        # no pencil), even though it's a touch slower than the instant path.
        if force and not _BOT_CHANNEL_FORBIDDEN["v"] and not (userbot_client and emoji_id):
            try:
                if ftype == "photo":
                    sent_msg = await bot.send_photo(
                        TARGET_CHANNEL, photo=fid, caption=text,
                        parse_mode="HTML", reply_markup=keyboard)
                else:
                    sent_msg = await bot.send_video(
                        TARGET_CHANNEL, video=fid, caption=text,
                        parse_mode="HTML", reply_markup=keyboard)
                posted         = True
                wizard_post_id = sent_msg.message_id
                logger.info(f"✅ Instant {x_val}x media sent by bot API (no userbot/emoji available): msg_id={wizard_post_id}")
            except Exception as e:
                # Bot channel ka member/admin nahi hai -> har alert par ye 403
                # round-trip waqt zaya karta tha. Ek dafa pata chal jaye to
                # seedha userbot path use karo (aur owner ko batao).
                if "not a member" in str(e).lower() or "forbidden" in str(e).lower():
                    _BOT_CHANNEL_FORBIDDEN["v"] = True
                    logger.error("🚫 Bot is NOT admin in " + str(TARGET_CHANNEL) +
                                 " — bot API media path disabled, using userbot. "
                                 "Fix: add the bot as admin in the channel for the fastest path.")
                logger.warning(f"Instant media send failed, trying userbot: {e}")

        # Normal path: userbot sends media + premium emojis in one shot.
        # DIAG/FIX: single 6s attempt was too short under real load (job is
        # already overloaded — see monitoring_job hard-timeout in logs — so a
        # slightly slow userbot response used to fall straight through to the
        # bot-fallback below, which is GUARANTEED to fail if the bot isn't
        # admin in TARGET_CHANNEL, landing on text-only. One retry gives a
        # transient stall a second chance before we give up on media.
        media_fail_reason = None
        if not posted and userbot_client and emoji_id:
            # FIX (2026-10-15): pehle 10s x 2 attempts the — pehli attempt cancel
            # hone par upload dobara shuru hota tha (aur kai dafa upload 10.4s mein
            # complete hone wala hota tha). Ab EK attempt, lamba timeout; upload
            # jaldi ho jaye to foran aage barh jata hai.
            for attempt in (1,):
                try:
                    sent_msg = await asyncio.wait_for(
                        _userbot_send_media_with_emoji(
                            bot, TARGET_CHANNEL, fid, ftype, text, emoji_id,
                            keyboard=keyboard, forced_pack=pack),
                        timeout=USERBOT_MEDIA_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    sent_msg = None
                    media_fail_reason = f"userbot media timed out after {USERBOT_MEDIA_TIMEOUT_SECONDS}s (attempt {attempt})"
                    logger.warning(f"{media_fail_reason}; continuing to Bot API fallback")
                    break
                if sent_msg:
                    posted = True
                    try: wizard_post_id = sent_msg.id
                    except Exception: pass
                    logger.info(f"✅ Alert {x_val}x sent by userbot (no pencil): msg_id={wizard_post_id}")
                break
        elif not posted:
            media_fail_reason = "userbot_client not connected" if not userbot_client else "no emoji_id"

        # Fallback: Bot sends media without premium emojis.
        if not posted:
            try:
                if ftype == "photo":
                    sent_msg = await bot.send_photo(
                        TARGET_CHANNEL, photo=fid, caption=text,
                        parse_mode="HTML", reply_markup=keyboard)
                else:
                    sent_msg = await bot.send_video(
                        TARGET_CHANNEL, video=fid, caption=text,
                        parse_mode="HTML", reply_markup=keyboard)
                posted         = True
                wizard_post_id = sent_msg.message_id
                needs_emoji_fix = True
                logger.info(f"Alert {x_val}x media sent by bot (no emojis): msg_id={wizard_post_id}")
            except Exception as e:
                if "not a member" in str(e).lower() or "forbidden" in str(e).lower():
                    _BOT_CHANNEL_FORBIDDEN["v"] = True
                logger.error(
                    f"Media alert send failed (after userbot: {media_fail_reason}): {e}")

    if not posted:
        # Option 3: Text-only — userbot sends with premium emojis.
        # Same one-retry pattern as the media path above: a single transient
        # stall (flood-wait, brief reconnect) shouldn't drop straight to the
        # Bot API fallback, which cannot attach premium emoji entities at all.
        if userbot_client and emoji_id:
            for attempt in (1, 2):
                try:
                    sent_msg = await asyncio.wait_for(
                        _userbot_send_with_premium_emoji(
                            TARGET_CHANNEL, text, emoji_id=emoji_id,
                            keyboard=keyboard, forced_pack=pack),
                        timeout=USERBOT_SEND_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    sent_msg = None
                    if attempt == 1:
                        logger.warning(
                            f"Userbot text send timed out after {USERBOT_SEND_TIMEOUT_SECONDS}s "
                            "(attempt 1), retrying once")
                        continue
                    logger.warning(
                        f"Userbot text send timed out after {USERBOT_SEND_TIMEOUT_SECONDS}s "
                        "(attempt 2); continuing to Bot API fallback (no premium emoji)")
                    break
                if sent_msg:
                    posted = True
                    try: wizard_post_id = sent_msg.id
                    except Exception: pass
                    logger.info(f"Alert {x_val}x text sent by userbot with premium emojis")
                break

        # Option 4: Bot sends text (final fallback, no edit)
        if not posted:
            try:
                sent_msg = await bot.send_message(
                    chat_id=TARGET_CHANNEL, text=text,
                    parse_mode="HTML", disable_web_page_preview=True,
                    reply_markup=keyboard)
                posted = True
                try: wizard_post_id = sent_msg.message_id
                except Exception: pass
                needs_emoji_fix = True
                logger.info(f"Alert {x_val}x text sent by bot (text fallback)")
            except Exception as e:
                logger.error(f"Text alert to channel failed: {e}")

    # The caller persists the milestone only after a confirmed Telegram send.
    # This makes transient Telegram/userbot failures retry on the next tick.
    if not posted:
        return False

    # DM delivery is scheduled IMMEDIATELY after Telegram confirms the original
    # @WizardScan post.  It must not depend on milestone-file writes, X-Ray,
    # points, counters, promo jobs, or any other bookkeeping below: any one of
    # those can fail independently and previously prevented every DM forward.
    # `_forward_alert_recipients` uses forward_message only.
    if needs_emoji_fix and userbot_client and emoji_id:
        # Post bina premium emoji ke gayi -> pehle userbot edit se emojis lagao,
        # PHIR bot forward kare (forward original ka snapshot leta hai, isliye
        # edit se pehle forward karna non-premium copy bhej deta).
        asyncio.create_task(
            _fix_alert_emojis_then_forward(
                bot, channel, x_val, wizard_post_id, text, emoji_id, pack, keyboard),
            name=f"emoji-fix-{channel}-{x_val}-{wizard_post_id}")
    elif wizard_post_id:
        asyncio.create_task(
            _forward_alert_recipients(bot, channel, x_val, wizard_post_id),
            name=f"forward-alert-{channel}-{x_val}-{wizard_post_id}")

    # Save WizardScan post ID for this milestone
    if wizard_post_id and call_key:
        if call_key not in milestone_posts:
            milestone_posts[call_key] = {}
        milestone_posts[call_key][str(x_val)] = wizard_post_id
        _save_milestone_posts()

    # Permanent X-Ray record for this channel + milestone
    try:
        record_xray_milestone(channel, x_val, {
            "symbol":  symbol or "TOKEN",
            "chain":   chain or "",
            "entry":   entry_fmt or "?",
            "ms_mc":   current_fmt or "?",
            "ca":      ca or "",
            "post_id": wizard_post_id,
            "at":      datetime.utcnow().isoformat(),
        })
    except Exception as _e_xr:
        logger.warning(f"xray archive write failed: {_e_xr}")

    # Award points to channel for this milestone (champion kols points system)
    if call_key:
        awarded = await award_points_for_milestone(channel, call_key, x_val)
        if awarded > 0:
            logger.info(f"⭐ Points awarded: +{awarded} to @{channel} for {x_val}X milestone")

    # Track post count
    count = _inc_channel_post_count()
    # Hashtag every 100 alerts
    if count % 100 == 0:
        asyncio.create_task(_post_hashtag_to_channel(bot))
    # Promo post every 25 alerts (if enabled)
    if count % 25 == 0:
        promo_cfg = load_config()
        if promo_cfg.get("promo_enabled"):
            asyncio.create_task(_post_promo_to_channel(bot))

    return True

#  ── Auto self-channel forward ────────────────────────────────────────────
#  If the bot is admin in a tracked KOL's OWN channel, milestone alerts are
#  forwarded straight into that channel too — automatically, no /linkme
#  needed. This MUST use forward_message (not a rebuilt/copy-pasted text
#  message) so premium emojis in the original post carry over correctly.
_ADMIN_CH_CACHE = {}       # channel(lower) -> (is_admin: bool, ts: float)
_ADMIN_CH_CACHE_TTL = 1800  # 30 min

async def _bot_is_admin_in_channel(bot, channel: str) -> bool:
    if not channel:
        return False
    key = channel.lower()
    cached = _ADMIN_CH_CACHE.get(key)
    if cached:
        # Positive result 30 min cache; negative sirf 2 min (taake owner ke
        # bot ko admin banate hi agli alert forward ho jaye).
        ttl = _ADMIN_CH_CACHE_TTL if cached[0] else 120
        if (time.time() - cached[1]) < ttl:
            return cached[0]
    is_admin = False
    try:
        me = await bot.get_me()
        cm = await bot.get_chat_member(f"@{channel}", me.id)
        is_admin = getattr(cm, "status", "") in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"admin check failed for @{channel}: {e}")
        is_admin = False
    _ADMIN_CH_CACHE[key] = (is_admin, time.time())
    return is_admin

# DIAG/FIX: forward_message needs the *bot account* (not the userbot) to be a
# member of TARGET_CHANNEL, because Bot API can only forward messages out of
# chats it can see. If the bot lost admin/membership in @WizardScan, EVERY
# single forward — to KOL owners, subscribers, linked channels, all of it —
# fails the same way, silently, one retry-loop per recipient. We now detect
# that specific failure once, log ONE loud actionable line (rate-limited so
# it doesn't spam), and keep rechecking periodically so forwards resume
# automatically the moment the owner re-adds the bot as admin — no restart
# needed.
_TARGET_CH_ACCESS = {"ok": True, "last_check": 0.0, "last_warn": 0.0}
_TARGET_CH_RECHECK_INTERVAL = 300   # re-verify every 5 min while broken
_TARGET_CH_WARN_INTERVAL    = 600   # don't spam the log more than every 10 min

async def _ensure_bot_can_read_target_channel(bot) -> bool:
    """Returns True if the bot account itself is currently able to read/forward
    from TARGET_CHANNEL. Cheap positive-cache; frequent recheck while broken."""
    now = time.time()
    if _TARGET_CH_ACCESS["ok"] and (now - _TARGET_CH_ACCESS["last_check"]) < _ADMIN_CH_CACHE_TTL:
        return True
    if not _TARGET_CH_ACCESS["ok"] and (now - _TARGET_CH_ACCESS["last_check"]) < _TARGET_CH_RECHECK_INTERVAL:
        return False
    ok = False
    try:
        me = await bot.get_me()
        cm = await bot.get_chat_member(TARGET_CHANNEL, me.id)
        ok = getattr(cm, "status", "") in ("administrator", "creator", "member")
    except Exception as e:
        logger.debug(f"target-channel access check failed: {e}")
        ok = False
    was_ok = _TARGET_CH_ACCESS["ok"]
    _TARGET_CH_ACCESS["ok"] = ok
    _TARGET_CH_ACCESS["last_check"] = now
    if ok and not was_ok:
        logger.info(f"✅ Bot access to {TARGET_CHANNEL} restored — DM/channel forwards resuming.")
        _BOT_CHANNEL_FORBIDDEN["v"] = False
    return ok


async def _forward_alert_post(bot, chat_id, wizard_post_id, label="") -> bool:
    """ALWAYS forward (never copy/paste) so premium emojis survive.
    Bot API only: retry, then return False — caller must NOT fall back to
    copy_message or a different template; it simply skips that recipient.
    Never use the owner/Thomas userbot for user DMs or KOL channels."""
    if not wizard_post_id:
        return False

    # DIAG/FIX: fail fast + loudly instead of burning 3 retries per recipient
    # on a failure mode that is known to affect ALL recipients equally.
    if not await _ensure_bot_can_read_target_channel(bot):
        now = time.time()
        if (now - _TARGET_CH_ACCESS["last_warn"]) > _TARGET_CH_WARN_INTERVAL:
            _TARGET_CH_ACCESS["last_warn"] = now
            logger.error(
                f"🚫 DM/channel forwards are ALL failing: the bot account is not "
                f"an admin/member of {TARGET_CHANNEL}. forward_message requires "
                f"the bot itself (not the userbot) to have access to the source "
                f"channel. FIX: add the bot as admin in {TARGET_CHANNEL} — "
                f"forwarding will resume automatically within {_TARGET_CH_RECHECK_INTERVAL}s, "
                f"no restart needed.")
        return False

    last_err = None
    for attempt in range(3):
        try:
            await bot.forward_message(chat_id=chat_id,
                                      from_chat_id=TARGET_CHANNEL,
                                      message_id=int(wizard_post_id))
            logger.info(f"✅ Forwarded alert post {wizard_post_id} → {chat_id} {label}")
            return True
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "flood" in msg:
                await asyncio.sleep(2 + attempt * 2)
                continue
            if any(k in msg for k in ("chat not found", "bot was kicked", "not enough rights",
                                      "forbidden", "chat_write_forbidden", "blocked")):
                break
            await asyncio.sleep(0.6)
    logger.warning(
        f"Bot-API forward → {chat_id} failed ({last_err}); "
        "owner userbot fallback is disabled"
    )
    return False


async def _auto_forward_to_own_channel(bot, channel, wizard_post_id, already_linked: set):
    """Forward the just-posted alert into the KOL's own channel if the bot
    has admin/post rights there — skipped if /linkme already set this up
    (that loop already forwards it, avoids a duplicate post)."""
    if not channel or not wizard_post_id:
        return
    if channel.lower() in already_linked:
        return
    try:
        if not await _bot_is_admin_in_channel(bot, channel):
            logger.debug(f"skip self-forward @{channel}: bot not admin")
            return
        ok = await _forward_alert_post(bot, f"@{channel}", wizard_post_id,
                                       label="(own channel)")
        if not ok:
            logger.warning(f"Auto self-channel forward @{channel} failed on all paths")
    except Exception as e:
        logger.warning(f"Auto self-channel forward @{channel} failed: {e}")


async def _text_alert(bot, chat_id, text, keyboard):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=keyboard)
    except Exception as e: logger.error(f"Text alert ({chat_id}): {e}")

# /linksmart only forwards these tiers (fewer, "solid" alerts); anything
# above 100X forwards normally same as /linkme, since past 100X the bot's
# own alert cadence is already coarse (every +50/+100/...).
SMART_LINK_MILESTONES = {5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100}

def _smart_link_milestone_qualifies(x_val) -> bool:
    try:
        xv = float(x_val)
    except (TypeError, ValueError):
        return True  # fail-open — never silently eat an alert on a bad value
    if xv > 100:
        return True
    return int(round(xv)) in SMART_LINK_MILESTONES

async def _forward_alert_recipients(bot, channel, x_val, wizard_post_id):
    """Forward the already-published WizardScan post without rebuilding it.

    Forwarding preserves Telegram's premium custom-emoji entities.  All
    recipients are handled concurrently and subscriber IDs are normalized to
    int, which also repairs old JSON files that stored IDs as strings.
    """
    try:
        channel_key = (channel or "").lstrip("@").lower()
        owner_id = load_kol_owners().get(channel_key)
        try:
            owner_id = int(owner_id) if owner_id is not None else None
        except (TypeError, ValueError):
            owner_id = None

        linked = load_linked_channels()
        destinations = []
        for kol_ch, link_val in linked.items():
            if str(kol_ch).lstrip("@").lower() != channel_key:
                continue
            # /linkme stores a plain "@channel" string (forward everything).
            # /linksmart stores {"target": "@channel", "mode": "smart"} so
            # only the milestones in SMART_LINK_MILESTONES get forwarded.
            if isinstance(link_val, dict):
                target = link_val.get("target")
                mode   = link_val.get("mode", "full")
            else:
                target = link_val
                mode   = "full"
            if not target:
                continue
            if mode == "smart" and not _smart_link_milestone_qualifies(x_val):
                continue
            destinations.append((target, f"({x_val}X linked)"))

        # Auto-forward to the KOL's own channel, if bot has admin rights.
        try:
            await _auto_forward_to_own_channel(
                bot, channel_key, wizard_post_id,
                already_linked={
                    str(k).lstrip("@").lower() for k in linked.keys()
                })
        except Exception as e:
            logger.warning(f"auto self-forward failed @{channel_key}: {e}")

        if owner_id:
            destinations.append((owner_id, f"(KOL owner @{channel_key} {x_val}X)"))

        # Channel-specific subscribers are the primary /subscribe path.
        specific = load_channel_subs().get(channel_key, []) or []
        # Keep legacy global subscribers working too, without duplicates.
        global_subs = load_subscriptions() or []
        seen = set()
        forwards = []
        for uid in list(specific) + list(global_subs):
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid == owner_id or uid in seen:
                continue
            seen.add(uid)
            label = f"(sub @{channel_key})" if uid in {
                int(x) for x in specific
                if str(x).lstrip("-").isdigit()
            } else "(global sub)"
            destinations.append((uid, label))

        async def _one(chat_id, label):
            # Forward only. Never copy, rebuild, or send a link/template.
            ok = await _forward_alert_post(bot, chat_id, wizard_post_id, label=label)
            if not ok:
                logger.error(
                    f"Alert forward failed: chat={chat_id} "
                    f"post={wizard_post_id} @{channel_key} {x_val}X")



        await asyncio.gather(*[_one(chat_id, label) for chat_id, label in destinations],
                             return_exceptions=True)
    except Exception as e:
        logger.error(f"_forward_alert_recipients failed @{channel}: {e}")



# ─── Hot Streak Alert (consecutive 2X+ calls per KOL) ─────────────────────────
# Owner feature: when a channel/caller posts 3+ calls in a row that ALL reach
# 2X or higher (no dead call in between), post a "Hot Streak" banner about
# that KOL. Holds at N calls (min 3, max 9) until either it caps at 9, or the
# very next call after the streak is confirmed dead (never reached 2X) —
# whichever happens first — matching the owner's exact spec.
HOT_STREAK_STATE_FILE = _dp("hot_streak_state.json")
HOT_STREAK_MIN = 4   # OWNER RULE (2026-10-15): minimum 4 alpha (2X+) calls in a row
HOT_STREAK_MAX = 9
# Backstop only — real rug detection (call["rugged"]) resolves a dead call
# instantly. This just prevents a streak from waiting forever on a call that
# never rugs and never moves either.
# OWNER RULE (2026-10-15): next call that has not hit 2X within 12h is DEAD.
HOT_STREAK_DEAD_TIMEOUT_HOURS = float(os.environ.get("HOT_STREAK_DEAD_TIMEOUT_HOURS", "12") or 12)

HOT_STREAK_COLORS = ["red", "blue", "green", "golden"]

HOT_STREAK_PACKS = {
    "red": {
        "checks": [5879457242041819253, 5879776611514981971, 5879906920822742529,
                   5879485524401464691, 5879857764922041309, 5879678952548605515,
                   5879591528489296028],
        "emoji": 5922462341554774161,
    },
    "blue": {
        "checks": [5879779519207842656, 5879792571613454694, 5879898081780048072,
                   5879856991827927106, 5879983920996425347, 5879776126183678163,
                   5879773811196305347],
        "emoji": 5920335246116527693,
    },
    "green": {
        "checks": [5879596051089858109, 5879559402133921572, 5879776366701845939,
                   5879673716983472905, 5879956209867431448, 5879477827820068149,
                   5879661618060600315],
        "emoji": 5922350041044886140,
    },
    "golden": {
        "checks": [5879553921755652107, 5879835753214648278, 5879893314366349311,
                   5879903128366620392, 5879693164595388798, 5879972659592174922,
                   5879645576357748539],
        "emoji": 5922570858198474764,
    },
}

# FIX (2026-10-07, duplicate/inconsistent Hot Streak alerts): every streak
# state change + "claim a streak to post" now happens under ONE lock. The old
# code posted the alert FIRST and only consumed the streak from state AFTER the
# (slow, multi-second) send finished, so every other _hs_evaluate running in
# that window (other 2X hits, the 45s sweep, the stall sweep) read the same
# un-consumed streak and posted it again -> 3-4 bots on one channel within
# 2-3 seconds, each with a different streak length. Now: claim (remove from
# state + save) under the lock, THEN post outside it.
_HS_LOCK = None
def _hs_lock():
    global _HS_LOCK
    if _HS_LOCK is None:
        _HS_LOCK = asyncio.Lock()
    return _HS_LOCK

# last posted fingerprint per channel — hard stop against any duplicate post
_HS_RECENT_POSTS = {}   # channel_key -> (frozenset(call_keys), ts)
def _hs_is_duplicate(ch, hits):
    keys = frozenset(r.get("call_key") for r in hits)
    now = time.time()
    for k in [k for k, (_, t) in _HS_RECENT_POSTS.items() if now - t > 900]:
        _HS_RECENT_POSTS.pop(k, None)
    prev = _HS_RECENT_POSTS.get(ch)
    if prev and (prev[0] & keys):
        return True
    _HS_RECENT_POSTS[ch] = (keys, now)
    return False

def _hs_snapshot_link_info(row, call_key):
    """Freeze channel/msg_id into the streak row so the CALL link never goes
    empty after the call is archived / removed from tracked_calls."""
    call = tracked_calls.get(call_key, {}) or {}
    if not call:
        arch = (load_call_archive() or {}).get(call_key) or {}
        call = arch if isinstance(arch, dict) else {}
    ch = str(call.get("channel") or "").lstrip("@")
    mid = str(call.get("msg_id") or "").strip()
    if ch and mid and mid not in ("0", "None"):
        row["src_channel"] = ch
        row["src_msg_id"] = mid
    posts = milestone_posts.get(call_key, {}) or {}
    pid = posts.get("2") or posts.get("2.0") or (list(posts.values())[0] if posts else None)
    if pid:
        row["wiz_post_id"] = pid

def _hs_load():
    return load_json(HOT_STREAK_STATE_FILE, {})

def _hs_save(d):
    save_json(HOT_STREAK_STATE_FILE, d)

# OWNER RULE (2026-10-15): once a call has been SETTLED (counted in a posted
# streak, or declared dead) it must never become part of a streak again — a dead
# call that hits 2X later, or a posted call hitting 3X/5X later, used to re-enter
# the sequence as a brand-new "hit" row (phantom streaks / repeat alerts).
_HS_SETTLED_MAX = 5000
def _hs_settle(state, rows):
    g = state.setdefault("_global", {})
    lst = g.setdefault("settled", [])
    have = set(lst)
    for r in rows:
        k = r.get("call_key")
        if k and k not in have:
            lst.append(k)
            have.add(k)
    if len(lst) > _HS_SETTLED_MAX:
        del lst[:len(lst) - _HS_SETTLED_MAX]

def _hs_unsettle(state, rows):
    g = state.setdefault("_global", {})
    drop = {r.get("call_key") for r in rows}
    g["settled"] = [k for k in g.get("settled", []) if k not in drop]

def _hs_channel_key(channel):
    return (channel or "").lstrip("@").lower().strip()

def _hs_next_color(state):
    """Rotate: red -> blue -> green -> golden -> red -> ..."""
    idx = int((state.get("_global") or {}).get("color_idx", 0) or 0)
    color = HOT_STREAK_COLORS[idx % 4]
    state.setdefault("_global", {})["color_idx"] = idx + 1
    return color

def _hs_register_call(channel, call_key, chain):
    """Hook: call right after a brand-new call starts tracking for a channel."""
    try:
        ch = _hs_channel_key(channel)
        if not ch or not call_key:
            return
        state = _hs_load()
        entry = state.setdefault(ch, {"seq": []})
        if any(row.get("call_key") == call_key for row in entry["seq"]):
            return
        entry["seq"].append({
            "call_key":   call_key,
            "chain":      (chain or "SOL").upper(),
            "status":     "pending",     # pending -> hit / dead
            "peak_x":     0.0,
            "registered": datetime.utcnow().isoformat(),
        })
        try:
            _hs_snapshot_link_info(entry["seq"][-1], call_key)
        except Exception:
            pass
        _hs_save(state)
    except Exception as e:
        logger.warning(f"hot-streak register failed @{channel}: {e}")

async def _hs_on_call_hit(bot, channel, call_key, x_val):
    """Hook: call right after a call's 2X+ milestone alert is successfully sent."""
    try:
        if not call_key or x_val < 2:
            return
        ch = _hs_channel_key(channel)
        if not ch:
            return
        async with _hs_lock():
            state = _hs_load()
            if call_key in set((state.get("_global") or {}).get("settled", [])):
                return   # already counted in a streak / declared dead — never reuse
            entry = state.setdefault(ch, {"seq": []})
            row = next((r for r in entry["seq"] if r.get("call_key") == call_key), None)
            if row is not None and row.get("status") == "dead":
                return   # dead call hitting 2X late must NOT join a streak
            if row is None:
                row = {"call_key": call_key,
                       "chain": (tracked_calls.get(call_key, {}) or {}).get("chain", "SOL"),
                       "status": "pending", "peak_x": 0.0,
                       "registered": datetime.utcnow().isoformat()}
                entry["seq"].append(row)
            # OWNER FIX (2026-09-24, "RH calls showing SOL emoji" bug): the row's
            # chain used to be frozen at whatever _hs_register_call guessed the
            # moment the call was FIRST seen — usually the generic "EVM" bucket,
            # since chain detection only resolves to a specific chain (RH/ETH/
            # BNB/BASE) later once the live dex data comes in. "EVM" isn't a key
            # in HOT_STREAK_PACKS[...]["chains"], so _hs_chain_emoji_for() always
            # fell back to the SOL emoji for every non-SOL EVM-family call,
            # including RH. By the time a call actually HITS 2X (here), its
            # tracked_calls chain has long since been resolved to the real value
            # — so re-sync it into the streak row every hit, not just on first
            # registration.
            resolved_chain = (tracked_calls.get(call_key, {}) or {}).get("chain")
            if resolved_chain and resolved_chain.upper() != "EVM":
                row["chain"] = resolved_chain.upper()
            row["status"] = "hit"
            row["hit_at"] = datetime.utcnow().isoformat()
            try:
                row["peak_x"] = max(float(row.get("peak_x", 0) or 0), float(x_val))
            except (TypeError, ValueError):
                row["peak_x"] = float(x_val)
            try:
                _hs_snapshot_link_info(row, call_key)
            except Exception:
                pass
            _hs_save(state)
        await _hs_evaluate(bot, ch)
    except Exception as e:
        logger.warning(f"hot-streak hit-hook failed @{channel}: {e}")

def _hs_call_is_dead(call_key):
    """True if this call is confirmed to have never reached 2X (permanently
    rugged sub-2X, archived sub-2X, vanished, or stalled past the timeout)."""
    call = tracked_calls.get(call_key)
    if call is not None:
        if call.get("rugged") and call_peak_ratio(call) < 2:
            return True
        try:
            reg = datetime.fromisoformat(call.get("tracked_since", ""))
            age_h = (datetime.utcnow() - reg).total_seconds() / 3600.0
        except Exception:
            age_h = 0.0
        if age_h >= HOT_STREAK_DEAD_TIMEOUT_HOURS and call_peak_ratio(call) < 2:
            return True
        return False
    archive = load_call_archive()
    arch = archive.get(call_key)
    if arch is not None:
        try:
            return float(arch.get("best_x", 0) or 0) < 2
        except (TypeError, ValueError):
            return True
    # vanished with no archive trace — don't let a streak hang forever on it
    return True

async def _hs_resolve_pending(bot):
    """Periodic sweep: settle 'pending' streak entries whose calls have since
    rugged / timed out / vanished without reaching 2X, then re-evaluate every
    channel that changed."""
    try:
        changed = set()
        async with _hs_lock():
            state = _hs_load()
            dirty = False
            for ch, entry in list(state.items()):   # list(): _hs_settle may add "_global"
                if ch == "_global":
                    continue
                for row in entry.get("seq", []):
                    if row.get("status") == "pending" and _hs_call_is_dead(row.get("call_key")):
                        row["status"] = "dead"
                        _hs_settle(state, [row])
                        dirty = True
                        changed.add(ch)
            if dirty:
                _hs_save(state)
        for ch in changed:
            await _hs_evaluate(bot, ch)
    except Exception as e:
        logger.warning(f"hot-streak resolve sweep failed: {e}")

async def _hs_restore_claimed(ch, claimed):
    """Post failed — put the claimed streak back at the front of the sequence."""
    async with _hs_lock():
        state = _hs_load()
        entry = state.setdefault(ch, {"seq": []})
        have = {r.get("call_key") for r in entry["seq"]}
        entry["seq"] = [r for r in claimed if r.get("call_key") not in have] + entry["seq"]
        _hs_unsettle(state, claimed)
        _hs_save(state)

async def _hs_evaluate(bot, channel):
    """Core streak state machine — hold a 3..9-call streak until it either
    caps at 9, or the call right after it is confirmed dead; then post+reset.

    FIX (2026-10-07): the streak is CLAIMED (removed from state and saved)
    under the lock BEFORE the alert is sent, so concurrent evaluations can
    never see the same streak twice."""
    ch = _hs_channel_key(channel)
    if not ch:
        return
    while True:
        to_post = None
        async with _hs_lock():
            state = _hs_load()
            entry = state.get(ch)
            if not entry:
                return
            seq = entry.get("seq", [])
            hits = []
            i = 0
            while i < len(seq) and seq[i].get("status") == "hit":
                hits.append(seq[i])
                i += 1

            if len(hits) >= HOT_STREAK_MAX:
                to_post = hits[:HOT_STREAK_MAX]
                entry["seq"] = seq[HOT_STREAK_MAX:]
                _hs_settle(state, to_post)
                _hs_save(state)
            elif i >= len(seq):
                return  # no next call registered yet — keep holding
            else:
                nxt = seq[i]
                if nxt.get("status") != "dead":
                    return  # next call still pending -> keep holding
                if len(hits) >= HOT_STREAK_MIN:
                    to_post = hits
                # Streak ends here. Hits shorter than MIN are discarded too
                # (they never count again), and the dead call is settled.
                _hs_settle(state, hits + [nxt])
                entry["seq"] = seq[i + 1:]
                _hs_save(state)
        if to_post:
            res = await _hs_post_alert(bot, ch, to_post)
            if res is None:
                await _hs_restore_claimed(ch, to_post)
                return
        # loop again: more of the sequence may be ready to settle

def _hs_chain_emoji_for(pack, chain=None):
    """OWNER CHANGE (2026-10-07): per-chain emojis removed. Every CALL line now
    uses ONE emoji — the streak color's own emoji (red/green/golden/blue)."""
    return f'<tg-emoji emoji-id="{pack["emoji"]}">🔥</tg-emoji>'

def _hs_chain_ids_in_order(pack, hits):
    """Kept for compatibility. The CALL emojis are explicit <tg-emoji> tags in
    the text, so no positional IDs are needed any more."""
    return []

def _hs_banner_html(pack):
    """OWNER FIX (2026-09-23): the 7 IDs under pack['checks'] are NOT
    per-call checkmarks — they're 7 slices of one wide 'HOT STREAK ALERT'
    banner graphic that only looks right when all 7 render back-to-back in
    a single row. The old code sprinkled one per CALL line instead, which
    is why the banner showed up as broken letter-fragments ('A', 'OT',
    'ST'...) scattered next to random calls. Join them here, all 7 in a
    row, as the post's title graphic instead."""
    return "".join(f'<tg-emoji emoji-id="{cid}">🔥</tg-emoji>' for cid in pack["checks"])

def _hs_call_link(row):
    """FIX (2026-09-30 + 2026-10-07): every CALL must be a link. Order:
    1) live @WizardScan milestone post, 2) wizard post id frozen in the row,
    3) live tracked call's original post, 4) channel/msg_id frozen in the row,
    5) archive. The frozen copies stop links going empty once a call is
    archived / dropped from tracked_calls."""
    ck = row.get("call_key")
    posts = milestone_posts.get(ck, {}) or {}
    pid = (posts.get("2") or posts.get("2.0") or (list(posts.values())[0] if posts else None)
           or row.get("wiz_post_id"))
    if pid:
        return f'https://t.me/{TARGET_CHANNEL.lstrip("@")}/{pid}'
    call = tracked_calls.get(ck, {}) or {}
    ch = str(call.get("channel") or row.get("src_channel") or "").lstrip("@")
    mid = str(call.get("msg_id") or row.get("src_msg_id") or "").strip()
    if not (ch and mid) or mid in ("0", "None"):
        arch = (load_call_archive() or {}).get(ck) or {}
        if isinstance(arch, dict):
            ch = str(arch.get("channel") or "").lstrip("@")
            mid = str(arch.get("msg_id") or "").strip()
    if ch and mid and mid not in ("0", "None"):
        return f"https://t.me/{ch}/{mid}"
    return None

def _hs_build_text(channel_key, hits, color):
    pack = HOT_STREAK_PACKS[color]
    n = len(hits)
    lines_call = []
    for idx, row in enumerate(hits):
        post_url = _hs_call_link(row)
        chain_e = _hs_chain_emoji_for(pack)
        label = f"CALL {idx + 1}"
        if post_url:
            label = f'<a href="{post_url}">{label}</a>'
        # Color emoji goes BEFORE the label: "<emoji> CALL 1".
        lines_call.append(f"{chain_e} {label}")

    rows_txt = []
    for a in range(0, len(lines_call), 2):
        rows_txt.append("     ".join(lines_call[a:a + 2]))

    best_idx, best_row = max(enumerate(hits), key=lambda t: float(t[1].get("peak_x", 0) or 0))
    best_x = float(best_row.get("peak_x", 0) or 0)
    # OWNER FIX (2026-09-23): test alerts must show a fixed "TestChannel"
    # placeholder, not a real KOL's handle — _display_handle also lowercases
    # everything after the first letter (bigbangcall -> Bigbangcall), which
    # doesn't match "TestChannel"'s exact capitalization either.
    ch_display = "TestChannel" if channel_key.lower() == "testchannel" else _display_handle(channel_key)

    _best_url = _hs_call_link(best_row)
    _best_label = f"CALL {best_idx + 1}"
    if _best_url:
        _best_label = f'<a href="{_best_url}">{_best_label}</a>'
    return (
        _hs_banner_html(pack) + "\n\n"
        f"<b>@{ch_display} delivered {n} consecutive 2X+ calls.\n"
        f"No breaks. No dead calls.</b>\n\n"
        + "\n".join(rows_txt) +
        f"\n\n<b>This KOL is worth following</b>\n"
        f"{n} Wins | Avg {fmt_x(best_x)} ({_best_label})"
    )

async def _hs_can_pin(bot, chat_id, is_channel):
    try:
        if not is_channel:
            return True  # a bot may pin in its own private chat with a user
        me = await bot.get_me()
        cm = await bot.get_chat_member(chat_id, me.id)
        return bool(getattr(cm, "can_pin_messages", False)) or getattr(cm, "status", "") == "creator"
    except Exception:
        return False

async def _hs_forward_and_pin(bot, channel_key, wizard_post_id):
    """Forward the streak post to the KOL owner's DM (auto-pin there), and —
    only if the bot is admin AND has pin rights — forward + pin it in the
    KOL's own channel too. Never posts anywhere the bot lacks rights for."""
    try:
        owner_id = load_kol_owners().get(channel_key)
        try:
            owner_id = int(owner_id) if owner_id is not None else None
        except (TypeError, ValueError):
            owner_id = None
        if owner_id:
            try:
                fwd = await bot.forward_message(chat_id=owner_id, from_chat_id=TARGET_CHANNEL,
                                                message_id=int(wizard_post_id))
                await bot.pin_chat_message(chat_id=owner_id, message_id=fwd.message_id,
                                           disable_notification=True)
            except Exception as e:
                logger.debug(f"hot-streak DM forward/pin skipped @{channel_key}: {e}")

        if await _bot_is_admin_in_channel(bot, channel_key):
            try:
                fwd_ch = await bot.forward_message(chat_id=f"@{channel_key}", from_chat_id=TARGET_CHANNEL,
                                                    message_id=int(wizard_post_id))
                if await _hs_can_pin(bot, f"@{channel_key}", is_channel=True):
                    await bot.pin_chat_message(chat_id=f"@{channel_key}", message_id=fwd_ch.message_id,
                                               disable_notification=True)
            except Exception as e:
                logger.debug(f"hot-streak channel forward/pin skipped @{channel_key}: {e}")
    except Exception as e:
        logger.warning(f"hot-streak forward/pin failed @{channel_key}: {e}")

async def _hs_post_alert(bot, channel_key, hits, forced_color=None):
    """Build + send a Hot Streak Alert to TARGET_CHANNEL, then forward/pin it
    to the KOL owner DM and (if allowed) the KOL's own channel."""
    try:
        if not forced_color and _hs_is_duplicate(channel_key, hits):
            logger.warning(f"🔥 hot-streak DUPLICATE blocked @{channel_key} ({len(hits)} calls)")
            return 0   # falsy but not None -> caller must NOT restore/retry
        state = _hs_load()
        color = forced_color or _hs_next_color(state)
        _hs_save(state)

        # OWNER FIX (2026-09-23): custom/premium emoji ARE supported on
        # inline button labels via icon_custom_emoji_id (Bot API 9.4+,
        # already used elsewhere in this bot via _pkb()) — corrected after
        # initially saying otherwise. Using the theme-specific "View KOL"
        # icon IDs the owner provided, via the same safe-fallback pattern.
        VIEW_KOL_ICON_IDS = {
            "red":    5816433179703124832,
            "golden": 5823636561413021540,
            "blue":   5438132790983942059,
            "green":  5821002775797965247,
        }
        text = _hs_build_text(channel_key, hits, color)
        kb = InlineKeyboardMarkup([[_icon_kb(
            "View KOL", VIEW_KOL_ICON_IDS.get(color), url=_x_alert_caller_link(channel_key, None))]])

        pack = HOT_STREAK_PACKS[color]
        emoji_ids = list(pack["checks"]) + _hs_chain_ids_in_order(pack, hits)
        media = (cfg_get("hot_streak_media", {}) or {}).get(color)

        # (Superseded by the 2026-09-23b fix below: Bot API does NOT actually
        # render <tg-emoji> premium emoji — it silently falls back to the
        # plain glyph. Button is now attached via edit after a userbot send.)
        # FIX (2026-09-23b, "premium emojis gayab" incident): the previous
        # fix sent Hot Streak Alerts via plain Bot API to guarantee the
        # button worked — but Bot API silently drops <tg-emoji> to its plain
        # fallback glyph (🔥/🔮 etc), exactly as already documented elsewhere
        # in this file for dropped-call/X-alerts ("bot sends = no premium
        # emojis"). Now: send via userbot FIRST (premium emoji render
        # correctly, same as every other alert type), then attach the "View
        # KOL" button afterward via the bot's channel-admin edit rights
        # (editMessageReplyMarkup works on ANY message in a channel the bot
        # administers, regardless of who originally sent it). Falls back to
        # plain Bot API (no premium emoji, but never silently missing) only
        # if the userbot is unavailable/fails.
        wizard_post_id = None
        sent_via_userbot = False
        if userbot_client:
            try:
                emoji_ids_hs = list(pack["checks"]) + _hs_chain_ids_in_order(pack, hits)
                if media and media.get("file_id"):
                    sm = await _userbot_send_media_with_emoji(
                        bot, TARGET_CHANNEL, media["file_id"], media.get("type", "photo"),
                        text, emoji_ids_hs, forced_pack=None)
                else:
                    sm = await _userbot_send_with_premium_emoji(
                        TARGET_CHANNEL, text, emoji_id=emoji_ids_hs, forced_pack=None)
                if sm:
                    wizard_post_id = sm.id
                    sent_via_userbot = True
            except Exception as e:
                logger.warning(f"hot-streak userbot send failed, falling back to bot API: {e}")

        if wizard_post_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=TARGET_CHANNEL, message_id=wizard_post_id, reply_markup=kb)
            except Exception as e:
                logger.warning(f"hot-streak View KOL button attach failed: {e}")
        else:
            try:
                if media and media.get("file_id"):
                    if media.get("type") == "video":
                        m = await bot.send_video(TARGET_CHANNEL, video=media["file_id"], caption=text,
                                                 parse_mode="HTML", reply_markup=kb)
                    else:
                        m = await bot.send_photo(TARGET_CHANNEL, photo=media["file_id"], caption=text,
                                                 parse_mode="HTML", reply_markup=kb)
                    wizard_post_id = m.message_id
            except Exception as e:
                logger.warning(f"hot-streak bot-API media send failed: {e}")
        if not wizard_post_id:
            try:
                m = await bot.send_message(TARGET_CHANNEL, text, parse_mode="HTML",
                                           disable_web_page_preview=True, reply_markup=kb)
                wizard_post_id = m.message_id
            except Exception as e:
                logger.error(f"hot-streak text send FAILED @{channel_key}: {e}")
                return None

        logger.info(f"🔥 Hot Streak Alert posted @{channel_key} — {len(hits)} calls, color={color}")
        if wizard_post_id:
            asyncio.create_task(_hs_forward_and_pin(bot, channel_key, wizard_post_id))
        return wizard_post_id
    except Exception as e:
        logger.error(f"hot-streak post failed @{channel_key}: {e}")
        return None

HOT_STREAK_STALL_MINUTES = float(os.environ.get("HOT_STREAK_STALL_MINUTES", "30") or 30)

async def _hs_check_stalled(bot):
    """Periodic sweep: a streak of >=HOT_STREAK_MIN hits with no next call
    registered yet normally just holds. If the channel goes quiet after the
    last hit, post the streak instead of waiting forever. Claimed under the
    lock before posting (no duplicates)."""
    try:
        claims = []
        async with _hs_lock():
            state = _hs_load()
            now = datetime.utcnow()
            dirty = False
            for ch, entry in list(state.items()):
                if ch == "_global":
                    continue
                seq = entry.get("seq", [])
                hits = []
                i = 0
                while i < len(seq) and seq[i].get("status") == "hit":
                    hits.append(seq[i])
                    i += 1
                if len(hits) < HOT_STREAK_MIN or i < len(seq):
                    continue
                try:
                    last_hit_at = datetime.fromisoformat(hits[-1].get("hit_at") or hits[-1].get("registered", ""))
                    stale_min = (now - last_hit_at).total_seconds() / 60.0
                except Exception:
                    continue
                if stale_min >= HOT_STREAK_STALL_MINUTES:
                    logger.info(f"🔥 hot-streak stall timeout @{ch}: {len(hits)} hits, "
                                f"{stale_min:.0f}min since last — posting now")
                    entry["seq"] = seq[i:]
                    _hs_settle(state, hits)
                    dirty = True
                    claims.append((ch, hits))
            if dirty:
                _hs_save(state)
        for ch, hits in claims:
            res = await _hs_post_alert(bot, ch, hits)
            if res is None:
                await _hs_restore_claimed(ch, hits)
    except Exception as e:
        logger.warning(f"hot-streak stall sweep failed: {e}")

async def hot_streak_resolve_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await _hs_resolve_pending(context.bot)
        await _hs_check_stalled(context.bot)
    except Exception as e:
        logger.error(f"hot_streak_resolve_job crash: {e}")

@owner_only
async def cmd_testalerths(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testalertHSA [color] — post ONE sample Hot Streak Alert using fake
    data + a fake @TestChannel handle, purely to let the owner eyeball the
    formatting. Never touches real streak state, never uses a real KOL.
    OWNER FIX (2026-09-23): this used to loop all 4 colors (4 separate posts)
    and hardcode the real "bigbangcall" channel, showing a real KOL's name/
    link instead of a test placeholder."""
    sample_chains = ["BNB", "BNB", "ETH", "BNB", "ARC", "SOL"]
    sample_x = [2.1, 4.8, 2.4, 6.0, 3.2, 5.5]
    hits = [{"call_key": f"testkey{i}", "chain": c, "peak_x": x}
            for i, (c, x) in enumerate(zip(sample_chains, sample_x))]
    color = (context.args[0].lower() if context.args and context.args[0].lower() in HOT_STREAK_COLORS
             else _hs_next_color(_hs_load()))
    await update.message.reply_text(f"🔥 Sending 1 test Hot Streak Alert ({color})...")
    await _hs_post_alert(context.bot, "testchannel", hits, forced_color=color)
    await update.message.reply_text("✅ Test alert sent — check @WizardScan.")

@owner_only
async def cmd_hschains(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hschains [color] — diagnostic: one message per color listing every chain
    NAME next to the emoji the bot maps to it, so a wrong ID is visible at a
    glance (the name is plain text, the icon is the custom emoji)."""
    colors = ([context.args[0].lower()] if context.args and context.args[0].lower() in HOT_STREAK_COLORS
              else list(HOT_STREAK_COLORS))
    for color in colors:
        pack = HOT_STREAK_PACKS[color]
        lines = [f"<b>{color.upper()}</b>"]
        lines.append(f'CALL emoji: <tg-emoji emoji-id="{pack["emoji"]}">🔥</tg-emoji>')
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"{color}: send failed: {e}")

@owner_only
async def cmd_hscheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hscheck — har Hot Streak emoji ID ka ASLI sticker-set naam + emoji dikhata hai
    (Telegram se seedha), taake galat pack/chain wali ID foran pakdi jaye."""
    meta = []
    for color in HOT_STREAK_COLORS:
        pack = HOT_STREAK_PACKS[color]
        for i, cid in enumerate(pack["checks"]):
            meta.append((color, f"banner{i + 1}", cid))
        meta.append((color, "call_emoji", pack["emoji"]))
    try:
        stk = await context.bot.get_custom_emoji_stickers([str(m[2]) for m in meta])
    except Exception as e:
        await update.message.reply_text(f"get_custom_emoji_stickers failed: {e}"); return
    by_id = {str(getattr(s, "custom_emoji_id", "")): s for s in stk}
    for color in HOT_STREAK_COLORS:
        lines = [f"{color.upper()}"]
        for c, name, cid in meta:
            if c != color:
                continue
            s = by_id.get(str(cid))
            if not s:
                lines.append(f"{name}: ❌ ID NOT FOUND ({cid})")
            else:
                lines.append(f"{name}: set={getattr(s, 'set_name', None)} | {getattr(s, 'emoji', '')} | {cid}")
        await update.message.reply_text("\n".join(lines))

@owner_only
async def cmd_sethsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in HOT_STREAK_COLORS:
        await update.message.reply_text(
            "Usage: /sethsmedia <red|blue|green|golden>\n"
            "(phir photo/video bhej do — ya isi message pe reply karke)"); return
    color = context.args[0].lower()
    owner_edit_state[update.effective_user.id] = {"state": ST_SET_HS_MEDIA, "color": color}
    await update.message.reply_text(f"📸 Send photo or video for the {color.upper()} Hot Streak Alert:")

@owner_only
async def cmd_clearhsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in HOT_STREAK_COLORS:
        await update.message.reply_text("Usage: /clearhsmedia <red|blue|green|golden>"); return
    color = context.args[0].lower()
    c = load_config(); media = c.get("hot_streak_media", {})
    if color in media:
        del media[color]; c["hot_streak_media"] = media; save_config(c)
        await update.message.reply_text(f"✅ {color.upper()} Hot Streak media removed.")
    else:
        await update.message.reply_text(f"No media set for {color.upper()}.")

@owner_only
async def cmd_listhsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media = cfg_get("hot_streak_media", {})
    if not media:
        await update.message.reply_text("No Hot Streak media set. Use /sethsmedia <color>"); return
    lines = [f"✅ <b>{k.upper()}</b> — {v.get('type', 'photo')}" for k, v in media.items()]
    await update.message.reply_text("🔥 <b>Hot Streak Media:</b>\n\n" + "\n".join(lines), parse_mode="HTML")



# ─── True ATH / peak tracking (GeckoTerminal OHLCV) ───────────────────────────
# Real-time peak: monitoring_job stores the highest ratio it ever sees.
# Backfill: GeckoTerminal candle highs recover peaks that happened while the
# bot was restarting/offline — this is how the displayed X becomes a REAL ATH
# multiplier (like Kolscope / SpyDefi) instead of the current live price.
_GT_NET = {"SOL": "solana", "ETH": "eth", "BNB": "bsc", "BASE": "base",
           "TON": "ton", "RH": "robinhood", "ARB": "arbitrum", "ARC": "arc"}
_ath_pool_cache: dict = {}

def _gt_get(url, timeout=12):
    try:
        r = _dex_session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"gecko get failed {url}: {e}")
    return None

def _gt_best_pool(ca, chain, dex_pair_addr=None):
    """(network, pool_address) of the CORRECT pool for this token, or None.

    FIX (real ATH was 70K, bot alerted 24M/100M/200M): this used to pick
    whichever GeckoTerminal pool showed the highest reserve_in_usd, with NO
    check that it's the same pool the token actually trades/was called on,
    and cached that choice FOREVER (no TTL). GeckoTerminal sometimes indexes
    a decoy/fake-liquidity pool with an inflated reserve_in_usd — once that
    wrong pool got cached, every future ATH-backfill tick fed its corrupted
    OHLCV candle highs into peak_mc, and because the plausibility guard
    compares against our own previously-accepted (already wrong) ratio, each
    bad reading raised the ceiling for the next one — a runaway feedback
    loop (70K real -> 24M -> 100M -> 200M).

    Permanent fix: only trust a GeckoTerminal pool whose address MATCHES
    DexScreener's own top-liquidity pair for this token (dex_pair_addr,
    ground truth the bot already relies on for live MC). No match -> skip
    candle-based ATH for this tick instead of guessing. Cache now expires
    (6h) so a stale/wrong lock can't persist indefinitely.
    """
    net = _GT_NET.get((chain or "SOL").upper())
    if not net or not ca:
        return None
    key = f"{net}:{ca}"
    cached = _ath_pool_cache.get(key)
    if cached and (time.time() - cached[2]) < 21600:  # 6h TTL
        return (cached[0], cached[1])
    d = _gt_get(f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{ca}/pools?page=1")
    want = (dex_pair_addr or "").strip().lower()
    best, best_liq, matched = None, -1.0, None
    for p in ((d or {}).get("data") or []):
        a = p.get("attributes", {}) or {}
        addr = a.get("address") or ""
        try:
            liq = float(a.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if want and addr.lower() == want:
            matched = addr
        if liq > best_liq and addr:
            best_liq, best = liq, addr
    # If we have DexScreener's ground-truth pair address, ONLY that pool is
    # trustworthy — an unmatched "highest liquidity" pool is exactly the
    # decoy-pool failure mode above, so refuse rather than guess.
    #
    # FIX (AIFORCE/@Bigbangcall incident, 2026-09-18): when `want` itself was
    # empty (DexScreener's dex_pair_addr not available at computation time —
    # e.g. a transient fetch miss), this used to fall back to `best`
    # (GeckoTerminal's own highest-liquidity pool, no cross-check at all) —
    # silently re-opening the exact decoy-pool hole the fix above was meant to
    # close. Real caller posted 26.3K, bot alerted a 950X/26.16M "peak" off a
    # bad candle. Now: no DexScreener ground truth to check against -> skip
    # candle-based ATH for this tick entirely, same as the "no match" case.
    chosen = matched if want else None
    if chosen:
        _ath_pool_cache[key] = (net, chosen, time.time())
        return (net, chosen)
    return None

def fetch_entry_and_peak_sync(ca, chain, since_dt):
    """(entry_price_at_call_time, highest_price_since) from real candles.

    entry_price = OPEN of the candle that contains the call timestamp.
    Both are 0.0 when unknown."""
    dex_pair_addr = ""
    try:
        _d = _fetch_dex_sync(ca, retries=1, min_liquidity=0)
        dex_pair_addr = (_d or {}).get("pair_addr", "") or ""
    except Exception:
        dex_pair_addr = ""
    pool = _gt_best_pool(ca, chain, dex_pair_addr)
    if not pool:
        return (0.0, 0.0)
    net, addr = pool
    age_h = max((datetime.utcnow() - since_dt).total_seconds() / 3600.0, 0.05)
    if   age_h <= 16:  tf, agg, span = "minute", 1,  60
    elif age_h <= 240: tf, agg, span = "minute", 15, 900
    elif age_h <= 960: tf, agg, span = "hour",   1,  3600
    else:              tf, agg, span = "day",    1,  86400
    d = _gt_get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{addr}"
                f"/ohlcv/{tf}?aggregate={agg}&limit=1000&currency=usd")
    lst = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    import calendar
    since_ts = calendar.timegm(since_dt.utctimetuple())
    peak = 0.0
    entry_price = 0.0
    best_delta = None
    for c in lst:
        try:
            ts, op, hi = int(c[0]), float(c[1]), float(c[2])
        except (TypeError, ValueError, IndexError):
            continue
        # candle containing / closest to the call moment → entry reference
        if ts <= since_ts < ts + span:
            entry_price = op
            best_delta = 0
        elif best_delta != 0:
            delta = abs(ts - since_ts)
            if op > 0 and (best_delta is None or delta < best_delta):
                best_delta, entry_price = delta, op
        if ts + span <= since_ts:     # candle fully before the call
            continue
        if ts < since_ts:
            # FIX (old-peak free X): this candle STRADDLES the call moment, so its
            # HIGH can belong to the time BEFORE the call (token was 200-400K, dumped
            # to 50K, caller posted at 50K). Only its CLOSE is post-call price.
            try:
                _cl = float(c[4])
            except (TypeError, ValueError, IndexError):
                _cl = 0.0
            if _cl > peak:
                peak = _cl
            continue
        if hi > peak:
            peak = hi
    return (entry_price, peak)

def fetch_peak_price_since_sync(ca, chain, since_dt):
    """Highest traded USD price since `since_dt`. Returns 0.0 when unknown."""
    return fetch_entry_and_peak_sync(ca, chain, since_dt)[1]


async def compute_true_x(call, dex=None, since=None):
    """Asli (ATH-based) X multiplier nikaalo — entry MC ko base bana kar.

    Returns (best_ratio, peak_mc). Live MC ratio + GeckoTerminal candle ATH,
    dono me se jo zyada ho wohi asli X hai. Purana stored peak kabhi kam nahi hota.
    """
    ca    = call.get("ca") or ""
    chain = call.get("chain", "SOL") or "SOL"
    try:    entry_mc = float(call.get("entry_mc") or 0)
    except (TypeError, ValueError): entry_mc = 0.0
    try:    entry_price = float(call.get("entry_price") or 0)
    except (TypeError, ValueError): entry_price = 0.0

    if dex is None:
        try:
            _invalidate_dex_cache(ca)
            dex = await asyncio.wait_for(fetch_dexscreener(ca), timeout=20)
        except Exception:
            dex = None
    try:    cur_mc = float((dex or {}).get("mcap") or 0)
    except (TypeError, ValueError): cur_mc = 0.0
    try:    cur_price = float((dex or {}).get("price") or 0)
    except (TypeError, ValueError): cur_price = 0.0

    best, peak_mc = 0.0, 0.0
    live_ratio = _verified_live_ratio(call, dex)
    if live_ratio > 0:
        best = live_ratio
        peak_mc = cur_mc if cur_mc > 0 else (entry_mc * live_ratio if entry_mc > 0 else 0)

    # Reference price jo entry MC ke barabar hai (candle ATH se compare karne ke liye)
    ref_price = entry_price
    if ref_price <= 0 and cur_price > 0 and cur_mc > 0 and entry_mc > 0:
        ref_price = cur_price * (entry_mc / cur_mc)

    if since is None:
        try:
            since = datetime.fromisoformat(call.get("tracked_since") or "")
        except Exception:
            since = datetime.utcnow() - timedelta(hours=6)
    # KILL SWITCH (2026-09-18, repeated GeckoTerminal decoy-pool wrong-MC
    # reports — AIFORCE/@Bigbangcall etc): GeckoTerminal's candle-ATH backfill
    # is the recurring source of these incidents. Owner asked to pull it out
    # of rotation entirely for now and let DexScreener + Birdeye (already the
    # live-tracking sources) carry peak detection alone via the live-polled
    # ratio above. Set GECKOTERMINAL_ATH_ENABLED=true to turn it back on.
    if not (os.environ.get("GECKOTERMINAL_ATH_ENABLED", "false").lower() in ("1", "true", "yes")):
        prev = call_peak_ratio(call)
        if prev > best:
            best = prev
            try:    peak_mc = float(call.get("peak_mc") or peak_mc)
            except (TypeError, ValueError): pass
        if best > 0 and peak_mc <= 0 and entry_mc > 0:
            peak_mc = entry_mc * best
        return (best, peak_mc)
    try:
        _ep, peak_price = await asyncio.wait_for(
            asyncio.to_thread(fetch_entry_and_peak_sync, ca, chain, since), timeout=30)
    except Exception:
        peak_price = 0.0
    # Candle ATH is accepted only when it agrees with the current token's MC
    # scale. This blocks a migrated/wrong pool candle from creating 10,000X.
    if peak_price > 0 and ref_price > 0:
        r = peak_price / ref_price
        candle_plausible = (best <= 0 or r <= max(best * 25, 50)) and r <= 1000
        if candle_plausible and r > best:
            best = r
            peak_mc = entry_mc * r if entry_mc > 0 else peak_mc

    prev = call_peak_ratio(call)
    if prev > best:
        best = prev
        try:    peak_mc = float(call.get("peak_mc") or peak_mc)
        except (TypeError, ValueError): pass
    if best > 0 and peak_mc <= 0 and entry_mc > 0:
        peak_mc = entry_mc * best
    return (best, peak_mc)


def milestones_for_ratio(ratio):
    """Jo milestones is ratio pe genuinely hit hue hain."""
    try: ratio = float(ratio or 0)
    except (TypeError, ValueError): return []
    return [m for m in get_milestones() if m <= MAX_MILESTONE and ratio >= m]


def call_peak_ratio(call):
    """Best known ATH multiplier for a tracked call (never below live ratio)."""
    try:
        pr = float(call.get("peak_ratio", 0) or 0)
    except (TypeError, ValueError):
        pr = 0.0
    try:
        lr = float(call.get("last_ratio", 0) or 0)
    except (TypeError, ValueError):
        lr = 0.0
    return max(pr, lr)

def _update_peak(call, ratio, cur_mc=0.0):
    """Store a new all-time-high ratio/MC on the call. Returns True if raised."""
    try:
        ratio = float(ratio or 0)
    except (TypeError, ValueError):
        return False
    if ratio <= 0:
        return False
    prev = 0.0
    try:
        prev = float(call.get("peak_ratio", 0) or 0)
    except (TypeError, ValueError):
        prev = 0.0
    if ratio <= prev + 1e-9:
        return False
    entry_mc = 0.0
    try:
        entry_mc = float(call.get("entry_mc", 0) or 0)
    except (TypeError, ValueError):
        entry_mc = 0.0
    peak_mc = cur_mc if cur_mc and cur_mc > 0 else (entry_mc * ratio if entry_mc > 0 else 0.0)
    call["peak_ratio"] = round(ratio, 4)
    if peak_mc > 0:
        call["peak_mc"] = peak_mc
        call["peak_mc_fmt"] = fmt_mc(peak_mc)
    call["peak_at"] = datetime.utcnow().isoformat()
    return True

def _verified_live_ratio_inner(call, dex):
    """Return a market-cap-first live ratio, or 0 when the sample is unsafe.

    Price ratios are not interchangeable across pool migrations, decimal/supply
    changes or bad pair responses. Market cap is therefore authoritative. A
    price ratio may only be used when MC is unavailable, and conflicting price
    data can never inflate a valid MC ratio — UNLESS the MC field itself looks
    stale/frozen (see below), in which case it's not really "data" anymore.
    """
    try:
        entry_mc = float(call.get("entry_mc", 0) or 0)
        entry_price = float(call.get("entry_price", 0) or 0)
        cur_mc = float((dex or {}).get("mcap", 0) or 0)
        cur_price = float((dex or {}).get("price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0

    # OWNER FIX (2026-09-19, PDOG + repeat incidents): GeckoTerminal /
    # DexPaprika / Birdeye fall back to FDV (fully-diluted valuation) when a
    # token has no real circulating-supply market_cap field. FDV counts
    # locked/team/treasury supply too, so it can read 3-10x+ higher than the
    # actual circulating MC shown on-chart — a single FDV-tagged tick was
    # enough to fire fake 4X/30X/40X alerts on tokens that hadn't moved.
    # This was previously only guarded at entry-MC time (_reconcile_entry_mc,
    # live_is_fdv=...); ongoing live-ratio checks had no such guard. Since
    # every X-alert/milestone/record path ultimately calls this one function
    # for its ratio, checking `_mc_is_fdv` here protects all of them at once
    # — an FDV-derived MC is treated as "no MC data this tick" (falls back to
    # price-ratio, or 0.0 / no alert) instead of ever being trusted directly.
    if (dex or {}).get("_mc_is_fdv"):
        cur_mc = 0.0

    mc_ratio = cur_mc / entry_mc if entry_mc > 0 and cur_mc > 0 else 0.0
    price_ratio = cur_price / entry_price if entry_price > 0 and cur_price > 0 else 0.0
    if mc_ratio > 0:
        if price_ratio > 0:
            # FIX (rug-pull supply-manipulation bug, SCMP/@robinhoodcallsbot
            # incident, 2026-09-20): price_ratio and mc_ratio come from the
            # SAME DexScreener response but can be computed from different
            # fields (raw price vs the reported marketCap/totalSupply). A
            # legitimate supply change (burn/mint) can only make mc_ratio
            # diverge from price_ratio in magnitude — it can NEVER flip which
            # DIRECTION the token moved, because MC is mathematically
            # price × supply: if price is really down, MC cannot be really up
            # unless supply itself exploded, which is exactly what a rug
            # contract does to its own reported supply. Here price_ratio was
            # 0.61x (correctly showing the token had crashed — real MC ~2K
            # against an entry of 4.8K) while mc_ratio claimed 39.73x — the
            # old code trusted mc_ratio purely because it wasn't "frozen at
            # exactly 1.00x", and fired 16X through 35X fake milestone alerts
            # on a dead token. A direction conflict (one side up, one side
            # down) is a much stronger and simpler red flag than the narrow
            # frozen-at-1.0 case below, and price is far harder to fake than a
            # reported supply/MC field, so it wins outright whenever they
            # disagree on direction — no divergence threshold needed.
            if (price_ratio < 0.97 and mc_ratio > 1.03) or (price_ratio > 1.03 and mc_ratio < 0.97):
                logger.warning(
                    f"🚫 Direction conflict: price ratio {price_ratio:.2f}x vs "
                    f"DexScreener MC ratio {mc_ratio:.2f}x for "
                    f"{call.get('ca','')[:12]}… — MC/supply field is almost "
                    f"certainly manipulated or stale (likely a rug); trusting "
                    f"price, which cannot be faked the same way.")
                return price_ratio
            divergence = max(mc_ratio, price_ratio) / max(min(mc_ratio, price_ratio), 1e-12)
            if divergence > 3:
                # OWNER FIX (2026-09-14): DexScreener's cached `marketCap`
                # field can freeze for hours at a time while `priceUsd` keeps
                # updating normally — a stale field on their side, not the
                # token actually standing still. This is only reliably
                # detectable in one specific case: MC frozen almost exactly
                # AT the entry value (mc_ratio ~1.00x) for a call that has
                # clearly moved — a real, actively-traded token essentially
                # never sits at exactly 1.000x for many ticks in a row, so
                # that coincidence is a strong, narrow signal the field
                # itself is stuck (proven correct for the DOGGO/minibangcall
                # incident: entry pinned every X at 1x forever).
                #
                # REVERTED (2026-09-14, same day, IDX/casinoinsiderhere
                # incident): a broader "MC unchanged for N ticks at ANY
                # level → treat as stale" version briefly replaced this and
                # made things worse — it fired on this token's mc_ratio
                # sitting normally at ~1.33-1.41x (a real, legitimately flat
                # stretch, not stale data) while price_ratio bounced around
                # 0.28-0.30x from ordinary quote noise, and confidently
                # trusted the WRONG noisy price over the CORRECT flat MC.
                # A token's market cap simply not moving much for a while is
                # completely normal and is not by itself evidence of a stuck
                # field — only the exact-1.00x coincidence is. Narrowed back.
                mc_frozen_at_entry = abs(mc_ratio - 1.0) < 0.03
                if mc_frozen_at_entry:
                    # BUG FIX (2026-09-14, live incident — channel called a ~1T MC
                    # token, entry got captured in the "millions" instead, then the
                    # bot spammed 2X/3X/4X/... alerts back to back): trusting price
                    # unconditionally whenever MC looks frozen was too permissive.
                    # A real pump ratchets up gradually tick by tick; a jump straight
                    # from ~1x to thousands-of-x in one sample (5575x was observed
                    # live) is virtually always bad data — wrong decimals, a
                    # rebased/forked pair, or a bad quote — not a real move. Trusting
                    # it meant every milestone from 2X up to 1000X+ fired at once in
                    # a single tick: an alert-spam burst, not thousands of real X.
                    # Cap it: an implausible jump is rejected and held at the call's
                    # last confirmed peak instead of being allowed through.
                    _prev_peak = 0.0
                    try:
                        _prev_peak = float(call.get("peak_ratio", 0) or 0)
                    except (TypeError, ValueError):
                        _prev_peak = 0.0
                    _implausible = price_ratio > 1000 or (
                        _prev_peak > 50 and price_ratio > _prev_peak * 50)
                    if _implausible:
                        logger.warning(
                            f"Rejecting implausible price ratio {price_ratio:.2f}x "
                            f"(prev peak {_prev_peak:.2f}x, MC ratio {mc_ratio:.2f}x) "
                            f"for {call.get('ca','')[:12]}… — likely bad decimals/pair "
                            f"data, not a real move; holding at last-known ratio")
                        return _prev_peak if _prev_peak > 0 else mc_ratio
                    logger.warning(
                        f"MC ratio frozen at entry ({mc_ratio:.2f}x) but price ratio is "
                        f"{price_ratio:.2f}x for {call.get('ca','')[:12]}… — trusting price instead")
                    return price_ratio
                # FIX (2026-09-30, BABYBUN/@spawnscalls fake 30X): MC = price x
                # supply, so an MC ratio that claims a big pump (>=2x) while
                # PRICE says it moved >3x less is the supply/MC field being
                # wrong or stale, not a real 26x. A pump claim must be backed by
                # price too — take the lower of the two. (Ratios < 2x are left
                # alone so the IDX/DOGGO fixes above keep behaving as before.)
                if MC_PRICE_PUMP_GUARD and mc_ratio >= 2 and mc_ratio > price_ratio:
                    logger.warning(
                        f"🛡️ MC-vs-price pump guard: MC ratio {mc_ratio:.2f}x but price "
                        f"ratio only {price_ratio:.2f}x for {call.get('ca','')[:12]}… "
                        f"— using price ratio")
                    return price_ratio
                logger.warning(
                    f"Ignoring conflicting price ratio {price_ratio:.2f}x; "
                    f"DexScreener MC ratio is {mc_ratio:.2f}x for {call.get('ca','')[:12]}…")
        return mc_ratio
    # Last-resort price tracking is deliberately conservative. Once a real MC
    # appears, all future ticks automatically switch to MC-first tracking.
    if 0 < price_ratio <= 1000:
        return price_ratio
    return 0.0

def _verified_live_ratio(call, dex):
    """Outer safety wrapper around _verified_live_ratio_inner().

    Every specific bad-data pattern found so far (frozen-at-1.0x MC field,
    price/MC direction conflicts, FDV-tagged MC, implausible one-tick jumps
    while frozen) has its own targeted fix inside the inner function. Each
    one only catches the exact pattern of the incident that revealed it —
    which is why this exact class of bug (a rugged/dead token being handed a
    wildly wrong MC from SOME source, in SOME shape nobody has hit yet) kept
    recurring under new disguises. This wrapper is the backstop: regardless
    of which internal path produced a number, or whether it's a shape this
    file has ever seen before, a jump this large in a single tick relative to
    the call's own last CONFIRMED peak is rejected outright and the peak is
    held instead — a real pump does not advance 20x+ beyond its own prior
    high between two ~2-second monitoring ticks. This does not replace the
    inner checks (they still produce a better-reasoned number/log message for
    the common cases); it only adds an absolute ceiling nothing can slip
    past, including patterns this file doesn't have a name for yet.
    """
    ratio = _verified_live_ratio_inner(call, dex)
    if ratio <= 0:
        return ratio
    # FIX (2026-10-18, $DACITY fake 2X..13X burst after a redeploy): a quote that came from an
    # ALTERNATE source (GeckoTerminal/Birdeye/...) -- e.g. while DexScreener is rate-limited or
    # right after a restart -- can be frozen/stale (Gecko kept returning 44.4K all day while
    # DexScreener showed 3.3K). The old code let such a quote produce ANY ratio up to 20x/1000x
    # when the call had no established peak yet. An alt-source quote may now never imply more
    # than the same ceiling the "unstick" probe uses (max(8x, +5x) over the last peak);
    # DexScreener's own quotes are unaffected.
    _qsrc = str((dex or {}).get("_source") or "").lower()
    if _qsrc and _qsrc != "dexscreener" and ALT_SOURCE_STRICT:
        # FIX (2026-10-21, "200k peak -> 40k par call -> free X"): token pehle 200k tak gaya, 40k par aa gaya,
        # caller ne 40k par call ki. Flat (~1.0x) tokens par alt-source probe chalta hai; Gecko/Birdeye
        # ka purana 200k (stale peak) 5x bana kar nikalta tha aur purana 8x ceiling usay pass kar deta tha.
        # Ab alt source akele X nahi bana sakta: ratio <= max(ALT_FREE_RATIO, DexScreener ratio * ALT_MAX_LEAD).
        # DexScreener ka apna reading na ho (ya purana ho) to sirf ALT_FREE_RATIO (1.9x, 2X se neeche) tak.
        try:
            _dx_r = float(call.get("_dx_ratio", 0) or 0)
            _dx_t = float(call.get("_dx_at", 0) or 0)
        except (TypeError, ValueError):
            _dx_r, _dx_t = 0.0, 0.0
        _fresh_dx = _dx_r > 0 and (time.time() - _dx_t) <= ALT_DX_MAX_AGE_S
        _alt_ceiling = max(ALT_FREE_RATIO, _dx_r * ALT_MAX_LEAD) if _fresh_dx else ALT_FREE_RATIO
        if ratio > _alt_ceiling:
            logger.warning(
                f"🚫 Alt-source ratio {ratio:.2f}x ({_qsrc}) rejected for {call.get('ca','')[:12]}… — "
                f"DexScreener-confirmed ratio {_dx_r:.2f}x (alt ceiling {_alt_ceiling:.2f}x). "
                f"Alt source akele X nahi bana sakta (stale-peak guard).")
            return min(call_peak_ratio(call), _alt_ceiling) if call_peak_ratio(call) > 0 else 0.0
    if _qsrc and _qsrc != "dexscreener":
        _base = max(call_peak_ratio(call), 1.0)
        if ratio > max(_base * 8, _base + 5):
            logger.warning(
                f"🚫 Rejecting alt-source ratio {ratio:.2f}x ({_qsrc}) for "
                f"{call.get('ca','')[:12]}… — too far above last peak {_base:.2f}x for an "
                f"unverified source; waiting for DexScreener to confirm.")
            return call_peak_ratio(call)
    prev_peak = call_peak_ratio(call)
    if prev_peak <= 0:
        # First-ever real reading for this call — no established baseline to
        # compare against yet, so only guard against obviously-broken data
        # (wrong decimals etc.), same ceiling already used for that case.
        if ratio > 1000:
            logger.warning(
                f"🚫 Rejecting implausible first-ever ratio {ratio:.2f}x for "
                f"{call.get('ca','')[:12]}… (no established peak yet) — "
                f"holding at 0 (no alert this tick).")
            return 0.0
        return ratio
    if ratio > max(prev_peak * 20, 50):
        logger.warning(
            f"🚫 Rejecting implausible jump {prev_peak:.2f}x → {ratio:.2f}x "
            f"for {call.get('ca','')[:12]}… (>20x prior peak in one tick) — "
            f"holding at last confirmed peak {prev_peak:.2f}x instead.")
        return prev_peak
    return ratio

def _complete_history_call(call, highest_x, current_x, current_mc):
    """Return True once a tracked call has enough real data for history.

    A new call is a valid record from the moment its token details are resolved;
    it must not be hidden merely because it is still near its 1.0x entry.  The
    old performance gate made correctly tracked fresh calls disappear from the
    username lookup until they moved more than 1%, which looked like data loss.
    """
    try:
        entry_mc = float(call.get("entry_mc", 0) or 0)
    except (TypeError, ValueError):
        entry_mc = 0.0
    # OWNER FIX (2026-09-14): /addmissedcall always stores msg_id=0 (there is
    # no real Telegram message for a manually-added call — see cmd_addmissedcall).
    # `call.get("msg_id")` truthy-checks that value, and bool(0) is False, so
    # EVERY manually-added call — and every /fixmc-corrected call that started
    # life as a manually-added one — was silently dropped from /history and
    # channel records forever, no matter how correct entry_mc/symbol/chain
    # were. msg_id only needs to exist on the dict; 0 is a valid value for a
    # manual entry, so check presence (`is not None`), not truthiness.
    return bool(
        call.get("ca") and call.get("msg_id") is not None and call.get("symbol")
        and call.get("chain") and entry_mc > 0 and current_mc > 0
        and call.get("entry_fmt") not in ("", "N/A")
        and not call.get("dex_pending")
    )

def _remove_impossible_milestones(call_key, call):
    """Purge legacy phantom milestones whose implied MC exceeds the hard cap."""
    try:
        entry_mc = float(call.get("entry_mc", 0) or 0)
    except (TypeError, ValueError):
        entry_mc = 0.0
    if entry_mc <= 0:
        return False
    levels = sent_milestones.get(call_key, set())
    valid = {m for m in levels if m <= MAX_MILESTONE and entry_mc * m <= MAX_PLAUSIBLE_MC}
    if valid == levels:
        return False
    removed = sorted(set(levels) - valid)
    sent_milestones[call_key] = valid
    posts = milestone_posts.get(call_key, {})
    for level in removed:
        posts.pop(str(level), None)
    call["peak_ratio"] = max(valid) if valid else min(call_peak_ratio(call), 1.0)
    call["peak_mc"] = entry_mc * call["peak_ratio"]
    call["peak_mc_fmt"] = fmt_mc(call["peak_mc"])
    logger.warning(f"Purged impossible milestones {removed} from {call_key}")
    return True


# ─── Rugged-call cleanup (prevents tracked_calls from growing forever) ───────
# Without this, tracked_calls only ever grows. monitoring_job checks EVERY
# tracked call each tick (via a rotating cursor for the non-urgent ones), so
# thousands of dead/rugged tokens sitting in the file forever means fewer real
# ticks reach each live call → "skipped: maximum number of running instances
# reached" + X alerts stuck in "pending" for a long time. Once a token is
# confirmed rugged (_rug_status sets rugged=True/rugged_at) it can never post
# another alert anyway, so it's safe to drop after it's sat there a while.
TRACKED_CALL_MAX_AGE_DAYS = float(os.environ.get("TRACKED_CALL_MAX_AGE_DAYS", "14") or 14)
# Owner requirement: a rugged token must stop consuming monitoring_job cycles
# almost immediately (not sit around for up to 14 days) — the call record
# itself stays forever via the permanent archive below, only the *active*
# polling load is dropped fast. Kept separate from TRACKED_CALL_MAX_AGE_DAYS
# (hours vs days) so both knobs can be tuned independently via env vars.
RUGGED_CALL_MAX_AGE_HOURS = float(os.environ.get("RUGGED_CALL_MAX_AGE_HOURS", "0.1") or 0.1)

def _prune_rugged_calls():
    """Delete tracked calls that are rugged AND older than
    RUGGED_CALL_MAX_AGE_HOURS. Cleans up every companion dict too so nothing
    stale is left behind (milestones, milestone posts/times, pending media)."""
    if not tracked_calls:
        return 0
    cutoff = datetime.utcnow() - timedelta(hours=RUGGED_CALL_MAX_AGE_HOURS)
    doomed = []
    for call_key, call in tracked_calls.items():
        if not call.get("rugged"):
            continue
        ts_str = call.get("rugged_at") or call.get("tracked_since") or ""
        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            # no usable timestamp on an already-rugged call — treat as old/junk
            doomed.append(call_key)
            continue
        if ts <= cutoff:
            doomed.append(call_key)

    if not doomed:
        return 0

    # ── Archive before delete ──────────────────────────────────────────────
    # OWNER REQUIREMENT: /history must never lose a channel's old calls, even
    # after the underlying tracked_calls entry is cleaned up. Active-tracking
    # data (price polling, dex_pending, etc.) is fine to delete for a rugged
    # token — there's nothing left to track — but the historical record must
    # keep showing it. So the essential display fields get saved into a
    # permanent archive first; get_call_history() below reads both.
    try:
        archive = load_call_archive()
        for call_key in doomed:
            call = tracked_calls.get(call_key) or {}
            archive[call_key] = {
                "channel":      call.get("channel", ""),
                "msg_id":       call.get("msg_id", 0),
                "ca":           call.get("ca", ""),
                "chain":        call.get("chain", "SOL"),
                "symbol":       call.get("symbol", ""),
                "entry_mc":     call.get("entry_mc", 0),
                "entry_fmt":    call.get("entry_fmt", "N/A"),
                "tracked_since": call.get("tracked_since", ""),
                "rugged":       True,
                "rugged_at":    call.get("rugged_at", ""),
                "best_x":       max(sent_milestones.get(call_key, {0}) or {0}),
                "post_deleted": bool(call.get("post_deleted")),
            }
        save_call_archive(archive)
    except Exception as e:
        # FIX (data-loss risk): this used to log a warning and then still fall
        # through to the delete loop below — meaning a failed archive write
        # (disk hiccup, bad JSON, anything) resulted in the call being
        # PERMANENTLY LOST with no backup at all. That directly violates the
        # owner requirement above ("/history must never lose a channel's old
        # calls"). Now a failed archive aborts the whole prune for this batch
        # — the calls stay in tracked_calls (still visible, still counted) —
        # rather than risk silently deleting un-backed-up history.
        logger.error(f"call archive save failed — ABORTING this prune batch, "
                     f"{len(doomed)} calls left untouched (nothing deleted): {e}")
        return 0

    for call_key in doomed:
        tracked_calls.pop(call_key, None)
        sent_milestones.pop(call_key, None)
        milestone_posts.pop(call_key, None)
        milestone_times.pop(str(call_key), None)
        pending_media_alerts.pop(call_key, None)

    _save_tracked()
    _save_milestones()
    _save_milestone_posts()
    _save_milestone_times()
    _save_pending_media()
    logger.info(f"🧹 tracked_calls cleanup: removed {len(doomed)} rugged call(s) "
                f"older than {RUGGED_CALL_MAX_AGE_HOURS:.1f}h — {len(tracked_calls)} remaining")
    return len(doomed)

async def tracked_calls_cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        _prune_rugged_calls()
    except Exception as e:
        logger.error(f"tracked_calls_cleanup_job crash: {e}")


# ─── Milestone job (slower — dex checks + alerts) ────────────────────────────
_ath_cursor = 0

async def pending_media_job(context):
    """Post milestones that were held back because their media was missing.
    As soon as the owner uploads media for that X level, the alert goes out
    and the channel/leaderboard/champions/trending start showing it."""
    try:
        if not pending_media_alerts:
            return
        changed = False
        for call_key in list(pending_media_alerts.keys()):
            levels = sorted(pending_media_alerts.get(call_key, set()))
            if not levels:
                pending_media_alerts.pop(call_key, None); changed = True; continue
            call = tracked_calls.get(call_key)
            if not call:
                pending_media_alerts.pop(call_key, None); changed = True; continue
            ready = [x for x in levels if milestone_has_media(x)]
            if not ready:
                continue
            top = max(ready)
            entry_mc = call.get("entry_mc", 0) or 0
            cur_fmt  = fmt_mc(entry_mc * top) if entry_mc > 0 else f"{top}X"
            try:
                posted = await send_alert(
                    context.bot, call.get("channel", ""), call.get("msg_id", 0),
                    top, call.get("chain", "SOL"), call.get("entry_fmt", "N/A"),
                    cur_fmt, call.get("ca", ""), call.get("symbol", ""))
            except Exception as e:
                logger.error(f"pending_media_job send failed: {e}")
                continue
            if posted and posted != "silent":
                rest = {x for x in levels if x not in ready}
                if rest:
                    pending_media_alerts[call_key] = rest
                else:
                    pending_media_alerts.pop(call_key, None)
                changed = True
                logger.info(f"🚀 Held milestone released: {top}X {call_key} (media now set)")
            await asyncio.sleep(0.3)
        if changed:
            _save_pending_media()
    except Exception as e:
        logger.error(f"pending_media_job: {e}")


# Candle backfill exists ONLY to recover peaks the live tracker could not see
# (bot offline / restart / long 429 blackout). While the live tracker is watching a
# fresh call every 1-2s, a candle may not claim more than a small step above the
# live-confirmed peak. (This job used to bypass the ALT_SOURCE_STRICT stale-peak
# guard completely and only checked "<= 50x", so an old 200-400K peak = free 4X-8X.)
ATH_BACKFILL_STRICT_AGE_MIN = float(os.environ.get("ATH_BACKFILL_STRICT_AGE_MIN", "30") or 30)
ATH_BACKFILL_COVER_SECONDS  = float(os.environ.get("ATH_BACKFILL_COVER_SECONDS", "120") or 120)

async def ath_backfill_job(context: ContextTypes.DEFAULT_TYPE):
    """Recover REAL peaks from GeckoTerminal candles.

    Runs in rotating slices so every tracked call gets re-checked regularly.
    This fixes calls that pumped 6-7x while the bot was restarting/offline and
    were previously shown as 1x-3x.
    """
    global _ath_cursor
    try:
        items = [(k, v) for k, v in tracked_calls.items()
                 if not v.get("frozen") and not v.get("rugged")]
        if not items:
            return
        slice_size = 25
        start = _ath_cursor % len(items)
        batch = (items + items)[start:start + slice_size]
        _ath_cursor = (start + slice_size) % len(items)

        changed = False
        for call_key, call in batch:
            ca = call.get("ca")
            entry_price = call.get("entry_price", 0) or 0
            entry_mc    = call.get("entry_mc", 0) or 0
            if not ca or (entry_price <= 0 and entry_mc <= 0):
                continue
            try:
                since = datetime.fromisoformat(call.get("tracked_since"))
            except Exception:
                continue
            try:
                candle_entry, peak_price = await asyncio.to_thread(
                    fetch_entry_and_peak_sync, ca, call.get("chain", "SOL"), since)
            except Exception as e:
                logger.debug(f"ATH backfill failed {ca[:10]}: {e}")
                continue

            # ── Late-entry correction ────────────────────────────────────────
            # If DexScreener only indexed the token minutes after the call, the
            # stored entry price is the ALREADY-PUMPED price → every X looks 1x.
            # Real entry = open of the candle at call time.
            #
            # FIX: pehle ye correction entry ko 10x tak neeche gira deta tha —
            # caller ne 26K MC par post kiya, bot ne 26K track kiya, aur kuch der
            # baad X alert me entry 2.6K dikhne lagti thi. Ab:
            #   • agar caller ke post me MC likha tha (entry_src == "post") → kabhi nahi
            #   • sirf call ke pehle 15 min ke andar
            #   • zyada se zyada 2x neeche (isse zyada = galat pool / purana candle)
            _corr_age_ok = (datetime.utcnow() - since) < timedelta(minutes=15)
            _hint_mc = 0.0
            try:
                _hint_mc = float(call.get("post_mc_hint") or 0)
            except (TypeError, ValueError):
                _hint_mc = 0.0
            if (candle_entry > 0 and entry_price > 0
                    and call.get("entry_src") not in ("post", "candle")
                    and _hint_mc <= 0
                    and _corr_age_ok
                    and entry_price > candle_entry * 1.4
                    and candle_entry >= entry_price * 0.5):
                scale = candle_entry / entry_price
                if entry_mc > 0:
                    entry_mc = entry_mc * scale
                    call["entry_mc"]  = entry_mc
                    call["entry_fmt"] = fmt_mc(entry_mc)
                entry_price = candle_entry
                call["entry_price"] = entry_price
                call["entry_src"]   = "candle"
                changed = True
                logger.info(f"🛠 entry corrected from candles: {call.get('symbol','?')} "
                            f"@{call.get('channel','?')} → {call.get('entry_fmt','?')}")

            if peak_price <= 0 or entry_price <= 0:
                continue
            ratio = peak_price / entry_price
            if ratio <= 0 or ratio > MAX_MILESTONE * 2:
                continue
            # Historical candles are advisory only. A wrong/migrated pool must
            # never jump far beyond the ratio already confirmed by live data.
            live_ref = max(float(call.get("last_ratio", 0) or 0), 1.0)
            if ratio > max(live_ref * 25, 50):
                logger.warning(f"Rejected implausible candle ATH {ratio:.2f}x for {call_key}")
                continue
            # STRICT mode for fresh calls the live tracker has been covering:
            # candle ratio may exceed the live-confirmed peak by at most 1.5x / +0.5X.
            # No "_live_at" yet (brand-new call) counts as covered. A STALE _live_at
            # (bot was offline / blind) keeps the old recovery behaviour.
            try:
                _age_min  = (datetime.utcnow() - since).total_seconds() / 60.0
                _live_at  = float(call.get("_live_at", 0) or 0)
                _covered  = (_live_at <= 0) or ((time.time() - _live_at) <= ATH_BACKFILL_COVER_SECONDS)
            except Exception:
                _age_min, _covered = 0.0, True
            if _age_min <= ATH_BACKFILL_STRICT_AGE_MIN and _covered:
                _kp  = max(call_peak_ratio(call), 1.0)
                _cap = max(_kp * 1.5, _kp + 0.5)
                if ratio > _cap:
                    logger.warning(
                        f"🚫 Candle ATH {ratio:.2f}x rejected for fresh call "
                        f"{call.get('symbol','?')} @{call.get('channel','?')} — live tracker "
                        f"peak is {_kp:.2f}x (cap {_cap:.2f}x). Old/pre-call peak, not a real X.")
                    continue
            peak_mc = entry_mc * ratio if entry_mc > 0 else 0
            if _update_peak(call, ratio, peak_mc):
                changed = True
                logger.info(f"📈 ATH backfill: {call.get('symbol','?')} @{call.get('channel','?')} "
                            f"→ {ratio:.2f}x")
                new_ms = [ms for ms in get_milestones()
                          if ms <= MAX_MILESTONE and ratio >= ms
                          and ms not in sent_milestones[call_key]]
                if new_ms:
                    new_ms.sort()
                    # Announce EVERY newly-reached X, not just the highest one.
                    # FIX: previously only top_ms was posted while ALL of new_ms
                    # got marked as sent — so if a call jumped past 2 unsent
                    # milestones at once (e.g. 2X and 3X together), 2X was
                    # silently marked "done" with no alert ever posted for it.
                    fresh = (datetime.utcnow() - since) < timedelta(days=3)
                    if fresh:
                        ms_fmt_top = fmt_mc(entry_mc * new_ms[-1]) if entry_mc > 0 else "N/A"
                        any_posted = False
                        for top_ms in new_ms:
                            ms_fmt = fmt_mc(entry_mc * top_ms) if entry_mc > 0 else ms_fmt_top
                            try:
                                posted = await send_alert(
                                    context.bot, call["channel"], call["msg_id"],
                                    top_ms, call.get("chain", "SOL"),
                                    call.get("entry_fmt", "N/A"), ms_fmt,
                                    call["ca"], call.get("symbol", ""))
                                if posted:
                                    sent_milestones[call_key].add(top_ms)
                                    any_posted = True
                                    logger.info(f"🚀 (backfill) {call.get('symbol','?')} "
                                                f"@{call['channel']} {top_ms}X!")
                                else:
                                    logger.warning(f"Backfill alert not posted; will retry: {top_ms}X {call_key}")
                            except Exception as e_al:
                                logger.error(f"backfill alert failed: {e_al}")
                            await asyncio.sleep(1.2)
                        if any_posted:
                            _save_milestones()
                    else:
                        for ms in new_ms:
                            sent_milestones[call_key].add(ms)
                        _save_milestones()
            await asyncio.sleep(0.4)   # GeckoTerminal rate-limit friendly
        if changed:
            _save_tracked()
    except Exception as e:
        logger.error(f"ath_backfill_job: {e}")


_MON_RUNNING = False
_PRIO_MON_RUNNING = False
_DEX_BULK_CURSOR = 0
_MON_CHECK_CURSOR = 0
_PRIO_CHECK_CURSOR = 0

# Wall-clock timestamp of the last time the BULK (non-priority) lane actually
# started a tick (i.e. APScheduler invoked monitoring_job AND it got past the
# _MON_RUNNING guard). Used by _bulk_lane_watchdog_job below to detect a wedge
# that the in-body 45s asyncio.wait_for cannot clear — see that watchdog's
# docstring for why this second layer exists.
_MON_LAST_TICK_ENTERED = time.time()

# Same as _MON_LAST_TICK_ENTERED but for the PRIORITY (owner KOL) lane.
# The bulk-lane watchdog originally assumed the priority lane could never
# wedge because it is a separate APScheduler job entry — but a separate job
# entry only means a separate *slot*; a stuck native call inside a to_thread
# worker wedges whichever lane triggered it, priority included. Without this,
# a wedged priority lane had no self-heal at all, silently delaying exactly
# the KOL alerts it exists to keep instant.
_PRIO_MON_LAST_TICK_ENTERED = time.time()

# ═══ MONITOR SCHEDULER v2 (2026-10-21) ═════════════════════════════════════════
# Owner problems: (1) X alert 20 min - 2 ghante late  (2) kabhi galat data / free X.
# Root causes jo code me mile:
#  a) "urgent" (<=10 min ya pending <=1h) calls har tick ke SAARE slots le leti thin.
#     Jab urgent >= MONITOR_MAX_CHECKS_PER_TICK ho jata to purani calls (regular)
#     ko ek bhi slot nahi milta tha -> unki X alert tab tak late jab tak urgent
#     calls 10 min / pending calls 1 ghanta purani na ho jayein.
#  b) frozen calls bhi rotation ke slots kha leti thin (kuch check nahi hota tha).
#  c) liq_glitch / pending tokens har tick dobara fetch hote the -> budget khatam.
# Ab: hot (<=3 min) har tick, warm (3-10 min / pending) har 2nd tick, regular ke liye
# MIN share GUARANTEED, frozen skip, glitch tokens par backoff, per-token fallback backoff.
_SEL_STATE = {"tick": {"bulk": 0, "prio": 0}, "ucur": {"bulk": 0, "prio": 0}, "log_at": {"bulk": 0.0, "prio": 0.0}}
_FB_NEXT_AT: dict = {}          # ca -> next time single-CA fallback allowed
HOT_WINDOW_SECONDS      = int(os.environ.get("HOT_WINDOW_SECONDS", "180") or 180)
WARM_EVERY_N_TICKS      = max(1, int(os.environ.get("WARM_EVERY_N_TICKS", "2") or 2))
REGULAR_MIN_SHARE       = min(0.9, max(0.1, float(os.environ.get("REGULAR_MIN_SHARE", "0.4") or 0.4)))
GLITCH_BACKOFF_BASE_S   = float(os.environ.get("GLITCH_BACKOFF_BASE_SECONDS", "3") or 3)
GLITCH_BACKOFF_MAX_S    = float(os.environ.get("GLITCH_BACKOFF_MAX_SECONDS", "20") or 20)
# Alternate sources (Gecko/Birdeye/Paprika) STALE/purani peak MC de sakte hain (DACITY, RETAIL,
# aur "200k peak -> 40k par call -> free X" incident). Strict mode: alt source akele X nahi bana sakta.
ALT_SOURCE_STRICT       = (os.environ.get("ALT_SOURCE_STRICT", "1") or "1").strip().lower() in ("1", "true", "yes")
ALT_FREE_RATIO          = float(os.environ.get("ALT_FREE_RATIO", "1.9") or 1.9)       # is se upar sirf DexScreener-confirmed
ALT_MAX_LEAD            = float(os.environ.get("ALT_MAX_LEAD", "1.25") or 1.25)       # alt <= DexScreener ratio * 1.25
ALT_DX_MAX_AGE_S        = float(os.environ.get("ALT_DX_MAX_AGE_SECONDS", "900") or 900)


async def monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every second (real-time). Fills dex_pending entry data and fires milestone alerts.

    Market caps come from DexScreener only, refreshed in bulk on every tick.
    A re-entrancy guard stops slow ticks from stacking up (that lag was why some
    X alerts arrived late or not at all).

    When the job is scheduled with data="priority" it becomes a fast lane that
    ONLY checks the owner's special KOL channels, so their calls are refreshed
    every second no matter how many other tokens are being tracked."""
    global _MON_RUNNING, _PRIO_MON_RUNNING, _MON_LAST_TICK_ENTERED, _PRIO_MON_LAST_TICK_ENTERED
    prio_mode = getattr(getattr(context, "job", None), "data", None) == "priority"
    if prio_mode:
        if _PRIO_MON_RUNNING:
            return
        _PRIO_MON_RUNNING = True
        _PRIO_MON_LAST_TICK_ENTERED = time.time()
    else:
        if _MON_RUNNING:
            return
        _MON_RUNNING = True
        _MON_LAST_TICK_ENTERED = time.time()
    try:
        # HARD TIMEOUT: if anything inside this tick ever blocks past 45s
        # (network hang, dead connection, etc.), abort THIS tick instead of
        # wedging the job forever. Without this, one stuck tick meant every
        # future tick was silently skipped ("maximum number of running
        # instances reached") until the process restarted — the exact cause
        # of multi-hour alert delays.
        #
        # NOTE: this timeout only protects code that actually yields control
        # back to the event loop on cancellation. A tick that is truly wedged
        # inside a blocking native call (e.g. a stuck thread-pool worker that
        # a cancelled asyncio.to_thread() task can request but not force to
        # stop) can still leave APScheduler believing the previous invocation
        # of THIS job is still running, which shows up in logs as endless
        # "monitoring_job ... skipped: maximum number of running instances
        # reached (1)" with zero successful ticks — this function's own body
        # never even gets re-entered when that happens, so nothing inside it
        # can self-heal. See _bulk_lane_watchdog_job for the outer safety net
        # that catches exactly this case.
        await asyncio.wait_for(_monitoring_job_body(context, prio_mode), timeout=MONITOR_TICK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("⏱️ monitoring_job hard-timeout hit (%ss, prio=%s) — aborting this tick" % (MONITOR_TICK_TIMEOUT_SECONDS, prio_mode))
    except Exception as e:
        logger.error(f"monitoring_job crash: {e}")
    finally:
        if prio_mode:
            _PRIO_MON_RUNNING = False
        else:
            _MON_RUNNING = False


# Above this many seconds without a lane starting a fresh tick, treat it as
# permanently wedged rather than merely slow. 15 consecutive missed 1-second
# ticks (all logged by APScheduler as "maximum number of running instances
# reached") is well outside anything a normal slow network call should cause.
# NOTE: this watchdog is itself scheduled as its own independent job entry
# (see _bulk_lane_watchdog_job's registration), so it keeps running even if
# BOTH the bulk and priority lanes wedge at the same time — it does not rely
# on either lane being healthy to detect the other.
MONITOR_TICK_TIMEOUT_SECONDS = int(os.environ.get("MONITOR_TICK_TIMEOUT_SECONDS", "20") or 20)
BULK_LANE_WEDGE_SECONDS = int(os.environ.get("BULK_LANE_WEDGE_SECONDS", "45") or 45)

async def _bulk_lane_watchdog_job(context: ContextTypes.DEFAULT_TYPE):
    """Detect a permanently wedged bulk monitoring lane and self-heal by
    restarting the process.

    Why a process restart, not just resetting _MON_RUNNING: when the bulk lane
    wedges (as seen in production — "monitoring_job ... skipped: maximum
    number of running instances reached (1)" repeating forever with zero
    successful ticks), APScheduler itself — not our _MON_RUNNING flag — is
    refusing to invoke monitoring_job again, because it still considers the
    previous invocation's coroutine unfinished. That previous invocation is
    stuck inside a blocking call our own code cannot forcibly interrupt from
    Python (a stuck native thread-pool worker cannot be killed, only abandoned).
    Flipping _MON_RUNNING back to False from outside does nothing: the
    function body it guards is never re-entered until APScheduler itself
    decides the old invocation is done, which it will not.

    This same wedge can happen to the PRIORITY lane too — a separate
    APScheduler job entry is a separate scheduling slot, not a separate
    process, so a stuck to_thread() worker triggered from the priority tick
    wedges the priority tick exactly the same way. The priority lane is what
    keeps the owner's KOL channel alerts instant, so it gets the same
    watchdog check here rather than relying on the bulk lane's health as a
    proxy for it.

    A full process exit is the one action guaranteed to clear this, and
    Railway (like any process supervisor watching the service) restarts the
    container automatically. All state this bot depends on (tracked_calls,
    sent_milestones, etc.) is persisted to disk/DB and reloaded on startup,
    so a restart here does not lose milestone history — it only costs the
    few seconds of downtime the restart itself takes, versus alerts silently
    not posting until a human notices and restarts manually.
    """
    now = time.time()
    bulk_stuck_for = now - _MON_LAST_TICK_ENTERED
    prio_stuck_for = now - _PRIO_MON_LAST_TICK_ENTERED
    wedged_lane = None
    stuck_for = 0.0
    if bulk_stuck_for >= BULK_LANE_WEDGE_SECONDS:
        wedged_lane, stuck_for = "bulk", bulk_stuck_for
    elif prio_stuck_for >= BULK_LANE_WEDGE_SECONDS:
        wedged_lane, stuck_for = "priority", prio_stuck_for
    if wedged_lane is None:
        return
    logger.critical(
        f"🚨 {wedged_lane.upper()} monitoring lane wedged for {stuck_for:.0f}s "
        f"(limit {BULK_LANE_WEDGE_SECONDS}s) — "
        f"{'no non-priority X/milestone alerts' if wedged_lane == 'bulk' else 'owner KOL X/milestone alerts'} "
        f"can fire while this holds. Restarting process to self-heal."
    )
    try:
        _save_milestones()
        _save_tracked()
    except Exception as e:
        logger.error(f"watchdog pre-restart save failed: {e}")
    os._exit(1)


async def _monitoring_job_body(context: ContextTypes.DEFAULT_TYPE, prio_mode: bool):
    try:
        bot   = context.bot
        items = list(tracked_calls.items())
        if not items:
            return

        # Skip calls for channels that have been delisted
        active_channels_lower = {c.lower() for c in load_channels()}
        items = [(k, v) for k, v in items if v.get("channel", "").lower() in active_channels_lower]

        # FIX (recurring "wrong MC after rug" bug, 2026-09-20): once a call is
        # confirmed rugged, it must NEVER be re-fetched or re-scored again.
        # ath_backfill_job already excluded rugged calls (see its own filter
        # above) but this main monitoring loop — the one that actually posts
        # milestone/X alerts and updates the MC shown in /history — had no
        # such check at all, so a rugged token kept getting fresh reads from
        # its now-dead/thin/delisted pool every tick. Dead pools are exactly
        # where DexScreener/GeckoTerminal data quality is worst (stale
        # caches, decoy pools, manipulated supply fields), so this alone
        # explains a large share of the wrong-MC and fake-X-alert reports for
        # already-rugged tokens. The call's last confirmed reading before the
        # rug is exactly what should stay shown forever after — never a new
        # one.
        items = [(k, v) for k, v in items if not v.get("rugged")]
        # frozen calls ka check_one() turant [] return karta hai — unhe rotation slots dena bekaar hai
        items = [(k, v) for k, v in items if not v.get("frozen")]

        # Priority (special) KOLs are always checked FIRST in every tick, so
        # their calls can never be delayed behind hundreds of normal tokens.
        _prio = priority_channels_lower()
        if prio_mode:
            if not _prio:
                return
            items = [kv for kv in items if kv[1].get("channel", "").lower() in _prio]
            if not items:
                return
        elif _prio:
            # Priority channels are handled by their dedicated 1-second lane.
            # Do NOT process them again in the bulk lane: duplicate concurrent
            # Dex reads here used to make slow ticks overlap and could delay
            # milestone detection for other calls.
            items = [kv for kv in items if kv[1].get("channel", "").lower() not in _prio]

        # Fresh/pending calls must stay instant, but checking every historical
        # token on every 1-second tick creates fallback storms. Keep urgent calls
        # first, then rotate a bounded slice of older calls.
        global _MON_CHECK_CURSOR, _PRIO_CHECK_CURSOR
        now_utc = datetime.utcnow()
        def _age_seconds(call):
            try:
                return (now_utc - datetime.fromisoformat(str(call.get("tracked_since", "")))).total_seconds()
            except Exception:
                return 999999.0

        # alert_pending / dex_pending calls that never resolve (token was
        # never indexed on DexScreener — usually rugged/dead within seconds)
        # used to stay "urgent" FOREVER, permanently piling up in the urgent
        # queue and starving brand-new calls of fast processing (this is why
        # new-call alerts were taking 15-20s instead of ~4-5s once the
        # tracked-calls history grew large). Give stuck pending flags a 1-hour
        # urgent window, then let them fall back into the regular rotation —
        # they still get checked eventually, just no longer at the front of
        # every single tick.
        STUCK_URGENT_CUTOFF_SECONDS = int(os.environ.get("STUCK_URGENT_CUTOFF_SECONDS", "3600") or 3600)
        # OWNER FIX (2026-09-13, WizardScan/74667 YOYO incident): a call fell
        # out of the fast "urgent" window after only 5 minutes and into the
        # slow rotating "regular" queue, where — once tracked_calls grew large
        # — it could go 40+ minutes or even hours between checks. A token that
        # spiked to 3-4x and pulled back inside that gap was never seen at its
        # peak, so the bot recorded (and alerted) only the smaller X it
        # happened to catch on the next check. Most real pumps/dumps play out
        # in the first 10-15 minutes, so keep calls in the fast lane for
        # 10 minutes instead of 5 to actually catch that window.
        # (Kept to +5 min rather than +10: logs from the casinoinsiderhere/
        # bizzradio incident (2026-09-14) showed DexScreener 429-ing on
        # almost every tick already — the bottleneck is request budget, not
        # queue position, so growing the always-checked "urgent" bucket
        # further would only add to that same overload.)
        URGENT_WINDOW_SECONDS = int(os.environ.get("URGENT_WINDOW_SECONDS", "600") or 600)

        _lane = "prio" if prio_mode else "bulk"
        _SEL_STATE["tick"][_lane] += 1
        _tick_n = _SEL_STATE["tick"][_lane]
        _now_ts = time.time()
        _total_before = len(items)

        hot, warm, regular = [], [], []
        _glitch_skipped = 0
        for kv in items:
            k, c = kv
            # liq_glitch backoff: isi token ko har tick dobara check karna budget jalata tha
            try:
                if float(c.get("_glitch_until", 0) or 0) > _now_ts:
                    _glitch_skipped += 1
                    continue
            except Exception:
                pass
            age = _age_seconds(c)
            pending_ok = bool((c.get("alert_pending") or c.get("dex_pending")) and age <= STUCK_URGENT_CUTOFF_SECONDS)
            if age <= HOT_WINDOW_SECONDS or (pending_ok and age <= 300):
                hot.append(kv)
            elif age <= URGENT_WINDOW_SECONDS or pending_ok:
                warm.append(kv)
            else:
                regular.append(kv)

        hot.sort(key=lambda kv: _age_seconds(kv[1]))
        warm.sort(key=lambda kv: _age_seconds(kv[1]))
        # warm calls har WARM_EVERY_N_TICKS-wa tick par (default: har 2nd tick = ~4s)
        _urgent_all = hot + (warm if (_tick_n % WARM_EVERY_N_TICKS == 0) else [])

        max_checks = max(20, int(os.environ.get(
            "MONITOR_PRIORITY_MAX_CHECKS_PER_TICK" if prio_mode else "MONITOR_MAX_CHECKS_PER_TICK",
            "130" if prio_mode else "110") or (130 if prio_mode else 110)))
        # FIX (late/stale-data bug): a token is only checked if its cache was
        # refreshed THIS tick. Previously max_checks (110/130) was larger than
        # what the bulk refresh covered (3 batches = 90 tokens), so ~20-40 selected
        # tokens had an expired cache -> skipped, yet the rotation cursor had
        # already moved past them. Now selection size == refresh capacity, so
        # EVERY selected token is really refreshed and checked, and the cursor
        # only advances over tokens that were actually served.
        # Priority lane 2 batches (1s tick), bulk lane 3 batches (2s tick) keeps
        # total DexScreener demand ~210 req/min < 260 budget (no self-429s).
        # Raise via MONITOR_PRIORITY_BULK_MAX_BATCHES / MONITOR_BULK_MAX_BATCHES
        # (+ DEX_MAX_REQUESTS_PER_MINUTE) only if DexScreener allows it.
        _max_batches = max(1, int(os.environ.get(
            "MONITOR_PRIORITY_BULK_MAX_BATCHES" if prio_mode else "MONITOR_BULK_MAX_BATCHES",
            "2" if prio_mode else "3") or (2 if prio_mode else 3)))
        max_checks = min(max_checks, _max_batches * 30)
        # regular (purani calls) ke liye slots GUARANTEED — urgent unhe kabhi starve nahi kar sakta
        reg_reserve = min(len(regular), max(10, int(max_checks * REGULAR_MIN_SHARE))) if regular else 0
        urgent_cap = max(10, max_checks - reg_reserve)
        if len(_urgent_all) > urgent_cap:
            _keep = urgent_cap // 2                      # sabse nayi calls hamesha
            _rest = _urgent_all[_keep:]
            _slots = urgent_cap - _keep
            _cur = _SEL_STATE["ucur"][_lane] % max(1, len(_rest))
            _rot = [_rest[(_cur + j) % len(_rest)] for j in range(min(_slots, len(_rest)))]
            _SEL_STATE["ucur"][_lane] = (_cur + len(_rot)) % max(1, len(_rest))
            urgent = _urgent_all[:_keep] + _rot
        else:
            urgent = list(_urgent_all)

        remaining = max(0, max_checks - len(urgent))
        if regular and remaining:
            cursor_name = "priority" if prio_mode else "bulk"
            cursor = (_PRIO_CHECK_CURSOR if prio_mode else _MON_CHECK_CURSOR) % len(regular)
            rotated = [regular[(cursor + j) % len(regular)] for j in range(min(remaining, len(regular)))]
            if prio_mode:
                _PRIO_CHECK_CURSOR = (cursor + len(rotated)) % len(regular)
            else:
                _MON_CHECK_CURSOR = (cursor + len(rotated)) % len(regular)
            items = urgent + rotated
            logger.debug(f"monitoring {cursor_name}: urgent={len(urgent)} rotated={len(rotated)} total={len(items)}")
        else:
            rotated = []
            items = list(urgent)

        # minute me ek baar visibility: kitni calls, rotation kitne second me poori hoti hai
        if _now_ts - _SEL_STATE["log_at"][_lane] >= 60:
            _SEL_STATE["log_at"][_lane] = _now_ts
            try:
                _tick_s = float(os.environ.get("PRIORITY_MONITOR_INTERVAL_SECONDS" if prio_mode else "MONITOR_INTERVAL_SECONDS", "3" if prio_mode else "2") or 2)
                _per_tick = max(1, len(rotated))
                _rot_s = (len(regular) / _per_tick) * _tick_s if regular else 0
                logger.info(
                    f"📡 monitor[{_lane}]: tracked={_total_before} hot={len(hot)} warm={len(warm)} "
                    f"regular={len(regular)} glitch_wait={_glitch_skipped} | this tick: urgent={len(urgent)} "
                    f"regular_checked={len(rotated)} | full regular pass ≈ {_rot_s:.0f}s")
            except Exception:
                pass

        # ── Watchdog: pending alerts must never get stuck forever ────────────
        # If a call has been waiting > 5 min and we at least know the symbol,
        # post it now (MC filled as soon as DexScreener indexes the token).
        # RH gets a much longer leash (12 min) when still unconfirmed — OWNER
        # REQUIREMENT (2026-09-24): RH's deferred-alert post is deliberately
        # gated on entry_confirmed (a real, non-FDV MC) rather than posting on
        # the first FDV/hint guess it sees, and the background confirmation
        # loops for RH keep retrying for several minutes — this watchdog only
        # needs to be the ABSOLUTE last resort so a genuinely unconfirmable
        # token doesn't get lost forever, not the thing that races ahead of
        # those loops and posts an unverified number anyway.
        for _k, _c in items:
            if not _c.get("alert_pending"):
                continue
            try:
                _since = datetime.fromisoformat(_c.get("tracked_since"))
            except Exception:
                continue
            _wd_threshold = 720 if (_c.get("chain") == "RH" and not _c.get("entry_confirmed")) else 300
            if (datetime.utcnow() - _since).total_seconds() < _wd_threshold:
                continue
            _sym = _c.get("symbol") or ""
            _fmt = _c.get("entry_fmt") or ""
            if not _sym or _fmt in ("", "N/A"):
                continue
            _c["alert_pending"] = False
            _save_tracked()
            try:
                await rt_enqueue_dropped(bot, _c["channel"], _c["msg_id"], _c["ca"],
                                         _c.get("chain", "SOL"), _fmt, _sym)
                logger.info(f"📣 Watchdog queued pending alert: {_sym} @{_c['channel']}")
            except Exception as _e_wd:
                logger.error(f"Watchdog alert failed: {_e_wd}")

        # ── Real-time bulk refresh: one DexScreener request per 30 tokens ─────
        # Batches now run in PARALLEL so a tick with hundreds of tracked tokens
        # finishes in well under a second instead of stacking up.
        try:
            # DexScreener's public endpoint is rate-limited. The old code fired
            # ALL token batches in parallel every second (10+ requests/sec on
            # a 300-token watchlist), which caused 429s and stale data; the
            # milestone engine then saw the X hours late. Refresh a rotating
            # slice instead: max 5 batches (=150 tokens) per second, with each
            # batch using a cache-busting request. A 300-token list therefore
            # gets a genuinely fresh pass about every 2 seconds without the
            # API storm. Priority channels still use their dedicated lane.
            global _DEX_BULK_CURSOR
            _sel_set = set()   # CAs already refreshed this tick (hot lane skips them)
            _urgent_keys = {k for k, _ in urgent}
            _ordered = [(k, v) for k, v in items if v.get("ca") and not v.get("frozen")]
            _ordered.sort(key=lambda kv: 0 if kv[0] in _urgent_keys else 1)   # stable: urgent first
            _chain_map = {v.get("ca"): v.get("chain", "SOL") for _, v in _ordered}
            _all_cas, _seen_cas = [], set()
            for _, _v in _ordered:
                _c_ = _v.get("ca")
                if _c_ not in _seen_cas:
                    _seen_cas.add(_c_)
                    _all_cas.append(_c_)
            _selected = [_all_cas[i:i + 30] for i in range(0, len(_all_cas), 30)]
            _sel_set = {_ca for _ch in _selected for _ca in _ch}
            if _selected:
                await asyncio.gather(*[
                    asyncio.to_thread(_bulk_refresh_dex_sync, ch, 0, _chain_map) for ch in _selected
                ], return_exceptions=True)
        except Exception as e_bulk:
            logger.warning(f"bulk dex refresh failed: {e_bulk}")

        # HOT-CALL FAST LANE:
        # For the newest calls, do one direct fresh quote in addition to the
        # bulk pass. This protects against a Dex bulk snapshot being briefly
        # stale exactly when a new call rips through 2X/3X.
        # FIX (late-alert bug, 2026-09-14): raised the old 4-calls/tick cap to
        # 40 — during a busy stretch, calls beyond the 4th used to silently
        # fall back to the slower rotation and could post their X alert late.
        #
        # REGRESSION FOUND (2026-09-17): that fix called _bulk_refresh_dex_sync
        # ONCE PER TOKEN — i.e. up to 40 separate individual DexScreener
        # requests, every single 1s priority tick (up to ~2400 req/min from
        # this one lane alone). _bulk_refresh_dex_sync already batches up to
        # 30 CAs into ONE request internally — calling it per-token instead of
        # once-with-the-whole-list threw that away entirely. This is what blew
        # through the rate budget and caused the constant 429s / late alerts.
        # Fix: pass the whole hot list in one call so it batches (40 tokens =
        # ~2 requests instead of 40), same coverage, ~20x fewer DexScreener hits.
        try:
            _hot = []
            _hot_chain_map = {}
            try:
                _sel_skip = _sel_set
            except NameError:
                _sel_skip = set()
            for _hk, _hc in items:
                if _hc.get("frozen") or not _hc.get("ca"):
                    continue
                if _hc.get("ca") in _sel_skip:
                    continue   # already refreshed in this same tick's bulk pass
                try:
                    _age = (datetime.utcnow() - datetime.fromisoformat(
                        str(_hc.get("tracked_since", "")))).total_seconds()
                except Exception:
                    _age = 999999
                if _age <= 180:
                    _hot.append((_hk, _hc))
                    _hot_chain_map[_hc.get("ca")] = _hc.get("chain", "SOL")
                if len(_hot) >= 40:
                    break
            if _hot:
                await asyncio.to_thread(
                    _bulk_refresh_dex_sync,
                    [_hc.get("ca") for _, _hc in _hot],
                    0, _hot_chain_map
                )
        except Exception as _hot_e:
            logger.debug(f"hot-call refresh failed: {_hot_e}")

        # Only cache misses use the slower multi-provider path.  Keep this
        # bounded so fallback requests cannot starve the one-second loop.
        # Raised from 3/4/8 → 6/10/16: with the thread pool now much bigger
        # (see post_init) these limits — not thread availability — were the
        # reason SOME tokens' X alerts fired and others silently didn't: any
        # call whose cache miss landed outside this small per-tick budget
        # was simply skipped for that tick, sometimes for several ticks in a
        # row, occasionally missing the exact tick where the milestone was
        # crossed.
        _monitor_miss_sem = asyncio.Semaphore(
            max(1, int(os.environ.get("MONITOR_FALLBACK_CONCURRENCY", "6") or 6))
        )
        _base_fb_max = max(0, int(os.environ.get(
            "MONITOR_PRIORITY_MAX_FALLBACKS_PER_TICK" if prio_mode else "MONITOR_MAX_FALLBACKS_PER_TICK",
            "16" if prio_mode else "10") or (16 if prio_mode else 10)))
        _storm_boost = 1
        try:
            if _dex_breaker_open():
                _storm_boost = 4
        except Exception:
            pass
        _fallback_budget = {"used": 0, "max": _base_fb_max * _storm_boost}
        _fallback_timeout = max(1.0, float(os.environ.get("MONITOR_FALLBACK_TIMEOUT_SECONDS", "4") or 4))
        _alt_timeout = max(1.0, float(os.environ.get("MONITOR_ALT_TIMEOUT_SECONDS", "3") or 3))

        async def check_one(call_key, call):
            try:
                if call.get("frozen"): return []
                # The bulk request immediately above is the only market-data
                # request allowed in this hot loop.  Never turn a cache miss
                # into a per-token API call here; that was the main source of
                # overlapping slow ticks and missed/late milestones.  Fresh
                # calls are populated by _fetch_and_alert and will be picked
                # up on the next bulk tick.
                dex = _get_cached_dex(call["ca"])
                if dex is None:
                    # DexScreener's bulk endpoint can miss brand-new/thin tokens.
                    # Try only a tiny number of PRIMARY Dex lookups per tick; do
                    # not fan out to all alternate APIs here because their 8-10s
                    # request timeouts were wedging the 1-second alert loop.
                    if not (call.get("alert_pending") or call.get("dex_pending") or _age_seconds(call) <= 300):
                        return []
                    if _fallback_budget["used"] >= _fallback_budget["max"]:
                        return []
                    # per-token backoff: jo token bulk me nahi aata (not indexed / liq filter) usay
                    # har tick single request se mat maaro — age ke hisaab se gap
                    _fb_ca = call["ca"]
                    _fb_now = time.time()
                    if _fb_now < _FB_NEXT_AT.get(_fb_ca, 0):
                        return []
                    _fb_age = _age_seconds(call)
                    _FB_NEXT_AT[_fb_ca] = _fb_now + (0.0 if _fb_age <= 45 else 2.0 if _fb_age <= 180
                                                     else 6.0 if _fb_age <= 600 else 20.0)
                    if len(_FB_NEXT_AT) > 6000:
                        for _k_ in [k_ for k_, v_ in list(_FB_NEXT_AT.items()) if v_ < _fb_now][:3000]:
                            _FB_NEXT_AT.pop(_k_, None)
                    _fallback_budget["used"] += 1
                    async with _monitor_miss_sem:
                        _lane_tok = _DEX_LANE_CV.set("mon")      # monitor fallback = capped lane
                        try:
                            dex = await asyncio.wait_for(
                                fetch_dexscreener(call["ca"], allow_alt=False, retries=1, min_liquidity=0),
                                timeout=_fallback_timeout)
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Market-data primary timeout: "
                                f"{call['ca'][:12]}...")
                            dex = None
                        finally:
                            try:
                                _DEX_LANE_CV.reset(_lane_tok)
                            except Exception:
                                pass
                if dex is None: return []

                try:
                    dex_ratio = _verified_live_ratio(call, dex)
                except Exception:
                    dex_ratio = 0.0
                # Sirf PRIMARY (DexScreener) quote ka ratio yaad rakho: alt-source quotes ko isi se
                # compare karke "stale peak" wale fake X rokte hain (_verified_live_ratio dekho).
                try:
                    if dex_ratio > 0 and str((dex or {}).get("_source") or "dexscreener").lower() == "dexscreener":
                        call["_dx_ratio"] = round(float(dex_ratio), 4)
                        call["_dx_at"] = time.time()
                except Exception:
                    pass

                # DexScreener can legally return HTTP 200 with a temporarily
                # stale pair snapshot.  That used to leave a fast token stuck
                # at 1.0X forever because the fallback path only ran when the
                # primary provider returned no pair at all.  Probe the
                # independent providers for sub-2X calls and prefer their
                # quote only when it materially advances the ratio.
                #
                # FIX (wrong-MC bug, AGRIPPA/@robinhoodcallsbot incident,
                # 2026-09-17): the old trigger (dex_ratio < 1.95) fired even
                # when DexScreener was already reporting a perfectly correct,
                # modestly-up ratio (1.17X here) — not just the genuinely
                # "stuck at 1.0X" case this probe exists for. And the
                # "fresher" check only compared the alt quote to dex_ratio
                # with NO ceiling, so when GeckoTerminal (a lower-quality
                # secondary source, especially for newer chains like RH) came
                # back with a wildly wrong 36.35X, the code trusted it purely
                # for being bigger — turning a correct 36.9K MC into a fake
                # 1.14M one. Two changes: (1) only probe when dex_ratio is
                # actually near the stuck-at-1.0X band, not anywhere under
                # 1.95; (2) never accept an alt quote that implies a jump
                # implausibly larger than what "unsticking" a 1.0X read could
                # explain — a real unstick is usually a few X, not 30x+.
                # FIX (2026-10-17, $RETAIL/@Earlycallsalert fake 4X): a token DUMPED to 0.33X is not
                # "stuck at 1.0X". GeckoTerminal returned a frozen 39.7K MC (the old peak) for hours,
                # the +5 plausibility ceiling let 0.33X -> 4.33X through and a fake 4X alert was posted.
                # Probe alternates only when DexScreener is ~flat (0.90-1.10X) or gave no ratio (0.0).
                if (EXTRA_SOURCES_ENABLED and (dex_ratio == 0.0 or 0.90 <= dex_ratio < 1.10)):
                    _ca_probe = call.get("ca", "")
                    _now_probe = time.time()
                    if _now_probe >= _monitor_alt_probe_at.get(_ca_probe, 0):
                        _monitor_alt_probe_at[_ca_probe] = _now_probe + 4.0
                        try:
                            async with _monitor_miss_sem:
                                if _fallback_budget["used"] >= _fallback_budget["max"]:
                                    alt_quote = None
                                else:
                                    _fallback_budget["used"] += 1
                                    alt_quote = await asyncio.wait_for(
                                        asyncio.to_thread(_fetch_alt_sources_sync, _ca_probe),
                                        timeout=_alt_timeout)
                        except Exception:
                            alt_quote = None
                        if alt_quote:
                            alt_ratio = _verified_live_ratio(call, alt_quote)
                            _plausible_jump = alt_ratio <= max(dex_ratio * 8, dex_ratio + 5)
                            if alt_ratio > max(dex_ratio * 1.15, dex_ratio + 0.10) and _plausible_jump:
                                logger.info(
                                    f"📈 fresher alternate quote selected "
                                    f"{call.get('symbol','?')} "
                                    f"{dex_ratio:.2f}X → {alt_ratio:.2f}X "
                                    f"({alt_quote.get('_source','alt')})")
                                dex = alt_quote
                            elif alt_ratio > max(dex_ratio * 1.15, dex_ratio + 0.10):
                                logger.warning(
                                    f"🚫 Ignoring implausible alt-source jump "
                                    f"{call.get('symbol','?')} {dex_ratio:.2f}X → "
                                    f"{alt_ratio:.2f}X ({alt_quote.get('_source','alt')}) "
                                    f"— too large to be a genuine unstick, keeping DexScreener's read.")

                cur_price = dex.get("price", 0)
                cur_mc    = dex.get("mcap", 0)

                # Sanity check: DexScreener sometimes returns garbage/inflated data.
                # If MC is > $1T it's almost certainly wrong — skip to avoid phantom billion alerts.
                if cur_mc > 1_000_000_000_000:
                    logger.warning(f"Skipping unrealistic MC ${cur_mc:.0f} for {call['ca'][:12]}...")
                    return []

                # Fill in entry data for calls tracked before token was on dex
                if call.get("dex_pending") and (cur_price > 0 or cur_mc > 0):
                    if not call.get("entry_locked"):
                        _hint_mc = call.get("post_mc_hint", 0)
                        # Same freshness fix as the instant-track path above
                        # (2026-09-18): dex_pending means we didn't have data on
                        # first sight, so this fill-in can legitimately run
                        # minutes later. Without checking age here, a genuinely
                        # early/low entry_mc from the caller's post always got
                        # compared against a since-pumped live price and lost.
                        try:
                            _post_dt = datetime.fromisoformat(str(call.get("tracked_since", "")))
                            _age = (datetime.utcnow() - _post_dt.replace(tzinfo=None)).total_seconds()
                        except Exception:
                            _age = 0
                        # FIX (2026-10-11): same RH stale-hint guard as the real-time path.
                        _rh_stale_hint = (dex.get("chain", call.get("chain", "SOL")) == "RH"
                                          and _hint_mc > 0 and cur_mc >= _hint_mc * 2
                                          and not dex.get("_mc_is_fdv"))
                        if _hint_mc > 0 and _age > ENTRY_MC_FRESH_SECONDS and not _rh_stale_hint:
                            _entry_mc, _src = (_hint_mc, "post")
                        else:
                            _entry_mc, _src = _reconcile_entry_mc(
                                _hint_mc, cur_mc,
                                live_is_fdv=bool(dex.get("_mc_is_fdv")))
                        logger.info(
                            f"🧮 entry-MC reconcile @{call.get('channel','')}: "
                            f"post_hint={fmt_mc(call.get('post_mc_hint', 0) or 0)} "
                            f"live={fmt_mc(cur_mc)} fdv={bool(dex.get('_mc_is_fdv'))} "
                            f"→ chosen={fmt_mc(_entry_mc)} src={_src}")
                        _entry_price = cur_price
                        if _src in ("post", "post_unconfirmed") and cur_mc > 0 and cur_price and _entry_mc > 0:
                            _entry_price = cur_price * (_entry_mc / cur_mc)
                        call["entry_price"] = _entry_price
                        call["entry_mc"]    = _entry_mc
                        call["entry_fmt"]   = (fmt_mc(_entry_mc) if _entry_mc > 0
                                               else dex.get("mcap_fmt", "N/A"))
                        call["entry_src"]   = _src
                        call["entry_locked"] = True
                        # Milestone alerts must not fire off an unconfirmed
                        # FDV-derived guess (see _reconcile_entry_mc) — the
                        # milestone-check block below gates on this flag.
                        call["entry_confirmed"] = _src not in ("live", "live_fdv", "post_unconfirmed")
                        # FIX: same indexing-lag protection as the real-time path —
                        # a no-hint entry locked off a single quote can be an
                        # artificially low pre-index read. Sample several times
                        # over ~48s (slower chains keep settling past 3s) and
                        # patch upward whenever a higher, more-settled read shows
                        # up — never delaying anything since this runs in the
                        # background on the existing 1s monitoring tick.
                        if _src in ("live", "live_fdv", "post_unconfirmed") and _entry_mc > 0:
                            _ck_confirm = call_key
                            async def _confirm_entry_mc(_key=_ck_confirm, _ca=call.get("ca",""),
                                                         _first_mc=_entry_mc, _chan=call.get("channel",""),
                                                         _chg=call.get("chain","SOL"),
                                                         _storm=_dex_breaker_open()):
                                _best_mc = _first_mc
                                _tighter2 = (_chg != "RH") and _storm
                                # RH: OWNER REQUIREMENT (2026-09-24) — keep retrying
                                # for real data over a much longer window than other
                                # chains (~9 min) instead of the usual ~48s, since the
                                # deferred post for RH is gated on entry_confirmed and
                                # we'd rather wait than post an FDV/hint guess. The
                                # watchdog (extended separately for RH) is the true
                                # last-resort ceiling, not this loop's end.
                                _delays2 = ((20, 30, 40, 60, 60, 60, 60, 60, 60) if _chg == "RH" else
                                            (2, 3, 5, 8, 10, 15) if _tighter2 else
                                            (3, 5, 10, 15, 15))
                                _rev_th2 = 1.08 if (_tighter2 or _chg == "RH") else 1.15
                                for _delay in _delays2:  # ~3s..48s cumulative (RH: much longer)
                                    await asyncio.sleep(_delay)
                                    _cc = tracked_calls.get(_key)
                                    _src_now = (_cc or {}).get("entry_src")
                                    if (not _cc
                                            or (_src_now not in ("live", "live_fdv", "post_unconfirmed")
                                                and not (_chg == "RH" and _src_now == "post"))):
                                        return
                                    try:
                                        _invalidate_dex_cache(_ca)
                                        _confirm = await asyncio.wait_for(
                                            asyncio.to_thread(_confirm_mc_cross_check, _ca, _chg),
                                            timeout=8)
                                    except Exception:
                                        continue
                                    # Only a real (non-FDV) cross-check counts as an
                                    # actual confirmation — see the matching fix in
                                    # the real-time path's _confirm_entry_mc above.
                                    if (_confirm or {}).get("mcap", 0) and not (_confirm or {}).get("_mc_is_fdv"):
                                        _cc["entry_confirmed"] = True
                                        _cmc = (_confirm or {}).get("mcap", 0) or 0
                                        if _cmc > _best_mc * _rev_th2:
                                            _best_mc = _cmc
                                            _cc["entry_mc"]    = _cmc
                                            _cc["entry_fmt"]   = fmt_mc(_cmc)
                                            _cc["entry_price"] = _confirm.get("price", _cc.get("entry_price", 0))
                                            _save_tracked()
                                            logger.info(
                                                f"📈 Entry MC revised up (indexing lag, "
                                                f"src={_confirm.get('_source','dex')}): "
                                                f"{fmt_mc(_first_mc)} → {fmt_mc(_cmc)} @{_chan}")
                                        return  # RH: stop retrying once really confirmed
                                # Fail-safe: never leave a call permanently gated if
                                # every cross-check attempt happened to raise — but
                                # for RH, don't force it here; let the watchdog (which
                                # waits much longer for RH) be the actual fail-safe,
                                # so a still-unconfirmed RH call doesn't get treated
                                # as "confirmed" and start firing milestone X alerts
                                # off an unverified number.
                                if _chg != "RH":
                                    _cc = tracked_calls.get(_key)
                                    if _cc is not None:
                                        if (not _cc.get("entry_confirmed")
                                                and _cc.get("entry_src") in ("live", "live_fdv", "post_unconfirmed")):
                                            # confirmed by exhaustion, not by evidence — tag it
                                            # so the early-rebase guard can still correct it
                                            _cc["entry_src"] = "unverified_fail_safe"
                                        _cc["entry_confirmed"] = True
                            asyncio.create_task(_confirm_entry_mc())

                    call["symbol"]      = dex.get("symbol", "")
                    call["chain"]       = dex.get("chain", call.get("chain", "SOL"))
                    call["dex_pending"] = False
                    logger.info(f"📊 entry filled: {call['symbol']} @{call['channel']} {call['entry_fmt']}")
                    # Deferred dropped-call alert: we skipped it earlier because
                    # data was N/A. Now that we have real data, post it — except
                    # for RH, where we hold off (OWNER REQUIREMENT, 2026-09-24)
                    # until entry_confirmed flips true (real, non-FDV MC from an
                    # independent source), re-checking on each future tick
                    # instead. The watchdog below still guarantees this can't
                    # get stuck forever.
                    _rh_gate_ok = (call.get("chain") != "RH") or call.get("entry_confirmed")
                    if call.get("alert_pending") and call.get("symbol") and call.get("entry_fmt") not in ("", "N/A") and _rh_gate_ok:
                        call["alert_pending"] = False
                        _save_tracked()
                        try:
                            await rt_enqueue_dropped(
                                bot, call["channel"], call["msg_id"], call["ca"],
                                call.get("chain", "SOL"), call["entry_fmt"], call["symbol"])
                            logger.info(f"📣 Deferred dropped alert queued: {call['symbol']} @{call['channel']}")
                        except Exception as e_da:
                            logger.error(f"Deferred dropped alert failed: {e_da}")
                # Also fill missing symbol for calls that were tracked but have empty symbol
                elif not call.get("dex_pending") and not call.get("symbol") and dex.get("symbol"):
                    call["symbol"] = dex.get("symbol", "")
                    logger.info(f"📊 symbol filled late: {call['symbol']} @{call['channel']}")

                # ── RUG / FAKE-MC GATE ──────────────────────────────────
                # Never announce an X for a token whose liquidity was pulled or
                # whose reported marketcap is not backed by a real pool.
                _ok_live, _rug_reason = _rug_status(call, dex)
                # liq_glitch_retry: isi token ko agle tick par dobara fetch/evaluate karna budget jalata tha.
                # Ab 3s -> 6s -> 12s -> 20s (cap) ka backoff; liquidity wapas aate hi reset.
                try:
                    if _rug_reason == "liq_glitch_retry":
                        _gn = int(call.get("_glitch_n", 0) or 0) + 1
                        call["_glitch_n"] = _gn
                        call["_glitch_until"] = time.time() + min(
                            GLITCH_BACKOFF_MAX_S, GLITCH_BACKOFF_BASE_S * (2 ** min(_gn - 1, 4)))
                    elif call.get("_glitch_n") or call.get("_glitch_until"):
                        call["_glitch_n"] = 0
                        call["_glitch_until"] = 0
                except Exception:
                    pass
                # HARD stop: token really is dead / data is garbage.
                # RH FIX (2026-09-24): liq_glitch_retry means DexScreener's
                # liquidity field came back glitched (liq=0) while 24h volume
                # proves the token is still trading live — it is NOT a real
                # rug. Keeping it in _HARD_RUG made the function `return []`
                # immediately, which skipped milestone detection entirely for
                # that tick. During the current DexScreener 429 storm this
                # was hitting the SAME coins on almost every tick for minutes
                # at a time, so a coin that had genuinely crossed 2x never
                # got recorded — it silently never appeared on the
                # Leaderboard even though its actual price justified a spot.
                # Moving it out of _HARD_RUG routes it through the SOFT path
                # below instead, which still fully records the milestone
                # (X-Ray + points + Leaderboard/Champions) and only pauses
                # the in-channel post if SOFT_GATE_RECORD_ONLY=1 is set.
                _HARD_RUG = ("liquidity_pulled", "liquidity_pulled_pending",
                             "already_rugged", "absurd_mc", "bad_data", "no_mc")
                _record_only = False
                # Caller deleted their original post: there is no message left
                # to reply/edit in-channel, so never attempt to post — but
                # keep computing/recording the true MC and milestones so
                # /history, X-Ray and points reflect what the token actually
                # did, instead of freezing at the MC seen when it was deleted.
                if _is_deleted_blocked(call):
                    _record_only = True
                if not _ok_live:
                    if _rug_reason in ("liquidity_pulled", "already_rugged"):
                        _save_tracked()
                    if _rug_reason in _HARD_RUG:
                        return []
                    # SOFT reasons (low liquidity / low volume / mc-liq ratio):
                    # pehle yahan se `return []` hota tha, isliye X milestone na
                    # channel me post hoti thi, na X-Ray record me jati thi, na
                    # leaderboard/champion points milte the. Ab milestone poori
                    # tarah record hoti hai (X-Ray + points + leaderboard), sirf
                    # channel post rok di jati hai.
                    # YEHI wajah thi ke "record me X show hoti hai lekin channel
                    # post nahi hoti" — soft gate (low liquidity / low 24h volume /
                    # mc-liq ratio) sirf channel post rok deta tha. Owner ko har
                    # verified X channel me chahiye, is liye ab default POST hai.
                    # Purana behaviour wapas chahiye to: SOFT_GATE_RECORD_ONLY=1
                    _soft_silent = str(os.environ.get("SOFT_GATE_RECORD_ONLY", "0")).strip().lower() in ("1", "true", "yes")
                    _record_only = _soft_silent
                    logger.info(f"🟡 soft gate ({_rug_reason}) {call.get('symbol','?')} "
                                f"@{call.get('channel','?')} — "
                                + ("record only, no channel post" if _soft_silent
                                   else "posting anyway (SOFT_GATE_RECORD_ONLY=0)"))

                entry_price = call.get("entry_price", 0)
                entry_mc    = call.get("entry_mc", 0)

                ratio = _verified_live_ratio(call, dex)
                if ratio <= 0:
                    return []

                if _remove_impossible_milestones(call_key, call):
                    _save_milestones()
                    _save_milestone_posts()

                # Safety cap: ratio cannot exceed MAX_MILESTONE × 2 to prevent phantom billions
                # If a token rugged, DexScreener returns None → this code won't run, so no inflated ratio
                ratio = min(ratio, MAX_MILESTONE * 2)

                # ── Single-tick spike-glitch guard ────────────────────────────
                # _update_peak() below locks in peaks permanently and never
                # lowers them, so ONE bad tick (a garbage/stale pool read, a
                # decimals glitch, etc.) can freeze a token's recorded ATH at a
                # wildly inflated fake number forever — while the real price
                # stays far lower (e.g. a real ~70K-MC token permanently shown
                # with a 24.57M "ATH" off a single glitched read that jumped
                # 1000x in one tick). ath_backfill_job already guards its
                # historical-candle path this same way; mirror it here for the
                # live 1s tick path: a jump far beyond the last CONFIRMED tick
                # must repeat on the very next tick before it's trusted, instead
                # of being locked in immediately off one sample. Genuine fast
                # pumps still fire normally, just ~1 second later.
                _prev_conf_ratio = float(call.get("_last_confirmed_ratio",
                                                   call.get("last_ratio", 0)) or 0)
                _spike_cap = max(_prev_conf_ratio * 25, 50)
                if _prev_conf_ratio > 0 and ratio > _spike_cap:
                    _pend_r = float(call.get("_spike_pending_ratio", 0) or 0)
                    if _pend_r > 0 and 0.7 * _pend_r <= ratio <= 1.3 * _pend_r:
                        # Same implausible jump seen on two consecutive ticks —
                        # trust it, this is a real fast pump, not a glitch.
                        call.pop("_spike_pending_ratio", None)
                        call.pop("_spike_pending_at", None)
                    else:
                        logger.warning(
                            f"⏳ Spike-glitch guard: {call.get('symbol','?')} "
                            f"@{call.get('channel','?')} jumped {_prev_conf_ratio:.2f}x → "
                            f"{ratio:.2f}x in one tick — holding for confirmation")
                        call["_spike_pending_ratio"] = ratio
                        call["_spike_pending_at"] = datetime.utcnow().isoformat()
                        ratio = _prev_conf_ratio
                        cur_mc = entry_mc * ratio if entry_mc > 0 else cur_mc
                else:
                    call.pop("_spike_pending_ratio", None)
                    call.pop("_spike_pending_at", None)
                call["_last_confirmed_ratio"] = ratio

                call["last_ratio"] = round(ratio, 4)
                call["_live_at"] = time.time()   # live tracker coverage stamp (used by ath_backfill_job)
                call["liquidity_usd"] = float(dex.get("liquidity_usd", 0) or 0)
                call["volume_h24"]    = float(dex.get("volume_h24", 0) or 0)
                call["txns_buys"]     = int(dex.get("txns_buys", 0) or 0)
                call["txns_sells"]    = int(dex.get("txns_sells", 0) or 0)

                # ── EARLY ENTRY RE-BASE GUARD v2 (2026-10-05) ──
                # A live-quote entry read at call time can sit far BELOW the real
                # MC (pool still indexing / FDV-only / wrong pair). The next good
                # ticks then look like a pump and hand the caller free X — and the
                # fake 2X/3X slip out ONE BY ONE (each step under the spike cap)
                # before any single tick looks "impossible". v2 vs the original:
                #   ratio 5 -> 2 | window 10 -> 45 min | "post"(unconfirmed) and
                #   "unverified_fail_safe" srcs allowed | 2 consecutive elevated
                #   ticks required | already-sent fake milestones get PURGED,
                #   their points reversed, and the owner is notified.
                try:
                    _rb_src = str(call.get("entry_src") or "")
                    _rb_src_ok = (
                        _rb_src in ("live", "live_fdv", "post_unconfirmed", "unverified_fail_safe")
                        or (_rb_src == "post" and not call.get("entry_confirmed"))
                    )
                    _rb_verified_real = bool(call.get("entry_confirmed")) and _rb_src != "unverified_fail_safe"
                    if (EARLY_REBASE_ENABLED and entry_mc > 0
                            and str(call.get("chain") or "").upper() in EARLY_REBASE_CHAINS
                            and ratio >= EARLY_REBASE_MIN_RATIO
                            and _rb_src_ok and not _rb_verified_real
                            and not call.get("_entry_rebased")
                            and cur_mc > 0 and not dex.get("_mc_is_fdv")):
                        call["_rb_elevated"] = int(call.get("_rb_elevated", 0) or 0) + 1
                    else:
                        call["_rb_elevated"] = 0
                    if int(call.get("_rb_elevated", 0) or 0) >= 2:
                        _ts = call.get("tracked_since", "")
                        _age_min = ((datetime.utcnow() - datetime.fromisoformat(_ts)).total_seconds() / 60.0
                                    if _ts else 9999)
                        if _age_min <= EARLY_REBASE_WINDOW_MIN:
                            _new_mc = cur_mc
                            _old_mc = entry_mc
                            _fake_sent = sorted(
                                int(x) for x in (sent_milestones.get(call_key) or set())
                                if 2 <= int(x) <= MAX_MILESTONE)
                            if _fake_sent:
                                for _fx in _fake_sent:
                                    sent_milestones[call_key].discard(_fx)
                                    try:
                                        milestone_times.get(call_key, {}).pop(str(_fx), None)
                                    except Exception:
                                        pass
                                _save_milestones(); _save_milestone_times()
                                await _purge_points_for_milestones(call, _fake_sent)
                            call["entry_mc"]    = _new_mc
                            call["entry_fmt"]   = fmt_mc(_new_mc)
                            _px = float(dex.get("price", 0) or 0)
                            if _px > 0:
                                call["entry_price"] = _px
                            call["entry_src"]      = "rebased_early"
                            call["entry_confirmed"] = True
                            call["_entry_rebased"] = True
                            call["peak_ratio"] = 1.0
                            call["last_ratio"] = 1.0
                            call["_last_confirmed_ratio"] = 1.0
                            call["peak_mc"] = _new_mc
                            call["peak_mc_fmt"] = fmt_mc(_new_mc)
                            _save_tracked()
                            logger.warning(
                                f"🛠️ EARLY REBASE {call.get('symbol','?')} @{call.get('channel','?')}: "
                                f"entry {fmt_mc(_old_mc)} → {fmt_mc(_new_mc)} "
                                f"({ratio:.1f}x at {_age_min:.1f} min) — purged fake X: {_fake_sent or 'none'}")
                            if OWNER_ID:
                                asyncio.create_task(bot.send_message(
                                    OWNER_ID,
                                    f"🛠️ <b>Entry re-based</b> {call.get('symbol','?')} @{call.get('channel','?')}\n"
                                    f"Entry quote {fmt_mc(_old_mc)} galat tha, live MC {fmt_mc(_new_mc)} "
                                    f"({ratio:.1f}x, {_age_min:.1f} min after call).\n"
                                    f"Fake X purge: {', '.join(str(x)+'X' for x in _fake_sent) if _fake_sent else 'koi nahi'}",
                                    parse_mode="HTML"))
                            return []
                except Exception as _e_rb:
                    logger.warning(f"early-rebase guard skipped: {_e_rb}")

                # ── Real-time ATH: remember the highest ratio ever seen ──────
                _update_peak(call, ratio, cur_mc)
                peak_ratio = call_peak_ratio(call)

                # ── Price-history sampling (website chart) ──────────────────
                # Data only: max 1 point / 300s per call, last 96 kept.
                # Frozen calls and invalid / <=0 / NaN ratios never append.
                try:
                    _rv = float(ratio or 0)
                    if 0 < _rv < float("inf") and not call.get("frozen"):
                        _now_ts = int(time.time())
                        if _now_ts - int(call.get("series_ts", 0) or 0) >= 300:
                            _ser = call.get("series")
                            if not isinstance(_ser, list):
                                _ser = []
                                call["series"] = _ser
                            _ser.append(round(_rv, 3))
                            del _ser[:-96]
                            call["series_ts"] = _now_ts
                            _save_tracked()
                except Exception:
                    pass

                # SAME-TICK plausibility cap: _remove_impossible_milestones()
                # above only cleans up milestones from a PAST tick, after
                # their alert already posted. That gap is exactly how a
                # momentarily-wrong/provisional entry_mc (before "dex filled"
                # confirms the real one) let a single tick compute a huge
                # implausible ratio and queue+post several fake high-X
                # alerts (e.g. 30X-50X) that only got purged on the NEXT
                # tick — too late, the channel already saw them. Clamp
                # peak_ratio with the exact same cap here, before `pending`
                # is built, so this tick can never queue an alert that the
                # purge would have to undo a moment later.
                if entry_mc > 0:
                    _max_plausible_ratio = MAX_PLAUSIBLE_MC / entry_mc
                    if peak_ratio > _max_plausible_ratio:
                        peak_ratio = _max_plausible_ratio

                # ── Entry-MC confirmation gate ────────────────────────────────
                # BUG FIX (2026-09-14, DOGGO/minibangcall incident): an entry_mc
                # locked in from an FDV-derived or otherwise unconfirmed live
                # read is not solid ground truth yet — _confirm_entry_mc() needs
                # at least one independent cross-check round-trip before ratios
                # computed against it can be trusted. Without this gate, a token
                # whose real entry was already ~75K got locked in at a stale
                # FDV-derived ~37K and looked like a fake instant "2X" alert
                # (with points already awarded) seconds later, before the
                # correction task had a chance to land. Peak/ratio bookkeeping
                # above still runs as normal — only the alert is withheld until
                # confirmed, so nothing is lost once it lands moments later.
                if not call.get("entry_confirmed", True):
                    return []

                # ── Which milestones fire on this tick? ──────────────────────
                # Milestones fire on the PEAK, so a token that spikes between
                # two ticks and dumps still gets its real X recorded.
                pending = [ms for ms in get_milestones()
                           if ms <= MAX_MILESTONE
                           and peak_ratio >= ms
                           and ms not in sent_milestones[call_key]]
                if not pending:
                    return []
                pending.sort()

                # REVERTED (2026-09-15): a per-new-milestone independent
                # cross-check was added here for the META/HOODSHIP
                # (Robinhood Chain, @robinhoodcallsbot) drift incident, but
                # it added a live network round-trip (up to 5s) inside this
                # per-second monitoring loop. The bot was already deep in a
                # DexScreener 429 storm at the time ("breaker open" / "bulk
                # refresh" warnings throughout the logs); the extra calls
                # piled onto that same overloaded API, slowed ticks down
                # enough that monitoring_job started overlapping itself
                # ("skipped: maximum number of running instances reached"),
                # and — as a direct result — brand-new real calls were
                # missed while some already-stale/rugged tokens' held-back
                # milestones slipped through once the system caught up. The
                # cure was worse than the disease. Needs a fix that doesn't
                # add a live call inside this hot loop before it's reattempted.

                triggered = []
                # OWNER FIX (2026-09-14, MiniBangCall/5381 POUR incident): this
                # used to post ONLY the highest milestone reached in a tick —
                # e.g. when DexScreener 429-blackout delayed data for a while
                # and the first successful read already showed 4X, 2X and 3X
                # were marked "sent" SILENTLY and never posted at all — same
                # class of bug already fixed for ath_backfill_job above, just
                # not carried over here. Owner wants every crossed milestone
                # to actually post: 2X the moment it's seen, then 3X, then 4X
                # — never silently skipped, even if several are detected on
                # the very same tick. The real-time queue already paces actual
                # sends _RT_GLOBAL_MIN_GAP seconds apart, so posting all of
                # them back-to-back does not flood the channel.
                for ms in pending:
                    # SEAL IMMEDIATELY — this X level is "done" the instant it
                    # is detected, regardless of what happens to the send
                    # afterward (media present or not, Telegram send succeeds,
                    # fails, or is retried and given up on). This makes every
                    # milestone strictly one-shot: attempted once, then
                    # permanently done either way — it can never resurface and
                    # post again later, stale/out of context.
                    sent_milestones[call_key].add(ms)
                    # FIX (wrong MC bug): when several milestones fire in the same
                    # tick (e.g. a call jumps straight to 4X), each post MUST show
                    # the market cap of ITS OWN level — 2X post = entry×2,
                    # 3X post = entry×3 — instead of repeating the live MC.
                    # FIX (wrong MC bug, take 2): the previous version tried a
                    # 5x/0.5x "sanity tolerance" around the live MC before
                    # falling back to entry×ms — but when several milestones
                    # fire in the same tick (e.g. straight to 6X), the live MC
                    # is very often within that band of EVERY intermediate
                    # level too (e.g. a 6X live read is only ~3x the 2X level,
                    # ~2x the 3X level, etc.), so the tolerance check let the
                    # SAME live number leak into the 2X, 3X and 4X posts as
                    # well — exactly the "all levels show 19.7K" bug reported
                    # for a call whose real 2X/3X/4X levels were 6.9K/9.9K/
                    # 13.2K. Only the HIGHEST milestone reached in a batch can
                    # legitimately claim the live/current MC (it's the one
                    # that's actually true right now); every earlier one in
                    # the same batch has no real data point for the exact
                    # moment it was crossed, so it must show its own
                    # mathematically correct entry×ms — same rule already
                    # used correctly in ath_backfill_job and /recheckx.
                    if ms == pending[-1] and cur_mc > 0 and cur_mc < 1_000_000_000_000:
                        expected_mc = entry_mc * ms if entry_mc > 0 else 0
                        # still guard against a wildly wrong live read (bad pool, etc.)
                        if expected_mc > 0 and (cur_mc > expected_mc * 5 or cur_mc < expected_mc * 0.5):
                            ms_mc_fmt = fmt_mc(expected_mc)
                        else:
                            ms_mc_fmt = fmt_mc(cur_mc)
                    elif entry_mc > 0:
                        ms_mc_fmt = fmt_mc(entry_mc * ms)
                    elif cur_mc > 0:
                        ms_mc_fmt = fmt_mc(cur_mc)
                    else:
                        ms_mc_fmt = dex.get("mcap_fmt", "N/A")
                    # REAL-TIME: queue the alert the moment it is detected.
                    # No batching, no waiting for the whole scan to finish.
                    await rt_enqueue_milestone(bot, call_key, call, ms, ms_mc_fmt,
                                               record_only=_record_only,
                                               delay=0)
                    triggered.append((call_key, call, ms, ms_mc_fmt))
                return []
            except Exception as e:
                logger.error(f"monitoring check failed {call_key}: {e}")
                return []

        all_hits = []
        for i in range(0, len(items), 30):
            batch = await asyncio.gather(*[check_one(k, v) for k, v in items[i:i+30]])
            for hits in batch:
                all_hits.extend(hits)
            # If fallback work used the whole per-tick budget, finish this tick
            # quickly so the next 1-second invocation can run instead of being
            # skipped by APScheduler.
            # (removed `break` on exhausted fallback budget: check_one() already
            #  returns instantly for cache-misses when the budget is used up, while
            #  cache-hit tokens in later batches are cheap and MUST still be checked,
            #  otherwise their milestones were skipped for that whole tick.)

        _save_tracked()
        # NOTE: alerts are already dispatched in real time by the FIFO worker
        # (rt_enqueue_milestone) as soon as each milestone is detected.

        # Failed-call deduction: no 2X within 48 hours. If it later reaches 2X,
        # award_points_for_milestone restores these 10 points before rewarding it.
        # (Skipped in the 1-second priority fast lane — it must stay lightweight.)
        if not prio_mode:
            cutoff_fail = datetime.utcnow() - timedelta(hours=CALL_FAIL_HOURS)
            for call_key, call in list(tracked_calls.items()):
                if 2 in sent_milestones.get(call_key, set()):
                    continue
                ts_str = call.get("tracked_since", "")
                if not ts_str:
                    continue
                try:
                    if datetime.fromisoformat(ts_str) < cutoff_fail:
                        await deduct_points_for_failed_call(call["channel"], call_key)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"monitoring_job body crash: {e}")


# ─── 🎬 champion_video warmup (bot start par, background thread) ───────────────
# Intro pehli baar ~20s me banta hai aur DATA_DIR me cache hota hai.
# Agar bot.py me champion_video.warmup() pehle se chal raha hai to ye hissa hata dein.
try:
    import threading as _cv_th
    import champion_video as _cv_mod
    _cv_th.Thread(target=_cv_mod.warmup, daemon=True, name="champion-video-warmup").start()
except Exception:
    pass
