# ===== dropped_call.py — New call detect + DROPPED CALL alert + deleted posts + scan =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).



def is_call_message(text):
    if not text or len(text) < 10: return False
    tl  = text.lower()
    kw  = ["buy","bought","long","short","entry","target","tp ","tp:","sl ","sl:","signal",
           "gem","moon","moonshot"," call","calls","called","ape","aped","snipe","load","bag",
           "stack","enter","exit","take profit","stop loss","watch","watching","monitor",
           "looking","launch","listed","presale","stealth","new","caller","kol",
           "mc ","mc:","mcap","market cap","fdv","liquidity","volume","chart",
           "ca ","ca:","contract","token","address","bullish","early",
           "dexscreener","dextools","birdeye","geckoterminal","pump.fun","pump","bullx",
           "photon","gmgn","ave.ai","defined.fi","dedust","stonfi","ston.fi","getgems",
           "solana","sol","ethereum","eth","bsc","bnb","base","ton","toncoin",
           "robinhood","rh ","hood","dyor","nfa","not financial advice","research",
           "risk","high risk","low cap","micro cap","soon","now","live","active",
           "fire","hot","good","check",
           "pinksale","pink sale","pinklock","gempad","gem pad","cheesepad","cheese pad",
           "cheesepad.ai","cheesepad.io","pinksale.finance","launchpad","launch pad",
           "fairlaunch","fair launch","fair-launch","presale","pre sale","pre-sale",
           "public sale","private sale","ido","ico","seed round","sale live","sale starts",
           "whitelist","wl round","kyc","audit","doxx","doxxed","refund","refundable",
           "vesting","tge","liquidity lock","lp lock","raised","contribute","contribution",
           "softcap","soft cap","hardcap","hard cap","no hard cap","min buy","max buy",
           "min contribution","max contribution","sale type","pool","pool live","buy now"]

    return any(k in tl for k in kw) or bool(ETH_CA_PATTERN.search(text)) or \
           bool(TON_CA_PATTERN.search(text)) or \
           any(32<=len(s)<=44 for s in SOL_CA_PATTERN.findall(text) if not s.startswith("0x"))

# ─── Recap / chart-update detector ─────────────────────────────────────────
# A KOL revisiting a token they already called ("CHART UPDATE", "look where
# it went from there", "delivered a 15x move from our call") still contains
# a CA and an mc-looking number, so is_call_message() matches it too — but
# it is NOT a fresh call. Treating it as one means the historical mc
# mentioned in the recap text gets used as "entry", and since the live
# price already reflects the pump, the very next monitoring tick fires a
# false "instant Xx" alert for a call that never actually happened just now.
_RECAP_PHRASES = [
    "chart update", "price update", "update:", "reminder:",
    "look where it went", "look how far", "look at it now", "look at this now",
    "since our call", "since we called", "since the call",
    "from our call", "from our original call", "from the call",
    "we called this", "we called it", "called this one", "already called",
    "delivering a move", "delivering an impressive", "delivered a", "delivered an",
    "and look where", "and look how", "early positioning matters",
    "recap", "revisit", "throwback", "flashback",
    "more opportunities like this are on the way",
    "accomplishment unlocked", "kolscreener",
]

# Regex patterns catch REPOSTED/RELAYED alerts from OTHER bots (e.g. a KOL
# channel forwarding a Maestro/PIRBView-style milestone post like
# "✅ 7.4x $DARK  🧢 Called @ $41.2K  🔝 $304.4K"). These don't match any
# fixed phrase above but are unmistakably a recap of an old call, not a fresh
# signal — if left undetected, scan_job would track them as brand-new and post
# a fake milestone X-alert built from someone else's historical numbers.
_RECAP_PATTERNS = [
    re.compile(r'✅\s*\d+(?:\.\d+)?\s*x\b', re.IGNORECASE),   # "✅ 7.4x $TICKER"
    re.compile(r'called\s*@\s*\$', re.IGNORECASE),            # "Called @ $41.2K"
    re.compile(r'🧢.{0,20}called', re.IGNORECASE),             # "🧢 Called @ ..."
    re.compile(r'milestone\s*alert', re.IGNORECASE),
    re.compile(r'🔝\s*\$'),                                    # "🔝 $304.4K" peak-MC marker
    # OWNER FIX (2026-09-12, WizardScan/74142 MOONCOIN incident): a
    # KOLScreener_bot-style "achievement" repost — "Accomplishment Unlocked:
    # x2!" / "made a **x2+** call on TOKEN" — was slipping through as a fresh
    # call. The token had already pumped by the time this got posted, so the
    # live DexScreener price was used as a fake "entry", and the next tick's
    # real price looked like an instant 3X. Catch this wording explicitly.
    re.compile(r'made\s+a\s*\**x\d+(?:\.\d+)?\+?\**\s*call', re.IGNORECASE),  # "made a **x2+** call"
    re.compile(r'accomplishment\s*unlocked', re.IGNORECASE),
]

def _is_recap_post(text):
    """True when a post is RE-mentioning a call already made earlier (a
    performance recap / chart update / reposted third-party milestone alert)
    rather than announcing a fresh call."""
    if not text:
        return False
    tl = text.lower()
    if any(p in tl for p in _RECAP_PHRASES):
        return True
    return any(p.search(text) for p in _RECAP_PATTERNS)

def _get_chain_emoji(chain, config=None):
    """Return chain-specific premium emoji ID, or None."""
    if config is None:
        config = load_config()
    chain_emoji_ids = config.get("chain_emoji_ids", {})
    # Normalize chain key: SOL→sol, ETH→eth, BNB→bsc (user uses 'bsc'), BASE→base
    key_map = {"SOL": "sol", "ETH": "eth", "BNB": "bsc", "BSC": "bsc", "BASE": "base", "TON": "ton"}
    key = key_map.get(chain.upper(), chain.lower())
    return chain_emoji_ids.get(key)


def build_dropped_alert(channel, msg_id, ca, chain, entry_fmt, symbol):
    """Build the 'Dropped a Call' post text."""
    kol_link  = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    dex_path  = CHAIN_TO_DEXPATH.get((chain or "").upper(), "ethereum")
    chart_url = f"https://dexscreener.com/{dex_path}/{ca}" if ca else "https://dexscreener.com"
    mae_link  = f"{MAESTRO_REFLINK_BASE}{ca}{MAESTRO_REF_SUFFIX}" if ca else f"{MAESTRO_REFLINK_BASE}r-wizard_scan"
    config    = load_config()
    ch_key    = (channel or "").lstrip("@").strip().lower()
    # KOL-specific dropped-call template (set via /setkoldroppedtemplate) wins
    # over the global one — lets a Chinese/Vietnamese/Turkish/Russian-tagged KOL's very first
    # "dropped a call" post go out in their own language too, same as the
    # X-range milestone templates. If the KOL is language-tagged (/setkollang),
    # this reads from the SHARED per-language store so one saved template
    # covers every KOL with that tag. Untagged KOLs keep the global/default text.
    _lang     = (config.get("channel_language", {}) or {}).get(ch_key)
    if _lang and _lang != "english":
        template = (config.get("lang_dropped_templates", {}).get(_lang)
                     or config.get("dropped_call_template")
                     or DEFAULT_DROPPED_TEMPLATE)
    else:
        template  = (config.get("channel_dropped_templates", {}).get(ch_key)
                     or config.get("dropped_call_template")
                     or DEFAULT_DROPPED_TEMPLATE)
    kwargs    = dict(
        channel=html.escape(_display_handle(channel)), symbol=(symbol or "TOKEN").upper(),
        chain=chain, entry=_mc_no_label(entry_fmt), ca=ca,
        kol_link=kol_link, bot_link=BOT_LINK,
        chart_url=chart_url, mae_link=mae_link,
    )
    try:
        return safe_format(template, **kwargs)
    except Exception:
        return safe_format(DEFAULT_DROPPED_TEMPLATE, **kwargs)


def _remember_drop_post(channel, ca, post_id, is_media):
    """Store the Wizard-Scan 'Dropped a Call' post id on the tracked call so the
    owner can later fix a late-tracked entry MC with /fixmc and the bot edits
    that exact post."""
    if not post_id or not ca:
        return
    key = f"{channel}_{ca}"
    call = tracked_calls.get(key) or next(
        (v for k, v in tracked_calls.items() if k.lower() == key.lower()), None)
    if call is None:
        return
    call["drop_post_id"]    = int(post_id)
    call["drop_post_media"] = bool(is_media)
    _save_tracked()


async def send_dropped_alert(bot, channel, msg_id, ca, chain, entry_fmt, symbol):
    """Post a 'Dropped a Call' alert to the main channel when a new call is first tracked.
    ALWAYS uses the WHITE emoji pack regardless of the active/locked pack setting.

    Flow: Bot sends (always reliable) → Userbot edits caption with premium emojis.
    No file download/re-upload — avoids silent fallback-to-no-emojis bug.
    """
    try:
        text = build_dropped_alert(channel, msg_id, ca, chain, entry_fmt, symbol)
        # Use chain-specific emoji for the two header 🔮 in dropped-call post
        _chain_key = (chain or "").upper()
        # FIX: EVM unknown chain defaults to ETH emoji, not SOL
        if _chain_key == "EVM":
            _drop_em = DROPPED_CHAIN_EMOJIS.get("ETH", DROPPED_CALL_EMOJI)
        else:
            _drop_em = DROPPED_CHAIN_EMOJIS.get(_chain_key, DROPPED_CALL_EMOJI)
        emoji_ids  = [_drop_em, _drop_em]

        # Rotating video (up to 20 — round robin)
        config = load_config()
        vids   = config.get("dropped_videos", [])
        media  = None
        if vids:
            idx   = config.get("dropped_video_index", 0) % len(vids)
            media = vids[idx]
            cfg_set("dropped_video_index", (idx + 1) % len(vids))
        else:
            # DIAG: this is the #1 cause of "every post is text-only" — the
            # rotation list is simply empty, so media is None before we even
            # try to send anything.
            logger.warning(
                "Dropped alert: NO videos configured (dropped_videos list is "
                "empty) — this call will post text-only.")

        posted   = False
        sent_id  = None
        fail_reason = None  # DIAG: tracks *why* we fell through to text-only

        # ── Step 1: Userbot sends directly (NO edit = NO pencil mark ✏️) ──────────
        if media and media.get("file_id"):
            fid, ftype = media["file_id"], media.get("type", "video")
            if userbot_client:
                for attempt in (1, 2):  # DIAG/FIX: one retry before giving up on media
                    try:
                        sm = await _userbot_send_media_with_emoji(
                            bot, TARGET_CHANNEL, fid, ftype, text, emoji_ids,
                            forced_pack=_DROPPED_CALL_PACK)
                        if sm:
                            posted = True
                            try: _remember_drop_post(channel, ca, sm.id, True)
                            except Exception: pass
                            logger.info(f"Dropped alert media sent by userbot (no pencil): msg_id={sm.id}")
                        break
                    except Exception as e:
                        fail_reason = f"userbot media send failed: {type(e).__name__}: {e}"
                        if attempt == 1:
                            logger.warning(
                                f"Userbot media send failed (attempt 1), retrying once: {e}")
                            await asyncio.sleep(0.25)
                            continue
                        logger.error(f"Userbot media send failed, trying bot fallback: {e}")
            else:
                fail_reason = "userbot_client not connected"
                logger.warning(
                    "Dropped alert: userbot_client is None (disconnected) — "
                    "skipping straight to bot fallback for media.")

            # Fallback: bot sends (no premium emojis, no edit to avoid pencil)
            if not posted:
                try:
                    if ftype == "photo":
                        sm = await bot.send_photo(TARGET_CHANNEL, photo=fid,
                                                  caption=text, parse_mode="HTML")
                    else:
                        sm = await bot.send_video(TARGET_CHANNEL, video=fid,
                                                  caption=text, parse_mode="HTML")
                    posted  = True
                    sent_id = sm.message_id
                    if not sent_id:
                        logger.error(f"Dropped alert bot-fallback returned suspicious message_id={sent_id!r}, raw object: {sm!r}")
                    try: _remember_drop_post(channel, ca, sent_id, True)
                    except Exception: pass
                    logger.info(f"Dropped alert media sent by bot (fallback): msg_id={sent_id}")
                except Exception as e:
                    fail_reason = f"bot media send also failed: {type(e).__name__}: {e}"
                    logger.error(f"Dropped alert media send failed: {e}")

        if not posted:
            if fail_reason:
                logger.warning(f"Dropped alert falling back to TEXT-ONLY — reason: {fail_reason}")
            elif not (media and media.get("file_id")):
                logger.warning("Dropped alert falling back to TEXT-ONLY — reason: no media configured/available")
            # Text-only: userbot sends with emojis in one shot (no edit = no pencil)
            if userbot_client:
                sm = await _userbot_send_with_premium_emoji(
                    TARGET_CHANNEL, text, emoji_id=emoji_ids, forced_pack=_DROPPED_CALL_PACK)
                if sm:
                    posted = True
                    try: _remember_drop_post(channel, ca, sm.id, False)
                    except Exception: pass
                    logger.info("Dropped alert text sent by userbot with premium emojis")
            if not posted:
                try:
                    sm = await bot.send_message(TARGET_CHANNEL, text,
                                                parse_mode="HTML", disable_web_page_preview=True)
                    posted  = True
                    sent_id = sm.message_id
                    try: _remember_drop_post(channel, ca, sent_id, False)
                    except Exception: pass
                except Exception as e:
                    logger.error(f"Dropped alert text send failed: {e}")

        # ── Step 2: Edit only if bot-fallback sent (sent_id set) — pencil acceptable here ──
        # FIX: `sent_id` can legitimately end up as 0 in rare cases (a bad
        # response object) — `if sent_id:` treated that as "no ID" and
        # silently skipped this whole step, leaving the post without premium
        # emoji forever. `is not None` catches 0 too, so we still attempt it.
        if posted and sent_id is not None and userbot_client and emoji_ids:
            await asyncio.sleep(0.1)
            if media and media.get("file_id"):
                ok = await _userbot_edit_caption_with_premium_emoji(
                    TARGET_CHANNEL, sent_id, text, emoji_ids,
                    forced_pack=_DROPPED_CALL_PACK)
            else:
                ok = await _userbot_edit_with_premium_emoji(
                    TARGET_CHANNEL, sent_id, text,
                    forced_pack=_DROPPED_CALL_PACK)
            if not ok:
                logger.warning(f"Dropped alert premium emoji edit failed for msg {sent_id}")

    except Exception as e:
        logger.error(f"send_dropped_alert crash: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# DEDICATED "Dropped a Call" queue — separate from the milestone queue above.
# FIX: dropped-call posts used to share the milestone priority queue with
# RT_PRIO_DROP (5) below RT_PRIO_X (0) — a busy burst of milestone alerts
# (especially now that userbot media/text retries can each take several
# seconds) could keep every worker occupied with milestones, starving a
# waiting dropped-call post well past the owner's 5-second target. Running
# drops on their own queue + worker pool means they are NEVER blocked by
# milestone volume — the two alert types post fully in parallel.
# ═══════════════════════════════════════════════════════════════════════════
_RT_DROP_QUEUE   = None
_RT_DROP_WORKERS = []
RT_DROP_WORKER_COUNT = max(1, int(os.environ.get("RT_DROP_WORKER_COUNT", "4") or 4))
# Pre-post settle window for a fresh drop call (was a fixed 1.5s sleep + up to
# 1.5s cross-check = ~3s on EVERY drop call). Backstops (48s background
# entry-MC confirm + ratio-jump cap) already correct a slightly-early entry.
DROP_SETTLE_SECONDS      = float(os.environ.get("DROP_SETTLE_SECONDS", "0.5") or 0.5)
DROP_CONFIRM_TIMEOUT_SEC = float(os.environ.get("DROP_CONFIRM_TIMEOUT_SEC", "1.0") or 1.0)

def _rt_drop_queue():
    global _RT_DROP_QUEUE
    if _RT_DROP_QUEUE is None:
        _RT_DROP_QUEUE = asyncio.Queue()
    return _RT_DROP_QUEUE

async def _rt_drop_worker_loop(bot):
    q = _rt_drop_queue()
    while True:
        data = await q.get()
        requeued = False
        try:
            age = time.time() - data.get("queued_at", time.time())
            if age > RT_MAX_QUEUE_AGE_SECONDS:
                logger.error(
                    f"⏳ Dropping stale dropped-call post (queued {age:.0f}s ago): "
                    f"{data.get('symbol','?')} @{data.get('channel','?')}")
                continue
            await asyncio.wait_for(send_dropped_alert(
                bot, data["channel"], data["msg_id"], data["ca"],
                data.get("chain", "SOL"), data["entry_fmt"], data["symbol"]),
                timeout=RT_SEND_TIMEOUT_SECONDS)
            logger.info(f"📣 Dropped call posted (real-time, dedicated queue): "
                        f"{data.get('symbol','?')} @{data['channel']}")
        except asyncio.TimeoutError:
            tries = int(data.get("_tries", 0)) + 1
            if tries <= RT_MAX_SEND_RETRIES:
                data["_tries"] = tries
                data["queued_at"] = time.time()
                logger.warning(
                    f"⏱️ drop_worker send timeout ({RT_SEND_TIMEOUT_SECONDS}s) — "
                    f"re-queued (try {tries}/{RT_MAX_SEND_RETRIES}): "
                    f"{data.get('symbol','?')} @{data.get('channel','?')}")
                asyncio.create_task(_rt_drop_queue().put(data))
                requeued = True
            else:
                logger.error(
                    f"⏱️ drop_worker hard-timeout x{tries} — giving up: "
                    f"{data.get('symbol','?')} @{data.get('channel','?')}")
        except Exception as e:
            logger.error(f"drop_worker failed: {e}")
        finally:
            if not requeued:
                _RT_INFLIGHT.discard(f"drop|{data.get('channel')}|{data.get('ca')}")
            q.task_done()

def _rt_ensure_drop_worker(bot):
    global _RT_DROP_WORKERS
    _RT_DROP_WORKERS = [t for t in _RT_DROP_WORKERS if not t.done()]
    while len(_RT_DROP_WORKERS) < RT_DROP_WORKER_COUNT:
        t = asyncio.create_task(_rt_drop_worker_loop(bot),
                                name=f"wizard-realtime-drops-{len(_RT_DROP_WORKERS)+1}")
        _RT_DROP_WORKERS.append(t)

async def rt_enqueue_dropped(bot, channel, msg_id, ca, chain, entry_fmt, symbol):
    """Queue a Dropped-a-Call post the instant the call is detected.
    Uses its own dedicated queue/workers (see _rt_drop_queue above) — never
    waits behind milestone alerts."""
    tag = f"drop|{channel}|{ca}"
    if tag in _RT_INFLIGHT:
        return
    _RT_INFLIGHT.add(tag)
    _rt_ensure_drop_worker(bot)
    await _rt_drop_queue().put({
        "channel": channel, "msg_id": msg_id, "ca": ca,
        "chain": chain, "entry_fmt": entry_fmt, "symbol": symbol,
        "queued_at": time.time(),
    })


async def _process_new_call(bot, channel: str, msg_id, text: str, post_date=None):
    """Process a single new message for a tracked channel.
    Zero blocking — track instantly, dex filled later by milestone_job.
    Returns True if a new call was registered."""
    try:
        channel = channel.lower()   # normalise — prevents same token tracked twice if casing differs
        msg_id_str = str(msg_id)

        # HARD STOP: removed channel ki koi post kabhi process nahi hogi
        # (purani post ka flood aur nayi call dono band).
        if is_channel_removed(channel) or channel not in {c.lower() for c in load_channels()}:
            seen_message_ids[channel].add(msg_id_str)
            return False

        # Mark as seen immediately — no re-processing on next poll
        if msg_id_str in seen_message_ids[channel]:
            return False

        # ── Redeploy anti-spam gate ──────────────────────────────────────────
        # Jab tak startup pre-scan complete nahi hota, boot se PEHLE ki koi bhi
        # post process nahi hogi (sirf seen mark hogi). Boot ke baad ki nayi
        # calls normal chalti rahengi — is liye kuch miss bhi nahi hota.
        if not BOT_READY and post_date:
            try:
                _pd = datetime.fromisoformat(str(post_date).replace("Z", "+00:00")).timestamp()
                if _pd and _pd < BOT_START_TS:
                    seen_message_ids[channel].add(msg_id_str)
                    return False
            except (TypeError, ValueError):
                pass

        # Catch calls posted shortly before a Railway restart. Previously the
        # hard BOT_START_TS-5 cutoff silently discarded every call made while
        # the service was restarting or the userbot was reconnecting.
        #
        # OWNER FIX (2026-09-19, casinoinsiderhere/1099 incident): a call from
        # a SPECIAL channel was silently dropped because the bot had been down
        # ~7 hours before a redeploy — longer than even the old 180-minute
        # SPECIAL_BACKFILL_MINUTES window. A minutes-based cutoff can never be
        # long enough for an unpredictable downtime, so special channels now
        # skip the age cutoff entirely: they always get processed on restart,
        # no matter how old the post is. Deduplication still comes from
        # seen_message_ids, so nothing gets alerted twice. Non-special
        # channels keep the normal time-boxed catch-up window so a long outage
        # doesn't flood the feed with hours of backlog from every channel.
        is_special_channel = channel in set(load_special_channels())
        if post_date and not is_special_channel:
            try:
                parsed_date = datetime.fromisoformat(str(post_date).replace("Z", "+00:00"))
                post_ts = parsed_date.timestamp()
                lookback_minutes = float(os.environ.get("CALL_CATCHUP_MINUTES", "30") or 30)
                if post_ts < BOT_START_TS - max(60.0, lookback_minutes * 60):
                    seen_message_ids[channel].add(msg_id_str)
                    return False
            except (TypeError, ValueError):
                pass
        # Do NOT mark the message as seen yet. A realtime Telethon event can
        # arrive without its hidden URL/entity text, while the polling safety
        # net can see that URL a moment later. Marking it seen here would make
        # the polling pass skip the valid call forever.
        #
        # We mark it as seen only after a valid CA is extracted (below).

        # ── Launchpad (PinkSale / GemPad / CheesePad) detection ──────────────
        # Presale posts ka apna CA nahi hota, is liye ye check normal call
        # gating se pehle chalta hai.
        try:
            lp_info = detect_launchpad(text)
        except Exception as e:
            logger.warning(f"detect_launchpad failed @{channel}/{msg_id_str}: {e}")
            lp_info = None
        if lp_info:
            logger.info(f"🩷 launchpad link detected @{channel}/{msg_id_str}: "
                        f"{lp_info.get('platform')} {lp_info.get('chain')} {lp_info.get('address')}")
            if lp_info.get("platform") == "PinkSale":
                asyncio.create_task(
                    send_pinksale_alert(bot, channel, msg_id_str, lp_info, text))
            elif lp_info.get("platform") == "CheesePad":
                asyncio.create_task(
                    send_cheesepad_alert(bot, channel, msg_id_str, lp_info, text))
            else:
                asyncio.create_task(
                    send_launchpad_alert(bot, channel, msg_id_str, lp_info, text))
        elif text and any(k in text.lower() for k in ("pinksale", "cheesepad", "gempad")):
            logger.warning(f"Launchpad mentioned but no usable link @{channel}/{msg_id_str}: "
                           f"{(text or '')[:200]}")


        # A caller message is accepted when the normal call detector matches OR
        # when a real token CA is present. This keeps tracking alive if a caller
        # changes wording/emoji style while still requiring an actual CA/link.
        result = extract_ca(text) if text else None
        if not result:
            result = extract_ca_from_links(text) if text else None
        if not result:
            return False
        if not is_call_message(text):
            logger.info(f"📡 CA-based call accepted despite wording gate @{channel}/{msg_id_str}")

        chain_guess, ca = result

        # HARD STOP: this CA is globally frozen — refuse it for EVERY channel/
        # caller, not just the ones already tracked when /freezecall ran. This
        # is what makes /freezecall CA (no @channel) actually stick when a
        # different caller posts the same CA afterwards.
        if ca.lower() in load_global_ca_bans():
            seen_message_ids[channel].add(msg_id_str)
            _save_seen()
            logger.info(f"⛔ Globally frozen CA ignored @{channel}/{msg_id_str} ca={ca[:12]}...")
            return False

        # Now that this is a real token call, claim the message so the
        # realtime handler + polling safety-net cannot double-process it.
        seen_message_ids[channel].add(msg_id_str)
        _save_seen()
        call_key = f"{channel}_{ca}"
        if call_key in tracked_calls:
            return False

        if _is_recap_post(text):
            logger.info(f"⏭️ Skipping recap/chart-update post (not a fresh call) "
                        f"@{channel}/{msg_id_str}: {(text or '')[:120]!r}")
            return False

        # FIX (2026-09-26, "$NFX called at 30K+, bot tracked at 6.9K" —
        # persistent RH-chain wrong-entry-MC reports): the 2026-09-08 owner
        # decision above fully disabled the caller's post-text MC out of a
        # real concern (a channel could lowball its own entry to inflate its
        # X multiplier). But the fix went further than the concern required —
        # post_mc_hint was hardcoded to 0 below, so _reconcile_entry_mc (the
        # function written specifically to compare hint vs live and only
        # trust the hint when it ISN'T suspiciously low, see its own
        # docstring) never even got a hint to check. That left "live chart"
        # as the only source, and on brand-new Robinhood-chain pools the
        # first live DexScreener read is frequently a badly-early/partial
        # price (this is what produced 6.9K/8.7K/8.5K-type entries against
        # real 30K-47K launches, every time — never the other direction).
        # Passing the real extracted hint through to post_mc_hint below lets
        # _reconcile_entry_mc do its job: a hint that is HIGHER than or close
        # to live (a genuine early caller) is trusted; a hint far BELOW live
        # (the manipulation case the 2026-09-08 decision was guarding
        # against) is still rejected in favor of live. Best of both.
        post_mc = extract_mc_from_text(text)
        if post_mc <= 0:
            logger.info(f"🧮 MC-hint extraction found NOTHING @{channel}/{msg_id_str} "
                        f"post text: {(text or '')[:200]!r}")
        else:
            logger.info(f"🧮 MC-hint extracted: {fmt_mc(post_mc)} @{channel}/{msg_id_str} "
                        f"(passed to _reconcile_entry_mc — still rejected if it looks "
                        f"like lowballing vs the live chart)")

        # ── Track immediately — NO dex wait ──────────────────────────────────
        # entry data filled in by milestone_job once token appears on Dexscreener
        tracked_calls[call_key] = {
            "channel":      channel,
            "msg_id":       msg_id_str,
            "ca":           ca,
            "chain":        chain_guess,   # "SOL" or "EVM"
            "entry_mc":     0,             # live-chart-only: never seeded from post text
            "entry_price":  0,
            "entry_fmt":    "N/A",
            "entry_locked": False,         # stays open until live data confirms
            "post_mc_hint": post_mc,       # real hint now — _reconcile_entry_mc guards it
            "symbol":       "",
            "tracked_since": datetime.utcnow().isoformat(),
            "dex_pending":  True,          # milestone_job will fill this in
        }
        _hs_register_call(channel, call_key, chain_guess)

        logger.info(f"📌 NEW CALL (instant) @{channel} chain={chain_guess} ca={ca[:12]}...")
        _save_tracked()
        _save_seen()

        # Background task: retry dex until we get data, THEN send dropped alert
        async def _fetch_and_alert():
            call_key_inner = f"{channel}_{ca}"
            dex = None

            # ── REAL-TIME retry schedule (SpyDefi/Kolscope style) ───────────────
            # Phase 1: 12 attempts × 0.25 s = ~3 s (already-indexed token → alert <1 s)
            # Phase 2: 15 attempts × 1 s    = 15 s  (fresh launches)
            # Phase 3: 20 attempts × 4 s    = 80 s  (slow BSC/Base indexing)
            # Phase 4: 12 attempts × 15 s   = 180 s (last resort, still no N/A post)
            # Dense first ~10 s so a dropped-call alert lands within seconds.
            retry_schedule = [(25, 0.2), (10, 0.5), (15, 1), (18, 4), (10, 15)]
            _call_detected_at = _t.time()

            def _is_full(d):
                return bool(d and (d.get("mcap") or d.get("price")) and d.get("symbol"))

            # SPEED FIX (2026-10-07): previously a token that DexScreener knew only
            # partially (symbol but no MC, or MC but no symbol — common on EVM) kept
            # the loop below spinning through EVERY phase (4+ minutes) before the
            # Jupiter/Gecko/Moralis fallback was ever tried. Now the fallback runs
            # right after the first two phases (~8s) and again after each later one,
            # and the loop stops as soon as it completes the data.
            async def _early_fallback():
                nonlocal dex
                try:
                    _rc = (dex.get("chain") if dex and dex.get("chain") else chain_guess)
                    fb = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_token_info_fallback_sync, ca, _rc), timeout=4)
                except Exception:
                    return
                if not fb:
                    return
                if dex is None:
                    dex = {"chain": _rc, "mcap": 0, "mcap_fmt": "N/A", "price": 0, "symbol": ""}
                if fb.get("symbol") and not dex.get("symbol"):
                    dex["symbol"] = fb["symbol"]
                if fb.get("mcap", 0) > 0 and not dex.get("mcap", 0) > 0:
                    dex["mcap"] = fb["mcap"]
                    dex["mcap_fmt"] = fb.get("mcap_fmt") or fmt_mc(fb["mcap"])
                    if fb.get("price") and not dex.get("price"):
                        dex["price"] = fb["price"]
                if fb.get("chain") and dex.get("chain") in (chain_guess, "EVM", None):
                    dex["chain"] = fb["chain"]

            _phase_no = 0
            for phase_count, phase_sleep in retry_schedule:
                if _is_full(dex):
                    break  # complete data already found in a previous phase
                if _phase_no >= 2:
                    await _early_fallback()
                    if _is_full(dex):
                        break
                _phase_no += 1
                for _attempt in range(phase_count):
                    try:
                        # A brand-new call must never read an older cached quote.
                        # Bypass the short Dex cache on every retry so the first
                        # usable MC/price is captured as soon as the token indexes.
                        _invalidate_dex_cache(ca)
                        fetched = await asyncio.wait_for(
                            asyncio.to_thread(_fetch_entry_sync, ca, chain_guess, 0),  # Birdeye-first, min_liquidity=0
                            timeout=3)
                        if fetched:
                            if fetched.get("mcap") or fetched.get("price"):
                                dex = fetched
                                if _is_full(dex):
                                    break
                            elif fetched.get("symbol") and not dex:
                                dex = fetched  # partial; may be improved later
                    except Exception:
                        pass
                    if _is_full(dex):
                        break
                    await asyncio.sleep(phase_sleep)
                if _is_full(dex):
                    break

            # ── Fallback: use Jupiter / Pump.fun / GeckoTerminal ───────────────
            # Run fallback when ANY of these is missing:
            #   • no data at all (token not on DexScreener yet)
            #   • no symbol (token name missing)
            #   • no market cap (EVM tokens often have price but mc=null on DexScreener)
            has_symbol  = bool(dex and dex.get("symbol"))
            has_mc      = bool(dex and dex.get("mcap", 0) > 0)
            has_price   = bool(dex and dex.get("price", 0) > 0)
            # Pass the already-resolved chain (e.g. "ETH","BNB","BASE") if available,
            # so the fallback targets the right network immediately.
            resolved_chain = (dex.get("chain") if dex and dex.get("chain") else chain_guess)
            if not has_symbol or not has_mc:
                try:
                    fallback = await asyncio.to_thread(
                        _fetch_token_info_fallback_sync, ca, resolved_chain)
                    if fallback:
                        if dex is None:
                            dex = {"chain": resolved_chain, "mcap": 0, "mcap_fmt": "N/A",
                                   "price": 0, "symbol": ""}
                        # Fill ONLY what is missing — never overwrite good existing data
                        if fallback.get("symbol") and not dex.get("symbol"):
                            dex["symbol"] = fallback["symbol"]
                        if fallback.get("mcap", 0) > 0 and not dex.get("mcap", 0) > 0:
                            dex["mcap"]     = fallback["mcap"]
                            dex["mcap_fmt"] = fallback.get("mcap_fmt") or fmt_mc(fallback["mcap"])
                        if fallback.get("chain") and dex.get("chain") in (chain_guess, "EVM", None):
                            dex["chain"] = fallback["chain"]
                        logger.info(
                            f"🔍 Fallback applied: sym={dex.get('symbol')} "
                            f"mc={dex.get('mcap_fmt')} chain={dex.get('chain')} @{channel}")
                except Exception as fe:
                    logger.warning(f"Fallback apply error: {fe}")

            # ── Determine final values ──────────────────────────────────────────
            if dex and (dex.get("mcap") or dex.get("price")):
                # Full data: update tracked call and alert
                if call_key_inner in tracked_calls:
                    _c_upd = tracked_calls[call_key_inner]
                    _c_upd.update({
                        "symbol":      dex.get("symbol", ""),
                        "chain":       dex.get("chain", chain_guess),
                        "dex_pending": False,
                    })
                    # FIX (2026-09-26, "$FARTCOIN posted with a totally wrong
                    # CA" report): when a KOL posts only a DexScreener chart
                    # link (no plain contract line), extract_ca grabs the
                    # PAIR/POOL address from that URL, not the token mint —
                    # _fetch_dex_sync already resolves this correctly behind
                    # the scenes (attaching it as dex["resolved_ca"], see the
                    # "PAIR-ADDRESS FALLBACK" above) and the symbol/MC shown
                    # were already right, but nothing ever copied the
                    # resolved mint back onto the tracked call — so the
                    # "Ca:" line in every alert kept showing the pool
                    # address instead of the real, tradable token address.
                    _resolved_ca = dex.get("resolved_ca")
                    if _resolved_ca and _resolved_ca.lower() != _c_upd.get("ca", "").lower():
                        logger.info(f"🔧 Correcting stored CA to resolved token mint "
                                    f"@{channel}: {_c_upd.get('ca','')[:10]}… → {_resolved_ca[:10]}…")
                        _c_upd["ca"] = _resolved_ca
                    # Entry MC = KOL's stated MC when it is plausible vs the
                    # live chart, otherwise the live chart MC. This kills the
                    # "called at 15K, tracked at 25K" drift without trusting a
                    # wrong number written in the post.
                    if not _c_upd.get("entry_locked"):
                        _live_mc = dex.get("mcap", 0) or 0
                        _hint_mc = _c_upd.get("post_mc_hint", 0) or 0
                        # FIX (2026-09-18, "called at 16K, bot shows 80K" reports):
                        # _age used to be time.time() - _call_detected_at, i.e. how
                        # long OUR OWN retry loop had been running — that's near-zero
                        # even when the underlying Telegram post itself was detected
                        # minutes late (scan gap/flood-wait). So the freshness gate
                        # almost never fired, and a genuinely-early caller's real MC
                        # kept getting outvoted by an already-pumped live price via
                        # _reconcile_entry_mc's anti-cheat path. Use the actual post
                        # timestamp so a late-detected call is correctly recognized
                        # as stale and the caller's stated MC is trusted directly.
                        try:
                            _post_dt = datetime.fromisoformat(str(post_date)) if post_date else None
                            _age = (datetime.utcnow() - _post_dt.replace(tzinfo=None)).total_seconds() if _post_dt else (_t.time() - _call_detected_at)
                        except Exception:
                            _age = _t.time() - _call_detected_at
                        # FIX (2026-10-11, FLW "103K call, bot said 45K, instant 2X"):
                        # on RH, DexScreener can index a token minutes late, so the
                        # 'age > fresh => trust post hint' shortcut then paired an OLD
                        # (or since-edited) hint with a live price already >=2x higher,
                        # firing a fake instant 2X. For RH, when live is already >=2x
                        # the hint, skip the shortcut and let _reconcile_entry_mc
                        # (anti-lowball + confirm retries) decide instead.
                        _rh_stale_hint = (str((dex or {}).get("chain") or chain_guess).upper() == "RH" and _hint_mc > 0
                                          and _live_mc >= _hint_mc * 2
                                          and not dex.get("_mc_is_fdv"))
                        if _hint_mc > 0 and _age > ENTRY_MC_FRESH_SECONDS and not _rh_stale_hint:
                            # quote arrived too late to be "the entry"
                            _entry_mc, _src = (_hint_mc, "post")
                        else:
                            _entry_mc, _src = _reconcile_entry_mc(
                                _hint_mc, _live_mc, live_is_fdv=bool(dex.get("_mc_is_fdv")))
                        logger.info(
                            f"🧮 entry-MC reconcile (real-time) @{channel}: "
                            f"post_hint={fmt_mc(_hint_mc)} live={fmt_mc(_live_mc)} "
                            f"fdv={bool(dex.get('_mc_is_fdv'))} age={_age:.1f}s "
                            f"→ chosen={fmt_mc(_entry_mc)} src={_src}")
                        # Pre-post confirmation: a "live" entry (no usable hint from
                        # the caller's post) can be read mid-index, straight off a
                        # pool that hasn't settled yet — this is what was producing
                        # wrong MCs in the posted alert itself (e.g. 33K posted when
                        # the real entry was 100K). Rather than posting immediately
                        # and silently correcting the internal record 48s later
                        # (which never touched the message already in the channel),
                        # do one short synchronous re-check here so the FIRST number
                        # anyone sees is already the corrected one. This adds ~2-3s
                        # to "instant" alerts for live-priced calls only — calls with
                        # a usable MC hint from the post (src="post") are unaffected
                        # and still post with zero delay.
                        _entry_confirmed = _src not in ("live", "live_fdv", "post_unconfirmed")
                        # FIX (2026-09-30, BABYBUN/@spawnscalls 6.9K-vs-40K incident): RH-only
                        # safeguards below keyed off chain_guess, which is just "EVM" at first
                        # sight of an 0x address — so RH tokens skipped the strict pre-post
                        # confirm loop and locked a half-indexed 6.9K read as entry. Use the chain
                        # dex data has ALREADY resolved (RH/ETH/BNB/BASE) whenever it is known.
                        _eff_chain = chain_guess
                        try:
                            _dc = (dex or {}).get("chain")
                            if _dc and str(_dc).upper() != "EVM":
                                _eff_chain = str(_dc).upper()
                        except Exception:
                            pass
                        _storm_at_detect = _dex_breaker_open()
                        if _src in ("live", "live_fdv", "post_unconfirmed") and _entry_mc > 0:
                            try:
                                if _eff_chain == "RH":
                                    # OWNER REQUIREMENT (2026-09-24): "RH tokens
                                    # should track late — don't post the dropped-
                                    # call alert until the real MC is confirmed."
                                    # RH-chain pairs commonly have NO real
                                    # circulating-MC field anywhere for a while
                                    # after a call (only FDV — see
                                    # _pick_market_cap), so the old single
                                    # ~9s settle window usually still had only
                                    # an FDV/hint number to offer, and the alert
                                    # posted on that anyway with a background
                                    # task correcting it (silently) afterwards.
                                    # Now: keep retrying HERE, before anything
                                    # is posted, until an independent source
                                    # (_confirm_mc_cross_check, which now also
                                    # tries GeckoTerminal — Birdeye doesn't
                                    # cover RH at all) returns a real, non-FDV
                                    # MC, or a hard ceiling is hit so a token
                                    # that can never be confirmed doesn't hang
                                    # forever.
                                    # SPEED FIX (2026-10-07): the full ~80s wait made every RH
                                    # drop call post ~1 min late. Cap the pre-post wait with
                                    # RH_PRE_POST_MAX_SECONDS (default 15s). Set it to 80 on
                                    # Railway to restore the old strict behaviour. If the entry
                                    # is still unconfirmed after the cap, the post goes out
                                    # best-effort and the background correction keeps running.
                                    # RH FIX (2026-10-11, CHNCHL 3.5K-vs-6M incident): RH drop calls now WAIT
                                    # before posting until a REAL (non-FDV) MC is seen on TWO consecutive reads
                                    # that agree within 25%. Never before RH_PRE_POST_MIN_SECONDS (default 10s),
                                    # never longer than RH_PRE_POST_MAX_SECONDS (default 60s). Other chains are
                                    # untouched (fast path below).
                                    _rh_min = float(os.environ.get("RH_PRE_POST_MIN_SECONDS", "10") or 10)
                                    _rh_cap = float(os.environ.get("RH_PRE_POST_MAX_SECONDS", "60") or 60)
                                    _rh_cap = max(_rh_cap, _rh_min)
                                    _rh_steps = (2, 3, 4, 5, 5, 5)
                                    _rh_t0 = _t.time()
                                    _pre_confirm, _pre_cmc = None, 0.0
                                    _prev_real, _rh_i, _rh_ok = 0.0, 0, False
                                    while True:
                                        _el = _t.time() - _rh_t0
                                        if _el >= _rh_cap:
                                            break
                                        _wd = _rh_steps[_rh_i] if _rh_i < len(_rh_steps) else 5
                                        _rh_i += 1
                                        await asyncio.sleep(min(_wd, max(0.0, _rh_cap - _el)))
                                        _invalidate_dex_cache(ca)
                                        try:
                                            _pre_confirm = await asyncio.wait_for(
                                                asyncio.to_thread(_confirm_mc_cross_check, ca, _eff_chain),
                                                timeout=8)
                                        except Exception:
                                            _pre_confirm = None
                                        _pre_cmc = (_pre_confirm or {}).get("mcap", 0) or 0
                                        _real = _pre_cmc if (_pre_cmc > 0 and not (_pre_confirm or {}).get("_mc_is_fdv")) else 0.0
                                        if _real > 0:
                                            if (_prev_real > 0
                                                    and max(_real, _prev_real) / min(_real, _prev_real) <= 1.25
                                                    and (_t.time() - _rh_t0) >= _rh_min):
                                                _rh_ok = True
                                                _entry_confirmed = True
                                                break
                                            _prev_real = _real
                                        else:
                                            _prev_real = 0.0
                                    if not _rh_ok:
                                        logger.warning(
                                            f"⚠️ RH entry NOT confirmed (2 agreeing real-MC reads) within "
                                            f"{_rh_cap:.0f}s @{channel} — posting best-effort, background correction continues")
                                    if _pre_cmc > 0 and not (_pre_confirm or {}).get("_mc_is_fdv") and (
                                            (_rh_ok and abs(_pre_cmc - _entry_mc) > _entry_mc * 0.08)
                                            or _pre_cmc > _entry_mc * 1.08):
                                        logger.info(
                                            f"🎯 RH pre-post MC confirm (src={(_pre_confirm or {}).get('_source','dex')}, "
                                            f"confirmed={_rh_ok}): {fmt_mc(_entry_mc)} → {fmt_mc(_pre_cmc)} @{channel}")
                                        dex["price"] = _pre_confirm.get("price", dex.get("price", 0))
                                        _entry_mc = _pre_cmc
                                else:
                                    # OWNER REQUIREMENT (2026-09-20): target 3-4s
                                    # total for the drop-call post. Tightened
                                    # further from the 3s+2s (~5s worst case)
                                    # window to 1.5s+1.5s (~3s worst case). This
                                    # is safe to tighten now because two stronger
                                    # backstops exist independently of this step:
                                    # the 48s background _confirm_entry_mc task
                                    # below, and the new universal ratio-jump cap
                                    # in _verified_live_ratio() that rejects any
                                    # future milestone tick implying a >20x jump
                                    # over the call's last confirmed peak — so a
                                    # slightly-off entry from a shorter settle
                                    # window can no longer cascade into a fake
                                    # milestone burst the way it used to.
                                    # OWNER REQUIREMENT (2026-10-11): non-RH chains must post NOT before
                                    # POST_MIN_SECONDS (default 5s) and NOT after POST_MAX_SECONDS (default 10s) from
                                    # detection, with the MC verified against fresh independent reads in that window.
                                    _nr_min = float(os.environ.get("POST_MIN_SECONDS", "5") or 5)
                                    _nr_max = max(float(os.environ.get("POST_MAX_SECONDS", "10") or 10), _nr_min)
                                    _nr_reads, _nr_i = [], 0
                                    while True:
                                        _el = _t.time() - _call_detected_at
                                        if _el >= _nr_max - 1.0:
                                            break
                                        await asyncio.sleep(min(2.0 if _nr_i == 0 else 2.5, max(0.0, _nr_max - 1.0 - _el)))
                                        _nr_i += 1
                                        _invalidate_dex_cache(ca)
                                        _tleft = max(1.0, _nr_max - (_t.time() - _call_detected_at) - 0.5)
                                        try:
                                            _pc = await asyncio.wait_for(
                                                asyncio.to_thread(_confirm_mc_cross_check, ca, _eff_chain),
                                                timeout=min(4.0, _tleft))
                                        except Exception:
                                            _pc = None
                                        _pm = (_pc or {}).get("mcap", 0) or 0
                                        if _pm > 0 and not (_pc or {}).get("_mc_is_fdv"):
                                            _nr_reads.append((_pm, _pc))
                                            if len(_nr_reads) >= 2 and (_t.time() - _call_detected_at) >= _nr_min:
                                                _a, _b = _nr_reads[-1][0], _nr_reads[-2][0]
                                                if max(_a, _b) / min(_a, _b) <= 1.10:
                                                    break   # two fresh reads agree and the 5s floor has passed
                                    if _nr_reads:
                                        _pre_cmc, _pre_confirm = max(_nr_reads, key=lambda x: x[0])
                                        _entry_confirmed = True
                                        if _pre_cmc > _entry_mc * 1.08:
                                            logger.info(
                                                f"🎯 Pre-post MC confirm (src={(_pre_confirm or {}).get('_source','dex')}): "
                                                f"{fmt_mc(_entry_mc)} → {fmt_mc(_pre_cmc)} @{channel}")
                                            dex["price"] = _pre_confirm.get("price", dex.get("price", 0))
                                            _entry_mc = _pre_cmc
                            except Exception as _e_precheck:
                                logger.warning(f"Pre-post MC confirm skipped: {_e_precheck}")
                        if _eff_chain != "RH":
                            _floor_left = float(os.environ.get("POST_MIN_SECONDS", "5") or 5) - (_t.time() - _call_detected_at)
                            if _floor_left > 0:
                                await asyncio.sleep(_floor_left)
                        _entry_price = dex.get("price", 0)
                        if _src in ("post", "post_unconfirmed") and _live_mc > 0 and _entry_price and _entry_mc > 0:
                            _entry_price = _entry_price * (_entry_mc / _live_mc)
                        _c_upd.update({
                            "entry_mc":    _entry_mc,
                            "entry_price": _entry_price,
                            "entry_fmt":   fmt_mc(_entry_mc) if _entry_mc > 0
                                           else dex.get("mcap_fmt", "N/A"),
                            "entry_src":   _src,
                            "entry_locked": True,
                            # Milestone alerts must not fire off an unconfirmed
                            # FDV-derived guess (see _reconcile_entry_mc) — the
                            # monitoring tick checks this before posting any X.
                            "entry_confirmed": _entry_confirmed,
                        })
                        # FIX: no MC in the caller's post means nothing to sanity-check
                        # the live quote against, and the fast real-time path can grab
                        # a quote within milliseconds of the call — catching a pool
                        # mid-index and reading far BELOW the token's real price (e.g.
                        # 56K locked in as "entry" when the token was already trading
                        # at 300K, manufacturing a fake 5X out of nothing a few minutes
                        # later once the ratio "catches up"). Schedule several
                        # confirmation re-fetches spread over ~48s (one snapshot isn't
                        # enough — slower-indexing chains like BSC/Base can still be
                        # settling well past 3s) and patch the entry up whenever a
                        # higher, more-settled read appears. This runs AFTER the alert
                        # already posted, so it never delays the "instant" dropped-call
                        # alert. Since indexing lag only ever biases LOW, never high,
                        # taking the highest of these reads can never inflate the
                        # caller's own X in their favor, so this introduces no new
                        # fraud risk.
                        if _src in ("live", "live_fdv", "post_unconfirmed") and _entry_mc > 0 and not _entry_confirmed:
                            async def _confirm_entry_mc(_key=call_key_inner, _ca=ca,
                                                         _first_mc=_entry_mc, _chan=channel,
                                                         _chg=_eff_chain, _storm=_storm_at_detect):
                                _best_mc = _first_mc
                                _tighter = _storm and _chg != "RH"
                                # RH already ran an ~80s retry-until-confirmed loop
                                # before posting (see above) — only reaches here if
                                # that loop hit its ceiling, so keep trying with
                                # wider spacing rather than giving up.
                                _delays = ((30, 30, 30, 60, 60) if _chg == "RH" else
                                           (2, 3, 5, 8, 10, 15) if _tighter else
                                           (3, 5, 10, 15, 15))
                                _rev_th = 1.08 if (_tighter or _chg == "RH") else 1.15
                                for _delay in _delays:  # ~3s..48s cumulative (RH: faster, tighter)
                                    await asyncio.sleep(_delay)
                                    _cc = tracked_calls.get(_key)
                                    _src_now = (_cc or {}).get("entry_src")
                                    if (not _cc
                                            or (_src_now not in ("live", "live_fdv", "post_unconfirmed")
                                                and not (_chg == "RH" and _src_now == "post"))):
                                        return  # entry was overridden elsewhere — stop
                                    try:
                                        _invalidate_dex_cache(_ca)
                                        _confirm = await asyncio.wait_for(
                                            asyncio.to_thread(_confirm_mc_cross_check, _ca, _chg),
                                            timeout=8)
                                    except Exception:
                                        continue
                                    # Only treat this as a REAL confirmation when the
                                    # cross-check itself wasn't just another FDV
                                    # fallback (see _pick_market_cap / _better_mc_
                                    # result) — an FDV-flagged "confirm" is exactly
                                    # the unverified guess this whole mechanism
                                    # exists to catch, not evidence against it.
                                    if (_confirm or {}).get("mcap", 0) and not (_confirm or {}).get("_mc_is_fdv"):
                                        _cc["entry_confirmed"] = True
                                        _cmc = (_confirm or {}).get("mcap", 0) or 0
                                        if _cmc > _best_mc * _rev_th:  # meaningful upward correction only
                                            _best_mc = _cmc
                                            _cc["entry_mc"]    = _cmc
                                            _cc["entry_fmt"]   = fmt_mc(_cmc)
                                            _cc["entry_price"] = _confirm.get("price", _cc.get("entry_price", 0))
                                            _save_tracked()
                                            logger.info(
                                                f"📈 Entry MC revised up (indexing lag, "
                                                f"src={_confirm.get('_source','dex')}): "
                                                f"{fmt_mc(_first_mc)} → {fmt_mc(_cmc)} @{_chan}")
                                # Fail-safe: even if every cross-check attempt raised
                                # (network trouble etc.) and entry_confirmed never
                                # got set above, never leave a call permanently gated
                                # — that would silently swallow every future X for it.
                                _cc = tracked_calls.get(_key)
                                if _cc is not None:
                                    if (not _cc.get("entry_confirmed")
                                            and _cc.get("entry_src") in ("live", "live_fdv", "post_unconfirmed")):
                                        # confirmed by exhaustion, not by evidence — tag it so
                                        # the early-rebase guard can still correct a bad quote
                                        _cc["entry_src"] = "unverified_fail_safe"
                                    _cc["entry_confirmed"] = True
                            asyncio.create_task(_confirm_entry_mc())

                _save_tracked()
                final_chain  = dex.get("chain", chain_guess)
                final_fmt    = (tracked_calls.get(call_key_inner, {}).get("entry_fmt")
                                if tracked_calls.get(call_key_inner, {}).get("entry_locked")
                                else None) or dex.get("mcap_fmt", "N/A")
                final_symbol = dex.get("symbol", "")
                logger.info(f"📊 dex filled: {final_symbol} {final_fmt} @{channel}")
            elif dex and dex.get("symbol"):
                # Partial data: token is on DEX but price/mc not indexed yet.
                # Still update the symbol so it shows correctly in the alert.
                if call_key_inner in tracked_calls:
                    tracked_calls[call_key_inner].update({
                        "symbol": dex.get("symbol", ""),
                        "chain":  dex.get("chain", chain_guess),
                    })
                _save_tracked()
                final_chain  = dex.get("chain", chain_guess)
                final_fmt    = "N/A"
                final_symbol = dex.get("symbol", "")
                logger.info(f"📊 partial dex: sym={final_symbol} (mc pending) @{channel}")
            else:
                # NO usable data at all → do NOT post an "N/A" alert.
                # The call stays tracked (dex_pending) and monitoring_job will
                # fill it in + post the dropped alert the moment data appears.
                logger.warning(f"⏭️ Alert skipped (no dex data yet, will auto-post later): {ca[:12]}...")
                if call_key_inner in tracked_calls:
                    tracked_calls[call_key_inner]["dex_pending"]  = True
                    tracked_calls[call_key_inner]["alert_pending"] = True
                    _save_tracked()
                return

            # Never post an alert with missing market cap / symbol
            if (not final_fmt or final_fmt == "N/A") or not final_symbol:
                logger.warning(f"⏭️ Alert deferred (incomplete data): {ca[:12]}...")
                if call_key_inner in tracked_calls:
                    tracked_calls[call_key_inner]["alert_pending"] = True
                    _save_tracked()
                return

            # Real-time FIFO: posted immediately, in detection order, never
            # batched behind other channels' posts.
            await rt_enqueue_dropped(
                bot, channel, msg_id_str, ca, final_chain, final_fmt, final_symbol)

        asyncio.create_task(_fetch_and_alert())
        return True
    except Exception as e:
        logger.error(f"_process_new_call @{channel}: {e}")
        return False


_RT_NEW_HANDLER = None
_RT_DELETED_HANDLER = None
# Channels the userbot has actually joined/resolved (realtime events arrive for
# these). scan_job polls them only slowly as a backstop, so GetHistory flood
# waits stop starving the realtime path.
_RT_COVERED = set()
_RT_COVERED2 = set()      # channels covered by the optional 2nd userbot
userbot_client2 = None    # optional 2nd account: MONITORING ONLY (never sends)

async def setup_realtime_monitoring(bot):
    """Register a Telethon event handler so new KOL posts are tracked instantly (< 2 sec).
    Falls back to the 15s polling loop if userbot is not connected.

    Crash-safe: all Telethon calls wrapped so any Telethon internal error (v1.44.0 bugs,
    RuntimeError: Event loop is closed, NoneType await etc.) only logs a warning and
    returns — it never propagates and crashes the Railway process.
    """
    global _bot_ref, userbot_client
    _bot_ref = bot
    if not userbot_client:
        logger.info("Realtime monitoring: no userbot — using polling fallback only")
        return

    # Guard: if userbot is connected but not authorized, skip silently
    try:
        if not await asyncio.wait_for(userbot_client.is_user_authorized(), timeout=10):
            logger.warning("Realtime monitoring: userbot not authorized — skipping")
            return
    except Exception as e:
        logger.warning(f"Realtime monitoring: auth check failed ({e}) — skipping")
        return

    try:
        from telethon import events

        channels = load_channels()
        if not channels:
            logger.info("Realtime monitoring: no channels tracked yet")
            return

        # ── Auto-join + resolve channel entities ─────────────────────────────
        # Telethon only fires NewMessage events for channels the userbot HAS JOINED.
        # get_entity() resolves but does NOT join. JoinChannelRequest joins.
        # SPEED FIX (2026-10-07): this loop used to be strictly sequential
        # (a JoinChannelRequest + get_entity round-trip PER channel). With a few
        # hundred channels it ran past the 90s startup-step timeout, the step was
        # cancelled BEFORE the NewMessage handler was registered, and the bot
        # silently fell back to slow polling (calls tracked ~2 min late, no
        # deleted-post events). Now: resolve concurrently, and only send a
        # join request for channels the userbot has not actually joined.
        from telethon.tl.functions.channels import JoinChannelRequest
        _t0_rt = time.time()
        _rt_sem = asyncio.Semaphore(int(os.environ.get("RT_RESOLVE_CONCURRENCY", "8") or 8))

        async def _resolve_one(ch):
            async with _rt_sem:
                ent = None
                try:
                    ent = await asyncio.wait_for(userbot_client.get_entity(ch), timeout=10)
                except Exception:
                    ent = None
                try:
                    if ent is None or getattr(ent, "left", False):
                        try:
                            await asyncio.wait_for(userbot_client(JoinChannelRequest(ch)), timeout=15)
                        except Exception:
                            pass  # already joined or public access
                        ent = await asyncio.wait_for(userbot_client.get_entity(ch), timeout=10)
                    if ent is not None and not getattr(ent, "left", False):
                        _RT_COVERED.add(str(ch).lstrip("@").lower())
                    return ent
                except Exception as e:
                    logger.warning(f"Realtime: could not resolve/join @{ch}: {e}")
                    return None

        # FIX (2026-10-07, dropped call ~2 min late): the NewMessage handler used to be
        # registered only AFTER every channel was resolved and was bound to that static
        # list, so (a) any channel that failed/hung resolving never got realtime events
        # (only slow round-robin polling = 1-2 min late) and (b) a slow resolve delayed
        # the handler for everybody. Now: resolve with a time budget, register the
        # handler regardless, and make it listen to ALL channel posts (it already
        # filters by tracked username). Unresolved channels keep resolving in the background.
        # FIX (2026-10-11, "calls detected 1-2 min late"): the 12:30 deploy resolved only
        # 31/71 channels in the 40s budget (earlier healthy deploys: 71/71 in ~3s). The
        # session is a StringSession, so the entity cache starts EMPTY on every restart
        # and each get_entity(@username) becomes a rate-limited ResolveUsername call.
        # Channels that did not resolve in time were only caught by the ~2 min polling.
        # One get_dialogs() call fills the cache for every joined channel at once, so the
        # per-channel get_entity() below is answered from cache (no flood-wait).
        try:
            _t_dlg = time.time()
            _dl = await asyncio.wait_for(
                userbot_client.get_dialogs(limit=None),
                timeout=float(os.environ.get("RT_DIALOGS_TIMEOUT_SECONDS", "25") or 25))
            logger.info(f"Realtime: dialogs warmup loaded {len(_dl)} dialogs in {time.time()-_t_dlg:.1f}s")
        except Exception as _e_dlg:
            logger.warning(f"Realtime: dialogs warmup skipped ({type(_e_dlg).__name__}: {_e_dlg})")
        _res_tasks = [asyncio.ensure_future(_resolve_one(c)) for c in channels]
        _budget = float(os.environ.get("RT_RESOLVE_BUDGET_SECONDS", "40") or 40)
        _done, _pending = await asyncio.wait(_res_tasks, timeout=_budget)
        resolved = []
        for _t in _done:
            try:
                _e = _t.result()
                if _e is not None:
                    resolved.append(_e)
            except Exception:
                pass
        logger.info(f"Realtime: resolved {len(resolved)}/{len(channels)} channels in "
                    f"{time.time()-_t0_rt:.1f}s ({len(_pending)} still resolving in background)")
        if not resolved:
            logger.warning("Realtime monitoring: could not resolve any channels yet — "
                           "registering catch-all handler anyway")

        # Remove only our own previous realtime handlers. Do NOT clear every
        # Telethon handler: that can break the userbot login/update pipeline.
        global _RT_NEW_HANDLER, _RT_DELETED_HANDLER
        try:
            if _RT_NEW_HANDLER is not None:
                userbot_client.remove_event_handler(_RT_NEW_HANDLER)
            if _RT_DELETED_HANDLER is not None:
                userbot_client.remove_event_handler(_RT_DELETED_HANDLER)
        except Exception:
            pass

        _RT_ID_USER = globals().setdefault("_RT_ID_USER", {})
        try:
            from telethon import utils as _tl_u0
            for _ent0 in resolved:
                _u0 = (getattr(_ent0, "username", None) or "").lower()
                if _u0:
                    _RT_ID_USER[_tl_u0.get_peer_id(_ent0)] = _u0
                    _RT_ID_USER[getattr(_ent0, "id", 0)] = _u0
        except Exception:
            pass

        @userbot_client.on(events.NewMessage(func=lambda e: bool(getattr(e, "is_channel", False))))
        async def _realtime_handler(event):
            global _bot_ref
            try:
                if _bot_ref is None:
                    return
                _cid = getattr(event, "chat_id", None)
                username = _RT_ID_USER.get(_cid)
                if username is None:
                    chat = await event.get_chat()
                    username = (getattr(chat, 'username', None) or "").lower()
                    _RT_ID_USER[_cid] = username
                channels_now = [c.lower() for c in load_channels()]
                if username not in channels_now:
                    return
                _RT_COVERED.add(username)
                text   = event.raw_text or event.text or ""
                # Telegram "Chart / Buy / DEX" buttons can contain a hidden URL.
                # event.text alone does NOT include MessageEntityTextUrl URLs.
                # Append those URLs before processing so extract_ca_from_links()
                # can see the real CA. This is critical for realtime detection.
                try:
                    ents = getattr(event.message, "entities", None) or []
                    from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
                    for ent in ents:
                        if isinstance(ent, MessageEntityTextUrl):
                            if getattr(ent, "url", None):
                                text += " " + ent.url
                        elif isinstance(ent, MessageEntityUrl):
                            raw_msg = getattr(event.message, "message", None) or ""
                            off = int(getattr(ent, "offset", 0) or 0)
                            ln = int(getattr(ent, "length", 0) or 0)
                            if raw_msg and ln > 0:
                                text += " " + raw_msg[off:off + ln]
                except Exception as _entity_e:
                    logger.debug(f"Realtime entity URL extraction skipped: {_entity_e}")

                msg_id = str(event.id)
                if msg_id in seen_message_ids[username]:
                    return
                # NOTE: do NOT add to seen here — _process_new_call owns that state
                await _process_new_call(_bot_ref, username, msg_id, text, getattr(event, "date", None))
            except (RuntimeError, TypeError) as e:
                # Telethon v1.44.0 internal errors — log only, don't re-raise
                logger.debug(f"Realtime handler Telethon noise: {e}")
            except Exception as e:
                logger.error(f"Realtime handler: {e}")

        _RT_NEW_HANDLER = _realtime_handler

        # peer-id -> username map, so a delete event is matched even when
        # event.get_chat() returns nothing (common for MessageDeleted in channels).
        _id_to_user = {}
        try:
            from telethon import utils as _tl_utils
            for _ent in resolved:
                _u = (getattr(_ent, "username", None) or "").lower()
                if _u:
                    _id_to_user[_tl_utils.get_peer_id(_ent)] = _u
                    _id_to_user[getattr(_ent, "id", 0)] = _u
        except Exception as _e_map:
            logger.warning(f"deleted-handler id map failed: {_e_map}")

        @userbot_client.on(events.MessageDeleted(chats=resolved or None))
        async def _deleted_post_handler(event):
            """Penalize a KOL once and stop a deleted call from producing alerts."""
            try:
                deleted_ids = {str(mid) for mid in (event.deleted_ids or [])}
                if not deleted_ids:
                    return
                username = _id_to_user.get(getattr(event, "chat_id", None), "")
                if not username:
                    try:
                        chat = await event.get_chat()
                        username = (getattr(chat, "username", None) or "").lower()
                    except Exception:
                        username = ""
                logger.info(f"🗑 MessageDeleted event: chat={getattr(event, 'chat_id', None)} "
                            f"@{username or '?'} ids={sorted(deleted_ids)[:10]}")
                if not username:
                    return
                await _process_deleted_posts(username, deleted_ids)
            except Exception as e:
                logger.warning(f"Deleted-post handler failed: {e}")

        _RT_DELETED_HANDLER = _deleted_post_handler
        logger.info(f"✅ Realtime monitoring active for {len(resolved)} channels")
    except (RuntimeError, TypeError) as e:
        # Suppress Telethon v1.44.0 internal errors (Event loop is closed etc.)
        logger.warning(f"setup_realtime_monitoring Telethon error (suppressed): {e}")
    except Exception as e:
        logger.error(f"setup_realtime_monitoring failed: {e}")


async def _process_deleted_posts(username, deleted_ids, bot=None):
    """Mark tracked calls whose Telegram post is gone, take the penalty, DM the owner."""
    username = (username or "").lower()
    changed = False
    _newly_deleted = []
    for call_key, call in list(tracked_calls.items()):
        if call.get("channel", "").lower() != username:
            continue
        if str(call.get("msg_id", "")) not in deleted_ids or call.get("post_deleted"):
            continue
        # NOTE: do NOT set frozen=True — a deleted post says nothing about the
        # token being dead; history/price keep updating. Alerts/points/ranking
        # are blocked via _is_deleted_blocked() until the owner taps Yes.
        call["post_deleted"] = True
        call["deleted_at"] = datetime.utcnow().isoformat()
        call["delete_decision"] = None
        await deduct_points_for_deleted_post(username, call_key)
        logger.info(f"🗑 Deleted KOL post: -10 points @{username} ({call_key})")
        _newly_deleted.append((call_key, call))
        changed = True
    if changed:
        _save_tracked()
    if _newly_deleted:
        await _notify_owner_post_deleted(username, _newly_deleted, bot=bot)
    return len(_newly_deleted)

DELETE_SWEEP_DAYS = int(os.environ.get("DELETE_SWEEP_DAYS", "7") or 7)

async def _sweep_channel_deleted(ch, bot=None, days=None):
    """Ask Telegram whether this channel's tracked call posts still exist.
    Returns a human-readable report line list."""
    days = days or DELETE_SWEEP_DAYS
    cutoff = datetime.utcnow() - timedelta(days=days)
    ids, report = set(), []
    for ck, call in list(tracked_calls.items()):
        if (call.get("channel") or "").lstrip("@").lower() != ch.lower() or call.get("post_deleted"):
            continue
        try:
            mid = int(call.get("msg_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        ts = call.get("tracked_since", "")
        try:
            if ts and datetime.fromisoformat(ts) < cutoff:
                continue
        except Exception:
            pass
        ids.add(mid)
    ids = sorted(ids)
    report.append(f"tracked posts checked: {len(ids)} {ids[:15]}")
    if not ids:
        return report
    gone = set()
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        msgs = await asyncio.wait_for(userbot_client.get_messages(ch, ids=chunk), timeout=25)
        if not isinstance(msgs, list):
            msgs = [msgs]
        for mid, m in zip(chunk, msgs):
            if m is None or m.__class__.__name__ == "MessageEmpty":
                gone.add(str(mid))
    report.append(f"missing on Telegram: {sorted(gone)}")
    if gone:
        n = await _process_deleted_posts(ch, gone, bot=bot)
        report.append(f"marked deleted + owner DM: {n}")
    return report

async def deleted_posts_sweep_job(context: ContextTypes.DEFAULT_TYPE):
    """Backstop for missed MessageDeleted events (runs every 2 min)."""
    try:
        if not userbot_client or not userbot_client.is_connected():
            logger.warning("delete sweep: userbot not connected — skipped")
            return
        chans = {(c.get("channel") or "").lstrip("@") for c in tracked_calls.values()
                 if not c.get("post_deleted")}
        for ch in sorted(x for x in chans if x):
            try:
                rep_lines = await _sweep_channel_deleted(ch, bot=context.bot)
                if any("missing on Telegram: []" not in l and l.startswith("missing") for l in rep_lines):
                    logger.info(f"delete sweep @{ch}: " + " | ".join(rep_lines))
            except Exception as e:
                logger.warning(f"delete sweep @{ch} failed: {e}")
            await asyncio.sleep(0.4)
    except Exception as e:
        logger.warning(f"deleted_posts_sweep_job crash: {e}")

@owner_only
async def cmd_checkdeleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/checkdeleted @channel — force a deleted-post check right now and show the result."""
    if not context.args:
        await update.message.reply_text("Usage: /checkdeleted @channel"); return
    ch = context.args[0].lstrip("@").strip()
    if not userbot_client or not userbot_client.is_connected():
        await update.message.reply_text("❌ Userbot is not connected."); return
    n_calls = sum(1 for c in tracked_calls.values() if (c.get("channel") or "").lower() == ch.lower())
    n_del = sum(1 for c in tracked_calls.values()
                if (c.get("channel") or "").lower() == ch.lower() and c.get("post_deleted"))
    try:
        lines = await _sweep_channel_deleted(ch, bot=context.bot, days=3650)
    except Exception as e:
        await update.message.reply_text(f"❌ Check failed: {e}"); return
    await update.message.reply_text(
        f"🔍 @{ch}\ntracked calls: {n_calls} (already marked deleted: {n_del})\n" + "\n".join(lines))

async def _notify_owner_post_deleted(channel, items, bot=None):
    """DM the Wizard Scan owner(s): a caller deleted call post(s). Yes/No decides
    whether the X alerts continue and whether the call may enter Champions/Leaderboard.
    One DM per deletion event; the buttons apply to every call listed in it."""
    try:
        bot = bot or _bot_ref
        _recipients = list(OWNER_IDS)
        try:
            for _a in (load_admins() or []):
                try:
                    if int(_a) not in _recipients:
                        _recipients.append(int(_a))
                except Exception:
                    pass
        except Exception:
            pass
        if bot is None or not _recipients:
            logger.warning(f"deleted-post DM NOT sent for @{channel}: bot={bot is not None} "
                           f"recipients={_recipients} (set OWNER_ID env var)")
            return
        batch = hashlib.md5(f"{channel}{time.time()}".encode()).hexdigest()[:10]
        lines = []
        for ck, call in items:
            call["delete_batch"] = batch
            sym = html.escape((call.get("symbol") or "TOKEN").upper())
            try:
                cx = fmt_x(max(call_peak_ratio(call), 1.0))
            except Exception:
                cx = "?"
            _mid = call.get("msg_id")
            _ch_u = (call.get("channel") or channel or "").lstrip("@")
            _link = (f' — <a href="https://t.me/{html.escape(_ch_u)}/{_mid}">View Post</a>'
                     if _mid else "")
            _when = ""
            try:
                _dt = datetime.fromisoformat(call.get("deleted_at") or datetime.utcnow().isoformat())
                _off = float(os.environ.get("DELETE_DM_TZ_OFFSET_HOURS", "0") or 0)
                _dt = _dt + timedelta(hours=_off)
                _tzl = "UTC" if _off == 0 else f"UTC{_off:+g}"
                _when = f"\n   🕒 Deleted: {_dt.strftime('%d %b %Y, %H:%M:%S')} {_tzl}"
            except Exception:
                pass
            lines.append(f"• <b>${sym}</b> — peak {cx}{_link}{_when}")
        _save_tracked()
        shown = "\n".join(lines[:15])
        if len(lines) > 15:
            shown += f"\n… and {len(lines) - 15} more"
        text = (
            f"🗑 <b>Caller deleted post{'s' if len(items) > 1 else ''}</b>\n\n"
            f"Channel: @{html.escape(_display_handle(channel))}\n"
            f"Deleted calls: <b>{len(items)}</b>\n\n{shown}\n\n"
            f"Should the X alerts for {'these calls' if len(items) > 1 else 'this call'} continue, "
            f"and should {'they' if len(items) > 1 else 'it'} be allowed into Champions / Leaderboard if "
            f"{'they qualify' if len(items) > 1 else 'it qualifies'}?\n\n"
            f"<i>Until you decide, no X alert is sent and the X is not counted.</i>"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"dpd:y:{batch}"),
            InlineKeyboardButton("❌ No",  callback_data=f"dpd:n:{batch}"),
        ]])
        for oid in _recipients:
            try:
                await bot.send_message(oid, text, parse_mode="HTML", reply_markup=kb,
                                       disable_web_page_preview=True)
            except Exception as e:
                logger.warning(f"deleted-post DM to {oid} failed: {e}")
    except Exception as e:
        logger.warning(f"_notify_owner_post_deleted failed: {e}")

async def deleted_post_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yes / No buttons from the deleted-post DM (owner/admin only)."""
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin_or_owner(uid):
        await query.answer("Not allowed.", show_alert=True)
        return
    try:
        _, choice, batch = (query.data or "").split(":", 2)
    except ValueError:
        await query.answer()
        return
    calls = [(k, c) for k, c in tracked_calls.items() if c.get("delete_batch") == batch]
    if not calls:
        await query.answer("This request is no longer available.", show_alert=True)
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        return
    channel = calls[0][1].get("channel", "")
    if choice == "y":
        restored = 0
        for ck, call in calls:
            call["delete_decision"] = "yes"
            try:
                restored += await restore_points_for_deleted_post(call.get("channel", channel), ck)
            except Exception as e:
                logger.warning(f"restore points failed {ck}: {e}")
            # give points for X levels already reached while the call was blocked
            for ms in sorted(sent_milestones.get(ck, set())):
                try:
                    await award_points_for_milestone(call.get("channel", channel), ck, ms)
                except Exception:
                    pass
        _save_tracked()
        try:
            asyncio.create_task(_update_leaderboard_with_premium_emojis(context.bot))
            asyncio.create_task(_update_champions_with_premium_emojis(context.bot))
        except Exception:
            pass
        verdict = (f"✅ <b>Approved</b> — X alerts continue normally and "
                   f"{'these calls' if len(calls) > 1 else 'this call'} can count in Champions / Leaderboard.")
        await query.answer("Approved")
    else:
        for ck, call in calls:
            call["delete_decision"] = "no"
        _save_tracked()
        verdict = ("❌ <b>Rejected</b> — no X alerts, not counted in Champions / Leaderboard. "
                   "Shown in the record with the “Post Deleted” tag.")
        await query.answer("Rejected")
    try:
        await query.message.edit_text(
            f"🗑 <b>Caller deleted post{'s' if len(calls) > 1 else ''}</b> — @{html.escape(_display_handle(channel))}\n\n{verdict}",
            parse_mode="HTML")
    except Exception:
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass

async def userbot_watchdog_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 5 minutes. Detects userbot disconnection and auto-recovers.

    Why needed: Telethon v1.44.0 has a known bug where connection drops cause
    RuntimeError crashes. Even with auto_reconnect=True, the internal event loop
    can get into a bad state. This watchdog detects that and re-initialises the
    entire client from scratch, then re-registers realtime monitoring.
    """
    global userbot_client
    try:
        if not userbot_client:
            # No session configured — nothing to watch
            return

        is_ok = False
        try:
            is_ok = (
                userbot_client.is_connected()
                and await asyncio.wait_for(userbot_client.is_user_authorized(), timeout=10)
            )
        except Exception:
            is_ok = False

        if is_ok:
            return  # All good — nothing to do

        logger.warning("🔄 Userbot watchdog: connection lost — reinitialising...")
        await init_userbot()
        if userbot_client:
            await setup_realtime_monitoring(context.bot)
            logger.info("✅ Userbot watchdog: reconnected and realtime monitoring restored")
        else:
            logger.warning("⚠️ Userbot watchdog: reconnect failed — will retry next cycle")
    except Exception as e:
        logger.error(f"userbot_watchdog_job crash: {e}")


async def cmd_joinkols(update, context):
    """Make userbot join all tracked KOL channels (needed for realtime monitoring)."""
    if update.effective_user.id not in OWNER_IDS:
        return
    if not userbot_client:
        await update.message.reply_text("⚠️ Userbot connected nahi hai. /userbotcheck karo."); return
    channels = load_channels()
    if not channels:
        await update.message.reply_text("⚠️ Koi channels track nahi hain."); return
    msg = await update.message.reply_text(
        f"⏳ {len(channels)} channels join kar raha hoon...", parse_mode="HTML")
    joined=0; failed=[]
    for ch in channels:
        try:
            await userbot_client.get_entity(ch)
            # If entity resolves, try to join
            try:
                from telethon.tl.functions.channels import JoinChannelRequest
                await userbot_client(JoinChannelRequest(ch))
                joined+=1
            except Exception:
                joined+=1  # already member or public access
        except Exception as e:
            failed.append(ch)
        await asyncio.sleep(1)
    txt = f"✅ <b>Done!</b>\n\nJoined/accessed: <b>{joined}</b> channels"
    if failed:
        txt += "\n\n❌ Failed (" + str(len(failed)) + "): " + ", ".join(f"@{c}" for c in failed[:10])
    txt += "\n\n<i>Ab realtime monitoring kaam kare gi — redeploy nahi chahiye.</i>"
    await msg.edit_text(txt, parse_mode="HTML")


# ─── Scan job (fast — new call detection only, no dex) ───────────────────────
async def scan_job(context: ContextTypes.DEFAULT_TYPE):
    """Polling safety-net. Telethon realtime handler is the primary path; this
    loop sweeps channels in small round-robin slices so Telegram never
    flood-limits the userbot (a flood kills ALL call detection)."""
    try:
        await asyncio.wait_for(
            _scan_job_body(context),
            timeout=float(os.environ.get("SCAN_TICK_TIMEOUT_SECONDS", "8") or 8))
    except asyncio.TimeoutError:
        logger.error("⏱️ scan_job hard-timeout hit — aborting this tick so the "
                     "next one isn't skipped forever")
    except Exception as e:
        logger.error(f"scan_job crash: {e}")


async def _scan_job_body(context: ContextTypes.DEFAULT_TYPE):
    try:
        # Startup: polling GetHistory competes with realtime join/resolve and made
        # 10+ channels time out. Realtime handler already handles new posts meanwhile.
        if not BOT_READY:
            return
        bot      = context.bot
        channels = [str(ch).lstrip("@").lower() for ch in load_channels()]
        if not channels:
            return

        # Special channels are scanned on EVERY tick; normal channels rotate.
        # Keep them out of the rotating slice to avoid duplicate concurrent scans.
        # Priority (special KOL) channels from /addpriority are treated exactly
        # like special channels: scanned on every single tick, never rotated.
        _sp = list(load_special_channels()) + list(priority_channels_lower())
        _seen_sp = set()
        special = [ch for ch in _sp
                   if ch in channels and not (ch in _seen_sp or _seen_sp.add(ch))]
        _rt_up = bool(userbot_client and userbot_client.is_connected())
        _covered = set(_RT_COVERED) if _rt_up else set()
        _special_set = set(special)
        regular = [ch for ch in channels if ch not in _special_set]
        slice_size = max(1, int(os.environ.get("SCAN_BATCH_SIZE", "10") or 10))
        # Channels WITHOUT realtime coverage keep the normal fast polling
        # (specials every tick, regulars in a rotating slice). Channels the
        # userbot already receives realtime events for are polled only as a slow
        # backstop (SCAN_COVERED_BATCH per tick) — this is what removes the
        # GetHistory flood-wait storm that was delaying detection by 60s+.
        special_unc = [ch for ch in special if ch not in _covered]
        regular_unc = [ch for ch in regular if ch not in _covered]
        covered_all = [ch for ch in channels if ch in _covered]
        regular_batch = []
        if regular_unc:
            i = _SCAN_CURSOR["i"] % len(regular_unc)
            regular_batch = [regular_unc[(i + k) % len(regular_unc)]
                             for k in range(min(slice_size, len(regular_unc)))]
            _SCAN_CURSOR["i"] = (i + len(regular_batch)) % len(regular_unc)
        cov_n = max(0, int(os.environ.get("SCAN_COVERED_BATCH", "3") or 3))
        cov_batch = []
        if covered_all and cov_n:
            j = _SCAN_CURSOR.get("c", 0) % len(covered_all)
            cov_batch = [covered_all[(j + k) % len(covered_all)]
                         for k in range(min(cov_n, len(covered_all)))]
            _SCAN_CURSOR["c"] = (j + len(cov_batch)) % len(covered_all)
        batch = special_unc + regular_batch + cov_batch

        sem = asyncio.Semaphore(max(1, int(os.environ.get("SCAN_CONCURRENCY", "6") or 6)))
        # Raised 8 -> 12 (2026-09-18): same reasoning as SCAN_BATCH_SIZE above —
        # above — 24 simultaneous GetHistoryRequest calls on one Telegram
        # account was the direct trigger for the repeated 7-14s flood sleeps.
        found = 0

        async def _scan_one(channel):
            nonlocal found
            async with sem:
                try:
                    posts = await fetch_channel_posts(channel)
                    for post in posts:
                        mid = str(post["id"])
                        if mid in seen_message_ids[channel]:
                            continue
                        if await _process_new_call(bot, channel, mid, post["text"], post.get("date")):
                            found += 1
                            try:
                                _pd = post.get("date")
                                if _pd is not None:
                                    _lag = (datetime.utcnow() - _pd.replace(tzinfo=None)).total_seconds()
                                    logger.warning(f"🐢 call @{channel}/{mid} caught by POLLING, "
                                                   f"{_lag:.0f}s after the post (realtime missed it)")
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"scan_job @{channel}: {e}")

        await asyncio.gather(*[_scan_one(ch) for ch in batch])
        if found:
            logger.info(f"🔎 scan_job: {found} new call(s) from {len(batch)} channel(s)")

    except Exception as e:
        logger.error(f"scan_job crash: {e}")
