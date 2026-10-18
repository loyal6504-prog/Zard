# ===== leaderboard.py — History, leaderboard, trending, champions =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).

def _ranking_channels():
    """Har woh channel jiski calls track ho rahi hain — chahe woh channels.json
    me na ho. Pehle sirf load_channels() use hota tha, isliye jin KOLs ki calls
    X jati thi magar entry list me case/@ mismatch tha, woh leaderboard aur
    champions dono se ghayab rehte the."""
    seen, out = set(), []
    removed = load_removed_channels()
    for ch in load_channels():
        c = str(ch).lstrip("@")
        if c.lower() in removed or c.lower() in seen:
            continue
        seen.add(c.lower()); out.append(c)
    for call in tracked_calls.values():
        c = str(call.get("channel", "")).lstrip("@")
        if not c or c.lower() in removed or c.lower() in seen:
            continue
        seen.add(c.lower()); out.append(c)
    return out

def champion_total_x(channel, champ_snap=None, excluded_keys=None):
    """Sum each qualifying call's best X (2X + 2X = 4X) for Champions."""
    champ_snap = champ_snap or {}
    excluded_keys = excluded_keys or set()
    total = 0
    best_call_key = None
    best_call_x = 0
    for call_key, call in tracked_calls.items():
        if call.get("channel", "").lower() != channel.lower() or _is_deleted_blocked(call):
            continue
        # Frozen calls (via /freezecall) must NOT contribute their X to
        # Champions — freezing a suspicious/fake call should retroactively
        # remove its 100x etc. from the channel's champion total, not just
        # stop future milestone alerts.
        if call.get("frozen"):
            continue
        if call_key in champ_snap:
            levels = [m for m in sent_milestones.get(call_key, set()) if m > champ_snap[call_key]]
        elif call_key in excluded_keys:
            continue
        else:
            levels = list(sent_milestones.get(call_key, set()))
        if not levels:
            continue
        call_x = display_x(max(levels))
        if call_x < 2:
            continue
        total += call_x
        if call_x > best_call_x:
            best_call_x = call_x
            best_call_key = call_key
    return total, best_call_key

# ─── History helpers ──────────────────────────────────────────────────────────
def get_call_history(channel, chain_filter=None, top=False):
    calls = []
    # OWNER CHANGE (2026-10-07): channel record is LIFETIME — the old 90-day
    # cutoff is gone (dead tokens leave live tracking but stay in the record).
    cutoff = None
    # DIAG: counts so an empty /history result is diagnosable from Railway logs
    # instead of a silent "No calls found" with no way to tell why.
    _matched_channel = 0
    _excluded_chain   = 0
    _excluded_stale   = 0
    _excluded_incomplete = 0
    for call_key, call in tracked_calls.items():
        if call.get("channel","").lower() != channel.lower(): continue
        _matched_channel += 1
        if chain_filter and call.get("chain","").upper() != chain_filter.upper():
            _excluded_chain += 1
            continue
        if not top and cutoff:
            ts = call.get("tracked_since","")
            if ts:
                try:
                    if datetime.fromisoformat(ts) < cutoff:
                        _excluded_stale += 1
                        continue
                except Exception: pass
        milestones = list(sent_milestones.get(call_key, set()))
        # Milestones held back because no media was set for that X level are still
        # part of the channel's real record.
        try:
            milestones += [int(x) for x in pending_media_alerts.get(call_key, set())]
        except Exception:
            pass
        highest_x  = max(milestones) if milestones else 0
        entry_mc   = call.get("entry_mc", 0)
        # Use live ratio for decimal display; floor at highest milestone so crashes don't hide peaks
        # Real ATH multiplier (peak ever recorded), not the current live price
        last_ratio  = call_peak_ratio(call)
        # Safety cap: never display a ratio higher than MAX_MILESTONE × 2 to prevent phantom billions
        last_ratio  = min(last_ratio, MAX_MILESTONE * 2)
        # For display: only trust last_ratio if it's close to the confirmed milestone.
        # Phantom ratios from DexScreener glitches (e.g. 105K MC token showing billions)
        # must not inflate the displayed X or MC.
        if highest_x > 0 and last_ratio > highest_x * 3:
            current_x = float(highest_x)   # use confirmed milestone, ignore phantom ratio
        else:
            current_x = round(max(last_ratio, float(highest_x) if highest_x > 0 else 1.0), 2)
        if current_x < 1.0: current_x = 1.0
        # Cap display MC: if entry_mc × ratio produces an absurd number, use confirmed milestone MC
        current_mc  = entry_mc * current_x if entry_mc > 0 else entry_mc
        try:
            _pmc = float(call.get("peak_mc", 0) or 0)
        except (TypeError, ValueError):
            _pmc = 0.0
        if _pmc > current_mc and _pmc < 1_000_000_000:
            current_mc = _pmc
        if current_mc > 1_000_000_000 and highest_x > 0:  # cap at $1B (tightened from $10B)
            current_mc = entry_mc * highest_x  # use confirmed milestone MC, not inflated ratio
        # FIX (2026-10-18): a call that already reached 2x+ (X alert was sent) is ALWAYS
        # part of the record, even if some data field is missing/incomplete.
        if highest_x < 2 and not _complete_history_call(call, highest_x, current_x, current_mc):
            _excluded_incomplete += 1
            if _excluded_incomplete <= 5:  # cap noise — first few examples are enough to diagnose
                logger.info(
                    f"🔍 /history @{channel}: excluded {call_key} — "
                    f"ca={bool(call.get('ca'))} msg_id={call.get('msg_id')!r} "
                    f"symbol={call.get('symbol')!r} chain={call.get('chain')!r} "
                    f"entry_mc={entry_mc} current_mc={current_mc} "
                    f"entry_fmt={call.get('entry_fmt')!r} dex_pending={call.get('dex_pending')}")
            continue
        calls.append({
            "symbol":         call.get("symbol","TOKEN") or "TOKEN",
            "chain":          call.get("chain",""),
            "ca":             call.get("ca",""),
            "msg_id":         call.get("msg_id", 0),
            "entry_mc":       entry_mc,
            "entry_fmt":      call.get("entry_fmt","N/A"),
            "highest_x":      highest_x,       # integer milestone — used for sorting
            "current_x":      current_x,       # real decimal X — used for display
            "highest_mc_fmt": fmt_mc(current_mc) if current_mc > 0 else call.get("entry_fmt","N/A"),
            "tracked_since":  call.get("tracked_since",""),
            "post_deleted":   bool(call.get("post_deleted")),
        })
    if _matched_channel == 0:
        logger.info(f"🔍 /history @{channel}: 0 tracked_calls have channel=={channel!r} "
                     f"(total tracked_calls in memory: {len(tracked_calls)})")
    elif not calls:
        logger.info(f"🔍 /history @{channel}: {_matched_channel} calls matched channel, "
                     f"but 0 passed filters — chain_excluded={_excluded_chain} "
                     f"stale_excluded={_excluded_stale} incomplete_excluded={_excluded_incomplete}")

    # ── Merge in permanently-archived (pruned/rugged) calls ──────────────────
    # OWNER REQUIREMENT: a rugged token's active-tracking data is fine to
    # delete, but /history must keep showing it. Archived entries had their
    # companion dicts (sent_milestones etc.) already wiped by the prune job,
    # so their display fields are read directly from the archive snapshot
    # instead of being recomputed live.
    try:
        archive = load_call_archive()
        for call_key, arc in archive.items():
            if call_key in tracked_calls:
                continue  # live entry already covered above, avoid duplicate
            if (arc.get("channel") or "").lower() != channel.lower():
                continue
            if chain_filter and (arc.get("chain") or "").upper() != chain_filter.upper():
                continue
            if not top and cutoff:
                ts = arc.get("tracked_since", "")
                if ts:
                    try:
                        if datetime.fromisoformat(ts) < cutoff:
                            continue
                    except Exception:
                        pass
            entry_mc  = arc.get("entry_mc", 0) or 0
            highest_x = int(arc.get("best_x", 0) or 0)
            current_mc = entry_mc * highest_x if entry_mc > 0 and highest_x > 0 else entry_mc
            calls.append({
                "symbol":         arc.get("symbol", "TOKEN") or "TOKEN",
                "chain":          arc.get("chain", ""),
                "ca":             arc.get("ca", ""),
                "msg_id":         arc.get("msg_id", 0),
                "entry_mc":       entry_mc,
                "entry_fmt":      arc.get("entry_fmt", "N/A"),
                "highest_x":      highest_x,
                "current_x":      float(highest_x) if highest_x > 0 else 1.0,
                "highest_mc_fmt": fmt_mc(current_mc) if current_mc > 0 else arc.get("entry_fmt", "N/A"),
                "tracked_since":  arc.get("tracked_since", ""),
                "rugged":         True,
                "post_deleted":   bool(arc.get("post_deleted")),
            })
    except Exception as e:
        logger.warning(f"get_call_history: archive merge failed: {e}")

    if top:
        calls.sort(key=lambda x: x["highest_x"], reverse=True)
        return calls[:50]
    calls.sort(key=lambda x: x.get("tracked_since",""), reverse=True)
    return calls

async def refresh_channel_calls_live(channel, limit=30, budget=3.0):
    """Pull fresh DexScreener data for this channel's tracked calls right before
    showing its record. Runs fully in PARALLEL with a hard time budget so the
    record reply is capped at `budget` seconds (default 3s), no matter how many
    calls the channel has or how slow an individual source responds."""
    try:
        ch_low = (channel or "").lower()
        targets = [(k, c) for k, c in tracked_calls.items()
                   if c.get("channel", "").lower() == ch_low and c.get("ca") and not c.get("frozen")]
        targets.sort(key=lambda kv: kv[1].get("tracked_since", ""), reverse=True)
        targets = targets[:limit]
        if not targets:
            return

        changed = False
        sem = asyncio.Semaphore(15)

        async def _one(call_key, call):
            nonlocal changed
            async with sem:
                try:
                    dex = await asyncio.wait_for(fetch_dexscreener(call["ca"]), timeout=2.0)
                except Exception:
                    return
            if not dex:
                return
            cur_mc = dex.get("mcap", 0) or 0
            if cur_mc > 1_000_000_000_000:
                return
            if not call.get("symbol") and dex.get("symbol"):
                call["symbol"] = dex.get("symbol", "")
                changed = True
            ratio = _verified_live_ratio(call, dex)
            # Do not let a stale DexScreener 1X snapshot make the channel
            # record lie.  The same independent quote check used by the
            # real-time monitor is needed when /record refreshes a call.
            # Kept short (1.5s) so it never blows the overall 3s budget.
            if EXTRA_SOURCES_ENABLED and ratio < 1.95:
                try:
                    async with sem:
                        alt_quote = await asyncio.wait_for(
                            asyncio.to_thread(_fetch_alt_sources_sync, call["ca"]),
                            timeout=1.5)
                    alt_ratio = _verified_live_ratio(call, alt_quote)
                    if alt_quote and alt_ratio > max(ratio * 1.15, ratio + 0.10):
                        dex = alt_quote
                        cur_mc = dex.get("mcap", 0) or 0
                        ratio = alt_ratio
                except Exception:
                    pass
            if ratio <= 0:
                return
            ratio = min(ratio, MAX_MILESTONE * 2)
            call["last_ratio"]  = round(ratio, 4)
            call["live_mc"]     = cur_mc
            call["live_mc_fmt"] = fmt_mc(cur_mc) if cur_mc > 0 else ""
            call["liquidity_usd"] = float(dex.get("liquidity_usd", 0) or 0)
            call["volume_h24"]    = float(dex.get("volume_h24", 0) or 0)
            call["txns_buys"]     = int(dex.get("txns_buys", 0) or 0)
            call["txns_sells"]    = int(dex.get("txns_sells", 0) or 0)
            _update_peak(call, ratio, cur_mc)
            changed = True

        tasks = [asyncio.create_task(_one(k, c)) for k, c in targets]
        done, pending = await asyncio.wait(tasks, timeout=budget)
        for t in pending:
            t.cancel()
        if changed:
            _save_tracked()
    except Exception as e:
        logger.warning(f"refresh_channel_calls_live @{channel}: {e}")


def _hist_deleted_tag(call):
    """Short English tag shown in the record for calls whose post was deleted."""
    return "  🗑 <b>Post Deleted</b>" if call.get("post_deleted") else ""

def format_history(channel, calls, is_top=False):
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    if not calls:
        return (
            f"<b>{title_emoji} Calls History Of @{ch_safe}</b>\n\n"
            "<i>No calls found for this filter.</i>"
        )
    label = "Top 30 Calls" if is_top else "Calls History"
    lines = [f"<b>{title_emoji} {label} Of @{ch_safe}</b>\n"]
    shown = 0
    # OWNER CHANGE (2026-10-07): ALL / chain filters / plain record show EVERY
    # call (lifetime). Only the TOP view stays capped at 30.
    for call in (calls[:30] if is_top else calls):
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = str(call.get("chain") or "").upper()
        chain_emoji = _history_chain_emoji(chain)
        arrow_emoji = _history_arrow_emoji()
        ca     = call["ca"]
        ef     = html.escape(_mc_no_label(call["entry_fmt"]))
        hf     = html.escape(_mc_no_label(call["highest_mc_fmt"]))
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        # Build post link (t.me/{channel}/{msg_id}) or chart fallback
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        # KOL TG override link (from addmissedcall)
        kol_tg = call.get("kol_tg_link", "")
        if kol_tg:
            post_link = f'<a href="{kol_tg}">View Post</a>'
        # X string — real decimal (e.g. 2.7x, 15.3x)
        x_label = f" {fmt_x(cx)}"
        # X/Twitter handle for this channel
        x_handle = _get_channel_x_handle(channel)
        x_link = f'  <a href="https://x.com/{html.escape(x_handle)}">𝕏</a>' if x_handle else ""
        lines.append(
            f'\n{chain_emoji} <b>${sym} {HISTORY_X_EMOJI}{x_label}</b>{x_link}{_hist_deleted_tag(call)}\n'
            f'     {post_link}  |  {ef} to {hf}'
        )
        shown += 1
    if is_top and len(calls) > 30:
        lines.append(f"\n\n<i>Showing 30 of {len(calls)} calls.</i>")
    # Show channel points at the end
    ch_pts = get_channel_points(channel)
    lines.append(f"\n\n<i>This channel has {ch_pts}/{POINTS_FOR_CHAMPION} points</i>")
    return "\n".join(lines)


def format_history_pages(channel, calls, is_top=False, max_len=3800):
    """Same text as format_history() but split into Telegram-sized messages
    (cut only between call blocks) so a channel with 100+ calls shows ALL of them."""
    full = format_history(channel, calls, is_top=is_top)
    blocks = full.split("\n\n")
    pages, cur = [], ""
    for b in blocks:
        cand = b if not cur else cur + "\n\n" + b
        if cur and len(cand) > max_len:
            pages.append(cur)
            cur = b
        else:
            cur = cand
    if cur:
        pages.append(cur)
    if len(pages) > 1:
        pages = [f"{p}\n\n<i>Page {i}/{len(pages)}</i>" for i, p in enumerate(pages, 1)]
    return pages

async def _reply_history_pages(msg, channel, calls, kb, is_top=False):
    """Send every history page as its own message; buttons go on the LAST page."""
    pages = format_history_pages(channel, calls, is_top=is_top)
    for i, p in enumerate(pages):
        last = (i == len(pages) - 1)
        await msg.reply_text(p, parse_mode="HTML", reply_markup=(kb if last else None),
                             disable_web_page_preview=True)

def _build_lookup_detail_caption(channel, calls, limit=1024):
    """New detailed @channelname lookup view: Best Call, Points, Total Calls,
    Alpha Calls (>=2X), CONSISTENCY (success rate), then the last 5 calls — all in one video caption.
    Falls back gracefully when a stat has no data yet (shows 0 / N/A, never
    crashes or omits the line entirely, since the owner wants the shape
    consistent regardless of how much history exists)."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    best_emoji   = _history_emoji(LOOKUP_BEST_CALL_EMOJI, "🔮")
    points_emoji = _history_emoji(LOOKUP_POINTS_EMOJI, "🔮")
    calls_emoji  = _history_emoji(LOOKUP_CALLS_EMOJI, "🔮")
    alpha_emoji  = _history_emoji(LOOKUP_ALPHA_EMOJI, "🔮")
    consistency_emoji = _history_emoji(LOOKUP_CONSISTENCY_EMOJI, "🔱")
    last5_emoji  = _history_emoji(LOOKUP_LAST5_EMOJI, "🔮")
    footer_emoji = _history_emoji(LOOKUP_FOOTER_EMOJI, "🔮")

    header = f"<b>{title_emoji} Calls History Of @{ch_safe}</b>\n\n\n"

    if not calls:
        return header + "<i>No calls found for this channel yet.</i>"

    # Best call = highest X ever reached by this channel
    # FIX (2026-10-07): compare by the REAL decimal peak X (current_x), not the
    # integer milestone. Before, 3.0x and 3.5x both had highest_x == 3, so the
    # tie went to whichever call came first in the list (e.g. $PODM 3.0x shown
    # instead of $LAL 3.5x). Integer milestone is only the tie-breaker now.
    def _best_key(c):
        try: cx = float(c.get("current_x", 0) or 0)
        except (TypeError, ValueError): cx = 0.0
        try: hx = float(c.get("highest_x", 0) or 0)
        except (TypeError, ValueError): hx = 0.0
        return (max(cx, hx), hx)
    best_call = max(calls, key=_best_key)
    best_sym  = html.escape((best_call.get("symbol") or "TOKEN").upper())
    best_x    = fmt_x(_best_key(best_call)[0])

    total_calls = len(calls)
    # A call counts as "successful" once it has reached 2x or more — this is
    # the same bar ALPHA CALLS uses, and CONSISTENCY is just that success
    # rate expressed as a percentage of every call the channel has made.
    alpha_calls = sum(
        1 for c in calls
        if (c.get("current_x", c.get("highest_x", 0)) or 0) >= 2
        or (c.get("highest_x", 0) or 0) >= 2
    )
    consistency_pct = round((alpha_calls / total_calls) * 100) if total_calls else 0
    pts = get_channel_points(channel)
    pts_display = min(pts, POINTS_FOR_CHAMPION)

    stats = (
        f"{best_emoji} BEST CALL:  ${best_sym} {best_x}\n"
        f"{points_emoji} TOTAL POINTS:  {pts_display}/{POINTS_FOR_CHAMPION}\n"
        f"{calls_emoji} TOTAL CALLS:  {total_calls}\n"
        f"{alpha_emoji} ALPHA CALLS:  {alpha_calls}\n"
        f"{consistency_emoji} CONSISTENCY:  {consistency_pct}%\n\n\n"
    )

    # Most recent 5 calls (by tracked_since, newest first)
    def _sort_key(c):
        try:
            return datetime.fromisoformat(c.get("tracked_since", ""))
        except Exception:
            return datetime.min
    recent = sorted(calls, key=_sort_key, reverse=True)[:5]
    # OWNER FIX (2026-09-19): the title used to say "Last 5 Calls" even when
    # a newly-listed channel only had 3 calls to show — now it always
    # matches the number actually listed below it (and stays in sync if the
    # caption-length trimming loop below shrinks the list further).
    def _list_title(n):
        return f"{last5_emoji} <b>Last {n} Calls</b>\n"
    list_title = _list_title(len(recent))

    rows = ""
    for call in recent:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = str(call.get("chain") or "").upper()
        chain_emoji = _history_chain_emoji(chain)
        ca     = call["ca"]
        ef     = html.escape(_mc_no_label(call["entry_fmt"]))
        hf     = html.escape(_mc_no_label(call["highest_mc_fmt"]))
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        kol_tg = call.get("kol_tg_link", "")
        if kol_tg:
            post_link = f'<a href="{kol_tg}">View Post</a>'
        rows += (
            f'\n<b>{chain_emoji} ${sym} {HISTORY_X_EMOJI} {fmt_x(cx)}</b>{_hist_deleted_tag(call)}\n'
            f'     {post_link}  |  {ef} to {hf}\n'
        )

    footer = f"\n{footer_emoji} <b>Use the buttons below to check the relevant call record.</b>"

    def _rendered_len(html_str):
        """Approximate Telegram's rendered-text length for the 1024 caption
        limit check — HTML markup (<b>, <a>, <tg-emoji>) doesn't count
        against the limit, only the visible characters do."""
        s = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', html_str)
        s = re.sub(r'<a href="[^"]*">(.*?)</a>', r'\1', s)
        s = re.sub(r'</?b>', '', s)
        return len(s)

    full = header + stats + list_title + rows + footer
    if _rendered_len(full) <= limit:
        return full

    # Trim recent-calls rows one at a time from the end if over budget —
    # the stat lines (Best Call/Points/Calls/Alpha) always stay intact,
    # only the "Last N Calls" list shrinks to fit (title count shrinks with it).
    while recent and _rendered_len(header + stats + list_title + rows + footer) > limit:
        recent = recent[:-1]
        list_title = _list_title(len(recent))
        rows = ""
        for call in recent:
            sym    = html.escape((call["symbol"] or "TOKEN").upper())
            chain  = str(call.get("chain") or "").upper()
            chain_emoji = _history_chain_emoji(chain)
            ca     = call["ca"]
            ef     = html.escape(_mc_no_label(call["entry_fmt"]))
            hf     = html.escape(_mc_no_label(call["highest_mc_fmt"]))
            cx     = call.get("current_x", call["highest_x"])
            msg_id = call.get("msg_id", 0)
            post_link = (f'<a href="https://t.me/{channel}/{msg_id}">View Post</a>' if msg_id
                        else f'<a href="https://dexscreener.com/{CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")}/{ca}">📊 Chart</a>')
            kol_tg = call.get("kol_tg_link", "")
            if kol_tg:
                post_link = f'<a href="{kol_tg}">View Post</a>'
            rows += (
                f'\n<b>{chain_emoji} ${sym} {HISTORY_X_EMOJI} {fmt_x(cx)}</b>{_hist_deleted_tag(call)}\n'
                f'     {post_link}  |  {ef} to {hf}\n'
            )
    return header + stats + list_title + rows + footer


def _build_html_caption_for_video(channel, calls, limit=1024, is_top=False):
    """Build valid HTML caption matching format_history style, fitting within 1024-char limit."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    label   = "Top 30 Calls" if is_top else "Calls History"
    header  = f"<b>{title_emoji} {label} Of @{ch_safe}</b>\n"
    body    = ""
    for call in calls[:30]:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = str(call.get("chain") or "").upper()
        chain_emoji = _history_chain_emoji(chain)
        ca     = call["ca"]
        ef     = html.escape(_mc_no_label(call["entry_fmt"]))
        hf     = html.escape(_mc_no_label(call["highest_mc_fmt"]))
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        x_label = f" {fmt_x(cx)}"
        entry = (
            f'\n\n{chain_emoji} <b>${sym} {HISTORY_X_EMOJI}{x_label}</b>{_hist_deleted_tag(call)}\n'
            f'     {post_link}  |  {ef} to {hf}'
        )
        if len(header) + len(body) + len(entry) > limit:
            break
        body += entry
    return header + body

# ─── Record system (2026-10-17) ───────────────────────────────────────────────
# /history @channel and a plain "@channel" message now show ONLY the last 10
# calls (+ summary stats) and a second message offering the weekly / 15-day /
# 30-day record. Everything public here is English.
RECORD_PERIODS = {7: "Weekly", 15: "15 Days", 30: "30 Days"}
RECORD_PERIOD_CMDS = {7: "/weekly", 15: "/15days", 30: "/30days"}
RECORD_LAST_CALLS = 10      # calls shown in the first message
RECORD_PERIOD_TOP = 15      # max calls listed in a weekly / 15-day / 30-day record

def _call_peak_x(c):
    try: cx = float(c.get("current_x", 0) or 0)
    except (TypeError, ValueError): cx = 0.0
    try: hx = float(c.get("highest_x", 0) or 0)
    except (TypeError, ValueError): hx = 0.0
    return max(cx, hx)

def _call_time(c):
    """tracked_since as a naive-UTC datetime (None if missing/invalid)."""
    try:
        dt = datetime.fromisoformat(c.get("tracked_since", ""))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt

def calls_in_last_days(calls, days):
    cutoff = datetime.utcnow() - timedelta(days=days)
    out = []
    for c in calls:
        dt = _call_time(c)
        if dt is not None and dt >= cutoff:
            out.append(c)
    return out

def _record_call_row(channel, call):
    sym    = html.escape((call.get("symbol") or "TOKEN").upper())
    chain  = str(call.get("chain") or "").upper()
    chain_emoji = _history_chain_emoji(chain)
    ca     = call.get("ca", "")
    ef     = html.escape(_mc_no_label(call.get("entry_fmt", "N/A")))
    hf     = html.escape(_mc_no_label(call.get("highest_mc_fmt", "N/A")))
    cx     = call.get("current_x", call.get("highest_x", 0))
    msg_id = call.get("msg_id", 0)
    if msg_id:
        post_link = f'<a href="https://t.me/{channel}/{msg_id}">View Post</a>'
    else:
        path = CHAIN_TO_DEXPATH.get(call.get("chain"), "ethereum")
        post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
    kol_tg = call.get("kol_tg_link", "")
    if kol_tg:
        post_link = f'<a href="{kol_tg}">View Post</a>'
    return (f'\n{chain_emoji} <b>${sym} {HISTORY_X_EMOJI} {fmt_x(cx)}</b>{_hist_deleted_tag(call)}\n'
            f'     {post_link}  |  {ef} to {hf}\n')

def _record_stats(channel, calls, with_points=False):
    best_emoji   = _history_emoji(LOOKUP_BEST_CALL_EMOJI, "🔮")
    points_emoji = _history_emoji(LOOKUP_POINTS_EMOJI, "🔮")
    calls_emoji  = _history_emoji(LOOKUP_CALLS_EMOJI, "🔮")
    alpha_emoji  = _history_emoji(LOOKUP_ALPHA_EMOJI, "🔮")
    cons_emoji   = _history_emoji(LOOKUP_CONSISTENCY_EMOJI, "🔱")
    best  = max(calls, key=lambda c: (_call_peak_x(c), float(c.get("highest_x", 0) or 0)))
    total = len(calls)
    alpha = sum(1 for c in calls if _call_peak_x(c) >= 2)
    pct   = round((alpha / total) * 100) if total else 0
    out = f"{best_emoji} BEST CALL:  ${html.escape((best.get('symbol') or 'TOKEN').upper())} {fmt_x(_call_peak_x(best))}\n"
    if with_points:
        pts = min(get_channel_points(channel), POINTS_FOR_CHAMPION)
        out += f"{points_emoji} TOTAL POINTS:  {pts}/{POINTS_FOR_CHAMPION}\n"
    out += (f"{calls_emoji} TOTAL CALLS:  {total}\n"
            f"{alpha_emoji} ALPHA CALLS:  {alpha}\n"
            f"{cons_emoji} CONSISTENCY:  {pct}%\n")
    return out

def _rlen(t):
    """Visible length of an HTML caption/message (tags don't count toward Telegram's limit)."""
    t = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', t)
    t = re.sub(r'<a href="[^"]*">(.*?)</a>', r'\1', t)
    return len(re.sub(r'</?[a-z]+>', '', t))

CAPTION_MAX = 1000   # Telegram video caption limit is 1024 visible chars

def build_overview_text(channel, calls, max_len=3800):
    """Message 1 — lifetime summary + the last 10 calls (newest first)."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    list_emoji  = _history_emoji(LOOKUP_LAST5_EMOJI, "🔮")
    header = f"<b>{title_emoji} Calls History Of @{ch_safe}</b>\n\n"
    if not calls:
        return header + "<i>No calls found for this channel yet.</i>"
    recent = sorted(calls, key=lambda c: _call_time(c) or datetime.min, reverse=True)[:RECORD_LAST_CALLS]
    stats = _record_stats(channel, calls, with_points=True) + "\n"
    def _assemble(items):
        return (header + stats + f"{list_emoji} <b>Last {len(items)} Calls</b>\n"
                + "".join(_record_call_row(channel, c) for c in items))
    text = _assemble(recent)
    while len(recent) > 1 and _rlen(text) > max_len:
        recent = recent[:-1]
        text = _assemble(recent)
    return text

def build_period_text(channel, calls, days, max_len=3800):
    """Weekly / 15-day / 30-day record: summary + the best calls of that period."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    list_emoji  = _history_emoji(LOOKUP_LAST5_EMOJI, "🔮")
    label  = RECORD_PERIODS.get(days, f"{days} Days")
    header = f"<b>{title_emoji} {label} Record Of @{ch_safe}</b>\n\n"
    period = calls_in_last_days(calls, days)
    if not period:
        return header + f"<i>No calls recorded in the last {days} days.</i>"
    period.sort(key=lambda c: (_call_peak_x(c), _call_time(c) or datetime.min), reverse=True)
    top = period[:RECORD_PERIOD_TOP]
    stats = _record_stats(channel, period) + "\n"
    def _assemble(items):
        foot = (f"\n<i>Showing the top {len(items)} of {len(period)} calls from this period.</i>"
                if len(items) < len(period) else "")
        return (header + stats + f"{list_emoji} <b>Top Calls</b>\n"
                + "".join(_record_call_row(channel, c) for c in items) + foot)
    text = _assemble(top)
    while len(top) > 1 and _rlen(text) > max_len:
        top = top[:-1]
        text = _assemble(top)
    return text

def record_prompt_text(channel):
    ch = html.escape(_display_handle(channel))
    return (
        f"<b>Want to see more of @{ch}'s record?</b>\n\n"
        "Pick a period and send the matching command:\n\n"
        f"📅 Last 7 days:  <code>/weekly @{ch}</code>\n"
        f"📅 Last 15 days:  <code>/15days @{ch}</code>\n"
        f"📅 Last 30 days:  <code>/30days @{ch}</code>\n"
        "🏆 Best calls of all time:  tap Top Calls\n\n"
        "Or just tap a button below."
    )

RECORD_PERIOD_ICON = "5888670372093502546"   # premium emoji on the 7/15/30 day buttons
RECORD_TOP_ICON    = "5823636561413021540"   # premium emoji on the Top Calls button

def record_keyboard(channel):
    ch = str(channel).lstrip("@")[:28]
    return InlineKeyboardMarkup([
        [
            _icon_kb("7 Days",  RECORD_PERIOD_ICON, callback_data=f"hp|{ch}|7"),
            _icon_kb("15 Days", RECORD_PERIOD_ICON, callback_data=f"hp|{ch}|15"),
            _icon_kb("30 Days", RECORD_PERIOD_ICON, callback_data=f"hp|{ch}|30"),
        ],
        [_icon_kb("Top Calls", RECORD_TOP_ICON, callback_data=f"hp|{ch}|top")],
    ])

RECORD_TOP_LIMIT = 15   # calls listed in the Top Calls record

def build_top_text(channel, calls, max_len=3800):
    """Top Calls record: the channel's best calls of all time, highest X first."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    list_emoji  = _history_emoji(LOOKUP_LAST5_EMOJI, "🔮")
    header = f"<b>{title_emoji} Top Calls Of @{ch_safe}</b>\n<i>Best calls of all time</i>\n\n"
    if not calls:
        return header + "<i>No calls found for this channel yet.</i>"
    ranked = sorted(calls, key=lambda c: (_call_peak_x(c), _call_time(c) or datetime.min), reverse=True)
    top = ranked[:RECORD_TOP_LIMIT]
    def _assemble(items):
        foot = (f"\n<i>Showing the top {len(items)} of {len(ranked)} calls.</i>"
                if len(items) < len(ranked) else "")
        return (header + f"{list_emoji} <b>Top {len(items)} Calls</b>\n"
                + "".join(_record_call_row(channel, c) for c in items) + foot)
    text = _assemble(top)
    while len(top) > 1 and _rlen(text) > max_len:
        top = top[:-1]
        text = _assemble(top)
    return text

def record_video_data(channel, calls, kind="overview", days=0):
    """kwargs for history_video.build_history_video for one record view.
    kind: "overview" (lifetime) | "period" (7/15/30 days) | "top" (best calls of all time).
    Returns None when there is nothing to show (caller sends text only)."""
    if kind == "period":
        subset = calls_in_last_days(calls, days)
    elif kind == "top":
        subset = sorted(calls, key=_call_peak_x, reverse=True)[:RECORD_TOP_LIMIT]
    else:
        subset = list(calls)
    if not subset:
        return None
    best  = max(subset, key=lambda c: (_call_peak_x(c), float(c.get("highest_x", 0) or 0)))
    total = len(calls) if kind == "overview" else len(subset)
    pool  = calls if kind == "overview" else subset
    alpha = sum(1 for c in pool if _call_peak_x(c) >= 2)
    pct   = round((alpha / len(pool)) * 100) if pool else 0
    times = [t for t in (_call_time(c) for c in calls) if t]
    age   = max(1, (datetime.utcnow() - min(times)).days + 1) if times else 0
    data = dict(channel=str(channel).lstrip("@"), total_calls=total, alpha_calls=alpha,
                best_x=_call_peak_x(best), best_sym=(best.get("symbol") or "TOKEN"),
                consistency=pct, max_points=POINTS_FOR_CHAMPION)
    if kind == "period":
        shown = min(days, age) if age else days
        data.update(points=None, days=shown, title=f"{RECORD_PERIODS.get(days, str(days)+' DAYS').upper()} RECORD",
                    footer=f"Bot showed {shown} day{'s' if shown != 1 else ''} of record")
    elif kind == "top":
        data.update(points=min(get_channel_points(channel), POINTS_FOR_CHAMPION), days=age, title="TOP CALLS",
                    footer=f"Bot showed the top {len(subset)} calls of all time")
    else:
        data.update(points=min(get_channel_points(channel), POINTS_FOR_CHAMPION), days=age, title="CALLS HISTORY")
    return data

async def _make_record_video(channel, calls, kind, days):
    try:
        data = record_video_data(channel, calls, kind, days)
        if not data:
            return None
        import history_video
        av = os.path.join(_dp("avatars"), f"{str(channel).lstrip('@').lower()}.jpg")
        data["avatar_path"] = av if os.path.exists(av) else None
        return await history_video.build_history_video_async(timeout=20, **data)
    except Exception as e:
        logger.warning(f"record video @{channel} ({kind}) skipped: {e}")
        return None

def _record_text(channel, calls, kind, days, max_len):
    if kind == "period":
        return build_period_text(channel, calls, days, max_len=max_len)
    if kind == "top":
        return build_top_text(channel, calls, max_len=max_len)
    return build_overview_text(channel, calls, max_len=max_len)

# ─── Record v2 (2026-10-18) ───────────────────────────────────────────────────
# * @channel lookup (overview): owner's PRESET video (set via /setcommandmedia history),
#   the bot does NOT generate a video there.
# * 7 / 15 / 30 day records: bot GENERATES the video.   Top Calls: text only.
# * Every call of the record is listed (no 10 / 15 limit) — split over as many
#   messages as needed; the buttons sit on the LAST message.
# * Top Calls = only calls that reached 2x or more, never trimmed by retention.
# * Monthly retention (display level): calls older than RECORD_KEEP_DAYS leave the
#   normal record unless they reached RECORD_KEEP_X or more.
RECORD_KEEP_DAYS = 30
RECORD_KEEP_X    = 10
RECORD_TOP_MIN_X = 2

def apply_record_retention(calls):
    cutoff = datetime.utcnow() - timedelta(days=RECORD_KEEP_DAYS)
    out = []
    for c in calls:
        dt = _call_time(c)
        if dt is None or dt >= cutoff or _call_peak_x(c) >= RECORD_KEEP_X:
            out.append(c)
    return out

def _record_parts(channel, calls, kind, days):
    """-> (head_html, list_title_html, [row_html, ...])"""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    list_emoji  = _history_emoji(LOOKUP_LAST5_EMOJI, "🔮")
    if kind == "period":
        label  = RECORD_PERIODS.get(days, f"{days} Days")
        header = f"<b>{title_emoji} {label} Record Of @{ch_safe}</b>\n\n"
        subset = calls_in_last_days(calls, days)
        if not subset:
            return header + f"<i>No calls recorded in the last {days} days.</i>", "", []
        subset.sort(key=lambda c: (_call_peak_x(c), _call_time(c) or datetime.min), reverse=True)
        head  = header + _record_stats(channel, subset)
        title = f"\n{list_emoji} <b>All Calls ({len(subset)})</b>\n"
    elif kind == "top":
        header = (f"<b>{title_emoji} Top Calls Of @{ch_safe}</b>\n"
                  f"<i>Calls that reached {RECORD_TOP_MIN_X}x or more</i>\n\n")
        subset = [c for c in calls if _call_peak_x(c) >= RECORD_TOP_MIN_X]
        if not subset:
            return header + f"<i>No {RECORD_TOP_MIN_X}x+ calls recorded yet.</i>", "", []
        subset.sort(key=lambda c: (_call_peak_x(c), _call_time(c) or datetime.min), reverse=True)
        head  = header
        title = f"{list_emoji} <b>Top {len(subset)} Calls</b>\n"
    else:
        header = f"<b>{title_emoji} Calls History Of @{ch_safe}</b>\n\n"
        if not calls:
            return header + "<i>No calls found for this channel yet.</i>", "", []
        subset = sorted(calls, key=lambda c: _call_time(c) or datetime.min, reverse=True)
        head  = header + _record_stats(channel, subset, with_points=True)
        title = f"\n{list_emoji} <b>All Calls ({len(subset)})</b>\n"
    return head, title, [_record_call_row(channel, c) for c in subset]

def _paginate(first, rows, max_len=3800):
    """Split into Telegram-sized pages, cutting only between call rows."""
    pages, cur = [], first
    for r in rows:
        if cur and _rlen(cur + r) > max_len:
            pages.append(cur)
            cur = r
        else:
            cur += r
    if cur:
        pages.append(cur)
    if len(pages) > 1:
        pages = [f"{p}\n<i>Page {i}/{len(pages)}</i>" for i, p in enumerate(pages, 1)]
    return pages

async def _send_preset_video(msg, caption):
    """Owner's own video/photo for the @channel lookup (no generation)."""
    try:
        m = get_command_media("history")
        if m and m.get("file_id"):
            if m.get("type") == "photo":
                await msg.reply_photo(photo=m["file_id"], caption=caption, parse_mode="HTML")
            else:
                await msg.reply_video(video=m["file_id"], caption=caption, parse_mode="HTML")
            return True
        if os.path.exists(VID_HISTORY):
            with open(VID_HISTORY, "rb") as f:
                await msg.reply_video(f, caption=caption, parse_mode="HTML")
            return True
    except Exception as e:
        logger.warning(f"preset record video failed, text fallback: {e}")
    return False

async def send_record(msg, channel, calls, kind="overview", days=0):
    kb = record_keyboard(channel)
    use = calls if kind == "top" else apply_record_retention(calls)
    head, title, rows = _record_parts(channel, use, kind, days)
    video_sent = False
    if rows:
        if kind == "period":
            path = await _make_record_video(channel, use, kind, days)
            if path:
                try:
                    with open(path, "rb") as f:
                        await msg.reply_video(f, caption=head, parse_mode="HTML", width=1536,
                                              height=592, duration=3, supports_streaming=True)
                    video_sent = True
                except Exception as e:
                    logger.warning(f"record video send @{channel} failed, text fallback: {e}")
                finally:
                    try: os.remove(path)
                    except Exception: pass
        elif kind == "overview":
            video_sent = await _send_preset_video(msg, head)
    pages = _paginate(title if video_sent else head + title, rows) or [head]
    for i, p in enumerate(pages):
        await msg.reply_text(p, parse_mode="HTML",
                             reply_markup=(kb if i == len(pages) - 1 else None),
                             disable_web_page_preview=True)

# ─── Leaderboard / Champions / Trending generation ───────────────────────────
def _calc_leaderboard_scores():
    """Calculate top 10 leaderboard channels.
    Channels already in Champions list (>=100 points, top-10) are excluded
    so they don't appear in both lists.
    Returns list of (ch, best_x, wizard_post_id)."""
    channels = _ranking_channels()
    pts_data = load_channel_points()

    # FIX: Build champion_set using the SAME sort key as build_champions_text()
    # i.e. sort by (best_x DESC, pts DESC) — not just pts.
    # Previously it sorted by pts only, so a KOL could land in champion_set
    # (excluded from leaderboard) but NOT appear in the champion post's top-10
    # (which uses best_x as primary sort). Result: KOL vanished from both lists.
    cfg_excl = load_config()
    _champ_snap_lb   = cfg_excl.get("champion_milestone_snapshot", {})
    _champ_excl_keys = set(cfg_excl.get("champion_excluded_call_keys", []))

    champ_candidates_bx = []
    for ch in channels:
        pts = pts_data.get(ch.lower(), {}).get("points", 0)
        if pts < POINTS_FOR_CHAMPION:
            continue
        ch_bx, _ = champion_total_x(ch, _champ_snap_lb, _champ_excl_keys)
        champ_candidates_bx.append((ch, pts, ch_bx))

    # Sort by (best_x desc, pts desc) — exact same key as build_champions_text()
    champ_candidates_bx.sort(key=lambda t: (t[2], t[1]), reverse=True)
    champion_set = {ch.lower() for ch, _, _ in champ_candidates_bx[:10]}

    cfg_now = load_config()
    # Primary exclusion: call_keys snapshot at reset time (most reliable)
    excluded_keys = set(cfg_now.get("lb_excluded_call_keys", []))
    # Secondary: date-based filter
    lb_reset_since = cfg_now.get("leaderboard_reset_since", "")
    lb_reset_dt = None
    if lb_reset_since:
        try: lb_reset_dt = datetime.fromisoformat(lb_reset_since)
        except Exception: lb_reset_dt = None

    # Snapshot-based reset: only milestones ABOVE what existed at reset time count.
    lb_snap = cfg_now.get("lb_milestone_snapshot", {})

    all_scores = {}
    for ch in channels:
        if ch.lower() in champion_set:
            continue   # already a Champion — skip from Leaderboard
        best_x = 1
        best_call_key = None
        for call_key, call in tracked_calls.items():
            if call.get("channel","").lower() != ch.lower(): continue
            # Frozen calls should not keep contributing their X to the
            # Leaderboard either — same reasoning as Champions above.
            if call.get("frozen"): continue
            # Caller deleted this post (not approved by owner) → X not counted.
            if _is_deleted_blocked(call): continue

            if call_key in lb_snap:
                # Snapshot-based exclusion: only count milestones ABOVE snap value
                snap_max = lb_snap[call_key]
                ms = [m for m in sent_milestones.get(call_key, set()) if m > snap_max]
            elif call_key in excluded_keys:
                # Legacy blanket exclusion (pre-snapshot resets).
                # FIX: instead of dropping the call forever, count only the X
                # levels that were actually reached AFTER the reset.
                ms = _milestones_since(call_key, lb_reset_dt)
                if not ms:
                    continue
            else:
                # New call added after reset — count all milestones
                ms = list(sent_milestones.get(call_key, set()))
                # Secondary date filter for calls not in snapshot and not in excluded_keys
                if lb_reset_dt:
                    ts_str = call.get("tracked_since", "")
                    # Missing/broken timestamp ab call ko disqualify nahi karta —
                    # warna sahi X karne wale KOLs list se gayab ho jate the.
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < lb_reset_dt:
                                continue
                        except Exception:
                            pass

            if ms:
                mx = display_x(max(ms))
                if mx > best_x:
                    best_x = mx
                    best_call_key = call_key
        wizard_post_id = None
        if best_call_key:
            posts = milestone_posts.get(best_call_key, {})
            wizard_post_id = posts.get(str(best_x))
            # Fallback: find closest available WizardScan post for this channel
            if wizard_post_id is None and posts:
                _valid = {str(k): v for k, v in posts.items() if v and str(k).lstrip('-').isdigit()}
                if _valid:
                    _nearest = sorted(_valid.items(), key=lambda kv: abs(int(kv[0]) - best_x))
                    wizard_post_id = _nearest[0][1]
        # Only include channels that have reached at least 2x milestone
        if best_x < 2:
            continue
        all_scores[ch] = (best_x, wizard_post_id)

    lb_scores = [(ch, x, wpost) for ch, (x, wpost) in all_scores.items()]
    lb_scores.sort(key=lambda t: t[1], reverse=True)
    return lb_scores[:10]

def _num_emoji(n):
    """Return unicode number emoji for rank 1-10."""
    _e = {1:"1️⃣",2:"2️⃣",3:"3️⃣",4:"4️⃣",5:"5️⃣",6:"6️⃣",7:"7️⃣",8:"8️⃣",9:"9️⃣",10:"🔟"}
    return _e.get(n, str(n))

def _get_kol_badge(channel):
    """Check if KOL is in champions (>=100 points, top10) or leaderboard (top10 by highest X).
    Returns dict {type, rank, entry, peak} or None."""
    try:
        # Check champions first — based on points system
        # Sort by (best_x, pts) to match build_champions_text() display order exactly
        pts_data = load_channel_points()
        channels = _ranking_channels()
        # FIX: Apply same champion reset snapshot filter as build_champions_text()
        # so badge rank matches actual champion post rank exactly
        _badge_cfg = load_config()
        _champ_snap = _badge_cfg.get("champion_milestone_snapshot", {})
        _champ_excl = set(_badge_cfg.get("champion_excluded_call_keys", []))
        champ_list_with_x = []
        for ch in channels:
            pts = pts_data.get(ch.lower(), {}).get("points", 0)
            if pts >= POINTS_FOR_CHAMPION:
                ch_best_x, _ = champion_total_x(ch, _champ_snap, _champ_excl)
                champ_list_with_x.append((ch, pts, ch_best_x))
        # Sort by (best_x desc, pts desc) — same order as build_champions_text
        champ_list_with_x.sort(key=lambda t: (t[2], t[1]), reverse=True)
        for i, (ch, pts, ch_bx) in enumerate(champ_list_with_x[:10]):
            if ch.lower() == channel.lower():
                bx = ch_bx; bck = None
                for ck, call in tracked_calls.items():
                    if call.get("channel","").lower() != ch.lower(): continue
                    if _is_deleted_blocked(call): continue
                    ms = list(sent_milestones.get(ck, set()))
                    if ms and max(ms) == bx:
                        bck = ck; break
                if bck is None:
                    for ck, call in tracked_calls.items():
                        if call.get("channel","").lower() != ch.lower(): continue
                        if _is_deleted_blocked(call): continue
                        ms = list(sent_milestones.get(ck, set()))
                        if ms and max(ms) >= bx:
                            bck = ck
                call_d = tracked_calls.get(bck, {}) if bck else {}
                em = call_d.get("entry_mc", 0)
                ef = call_d.get("entry_fmt", "N/A")
                pf = fmt_mc(em * bx) if em > 0 else f"{bx}X"
                return {"type": "champions", "rank": i+1, "entry": ef, "peak": pf}
        # Check leaderboard (top 10 by highest X)
        top10 = _calc_leaderboard_scores()
        for i, (ch, bx, wpost) in enumerate(top10):
            if ch.lower() == channel.lower():
                bck2 = None; bx2 = 1
                for ck, call in tracked_calls.items():
                    if call.get("channel","").lower() != ch.lower(): continue
                    if _is_deleted_blocked(call): continue
                    ms = list(sent_milestones.get(ck, set()))
                    if ms:
                        mx = display_x(max(ms))
                        if mx > bx2: bx2 = mx; bck2 = ck
                call_d = tracked_calls.get(bck2, {}) if bck2 else {}
                em = call_d.get("entry_mc", 0); ef = call_d.get("entry_fmt", "N/A")
                pf = fmt_mc(em * bx2) if em > 0 else f"{bx2}X"
                return {"type": "leaderboard", "rank": i+1, "entry": ef, "peak": pf}
    except Exception as e:
        logger.warning(f"_get_kol_badge error: {e}")
    return None

def build_leaderboard_text(top10=None):
    """Build top 10 leaderboard with 🔮 placeholders for premium emojis.
    Order: star(header), then per row: num+arrow, then star(footer).
    Pass top10 in explicitly (from the caller's own _calc_leaderboard_scores()
    call) whenever the emoji_ids list is built from the same data — otherwise
    calling _calc_leaderboard_scores() a second time here can return a
    different row count if a call/milestone lands in between the two calls,
    desyncing emoji_ids from the 🔮 placeholders (emojis shift onto wrong rows)."""
    if top10 is None:
        top10 = _calc_leaderboard_scores()

    # User-set custom template support
    tmpl = cfg_get("leaderboard_template","")
    if tmpl:
        kwargs = {}
        for i in range(10):
            idx = i + 1
            if i < len(top10):
                ch, x, wpost = top10[i]
                _ch_disp = _display_handle(ch)
                kwargs[f"rank{idx}_link"]    = f"@{html.escape(_ch_disp)}"
                kwargs[f"rank{idx}_channel"] = f"@{html.escape(_ch_disp)}"
                kwargs[f"rank{idx}_x"]       = fmt_x(x)
            else:
                kwargs[f"rank{idx}_link"]    = "—"
                kwargs[f"rank{idx}_channel"] = "—"
                kwargs[f"rank{idx}_x"]       = "—"
        return safe_format(tmpl, **kwargs)

    # Default: 🔮 placeholders — star header, (num + arrow) per row, star footer
    lines = []
    for i, (ch, x, wpost) in enumerate(top10):
        x_str  = fmt_x(x)
        # Link X value to the WizardScan post when available
        x_part = (f'<a href="https://t.me/WizardScan/{wpost}">{x_str}</a>'
                  if wpost else x_str)
        x_handle = _get_channel_x_handle(ch)
        x_link = f'  <a href="https://x.com/{html.escape(x_handle)}">𝕏</a>' if x_handle else ""
        lines.append(f"🔮 <b>@{html.escape(_display_handle(ch))}</b> 🔮 {x_part}{x_link}")

    if not lines:
        return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
                "<i>No data yet. Tracking in progress...</i>\n\n"
                f"🔮 <b>{_leaderboard_countdown_text()}</b>")

    # OWNER FIX (2026-09-26): no more dash-padded empty rows ("🔮 — 🔮 —")
    # down to a fixed 10. A rank only appears once a real KOL earns it —
    # the list is simply as long as the number of KOLs that currently
    # qualify (1–10 rows), never padded with placeholders.

    return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
            + "\n".join(lines)
            + f"\n\n🔮 <b>{_leaderboard_countdown_text()}</b>")

def _channel_qualifies_champion(ch, pts_data, champ_snap, excluded_keys):
    """A channel only counts as a Champion KOL if it has BOTH the points
    (100+) AND at least one real 2X+ call — points alone let a channel with
    only 1X (never-profitable) calls slip onto the list, which is exactly
    what channel owners complained about."""
    pts = pts_data.get(ch.lower(), {}).get("points", 0)
    if pts < POINTS_FOR_CHAMPION:
        return False
    best_x, _ = champion_total_x(ch, champ_snap, excluded_keys)
    return best_x >= 2

def build_champions_text():
    """Build champion kols list for post 134.
    Only channels with >= POINTS_FOR_CHAMPION (100) points AND at least one
    real 2X+ call qualify. Sorted by points descending. Uses 🔮 placeholders
    for premium emojis."""
    channels = _ranking_channels()
    pts_data = load_channel_points()

    # Exclude milestones that existed before the last champion reset
    cfg_now      = load_config()
    excluded_keys = set(cfg_now.get("champion_excluded_call_keys", []))  # legacy blanket list
    champ_snap    = cfg_now.get("champion_milestone_snapshot", {})       # snapshot-based (preferred)

    champions = []
    for ch in channels:
        pts = pts_data.get(ch.lower(), {}).get("points", 0)
        best_x, best_call_key = champion_total_x(ch, champ_snap, excluded_keys)
        if pts >= POINTS_FOR_CHAMPION and best_x >= 2:
            wizard_post_id = None
            if best_call_key:
                posts = milestone_posts.get(best_call_key, {})
                wizard_post_id = posts.get(str(best_x))
                # Fallback: find closest available WizardScan post for this channel
                if wizard_post_id is None and posts:
                    _valid = {str(k): v for k, v in posts.items() if v and str(k).lstrip('-').isdigit()}
                    if _valid:
                        _nearest = sorted(_valid.items(), key=lambda kv: abs(int(kv[0]) - best_x))
                        wizard_post_id = _nearest[0][1]
            champions.append((ch, pts, best_x, wizard_post_id))
    # Sort by best_x descending, then by points as tiebreaker
    champions.sort(key=lambda x: (x[2], x[1]), reverse=True)

    lines = []
    for i, (ch, pts, best_x, _wpost) in enumerate(champions[:10]):
        x_str  = fmt_x(best_x)
        # Champion multiplier must be plain text — never a Telegram link.
        x_part = x_str
        x_handle = _get_channel_x_handle(ch)
        x_link = f'  <a href="https://x.com/{html.escape(x_handle)}">𝕏</a>' if x_handle else ""
        lines.append(f"🔮 <b>@{html.escape(_display_handle(ch))}</b> 🔮 {x_part}{x_link}")

    # OWNER FIX (2026-09-26): no more dash-padded empty rows ("🔮 — 🔮 —")
    # and no forced minimum of 5 slots. The list is exactly as long as the
    # number of channels that currently qualify (0–10) — a rank only shows
    # once a real KOL earns it.

    # OWNER TEMPLATE (2026-10-17) — post 134. Spacing exactly as owner gave:
    #   header (bold) · blank · rows · 2 blank lines · 3 ⚡ points ·
    #   2 blank lines · bold Web line.
    # Placeholder order (must match emoji_ids in _update_champions_with_premium_emojis):
    #   1 header 🔮 · per row [num 🔮, arrow 🔮] · 3 × ⚡ 🔮 · 1 Web 🔮
    header = "<b>🔮 W-S CHAMPION KOLS</b>"
    rows   = "\n".join(lines)
    points = ("🔮 Visit our Web to check these KOLs’ live data.\n"
              "🔮 You must earn 100 points to get here.\n"
              "🔮 The bot only shares info. Always DYOR.")
    web    = f'<b>🔮 Web: <a href="{CHAMPIONS_WEB_LINK}">wizardscanlive.com</a></b>'
    if rows:
        return f"{header}\n\n{rows}\n\n\n{points}\n\n\n{web}"
    return f"{header}\n\n{points}\n\n\n{web}"

def build_trending_text_and_emojis(chain_tokens):
    """Build trending post text with 🔮 placeholders + ordered emoji_ids list.
    Returns (text, emoji_ids). Each 🔮 in text maps to next emoji in list."""
    parts      = []
    emoji_ids  = []
    num_ctr    = 1  # global counter for numbered tokens (1-20)

    for chain in ["SOL", "ETH", "BNB", "BASE"]:
        chain_label = {"ETH":"ETH","BNB":"BSC","SOL":"SOL","BASE":"BASE"}[chain]
        parts.append(f"<b>🔮 {chain_label} TRENDING</b>")
        emoji_ids.append(TRENDING_PREMIUM_EMOJIS.get(chain, TRENDING_PREMIUM_EMOJIS["SOL"]))
        parts.append("")
        tokens = chain_tokens.get(chain, [])
        if tokens:
            for t in tokens:
                sym     = html.escape(t.get("symbol","TOKEN"))
                mc_raw  = html.escape(t.get("mc_fmt","N/A"))
                tg_url  = html.escape(t.get("tg_url",""), quote=True)
                dex_url = html.escape(t.get("dex_url",""), quote=True)
                sym_part = f'<b><a href="{dex_url}">{sym}</a></b>' if dex_url else f"<b>{sym}</b>"
                # MC becomes a TG link if token has a Telegram; otherwise plain text
                mc_part = f'<a href="{tg_url}">{mc_raw}</a>' if tg_url else mc_raw
                # Row: 🔮(number) SYM 🔮(arrow) MC
                parts.append(f"🔮 {sym_part} 🔮 {mc_part}")
                emoji_ids.append(TRENDING_PREMIUM_EMOJIS.get(num_ctr, TRENDING_PREMIUM_EMOJIS[20]))
                emoji_ids.append(TRENDING_PREMIUM_EMOJIS["arrow"])
                num_ctr += 1
        else:
            parts.append("<i>No data available</i>")
        parts.append("")

    return "\n".join(parts), emoji_ids

def build_trending_text(chain_tokens):
    """Build trending tokens post text for post 135 (returns text only)."""
    text, _ = build_trending_text_and_emojis(chain_tokens)
    return text

# ─── New Trending Posts (3560 and 3562) ──────────────────────────────────────
def _fetch_trending2_sync():
    """Fetch trending tokens for the two new posts.
    Post 3560: SOL (1-5) + ETH (6-10) + BSC (11-15)
    Post 3562: Robinhood (16-20) + BASE (21-25) + TON (26-30)
    Returns dict: {chain: [tokens...]}
    """
    blacklist = load_trending_blacklist()
    chain_tokens = {"SOL": [], "ETH": [], "BSC": [], "RH": [], "BASE": [], "TON": []}

    # SOL — DexScreener boosts
    try:
        resp = requests.get("https://api.dexscreener.com/token-boosts/top/v1", timeout=20, headers=HEADERS)
        if resp.status_code == 200:
            raw = resp.json()
            if isinstance(raw, list):
                sol_candidates = []
                seen = set()
                for token in raw:
                    if token.get("chainId","").lower() != "solana": continue
                    ca = token.get("tokenAddress","")
                    if not ca or ca in seen or ca.lower() in blacklist: continue
                    seen.add(ca)
                    tg = _extract_tg_link(token.get("links") or [])
                    sol_candidates.append((ca, tg))
                sol_results = []
                for batch_start in range(0, min(len(sol_candidates), 60), 10):
                    batch = sol_candidates[batch_start: batch_start + 10]
                    cas   = [ca for ca, _ in batch]
                    tg_map = {ca: tg for ca, tg in batch}
                    try:
                        r = requests.get(
                            f"https://api.dexscreener.com/latest/dex/tokens/{','.join(cas)}",
                            timeout=15, headers=HEADERS)
                        if r.status_code != 200: continue
                        pairs_data = r.json().get("pairs") or []
                    except Exception: continue
                    best_per_ca = {}
                    for pair in pairs_data:
                        ca_key = pair.get("baseToken",{}).get("address","")
                        if ca_key not in best_per_ca:
                            best_per_ca[ca_key] = pair
                        else:
                            liq_new = pair.get("liquidity",{}).get("usd",0) or 0
                            liq_old = best_per_ca[ca_key].get("liquidity",{}).get("usd",0) or 0
                            if liq_new > liq_old:
                                best_per_ca[ca_key] = pair
                    for ca in cas:
                        pair = best_per_ca.get(ca)
                        if not pair: continue
                        mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
                        if mc <= 0: continue
                        tg = tg_map.get(ca,"")
                        if not tg:
                            info = pair.get("info") or {}
                            for s in (info.get("socials") or []):
                                u = s.get("url",""); t = s.get("type","").lower()
                                if "t.me" in u or "telegram" in t: tg = u; break
                        sol_results.append({
                            "symbol":  pair.get("baseToken",{}).get("symbol","TOKEN"),
                            "mc_fmt":  fmt_mc(mc),
                            "tg_url":  tg,
                            "dex_url": f"https://dexscreener.com/solana/{ca}",
                            "has_tg":  bool(tg),
                        })
                    time.sleep(0.2)
                sol_results.sort(key=lambda x: (0 if x["has_tg"] else 1))
                chain_tokens["SOL"] = sol_results[:5]
    except Exception as e:
        logger.warning(f"T2 SOL trending fetch failed: {e}")

    # ETH, BSC, BASE, RH via GeckoTerminal
    gecko_map2 = [("eth", "ETH"), ("bsc", "BSC"), ("base", "BASE"), ("robinhood", "RH")]
    for gecko_net, chain_key in gecko_map2:
        try:
            chain_tokens[chain_key] = _fetch_gecko_chain(gecko_net, chain_key, blacklist)
        except Exception as e:
            logger.warning(f"T2 GeckoTerminal {gecko_net} failed: {e}")
        time.sleep(0.3)

    # SOL top-up — boosts endpoint kabhi kabhi khali aata hai, to GeckoTerminal se bharo
    if len(chain_tokens["SOL"]) < 5:
        try:
            extra_sol = _fetch_gecko_chain("solana", "SOL", blacklist)
            have = {t.get("symbol") for t in chain_tokens["SOL"]}
            for t in extra_sol:
                if len(chain_tokens["SOL"]) >= 5: break
                if t.get("symbol") in have: continue
                chain_tokens["SOL"].append(t)
        except Exception as e:
            logger.warning(f"T2 SOL gecko top-up failed: {e}")

    # RH retry — Robinhood chain par 429 aata hai to dobara koshish karo
    if len(chain_tokens["RH"]) < 1:
        try:
            time.sleep(1.0)
            chain_tokens["RH"] = _fetch_gecko_chain("robinhood", "RH", blacklist)
        except Exception as e:
            logger.warning(f"T2 RH retry failed: {e}")

    # TON — try DexScreener first, then GeckoTerminal as fallback
    try:
        ton_results = []
        seen_t = set()

        # --- DexScreener TON trending (token-boosts endpoint) ---
        try:
            resp_ton = requests.get("https://api.dexscreener.com/token-boosts/top/v1", timeout=15, headers=HEADERS)
            if resp_ton.status_code == 200:
                raw_ton = resp_ton.json()
                if isinstance(raw_ton, list):
                    ton_candidates = []
                    for token in raw_ton:
                        if token.get("chainId","").lower() not in ("ton", "the-open-network"): continue
                        ca = token.get("tokenAddress","")
                        if not ca or ca in seen_t or ca.lower() in blacklist: continue
                        seen_t.add(ca)
                        tg = _extract_tg_link(token.get("links") or [])
                        ton_candidates.append((ca, tg))
                    for batch_start in range(0, min(len(ton_candidates), 30), 10):
                        batch = ton_candidates[batch_start: batch_start + 10]
                        cas   = [ca for ca, _ in batch]
                        tg_map = {ca: tg for ca, tg in batch}
                        try:
                            r2 = requests.get(
                                f"https://api.dexscreener.com/latest/dex/tokens/{','.join(cas)}",
                                timeout=12, headers=HEADERS)
                            if r2.status_code != 200: continue
                            for pair in (r2.json().get("pairs") or []):
                                if pair.get("chainId","").lower() not in ("ton","the-open-network"): continue
                                ca_key = pair.get("baseToken",{}).get("address","")
                                if not ca_key or ca_key in seen_t: continue
                                mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
                                if mc <= 0: continue
                                seen_t.add(ca_key)
                                sym = pair.get("baseToken",{}).get("symbol","TOKEN")
                                tg  = tg_map.get(ca_key,"")
                                if not tg:
                                    for s in ((pair.get("info") or {}).get("socials") or []):
                                        if "t.me" in s.get("url","") or "telegram" in s.get("type","").lower():
                                            tg = s["url"]; break
                                dex = f"https://dexscreener.com/ton/{ca_key}"
                                ton_results.append({"symbol": sym, "mc_fmt": fmt_mc(mc),
                                                    "tg_url": tg, "dex_url": dex, "has_tg": bool(tg)})
                        except Exception: pass
                        if len(ton_results) >= 10: break
        except Exception as e_dex_ton:
            logger.debug(f"DexScreener TON trending skipped: {e_dex_ton}")

        # --- GeckoTerminal TON fallback ---
        if len(ton_results) < 5:
            try:
                GECKO_HEADERS = {"Accept": "application/json"}
                r = requests.get(
                    "https://api.geckoterminal.com/api/v2/networks/ton/trending_pools?page=1&include=base_token",
                    headers=GECKO_HEADERS, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    included_map = {}
                    for item in (data.get("included") or []):
                        attr = item.get("attributes",{})
                        iid  = item.get("id","")
                        if iid:
                            included_map[iid] = {
                                "symbol":   attr.get("symbol",""),
                                "address":  attr.get("address","") or attr.get("pool_address",""),
                                "telegram": attr.get("telegram_chat_url") or "",
                            }
                    for pool in (data.get("data") or []):
                        attr = pool.get("attributes",{})
                        mc_raw = attr.get("market_cap_usd") or attr.get("fdv_usd") or attr.get("reserve_in_usd") or 0
                        try: mc = float(mc_raw)
                        except Exception: mc = 0.0
                        if mc <= 0:
                            try: mc = float(attr.get("volume_usd",{}).get("h24") or 0)
                            except Exception: mc = 0.0
                        # Accept pools even if MC is low — some TON pools report reserve not fdv
                        rel_id = pool.get("relationships",{}).get("base_token",{}).get("data",{}).get("id","")
                        tok = included_map.get(rel_id,{})
                        # Try multiple ways to get a usable address
                        ca = (tok.get("address","")
                              or (rel_id.split("_",1)[1] if "_" in rel_id else ""))
                        # For GeckoTerminal TON, the pool address itself is usable as chart URL
                        pool_id = pool.get("id","")
                        pool_addr = pool_id.split("_",1)[1] if "_" in pool_id else pool_id
                        sym = tok.get("symbol","") or attr.get("name","TOKEN").split(" / ")[0]
                        if not sym or sym == "TOKEN": continue
                        # Use pool address for dex link if token CA not available
                        chart_addr = ca if (ca and len(ca) > 20) else pool_addr
                        if not chart_addr or chart_addr.lower() in blacklist: continue
                        if chart_addr in seen_t: continue
                        seen_t.add(chart_addr)
                        tg = tok.get("telegram","")
                        dex = f"https://dexscreener.com/ton/{chart_addr}"
                        mc_display = mc if mc > 0 else 1  # show something rather than nothing
                        ton_results.append({
                            "symbol": sym, "mc_fmt": fmt_mc(mc_display),
                            "tg_url": tg,  "dex_url": dex, "has_tg": bool(tg),
                        })
                        if len(ton_results) >= 10: break
            except Exception as e_gecko_ton:
                logger.warning(f"T2 TON GeckoTerminal fallback failed: {e_gecko_ton}")

        ton_results.sort(key=lambda x: (0 if x["has_tg"] else 1))
        chain_tokens["TON"] = ton_results[:5]
        if ton_results:
            logger.info(f"T2 TON trending: found {len(chain_tokens['TON'])} tokens")
        else:
            logger.warning("T2 TON trending: no tokens found from DexScreener or GeckoTerminal")
    except Exception as e:
        logger.warning(f"T2 TON trending outer error: {e}")

    # ── Inject pinned tokens at position 0 for their chain ─────────────────────
    _inject_pinned_tokens(chain_tokens)
    return chain_tokens

async def fetch_trending2():
    fresh = await asyncio.to_thread(_fetch_trending2_sync)
    # A chain that fails (API rate limit) must not blank out its section —
    # reuse the last known tokens for that chain only.
    try:
        old = load_trending2_cache() or {}
        for ck, toks in (fresh or {}).items():
            if not toks and old.get(ck):
                fresh[ck] = old[ck]
                logger.info(f"Trending2: {ck} empty — purane tokens reuse kiye")
    except Exception:
        pass
    return fresh


def _build_new_trending_row(pos, token, emoji_ids_out):
    """Build one token row for the new trending post format.
    Appends position emoji + mc emoji to emoji_ids_out.
    Returns row text string."""
    sym     = html.escape(token.get("symbol","TOKEN"))
    mc_raw  = html.escape(token.get("mc_fmt","N/A"))
    tg_url  = token.get("tg_url","")
    dex_url = token.get("dex_url","")
    sym_part = (f'<b><a href="{html.escape(dex_url, quote=True)}">${sym}</a></b>'
                if dex_url else f"<b>${sym}</b>")
    mc_part  = (f'<a href="{html.escape(tg_url, quote=True)}">{mc_raw}</a>'
                if tg_url else mc_raw)
    # Number emoji placeholder  +  MC emoji placeholder
    emoji_ids_out.append(TRENDING2_EMOJIS_POS.get(pos, TRENDING2_EMOJIS_POS.get(1, 0)))
    emoji_ids_out.append(TRENDING2_MC_EMOJI)
    return f"🔮 {sym_part} 🔮 {mc_part}"


def build_trending2_post1_text_and_emojis(chain_tokens2):
    """Build post 3560: SOL (1-5) + ETH (6-10) + BSC (11-15).
    Returns (text, emoji_ids)."""
    parts = []
    emoji_ids = []

    sections = [
        ("SOL",  "SOL TRENDING",  range(1, 6)),
        ("ETH",  "ETH TRENDING",  range(6, 11)),
        ("BSC",  "BSC TRENDING",  range(11, 16)),
    ]
    for chain_key, label, pos_range in sections:
        # Chain header with emoji placeholder
        parts.append(f"<b>🔮 {label}</b>")
        emoji_ids.append(TRENDING2_CHAIN_EMOJIS.get(chain_key, TRENDING2_CHAIN_EMOJIS["SOL"]))
        parts.append("")
        tokens = chain_tokens2.get(chain_key, [])
        for i, pos in enumerate(pos_range):
            if i < len(tokens):
                row = _build_new_trending_row(pos, tokens[i], emoji_ids)
                parts.append(row)
            else:
                parts.append(f"🔮 <b>—</b> 🔮 —")
                emoji_ids.append(TRENDING2_EMOJIS_POS.get(pos, 0))
                emoji_ids.append(TRENDING2_MC_EMOJI)
        parts.append("")

    parts.append("These are the trending projects on DexScreener. \nThis is not financial advice, DYOR.")
    return "\n".join(parts), emoji_ids


def build_trending2_post2_text_and_emojis(chain_tokens2):
    """Build post 3562: Robinhood (16-20) + BASE (21-25) + TON (26-30).
    Returns (text, emoji_ids)."""
    parts = []
    emoji_ids = []

    sections = [
        ("RH",   "ROBINHOOD TRENDING", range(16, 21)),
        ("BASE", "BASE TRENDING",      range(21, 26)),
        ("TON",  "TON TRENDING",       range(26, 31)),
    ]
    for chain_key, label, pos_range in sections:
        parts.append(f"<b>🔮 {label}</b>")
        emoji_ids.append(TRENDING2_CHAIN_EMOJIS.get(chain_key, TRENDING2_CHAIN_EMOJIS["SOL"]))
        parts.append("")
        tokens = chain_tokens2.get(chain_key, [])
        for i, pos in enumerate(pos_range):
            if i < len(tokens):
                row = _build_new_trending_row(pos, tokens[i], emoji_ids)
                parts.append(row)
            else:
                parts.append(f"🔮 <b>—</b> 🔮 —")
                emoji_ids.append(TRENDING2_EMOJIS_POS.get(pos, 0))
                emoji_ids.append(TRENDING2_MC_EMOJI)
        parts.append("")

    parts.append("These are the trending projects on DexScreener. \nThis is not financial advice, DYOR.")
    return "\n".join(parts), emoji_ids


async def _update_trending2_posts(bot, chain_tokens2=None):
    """Fetch trending2 data and update posts 3560 + 3562 via userbot with premium emojis."""
    if chain_tokens2 is None:
        chain_tokens2 = await fetch_trending2()
        save_trending2_cache(chain_tokens2)  # persist for price-only updates next run

    text1, eids1 = build_trending2_post1_text_and_emojis(chain_tokens2)
    text2, eids2 = build_trending2_post2_text_and_emojis(chain_tokens2)

    results = {}
    for post_id, text, emoji_ids in [
        (POST_TRENDING_1, text1, eids1),
        (POST_TRENDING_2, text2, eids2),
    ]:
        ok = False
        if userbot_client:
            try:
                from telethon.extensions.html import parse as tl_html_parse
                plain_text, base_entities = tl_html_parse(text)
                all_entities = _build_new_trending_entities(plain_text, base_entities, emoji_ids)
                await _locked_userbot_edit(
                    TARGET_CHANNEL, post_id, plain_text,
                    formatting_entities=all_entities, link_preview=False
                )
                logger.info(f"✅ New trending post {post_id} updated with premium emojis")
                ok = True
            except Exception as e:
                logger.error(f"New trending post {post_id} premium edit failed: {e}")
        if not ok:
            try:
                await bot.edit_message_text(
                    chat_id=TARGET_CHANNEL, message_id=post_id,
                    text=text, parse_mode="HTML", disable_web_page_preview=True)
                ok = True
                logger.info(f"✅ New trending post {post_id} updated via bot API")
            except Exception:
                try:
                    await bot.edit_message_caption(
                        chat_id=TARGET_CHANNEL, message_id=post_id,
                        caption=text[:1024], parse_mode="HTML")
                    ok = True
                except Exception as e2:
                    logger.error(f"New trending post {post_id} bot fallback failed: {e2}")
        results[post_id] = ok

    return results


def _build_new_trending_entities(plain_text, base_entities, emoji_ids):
    """Build MessageEntityCustomEmoji for the new trending posts.
    Unlike the alert builder, these posts use simple sequential emoji replacement
    (no special-casing for KOL/BOT/badge — just positional).
    """
    try:
        from telethon.tl.types import MessageEntityCustomEmoji
    except ImportError:
        return base_entities or []
    if not emoji_ids:
        return base_entities or []

    custom_entities = []
    PLACEHOLDER = '🔮'
    PH_LEN = len(PLACEHOLDER)
    emoji_u16 = len(PLACEHOLDER.encode('utf-16-le')) // 2
    pos_index = 0
    utf16_off = 0
    i = 0
    while i < len(plain_text):
        if plain_text[i:i+PH_LEN] == PLACEHOLDER:
            if pos_index < len(emoji_ids):
                eid = emoji_ids[pos_index]
                if eid:
                    custom_entities.append(MessageEntityCustomEmoji(
                        offset=utf16_off, length=emoji_u16,
                        document_id=int(eid)
                    ))
                pos_index += 1
            utf16_off += emoji_u16
            i += PH_LEN
            continue
        char_u16 = len(plain_text[i].encode('utf-16-le')) // 2
        utf16_off += char_u16
        i += 1
    return (base_entities or []) + custom_entities


async def _post_hashtag_to_channel(bot):
    """Post the hashtag image to the channel."""
    try:
        with open(IMG_HASHTAG, "rb") as f:
            await bot.send_photo(TARGET_CHANNEL, photo=f, caption=HASHTAG_CAPTION)
        logger.info("✅ Hashtag post sent to channel")
    except Exception as e:
        logger.error(f"Hashtag post failed: {e}")

async def _post_promo_to_channel(bot):
    """Post the owner's promo template to @WizardScan (every 25 alerts)."""
    try:
        config   = load_config()
        template = config.get("promo_template","")
        if not template: return
        media    = config.get("promo_video")
        if media and media.get("file_id"):
            fid, ftype = media["file_id"], media.get("type","video")
            try:
                if ftype == "photo":
                    await bot.send_photo(TARGET_CHANNEL, photo=fid, caption=template, parse_mode="HTML")
                else:
                    await bot.send_video(TARGET_CHANNEL, video=fid, caption=template, parse_mode="HTML")
                logger.info("✅ Promo post sent")
                return
            except Exception: pass
        await bot.send_message(TARGET_CHANNEL, template, parse_mode="HTML", disable_web_page_preview=True)
        logger.info("✅ Promo post (text) sent")
    except Exception as e:
        logger.error(f"Promo post failed: {e}")

async def update_channel_post(bot, message_id, text, use_ranking_emojis=False):
    """Edit a post in @WizardScan — prefers userbot (premium account for emojis)."""
    ranking_emoji_ids = cfg_get("ranking_emojis", []) if use_ranking_emojis else None
    # Try userbot first (premium account, supports custom emojis)
    if userbot_client:
        ok = await _userbot_edit_with_premium_emoji(
            TARGET_CHANNEL, message_id, text,
            emoji_ids_for_ranking=ranking_emoji_ids
        )
        if ok:
            logger.info(f"✅ Userbot updated post {message_id}")
            return True
    # Fallback: bot API — try text edit first, then caption (for media posts)
    try:
        await bot.edit_message_text(
            chat_id=TARGET_CHANNEL, message_id=message_id,
            text=text, parse_mode="HTML", disable_web_page_preview=True
        )
        logger.info(f"✅ Bot updated post {message_id} (text)")
        return True
    except Exception as e:
        err = str(e)
        if "no text" in err.lower() or "message is not modified" in err.lower() or "there is no text" in err.lower():
            # Post has media — edit caption instead
            try:
                await bot.edit_message_caption(
                    chat_id=TARGET_CHANNEL, message_id=message_id,
                    caption=text[:1024], parse_mode="HTML"
                )
                logger.info(f"✅ Bot updated post {message_id} (caption)")
                return True
            except Exception as e2:
                logger.error(f"Bot edit caption post {message_id} failed: {e2}")
        else:
            logger.error(f"Bot edit post {message_id} failed: {e}")
    return False

# ─── Leaderboard auto-update job ─────────────────────────────────────────────
_LAST_LB_TEXT    = None   # last text actually pushed to the leaderboard post
_LAST_CHAMP_TEXT = None   # last text actually pushed to the champions post
async def _update_leaderboard_with_premium_emojis(bot):
    """Edit post 136 with leaderboard data + all premium emojis via userbot."""
    global userbot_client, _LAST_LB_TEXT
    top10   = _calc_leaderboard_scores()  # computed ONCE, reused for text + emoji_ids
    text    = build_leaderboard_text(top10)
    # Nothing changed since the last edit → skip the Telegram call entirely.
    # (Job now runs every 5 s; this keeps us far away from edit rate limits.)
    if text == _LAST_LB_TEXT:
        return False
    n       = len(top10)

    # Ordered emoji list: [star(header), num1, arrow, num2, arrow, ... numN, arrow, star(footer)]
    emoji_ids = [LEADERBOARD_PREMIUM_EMOJIS["star"]]
    for i in range(1, n + 1):
        emoji_ids.append(LEADERBOARD_PREMIUM_EMOJIS.get(i, LEADERBOARD_PREMIUM_EMOJIS[1]))
        emoji_ids.append(LEADERBOARD_PREMIUM_EMOJIS["arrow"])
    emoji_ids.append(LEADERBOARD_PREMIUM_EMOJIS["star"])  # footer star

    # Try to reconnect userbot if disconnected
    if userbot_client and not userbot_client.is_connected():
        try:
            await userbot_client.connect()
            logger.info("✅ Userbot reconnected for leaderboard update")
        except Exception as e_rc:
            logger.warning(f"Userbot reconnect failed: {e_rc}")
            userbot_client = None

    if userbot_client:
        try:
            from telethon.extensions.html import parse as tl_html_parse
            plain_text, base_entities = tl_html_parse(text)
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids, forced_pack=None)
            await _locked_userbot_edit(
                TARGET_CHANNEL, POST_LEADERBOARD, plain_text,
                formatting_entities=all_entities,
                link_preview=False
            )
            _LAST_LB_TEXT = text
            logger.info("✅ Leaderboard post 136 updated with premium emojis")
            return True
        except Exception as e:
            if "not modified" in str(e).lower():
                # Benign: leaderboard ranking genuinely didn't change since the
                # last edit — Telegram correctly no-ops it. Not an error.
                _LAST_LB_TEXT = text
                return True
            logger.error(f"Leaderboard premium emoji edit failed: {e}")
            # Do NOT fall back to bot API — it would strip premium emojis from the post.
            # Better to keep the old premium-emoji post than overwrite with plain text.
            return False

    # Userbot unavailable — skip update to preserve existing premium emojis on the post.
    logger.warning("Leaderboard update skipped: userbot unavailable (preserving premium emojis).")
    return False

async def channel_meta_job(context: ContextTypes.DEFAULT_TYPE):
    """Every ~10 min: fetch each tracked channel's subscriber count + profile
    photo from Telegram, cache to channel_meta.json / DATA_DIR/avatars/*.jpg.
    Powers the website's leaderboard cards (avatar + subscriber count).
    Runs inside the bot process (where BOT_TOKEN lives) — api_server.py only
    ever serves the already-downloaded local jpg, so the token is never
    exposed in any URL sent to the browser.
    Fully wrapped in try/except per-channel and for the whole job, so it can
    never crash or slow down the bot's existing jobs even if Telegram
    rate-limits or a channel lookup fails."""
    try:
        avatars_dir = _dp("avatars")
        os.makedirs(avatars_dir, exist_ok=True)
        meta = load_json(CHANNEL_META_FILE, {})

        for ch in load_channels():
            key = str(ch).lstrip("@").lower()
            try:
                chat  = await context.bot.get_chat(f"@{key}")
                count = await context.bot.get_chat_member_count(f"@{key}")
                entry = meta.get(key, {})
                entry["title"] = chat.title or key
                entry["subscribers"] = count
                if chat.photo:
                    file = await context.bot.get_file(chat.photo.small_file_id)
                    await file.download_to_drive(os.path.join(avatars_dir, f"{key}.jpg"))
                    entry["has_avatar"] = True
                meta[key] = entry
            except Exception as e_ch:
                logger.warning(f"channel_meta_job: failed for @{key}: {e_ch}")
            await asyncio.sleep(0.3)  # gentle pacing — avoid Telegram rate limits

        save_json(CHANNEL_META_FILE, meta)
        logger.info(f"channel_meta_job: refreshed metadata for {len(meta)} channel(s)")
    except Exception as e:
        logger.warning(f"channel_meta_job failed: {e}")

LEADERBOARD_RESET_HOURS = 24

def _leaderboard_countdown_text():
    """OWNER FIX (2026-09-24): owner wants a fixed label here, not a live
    countdown — always show the static reset notice regardless of how much
    time is actually left until the next 24h auto-reset."""
    return "Auto-resets every 24 hours."

async def leaderboard_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 136 (leaderboard) every 1-2 minutes. Auto-resets scores every 24 hours."""
    config = load_config()
    now    = datetime.utcnow()

    # ── automatic 24-hour reset ──────────────────────────────────────────────
    reset_since = config.get("leaderboard_reset_since", "")
    try:
        since_dt = datetime.fromisoformat(reset_since) if reset_since else None
    except Exception:
        since_dt = None
    if since_dt is None or (now - since_dt) >= timedelta(hours=LEADERBOARD_RESET_HOURS):
        _perform_leaderboard_reset()
        config = load_config()  # reflect the reset before continuing this tick

    # ── regular update cooldown (2 min) ──────────────────────────────────────
    last_upd = config.get("last_leaderboard_update","")
    try:
        if last_upd:
            last_dt = datetime.fromisoformat(last_upd)
            if now - last_dt < timedelta(seconds=5): return
    except Exception: pass
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_leaderboard_update", now.isoformat())
    except Exception as e:
        logger.error(f"Leaderboard update failed: {e}")

async def _update_champions_with_premium_emojis(bot):
    """Edit post 134 with champions data + all premium emojis via userbot."""
    global userbot_client, _LAST_CHAMP_TEXT
    text     = build_champions_text()
    if text == _LAST_CHAMP_TEXT:
        # DIAG: this used to return silently, making it impossible to tell
        # from logs whether champions genuinely didn't change or something
        # upstream (points, snapshot exclusion) is stuck. Log the real
        # numbers every ~10 cycles (5 min) so it's diagnosable without
        # needing to read the raw JSON files directly.
        global _CHAMP_NOOP_STREAK
        _CHAMP_NOOP_STREAK = globals().get("_CHAMP_NOOP_STREAK", 0) + 1
        if _CHAMP_NOOP_STREAK % 10 == 1:
            pts_data = load_channel_points()
            channels = _ranking_channels()
            _cfg_now = load_config()
            _excl = set(_cfg_now.get("champion_excluded_call_keys", []))
            _snap = _cfg_now.get("champion_milestone_snapshot", {})
            qualifying = sorted(
                [(ch, pts_data.get(ch.lower(), {}).get("points", 0))
                 for ch in channels
                 if _channel_qualifies_champion(ch, pts_data, _snap, _excl)],
                key=lambda t: -t[1])
            logger.info(
                f"🏆 Champions unchanged for {_CHAMP_NOOP_STREAK} cycles — "
                f"{len(qualifying)} channels qualify (>=100 pts AND >=2X): "
                f"{qualifying[:10]}")
        return False
    _CHAMP_NOOP_STREAK = 0
    # Build emoji_ids to match EXACTLY the 🔮 placeholders in build_champions_text().
    # OWNER FIX (2026-09-26): build_champions_text() no longer pads with dash
    # rows or a minimum of 5 — it now emits exactly `n` real champion rows
    # (0-10), so emoji_ids must do the same: 1 header 🔮 + n rows × 2 🔮 +
    # 3 ⚡ points + 1 Web 🔮.
    pts_data = load_channel_points()
    channels = _ranking_channels()  # FIX: must match build_champions_text()'s channel
    # source exactly, otherwise champ_count here can undercount vs the real
    # number of champion rows in the text, desyncing emoji_ids from the 🔮
    # placeholders and shifting every emoji after the mismatch onto the wrong row.
    excluded_keys = set(load_config().get("champion_excluded_call_keys", []))
    champ_snap_now = load_config().get("champion_milestone_snapshot", {})
    champ_count = sum(
        1 for ch in channels
        if _channel_qualifies_champion(ch, pts_data, champ_snap_now, excluded_keys)
    )
    n = min(champ_count, 10)  # real champion rows (0–10), no padding

    emoji_ids = [CHAMPIONS_PREMIUM_EMOJIS["star"]]  # header star (row 0 🔮)
    for i in range(1, n + 1):
        emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS.get(i, CHAMPIONS_PREMIUM_EMOJIS[1]))
        emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["arrow"])  # second 🔮 in every row
    # 3 info points (⚡ premium emoji) then the Web line star.
    emoji_ids.extend([CHAMPIONS_PREMIUM_EMOJIS["bolt"]] * 3)
    emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["star"])  # Web line star (last 🔮)

    # Try to reconnect userbot if disconnected
    if userbot_client and not userbot_client.is_connected():
        try:
            await userbot_client.connect()
            logger.info("✅ Userbot reconnected for champions update")
        except Exception as e_rc:
            logger.warning(f"Userbot reconnect failed: {e_rc}")
            userbot_client = None

    if userbot_client:
        try:
            from telethon.extensions.html import parse as tl_html_parse
            plain_text, base_entities = tl_html_parse(text)
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids, forced_pack=None)
            await _locked_userbot_edit(
                TARGET_CHANNEL, POST_CHAMPIONS, plain_text,
                formatting_entities=all_entities, link_preview=False
            )
            _LAST_CHAMP_TEXT = text
            logger.info("✅ Champions post 134 updated with premium emojis")
            return True
        except Exception as e:
            if "not modified" in str(e).lower():
                _LAST_CHAMP_TEXT = text
                return True
            logger.error(f"Champions premium emoji edit failed: {e}")

    # Userbot was configured but failed — do NOT fall back to bot API.
    # Falling back strips premium emojis permanently from the post.
    # The champions_job runs every 2 min and will retry via userbot automatically.
    if userbot_client is not None:
        logger.warning("Champions userbot edit failed — skipping bot API fallback to preserve premium emojis. Will retry next cycle.")
        return False

    # Userbot never configured — use bot API as the only option
    try:
        await bot.edit_message_caption(
            chat_id=TARGET_CHANNEL, message_id=POST_CHAMPIONS,
            caption=text, parse_mode="HTML"
        )
        logger.info("✅ Champions post 134 updated via bot API (userbot not configured)")
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            chat_id=TARGET_CHANNEL, message_id=POST_CHAMPIONS,
            text=text, parse_mode="HTML", disable_web_page_preview=True
        )
        logger.info("✅ Champions post 134 updated via bot API text fallback")
        return True
    except Exception as e2:
        logger.error(f"Champions bot API fallback also failed: {e2}")
        return False

CHAMPIONS_RESET_DAYS = 7   # Champion KOLs list + points auto-reset every 7 days

async def _auto_champions_reset(now):
    """Same as /refreshchampions: snapshot max milestone per call (old X records don't count),
    zero ALL channel points, start a fresh 7-day cycle. Leaderboard (post 136) is NOT touched."""
    c = load_config()
    champ_snap = {}
    for ck, _ in tracked_calls.items():
        ms = list(sent_milestones.get(ck, set()))
        champ_snap[ck] = max(ms) if ms else 0
    c["champion_milestone_snapshot"] = champ_snap
    c["champion_excluded_call_keys"] = []
    c["last_champions_update"] = ""
    c["last_champions_reset"]  = now.isoformat()
    save_config(c)
    async with _points_lock:
        save_channel_points({})
    logger.info(f"🏆 Champions AUTO-RESET (every {CHAMPIONS_RESET_DAYS} days): points zeroed, "
                f"{len(champ_snap)} calls snapshotted")

async def champions_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 134 (champions) every 1-2 minutes. Resets all points every 7 days."""
    config   = load_config()
    now      = datetime.utcnow()

    # ── automatic 7-day reset (Champions only) ────────────────────────────────
    try:
        last_reset = config.get("last_champions_reset", "")
        if not last_reset:
            # no cycle recorded yet -> start the 7-day clock now, do NOT wipe the current list
            cfg_set("last_champions_reset", now.isoformat())
        elif (now - datetime.fromisoformat(last_reset)) >= timedelta(days=CHAMPIONS_RESET_DAYS):
            await _auto_champions_reset(now)
            config = load_config()
    except Exception as e:
        logger.error(f"Champions auto-reset check failed: {e}")

    # ── regular update cooldown (2 min) ──────────────────────────────────────
    last_upd = config.get("last_champions_update", "")
    try:
        if last_upd:
            last_dt = datetime.fromisoformat(last_upd)
            if now - last_dt < timedelta(seconds=5): return
    except Exception: pass
    try:
        ok = await _update_champions_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_champions_update", now.isoformat())
    except Exception as e:
        logger.error(f"Champions update failed: {e}")

async def _update_trending_with_premium_emojis(bot, chain_tokens=None):
    """Fetch trending and edit post 135 with premium emojis via userbot.
    If chain_tokens is provided, skips the fetch (used by /refreshtrending to avoid double-fetch).
    """
    if chain_tokens is None:
        chain_tokens = await fetch_trending()
        save_trending_cache(chain_tokens)  # persist so next run uses price-only update
    text, emoji_ids     = build_trending_text_and_emojis(chain_tokens)

    if userbot_client:
        try:
            from telethon.extensions.html import parse as tl_html_parse
            plain_text, base_entities = tl_html_parse(text)
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids, forced_pack=None)
            await _locked_userbot_edit(
                TARGET_CHANNEL, POST_TRENDING, plain_text,
                formatting_entities=all_entities, link_preview=False
            )
            logger.info("✅ Trending post 135 updated with premium emojis")
            return True
        except Exception as e:
            if "not modified" in str(e).lower():
                return True
            logger.error(f"Trending premium emoji edit failed: {e}")
    try:
        await bot.edit_message_caption(
            chat_id=TARGET_CHANNEL, message_id=POST_TRENDING,
            caption=text, parse_mode="HTML"
        )
        logger.info("✅ Trending post 135 updated via bot API caption")
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            chat_id=TARGET_CHANNEL, message_id=POST_TRENDING,
            text=text, parse_mode="HTML", disable_web_page_preview=True
        )
        return True
    except Exception as e:
        logger.error(f"Trending bot fallback edit failed: {e}")
        return False

TRENDING_AUTO_RESET_HOURS = 24  # auto-refresh token list every 24 hours

# Owner ne "MOMENTUM ACTIVE" posts filhal band karwa di hain. Job schedule bhi
# off hai aur ye flag double safety hai — dobara chalu karne ke liye True kar do.
MOMENTUM_POSTS_ENABLED = False   # PERMANENTLY REMOVED — do not re-enable
