# ===== commands.py — Public + OWNER commands, /ownerhelp panels, button_callback =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).

async def notify_owners(bot, text, reply_markup=None):
    """Send an owner notification to EVERY owner + admin.

    Previously only OWNER_ID was messaged and a single failure (blocked bot,
    HTML parse error, wrong id) silently swallowed the whole notification —
    which is why new KOL requests never arrived. Now: all owners + admins,
    HTML first with a plain-text retry, and every failure is logged.

    FIX (2026-09-26): now returns the list of [chat_id, message_id] pairs
    that were actually delivered, instead of a plain bool. Callers that only
    checked truthiness still work unchanged (an empty list is falsy). This
    lets callers that need to delete/edit these notifications later (e.g. the
    KOL-request accept/reject flow) find every copy of the message across
    every owner/admin chat.
    """
    targets = []
    for oid in OWNER_IDS:
        if oid and oid not in targets:
            targets.append(oid)
    try:
        for aid in load_admins():
            aid = int(aid)
            if aid not in targets:
                targets.append(aid)
    except Exception:
        pass
    if not targets:
        logger.error("notify_owners: no OWNER_ID/admins configured — notification dropped")
        return []
    sent_refs = []
    for tid in targets:
        try:
            m = await bot.send_message(tid, text, parse_mode="HTML",
                                   reply_markup=reply_markup,
                                   disable_web_page_preview=True)
            sent_refs.append([m.chat_id, m.message_id])
        except Exception as e_html:
            try:
                plain = re.sub(r"<[^>]+>", "", text)
                m2 = await bot.send_message(tid, plain, reply_markup=reply_markup,
                                       disable_web_page_preview=True)
                sent_refs.append([m2.chat_id, m2.message_id])
            except Exception as e_plain:
                logger.error(f"notify_owners failed for {tid}: {e_html} / {e_plain}")
    return sent_refs


# ── KOL-accept DM: config-driven photo + owner's 3 premium emojis (2026-09-26) ──
# Owner-specified premium emoji IDs for the "you've been listed" DM:
KOL_ACCEPT_EMOJI_HEART   = 5203913930681896358   # 💟 next to "Congratulations"
KOL_ACCEPT_EMOJI_PARTY   = 5845762471985422139   # 🎉 next to "@WizardScan"
KOL_ACCEPT_EMOJI_CRYSTAL = 5816473758554136304   # 🔮 next to "Private:"
KOL_PRIVATE_GROUP_LINK   = "https://t.me/+Yhpbj_bDsn04YWE0"

def _kol_accept_caption(username: str, channel: str) -> str:
    """Build the exact 'you've been listed' DM text the owner asked for,
    with the 3 premium emojis in place of the plain 💟/🎉/🔮."""
    if username and not str(username).startswith("User#"):
        who = f"@{html.escape(str(username))}"
    else:
        who = "there"
    ch = html.escape(str(channel))
    return (
        f'<tg-emoji emoji-id="{KOL_ACCEPT_EMOJI_HEART}">💟</tg-emoji> <b>Congratulations {who}!</b>\n\n'
        f'Your channel <b>@{ch}</b> has been listed on @WizardScan. '
        f'<tg-emoji emoji-id="{KOL_ACCEPT_EMOJI_PARTY}">🎉</tg-emoji> \n\n'
        f'The bot will now track all your calls and send you X alerts as well. '
        f'The bot also tracks PinkSale and Cheesepad tokens, whether the presale/fairlaunch is live or '
        f'not, and provides key details like soft cap, hard cap, min/max buy, current raise, end time, '
        f'and more.\n\n'
        f'You\u2019re officially our partner from today, so please send a request to join our private '
        f'group:\n\n'
        f'<tg-emoji emoji-id="{KOL_ACCEPT_EMOJI_CRYSTAL}">🔮</tg-emoji><b>Private:</b> {KOL_PRIVATE_GROUP_LINK}'
    )

async def _send_kol_accept_dm(bot, uid: int, username: str, channel: str):
    """Send the accept DM. Uses the photo/video set via
    /setkolacceptmedia (config["command_media"]["kol_accept"]) when the
    owner has configured one; otherwise falls back to text-only so the DM
    still goes out before any media is set."""
    caption = _kol_accept_caption(username, channel)
    media = get_command_media("kol_accept")
    if media and media.get("file_id"):
        try:
            if media.get("type") == "video":
                await bot.send_video(uid, video=media["file_id"], caption=caption, parse_mode="HTML")
            else:
                await bot.send_photo(uid, photo=media["file_id"], caption=caption, parse_mode="HTML")
            return
        except Exception as e:
            logger.warning(f"kol_accept media DM to {uid} failed, falling back to text: {e}")
    try:
        await bot.send_message(uid, caption, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"kol_accept DM to {uid} failed: {e}")


# ── /submit confirmation reply: config-driven photo + owner's 2 emojis (2026-09-26) ──
KOL_SUBMIT_EMOJI_BOOK = 6044119257308995249   # 📔 next to "DM to @Wizard_Scan for Fast Track"

def _kol_submit_confirm_caption(channel: str) -> str:
    """Build the owner's exact 'Request Submitted' reply text, sent right
    after a successful /submit @channel. PE_CRYSTAL already carries the
    5888670372093502546 emoji the owner asked for here."""
    ch = html.escape(str(channel))
    return (
        f'{PE_CRYSTAL} <b>REQUEST SUBMITTED</b>\n\n'
        f'Your request to track <b>@{ch}</b> has been successfully submitted to our team.\n\n'
        f'Our team will review your request and add your channel to the tracking system if approved.\n\n'
        f'Review time can take anywhere from 1 day to 1 month, depending on the current queue.\n\n'
        f'<tg-emoji emoji-id="{KOL_SUBMIT_EMOJI_BOOK}">📔</tg-emoji> DM to @Wizard_Scan for Fast Track'
    )

async def _send_submit_confirm_reply(message, channel: str):
    """Reply to a successful /submit @channel with the owner's template.
    Sends the bundled IMG_REQUEST_SUBMITTED banner by default; an owner
    override set via /setsubmitconfirmmedia (config["command_media"]
    ["submit_confirm"]) takes priority if present, else falls back to
    text-only if neither is available."""
    caption = _kol_submit_confirm_caption(channel)
    media = get_command_media("submit_confirm")
    if media and media.get("file_id"):
        try:
            if media.get("type") == "video":
                await message.reply_video(video=media["file_id"], caption=caption, parse_mode="HTML")
            else:
                await message.reply_photo(photo=media["file_id"], caption=caption, parse_mode="HTML")
            return
        except Exception as e:
            logger.warning(f"submit_confirm media reply failed, falling back to bundled image: {e}")
    msg = await send_photo_safe(message, IMG_REQUEST_SUBMITTED, caption)
    if msg:
        return
    await message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=True)


async def _delete_owner_request_msgs(bot, entries):
    """Delete every notify_owners() copy of a KOL-request notification
    (across every owner/admin chat) once the request has been resolved.

    Used by both the accept and reject flows so a decided request doesn't
    keep sitting there with live Accept/Reject buttons — including every
    duplicate notification when several people submitted the same channel.
    `entries` is an iterable of the pending-request dicts (each may carry an
    "owner_msgs": [[chat_id, message_id], ...] list saved at submit time).
    """
    seen = set()
    for entry in entries:
        for ref in (entry.get("owner_msgs") or []):
            try:
                chat_id, msg_id = ref[0], ref[1]
            except Exception:
                continue
            key = (chat_id, msg_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                await bot.delete_message(chat_id, msg_id)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

BACK_EMOJI_ID = 4956591954088428445   # Back button premium emoji

KOL_REQUEST_TEXT = (
    f'{PE_CRYSTAL} <b>REQUEST YOUR KOL</b>\n\n'
    'We listed a few KOLs for free initially, but no more free listings are '
    'available. Still, use the command below to submit your channel for review. '
    'If approved, we\u2019ll list it; otherwise, paid listing is the only option.\n\n'
    f'{PE_CRYSTAL} <b>Send:</b> <code>/submit @Channel</code>'
)

DM_ALERTS_TEXT = (
    f'{PE_CRYSTAL} <b>DM ALERTS</b>\n\n'
    'Want the bot to forward X alerts from your channel or any other channel '
    'directly to your DMs? The bot can forward 2x and higher X alerts. '
    'Just use the commands below:\n\n'
    f'{PE_CRYSTAL} /subscribe @channel\n\n'
    '<b>Send <code>/unsubscribe @channel</code> to unsub.</b>'
)

FAST_TRACK_TEXT = (
    f'{PE_CRYSTAL} <b>FAST TRACK ACCESS</b>\n\n'
    'Contact the team to get your channel fast-tracked! We don\u2019t offer free '
    'listings, so paid listing may be your best option. For paid listings, '
    'contact the owner. Your KOL will be listed not only on @WizardScan but also '
    'on our website, based on its quality.\n\n'
    f'{PE_CRYSTAL} <b>Fast Track provides:</b>\n\n'
    f'{PE_WAND} Priority Review\n'
    f'{PE_WAND} Lifetime Tracking\n'
    f'{PE_WAND} Faster Channel Approval\n\n'
    'Contact our team for Fast Track access.'
)


def _back_btn(plain=False):
    """Back button. plain=True -> bina emoji (sirf Leaderboard ke liye)."""
    if plain:
        return InlineKeyboardButton("Back", callback_data="back_menu")
    return _icon_kb("Back", BACK_EMOJI_ID, callback_data="back_menu")


def _owner_btn():
    # WIZARD_DEV_LINK = https://t.me/Wizard_Scan  (agar owner ka link alag hai to yahan badal do)
    return _pkb("🔮 OWNER 🔮", url=WIZARD_DEV_LINK)


def _is_x_button(b):
    url = (getattr(b, "url", None) or "").lower()
    txt = (getattr(b, "text", "") or "").lower().strip()
    return ("x.com" in url or "twitter.com" in url
            or "twitter" in txt or txt in ("x", "\U0001d54f"))


def _contact_kb_with_back(markup=None):
    """CONTACT_BUTTONS se X/Twitter button hata ke, sabse neeche Back lagata hai."""
    markup = markup or CONTACT_BUTTONS
    rows = []
    for row in markup.inline_keyboard:
        kept = [b for b in row if not _is_x_button(b)]
        if kept:
            rows.append(kept)
    rows.append([_back_btn()])
    return InlineKeyboardMarkup(rows)


async def _del_msg(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def _send_screen(query, key, text, kb, img=None, vid=None, media_cmd=None):
    """Naya screen bhejo (media > image/video > text), phir purana command reply delete."""
    msg = query.message
    if media_cmd:
        bm = get_command_media(media_cmd)
    else:
        bm = (cfg_get("button_media", {}) or {}).get(key)
    sent = False
    if bm and bm.get("file_id"):
        try:
            if bm.get("type") == "photo":
                await msg.reply_photo(photo=bm["file_id"], caption=text,
                                      parse_mode="HTML", reply_markup=kb)
            else:
                await msg.reply_video(video=bm["file_id"], caption=text,
                                      parse_mode="HTML", reply_markup=kb)
            sent = True
        except Exception:
            pass
    if not sent and img:
        sent = bool(await send_photo_safe(msg, img, text, reply_markup=kb))
    if not sent and vid:
        sent = bool(await send_video_safe(msg, vid, text, reply_markup=kb))
    if not sent:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=kb,
                             disable_web_page_preview=True)
    await _del_msg(msg)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; add_user(user.id, user.username, user.first_name)

    # Handle deep links (e.g. /start xray_channel_100)
    if context.args:
        arg = context.args[0]
        if arg.startswith("cp_"):
            pid = arg[3:]
            rec = load_ps_projects().get(pid)
            if not rec:
                await update.message.reply_text(
                    "⚠️ This CheesePad project is no longer available."); return
            ok = await send_details_message(context.bot, update.effective_chat.id, "cp",
                                            build_cp_details_text, pid, rec)
            if not ok:
                await update.message.reply_text("⚠️ Could not load the details. Please try again.")
            return
        if arg.startswith("ps_"):
            pid = arg[3:]
            rec = load_ps_projects().get(pid)
            if not rec:
                await update.message.reply_text(
                    "⚠️ This PinkSale project is no longer available."); return
            ok = await send_details_message(context.bot, update.effective_chat.id, "ps",
                                            build_ps_details_text, pid, rec)
            if not ok:
                await update.message.reply_text("⚠️ Could not load the details. Please try again.")
            return
        if arg.startswith("buybot_") or arg.startswith("xray_"):
            # Buy Bot + X-Ray features permanently removed
            try:
                await update.message.reply_text("This feature is no longer available.")
            except Exception:
                pass
            return
        if arg.startswith("details_"):
            sid = arg[len("details_"):]
            entry = _xalert_details_map().get(sid)
            if isinstance(entry, dict):
                call_key = entry.get("call_key")
                d_xval   = entry.get("x_val")
            else:
                call_key = entry   # legacy entries were a plain call_key string
                d_xval   = None
            call = tracked_calls.get(call_key) if call_key else None
            if not call:
                await update.message.reply_text(
                    "⚠️ This signal's details are no longer available.")
                return
            try:
                subs = await get_channel_subscribers(context.bot, call.get("channel", "?"))
                wizrad_text = build_wizrad_intelligence_text(call, x_val=d_xval, subscribers=subs)
                # OWNER FIX (2026-09-19): /setxalertvideo now actually exists.
                # Prefer the owner-configured Telegram file_id (set via
                # /setxalertvideo) — it survives redeploys, unlike the old
                # hardcoded local path which silently failed with
                # FileNotFoundError on any container that didn't ship that
                # exact file. Fall back to the local file (legacy) and then
                # plain text if neither is available.
                custom_video = cfg_get("xalert_details_video")
                try:
                    # NOTE: do not slice the raw HTML string at 1024 chars —
                    # Telegram's caption limit applies to the PARSED/visible
                    # text (tg-emoji/<b>/<a> tags don't count), which for
                    # this template stays well under 1024 even with 8 top
                    # callers. Slicing the raw HTML risks cutting mid-tag and
                    # breaking the parse entirely.
                    if custom_video and custom_video.get("file_id"):
                        if custom_video.get("type") == "photo":
                            await update.message.reply_photo(
                                photo=custom_video["file_id"], caption=wizrad_text, parse_mode="HTML")
                        else:
                            await update.message.reply_video(
                                video=custom_video["file_id"], caption=wizrad_text, parse_mode="HTML")
                    else:
                        with open(VID_X_ALERT_DETAILS, "rb") as f:
                            await update.message.reply_video(
                                video=f, caption=wizrad_text, parse_mode="HTML")
                except FileNotFoundError:
                    logger.warning(f"X-alert details video not found: {VID_X_ALERT_DETAILS}")
                    await update.message.reply_text(
                        wizrad_text, parse_mode="HTML", disable_web_page_preview=True)
                except Exception as e1:
                    # One or more premium emoji IDs may be wrong/unrecognized
                    # (Telegram raises Document_invalid for that) — never let
                    # that break Details entirely. Retry once with plain
                    # glyphs instead of the tg-emoji tags so the content
                    # still reaches the user.
                    logger.error(f"WIZRAD intelligence premium-emoji send failed, retrying plain: {e1}")
                    plain_text = strip_owner_emoji_markers(wizrad_text)
                    try:
                        if custom_video and custom_video.get("file_id"):
                            if custom_video.get("type") == "photo":
                                await update.message.reply_photo(
                                    photo=custom_video["file_id"], caption=plain_text, parse_mode="HTML")
                            else:
                                await update.message.reply_video(
                                    video=custom_video["file_id"], caption=plain_text, parse_mode="HTML")
                        else:
                            with open(VID_X_ALERT_DETAILS, "rb") as f:
                                await update.message.reply_video(
                                    video=f, caption=plain_text, parse_mode="HTML")
                    except Exception as e2:
                        logger.error(f"WIZRAD intelligence plain video send also failed: {e2}")
                        await update.message.reply_text(
                            plain_text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"WIZRAD intelligence send failed: {e}")
                await update.message.reply_text("⚠️ Couldn't load details right now, please try again.")
            return

    welcome = cfg_get("start_text", DEFAULT_START_TEXT)
    kb      = InlineKeyboardMarkup([[_pkb("🔮 Command 🔮", callback_data="command_menu")]])
    media   = cfg_get("start_media") or get_command_media("start")
    if media and media.get("file_id"):
        fid, ftype = media["file_id"], media.get("type","video")
        try:
            if ftype == "photo": await update.message.reply_photo(photo=fid, caption=welcome, parse_mode="HTML", reply_markup=kb)
            else:                await update.message.reply_video(video=fid, caption=welcome, parse_mode="HTML", reply_markup=kb)
            return
        except Exception as e: logger.error(f"Start media: {e}")
    msg = await send_video_safe(update.message, VID_START, welcome, reply_markup=kb)
    if not msg:
        await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=kb)

# ── Public command media ────────────────────────────────────────────────
# Owner can attach a photo/video to any of these public commands with
# /setcommandmedia <cmd>. Stored in config["command_media"][cmd].
PUBLIC_MEDIA_CMDS = [
    "start", "command", "submit", "subscribe", "linkme", "linksmart", "linkinfo", "history",
]

PUBLIC_TEXT_CMDS = ["submit", "subscribe", "linkme", "linksmart", "linkinfo", "history"]

def get_public_text(cmd: str, default: str) -> str:
    """Owner-set text for a public command (/settext), warna built-in default."""
    try:
        txt = (cfg_get("public_texts", {}) or {}).get(cmd)
        if isinstance(txt, str) and txt.strip():
            return txt
    except Exception:
        pass
    return default


def get_command_media(cmd: str):
    cm = cfg_get("command_media", {}) or {}
    m  = cm.get(cmd)
    return m if isinstance(m, dict) and m.get("file_id") else None

async def send_cmd_media(message, cmd: str, text: str, reply_markup=None) -> bool:
    """Send `text` using the owner-configured media for `cmd`. False if none/failed."""
    m = get_command_media(cmd)
    if not m:
        return False
    try:
        if m.get("type") == "photo":
            await message.reply_photo(photo=m["file_id"], caption=text,
                                      parse_mode="HTML", reply_markup=reply_markup)
        else:
            await message.reply_video(video=m["file_id"], caption=text,
                                      parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception as e:
        logger.warning(f"command media send failed for /{cmd}: {e}")
        return False

async def _send_command_menu(message, context):
    caption  = cfg_get("command_text", DEFAULT_COMMAND_TEXT)
    keyboard = build_command_keyboard()
    # NOTE: "command_media" is the per-public-command map ({"submit": {...}}).
    # The /command MENU media lives under its own key so the two never clash.
    media    = cfg_get("menu_media") or get_command_media("command")
    sent     = None
    if media and media.get("file_id"):
        fid, ftype = media["file_id"], media.get("type","photo")
        try:
            if ftype == "photo": sent = await message.reply_photo(photo=fid, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            else:                sent = await message.reply_video(video=fid, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception: pass
    if not sent:
        sent = await message.reply_text(caption, parse_mode="HTML", reply_markup=keyboard)

async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    await _send_command_menu(update.message, context)

async def cmd_xcommand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

async def cmd_xlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

async def _send_channel_overview(msg, channel):
    """Message 1: summary + last 10 calls. Message 2: weekly / 15-day / 30-day options."""
    try:
        await refresh_channel_calls_live(channel)
        calls = get_call_history(channel)
        await send_record(msg, channel, calls, "overview")
    except Exception as e:
        logger.error(f"record overview @{channel} failed: {e}")
        await msg.reply_text(f"⚠️ Could not display the record for @{channel}. Please try again in a moment.")

async def _send_period_record(msg, channel, days):
    """Weekly (7) / 15-day / 30-day record for one channel."""
    channels = [c.lower() for c in load_channels()]
    if channel.lower() not in channels:
        await msg.reply_text(
            f"<b>{PE_CRYSTAL} @{html.escape(channel)}</b>\n\n"
            f"❌ This channel is not currently tracked by Wizard Scan.\n\n"
            f"To request tracking, contact our team.",
            parse_mode="HTML", reply_markup=CHAT_US_BUTTON)
        return
    try:
        await refresh_channel_calls_live(channel)
        calls = get_call_history(channel)
        await send_record(msg, channel, calls, "period", days)
    except Exception as e:
        logger.error(f"period record @{channel} {days}d failed: {e}")
        await msg.reply_text(f"⚠️ Could not display the record for @{channel}. Please try again in a moment.")

async def _send_top_record(msg, channel):
    """Top Calls record (best calls of all time) with video + button."""
    channels = [c.lower() for c in load_channels()]
    if channel.lower() not in channels:
        await msg.reply_text(
            f"<b>{PE_CRYSTAL} @{html.escape(channel)}</b>\n\n"
            f"❌ This channel is not currently tracked by Wizard Scan.\n\n"
            f"To request tracking, contact our team.",
            parse_mode="HTML", reply_markup=CHAT_US_BUTTON)
        return
    try:
        await refresh_channel_calls_live(channel)
        calls = get_call_history(channel)
        await send_record(msg, channel, calls, "top")
    except Exception as e:
        logger.error(f"top record @{channel} failed: {e}")
        await msg.reply_text(f"⚠️ Could not display the record for @{channel}. Please try again in a moment.")

async def _period_command(update, context, days):
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    if not context.args:
        cmd = RECORD_PERIOD_CMDS.get(days, "/weekly")
        await update.message.reply_text(
            f"Please include a channel name.\n\nExample: <code>{cmd} @channelname</code>",
            parse_mode="HTML")
        return
    channel = context.args[0].lstrip("@").strip()
    await _send_period_record(update.message, channel, days)

async def cmd_topcalls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    if not context.args:
        await update.message.reply_text(
            "Please include a channel name.\n\nExample: <code>/topcalls @channelname</code>",
            parse_mode="HTML")
        return
    await _send_top_record(update.message, context.args[0].lstrip("@").strip())

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _period_command(update, context, 7)

async def cmd_15days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _period_command(update, context, 15)

async def cmd_30days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _period_command(update, context, 30)

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dedicated /history @channel command."""
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    if not context.args:
        _info = get_public_text("history", DEFAULT_HISTORY_INFO)
        if await send_cmd_media(update.message, "history", _info):
            return
        msg = await send_photo_safe(update.message, IMG_HISTORY, _info)
        if not msg:
            await update.message.reply_text(_info, parse_mode="HTML")
        return
    channel  = context.args[0].lstrip("@").strip()
    channels = [c.lower() for c in load_channels()]
    if channel.lower() in channels:
        await _send_channel_overview(update.message, channel)
    else:
        await update.message.reply_text(
            f"<b>{PE_CRYSTAL} @{channel}</b>\n\n"
            f"❌ This channel is not currently tracked by Wizard Scan.\n\n"
            f"To request tracking, contact our team.\n\n"
            f"For priority review, contact our team below.",
            parse_mode="HTML", reply_markup=CHAT_US_BUTTON)

async def cmd_linkinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show /linkme usage explanation."""
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    _info = get_public_text("linkinfo", get_public_text("linkme", DEFAULT_LINKME_INFO))
    if await send_cmd_media(update.message, "linkinfo", _info):
        return
    msg = await send_photo_safe(update.message, IMG_LINKME, _info)
    if not msg:
        await update.message.reply_text(_info, parse_mode="HTML")

async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; add_user(user.id, user.username, user.first_name)
    if not context.args:
        btexts = cfg_get("button_texts", {})
        text = KOL_REQUEST_TEXT
        kb   = InlineKeyboardMarkup([[_owner_btn()], [_back_btn()]])
        cm   = cfg_get("command_media", {}).get("submit")
        sent = False
        if cm and cm.get("file_id"):
            try:
                fn = update.message.reply_photo if cm.get("type") == "photo" else update.message.reply_video
                await fn(**{("photo" if cm.get("type")=="photo" else "video"): cm["file_id"]},
                         caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(update.message, IMG_KOLREQUEST, text, reply_markup=kb)
            if not msg:
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    channel = context.args[0].lstrip("@").strip()
    if not channel: await update.message.reply_text("⚠️ Please provide a valid channel username."); return

    # FIX (2026-09-26): users often send a full link — "/submit
    # https://t.me/bigbangcall" — instead of the channel's @username. That
    # left "https://t.me/bigbangcall" stored as the literal channel name
    # (never a real request the team could act on) with no indication to
    # the user anything was wrong. Detect a link/path-style argument, reply
    # in English with the correct /submit @username form (built from the
    # SAME username they linked to), and stop before creating a request.
    _raw_arg = context.args[0].strip()
    if re.search(r'(https?://|t\.me/|telegram\.me/)', _raw_arg, re.I):
        _clean_channel = _norm_channel_arg(_raw_arg)
        if _clean_channel:
            await update.message.reply_text(
                f"⚠️ <b>Wrong format.</b> Your request did not reach our team.\n\n"
                f"Please send it like this: <code>/submit @{html.escape(_clean_channel)}</code>\n\n"
                f"Use the channel's @username, not a link.",
                parse_mode="HTML")
        else:
            await update.message.reply_text(
                "⚠️ <b>Wrong format.</b> Your request did not reach our team.\n\n"
                "Please send it like this: <code>/submit @ChannelUsername</code>\n\n"
                "Use the channel's @username, not a link.",
                parse_mode="HTML")
        return

    channels = load_channels()
    if channel.lower() in [c.lower() for c in channels]:
        await update.message.reply_text(
            f"✅ <b>@{channel} is already tracked</b> by Wizard Scan!\n\nType <code>@{channel}</code> in the bot to view call history.",
            parse_mode="HTML"); return

    pending = load_pending()
    for req in pending.values():
        if req["channel"].lower() == channel.lower() and req["user_id"] == user.id:
            await update.message.reply_text(
                f"⏳ You already have a pending request for <b>@{channel}</b>.\nPlease wait for our team to review it.",
                parse_mode="HTML"); return

    req_id  = str(uuid.uuid4())[:8]
    username = user.username or f"User#{user.id}"
    pending[req_id] = {"user_id": user.id, "username": username,
                       "channel": channel, "ts": datetime.utcnow().isoformat()}
    save_pending(pending)

    owner_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"kreq|{user.id}|{channel[:28]}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"krej|{user.id}|{channel[:28]}"),
    ]])
    owner_msgs = await notify_owners(context.bot,
        f"{PE_CRYSTAL} <b>New KOL Tracking Request</b>\n\n"
        f"👤 From: @{html.escape(username)} (ID: <code>{user.id}</code>)\n"
        f"📡 Channel: <b>@{html.escape(channel)}</b>\n"
        f"🆔 Request: <code>{req_id}</code>\n"
        f"🕐 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"📋 Full list: /pendingkols",
        reply_markup=owner_kb)
    if not owner_msgs:
        logger.error(f"KOL request {req_id} (@{channel}) could not be delivered to any owner")
    else:
        # FIX (2026-09-26): remember exactly which [chat_id, message_id] this
        # request's notification landed as in every owner/admin chat, so the
        # accept/reject flow can delete every copy later instead of leaving
        # stale request messages behind (especially when several people
        # submit the same channel — each one's notification gets cleaned up).
        pending[req_id]["owner_msgs"] = owner_msgs
        save_pending(pending)

    await _send_submit_confirm_reply(update.message, channel)

def _norm_channel_arg(raw: str) -> str:
    """@Name, Name, t.me/Name, https://t.me/Name/ → 'name' (lowercase)."""
    v = (raw or "").strip()
    v = re.sub(r'^https?://', '', v, flags=re.I)
    v = re.sub(r'^(www\.)?t\.me/', '', v, flags=re.I)
    v = v.split('/')[0].split('?')[0]
    return v.lstrip('@').strip().lower()

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; add_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not context.args:
        text = DM_ALERTS_TEXT
        cm   = cfg_get("command_media", {}).get("subscribe")
        sent = False
        if cm and cm.get("file_id"):
            try:
                fn = update.message.reply_photo if cm.get("type") == "photo" else update.message.reply_video
                await fn(**{("photo" if cm.get("type")=="photo" else "video"): cm["file_id"]},
                         caption=text, parse_mode="HTML")
                sent = True
            except Exception: pass
        if not sent:
            await update.message.reply_text(text, parse_mode="HTML")
        return
    channel = _norm_channel_arg(context.args[0])
    tracked = [c.lower() for c in load_channels()]
    if channel not in tracked:
        await update.message.reply_text(
            f"⚠️ <b>@{channel} is not a listed/tracked channel.</b>\n\n"
            f"Wizard Scan does not currently track this channel, so you will not "
            f"receive any alerts for it. Please check the channel name, or use "
            f"/subscribe with a channel that Wizard Scan tracks.",
            parse_mode="HTML"
        )
        return
    subs    = load_channel_subs()
    ch_list = subs.get(channel, [])
    if uid in ch_list:
        await update.message.reply_text(
            f"🔔 <b>You're already subscribed to @{channel}.</b>\n\n"
            f"Send <code>/unsubscribe @{channel}</code> if you want to stop DM alerts.",
            parse_mode="HTML"
        )
    else:
        ch_list.append(uid)
        subs[channel] = ch_list
        save_channel_subs(subs)
        await update.message.reply_text(
            f"🔔 <b>Subscribed to @{channel}!</b>\n\n"
            f"Every time this KOL hits a milestone, you'll get the alert here in DM.\n\n"
            f"Send <code>/unsubscribe @{channel}</code> to stop.",
            parse_mode="HTML"
        )

async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; add_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/unsubscribe @channelname</code>\n\n"
            "Example: <code>/unsubscribe @SomeCryptoKOL</code>",
            parse_mode="HTML"); return
    channel = _norm_channel_arg(context.args[0])
    subs    = load_channel_subs()
    ch_list = subs.get(channel, [])
    if uid in ch_list:
        ch_list.remove(uid)
        subs[channel] = ch_list
        save_channel_subs(subs)
        await update.message.reply_text(
            f"🔕 <b>Unsubscribed from @{channel}</b>\n\nYou will no longer receive DM alerts for this channel.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"ℹ️ You weren't subscribed to @{channel}.",
            parse_mode="HTML"
        )

async def cmd_linkme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    if not context.args:
        _info = get_public_text("linkme", DEFAULT_LINKME_INFO)
        cm   = cfg_get("command_media", {}).get("linkme")
        sent = False
        if cm and cm.get("file_id"):
            try:
                fn = update.message.reply_photo if cm.get("type") == "photo" else update.message.reply_video
                await fn(**{("photo" if cm.get("type")=="photo" else "video"): cm["file_id"]},
                         caption=_info, parse_mode="HTML")
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(update.message, IMG_LINKME, _info)
            if not msg:
                await update.message.reply_text(_info, parse_mode="HTML")
        return

    kol_ch = context.args[0].lstrip("@").lower()
    channels = [c.lower() for c in load_channels()]
    if kol_ch not in channels:
        await update.message.reply_text(
            f"⚠️ <b>@{kol_ch}</b> is not currently tracked by Wizard Scan.\n\n"
            f"Only owners of tracked KOL channels can use /linkme.\n\n"
            f"If you'd like your channel tracked, contact our team.",
            parse_mode="HTML"); return

    # Verify the user is actually an admin in the specified channel
    is_admin = False
    try:
        member = await context.bot.get_chat_member(f"@{kol_ch}", u.id)
        if member.status in ("administrator", "creator"):
            is_admin = True
    except Exception:
        pass  # Channel may be private — skip admin check, trust the user

    if not is_admin:
        await update.message.reply_text(
            f"⚠️ You don't appear to be an admin of @{kol_ch}.\n\n"
            f"Please make sure:\n"
            f"1. You are an admin of @{kol_ch}\n"
            f"2. @WIZARD_SCAN_BOT is also added as admin with post permission\n\n"
            f"Then try <code>/linkme @{kol_ch}</code> again.",
            parse_mode="HTML"); return

    # Link: alerts for this KOL channel will also be forwarded INTO the same channel
    linked = load_linked_channels()
    linked[kol_ch] = f"@{kol_ch}"
    save_linked_channels(linked)
    # Store KOL owner for forward DMs on milestones
    kol_owners = load_kol_owners()
    kol_owners[kol_ch] = u.id
    save_kol_owners(kol_owners)

    await update.message.reply_text(
        f"✅ <b>Channel Linked!</b>\n\n"
        f"📡 KOL Channel: @{kol_ch}\n"
        f"📬 Milestone Alerts → @{kol_ch}\n\n"
        f"Whenever a call from @{kol_ch} hits 2X, 5X, 10X, 50X, 100X, the Wizard Scan alert "
        f"will automatically be forwarded to @{kol_ch} so your community sees it instantly.\n\n"
        f"⚠️ Make sure @WIZARD_SCAN_BOT stays as admin in @{kol_ch} with post permission.",
        parse_mode="HTML")
    await notify_owners(context.bot,
        f"🔗 <b>Channel Linked</b>\n\nKOL: @{html.escape(kol_ch)} (self-forward)\n"
        f"By: <code>{u.id}</code> @{html.escape(u.username or '')}")

async def cmd_linksmart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Same as /linkme, but only forwards the 'solid' milestone tiers
    (5X, 10X, 20X...100X, then everything above 100X normally) instead of
    every single milestone — for KOLs who don't want the +5X spam."""
    u = update.effective_user; add_user(u.id, u.username, u.first_name)
    if not context.args:
        _info = get_public_text("linksmart", DEFAULT_LINKSMART_INFO)
        cm   = cfg_get("command_media", {}).get("linksmart")
        sent = False
        if cm and cm.get("file_id"):
            try:
                fn = update.message.reply_photo if cm.get("type") == "photo" else update.message.reply_video
                await fn(**{("photo" if cm.get("type")=="photo" else "video"): cm["file_id"]},
                         caption=_info, parse_mode="HTML")
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(update.message, IMG_LINKME, _info)
            if not msg:
                await update.message.reply_text(_info, parse_mode="HTML")
        return

    kol_ch = context.args[0].lstrip("@").lower()
    channels = [c.lower() for c in load_channels()]
    if kol_ch not in channels:
        await update.message.reply_text(
            f"⚠️ <b>@{kol_ch}</b> is not currently tracked by Wizard Scan.\n\n"
            f"Only owners of tracked KOL channels can use /linksmart.\n\n"
            f"If you'd like your channel tracked, contact our team.",
            parse_mode="HTML"); return

    # Verify the user is actually an admin in the specified channel
    is_admin = False
    try:
        member = await context.bot.get_chat_member(f"@{kol_ch}", u.id)
        if member.status in ("administrator", "creator"):
            is_admin = True
    except Exception:
        pass  # Channel may be private — skip admin check, trust the user

    if not is_admin:
        await update.message.reply_text(
            f"⚠️ You don't appear to be an admin of @{kol_ch}.\n\n"
            f"Please make sure:\n"
            f"1. You are an admin of @{kol_ch}\n"
            f"2. @WIZARD_SCAN_BOT is also added as admin with post permission\n\n"
            f"Then try <code>/linksmart @{kol_ch}</code> again.",
            parse_mode="HTML"); return

    # Link in "smart" mode: only the milestones in SMART_LINK_MILESTONES (plus
    # everything above 100X) get forwarded — this REPLACES a plain /linkme
    # link for the same channel if one existed.
    linked = load_linked_channels()
    linked[kol_ch] = {"target": f"@{kol_ch}", "mode": "smart"}
    save_linked_channels(linked)
    kol_owners = load_kol_owners()
    kol_owners[kol_ch] = u.id
    save_kol_owners(kol_owners)

    await update.message.reply_text(
        f"✅ <b>Channel Linked (Smart Mode)!</b>\n\n"
        f"📡 KOL Channel: @{kol_ch}\n"
        f"📬 Milestone Alerts → @{kol_ch}\n\n"
        f"Only the solid tiers (5X, 10X, 20X...100X, then everything above 100X normally) "
        f"will be forwarded to @{kol_ch} — no +5X spam.\n\n"
        f"⚠️ Make sure @WIZARD_SCAN_BOT stays as admin in @{kol_ch} with post permission.",
        parse_mode="HTML")
    await notify_owners(context.bot,
        f"🔗 <b>Channel Linked (Smart)</b>\n\nKOL: @{html.escape(kol_ch)} (self-forward, smart)\n"
        f"By: <code>{u.id}</code> @{html.escape(u.username or '')}")

# ─── Lookup ───────────────────────────────────────────────────────────────────
async def handle_lookup(update: Update, text: str):
    msg = update.message
    # Twitter/X lookup removed

    tg_match = TG_MENTION_RE.match(text.strip())
    if tg_match:
        channel  = tg_match.group(1)
        channels = [c.lower() for c in load_channels()]
        if channel.lower() in channels:
            await _send_channel_overview(msg, channel)
        else:
            await msg.reply_text(
                f"<b>{PE_CRYSTAL} @{channel}</b>\n\n❌ This channel is not tracked by Wizard Scan.\n\n"
                f"To request tracking, contact our team for priority review.",
                parse_mode="HTML", reply_markup=CHAT_US_BUTTON)
        return True
    return False

# ─── Button callbacks ─────────────────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data  = query.data
    btexts = cfg_get("button_texts", {})

    if data == "command_menu":
        await _send_command_menu(query.message, context)

    elif data == "back_menu":
        await _send_command_menu(query.message, context)
        await _del_msg(query.message)

    elif data == "kol_request":
        kb = InlineKeyboardMarkup([[_owner_btn()], [_back_btn()]])
        await _send_screen(query, "kol_request", KOL_REQUEST_TEXT, kb, img=IMG_KOLREQUEST)

    elif data == "promo_hub":
        cap = btexts.get("promo_hub", DEFAULT_PROMO_HUB)
        await _send_screen(query, "promo_hub", cap, _contact_kb_with_back(), img=IMG_PROMO)

    elif data == "tracked_kols":
        if not is_admin_or_owner(query.from_user.id):
            await query.answer("🔒 Owner only.", show_alert=True)
            return
        channels = load_channels()
        kb = InlineKeyboardMarkup([[_pkb("🔮 MAIN CHANNEL 🔮", url="https://t.me/WizardScan")], [_back_btn()]])
        bm = cfg_get("button_media", {}).get("tracked_kols")
        if not channels:
            cap_txt = f"<b>{PE_CRYSTAL} TRACKED KOLs (0)</b>\n\nNo channels tracked yet.\n\n<i>Type /history @channelname to see their call history</i>"
            sent = False
            if bm and bm.get("file_id"):
                try:
                    fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                    await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                             caption=cap_txt, parse_mode="HTML", reply_markup=kb)
                    sent = True
                except Exception: pass
            if not sent:
                msg = await send_photo_safe(query.message, IMG_TRACKED, cap_txt, reply_markup=kb)
                if not msg: await query.message.reply_text("No channels tracked yet.", parse_mode="HTML", reply_markup=kb)
        else:
            all_lines = [
                f"- @{html.escape(c)}"
                for i, c in enumerate(channels)
            ]
            header1 = f"<b>{PE_CRYSTAL} TRACKED KOLs ({len(channels)})</b>\n\nHall of Tracked KOLs:\n\n"
            footer1 = f"\n\n<i>Type /history @channelname to see their call history</i>"
            # --- first message: photo + caption (max 1024 chars) ---
            first_lines = []
            for line in all_lines:
                if len(header1 + "\n".join(first_lines + [line]) + footer1) <= 1024:
                    first_lines.append(line)
                else:
                    break
            first_caption = header1 + "\n".join(first_lines) + footer1
            sent = False
            if bm and bm.get("file_id"):
                try:
                    fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                    await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                             caption=first_caption, parse_mode="HTML", reply_markup=kb)
                    sent = True
                except Exception: pass
            if not sent:
                msg = await send_photo_safe(query.message, IMG_TRACKED, first_caption, reply_markup=kb)
                if not msg:
                    await query.message.reply_text(first_caption, parse_mode="HTML", reply_markup=kb)
            # --- subsequent messages: text chunks (max 4096 chars) ---
            remaining = all_lines[len(first_lines):]
            chunk_hdr  = "<b>🔮 TRACKED KOLs (continued)</b>\n\n"
            chunk: list = []
            for line in remaining:
                if len(chunk_hdr + "\n".join(chunk + [line])) <= 4096:
                    chunk.append(line)
                else:
                    await query.message.reply_text(
                        chunk_hdr + "\n".join(chunk),
                        parse_mode="HTML", disable_web_page_preview=True)
                    chunk = [line]
            if chunk:
                await query.message.reply_text(
                    chunk_hdr + "\n".join(chunk),
                    parse_mode="HTML", disable_web_page_preview=True)

        await _del_msg(query.message)

    elif data == "leaderboard":
        text = btexts.get("leaderboard", DEFAULT_LEADERBOARD)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("View Leaderboard", url="https://t.me/WizardScan/136")],
            [InlineKeyboardButton("View Champions", url="https://t.me/WizardScan/134")],
            [_back_btn(plain=True)],          # leaderboard me emoji NAHI
        ])
        await _send_screen(query, "leaderboard", text, kb, img=IMG_LEADERBOARD)

    elif data == "alert_rules":
        text = btexts.get("alert_rules", DEFAULT_ALERT_RULES)
        kb = InlineKeyboardMarkup([[_back_btn()]])
        await _send_screen(query, "alert_rules", text, kb, img=IMG_ALERT)

    elif data == "dm_alerts":
        kb = InlineKeyboardMarkup([[_back_btn()]])
        await _send_screen(query, "dm_alerts", DM_ALERTS_TEXT, kb, media_cmd="subscribe")

    elif data == "fast_track":
        await _send_screen(query, "fast_track", FAST_TRACK_TEXT,
                           _contact_kb_with_back(), img=IMG_FASTTRACK)

    elif data == "chat_us":
        cap = btexts.get("chat_us", DEFAULT_CHAT_US)
        await _send_screen(query, "chat_us", cap, _contact_kb_with_back(), vid=VID_CHAT_US)

    # ── KOL request approve ───────────────────────────────────────────────────
    elif data.startswith("kreq|"):
        _, uid_str, channel = data.split("|", 2)
        uid = int(uid_str)
        channels = load_channels()
        if channel.lower() not in [c.lower() for c in channels]:
            channels.append(channel); save_channels(channels)
        # Store KOL owner for forward DMs on milestones
        kol_owners = load_kol_owners()
        kol_owners[channel.lower()] = uid
        save_kol_owners(kol_owners)
        # FIX (2026-09-26): when two different accounts /submit the SAME
        # channel, accepting one used to leave the other's request sitting
        # in /pendingkols forever (the delete filter only matched THIS
        # button's user_id). Now every pending request for this channel,
        # from any user, is resolved together — each requester gets the
        # same "accepted" DM, and none of the duplicates linger.
        # FIX (2026-09-26): callback_data only ever carries channel[:28] (the
        # Accept/Reject buttons truncate it when built), but pending.json
        # stores the FULL channel name. For any channel name over 28 chars
        # this equality check used to never match, so the reject/accept flow
        # silently failed to remove that request from pending — it just sat
        # there forever. Comparing the pending channel's own [:28] against
        # the (already truncated) callback channel makes both sides match.
        _pending_acc = load_pending()
        _to_del_acc = [k for k, v in _pending_acc.items()
                       if v.get("channel", "")[:28].lower() == channel.lower()]
        _resolved_entries_acc = [_pending_acc[k] for k in _to_del_acc]
        _uid_username_acc = {v.get("user_id"): v.get("username") for v in _resolved_entries_acc}
        _other_uids = {v.get("user_id") for v in _resolved_entries_acc} - {uid}
        for k in _to_del_acc: del _pending_acc[k]
        if _to_del_acc: save_pending(_pending_acc)
        # FIX (2026-09-26): the accept DM now uses the owner's "you've been
        # listed" template (private-group invite + the 3 requested premium
        # emojis), sent as a photo when one is set via /setkolacceptmedia.
        await _send_kol_accept_dm(context.bot, uid, _uid_username_acc.get(uid), channel)
        for _other_uid in _other_uids:
            await _send_kol_accept_dm(context.bot, _other_uid, _uid_username_acc.get(_other_uid), channel)
        _dup_note = f" ({len(_other_uids)} duplicate request(s) auto-resolved)" if _other_uids else ""
        await query.message.reply_text(f"✅ <b>@{channel}</b> confirmed and added to tracking!{_dup_note}", parse_mode="HTML")
        # FIX (2026-09-26): delete every copy of this request's owner
        # notification (every owner/admin, every duplicate submitter) instead
        # of only clearing the buttons on the one message that was clicked.
        await _delete_owner_request_msgs(context.bot, _resolved_entries_acc)
        try: await query.message.delete()
        except Exception: pass

    # ── KOL request reject — show confirm first (prevent accidental rejects) ──
    elif data.startswith("krej|"):
        _, uid_str, channel = data.split("|", 2)
        # FIX (2026-09-26): a Reject button can be on two different kinds of
        # message — the original notification sent at /submit time, or a
        # fresh card shown by /pendingkols. Only the first kind was ever
        # remembered as "owner_msgs" on the pending request, so rejecting a
        # /pendingkols card (or an OLD request submitted before this fix
        # existed) left that card sitting on screen forever with live
        # buttons even after the request was rejected. Recording THIS exact
        # card's own [chat_id, message_id] onto every matching pending
        # entry — right here, the moment Reject is first clicked — means
        # krej_confirm below will always find and delete it, no matter which
        # screen it came from or how old the request is.
        try:
            _pending_now = load_pending()
            _changed = False
            for _v in _pending_now.values():
                if _v.get("channel", "")[:28].lower() == channel.lower():
                    _refs = _v.setdefault("owner_msgs", [])
                    _ref = [query.message.chat_id, query.message.message_id]
                    if _ref not in _refs:
                        _refs.append(_ref)
                        _changed = True
            if _changed:
                save_pending(_pending_now)
        except Exception as e:
            logger.warning(f"krej: failed to record card message ref: {e}")
        confirm_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Haan, Reject Karo",  callback_data=f"krej_confirm|{uid_str}|{channel}"),
            InlineKeyboardButton("🔙 Cancel",             callback_data="krej_cancel"),
        ]])
        await query.message.reply_text(
            f"⚠️ <b>Confirm Rejection</b>\n\n"
            f"Kya aap sure hain ke <b>@{channel}</b> ko reject karna chahte hain?\n\n"
            f"Yeh action KOL ko DM bheji jaegi.",
            parse_mode="HTML", reply_markup=confirm_kb)

    elif data.startswith("krej_confirm|"):
        _, uid_str, channel = data.split("|", 2)
        uid = int(uid_str)
        fast_kb = InlineKeyboardMarkup([
            [_pkb("🔮 Fast Track 🔮",      callback_data="fast_track")],
            [_pkb("🔮  Chat With Us  🔮", callback_data="chat_us")],
        ])
        # FIX (2026-09-26): resolve every pending request for this channel,
        # from any user, together — same reasoning as the accept branch
        # above, so duplicate /submit requests for the same channel don't
        # linger in /pendingkols after the owner has already decided.
        # ALSO FIX: match on the pending channel's own [:28] (see note in the
        # accept branch above) — this is the actual reason a rejected
        # request used to keep sitting in /pendingkols forever for any
        # channel name longer than 28 characters.
        _pending = load_pending()
        _to_del = [k for k, v in _pending.items()
                   if v.get("channel", "")[:28].lower() == channel.lower()]
        _resolved_entries = [_pending[k] for k in _to_del]
        _other_uids = {v.get("user_id") for v in _resolved_entries} - {uid}
        for k in _to_del: del _pending[k]
        if _to_del: save_pending(_pending)
        try:
            await context.bot.send_message(uid,
                f"📋 <b>Channel Review Update</b>\n\n"
                f"Your channel <b>@{channel}</b> has been rejected by our team.\n\n"
                f"Please try again. For priority review, contact our team for Fast Track.\n\n"
                f"Thank you.",
                parse_mode="HTML", reply_markup=fast_kb)
        except Exception as e: logger.warning(f"DM to {uid} failed: {e}")
        for _other_uid in _other_uids:
            try:
                await context.bot.send_message(_other_uid,
                    f"📋 <b>Channel Review Update</b>\n\n"
                    f"Your channel <b>@{channel}</b> has been rejected by our team.\n\n"
                    f"Please try again. For priority review, contact our team for Fast Track.\n\n"
                    f"Thank you.",
                    parse_mode="HTML", reply_markup=fast_kb)
            except Exception as e: logger.warning(f"DM to {_other_uid} failed: {e}")
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        _dup_note = f" ({len(_other_uids)} duplicate request(s) auto-resolved)" if _other_uids else ""
        await query.message.reply_text(f"❌ <b>@{channel}</b> request rejected.{_dup_note}", parse_mode="HTML")
        # FIX (2026-09-26): delete every copy of this request's owner
        # notification message — across every owner/admin, and for every
        # duplicate submitter of this same channel — so a rejected request
        # no longer sits there with live Accept/Reject buttons. This is the
        # main fix requested: rejecting a channel now removes its request
        # message(s) even if 10 different people had submitted it.
        await _delete_owner_request_msgs(context.bot, _resolved_entries)

    elif data == "krej_cancel":
        try: await query.message.delete()
        except Exception: pass

    # ── Weekly / 15-day / 30-day record buttons ───────────────────────────────
    elif data.startswith("hp|"):
        _hp = data.split("|")
        if len(_hp) >= 3 and _hp[2] == "top":
            await _send_top_record(query.message, _hp[1])
        elif len(_hp) >= 3 and _hp[2].isdigit() and int(_hp[2]) in RECORD_PERIODS:
            await _send_period_record(query.message, _hp[1], int(_hp[2]))

    # ── History filter ────────────────────────────────────────────────────────
    elif data.startswith("h|"):
        parts = data.split("|")
        if len(parts) >= 3:
            _, channel, filt = parts[0], parts[1], parts[2]
            is_detail = len(parts) >= 4 and parts[3] == "d"
            chain_map = {"bnb":"BNB","eth":"ETH","sol":"SOL","base":"BASE","rh":"RH","ton":"TON","arc":"ARC"}
            is_top = (filt == "top")
            await refresh_channel_calls_live(channel)
            if is_top:         calls = get_call_history(channel, top=True)
            elif filt in chain_map: calls = get_call_history(channel, chain_filter=chain_map[filt])
            else:              calls = get_call_history(channel)

            if is_detail:
                # OWNER REQUIREMENT: buttons on the new detailed lookup view
                # delete the old post and send a fresh one, rather than
                # editing in place — matches how a brand-new record should
                # feel each time a filter is picked.
                # FIX 1: every button here (TOP, a chain, or ALL) used to fall
                # back to the same BEST CALL / TOTAL POINTS / Last 5 Calls
                # snapshot regardless of which filter was tapped, so it looked
                # like nothing happened. TOP and a chain (or ALL) are requests
                # to see the actual filtered list of calls — every one of
                # them, however many there are — not the summary snapshot.
                # FIX 2: these filtered lists can be long, so the media
                # (photo/video) is dropped for these views — text only —
                # so the full list of calls always fits and shows cleanly.
                kb   = history_keyboard(channel, detail=True)
                try:
                    await query.message.delete()
                except Exception as e:
                    logger.warning(f"lookup detail: old message delete failed: {e}")
                try:
                    _pages = format_history_pages(channel, calls, is_top=is_top)
                    for _i, _p in enumerate(_pages):
                        await query.message.chat.send_message(
                            _p, parse_mode="HTML",
                            reply_markup=(kb if _i == len(_pages) - 1 else None),
                            disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"lookup detail @{channel} ({filt}) resend failed ({len(calls)} calls): {e}")
                return

            text = format_history(channel, calls, is_top=is_top)
            kb   = history_keyboard(channel)
            _pages = format_history_pages(channel, calls, is_top=is_top)
            if len(_pages) > 1:
                # Long list (e.g. ALL with 100+ calls): send every page as a
                # fresh message instead of editing one message in place.
                try:
                    await query.message.delete()
                except Exception:
                    pass
                try:
                    for _i, _p in enumerate(_pages):
                        await query.message.chat.send_message(
                            _p, parse_mode="HTML",
                            reply_markup=(kb if _i == len(_pages) - 1 else None),
                            disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"history filter @{channel} ({filt}) multi-page send failed ({len(calls)} calls): {e}")
                return
            # FIX: if history_media is set, the original /history reply is a
            # video/photo (caption = record text), not a plain text message —
            # edit_text() fails on those (Telegram needs edit_caption for
            # media messages), which silently broke every Top/chain filter
            # button once media got attached. Detect which one this message
            # actually is and edit the right part.
            is_media_msg = bool(query.message.video or query.message.photo)
            try:
                if is_media_msg:
                    cap, rest = _split_html_for_caption(text)
                    await query.message.edit_caption(caption=cap, parse_mode="HTML",
                                                     reply_markup=(kb if not rest else None))
                    if rest:
                        await query.message.reply_text(rest, parse_mode="HTML", reply_markup=kb,
                                                        disable_web_page_preview=True)
                else:
                    await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                try:
                    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                except Exception as e2:
                    logger.error(f"history filter @{channel} ({filt}) send failed ({len(calls)} calls): {e2}")

# ═══════════════════════════════════════════════════════════════════════════════
# OWNER COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

# ── /ownerhelp — button based control panel ─────────────────────────────
OH_PANELS = {}
_OH_PANELS_RAW = {
    "fix": ("🆘 Masla Aa Raha Hai? — Fix Karo",
        "🆘 <b>PROBLEM FIX CENTER</b>\n\n"
        "Neeche apna masla choose karo. Bot ek ek kar ke sawal poochega — "
        "koi lambi command likhne ki zaroorat nahi.\n\n"
        "1️⃣ <b>Skipped call</b> — bot ne KOL ki call miss kar di\n"
        "2️⃣ <b>Late track</b> — call late track hui, MC galat hai\n"
        "3️⃣ <b>X alert nahi aayi</b> — 2X/3X/10X update miss ho gaya\n"
        "4️⃣ <b>Call rok do</b> — galat call freeze karni hai\n"
        "5️⃣ <b>Call check</b> — kisi call ki asli performance dekho\n"
        "6️⃣ <b>RH galat MC</b> — RH call ka MC/X galat track hua, record reset karke sahi MC se shuru\n\n"
        "ℹ️ Har cheez buttons se hoti hai — <b>/cancel</b> se kabhi bhi rok sakte ho.",
        [("1️⃣ Skipped call add", "oh:fix:missed"),
         ("2️⃣ Late track MC fix", "oh:fix:latemc"),
         ("3️⃣ X alert nahi aayi", "oh:fix:fixx"),
         ("4️⃣ Call freeze", "oh:fix:freeze"),
         ("5️⃣ Call check", "oh:fix:check"),
         ("6️⃣ RH galat MC fix", "oh:fix:rhfix")]),

    "posts": ("📣 Channel Post Media",
        "📣 <b>CHANNEL POSTS — MEDIA</b>\n"
        "(jo posts channel ke andar jaati hain)\n\n"
        "🚀 <b>X Alert Media (2X, 5X, 10X…)</b>\n"
        "/setmedia 2 — us X ka media set (media reply karo)\n"
        "/clearmedia 2 · /listmedia\n"
        "ℹ️ Jis X ka media set nahi, wo channel me post nahi hoti — "
        "lekin trending / champions / record me phir bhi count hoti hai.\n\n"
        "🩷 <b>PinkSale Media</b>\n"
        "/addpsmedia · /listpsmedia · /removepsmedia N · /clearpsmedia\n\n"
        "🧀 <b>CheesePad Media</b>\n"
        "/addcpmedia · /listcpmedia · /removecpmedia N · /clearcpmedia\n"
        "ℹ️ CheesePad ab sirf apna media use karta hai (PinkSale ka nahi).\n\n"
        "📄 <b>Bot ke andar TOKEN DETAILS ka media</b>\n"
        "🧀 /addcpdmedia · /listcpdmedia · /removecpdmedia N · /clearcpdmedia\n"
        "🩷 /addpsdmedia · /listpsdmedia · /removepsdmedia N · /clearpsdmedia\n\n"
        "📉 <b>Dropped a Call Videos (max 20)</b>\n"
        "/adddroppedvideo · /listdroppedvideos\n"
        "/removedroppedvideo N · /cleardroppedvideos · /testdropped\n\n"
        "🔥 <b>Hot Streak Alert Media</b> (3+ consecutive 2X+ calls)\n"
        "/sethsmedia red|blue|green|golden — us color ka media set (reply karo)\n"
        "/clearhsmedia <color> · /listhsmedia\n"
        "/testalertHSA — sample alert (sab 4 colors) bhej ke dekho\n"
        "ℹ️ Colors har post pe rotate hote hain: red → blue → green → golden → red…",
        [("➕ PinkSale Media", "oh:do:ps"), ("➕ CheesePad Media", "oh:do:cp"),
         ("📄 CP Details Media", "oh:do:cpd"), ("📄 PS Details Media", "oh:do:psd"),
         ("➕ Dropped Video", "oh:do:drop")]),

    "bot": ("🤖 Bot Media & Templates",
        "🤖 <b>BOT KE ANDAR — MEDIA + TEMPLATES</b>\n\n"
        "📸 <b>Public command media</b>\n"
        "/setcommandmedia COMMAND (media reply karo)\n"
        "/clearcommandmedia COMMAND\n"
        "Commands: <code>" + "</code>, <code>".join(PUBLIC_MEDIA_CMDS) + "</code>\n\n"
        "🎛 <b>Menu / Start</b>\n"
        "/setstart — /start ka text + media\n"
        "/setcommand — /command ka text\n"
        "/setcommandvideo — /command menu ki video\n\n"
        "🔘 <b>Buttons ke jawab (text)</b>\n"
        "/editbutton kol_request · promo_hub · alert_rules\n"
        "/editbutton leaderboard · fast_track · chat_us\n"
        "/editbtnlabel ID LABEL — button ka naam badlo\n\n"
        "🖼 <b>Button media (har button ki apni photo/video)</b>\n"
        "/setbuttonmedia BUTTON · /clearbuttonmedia BUTTON\n"
        "Buttons: kol_request, promo_hub, tracked_kols, leaderboard, "
        "fast_track, chat_us\n\n"
        "✏️ <b>Alert Templates</b>\n"
        "/settemplate · /edittemplate · /showtemplate\n"
        "/editmilestone 2 · /clearmilestone 2 · /listmilestones\n"
        "/setmilestones 2,5,10,50\n"
        "/setdroppedtemplate · /showdroppedtemplate · /cleardroppedtemplate\n\n"
        "🔍 <b>Preview</b>\n"
        "/previewtemplate 100 · /previewmomentum · /testalert",
        [("🎬 /command video", "oh:tip:setcommandvideo"),
         ("📸 Public cmd media", "oh:tip:setcommandmedia")]),

    "public": ("🌐 Public Commands — Text Set Karo",
        "🌐 <b>PUBLIC COMMANDS — TEXT + MEDIA</b>\n"
        "(jo users bot me use karte hain)\n\n"
        "✏️ <b>Text badlo (naya)</b>\n"
        "/settext subscribe — /subscribe ka info text\n"
        "/settext history — /history ka info text\n"
        "/settext linkme — /linkme ka info text\n"
        "/settext linkinfo — /linkinfo ka text\n"
        "/settext submit — /submit ka text\n"
        "/showtext CMD — abhi ka text dekho\n"
        "/cleartext CMD — default par wapas\n"
        "ℹ️ HTML allowed: <code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, "
        "<code>&lt;code&gt;</code>, <code>&lt;a href&gt;</code>\n\n"
        "📸 <b>Media</b>\n"
         "/setcommandmedia subscribe · history · linkme · linksmart · submit\n"
         "/clearcommandmedia CMD\n\n"
        "🎛 <b>Menu</b>\n"
        "/setstart · /setcommand · /setcommandvideo",
        [("✏️ /settext subscribe", "oh:txt:subscribe"),
         ("✏️ /settext history",   "oh:txt:history"),
         ("✏️ /settext linkme",    "oh:txt:linkme"),
         ("✏️ /settext linkinfo",  "oh:txt:linkinfo"),
         ("✏️ /settext submit",    "oh:txt:submit")]),

    "ranks": ("🏆 Lists, Points & Trending",
        "🏆 <b>LEADERBOARD · CHAMPIONS · POINTS</b>\n\n"
        "🔄 <b>Force refresh</b>\n"
        "/refreshleaderboard — Leaderboard (136)\n"
        "/refreshchampions — Champion KOLs (134)\n"
        "\n"
        "🥇 <b>Champion points</b>\n"
        "/givepoints @channel 50  (minus ke liye -20)\n"
        "/checkpoints @channel · /checkpoints (top 20)\n"
        "/zerocolpoints @channel — ek channel ke points zero\n"
        "/resetallpoints — ⚠️ SAB channels ke points zero (confirm step hai)\n"
        "ℹ️ Champions/Leaderboard refresh se points affect NAHI hote.\n\n"
        "📋 <b>Current rules</b>\n"
        "2X+4 · 5X+9 · 10X+18 · 25X+30 · 50X+45 · 100X+65 · 250X+90\n"
        "48h me 2X na ho → −10 · Champion entry = 100 pts · reset har 7 din\n\n"
        "✏️ Text badalna ho: /editbutton leaderboard",
        [("🔄 Leaderboard", "oh:tip:refreshleaderboard"),
         ("🔄 Champions", "oh:tip:refreshchampions"),
         ("📊 Check points", "oh:tip:checkpoints")]),

    "ps": ("🩷 PinkSale · GemPad · CheesePad",
        "🩷 <b>LAUNCHPAD PANEL</b>\n\n"
        "🔁 <b>Skipped call — manually post</b>\n"
        "<code>/pscall &lt;pinksale link&gt; @caller</code>\n"
        "<code>/cpcall &lt;cheesepad link&gt; @caller</code>\n\n"
        "🧪 <b>Test / debug</b>\n"
        "/pstest LINK · /psdebug LINK · /cptest LINK @caller\n"
        "/psclear address|all · /cpclear address|all\n\n"
        "🎬 <b>Channel template media (max 10, rotate)</b>\n"
        "🩷 /addpsmedia · /listpsmedia · /removepsmedia N · /clearpsmedia\n"
        "🧀 /addcpmedia · /listcpmedia · /removecpmedia N · /clearcpmedia\n\n"
        "📄 <b>Bot ke andar token DETAILS ka media (max 10, rotate)</b>\n"
        "🧀 /addcpdmedia · /listcpdmedia · /removecpdmedia N · /clearcpdmedia\n"
        "🩷 /addpsdmedia · /listpsdmedia · /removepsdmedia N · /clearpsdmedia\n\n"
        "🔗 <b>Referral / affiliate</b>\n"
        "/setpsref CODE|LINK · /showpsref · /clearpsref\n"
        "/setaffiliate 0xWallet\n\n"
        "📊 <b>Projects & live watch</b>\n"
        "/pslist · /pswatch · /psdrop N\n\n"
        "📚 Poori detail: /ownerhelpPS",
        [("➕ PinkSale Media", "oh:do:ps"), ("➕ CheesePad Media", "oh:do:cp"),
         ("📄 CP Details Media", "oh:do:cpd"), ("📄 PS Details Media", "oh:do:psd"),
         ("📊 PS list", "oh:tip:pslist")]),

    "kols": ("📡 Channels & KOLs",
        "📡 <b>CHANNELS & KOLs</b>\n\n"
        "/mychannels · /addchannel @name · /removechannel @name\n"
        "/special — special channel settings\n"
        "/joinkols — userbot ko sab KOL channels join karwao\n\n"
        "📨 <b>Requests</b>\n"
        "/pendingkols — users ki bheji hui KOL requests\n\n"
        "🔗 <b>X (Twitter) handles</b>\n"
        "/addx @channel xhandle · /removex @channel\n\n"
        "🔔 <b>KOL owner DM</b>\n"
        "/setkolowner · /setkolownerid @ch ID\n"
        "/removekolowner @ch · /listkolowners\n\n"
        "📉 <b>Dropped post skip</b> (zyada spam karne wale KOL)\n"
        "/dropskip — channel ki dropped post ek chhor kar / 2 chhor kar. "
        "Call track + X alerts waise hi rehte hain.",
        [("📨 Pending KOLs", "oh:tip:pendingkols"),
         ("📡 My channels", "oh:tip:mychannels"),
         ("📉 Dropped post skip", "oh:dskip:home"),
         ("🔮 Tracked KOLs 🔮", "tracked_kols")]),

    "links": ("🔗 Promo Link & Extra Links",
        "🔗 <b>PROMO LINK (12 ghante)</b>\n\n"
        "/setpromolink — text + link set karo\n"
        "  ➊ Pehli line: text · ➋ Dusri line: URL\n"
        "  ➌ Phir bot premium emoji ID poochega (ya /skip)\n"
        "  ℹ️ Ye link har post ke <b>sabse neeche</b> aata hai — "
        "Champion / Leaderboard KOL line ke baad, ek khali line chhod kar.\n"
        "/clearpromolink — foran hata do\n\n"
        "🔗 <b>Extra Alert Links (hamesha ke liye)</b>\n"
        "/addpostlink EMOJI_ID TEXT URL\n"
        "  Example: <code>/addpostlink 5368324170671202310 ALPHA https://t.me/mychannel</code>\n"
        "/removepostlink N · /listpostlinks\n\n"
        "📣 <b>Promo post (har 25 alerts ke baad)</b>\n"
        "/setpromo · /stoppromo",
        [("🔗 Set promo link", "oh:tip:setpromolink"),
         ("🧹 Clear promo link", "oh:tip:clearpromolink"),
         ("📋 Post links", "oh:tip:listpostlinks")]),

    "users": ("👥 Users, Admins & Userbot",
        "👥 <b>USERS · ADMINS · USERBOT</b>\n\n"
        "/myusers · /mystats · /broadcast · /mediabroadcast\n"
        "/addadmin ID · /removeadmin ID · /listadmins\n\n"
        "🤖 <b>Userbot</b>\n"
        "/userbotlogin · /userbotcheck · /userbotlogout\n"
        "/reconnectuserbot · /qrlogin · /markseen\n"
        "ℹ️ <b>/markseen</b> — restart ke baad purani posts ko 'seen' mark "
        "kar deta hai (spam rok deta hai).\n\n"
        "🔗 Promo link: /setpromolink · /clearpromolink\n"
        "🩷 Launchpad panel: /ownerhelpPS",
        [("👥 My users", "oh:tip:myusers"),
         ("🤖 Userbot check", "oh:tip:userbotcheck")]),

    "tpl": ("🧩 Templates & Premium Emojis (CP · PS)",
        "🧩 <b>TEMPLATE CONTROL — CheesePad 🧀 · PinkSale 🩷</b>\n\n"
        "Har launchpad ke <b>2</b> template hote hain:\n"
        "• <b>channel</b> = bahar wala (main channel post)\n"
        "• <b>details</b> = bot ke andar wala (token details page)\n\n"
        "✏️ <b>Set karo</b>\n"
        "<code>/setcptemplate channel &lt;text&gt;</code>\n"
        "<code>/setcptemplate details &lt;text&gt;</code>\n"
        "<code>/setpstemplate channel &lt;text&gt;</code>\n"
        "<code>/setpstemplate details &lt;text&gt;</code>\n\n"
        "👀 <b>Dekho / reset</b>\n"
        "<code>/cptemplate channel|details</code> · <code>/pstemplate channel|details</code>\n"
        "<code>/resetcptemplate channel|details</code> · <code>/resetpstemplate channel|details</code>\n\n"
        "🧩 <b>Placeholders ki poori guide</b>\n"
        "/cptemplatevars · /pstemplatevars · /templatehelp\n\n"
        "✨ <b>Apni premium emojis (kahin bhi, dono templates me)</b>\n"
        "<code>[[emoji:5773941882832822049]]</code> ya "
        "<code>&lt;tg-emoji emoji-id=\"ID\"&gt;🔮&lt;/tg-emoji&gt;</code>\n"
        "Ye apni ID khud carry karti hain — 🔮 marker count me nahi ginti.\n"
        "🆔 ID nikalne ke liye: /getemoji (premium emoji reply karo)\n\n"
        "🎨 <b>Built-in emoji pack override</b>\n"
        "<code>/setcpemoji &lt;key&gt; &lt;EMOJI_ID&gt;</code> · /listcpemojis · /clearcpemoji key\n"
        "<code>/setpsemoji &lt;key&gt; &lt;EMOJI_ID&gt;</code> · /listpsemojis · /clearpsemoji key\n\n"
        "🧪 <b>Test</b>: <code>/cptest LINK</code> · <code>/pstest LINK</code> "
        "(DM preview) — channel par: <code>/cpcall LINK @caller</code>",
        [("🧀 CP channel (bahar)", "oh:tplv:cp:channel"),
         ("🧀 CP details (andar)", "oh:tplv:cp:details"),
         ("🩷 PS channel (bahar)", "oh:tplv:ps:channel"),
         ("🩷 PS details (andar)", "oh:tplv:ps:details"),
         ("🧀 CP emoji IDs", "oh:tip:listcpemojis"),
         ("🩷 PS emoji IDs", "oh:tip:listpsemojis"),
         ("🆔 Emoji ID lo", "oh:tip:getemoji"),
         ("📘 Poori guide", "oh:tip:templatehelp")]),

    "xemoji": ("🎨 X Range Emojis (2-10 red · 11+ blue)",
        "🎨 <b>X ALERT PREMIUM EMOJIS — RANGE WISE</b>\n\n"
        "Abhi ye chal raha hai:\n"
        "• <b>2X – 10X</b> → 🔴 red pack\n"
        "• <b>11X aur upar</b> → 🔵 blue pack\n\n"
        "➕ <b>Naya range pack add karo</b> (jaise 21-30 ya 31-40):\n"
        "Neeche button dabao — bot pehle range poochega (misal: <code>31 40</code>), "
        "phir <b>ek ek kar ke 14 emoji IDs</b> maangega:\n"
        "BNB · ETH · SOL · TON · BASE · RH · Crystal ball · Teer · "
        "Caller · Twitter · Trade · DEX · Details · DEV\n\n"
        "ℹ️ Kisi emoji ko blue pack wali hi rakhni ho to us step par <b>/skip</b> bhejo.\n"
        "🆔 ID nikalne ke liye: /getemoji (premium emoji reply karo)\n"
        "📋 /listxemojis — saari custom ranges dekho\n"
        "🗑 /removexemojis N — koi range hata do\n"
        "🚫 /cancel — kabhi bhi rok do",
        [("➕ Naya range set karo", "oh:xem:new"),
         ("📋 List ranges", "oh:tip:listxemojis")]),

    "lang": ("Chn, Vtm, Tur, Rus",
        "🌏 <b>LANGUAGE-GROUP TEMPLATES</b>\n\n"
        "KOLs ko Chinese, Vietnamese, Turkish ya Russian tag do — us group ke sab KOLs ka template "
        "EK BAAR mein set ho jata hai, alag alag channel batane ki zaroorat nahi.\n\n"
        "🏷 <b>Tag lagao</b>\n"
        "<code>/setkollang CHANNEL chinese|vietnamese|turkish|russian|english</code> — ek ek karke\n"
        "<code>/addkolstolang chinese</code> — guided, kai KOLs ek sath tag karo\n"
        "/listkollang — sab tagged KOLs dekho\n\n"
        "✏️ <b>X-alert template (poore group ke liye, seedha)</b>\n"
        "<code>/setlangtemplate chinese|vietnamese|turkish|russian LOW HIGH</code> — phir template text\n"
        "/listlangtemplates chinese · /dellangtemplate chinese LOW HIGH\n\n"
        "📣 <b>Dropped-Call template (poore group ke liye, seedha)</b>\n"
        "<code>/setlangdroppedtemplate chinese|vietnamese|turkish|russian</code> — phir template text\n"
        "/showlangdroppedtemplate chinese · /clearlangdroppedtemplate chinese\n\n"
        "ℹ️ Jis KOL ka tag nahi, uski posts English mein hi jati rahengi. "
        "CHAMP/LEAD KOL rank badge hamesha English rehta hai, kisi tag se asar nahi.",
        [("🏷 Set KOL lang", "oh:tip:setkollang"),
         ("➕ Bulk-tag KOLs", "oh:tip:addkolstolang"),
         ("📋 List KOL langs", "oh:tip:listkollang"),
         ("✏️ Set group X tmpl", "oh:tip:setlangtemplate"),
         ("📣 Set group dropped tmpl", "oh:tip:setlangdroppedtemplate")]),

    "extras": ("🚀 Nayi Features (Stats & Premium)",
        "🚀 <b>NAYI FEATURES — STATS &amp; PREMIUM</b>\n\n"
        "📣 <b>Owner</b>\n"
        "/mediakit [din] — advertisers ke liye asli numbers (default 30 din)\n"
        "/addpremium USER_ID DIN · /delpremium USER_ID · /premiumlist\n\n"
        "🌐 <b>Public (sab users)</b>\n"
        "/kolstats @channel — KOL ka hit rate, avg/best X\n"
        "/token CA — kis ne pehle call ki, kitne callers\n"
        "/topdaily [ghante] — top calls · /topkols [din] — top KOLs\n\n"
        "👑 <b>Premium users</b>\n"
        "/premium — status · /alertme 5 — DM mein X alerts (5x se upar)",
        [("📣 Media kit", "oh:tip:mediakit"),
         ("👑 Premium list", "oh:tip:premiumlist"),
         ("🏆 Top daily", "oh:tip:topdaily"),
         ("🥇 Top KOLs", "oh:tip:topkols")]),

    "pages": ("📚 Baqi Help Pages",
        "📚 <b>HELP PAGES — TARTEEB SE</b>\n\n"
        "1️⃣ <b>/ownerhelp</b> — yehi buttons wala main panel (sab kuch yahan hai)\n"
        "2️⃣ <b>/ownerhelp2</b> — points reset, dropped-call posts, button media, "
        "extra alert links\n"
        "3️⃣ <b>/ownerhelpPS</b> — PinkSale · GemPad · CheesePad panel\n"

        "5️⃣ <b>/ownerhelpfull</b> — poori purani text list (2 parts)\n\n"
        "🆘 Koi masla ho to seedha <b>/fix</b> bhejo — buttons wala fix center khul jayega.",
        [("2️⃣ /ownerhelp2", "oh:tip:ownerhelp2"),
         ("3️⃣ /ownerhelpPS", "oh:tip:ownerhelpPS"),
         ("5️⃣ /ownerhelpfull", "oh:tip:ownerhelpfull")]),
}

# Panel order — sabse pehle problem fix, phir baqi sab
_OH_ORDER = ["fix", "posts", "bot", "tpl", "lang", "xemoji", "public", "ranks", "ps",
             "kols", "links", "users", "extras", "pages"]
for _k in _OH_ORDER:
    if _k in _OH_PANELS_RAW:
        OH_PANELS[_k] = _OH_PANELS_RAW[_k]
for _k, _v in _OH_PANELS_RAW.items():
    OH_PANELS.setdefault(_k, _v)

OH_STATE_MAP = {
    "ps":   (ST_ADD_PS_MEDIA,      "🩷 PinkSale media (video/gif/photo) ab bhejo:"),
    "cp":   (ST_ADD_CP_MEDIA,      "🧀 CheesePad media (video/gif/photo) ab bhejo:"),
    "cpd":  (ST_ADD_CPD_MEDIA,     "🧀 CheesePad DETAILS media (bot ke andar) ab bhejo:"),
    "psd":  (ST_ADD_PSD_MEDIA,     "🩷 PinkSale DETAILS media (bot ke andar) ab bhejo:"),
    "drop": (ST_ADD_DROPPED_VID,   "📉 Dropped-Call video ab bhejo:"),
}

def _oh_main_kb():
    rows = [[InlineKeyboardButton(v[0], callback_data=f"oh:p:{k}")] for k, v in OH_PANELS.items()]
    return InlineKeyboardMarkup(rows)

def _oh_panel_kb(extra):
    rows = []
    pair = []
    for label, data in extra:
        pair.append(InlineKeyboardButton(label, callback_data=data))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="oh:home")])
    return InlineKeyboardMarkup(rows)

OH_HOME_TEXT = (
    f"{PE_CRYSTAL} <b>OWNER CONTROL PANEL</b>\n\n"
    "Neeche se category choose karo — har button me us kaam ki saari commands "
    "tarteeb ke saath hain.\n\n"
    "🆘 <b>Koi masla?</b> Sabse upar wala button dabao — bot khud ek ek sawal "
    "puch kar masla theek kar dega."
)

OH_FIX_FLOWS = {
    "missed": ("missedcall", "📡 Skipped call add karte hain — ek ek sawal:"),
    "latemc": ("latemc",     "🔧 Late track fix karte hain — ek ek sawal:"),
    "fixx":   ("fixx",       "🚀 Missing X alert fix karte hain — ek ek sawal:"),
    "freeze": ("freeze",     "⛔ Call freeze karte hain — ek ek sawal:"),
    "check":  ("xcheck",     "📊 Call check karte hain — ek ek sawal:"),
    "rhfix":  ("rhfix",      "🛠 RH galat MC fix karte hain — ek ek sawal:"),
}

@owner_only
async def cmd_ownerhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(OH_HOME_TEXT, parse_mode="HTML", reply_markup=_oh_main_kb())


@owner_only
async def cmd_setxemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setxemojis — X range ke liye naye premium emojis set karne ka wizard."""
    owner_edit_state[update.effective_user.id] = {"state": ST_XEM_RANGE}
    await update.message.reply_text(
        "🎨 <b>Naya X range emoji pack</b>\n\n"
        "Pehle batao — <b>kitne X se kitne X tak</b> ye emojis use hongi?\n\n"
        "Aise likho: <code>31 40</code>\n\n"
        "(/cancel se kabhi bhi rok sakte ho)", parse_mode="HTML")

# Maps the labels an owner naturally types (any case, with/without spaces)
# to the XEM_STEPS keys — lets /setxemojisbulk accept one pasted block
# instead of 14 separate one-at-a-time replies.
_XEM_BULK_ALIASES = {
    "bnb": "chain.BNB", "eth": "chain.ETH", "ethereum": "chain.ETH",
    "sol": "chain.SOL", "solana": "chain.SOL",
    "ton": "chain.TON", "base": "chain.BASE",
    "rh": "chain.RH", "robinhood": "chain.RH",
    "arc": "chain.ARC",
    "crystal": "crystal", "crystalball": "crystal", "crystal ball": "crystal",
    "teer": "teer",
    "caller": "caller", "twitter": "twitter", "x": "twitter",
    "trade": "trade", "details": "details", "dex": "dex", "dev": "dev",
}

def _parse_bulk_xem(text: str) -> dict:
    """Parse a pasted block like 'Bnb: 12345\\nSol: 67890\\n...' into
    {xem_key: emoji_id}, matching any of the _XEM_BULK_ALIASES labels
    (case/spacing insensitive), one per line."""
    vals = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z ]+?)\s*[:;\-–]\s*(\d{10,25})\s*$", line)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m.group(1).strip().lower())
        key = _XEM_BULK_ALIASES.get(label) or _XEM_BULK_ALIASES.get(label.replace(" ", ""))
        if key:
            vals[key] = int(m.group(2))
    return vals

@owner_only
async def cmd_setxemojisbulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setxemojisbulk LOW HIGH — then paste ALL emoji IDs in one message,
    one per line as 'Label: ID' (Bnb/Eth/Sol/Ton/Base/Rh/Crystal ball/Teer/
    Caller/Twitter/Trade/Details/Dex/Dev). Faster than /setxemojis' 14-step
    wizard when you already have every ID ready."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "🎨 <b>Bulk X-Range Emoji Pack</b>\n\n"
            "Usage: <code>/setxemojisbulk LOW HIGH</code>\n"
            "Example: <code>/setxemojisbulk 100 499</code>\n\n"
            "Phir agla message mein sab IDs ek sath paste karo, ek line per emoji:\n"
            "<code>Bnb: 5843674404914994680\n"
            "Eth: 5843921236685496043\n"
            "Sol: 5843831420329403072\n"
            "Ton: 5843787134921613128\n"
            "Base: 5843483918820450365\n"
            "Rh: 5846129678804327480\n"
            "Crystal ball: 5841381089947426410\n"
            "Teer: 5843545993982780072\n"
            "Caller: 5843683802303440727\n"
            "Twitter: 5843622358501303870\n"
            "Trade: 5843469169902758985\n"
            "Details: 5843477832851791550\n"
            "Dex: 5843442897587805937\n"
            "Dev: 5843591542110952716</code>\n\n"
            "Jo bhi label miss ho jaye, uske liye built-in default use hoga.",
            parse_mode="HTML"); return
    try:
        low, high = int(context.args[0]), int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye."); return
    if low > high:
        low, high = high, low
    owner_edit_state[update.effective_user.id] = {"state": ST_XEM_BULK, "low": low, "high": high}
    await update.message.reply_text(
        f"✅ Range set: <b>{low}X – {high}X</b>\n\nAb saari emoji IDs ek sath paste karo "
        f"(ek line per emoji, <code>Label: ID</code> format mein):",
        parse_mode="HTML")

@owner_only
async def cmd_listxemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/listxemojis — custom X range emoji packs ki list."""
    packs = load_config().get("x_emoji_packs", [])
    head = ("🎨 <b>X RANGE EMOJI PACKS</b>\n\n"
            "Built-in:\n• 2X – 10X → 🔴 red\n• 11X+ → 🔵 blue\n\n")
    if not packs:
        await update.message.reply_text(
            head + "Custom range abhi koi set nahi.\n➕ /setxemojis se add karo.",
            parse_mode="HTML"); return
    out = [head + "<b>Custom (in ki priority sabse upar hai):</b>"]
    for i, r in enumerate(packs, 1):
        out.append(f"\n<b>{i}. {r.get('low')}X – {r.get('high')}X</b>\n"
                   + _xem_pack_summary(r.get("pack") or {}))
    out.append("\n🗑 Hatane ke liye: /removexemojis N")
    await update.message.reply_text("\n".join(out), parse_mode="HTML")

@owner_only
async def cmd_removexemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/removexemojis N — ek custom X range pack hata do."""
    c     = load_config()
    packs = c.get("x_emoji_packs", [])
    args  = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Aise likho: <code>/removexemojis 1</code>\n"
            "Number /listxemojis se dekho.", parse_mode="HTML"); return
    n = int(args[0])
    if not (1 <= n <= len(packs)):
        await update.message.reply_text(f"⚠️ 1 se {len(packs)} ke darmiyan number bhejo."); return
    gone = packs.pop(n - 1)
    c["x_emoji_packs"] = packs
    save_config(c)
    await update.message.reply_text(
        f"🗑 <b>{gone.get('low')}X – {gone.get('high')}X</b> ka pack hata diya. "
        f"Ab is range par built-in pack (red/blue) chalega.", parse_mode="HTML")

@owner_only
async def cmd_fixpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ek hi solid button panel — bot puchta hai kya masla hai."""
    title, text, extra = OH_PANELS["fix"]
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=_oh_panel_kb(extra),
                                    disable_web_page_preview=True)

@owner_only
async def cmd_templatepanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/templates — CheesePad + PinkSale template & premium-emoji control panel."""
    title, text, extra = OH_PANELS["tpl"]
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=_oh_panel_kb(extra),
                                    disable_web_page_preview=True)


# ─── /dropskip — channel ki "Dropped a Call" POST ek chhor kar / 2 chhor kar ──
# Sirf dropped POST par asar. Call track hoti hai, record me aati hai aur X
# alerts bilkul pehle ki tarah jate hain (dropped_call._drop_skip_decision).
def _dropskip_example(p, s, n=8):
    cyc = max(1, p + s)
    return "  ".join(f"{i+1}{'✅' if (i % cyc) < p else '❌'}" for i in range(n))

def _dropskip_rules():
    return dict(cfg_get("drop_skip_rules", {}) or {})

def _dropskip_home():
    rules = _dropskip_rules()
    if rules:
        body = "\n".join(
            f"• @{html.escape(ch)} — <b>{r.get('post',1)} post · {r.get('skip',0)} skip</b>\n"
            f"   <code>{_dropskip_example(int(r.get('post',1)), int(r.get('skip',0)), 6)}</code>"
            for ch, r in sorted(rules.items()))
    else:
        body = "<i>Abhi kisi channel par rule set nahi.</i>"
    text = (
        "📉 <b>DROPPED POST SKIP</b>\n\n"
        "Jo KOL din me bohot calls karte hain, unki <b>Dropped a Call post</b> kam ki ja sakti hai.\n"
        "✅ = post hogi · ❌ = post nahi hogi\n\n"
        "ℹ️ Skip hui call phir bhi <b>track</b> hoti hai, record me aati hai aur uske "
        "<b>saare X alerts</b> normal aate hain.\n\n"
        f"<b>Active rules:</b>\n{body}"
    )
    rows = [[InlineKeyboardButton("➕ Naya / change rule", callback_data="oh:dskip:new")]]
    for ch in sorted(rules):
        rows.append([InlineKeyboardButton(f"🗑 @{ch} ka rule hatao", callback_data=f"oh:dskip:del:{ch}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="oh:p:kols")])
    return text, InlineKeyboardMarkup(rows)

def _dropskip_apply(ch, p, s):
    rules = _dropskip_rules()
    rules[ch] = {"post": int(p), "skip": int(s)}
    cfg_set("drop_skip_rules", rules)
    counters = dict(cfg_get("drop_skip_counters", {}) or {})
    counters[ch] = 0          # naye rule par count 1 se shuru
    cfg_set("drop_skip_counters", counters)
    return (f"✅ <b>@{html.escape(ch)}</b> — {p} post · {s} skip\n"
            f"<code>{_dropskip_example(int(p), int(s))}</code>\n\n"
            f"Agli dropped call se ye pattern chalega (1 se shuru).\n"
            f"Call track + X alerts pehle jaise.")

def _dropskip_preset_kb(ch):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1 post · 1 skip (ek chhor kar ek)", callback_data=f"oh:dskip:set:{ch}:1:1")],
        [InlineKeyboardButton("1 post · 2 skip", callback_data=f"oh:dskip:set:{ch}:1:2")],
        [InlineKeyboardButton("1 post · 3 skip", callback_data=f"oh:dskip:set:{ch}:1:3")],
        [InlineKeyboardButton("2 post · 1 skip", callback_data=f"oh:dskip:set:{ch}:2:1")],
        [InlineKeyboardButton("⬅️ Back", callback_data="oh:dskip:home")],
    ])

async def _dropskip_callback(query, uid, data):
    parts = data.split(":")            # oh:dskip:<action>[:...]
    action = parts[2] if len(parts) > 2 else "home"
    if action == "home":
        owner_edit_state[uid] = {"state": None}
        text, kb = _dropskip_home()
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif action == "new":
        owner_edit_state[uid] = {"state": ST_DROPSKIP_CH}
        await query.message.reply_text(
            "📉 Kis KOL channel ki dropped post kam karni hai?\n\n"
            "Username bhejo, jaise: <code>@kolname</code>\n\n(/cancel se rok sakte ho)",
            parse_mode="HTML")
    elif action == "set" and len(parts) >= 5:
        ch, p, s = parts[3], int(parts[4]), int(parts[5]) if len(parts) > 5 else 1
        owner_edit_state[uid] = {"state": None}
        await query.message.reply_text(_dropskip_apply(ch, p, s), parse_mode="HTML")
    elif action == "del" and len(parts) >= 4:
        ch = parts[3]
        rules = _dropskip_rules(); rules.pop(ch, None)
        cfg_set("drop_skip_rules", rules)
        counters = dict(cfg_get("drop_skip_counters", {}) or {}); counters.pop(ch, None)
        cfg_set("drop_skip_counters", counters)
        text, kb = _dropskip_home()
        try:
            await query.message.edit_text(f"🗑 @{html.escape(ch)} ka rule hata diya — ab sab dropped posts aayengi.\n\n" + text,
                                          parse_mode="HTML", reply_markup=kb)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

@owner_only
async def cmd_dropskip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dropskip — panel. Shortcut: /dropskip @channel POST SKIP  (e.g. /dropskip @kol 1 2)
    Rule hatane ke liye: /dropskip off @channel"""
    args = context.args or []
    if len(args) >= 2 and args[0].lower() in ("off", "remove", "clear"):
        ch = _norm_channel_arg(args[1])
        rules = _dropskip_rules(); rules.pop(ch, None); cfg_set("drop_skip_rules", rules)
        await update.message.reply_text(f"🗑 @{ch} ka rule hata diya.")
        return
    if len(args) >= 3 and args[1].isdigit() and args[2].isdigit() and int(args[1]) >= 1:
        ch = _norm_channel_arg(args[0])
        await update.message.reply_text(_dropskip_apply(ch, int(args[1]), int(args[2])), parse_mode="HTML")
        return
    text, kb = _dropskip_home()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

async def cb_ownerhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid   = query.from_user.id
    if not is_admin_or_owner(uid):
        await query.answer("Owner only.", show_alert=True); return
    data = query.data or ""
    await query.answer()
    if data == "oh:home":
        try:
            await query.message.edit_text(OH_HOME_TEXT, parse_mode="HTML", reply_markup=_oh_main_kb())
        except Exception:
            await query.message.reply_text(OH_HOME_TEXT, parse_mode="HTML", reply_markup=_oh_main_kb())
        return
    if data.startswith("oh:p:"):
        key = data.split(":", 2)[2]
        panel = OH_PANELS.get(key)
        if not panel: return
        _, text, extra = panel
        kb = _oh_panel_kb(extra)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb,
                                          disable_web_page_preview=True)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb,
                                           disable_web_page_preview=True)
        return
    if data.startswith("oh:fix:"):
        key = data.split(":", 2)[2]
        flow = OH_FIX_FLOWS.get(key)
        if not flow: return
        wizard_state.pop(uid, None)
        await query.message.reply_text(flow[1] + "\n\n(/cancel se kabhi bhi rok sakte ho)")
        await _wiz_goto(query.message, uid, flow[0], ("post" if flow[0] == "rhfix" else "channel"), {})
        return
    if data.startswith("oh:xem:"):
        owner_edit_state[uid] = {"state": ST_XEM_RANGE}
        await query.message.reply_text(
            "🎨 <b>Naya X range emoji pack</b>\n\n"
            "Pehle batao — <b>kitne X se kitne X tak</b> ye emojis use hongi?\n\n"
            "Aise likho: <code>31 40</code>\n\n"
            "(/cancel se kabhi bhi rok sakte ho)", parse_mode="HTML")
        return
    if data.startswith("oh:do:"):
        key = data.split(":", 2)[2]
        st  = OH_STATE_MAP.get(key)
        if not st: return
        owner_edit_state[uid] = {"state": st[0]}
        await query.message.reply_text(st[1] + "\n\n(/cancel se rok sakte ho)")
        return
    if data.startswith("oh:txt:"):
        cmdkey = data.split(":", 2)[2]
        if cmdkey not in PUBLIC_TEXT_CMDS:
            return
        cur = (cfg_get("public_texts", {}) or {}).get(cmdkey) or "(default text — abhi set nahi)"
        owner_edit_state[uid] = {"state": ST_SET_PUBLIC_TEXT, "cmd": cmdkey}
        await query.message.reply_text(
            f"✏️ <b>/{cmdkey}</b> ka naya text bhejo:\n\n"
            f"<b>Abhi:</b>\n<pre>{html.escape(cur[:700])}</pre>\n\n"
            f"(/cancel se rok sakte ho)", parse_mode="HTML")
        return
    if data.startswith("oh:tplv:"):
        # oh:tplv:<cp|ps>:<channel|details> — abhi ka template dikhao +
        # copy-paste ready command bhejo
        try:
            _, _, platform, which = data.split(":", 3)
            cfg_key, allowed_vars, note = _TEMPLATE_MAP[(platform, which)]
        except Exception:
            return
        label   = "CheesePad 🧀" if platform == "cp" else "PinkSale 🩷"
        where   = "bahar wala (channel post)" if which == "channel" else "bot ke andar wala (details page)"
        current = cfg_get(cfg_key, "")
        body = (f"<code>{html.escape(current[:2500])}</code>" if current
                else "<i>Custom set nahi — built-in default chal raha hai.</i>")
        await query.message.reply_text(
            f"🧩 <b>{label} — {which}</b> ({where})\n\n{body}\n\n"
            f"<b>Placeholders:</b>\n"
            f"{html.escape(', '.join('{' + v + '}' for v in allowed_vars))}\n\n"
            f"✏️ Edit: <code>/set{platform}template {which} &lt;naya text&gt;</code>\n"
            f"♻️ Reset: <code>/reset{platform}template {which}</code>\n"
            f"✨ Apni premium emoji: <code>[[emoji:ID]]</code> (ID: /getemoji)\n\n"
            f"{note}",
            parse_mode="HTML", disable_web_page_preview=True)
        return
    if data.startswith("oh:dskip:"):
        await _dropskip_callback(query, uid, data)
        return
    if data.startswith("oh:tip:"):
        cmd = data.split(":", 2)[2]
        await query.message.reply_text(
            f"👉 Ye command bhejo: <code>/{cmd}</code>", parse_mode="HTML")
        return

@owner_only
async def cmd_ownerhelp_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Part 1 — sent first
    await update.message.reply_text(
        f"{PE_CRYSTAL} <b>OWNER COMMANDS (1/2)</b>\n\n"

        "✏️ <b>Alert Templates</b>\n"
        "/settemplate — template + premium emojis set\n"
        "/edittemplate — sirf text template edit\n"
        "/showtemplate — templates preview\n"
        "/editmilestone 2 — 2X ke liye template\n"
        "/clearmilestone 2 — milestone template remove\n"
        "/listmilestones — milestone status\n"
        "/setmilestones 2,5,10,50 — list change\n\n"

        "🔍 <b>Preview</b>\n"
        "/previewtemplate 100 — 100X alert preview\n"
        "/previewmomentum — MOMENTUM post preview\n\n"

        "🖼️ <b>Milestone Media</b>\n"
        "/setmedia 2 | /clearmedia 2 | /listmedia\n"
        "✅ Media optional hai — jis X ka media set nahi, us ki alert phir bhi\n"
        "INSTANT text ke sath channel par chali jaati hai (leaderboard /\n"
        "champions / X-Ray me bhi turant show hoti hai).\n\n"

        "📡 <b>Channels</b>\n"
        "/mychannels | /addchannel @name | /removechannel @name\n\n"

        "🔗 <b>X (Twitter) Handles</b>\n"
        "/addx @channel xhandle — KOL ka X handle set karo\n"
        "/settwitter @channel xhandle — same kaam (alias)\n"
        "/removex @channel — X handle hata do\n"
        "/xlist — sab set handles\n"
        "ℹ️ X alert ke <b>𝕏 Twitter</b> button me yehi handle lagta hai.\n"
        "   Handle set na ho to button me x.com/Cheesy_Wolf chala jata hai.\n\n"

        "🔘 <b>X Alert Buttons (fixed)</b>\n"
        "👤 Caller — tracked post ka link\n"
        "𝕏 Twitter — KOL ka X (ya WizardScan)\n"
        "⚡ Trade — Maestro reflink\n"
        "📊 Dex — DexScreener chart\n"
        "📄 Details — filhal khali\n"
        "👨‍💻 Dev — @Wizard_Scan\n\n"

        "👤 <b>Admins</b>\n"
        "/addadmin USER_ID | /removeadmin USER_ID | /listadmins\n\n"

        "🤖 <b>Userbot</b>\n"
        "/userbotlogin | /userbotcheck | /userbotlogout\n\n"

        "👥 <b>Users & Stats</b>\n"
        "/myusers | /mystats | /broadcast\n\n"

        "🔗 <b>Promo Link (12h)</b>\n"
        "/setpromolink | /clearpromolink\n\n"

        "🔄 <b>Lists Force Refresh</b>\n"
        "/refreshleaderboard — Leaderboard (136)\n"
        "/refreshchampions — Champions (134)\n"

        "📌 Aage ke commands: /ownerhelp2\n"
        "🩷 PinkSale panel: /ownerhelpPS",

        parse_mode="HTML"
    )
    # Part 2 — sent immediately after
    await update.message.reply_text(
        f"{PE_CRYSTAL} <b>OWNER COMMANDS (2/2)</b>\n\n"

        "🏆 <b>Points (Champion KOL)</b>\n"
        "/givepoints @channel 50 — points do ya kato (-20)\n"
        "/checkpoints @channel | /checkpoints (top 20)\n"
        "/zerocolpoints @channel — sab points zero\n\n"

        "⛔ <b>Call Freeze</b>\n"
        "/freezecall CA | /freezecall @ch CA | /unfreezecall CA\n\n"

        "🆘 <b>Calls ke saare masle — sirf ek command</b>\n"
        "<b>/fix</b> — buttons wala Problem Fix Center:\n"
        "   1️⃣ Skipped call add · 2️⃣ Late track MC fix\n"
        "   3️⃣ X alert nahi aayi · 4️⃣ Call freeze\n"
        "   5️⃣ Call check · 6️⃣ Trending block\n"
        "ℹ️ Bot ek ek sawal poochta hai — koi lambi command nahi.\n"
        "(Purane /fixmc /latecall /adjustcall /recheckx /fixxalert /forcex hata diye gaye hain.)\n\n"

        "📨 <b>KOL Requests</b>\n"
        "/pendingkols — jin logon ne apne KOL ki request bheji hai\n\n"

        "📊 <b>Token Check</b>\n"
        "/xcheck @channel CA\n\n"

        "🔔 <b>KOL Owner DM</b>\n"
        "/setkolowner | /setkolownerid @ch ID\n"
        "/removekolowner @ch | /listkolowners\n\n"

        "🔧 <b>Other</b>\n"
        "/testalert | /ownerhelp | /premiumguide\n\n"

        "📌 /ownerhelp2 — aur commands",
        parse_mode="HTML"
    )


@owner_only
async def cmd_setkoldroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the 'Dropped a Call' post template for ONE KOL: /setkoldroppedtemplate CHANNEL
    Overrides the global dropped-call template for that channel only — every
    other KOL's first-call post stays on the global/default text."""
    if not context.args:
        await update.message.reply_text("Usage: /setkoldroppedtemplate CHANNEL\nExample: <code>/setkoldroppedtemplate somekol</code>", parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    uid = update.effective_user.id
    cur = load_config().get("channel_dropped_templates", {}).get(channel, "")
    owner_edit_state[uid] = {"state": ST_KOL_DROPPED_TMPL, "channel": channel}
    await update.message.reply_text(
        f"📋 <b>@{channel} — Dropped a Call Template Setup</b>\n\n"
        "Jab yeh KOL pehli baar call kare to jo post channel mein jaye us ka template set karo "
        "(is KOL ki apni language mein — Chinese/Vietnamese/Turkish/Russian jo bhi chahiye).\n\n"
        "<b>Available variables:</b>\n"
        "• <code>{channel}</code> → KOL channel name\n"
        "• <code>{symbol}</code> → token symbol\n"
        "• <code>{chain}</code> → blockchain (SOL/ETH/BNB/BASE)\n"
        "• <code>{entry}</code> → entry market cap\n"
        "• <code>{ca}</code> → contract address\n"
        "• <code>{chart_url}</code> → DexScreener chart link\n"
        "• <code>{kol_link}</code> → link to KOL post\n"
        "• <code>{bot_link}</code> → bot link\n\n"
        f"<b>Current @{channel} template:</b>\n<pre>{html.escape(cur) if cur else '(using global/default)'}</pre>\n\n"
        "💡 Premium emoji seedha apne Telegram Premium se type/pick kar sakte ho.\n\n"
        "New template bhejo ya /cancel karo:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_showkoldroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /showkoldroppedtemplate CHANNEL"); return
    channel = context.args[0].lstrip("@").strip().lower()
    cur = load_config().get("channel_dropped_templates", {}).get(channel, "")
    if not cur:
        await update.message.reply_text(f"@{channel} ke liye koi custom dropped-call template nahi hai — global/default use ho raha hai."); return
    await update.message.reply_text(
        f"📋 <b>@{channel} — Dropped-Call Template:</b>\n\n<pre>{html.escape(cur)}</pre>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearkoldroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /clearkoldroppedtemplate CHANNEL"); return
    channel = context.args[0].lstrip("@").strip().lower()
    c = load_config()
    templates = c.get("channel_dropped_templates", {})
    if channel not in templates:
        await update.message.reply_text(f"@{channel} ke liye koi custom dropped-call template set nahi tha."); return
    templates.pop(channel, None)
    c["channel_dropped_templates"] = templates
    save_config(c)
    await update.message.reply_text(f"✅ @{channel} ka dropped-call template hata diya. Ab global/default use hoga.")

@owner_only
async def cmd_setdroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the 'Dropped a Call' post template."""
    uid = update.effective_user.id
    cur = load_config().get("dropped_call_template", "")
    owner_edit_state[uid] = {"state": ST_DROPPED_TMPL}
    await update.message.reply_text(
        "📋 <b>Dropped a Call Template Setup</b>\n\n"
        "Jab koi KOL pehli baar call kare to jo post channel mein jaye us ka template set karo.\n\n"
        "<b>Available variables:</b>\n"
        "• <code>{channel}</code> → KOL channel name\n"
        "• <code>{symbol}</code> → token symbol\n"
        "• <code>{chain}</code> → blockchain (SOL/ETH/BNB/BASE)\n"
        "• <code>{entry}</code> → entry market cap\n"
        "• <code>{ca}</code> → contract address\n"
        "• <code>{chart_url}</code> → DexScreener chart link\n"
        "• <code>{kol_link}</code> → link to KOL post\n"
        "• <code>{bot_link}</code> → bot link\n\n"
        f"<b>Current template:</b>\n<pre>{html.escape(cur) if cur else '(default)'}</pre>\n\n"
        "💡 Premium emoji seedha apne Telegram Premium se type/pick kar sakte ho — "
        "ID nikalne ki zaroorat nahi, bot khud correctly save kar lega.\n\n"
        "New template bhejo ya /cancel karo:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_showdroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current 'Dropped a Call' template."""
    cur = load_config().get("dropped_call_template", "")
    tmpl = cur if cur else DEFAULT_DROPPED_TEMPLATE
    await update.message.reply_text(
        f"📋 <b>Current Dropped-Call Template:</b>\n\n<pre>{html.escape(tmpl)}</pre>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_cleardroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset 'Dropped a Call' template to default."""
    cfg_set("dropped_call_template", "")
    await update.message.reply_text("✅ Dropped-Call template reset — default use hoga.")

@owner_only
async def cmd_adddroppedvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a video to the Dropped-Call post rotation (up to 20)."""
    vids = load_config().get("dropped_videos", [])
    if len(vids) >= 20:
        await update.message.reply_text(
            "⚠️ Maximum 20 Dropped-Call videos already stored.\n"
            "Use /removedroppedvideo N to remove one first."
        ); return
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_DROPPED_VID}
    await update.message.reply_text(
        f"🎬 <b>Add Dropped-Call Video</b>\n\n"
        f"Stored: <b>{len(vids)}/20</b>\n\n"
        f"Video bhejo — har 'Dropped a Call' post mein rotate hogi (ya bina video k bhi chale gi).\n"
        f"(/cancel se cancel karo)",
        parse_mode="HTML"
    )

@owner_only
async def cmd_listdroppedvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all Dropped-Call rotating videos."""
    vids = load_config().get("dropped_videos", [])
    if not vids:
        await update.message.reply_text(
            "📭 Koi Dropped-Call video store nahi hai.\n\n"
            "Posts abhi text-only hain. /adddroppedvideo se video add karo."
        ); return
    lines = [f"🎬 <b>Dropped-Call Videos ({len(vids)}/20)</b>\n"]
    for i, v in enumerate(vids, 1):
        lines.append(f"<b>{i}.</b> {v.get('type','video')} — <code>{v.get('file_id','?')[:30]}...</code>")
    idx = load_config().get("dropped_video_index", 0)
    lines.append(f"\n⏩ Next video: #{(idx % len(vids)) + 1}")
    lines.append("Use /removedroppedvideo N to remove.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_removedroppedvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a Dropped-Call video by number."""
    if not context.args:
        await update.message.reply_text("Usage: /removedroppedvideo <number>"); return
    try: n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Valid number bhejo."); return
    vids = load_config().get("dropped_videos", [])
    if n < 1 or n > len(vids):
        await update.message.reply_text(f"❌ Invalid. {len(vids)} videos hain."); return
    vids.pop(n - 1)
    cfg_set("dropped_videos", vids)
    await update.message.reply_text(f"✅ Video #{n} hata di. {len(vids)} remaining.")

@owner_only
async def cmd_cleardroppedvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all Dropped-Call videos (posts will be text-only)."""
    cfg_set("dropped_videos", [])
    cfg_set("dropped_video_index", 0)
    await update.message.reply_text("✅ Sab Dropped-Call videos hata diye. Posts ab text-only hongi.")


# ─── PinkSale owner panel (/ownerhelpPS) ─────────────────────────────────────
@owner_only
async def cmd_ownerhelpps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med   = cfg_get("ps_media", []) or []
    cpmed = cfg_get("cp_media", []) or []
    ref   = cfg_get("pinksale_ref", "") or "—"
    watch = cfg_get("ps_watch", []) or []
    txt = (
        "🩷 <b>PINKSALE OWNER PANEL</b>\n\n"
        "<b>🔁 Skipped call — manually post</b>\n"
        "<code>/pscall &lt;pinksale link&gt; @caller</code>\n"
        "   → Agar bot ne kisi caller ki PinkSale call skip kar di ho, ye command us call ko\n"
        "   turant channel par post kar deti hai (duplicate guard bypass).\n"
        "<code>/psclear &lt;address|all&gt;</code> — duplicate memory clear (dobara post allow)\n"
        "<code>/pstest &lt;pinksale link&gt;</code> — preview yahin DM me (channel par nahi)\n"
        "<code>/psdebug &lt;pinksale link&gt;</code> — kaunsi detail mili/nahi mili (N/A debug)\n\n"
        "<b>🎬 Media (max 10, rotate)</b>\n"
        "<code>/addpsmedia</code> · <code>/listpsmedia</code> · "
        "<code>/removepsmedia N</code> · <code>/clearpsmedia</code>\n\n"
        "<b>🔗 Referral link</b>\n"
        "<code>/setpsref &lt;ref code ya apna ref link&gt;</code>\n"
        "<code>/showpsref</code> · <code>/clearpsref</code>\n\n"
        "<b>📊 Projects &amp; live watch</b>\n"
        "<code>/pslist</code> — last posted PinkSale projects\n"
        "\n🧀 <b>CHEESEPAD</b>\n"
        "<b>🔁 Skipped call — manually post</b>\n"
        "<code>/cpcall &lt;cheesepad link&gt; @caller</code>\n"
        "   → Agar bot ne kisi caller ki CheesePad call skip kar di ho, ye command us call ko\n"
        "   turant channel par post kar deti hai (duplicate guard bypass).\n"
        "<code>/cpclear &lt;address|all&gt;</code> — duplicate memory clear (dobara post allow)\n"
        "<code>/cptest &lt;cheesepad link&gt; @caller</code> — preview yahin DM me\n"
        "<b>🎬 CheesePad videos (max 10, rotate)</b>\n"
        "<code>/addcpmedia</code> · <code>/listcpmedia</code> · "
        "<code>/removecpmedia N</code> · <code>/clearcpmedia</code>\n"
        "<b>📄 Bot ke andar token details ka media</b>\n"
        "<code>/addcpdmedia</code> · <code>/listcpdmedia</code> · "
        "<code>/removecpdmedia N</code> · <code>/clearcpdmedia</code>\n"
        "<code>/addpsdmedia</code> · <code>/listpsdmedia</code> · "
        "<code>/removepsdmedia N</code> · <code>/clearpsdmedia</code>\n"
        "<code>/setaffiliate 0xWallet</code> — affiliate wallet for all launchpad links\n\n"
        "<code>/pswatch</code> — jo presale end hone ke baad live tracking ka intezaar kar rahe hain\n"
        "<code>/psdrop N</code> — watch list se entry hatao\n\n"
        f"📦 PinkSale media: <b>{len(med)}/{PS_MAX_MEDIA}</b>\n"
        f"🧀 CheesePad videos: <b>{len(cpmed)}/{CP_MAX_MEDIA}</b>\n"
        f"💰 Affiliate: <code>{html.escape(str(cfg_get('affiliate_wallet', AFFILIATE_WALLET)))}</code>\n"
        f"🔗 Ref: <code>{html.escape(str(ref))}</code>\n"
        f"👀 Live-watch queue: <b>{len(watch)}</b>\n\n"
        "\n🧩 <b>TEMPLATE EDITOR (channel post + bot ke andar wala)</b>\n"
        "<code>/cptemplatevars</code> — CheesePad ki poori template guide\n"
        "<code>/pstemplatevars</code> — PinkSale ki poori template guide\n"
        "<code>/setcptemplate channel|details &lt;text&gt;</code>\n"
        "<code>/setpstemplate channel|details &lt;text&gt;</code>\n"
        "<code>/cptemplate channel|details</code> · <code>/pstemplate channel|details</code> — abhi ka template dekho\n"
        "<code>/resetcptemplate channel|details</code> · <code>/resetpstemplate channel|details</code>\n"
        "✨ Apni premium emoji kisi bhi jagah: <code>[[emoji:ID]]</code> "
        "(ID lene ke liye /getemoji)\n\n"
        "📚 Baaki pages: /ownerhelp · /ownerhelp2\n"
        "🆘 Koi masla ho to: /fix"
    )
    await update.message.reply_text(txt, parse_mode="HTML", disable_web_page_preview=True)

def _ps_info_from_args(args):
    """Parse '/pscall <link> @caller' → (info, channel)."""
    link = ""
    channel = ""
    for a in args:
        if "pinksale" in a.lower():
            link = a
        elif a.startswith("@") or (a and not a.lower().startswith("http")):
            channel = a.lstrip("@").lower()
    info = detect_launchpad(link) if link else None
    return info, channel

@owner_only
async def cmd_pscall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually post a PinkSale call the bot skipped."""
    info, channel = _ps_info_from_args(context.args or [])
    if not info or info.get("platform") != "PinkSale":
        await update.message.reply_text(
            "Usage: <code>/pscall https://www.pinksale.finance/launchpad/0x... @caller</code>",
            parse_mode="HTML"); return
    if not channel:
        await update.message.reply_text("⚠️ Caller channel bhi do: <code>/pscall &lt;link&gt; @caller</code>",
                                        parse_mode="HTML"); return
    await update.message.reply_text("⏳ PinkSale details fetch ho rahi hain…")
    ok = await send_pinksale_alert(context.bot, channel, 0, info, "", force=True)
    await update.message.reply_text("✅ Post ho gayi." if ok else "❌ Post fail — logs check karo.")

@owner_only
async def cmd_pstest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info, channel = _ps_info_from_args(context.args or [])
    if not info:
        await update.message.reply_text("Usage: <code>/pstest &lt;pinksale link&gt; @caller</code>",
                                        parse_mode="HTML"); return
    await send_pinksale_alert(context.bot, channel or "WizardScan", 0, info, "",
                              force=True, preview_to=update.effective_user.id)

@owner_only
async def cmd_psdebug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dikhata hai ke kis field ki value kahan se mili — N/A debug ke liye."""
    info, _ = _ps_info_from_args(context.args or [])
    if not info:
        await update.message.reply_text("Usage: <code>/psdebug &lt;pinksale link&gt;</code>",
                                        parse_mode="HTML"); return
    onchain = await asyncio.to_thread(_ps_onchain_details, info)
    full    = await asyncio.to_thread(fetch_launchpad_details_sync, info, "")
    def dump(d):
        return "\n".join(f"  {k} = {v}" for k, v in sorted((d or {}).items())) or "  (kuch nahi mila)"
    await update.message.reply_text(
        f"<b>🔎 PS DEBUG</b>\n<code>{html.escape(str(info))}</code>\n\n"
        f"<b>On-chain:</b>\n<code>{html.escape(dump(onchain))}</code>\n\n"
        f"<b>Final merged:</b>\n<code>{html.escape(dump(full))}</code>",
        parse_mode="HTML")



@owner_only
async def cmd_psclear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: <code>/psclear all</code> ya "
                                        "<code>/psclear 0xADDRESS</code>", parse_mode="HTML"); return
    arg = context.args[0].strip().lower()
    if arg == "all":
        _PINKSALE_SEEN.clear()
        await update.message.reply_text("✅ PinkSale duplicate memory clear. Sab projects dobara post ho sakte hain.")
        return
    _PINKSALE_SEEN.discard(f"PS|{arg}")
    await update.message.reply_text(f"✅ <code>{html.escape(arg)}</code> unblocked.", parse_mode="HTML")

@owner_only
async def cmd_addpsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("ps_media", []) or []
    if len(med) >= PS_MAX_MEDIA:
        await update.message.reply_text(
            f"⚠️ Already {PS_MAX_MEDIA}/{PS_MAX_MEDIA} stored. /removepsmedia N se hatao."); return
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_PS_MEDIA}
    await update.message.reply_text(
        f"🎬 <b>Add PinkSale Media</b>\n\nStored: <b>{len(med)}/{PS_MAX_MEDIA}</b>\n\n"
        f"Ab video / GIF / photo bhejo — har PinkSale post par rotate hogi.\n(/cancel se cancel)",
        parse_mode="HTML")

@owner_only
async def cmd_listpsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("ps_media", []) or []
    if not med:
        await update.message.reply_text("📭 Koi PinkSale media set nahi. /addpsmedia se add karo."); return
    lines = [f"🎬 <b>PinkSale Media ({len(med)}/{PS_MAX_MEDIA})</b>\n"]
    for i, m in enumerate(med, 1):
        lines.append(f"<b>{i}.</b> {m.get('type','video')} — <code>{m.get('file_id','?')[:28]}…</code>")
    idx = int(cfg_get("ps_media_index", 0) or 0)
    lines.append(f"\n⏩ Next: #{(idx % len(med)) + 1}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_removepsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("ps_media", []) or []
    try:
        n = int(context.args[0])
    except Exception:
        await update.message.reply_text("Usage: /removepsmedia <number>"); return
    if n < 1 or n > len(med):
        await update.message.reply_text(f"❌ Invalid. {len(med)} media hain."); return
    med.pop(n - 1)
    cfg_set("ps_media", med)
    await update.message.reply_text(f"✅ Media #{n} hata di. {len(med)} remaining.")

@owner_only
async def cmd_clearpsmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg_set("ps_media", []); cfg_set("ps_media_index", 0)
    await update.message.reply_text("✅ Sab PinkSale media hata di. Posts ab text-only hongi.")

@owner_only
async def cmd_addcpmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("cp_media", []) or []
    if len(med) >= CP_MAX_MEDIA:
        await update.message.reply_text(
            f"⚠️ Already {CP_MAX_MEDIA}/{CP_MAX_MEDIA} stored. /removecpmedia N se hatao."); return
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_CP_MEDIA}
    await update.message.reply_text(
        f"🧀 <b>Add CheesePad Video</b>\n\nStored: <b>{len(med)}/{CP_MAX_MEDIA}</b>\n\n"
        f"Ab video / GIF / photo bhejo — har CheesePad post par rotate hogi.\n(/cancel se cancel)",
        parse_mode="HTML")

@owner_only
async def cmd_listcpmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("cp_media", []) or []
    if not med:
        await update.message.reply_text("📭 Koi CheesePad video set nahi. /addcpmedia se add karo."); return
    lines = [f"🧀 <b>CheesePad Videos ({len(med)}/{CP_MAX_MEDIA})</b>\n"]
    for i, m in enumerate(med, 1):
        lines.append(f"<b>{i}.</b> {m.get('type','video')} — <code>{m.get('file_id','?')[:28]}…</code>")
    idx = int(cfg_get("cp_media_index", 0) or 0)
    lines.append(f"\n⏩ Next: #{(idx % len(med)) + 1}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_removecpmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    med = cfg_get("cp_media", []) or []
    try:
        n = int(context.args[0])
    except Exception:
        await update.message.reply_text("Usage: /removecpmedia <number>"); return
    if n < 1 or n > len(med):
        await update.message.reply_text(f"❌ Invalid. {len(med)} videos hain."); return
    med.pop(n - 1)
    cfg_set("cp_media", med)
    await update.message.reply_text(f"✅ Video #{n} hata di. {len(med)} remaining.")

@owner_only
async def cmd_clearcpmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg_set("cp_media", []); cfg_set("cp_media_index", 0)
    await update.message.reply_text("✅ Sab CheesePad videos hata di. Posts ab text-only hongi.")

# ── Details-message media (bot ke andar aane wali token details) ──────────
def _dm_labels(kind):
    return ("🧀 CheesePad", CPD_MAX_MEDIA, ST_ADD_CPD_MEDIA) if kind == "cp" else \
           ("🩷 PinkSale", PSD_MAX_MEDIA, ST_ADD_PSD_MEDIA)


async def _dm_add(update, kind):
    label, mx, state = _dm_labels(kind)
    med = cfg_get(f"{kind}d_media", []) or []
    if len(med) >= mx:
        await update.message.reply_text(
            f"⚠️ Already {mx}/{mx} stored. /remove{kind}dmedia N se hatao."); return
    owner_edit_state[update.effective_user.id] = {"state": state}
    await update.message.reply_text(
        f"{label} <b>Details Media</b>\n\nStored: <b>{len(med)}/{mx}</b>\n\n"
        f"Ab video / GIF / photo bhejo — bot ke andar aane wali {label} token "
        f"details ke sath rotate hogi.\n(/cancel se cancel)", parse_mode="HTML")


async def _dm_list(update, kind):
    label, mx, _ = _dm_labels(kind)
    med = cfg_get(f"{kind}d_media", []) or []
    if not med:
        await update.message.reply_text(
            f"📭 Koi {label} details media set nahi. /add{kind}dmedia se add karo."); return
    lines = [f"{label} <b>Details Media ({len(med)}/{mx})</b>\n"]
    for i, m in enumerate(med, 1):
        lines.append(f"<b>{i}.</b> {m.get('type','video')} — <code>{m.get('file_id','?')[:28]}…</code>")
    idx = int(cfg_get(f"{kind}d_media_index", 0) or 0)
    lines.append(f"\n⏩ Next: #{(idx % len(med)) + 1}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _dm_remove(update, context, kind):
    med = cfg_get(f"{kind}d_media", []) or []
    try:
        n = int(context.args[0])
    except Exception:
        await update.message.reply_text(f"Usage: /remove{kind}dmedia <number>"); return
    if n < 1 or n > len(med):
        await update.message.reply_text(f"❌ Invalid. {len(med)} media hain."); return
    med.pop(n - 1)
    cfg_set(f"{kind}d_media", med)
    await update.message.reply_text(f"✅ Media #{n} hata di. {len(med)} remaining.")


async def _dm_clear(update, kind):
    cfg_set(f"{kind}d_media", []); cfg_set(f"{kind}d_media_index", 0)
    label, _, _ = _dm_labels(kind)
    await update.message.reply_text(f"✅ Sab {label} details media hata di. Details ab text-only.")


@owner_only
async def cmd_addcpdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_add(update, "cp")

@owner_only
async def cmd_listcpdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_list(update, "cp")

@owner_only
async def cmd_removecpdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_remove(update, context, "cp")

@owner_only
async def cmd_clearcpdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_clear(update, "cp")

@owner_only
async def cmd_addpsdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_add(update, "ps")

@owner_only
async def cmd_listpsdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_list(update, "ps")

@owner_only
async def cmd_removepsdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_remove(update, context, "ps")

@owner_only
async def cmd_clearpsdmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dm_clear(update, "ps")


@owner_only
async def cmd_cpclear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear CheesePad duplicate memory so a project can be posted again."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/cpclear all</code> ya "
                                        "<code>/cpclear 0xADDRESS</code>", parse_mode="HTML"); return
    arg = context.args[0].strip().lower()
    if arg == "all":
        _CHEESEPAD_SEEN.clear()
        await update.message.reply_text("✅ CheesePad duplicate memory clear. Sab projects dobara post ho sakte hain.")
        return
    _CHEESEPAD_SEEN.discard(f"CP|{arg}")
    await update.message.reply_text(f"✅ <code>{html.escape(arg)}</code> unblocked.", parse_mode="HTML")

@owner_only
async def cmd_cptest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview a CheesePad alert here in DM (channel par post nahi hoti)."""
    arg = " ".join(context.args) if context.args else ""
    info = detect_launchpad(arg)
    channel = next((a.lstrip("@") for a in (context.args or []) if a.startswith("@")), "")
    if not info:
        await update.message.reply_text(
            "Usage: <code>/cptest &lt;cheesepad link&gt; @caller</code>", parse_mode="HTML"); return
    info["platform"] = "CheesePad"
    await send_cheesepad_alert(context.bot, channel or "WizardScan", 0, info, "",
                               force=True, preview_to=update.effective_chat.id)

@owner_only
async def cmd_cpcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually post a CheesePad call the bot skipped."""
    arg = " ".join(context.args) if context.args else ""
    info = detect_launchpad(arg)
    channel = next((a.lstrip("@") for a in (context.args or []) if a.startswith("@")), "")
    if not info:
        await update.message.reply_text(
            "Usage: <code>/cpcall &lt;cheesepad link&gt; @caller</code>", parse_mode="HTML"); return
    info["platform"] = "CheesePad"
    await update.message.reply_text("⏳ CheesePad details fetch ho rahi hain…")
    ok = await send_cheesepad_alert(context.bot, channel or "WizardScan", 0, info, "", force=True)
    await update.message.reply_text("✅ Posted." if ok else "❌ Post nahi ho saka, logs check karo.")

@owner_only
async def cmd_setaffiliate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the affiliate/referral wallet used on every launchpad link."""
    if not context.args:
        cur = cfg_get("affiliate_wallet", "") or AFFILIATE_WALLET
        await update.message.reply_text(
            f"Current affiliate: <code>{html.escape(cur)}</code>\n\n"
            f"Usage: <code>/setaffiliate 0xYourWallet</code>", parse_mode="HTML"); return
    cfg_set("affiliate_wallet", context.args[0].strip())
    await update.message.reply_text("✅ Affiliate wallet updated (PinkSale + CheesePad links).")

@owner_only
async def cmd_setpsref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/setpsref 0xYourRefAddress</code>\n"
            "Ya apna poora ref link paste karo — bot us me se ref value nikaal lega.",
            parse_mode="HTML"); return
    cfg_set("pinksale_ref", " ".join(context.args).strip())
    await update.message.reply_text("✅ PinkSale referral set. Ab har post ka Pinksale link tumhare ref ke saath jayega.")

@owner_only
async def cmd_showpsref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref = cfg_get("pinksale_ref", "") or "—"
    await update.message.reply_text(f"🔗 Current PinkSale ref: <code>{html.escape(str(ref))}</code>",
                                    parse_mode="HTML")

@owner_only
async def cmd_clearpsref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg_set("pinksale_ref", "")
    await update.message.reply_text("✅ PinkSale referral hata diya.")

@owner_only
async def cmd_pslist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = load_ps_projects()
    if not projects:
        await update.message.reply_text("📭 Abhi tak koi PinkSale project post nahi hua."); return
    items = sorted(projects.items(), key=lambda kv: kv[1].get("saved_at", ""), reverse=True)[:15]
    lines = ["🩷 <b>Recent PinkSale Projects</b>\n"]
    for pid, rec in items:
        d = rec.get("details", {}) or {}
        lines.append(f"• <b>{html.escape(str(d.get('name') or d.get('symbol') or 'Project'))}</b> "
                     f"— @{html.escape(rec.get('channel',''))} "
                     f"(<a href=\"{BOT_LINK}?start=ps_{pid}\">details</a>)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML",
                                    disable_web_page_preview=True)

@owner_only
async def cmd_pswatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    watch = cfg_get("ps_watch", []) or []
    if not watch:
        await update.message.reply_text("📭 Live-watch queue khali hai."); return
    lines = [f"👀 <b>PinkSale Live-Watch ({len(watch)})</b>\n"]
    for i, w in enumerate(watch, 1):
        try:
            ends = datetime.utcfromtimestamp(float(w.get("end_ts") or 0)).strftime("%d %b %H:%M UTC")
        except Exception:
            ends = "?"
        lines.append(f"<b>{i}.</b> @{html.escape(w.get('channel',''))} — {w.get('chain','')} — ends {ends}")
    lines.append("\nPresale end hone ke baad token live hote hi normal X tracking shuru ho jati hai.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_psdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    watch = cfg_get("ps_watch", []) or []
    try:
        n = int(context.args[0])
    except Exception:
        await update.message.reply_text("Usage: /psdrop <number>  (/pswatch se number dekho)"); return
    if n < 1 or n > len(watch):
        await update.message.reply_text("❌ Invalid number."); return
    watch.pop(n - 1)
    cfg_set("ps_watch", watch)
    await update.message.reply_text(f"✅ Watch entry #{n} hata di.")

@owner_only
async def cmd_debugscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full pipeline diagnostic — shows exactly where tracking breaks.
    Usage: /debugscan  (checks the first tracked channel)
           /debugscan <channel>  (checks that specific channel, e.g. /debugscan maybac_whales)"""
    if update.effective_user.id not in OWNER_IDS:
        return
    uid = update.effective_user.id
    bot = context.bot

    await bot.send_message(uid, "🔍 <b>DEBUG SCAN starting...</b>", parse_mode="HTML")

    # 1. Channels
    channels = load_channels()
    await bot.send_message(uid,
        f"<b>1. Channels tracked:</b> {len(channels)}\n" +
        ("\n".join(f"  • @{c}" for c in channels[:15]) or "  (none)"),
        parse_mode="HTML")

    if not channels:
        await bot.send_message(uid, "❌ No channels — /addchannel karo pehle."); return

    # 2. Fetch posts — target channel from /debugscan <channel>, else first
    # tracked channel. Passing the channel explicitly lets the owner debug
    # ANY specific channel (e.g. one whose posts aren't tracking) instead of
    # always being stuck on whichever channel happens to be first in the list.
    if context.args:
        ch_arg = context.args[0].lstrip("@").strip().lower()
        matches = [c for c in channels if c.lower() == ch_arg]
        if not matches:
            await bot.send_message(uid,
                f"❌ @{ch_arg} tracked channels mein nahi hai.\n"
                f"/addchannel {ch_arg} se pehle add karo, ya sahi naam do."); return
        ch = matches[0]
    else:
        ch = channels[0]
    await bot.send_message(uid, f"<b>2. Fetching posts from @{ch}...</b>", parse_mode="HTML")
    try:
        posts = await fetch_channel_posts(ch)
    except Exception as e:
        await bot.send_message(uid, f"❌ fetch_channel_posts crashed: {e}"); return

    await bot.send_message(uid,
        f"<b>Posts fetched from @{ch}:</b> {len(posts)}\n"
        f"Seen IDs stored: {len(seen_message_ids.get(ch, set()))}\n"
        f"Sample IDs: {[p['id'] for p in posts[-3:]]}",
        parse_mode="HTML")

    if not posts:
        await bot.send_message(uid,
            "❌ No posts returned — t.me/s scraping fail.\n"
            "Channel private hai ya Telegram ne block kiya?"); return

    # 3. Check last 5 posts for is_call_message / extract_ca
    await bot.send_message(uid, "<b>3. Last 5 posts analysis:</b>", parse_mode="HTML")
    for post in posts[-5:]:
        pid  = post["id"]
        txt  = post["text"][:200].replace("<","&lt;").replace(">","&gt;")
        seen = pid in seen_message_ids.get(ch, set())
        icm  = is_call_message(post["text"])
        ca   = extract_ca(post["text"])
        ck   = f"{ch}_{ca[1]}" if ca else None
        tracked = ck in tracked_calls if ck else False
        await bot.send_message(uid,
            f"<b>Post {pid}</b> {'✅seen' if seen else '🆕new'}\n"
            f"is_call_message: {'✅' if icm else '❌'}\n"
            f"extract_ca: {ca or '❌ none'}\n"
            f"already tracked: {'yes' if tracked else 'no'}\n"
            f"Text: <code>{txt}</code>",
            parse_mode="HTML")

    # 4. Try live dexscreener on first untracked CA we find
    await bot.send_message(uid, "<b>4. Dexscreener test on first new CA...</b>", parse_mode="HTML")
    tested = False
    for post in reversed(posts):
        if not is_call_message(post["text"]): continue
        ca_res = extract_ca(post["text"])
        if not ca_res: continue
        _, ca_addr = ca_res
        ck = f"{ch}_{ca_addr}"
        if ck in tracked_calls: continue
        try:
            dex = await asyncio.wait_for(fetch_dexscreener(ca_addr), timeout=15)
        except asyncio.TimeoutError:
            await bot.send_message(uid, f"❌ Dexscreener TIMEOUT for {ca_addr[:12]}..."); tested=True; break
        except Exception as e:
            await bot.send_message(uid, f"❌ Dexscreener ERROR: {e}"); tested=True; break
        if dex:
            await bot.send_message(uid,
                f"✅ Dexscreener OK: {dex.get('symbol','?')} {dex.get('chain','?')} mcap={dex.get('mcap_fmt','?')}\n"
                f"CA: <code>{ca_addr}</code>",
                parse_mode="HTML")
        else:
            await bot.send_message(uid, f"❌ Dexscreener returned None for <code>{ca_addr}</code>", parse_mode="HTML")
        tested = True; break
    if not tested:
        await bot.send_message(uid, "⚠️ No untracked CAs found in last posts to test dex.")

    # 5. tracked_calls & milestone check
    await bot.send_message(uid,
        f"<b>5. tracked_calls:</b> {len(tracked_calls)} total\n"
        f"<b>sent_milestones:</b> {sum(len(v) for v in sent_milestones.values())} total\n"
        f"<b>userbot connected:</b> {'✅ yes' if userbot_client else '❌ no'}",
        parse_mode="HTML")

    # 6. Config: milestone_media
    cfg = load_config()
    mm = cfg.get("milestone_media", {})
    await bot.send_message(uid,
        f"<b>6. milestone_media keys:</b> {list(mm.keys()) or '(empty — NO ALERTS WILL FIRE)'}\n"
        f"global media: {'✅ set' if mm.get('global') else '❌ not set'}",
        parse_mode="HTML")

    await bot.send_message(uid, "✅ <b>Debug scan complete.</b>", parse_mode="HTML")


@owner_only
async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live health check: kyun calls/x alerts nahi aa rahe."""
    if not is_admin_or_owner(update.effective_user.id):
        return
    try:
        chans = load_channels()
        total = len(tracked_calls)
        pending_dex   = sum(1 for c in tracked_calls.values() if c.get("dex_pending"))
        pending_alert = sum(1 for c in tracked_calls.values() if c.get("alert_pending"))
        with_entry    = sum(1 for c in tracked_calls.values() if (c.get("entry_price") or 0) > 0 or (c.get("entry_mc") or 0) > 0)
        ub = bool(userbot_client and userbot_client.is_connected())
        now = _t.time()
        flooded = [k for k, v in _FLOOD_UNTIL.items() if v > now]

        # DIAG: ground-truth check of what THIS running bot process actually
        # is, and its real live membership status in TARGET_CHANNEL — separate
        # from what the owner sees in the Telegram app's admin list, since a
        # stale/wrong BOT_TOKEN would show as admin in-app but fail here.
        try:
            _me = await context.bot.get_me()
            bot_identity = f"@{_me.username} (id={_me.id})"
        except Exception as e:
            bot_identity = f"❌ get_me() failed: {e}"
        try:
            _cm = await context.bot.get_chat_member(TARGET_CHANNEL, _me.id)
            target_status = f"✅ {_cm.status}"
        except Exception as e:
            target_status = f"❌ {e}"

        # get_chat_member can false-negative on broadcast channels ("member
        # list is inaccessible") even when the bot IS a working admin — so
        # cross-check against the full admin list, which is what actually
        # matters for send/edit/forward permissions.
        try:
            _admins = await context.bot.get_chat_administrators(TARGET_CHANNEL)
            _admin_ids = [a.user.id for a in _admins]
            if _me.id in _admin_ids:
                admin_list_status = f"✅ found in admin list ({len(_admins)} admins total)"
            else:
                admin_list_status = f"❌ NOT in admin list ({len(_admins)} admins total, bot missing)"
        except Exception as e:
            admin_list_status = f"❌ get_chat_administrators failed: {e}"

        # Premium emoji entities only render as premium when the SENDING
        # account (the userbot) currently has an active Telegram Premium
        # subscription — otherwise Telegram silently downgrades them to
        # plain emoji with NO error, which is invisible in normal logs.
        try:
            if userbot_client and userbot_client.is_connected():
                _ub_me = await userbot_client.get_me()
                ub_premium = "✅ Premium active" if getattr(_ub_me, "premium", False) else "❌ Premium NOT active (emojis will render plain)"
            else:
                ub_premium = "❌ userbot not connected"
        except Exception as e:
            ub_premium = f"❌ check failed: {e}"

        # Live DexScreener probe (SOL)
        try:
            probe = await asyncio.wait_for(asyncio.to_thread(
                _fetch_dex_sync, "So11111111111111111111111111111111111111112", 1, 0), timeout=15)
            dex_ok = "✅ OK" if probe else "❌ no data"
        except Exception as pe:
            dex_ok = f"❌ {pe}"

        recent = sorted(tracked_calls.values(), key=lambda c: c.get("tracked_since", ""), reverse=True)[:5]
        lines = [
            "🩺 <b>DIAGNOSTICS</b>",
            f"Running as: <b>{bot_identity}</b>",
            f"Status in {TARGET_CHANNEL} (get_chat_member): <b>{target_status}</b>",
            f"Status in {TARGET_CHANNEL} (admin list): <b>{admin_list_status}</b>",
            f"Userbot Telegram Premium: <b>{ub_premium}</b>",
            f"BOT_READY: {'✅' if BOT_READY else '❌'} ({STARTUP_STAGE})",
            f"Userbot connected: {'✅' if ub else '❌'}",
            f"Channels: <b>{len(chans)}</b>",
            f"Tracked calls: <b>{total}</b> (entry data: {with_entry})",
            f"Waiting for dex: {pending_dex} · Alert pending: {pending_alert}",
            f"FloodWait active: {', '.join(flooded) if flooded else 'none'}",
            f"DexScreener probe: {dex_ok}",
            "",
            "<b>Last 5 calls:</b>" if recent else "<i>No calls tracked yet.</i>",
        ]
        for c in recent:
            lines.append(
                f"· @{c.get('channel')} {c.get('symbol') or '—'} "
                f"entry={c.get('entry_fmt')} x={c.get('last_ratio', 0)} "
                f"{'⏳alert-pending' if c.get('alert_pending') else ''}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"diag error: {e}")


async def cmd_testdropped(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test the Dropped-Call post format."""
    await update.message.reply_text("⏳ Dropped-Call test post bhej raha hoon...")
    try:
        await send_dropped_alert(
            context.bot, "TestKOL", 0,
            "So11111111111111111111111111111111111111111",
            "SOL", "$500K", "TESTTOKEN"
        )
        await update.message.reply_text("✅ Test Dropped-Call post channel mein bhej diya!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


@owner_only
async def cmd_prunerugged(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually run the rugged-call cleanup right now (instead of waiting for
    the scheduled job). Removes tracked calls that are rugged=True AND older
    than RUGGED_CALL_MAX_AGE_HOURS (default 1h, rugged_at/tracked_since)."""
    before = len(tracked_calls)
    removed = _prune_rugged_calls()
    after = len(tracked_calls)
    await update.message.reply_text(
        f"🧹 <b>Cleanup done.</b>\n"
        f"Removed: <b>{removed}</b> rugged call(s) older than {RUGGED_CALL_MAX_AGE_HOURS:.1f}h\n"
        f"tracked_calls: {before} → {after}\n"
        f"(History record stays permanent — only active tracking load is freed.)",
        parse_mode="HTML")


@owner_only
async def cmd_backupnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger Telegram backup — sab JSON data Telegram pe bhejo."""
    await update.message.reply_text("⏳ Backup ho raha hai...")
    await backup_data_to_telegram(context.bot)
    await update.message.reply_text(
        "✅ <b>Backup Telegram pe bhej diya!</b>\n\n"
        "<i>Har 5 min mein auto-backup bhi hoti hai. "
        "Bot restart ke baad userbot se data wapas restore ho jaata hai.</i>",
        parse_mode="HTML"
    )

async def _silent_catchup_milestones():
    """After restore, silently mark all already-passed milestones as sent WITHOUT posting alerts.

    Why needed: backup is max 30 min old. Tokens that crossed X milestones in the last
    30 min will NOT be in the restored sent_milestones. On next monitoring_job tick,
    they would fire again — spamming the channel with duplicate/old alerts.

    This function fetches live prices, computes which milestones are already passed,
    and quietly adds them to sent_milestones. No alerts are sent.
    """
    try:
        items = list(tracked_calls.items())
        if not items:
            return
        pre_marked = 0
        milestones_list = get_milestones()
        for call_key, call in items:
            try:
                if call.get("frozen"):
                    continue
                # Use last_ratio if available (from a recent monitoring_job run in memory)
                # Otherwise fetch live from DexScreener
                ratio = call.get("last_ratio", 0)
                if ratio <= 0:
                    # Try DexScreener; skip if unavailable (don't block restore for one token)
                    try:
                        _invalidate_dex_cache(call["ca"])
                        dex = await asyncio.wait_for(fetch_dexscreener(call["ca"]), timeout=10)
                    except Exception:
                        dex = None
                    if dex:
                        entry_price = call.get("entry_price", 0)
                        entry_mc    = call.get("entry_mc", 0)
                        cur_price   = dex.get("price", 0)
                        cur_mc      = dex.get("mcap", 0)
                        if entry_price > 0 and cur_price > 0:
                            ratio = cur_price / entry_price
                        elif entry_mc > 0 and cur_mc > 0:
                            ratio = cur_mc / entry_mc
                if ratio <= 0:
                    continue
                # Mark every milestone that has ALREADY been passed, silently
                for ms in milestones_list:
                    if ms > MAX_MILESTONE:
                        continue
                    if ratio >= ms and ms not in sent_milestones[call_key]:
                        sent_milestones[call_key].add(ms)
                        pre_marked += 1
            except Exception as e:
                logger.warning(f"_silent_catchup {call_key}: {e}")
        if pre_marked:
            _save_milestones()
            logger.info(f"✅ Silent catchup: {pre_marked} already-passed milestones marked (no alerts sent)")
    except Exception as e:
        logger.error(f"_silent_catchup_milestones crash: {e}")


@owner_only
async def cmd_restorenow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram backup se abhi restore karo — userbot connected hona chahiye."""
    if not userbot_client:
        await update.message.reply_text(
            "❌ <b>Userbot connected nahi hai!</b>\n\n"
            "Pehle userbot connect karo:\n"
            "👉 <code>/userbotlogin</code> bhejo → OTP enter karo\n\n"
            "Phir dobara <code>/restorenow</code> chalao.",
            parse_mode="HTML"
        )
        return
    await update.message.reply_text("⏳ Telegram se backup dhund raha hoon...")
    ok = await restore_data_from_telegram(userbot_client)
    if ok:
        # Run template migrations after every restore so old backups don't
        # bring back X/Twitter lines or wrong template order.
        _migrate_remove_x_from_templates()
        # KEY FIX: silently pre-mark all already-passed milestones so monitoring_job
        # does NOT re-post duplicate alerts for tokens that already crossed X levels.
        status_msg = await update.message.reply_text(
            "⏳ <b>Milestone history sync ho rahi hai...</b>\n"
            "<i>(Yeh step purane duplicate alerts rok ta hai — bas 10-15 seconds...)</i>",
            parse_mode="HTML"
        )
        await _silent_catchup_milestones()
        await status_msg.delete()
        await update.message.reply_text(
            "✅ <b>Restore complete!</b>\n\n"
            "Sab videos, photos, channels, members — sab wapas aa gaye.\n"
            "🔒 <b>Purane milestone alerts block ho gaye</b> — koi duplicate posts nahi jayenge.\n"
            "<i>Ab /listdroppedvideos etc. se check kar sakte ho.</i>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "⚠️ <b>Koi backup nahi mila.</b>\n\n"
            "Ya to pehle kabhi <code>/backupnow</code> nahi chali, "
            "ya BACKUP_CHAT_ID galat set hai.\n\n"
            f"<i>Abhi BACKUP_CHAT_ID = <code>{BACKUP_CHAT_ID}</code> pe dhund raha tha.</i>",
            parse_mode="HTML"
        )


@owner_only
async def cmd_ownerhelp2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{PE_CRYSTAL} <b>OWNER COMMANDS — PAGE 2</b>\n\n"

        "💣 <b>KOL Points Reset (Owner Control)</b>\n"
        "/resetallpoints — ⚠️ SAB channels ke points ek saath zero karo\n"
        "  (confirm step bhi hai — galti se nahi hoga)\n"
        "/zerocolpoints @channel — sirf EK channel ke points zero karo\n"
        "/checkpoints — sab channels ke current points dekho\n"
        "/checkpoints @channel — kisi ek channel ke points dekho\n\n"
        "ℹ️ <i>Champions/Leaderboard reset karne se points affect NAHI honge.\n"
        "Points sirf inhi commands se reset honge.</i>\n\n"

        "📣 <b>Dropped-Call Posts (jab KOL pehli baar call kare)</b>\n"
        "/setdroppedtemplate — custom template set karo\n"
        "/showdroppedtemplate — current template dekho\n"
        "/cleardroppedtemplate — default par wapas\n"
        "/adddroppedvideo — video add karo rotation mein (max 20)\n"
        "/listdroppedvideos — stored videos dekho\n"
        "/removedroppedvideo N — video N hata do\n"
        "/cleardroppedvideos — sab videos hata do (text-only)\n"
        "/testdropped — test post bhejo channel mein\n"
        "/joinkols — userbot ko sab KOL channels join karwao (realtime tracking ke liye)\n\n"

        "🔘 <b>Command Button Media (har button ki apni photo/video)</b>\n"
        "/setbuttonmedia BUTTON — button ka media set karo (reply karo)\n"
        "  Buttons: kol_request, promo_hub, tracked_kols, leaderboard, fast_track, chat_us\n"
        "  Example: <code>/setbuttonmedia fast_track</code> (phir video reply karo)\n"
        "/clearbuttonmedia BUTTON — button ka media hata do\n\n"

        "🎬 <b>X-Alert Details Video</b> (Details button ka WIZRAD intelligence video)\n"
        "/setxalertvideo — video/photo ko reply karo isse set karne ke liye\n"
        "/clearxalertvideo — default/text par wapas\n\n"

        "🔥 <b>Hot Streak Alerts</b>\n"
        "/testalerths [color] — 1 sample Hot Streak Alert bhejo (@TestChannel, fake data)\n"
        "  color: red/blue/green/golden (na diya to rotation se agla color)\n\n"

        "🌐 <b>Public Command Media (/submit /subscribe /linkme)</b>\n"
        "/setcommandmedia COMMAND — public command reply mein photo/video set karo (reply se)\n"
        "  Commands: <code>submit</code>, <code>subscribe</code>, <code>linkme</code>\n"
        "  Example: <code>/setcommandmedia subscribe</code> (phir video reply karo)\n"
        "/clearcommandmedia COMMAND — command ka media hata do\n\n"

        "📜 <b>History Media</b>\n"
        "/sethistorymedia — /history reply mein photo/video set karo (reply karo)\n"
        "/clearhistorymedia — history media hata do\n\n"

        "🔗 <b>Extra Alert Links (custom links in every alert post)</b>\n"
        "/addpostlink EMOJI_ID TEXT URL — alert mein extra link add karo\n"
        "  Example: <code>/addpostlink 5368324170671202310 ALPHA https://t.me/mychannel</code>\n"
        "/removepostlink N — link N hata do\n"
        "/listpostlinks — current extra links dekho\n\n"

        "🌏 <b>Per-KOL Language Templates (Chinese/Vietnamese/Turkish/Russian KOLs)</b>\n"
        "/setkollang CHANNEL chinese|vietnamese|turkish|russian|english — KOL ko language tag do\n"
        "/listkollang — sab tagged KOLs dekho\n"
        "/setkoltemplate CHANNEL LOW HIGH — sirf isi KOL ka template set karo\n"
        "  (agla message template text bhejo, us KOL ki apni language mein)\n"
        "  Example: <code>/setkoltemplate somekol 2 99</code> phir Chinese text bhejo\n"
        "/listkoltemplates CHANNEL — us KOL ke saved templates dekho\n"
        "/delkoltemplate CHANNEL LOW HIGH — us range ka template hata do\n"
        "/setkoldroppedtemplate CHANNEL — isi KOL ke 'Dropped a Call' post ka template\n"
        "/showkoldroppedtemplate CHANNEL — dekho, /clearkoldroppedtemplate CHANNEL — hatao\n"
        "ℹ️ <i>Jis KOL ka tag/template set nahi kiya, uski posts English mein "
        "hi jati rahengi — kuch nahi badalta.</i>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📚 <b>Help pages (tarteeb se):</b>\n"
        "1️⃣ /ownerhelp — main buttons panel\n"
        "2️⃣ /ownerhelp2 — yeh page\n"
        "3️⃣ /ownerhelpPS — PinkSale / GemPad / CheesePad\n"
        "4️⃣ /ownerhelpfull — poori purani list\n\n"
        "🆘 Koi masla ho to: <b>/fix</b>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_setbuttonmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a custom photo/video for a specific command button.
    Usage: /setbuttonmedia BUTTON_NAME  (reply to a photo/video)
    Buttons: kol_request, promo_hub, tracked_kols, leaderboard, fast_track, chat_us"""
    VALID_BUTTONS = ["kol_request", "promo_hub", "tracked_kols", "leaderboard", "fast_track", "chat_us"]
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "📸 <b>Button Media Set Karne Ka Tarika:</b>\n\n"
            "1. Photo ya video bhejo is chat mein\n"
            "2. Us media ko reply karo:\n"
            "   <code>/setbuttonmedia BUTTON_NAME</code>\n\n"
            "<b>Available buttons:</b>\n"
            + "\n".join(f"• <code>{b}</code>" for b in VALID_BUTTONS) +
            "\n\nExample: <code>/setbuttonmedia fast_track</code>",
            parse_mode="HTML"
        ); return
    btn = context.args[0].strip().lower()
    if btn not in VALID_BUTTONS:
        await msg.reply_text(
            f"❌ Invalid button: <code>{btn}</code>\n\n"
            f"Valid buttons:\n" + "\n".join(f"• <code>{b}</code>" for b in VALID_BUTTONS),
            parse_mode="HTML"
        ); return
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            f"📸 Video ya photo ko reply karo <code>/setbuttonmedia {btn}</code> se.",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    c = load_config()
    bm = c.get("button_media", {})
    bm[btn] = {"file_id": fid, "type": ftype}
    c["button_media"] = bm
    save_config(c)
    await msg.reply_text(f"✅ <b>{btn}</b> button ka media set ho gaya! ({ftype})", parse_mode="HTML")

@owner_only
async def cmd_clearbuttonmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove custom media from a specific command button."""
    VALID_BUTTONS = ["kol_request", "promo_hub", "tracked_kols", "leaderboard", "fast_track", "chat_us"]
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/clearbuttonmedia BUTTON_NAME</code>\n\nButtons: " +
            ", ".join(f"<code>{b}</code>" for b in VALID_BUTTONS),
            parse_mode="HTML"
        ); return
    btn = context.args[0].strip().lower()
    c = load_config()
    bm = c.get("button_media", {})
    if btn in bm:
        del bm[btn]
        c["button_media"] = bm
        save_config(c)
        await update.message.reply_text(f"✅ <b>{btn}</b> button ka custom media hata diya.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"ℹ️ <b>{btn}</b> button ka koi custom media nahi tha.", parse_mode="HTML")

@owner_only
async def cmd_sethistorymedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a photo/video → sets it as the /history reply media."""
    msg   = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📜 <b>History Media Set Karne Ka Tarika:</b>\n\n"
            "1. Photo ya video bhejo is chat mein\n"
            "2. Us media ko reply karo <code>/sethistorymedia</code> se\n\n"
            "Yeh /history @channel reply mein dikhega.",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    cfg_set("history_media", {"file_id": fid, "type": ftype})
    await msg.reply_text(f"✅ /history media set ho gaya! ({ftype})", parse_mode="HTML")

@owner_only
async def cmd_clearhistorymedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the custom /history media (revert to built-in VID_HISTORY)."""
    cfg_set("history_media", None)
    await update.message.reply_text("✅ History custom media hata diya. Ab built-in video use hogi.")

@owner_only
async def cmd_setlookupmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a photo/video → sets it as the @channel lookup (typed handle,
    not the /history command) record media. Separate slot from /sethistorymedia."""
    msg   = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📜 <b>Lookup Record Media Set Karne Ka Tarika:</b>\n\n"
            "1. Photo ya video bhejo is chat mein\n"
            "2. Us media ko reply karo <code>/setlookupmedia</code> se\n\n"
            "Yeh sirf jab koi @channelname direct type kare (lookup) tab dikhega — "
            "/history command wali video alag hai, ye usay touch nahi karta.",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    cfg_set("lookup_media", {"file_id": fid, "type": ftype})
    await msg.reply_text(f"✅ Lookup record media set ho gaya! ({ftype})", parse_mode="HTML")

@owner_only
async def cmd_clearlookupmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the custom lookup media (falls back to history_media, then built-in)."""
    cfg_set("lookup_media", None)
    await update.message.reply_text("✅ Lookup custom media hata diya. Ab /history wali (ya built-in) video use hogi.")

@owner_only
async def cmd_setkolacceptmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setkolacceptmedia — reply to a photo/video to attach it to the
    "Congratulations, you've been listed" DM that goes out when a /submit
    request is accepted. Stored under the same config["command_media"]
    mechanism as /setcommandmedia, just its own "kol_accept" key, so
    get_command_media("kol_accept") picks it up automatically."""
    msg = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📸 <b>KOL-Accept DM Photo Set Karne Ka Tarika:</b>\n\n"
            "1. Wo photo (ya video) is chat mein bhejo jo accept DM ke sath jani chahiye\n"
            "2. Us photo ko reply karo: <code>/setkolacceptmedia</code>\n\n"
            "Ab jab bhi koi /submit request accept hogi, yeh photo us "
            "\"Congratulations\" DM ke sath us KOL ko jayegi.\n\n"
            "Hatana ho to: <code>/clearkolacceptmedia</code>",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf photo ya video reply karo."); return
    c = load_config()
    cm = c.get("command_media", {})
    cm["kol_accept"] = {"file_id": fid, "type": ftype}
    c["command_media"] = cm
    save_config(c)
    await msg.reply_text(
        f"✅ KOL-accept DM ka media set ho gaya! ({ftype})\n\n"
        f"Ab jab bhi koi /submit request accept hogi, yeh {ftype} us "
        f"\"Congratulations\" DM ke sath jayegi.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearkolacceptmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the custom photo/video from the KOL-accept DM (falls back to text-only)."""
    c = load_config()
    cm = c.get("command_media", {})
    if "kol_accept" in cm:
        del cm["kol_accept"]
        c["command_media"] = cm
        save_config(c)
        await update.message.reply_text("✅ KOL-accept DM ka custom media hata diya. Ab sirf text jayega.")
    else:
        await update.message.reply_text("KOL-accept DM ke liye koi media set nahi hai.")

@owner_only
async def cmd_setsubmitconfirmmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setsubmitconfirmmedia — reply to a photo/video to OVERRIDE the
    bundled IMG_REQUEST_SUBMITTED banner used on the "REQUEST SUBMITTED"
    reply (the one sent right after /submit @channel succeeds). By default
    the bundled banner is used automatically — this command is only needed
    if the owner wants to swap it later without redeploying the bot."""
    msg = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📸 <b>REQUEST SUBMITTED Banner Badalne Ka Tarika:</b>\n\n"
            "1. Nayi photo (ya video) is chat mein bhejo\n"
            "2. Us par reply karo: <code>/setsubmitconfirmmedia</code>\n\n"
            "Default banner already bundled hai — yeh command sirf tab chahiye "
            "jab redeploy kiye bagair banner badalna ho.\n\n"
            "Default par wapas: <code>/clearsubmitconfirmmedia</code>",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf photo ya video reply karo."); return
    c = load_config()
    cm = c.get("command_media", {})
    cm["submit_confirm"] = {"file_id": fid, "type": ftype}
    c["command_media"] = cm
    save_config(c)
    await msg.reply_text(f"✅ REQUEST SUBMITTED banner override set ho gaya! ({ftype})", parse_mode="HTML")

@owner_only
async def cmd_clearsubmitconfirmmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the override and go back to the bundled IMG_REQUEST_SUBMITTED banner."""
    c = load_config()
    cm = c.get("command_media", {})
    if "submit_confirm" in cm:
        del cm["submit_confirm"]
        c["command_media"] = cm
        save_config(c)
        await update.message.reply_text("✅ Override hata diya. Ab default bundled banner use hoga.")
    else:
        await update.message.reply_text("Koi override set nahi hai — default bundled banner already use ho raha hai.")

@owner_only
async def cmd_setcommandmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setcommandmedia COMMAND — reply to a photo/video to set media for a public command.
    Supported: see PUBLIC_MEDIA_CMDS"""
    VALID_CMDS = PUBLIC_MEDIA_CMDS
    msg = update.message
    if not context.args:
        await msg.reply_text(
            "📸 <b>Public Command Media Set Karne Ka Tarika:</b>\n\n"
            "1. Photo ya video bhejo is chat mein\n"
            "2. Us media ko reply karo:\n"
            "   <code>/setcommandmedia COMMAND</code>\n\n"
            "<b>Available commands:</b>\n"
            + "\n".join(f"• <code>{c}</code> → /{c}" for c in VALID_CMDS) +
            "\n\nExample: <code>/setcommandmedia submit</code> (video reply ke saath)",
            parse_mode="HTML"
        ); return
    cmd = context.args[0].strip().lower().lstrip("/")
    if cmd not in VALID_CMDS:
        await msg.reply_text(
            f"❌ Invalid command: <code>{cmd}</code>\n\n"
            f"Valid options:\n" + "\n".join(f"• <code>{c}</code>" for c in VALID_CMDS),
            parse_mode="HTML"
        ); return
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            f"📸 Photo ya video ko reply karo <code>/setcommandmedia {cmd}</code> se.",
            parse_mode="HTML"
        ); return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    c = load_config()
    cm = c.get("command_media", {})
    cm[cmd] = {"file_id": fid, "type": ftype}
    c["command_media"] = cm
    save_config(c)
    await msg.reply_text(
        f"✅ <b>/{cmd}</b> command ka media set ho gaya! ({ftype})\n\n"
        f"Ab jab koi <code>/{cmd}</code> use karega, yeh {ftype} dikhegi.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearcommandmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clearcommandmedia COMMAND — remove custom media from a public command."""
    VALID_CMDS = PUBLIC_MEDIA_CMDS
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/clearcommandmedia COMMAND</code>\n\nCommands: " +
            ", ".join(f"<code>{c}</code>" for c in VALID_CMDS),
            parse_mode="HTML"
        ); return
    cmd = context.args[0].strip().lower().lstrip("/")
    c = load_config()
    cm = c.get("command_media", {})
    if cmd in cm:
        del cm[cmd]
        c["command_media"] = cm
        save_config(c)
        await update.message.reply_text(
            f"✅ <b>/{cmd}</b> ka custom media hata diya. Ab default text/image use hoga.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"ℹ️ <b>/{cmd}</b> ka koi custom media nahi tha.",
            parse_mode="HTML"
        )

@owner_only
async def cmd_addpostlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add an extra link to every alert post with a custom premium emoji.
    Usage: /addpostlink EMOJI_ID LINK_TEXT URL
    Example: /addpostlink 5368324170671202310 ALPHA https://t.me/mychannel"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "🔗 <b>Alert Post Extra Link Add Karne Ka Tarika:</b>\n\n"
            "<code>/addpostlink EMOJI_ID LINK_TEXT URL</code>\n\n"
            "Example:\n"
            "<code>/addpostlink 5368324170671202310 ALPHA https://t.me/mychannel</code>\n\n"
            "EMOJI_ID = premium emoji ka ID (/getemoji se hasil karo)\n"
            "LINK_TEXT = button ka text (e.g. ALPHA, SIGNALS, JOIN)\n"
            "URL = link address\n\n"
            "Alert post mein yeh line add hogi:\n"
            "<code>🔮<a href='URL'>LINK_TEXT</a></code>",
            parse_mode="HTML"
        ); return
    try:
        emoji_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ EMOJI_ID number hona chahiye. /getemoji se ID nikal lo."); return
    link_text = context.args[1].strip()
    link_url  = context.args[2].strip()
    if not link_url.startswith("http"):
        await update.message.reply_text("⚠️ URL http/https se shuru hona chahiye."); return
    c = load_config()
    links = c.get("extra_post_links", [])
    if len(links) >= 5:
        await update.message.reply_text("⚠️ Maximum 5 extra links allowed. /removepostlink se pehle ek hata do."); return
    links.append({"emoji_id": emoji_id, "text": link_text, "url": link_url})
    c["extra_post_links"] = links
    save_config(c)
    await update.message.reply_text(
        f"✅ <b>Extra Link #{len(links)} Added!</b>\n\n"
        f"Emoji ID: <code>{emoji_id}</code>\n"
        f"Text: {html.escape(link_text)}\n"
        f"URL: {link_url}\n\n"
        f"Ab har alert post mein yeh link dikhega.\n"
        f"Use /listpostlinks to see all · /removepostlink N to remove",
        parse_mode="HTML"
    )

@owner_only
async def cmd_removepostlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an extra post link by number."""
    if not context.args:
        await update.message.reply_text("Usage: /removepostlink <number>\nUse /listpostlinks to see numbers."); return
    try: n = int(context.args[0])
    except ValueError: await update.message.reply_text("Please send a valid number."); return
    links = load_config().get("extra_post_links", [])
    if n < 1 or n > len(links):
        await update.message.reply_text(f"❌ Invalid number. There are {len(links)} links."); return
    removed = links.pop(n - 1)
    cfg_set("extra_post_links", links)
    await update.message.reply_text(
        f"✅ Link #{n} hata diya: <b>{html.escape(removed.get('text',''))}</b>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_addpriority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a special KOL channel to the 1-second priority fast lane."""
    if not context.args:
        await update.message.reply_text("Usage: /addpriority @channel"); return
    added = []
    not_tracked = []
    tracked_lower = {str(c).lstrip("@").lower() for c in load_channels()}
    lst = load_priority_channels()
    for a in context.args:
        ch = str(a).lstrip("@").strip()
        if not ch:
            continue
        if ch.lower() not in tracked_lower:
            not_tracked.append(ch)
            continue
        if ch.lower() not in {c.lower() for c in lst}:
            lst.append(ch); added.append(ch)
    save_priority_channels(lst)

    # FIX: without this, a priority channel that isn't already joined by the
    # userbot keeps falling back to weak t.me/s scraping (hidden links,
    # button-only CAs get missed) even though it's scanned every tick.
    global userbot_client
    join_note = ""
    if added and userbot_client and userbot_client.is_connected():
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            for ch in added:
                try:
                    await userbot_client.get_entity(ch)
                    try:
                        await userbot_client(JoinChannelRequest(ch))
                    except Exception:
                        pass  # already a member or public access is enough
                except Exception as e:
                    logger.warning(f"addpriority: userbot join failed @{ch}: {e}")
            await setup_realtime_monitoring(context.bot)
            join_note = "\n🔗 Userbot un channels mein join/registered ho gaya (realtime active)."
        except Exception as e:
            logger.warning(f"addpriority: realtime re-register failed: {e}")
            join_note = "\n⚠️ Userbot join automatically nahi ho saka — /joinkols chala dein."

    msg = ""
    if added:
        msg += "\u26a1 Priority me add ho gaya: " + ", ".join("@" + c for c in added) + join_note
    if not_tracked:
        if msg: msg += "\n\n"
        msg += ("⚠️ Yeh channel(s) tracked list mein nahi hain, is liye priority mein add nahi "
                "hue (scan_job inhe skip kar deta hai):\n" +
                "\n".join(f"• @{c} — pehle <code>/addchannel {c}</code> karein" for c in not_tracked))
    if not msg:
        msg = "\u2139\ufe0f Ye channel(s) pehle se priority list me hain."
    await update.message.reply_text(msg, parse_mode="HTML")

@owner_only
async def cmd_removepriority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a channel from the priority fast lane."""
    if not context.args:
        await update.message.reply_text("Usage: /removepriority @channel"); return
    targets = {str(a).lstrip("@").lower() for a in context.args}
    lst = [c for c in load_priority_channels() if c.lower() not in targets]
    save_priority_channels(lst)
    await update.message.reply_text("\u2705 Priority list update ho gayi.")

@owner_only
async def cmd_listpriority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the priority (special) KOL channels."""
    lst = load_priority_channels()
    if not lst:
        await update.message.reply_text(
            "\U0001f4ed Koi priority channel set nahi.\n\nUse /addpriority @channel"); return
    await update.message.reply_text(
        "\u26a1 <b>Priority KOL Channels</b>\n" + "\n".join(f"\u2022 @{c}" for c in lst),
        parse_mode="HTML")

@owner_only
async def cmd_listpostlinks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all extra post links."""
    links = load_config().get("extra_post_links", [])
    if not links:
        await update.message.reply_text(
            "📭 Koi extra post link nahi hai.\n\n"
            "Use /addpostlink EMOJI_ID TEXT URL to add one."
        ); return
    lines = [f"🔗 <b>Extra Alert Post Links ({len(links)})</b>\n"]
    for i, lnk in enumerate(links, 1):
        lines.append(
            f"<b>{i}.</b> Emoji: <code>{lnk.get('emoji_id','?')}</code>\n"
            f"     Text: {html.escape(lnk.get('text',''))}\n"
            f"     URL: {lnk.get('url','')}"
        )
    lines.append("\nUse /removepostlink N to remove · /addpostlink to add more")
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

@owner_only
async def cmd_setcommandvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a video/photo → sets it as the /command menu media."""
    msg = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📹 <b>Command Video Set karne ka tarika:</b>\n\n"
            "1. Apni video forward karo is chat mein\n"
            "2. Us video ko reply karo <code>/setcommandvideo</code> se\n\n"
            "Video /command mein dikhnay lagegi.",
            parse_mode="HTML"
        )
        return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    cfg_set("menu_media", {"file_id": fid, "type": ftype})
    await msg.reply_text(f"✅ /command menu video set ho gayi! ({ftype})")

@owner_only
async def cmd_setxalertvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a video/photo → sets it as the X-alert 'Details' (WIZRAD
    intelligence) video, replacing the hardcoded local VID_X_ALERT_DETAILS
    file. Stored as a Telegram file_id via cfg_set so it survives redeploys
    — the old constant pointed at a local attached_assets/ path that isn't
    guaranteed to exist on a fresh Railway container, which is why this
    command never actually worked before (it didn't exist at all)."""
    msg = update.message
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📹 <b>X-Alert Details Video Set karne ka tarika:</b>\n\n"
            "1. Apni video (ya photo) is chat mein bhejo/forward karo\n"
            "2. Us video ko reply karo <code>/setxalertvideo</code> se\n\n"
            "Ye video ab 'Details' button dabane par WIZRAD intelligence "
            "caption ke saath jayegi.\n\n"
            "Hatane ke liye: /clearxalertvideo",
            parse_mode="HTML"
        )
        return
    if reply.video:
        fid = reply.video.file_id; ftype = "video"
    elif reply.photo:
        fid = reply.photo[-1].file_id; ftype = "photo"
    elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
        fid = reply.document.file_id; ftype = "video"
    else:
        await msg.reply_text("❌ Sirf video ya photo reply karo."); return
    cfg_set("xalert_details_video", {"file_id": fid, "type": ftype})
    await msg.reply_text(f"✅ X-alert Details video set ho gayi! ({ftype})")

@owner_only
async def cmd_clearxalertvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the custom X-alert Details video, falling back to the old
    local-file constant (if present) or plain text."""
    cfg_set("xalert_details_video", None)
    await update.message.reply_text("🗑️ X-alert Details video clear kar diya gaya.")

@owner_only
async def cmd_setpromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set promo template + optional video (reply to video), enables promo every 25 alerts."""
    msg = update.message
    template = " ".join(context.args).strip() if context.args else ""
    if not template:
        await msg.reply_text(
            "📣 <b>Promo Set karne ka tarika:</b>\n\n"
            "<code>/setpromo Apna promo text yahan likhein</code>\n\n"
            "Video ke saath: pehle video bhejo, phir us video ko reply karo "
            "<code>/setpromo Apna text</code> se.\n\n"
            "Promo har <b>25 alerts</b> ke baad @WizardScan mein post hogi.\n"
            "/stoppromo se band karo.",
            parse_mode="HTML"
        )
        return
    c = load_config()
    c["promo_template"] = template
    c["promo_enabled"]  = True
    # Check if replying to video
    reply = msg.reply_to_message
    if reply:
        if reply.video:
            c["promo_video"] = {"file_id": reply.video.file_id, "type": "video"}
        elif reply.photo:
            c["promo_video"] = {"file_id": reply.photo[-1].file_id, "type": "photo"}
        elif reply.document and reply.document.mime_type and reply.document.mime_type.startswith("video"):
            c["promo_video"] = {"file_id": reply.document.file_id, "type": "video"}
    save_config(c)
    vid_status = "✅ Video bhi set ho gayi." if reply and c.get("promo_video") else "ℹ️ Koi video nahi (sirf text post hogi)."
    await msg.reply_text(
        f"✅ <b>Promo Active!</b>\n\n"
        f"Template set ho gaya. Har 25 alerts ke baad @WizardScan mein post hogi.\n\n"
        f"{vid_status}\n\n"
        f"Band karne ke liye: /stoppromo",
        parse_mode="HTML"
    )

@owner_only
async def cmd_stoppromo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable the promo post."""
    cfg_set("promo_enabled", False)
    await update.message.reply_text("🔕 Promo band kar diya. Alerts ke baad ab promo post nahi hogi.")

@owner_only
async def cmd_setpromolink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a custom text+link for 2X-50X alert posts for 48 hours."""
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_SETPROMOLINK}
    await update.message.reply_text(
        "🔗 <b>Set Promo Link (48 hours)</b>\n\n"
        "Send your promo text and link in two lines:\n\n"
        "<code>Your custom text here\nhttps://yourlink.com</code>\n\n"
        "Ye link har 2X–50X post ke <b>sabse neeche</b> (Champion / Leaderboard "
        "KOL line ke baad, ek khali line chhod kar) 48 ghante tak show hoga.\n\n"
        "Text bhejne ke baad bot premium emoji ki ID poochega.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearpromolink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the active promo link immediately."""
    cfg_set("promo_link", None)
    await update.message.reply_text("✅ Promo link removed from alert posts.")

@owner_only
async def cmd_pendingkols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show only unreviewed pending KOL requests (not yet accepted/rejected)."""
    pending = load_pending()
    active_channels_lower = {c.lower() for c in load_channels()}
    # Filter to requests whose channel has not been listed yet
    unreviewed = {
        rid: req for rid, req in pending.items()
        if req.get("channel", "").lower() not in active_channels_lower
    }
    if not unreviewed:
        await update.message.reply_text("✅ No unreviewed KOL requests at the moment."); return
    await update.message.reply_text(f"📋 <b>Pending KOL Requests ({len(unreviewed)} unreviewed)</b>", parse_mode="HTML")
    for req_id, req in list(unreviewed.items()):
        uid   = req.get("user_id", "?")
        uname = req.get("username", f"User#{uid}")
        ch    = req.get("channel", "?")
        ts    = req.get("ts", "")[:16]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"kreq|{uid}|{ch[:28]}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"krej|{uid}|{ch[:28]}"),
        ]])
        await update.message.reply_text(
            f"{PE_CRYSTAL} Channel: <b>@{ch}</b>\n👤 From: @{uname} (ID: <code>{uid}</code>)\n🕐 {ts} UTC",
            parse_mode="HTML", reply_markup=kb)

@owner_only
async def cmd_previewtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview what an alert post looks like for a given X value."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /previewtemplate 100\nExample: /previewtemplate 1000")
        return
    x_val = int(context.args[0])
    preview = build_alert(
        channel="DemoKOL", msg_id=1, x_val=x_val, chain="SOL",
        entry_fmt="$5K", current_fmt=f"${x_val*5}K", ca="So1DemoCA1111111111111111111111111111111111", symbol="DEMO"
    )
    await update.message.reply_text(
        f"🔍 <b>Preview — {x_val}X Template:</b>\n\n{preview}",
        parse_mode="HTML", disable_web_page_preview=True
    )

@owner_only
async def cmd_previewmomentum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview a MOMENTUM ACTIVE post in owner DM — bot API only (fast, reliable)."""
    bot_username = (await context.bot.get_me()).username
    xray_url     = f"https://t.me/{bot_username}?start=xray_DemoKOL_10"
    text = (
        "<b>🔮 MOMENTUM ACTIVE 🔮</b>\n\n"
        "<b>@DemoKOL</b> has delivered <b>5</b> calls above <b>10X</b> in the last 7 days.\n\n"
        "Consistent edge. Consistent results. Track the pattern."
    )
    buttons = [
        [_pkb("🔮 KOL Signal", url="https://t.me/WizardScan"),
         _pkb("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [_pkb("🔮 KOL Signal", url="https://t.me/WizardScan"),
         _pkb("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [_pkb("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [_pkb("🔮 X-Ray Report", url=xray_url)],
    ]
    kb = InlineKeyboardMarkup(buttons)

    # Use bot API directly for DM preview (reliable; userbot reserved for channel posts)
    _pcfg    = load_config()
    mom_vids = _pcfg.get("momentum_videos", [])
    sent     = False

    if mom_vids:
        idx     = _pcfg.get("momentum_video_index", 0)
        _mv     = mom_vids[idx % len(mom_vids)]
        vid_fid = _mv.get("file_id")
        ftype   = _mv.get("type", "video")
        if vid_fid:
            try:
                if ftype == "gif":
                    await update.message.reply_animation(animation=vid_fid, caption=text, parse_mode="HTML", reply_markup=kb)
                else:
                    await update.message.reply_video(video=vid_fid, caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception as ep:
                logger.warning(f"previewmomentum video send failed: {ep}")

    if not sent:
        no_vid_note = (
            "\n\n<i>📌 No momentum video set yet.\n"
            "Use /addmomentumvideo to add videos — actual channel posts will include the video.</i>"
        ) if not mom_vids else ""
        await update.message.reply_text(text + no_vid_note, parse_mode="HTML", reply_markup=kb)

@owner_only
async def cmd_premiumguide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌟 <b>PREMIUM EMOJI SETUP GUIDE</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>STEP 1 — Emoji ID nikalna</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Telegram mein koi bhi chat kholo\n"
        "2️⃣ Message type karo aur woh premium emoji add karo jo chahiye\n"
        "3️⃣ Us message ko <b>@getidsbot</b> ko forward karo\n"
        "4️⃣ Bot ek lamba reply dega — usme <code>custom_emoji_id</code> wala number copy karo\n\n"
        "Woh number kuch aisa hoga:\n"
        "<code>5368324170671202286</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>STEP 2 — Range set karna</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "2x se 49x tak ek style:\n"
        "<code>/setalertemoji low 5368324170671202286</code>\n\n"
        "50x aur upar dusra style:\n"
        "<code>/setalertemoji high 5391210377057173506</code>\n\n"
        "Sirf 10x ke liye alag (optional):\n"
        "<code>/setalertemoji 10 5368324170671202286</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>STEP 3 — Check karo</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "/listalertemojis — sab set emojis dekho\n"
        "/testalert — channel pe test post karo\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>Zaruri baatein</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "• Userbot (premium account) ka connected hona zaruri hai\n"
        "• /userbotcheck se confirm karo ke connected hai\n"
        "• Premium emoji sirf 🔮 wali jagah replace hoti hai post mein\n"
        "• Agar emoji show na ho — userbot ka premium active check karo\n\n"
        "💡 <b>Asaan tarika:</b> Bot ko seedha premium emoji bhejo — /getemoji command se!",
        parse_mode="HTML"
    )

@owner_only
async def cmd_getemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User premium emoji bhejta hai, bot ID nikal ke deta hai aur set bhi kar deta hai."""
    msg = update.message

    # Check if this is a reply to a message with premium emojis
    target = msg.reply_to_message if msg.reply_to_message else msg

    # Collect all custom emoji IDs from entities
    all_entities = list(target.entities or []) + list(target.caption_entities or [])
    emoji_ids = []
    for ent in all_entities:
        if ent.type == "custom_emoji" and ent.custom_emoji_id:
            if ent.custom_emoji_id not in emoji_ids:
                emoji_ids.append(ent.custom_emoji_id)

    if not emoji_ids:
        await msg.reply_text(
            "🌟 <b>Premium Emoji ID Nikalo</b>\n\n"
            "Aisa karo:\n"
            "1️⃣ Message box mein emoji button dabao\n"
            "2️⃣ ✨ wala star icon dabao — premium emojis aayenge\n"
            "3️⃣ Jo emoji chahiye woh select karo\n"
            "4️⃣ Send karo\n"
            "5️⃣ Us message ko reply karo <code>/getemoji</code> se\n\n"
            "Ya seedha: premium emoji type karo aur bhejo — main ID nikal dunga!",
            parse_mode="HTML"
        )
        return

    lines = []
    for i, eid in enumerate(emoji_ids[:5]):
        lines.append(f"Emoji {i+1}: <code>{eid}</code>")

    # Quick-set buttons — position based
    # pos1 = pehla 🔮 (top line), pos2 = doosra 🔮 (price line), global = sab same
    keyboard_rows = []
    if len(emoji_ids) == 1:
        eid = emoji_ids[0]
        keyboard_rows.append([
            InlineKeyboardButton("1️⃣ Pehla 🔮 (top)", callback_data=f"setemoji:pos1:{eid}"),
            InlineKeyboardButton("2️⃣ Doosra 🔮 (price)", callback_data=f"setemoji:pos2:{eid}"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("🌍 Dono 🔮 same", callback_data=f"setemoji:global:{eid}"),
        ])
        keyboard_rows.append([
            InlineKeyboardButton("🟢 LOW range (2x–49x)", callback_data=f"setemoji:low:{eid}"),
            InlineKeyboardButton("🔴 HIGH range (50x+)", callback_data=f"setemoji:high:{eid}"),
        ])
    else:
        for i, eid in enumerate(emoji_ids[:2]):
            keyboard_rows.append([
                InlineKeyboardButton(f"Emoji {i+1} → Pos1 (top 🔮)", callback_data=f"setemoji:pos1:{eid}"),
                InlineKeyboardButton(f"Emoji {i+1} → Pos2 (price 🔮)", callback_data=f"setemoji:pos2:{eid}"),
            ])
        keyboard_rows.append([
            InlineKeyboardButton(f"Emoji 1 → LOW", callback_data=f"setemoji:low:{emoji_ids[0]}"),
            InlineKeyboardButton(f"Emoji 1 → HIGH", callback_data=f"setemoji:high:{emoji_ids[0]}"),
        ])

    reply = (
        f"✅ <b>{len(emoji_ids)} premium emoji(s) mili!</b>\n\n"
        + "\n".join(lines) +
        "\n\n<b>Kahan set karna hai? Button dabao:</b>\n"
        "<i>Pos1 = post ka pehla 🔮 | Pos2 = price wala 🔮</i>"
    )
    await msg.reply_text(reply, parse_mode="HTML",
                         reply_markup=InlineKeyboardMarkup(keyboard_rows))

async def cb_setemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback: set emoji from getemoji buttons."""
    query = update.callback_query
    await query.answer()
    if not query.from_user or not is_admin_or_owner(query.from_user.id):
        await query.answer("⛔ Admins only", show_alert=True); return
    try:
        _, range_key, emoji_id = query.data.split(":", 2)
    except Exception:
        return
    c = load_config()
    emojis = c.get("alert_emoji_ids", {})
    emojis[range_key] = emoji_id
    c["alert_emoji_ids"] = emojis
    save_config(c)
    labels = {
        "low":    "LOW range (2x – 49x)",
        "high":   "HIGH range (50x aur upar)",
        "global": "Dono 🔮 same (global)",
        "pos1":   "Pehla 🔮 position (top line)",
        "pos2":   "Doosra 🔮 position (price line)",
    }
    label = labels.get(range_key, f"{range_key}X")
    await query.edit_message_text(
        f"✅ <b>Premium emoji set!</b>\n\n"
        f"Range: <b>{label}</b>\n"
        f"ID: <code>{emoji_id}</code>\n\n"
        f"Ab channel posts mein 🔮 ki jagah yeh emoji lagegi!\n"
        f"Test karo: /debugemoji",
        parse_mode="HTML"
    )

@owner_only
async def cmd_debugemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a live test to channel with current premium emoji settings."""
    msg = update.message
    c = load_config()
    emojis = c.get("alert_emoji_ids", {})

    if not emojis:
        await msg.reply_text("⚠️ Koi emoji set nahi hai!\nPehle /getemoji use karo.")
        return

    await msg.reply_text(
        f"🔍 <b>Debug info:</b>\n\n"
        f"Saved emojis: <code>{emojis}</code>\n"
        f"Userbot connected: <b>{'✅ Yes' if userbot_client else '❌ No'}</b>\n\n"
        f"Channel pe test post bhej raha hun...",
        parse_mode="HTML"
    )

    # Build a test post text exactly like real alerts
    test_text = (
        "🔮 <b>@TestKOL KOL Hit 2X!</b>\n\n"
        "(<b>$TEST</b>) SOL play called at $10K. Current: $20K.\n\n"
        "Ca: <code>0xTESTCALONLY</code>\n\n"
        "🔮 $10K    🔮    $20K\n\n"
        f"{PE_ARROW} <a href='https://t.me/WizardScan'>KOL</a>\n"
        f"{PE_ARROW} <a href='https://t.me/WizardScan'>TG</a>"
    )

    # Figure out which emoji_id to use (same logic as send_alert)
    range_key = "low"
    base_emoji = emojis.get("global") or emojis.get("low") or emojis.get("high")
    pos1 = emojis.get("pos1") or base_emoji
    pos2 = emojis.get("pos2") or base_emoji
    if pos1 and pos2 and pos1 != pos2:
        emoji_id = [pos1, pos2]
    elif pos1:
        emoji_id = pos1
    elif pos2:
        emoji_id = pos2
    else:
        emoji_id = base_emoji

    await msg.reply_text(f"Using emoji_id: <code>{emoji_id}</code>", parse_mode="HTML")

    if not userbot_client:
        await msg.reply_text(
            "❌ <b>Userbot connected nahi hai!</b>\n\n"
            "Premium emoji ke liye userbot zaroori hai.\n"
            "Type karo: /userbotlogin",
            parse_mode="HTML"
        )
        return

    try:
        from telethon.extensions.html import parse as tl_html_parse
        plain_text, base_ents = tl_html_parse(test_text)
        all_entities = _build_premium_entities(plain_text, base_ents, emoji_id)
        sent = await userbot_client.send_message(
            TARGET_CHANNEL, plain_text,
            formatting_entities=all_entities,
            link_preview=False
        )
        await msg.reply_text(
            f"✅ <b>Test post channel pe bhej diya!</b>\n"
            f"Message ID: <code>{sent.id}</code>\n\n"
            f"Channel check karo — 🔮 ki jagah premium emoji dikhni chahiye!",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.reply_text(
            f"❌ <b>Error:</b> <code>{e}</code>\n\n"
            f"Yeh error share karo please.",
            parse_mode="HTML"
        )

@owner_only
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /addadmin USER_ID"); return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ USER_ID must be a number."); return
    admins = load_admins()
    if uid in admins:
        await update.message.reply_text(f"ℹ️ {uid} is already an admin."); return
    admins.append(uid); save_admins(admins)
    await update.message.reply_text(f"✅ {uid} added as admin.\n\nAdmin can now use: /setalertemoji /listalertemojis /clearalertemoji /setrankingemojis")

@owner_only
async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin USER_ID"); return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ USER_ID must be a number."); return
    admins = load_admins()
    if uid not in admins:
        await update.message.reply_text(f"⚠️ {uid} is not an admin."); return
    admins.remove(uid); save_admins(admins)
    await update.message.reply_text(f"✅ {uid} removed from admins.")

@owner_only
async def cmd_listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = load_admins()
    if not admins:
        await update.message.reply_text("👤 No admins added yet.\n\nUse /addadmin USER_ID"); return
    lines = [f"{i+1}. <code>{uid}</code>" for i, uid in enumerate(admins)]
    await update.message.reply_text(
        f"👤 <b>Admins ({len(admins)})</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@admin_only
async def cmd_setalertemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set premium emoji ID for alert posts. Usage: /setalertemoji <x_val|global|low|high> <EMOJI_ID>"""
    if len(context.args) < 2:
        current = cfg_get("alert_emoji_ids", {})
        def label_key(k):
            if k == "global": return "🌍 global (sab)"
            if k == "low":    return "🟢 low (2x–49x)"
            if k == "high":   return "🔴 high (50x+)"
            return f"⚡ {k}X"
        lines = [f"{label_key(k)}: <code>{v}</code>" for k, v in current.items()]
        await update.message.reply_text(
            "🌟 <b>Alert Emoji IDs</b>\n\n"
            "Usage:\n"
            "<code>/setalertemoji low EMOJI_ID</code> — 2x se 49x tak\n"
            "<code>/setalertemoji high EMOJI_ID</code> — 50x aur upar\n"
            "<code>/setalertemoji global EMOJI_ID</code> — sab ke liye\n"
            "<code>/setalertemoji 10 EMOJI_ID</code> — sirf 10x ke liye\n\n"
            f"<b>Currently set:</b>\n" + ("\n".join(lines) if lines else "<i>None</i>"),
            parse_mode="HTML"); return
    key      = context.args[0].lower().strip()
    emoji_id = context.args[1].strip()
    try: int(emoji_id)
    except ValueError:
        await update.message.reply_text("⚠️ EMOJI_ID sirf numbers hota hai (e.g. 5368324170671202286)."); return
    c = load_config()
    emojis = c.get("alert_emoji_ids", {})
    emojis[key] = emoji_id
    c["alert_emoji_ids"] = emojis
    save_config(c)
    if key == "low":    label = "2x – 49x range"
    elif key == "high": label = "50x aur upar"
    elif key == "global": label = "global (sab posts)"
    else: label = f"{key}X posts"
    await update.message.reply_text(f"✅ Premium emoji set for <b>{label}</b>\n\nID: <code>{emoji_id}</code>", parse_mode="HTML")

@admin_only
async def cmd_listalertemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    emojis = cfg_get("alert_emoji_ids", {})
    ranking = cfg_get("ranking_emojis", [])
    lines = [f"{'global' if k == 'global' else k+'X'}: <code>{v}</code>" for k, v in emojis.items()]
    rank_lines = [f"Pos {i+1}: <code>{e}</code>" for i, e in enumerate(ranking) if e]
    text = "🌟 <b>Premium Emoji IDs</b>\n\n"
    text += "<b>Alert Emojis:</b>\n" + ("\n".join(lines) if lines else "<i>None set</i>") + "\n\n"
    text += "<b>Ranking Emojis (post 136):</b>\n" + ("\n".join(rank_lines) if rank_lines else "<i>None set</i>")
    await update.message.reply_text(text, parse_mode="HTML")

@admin_only
async def cmd_clearalertemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /clearalertemoji <x_val|global>"); return
    key = context.args[0].lower().strip()
    c   = load_config(); emojis = c.get("alert_emoji_ids", {})
    if key not in emojis:
        await update.message.reply_text(f"⚠️ No emoji set for '{key}'."); return
    del emojis[key]; c["alert_emoji_ids"] = emojis; save_config(c)
    await update.message.reply_text(f"✅ Emoji removed for '{key}'.")


# ═══════════════════════════════════════════════════════════════════════════
# CHEESEPAD / PINKSALE — owner-editable premium emoji IDs + templates
#   /setcpemoji · /listcpemojis · /clearcpemoji     → CheesePad emoji pack
#   /setpsemoji · /listpsemojis · /clearpsemoji     → PinkSale emoji pack
#   /setcptemplate · /cptemplate · /resetcptemplate → CheesePad text templates
#   /setpstemplate · /pstemplate · /resetpstemplate → PinkSale text templates
# ═══════════════════════════════════════════════════════════════════════════

def _apply_emoji_overrides():
    """Called at startup: overlay owner-saved emoji-ID overrides on top of
    the built-in CP_EMOJI / PS_EMOJI packs (persists across restarts)."""
    try:
        for k, v in (cfg_get("cp_emoji_overrides", {}) or {}).items():
            try: CP_EMOJI[k] = int(v)
            except Exception: pass
        for k, v in (cfg_get("ps_emoji_overrides", {}) or {}).items():
            try: PS_EMOJI[k] = int(v)
            except Exception: pass
    except Exception as e:
        logger.warning(f"_apply_emoji_overrides failed: {e}")


def _emoji_pack_cmd_texts(pack_name, emoji_dict, cfg_key):
    keys = ", ".join(sorted(emoji_dict.keys()))
    return keys

@owner_only
async def cmd_setcpemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setcpemoji <key> <EMOJI_ID> — owner's own premium emoji for CheesePad posts."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "🧀 <b>Set CheesePad Premium Emoji</b>\n\n"
            "Usage: <code>/setcpemoji &lt;key&gt; &lt;EMOJI_ID&gt;</code>\n\n"
            f"Available keys:\n<code>{_emoji_pack_cmd_texts('cp', CP_EMOJI, 'cp_emoji_overrides')}</code>\n\n"
            "Example: <code>/setcpemoji fire 5769583457395024807</code>\n\n"
            "See /listcpemojis for current values.", parse_mode="HTML"); return
    key, emoji_id = context.args[0].lower().strip(), context.args[1].strip()
    if key not in CP_EMOJI:
        await update.message.reply_text(
            f"⚠️ Unknown key '{key}'.\nValid keys:\n<code>{_emoji_pack_cmd_texts('cp', CP_EMOJI, '')}</code>",
            parse_mode="HTML"); return
    try: int(emoji_id)
    except ValueError:
        await update.message.reply_text("⚠️ EMOJI_ID sirf numbers hota hai."); return
    CP_EMOJI[key] = int(emoji_id)
    overrides = cfg_get("cp_emoji_overrides", {}); overrides[key] = emoji_id
    cfg_set("cp_emoji_overrides", overrides)
    await update.message.reply_text(f"✅ CheesePad emoji '<b>{key}</b>' set to <code>{emoji_id}</code>", parse_mode="HTML")

@owner_only
async def cmd_listcpemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{k}: <code>{v}</code>" for k, v in sorted(CP_EMOJI.items())]
    await update.message.reply_text(
        "🧀 <b>CheesePad Premium Emoji IDs</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_clearcpemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /clearcpemoji <key>"); return
    key = context.args[0].lower().strip()
    overrides = cfg_get("cp_emoji_overrides", {})
    if key not in overrides:
        await update.message.reply_text(f"⚠️ No custom override set for '{key}' (still using built-in default)."); return
    del overrides[key]; cfg_set("cp_emoji_overrides", overrides)
    await update.message.reply_text(f"✅ '{key}' reset to built-in default. (Restart bot to fully restore original ID.)")

@owner_only
async def cmd_setpsemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setpsemoji <key> <EMOJI_ID> — owner's own premium emoji for PinkSale posts."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "🩷 <b>Set PinkSale Premium Emoji</b>\n\n"
            "Usage: <code>/setpsemoji &lt;key&gt; &lt;EMOJI_ID&gt;</code>\n\n"
            f"Available keys:\n<code>{_emoji_pack_cmd_texts('ps', PS_EMOJI, 'ps_emoji_overrides')}</code>\n\n"
            "Example: <code>/setpsemoji fire 5769583457395024807</code>\n\n"
            "See /listpsemojis for current values.", parse_mode="HTML"); return
    key, emoji_id = context.args[0].lower().strip(), context.args[1].strip()
    if key not in PS_EMOJI:
        await update.message.reply_text(
            f"⚠️ Unknown key '{key}'.\nValid keys:\n<code>{_emoji_pack_cmd_texts('ps', PS_EMOJI, '')}</code>",
            parse_mode="HTML"); return
    try: int(emoji_id)
    except ValueError:
        await update.message.reply_text("⚠️ EMOJI_ID sirf numbers hota hai."); return
    PS_EMOJI[key] = int(emoji_id)
    overrides = cfg_get("ps_emoji_overrides", {}); overrides[key] = emoji_id
    cfg_set("ps_emoji_overrides", overrides)
    await update.message.reply_text(f"✅ PinkSale emoji '<b>{key}</b>' set to <code>{emoji_id}</code>", parse_mode="HTML")

@owner_only
async def cmd_listpsemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{k}: <code>{v}</code>" for k, v in sorted(PS_EMOJI.items())]
    await update.message.reply_text(
        "🩷 <b>PinkSale Premium Emoji IDs</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_clearpsemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /clearpsemoji <key>"); return
    key = context.args[0].lower().strip()
    overrides = cfg_get("ps_emoji_overrides", {})
    if key not in overrides:
        await update.message.reply_text(f"⚠️ No custom override set for '{key}' (still using built-in default)."); return
    del overrides[key]; cfg_set("ps_emoji_overrides", overrides)
    await update.message.reply_text(f"✅ '{key}' reset to built-in default. (Restart bot to fully restore original ID.)")


# ─── Template editing ─────────────────────────────────────────────────────────
CP_CHANNEL_VARS  = ("name", "caller", "chain", "sale_type", "soft_cap", "hard_cap",
                     "min_buy", "max_buy", "raised", "starts", "ends",
                     "cheesepad_link", "details_link", "kol_link")
CP_DETAILS_VARS  = ("name", "caller", "chain", "sale_type", "token", "supply", "currency",
                     "soft_cap", "hard_cap", "raised", "min_buy", "max_buy", "starts", "ends",
                     "presale_rate", "listing_rate", "tokens_for_sale", "liquidity", "lp_lock",
                     "token_ca", "sale_address", "buy_link", "caller_post_link", "chain_emoji")
PS_CHANNEL_VARS  = ("name", "caller", "chain", "sale_type", "soft_cap", "hard_cap",
                     "min_buy", "max_buy", "raised", "starts", "ends",
                     "pinksale_link", "details_link", "channel_link")
PS_DETAILS_VARS  = ("name", "caller", "chain", "sale_type", "token", "supply", "currency",
                     "soft_cap", "hard_cap", "raised", "min_buy", "max_buy", "starts", "ends",
                     "presale_rate", "listing_rate", "tokens_for_sale", "liquidity", "lp_lock",
                     "token_ca", "sale_address", "buy_link", "caller_post_link", "chain_emoji")

_TEMPLATE_MAP = {
    ("cp", "channel"): ("cp_channel_template", CP_CHANNEL_VARS,
                         "Channel post (main WizardScan channel). Must contain exactly "
                         f"{CP_CHANNEL_TEMPLATE_MARKERS} 🔮 marker emojis for premium "
                         "emoji placement to line up correctly."),
    ("cp", "details"): ("cp_details_template", CP_DETAILS_VARS,
                         "Details page shown inside the bot. Use "
                         '<code>&lt;tg-emoji emoji-id="ID"&gt;fallback&lt;/tg-emoji&gt;</code> '
                         "anywhere you want a premium emoji — no marker-count limit."),
    ("ps", "channel"): ("ps_channel_template", PS_CHANNEL_VARS,
                         "Channel post (main WizardScan channel). Must contain exactly "
                         f"{PS_CHANNEL_TEMPLATE_MARKERS} 🔮 marker emojis for premium "
                         "emoji placement to line up correctly."),
    ("ps", "details"): ("ps_details_template", PS_DETAILS_VARS,
                         "Details page shown inside the bot. Use "
                         '<code>&lt;tg-emoji emoji-id="ID"&gt;fallback&lt;/tg-emoji&gt;</code> '
                         "anywhere you want a premium emoji — no marker-count limit."),
}

async def _tpl_set(update, context, platform):
    """Shared handler: /set{platform}template <channel|details> <template text...>"""
    label = "CheesePad" if platform == "cp" else "PinkSale"
    if len(context.args) < 2:
        await update.message.reply_text(
            f"Usage: <code>/set{platform}template &lt;channel|details&gt; &lt;template text&gt;</code>\n\n"
            f"See /{platform}templatevars for the placeholders you can use.",
            parse_mode="HTML"); return
    which = context.args[0].lower().strip()
    if which not in ("channel", "details"):
        await update.message.reply_text("⚠️ First argument must be 'channel' or 'details'."); return
    tpl_text = update.message.text.split(None, 2)[2]
    cfg_key, allowed_vars, note = _TEMPLATE_MAP[(platform, which)]
    if which == "channel":
        # Owner ki apni premium emojis ([[emoji:ID]] / <tg-emoji>) marker count
        # me nahi ginti — wo apni ID khud carry karti hain.
        marker_count = _EMOJI_MARKER_RE.sub("", _TG_EMOJI_TAG_RE.sub("", tpl_text)).count("🔮")
        need = CP_CHANNEL_TEMPLATE_MARKERS if platform == "cp" else PS_CHANNEL_TEMPLATE_MARKERS
        if marker_count != need:
            await update.message.reply_text(
                f"⚠️ This template has {marker_count} 🔮 markers — it needs exactly {need} "
                f"for premium emojis to line up correctly. Please adjust and resend.\n\n{note}",
                parse_mode="HTML"); return
    # Sanity-check the template renders without crashing (empty test values).
    test_vals = {v: "test" for v in allowed_vars}
    try:
        tpl_text.format_map(_SafeFmtDict(test_vals))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Template has a formatting error: {e}\n\nNot saved — please fix and resend."); return
    cfg_set(cfg_key, tpl_text)
    await update.message.reply_text(
        f"✅ <b>{label} {which} template saved.</b>\n\nTest it with /{platform}test or /{platform}call.",
        parse_mode="HTML")

async def _tpl_view(update, context, platform):
    label = "CheesePad" if platform == "cp" else "PinkSale"
    which = (context.args[0].lower().strip() if context.args else "channel")
    if which not in ("channel", "details"):
        await update.message.reply_text("Usage: /{}template <channel|details>".format(platform)); return
    cfg_key, allowed_vars, note = _TEMPLATE_MAP[(platform, which)]
    current = cfg_get(cfg_key, "")
    var_list = ", ".join("{" + v + "}" for v in allowed_vars)
    if current:
        await update.message.reply_text(
            f"📄 <b>{label} {which} template (custom, active):</b>\n\n<code>{html.escape(current)}</code>\n\n"
            f"<b>Available placeholders:</b>\n{html.escape(var_list)}\n\n{note}",
            parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"📄 <b>{label} {which} template:</b> using built-in default (no custom template set).\n\n"
            f"<b>Available placeholders:</b>\n{html.escape(var_list)}\n\n{note}\n\n"
            f"Use <code>/set{platform}template {which} &lt;text&gt;</code> to customize.",
            parse_mode="HTML")

async def _tpl_reset(update, context, platform):
    label = "CheesePad" if platform == "cp" else "PinkSale"
    if not context.args or context.args[0].lower() not in ("channel", "details"):
        await update.message.reply_text(f"Usage: /reset{platform}template <channel|details>"); return
    which = context.args[0].lower().strip()
    cfg_key, _, _ = _TEMPLATE_MAP[(platform, which)]
    c = load_config()
    if cfg_key in c:
        del c[cfg_key]; save_config(c)
    await update.message.reply_text(f"✅ {label} {which} template reset to built-in default.")

@owner_only
async def cmd_setcptemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_set(update, context, "cp")
@owner_only
async def cmd_cptemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_view(update, context, "cp")
@owner_only
async def cmd_resetcptemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_reset(update, context, "cp")
@owner_only
async def cmd_setpstemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_set(update, context, "ps")
@owner_only
async def cmd_pstemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_view(update, context, "ps")
@owner_only
async def cmd_resetpstemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_reset(update, context, "ps")

# ── Template placeholders + custom premium emoji help ────────────────────────
async def _tpl_vars(update, context, platform):
    label = "CheesePad" if platform == "cp" else "PinkSale"
    ch_key, ch_vars, _ = _TEMPLATE_MAP[(platform, "channel")]
    dt_key, dt_vars, _ = _TEMPLATE_MAP[(platform, "details")]
    need = CP_CHANNEL_TEMPLATE_MARKERS if platform == "cp" else PS_CHANNEL_TEMPLATE_MARKERS
    txt = (
        f"🧩 <b>{label} TEMPLATE EDITOR</b>\n\n"
        f"<b>1) Channel post (bahar wala — main channel):</b>\n"
        f"<code>/set{platform}template channel &lt;text&gt;</code>\n"
        f"Placeholders: {html.escape(', '.join('{' + v + '}' for v in ch_vars))}\n"
        f"Isme exactly <b>{need}</b> 🔮 markers hone chahiye (built-in premium emojis "
        f"unhi par lagti hain).\n\n"
        f"<b>2) Details page (bot ke andar wala):</b>\n"
        f"<code>/set{platform}template details &lt;text&gt;</code>\n"
        f"Placeholders: {html.escape(', '.join('{' + v + '}' for v in dt_vars))}\n"
        f"Yahan 🔮 markers ki koi limit nahi.\n\n"
        f"<b>3) Apni premium emojis (koi bhi jagah, dono templates me):</b>\n"
        f"<code>[[emoji:5773941882832822049]]</code>\n"
        f"Ya phir: <code>&lt;tg-emoji emoji-id=\"ID\"&gt;🔮&lt;/tg-emoji&gt;</code>\n"
        f"Ye apni ID khud carry karti hain — ye 🔮 marker count me nahi ginti, "
        f"is liye baaki emojis ki alignment kabhi kharab nahi hoti.\n"
        f"ID nikalne ke liye: /getemoji (premium emoji bhejo) ya /premiumguide.\n\n"
        f"<b>4) Dekho / reset karo:</b>\n"
        f"<code>/{platform}template channel</code> · <code>/{platform}template details</code>\n"
        f"<code>/reset{platform}template channel|details</code>\n\n"
        f"<b>5) Test:</b> <code>/{platform}test &lt;link&gt;</code> (DM preview) · "
        f"<code>/{platform}call &lt;link&gt; @caller</code> (channel par post)"
    )
    await update.message.reply_text(txt, parse_mode="HTML", disable_web_page_preview=True)

@owner_only
async def cmd_cptemplatevars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_vars(update, context, "cp")

@owner_only
async def cmd_pstemplatevars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _tpl_vars(update, context, "ps")

@owner_only
async def cmd_templatehelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧩 <b>TEMPLATE COMMANDS</b>\n\n"
        "🧀 CheesePad: /cptemplatevars — poori guide\n"
        "   <code>/setcptemplate channel|details &lt;text&gt;</code>\n"
        "   <code>/cptemplate channel|details</code> · <code>/resetcptemplate channel|details</code>\n\n"
        "🩷 PinkSale: /pstemplatevars — poori guide\n"
        "   <code>/setpstemplate channel|details &lt;text&gt;</code>\n"
        "   <code>/pstemplate channel|details</code> · <code>/resetpstemplate channel|details</code>\n\n"
        "✨ Apni premium emoji kahin bhi: <code>[[emoji:ID]]</code>\n"
        "🆔 ID lene ke liye: /getemoji",
        parse_mode="HTML")

@owner_only
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /addchannel username"); return
    ch = context.args[0].lstrip("@"); channels = load_channels()
    if ch.lower() in [c.lower() for c in channels]:
        await update.message.reply_text(f"@{ch} already tracked."); return
    unmark_channel_removed(ch)          # blocklist se nikaalo (dobara add ho raha hai)
    channels.append(ch); save_channels(channels)
    await update.message.reply_text(f"✅ @{ch} added to tracking. Scanning existing posts to skip old calls...")
    # Pre-populate seen_message_ids so bot only tracks NEW posts from now on
    try:
        posts = await fetch_channel_posts(ch)
        for post in posts:
            seen_message_ids[ch.lower()].add(str(post["id"]))
        _save_seen()
        await update.message.reply_text(f"✅ {len(posts)} existing posts marked as seen. Only new posts will be tracked.")
    except Exception as e:
        logger.warning(f"addchannel pre-scan failed for @{ch}: {e}")

    # FIX: Auto-register new channel with realtime Telethon monitoring
    # Without this, the new channel is only polled every 15s instead of getting instant alerts
    global userbot_client
    if userbot_client and userbot_client.is_connected():
        try:
            from telethon import events
            ent = await userbot_client.get_entity(ch)
            # Add to existing realtime handler by re-registering with updated chats list
            # The cleanest way is to add the entity to userbot and re-call setup
            try:
                await userbot_client.get_input_entity(ch)
            except Exception:
                pass
            # Re-run setup to include the new channel in the realtime handler
            await setup_realtime_monitoring(context.bot)
            await update.message.reply_text(
                f"✅ @{ch} realtime monitoring mein register ho gaya (instant tracking active).",
                parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Realtime re-register for @{ch} failed: {e}")
            await update.message.reply_text(
                f"⚠️ Realtime monitoring update fail: {e}\n"
                f"/joinkols run karo taake realtime tracking start ho.",
                parse_mode="HTML")

@owner_only
async def cmd_special(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage channels scanned on every polling tick."""
    args = [str(a).strip() for a in context.args]
    current = load_special_channels()
    if not args or args[0].lower() == "list":
        rows = "\n".join(f"• @{ch}" for ch in current) or "<i>List empty hai.</i>"
        await update.message.reply_text(
            "⚡ <b>Special Priority Channels</b>\n\n" + rows +
            "\n\nAdd: <code>/special add @channel</code>\n"
            "Remove: <code>/special remove @channel</code>", parse_mode="HTML")
        return
    if len(args) < 2 or args[0].lower() not in ("add", "remove"):
        await update.message.reply_text(
            "Use: <code>/special add @channel</code> ya "
            "<code>/special remove @channel</code>", parse_mode="HTML")
        return
    action = args[0].lower()
    channel = args[1].lstrip("@").lower()
    if action == "add":
        tracked = {str(ch).lstrip("@").lower() for ch in load_channels()}
        if channel not in tracked:
            await update.message.reply_text(
                f"⚠️ Pehle <code>/addchannel @{channel}</code> run karein.", parse_mode="HTML")
            return
        if channel not in current:
            current.append(channel)
            save_special_channels(current)
        await update.message.reply_text(
            f"✅ @{channel} special priority mein hai. Har scan tick par check hoga.")
    else:
        save_special_channels([ch for ch in current if ch != channel])
        await update.message.reply_text(f"✅ @{channel} special priority se remove ho gaya.")

@owner_only
async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /removechannel username"); return
    ch_input = context.args[0].lstrip("@"); channels = load_channels()
    # Case-insensitive lookup — stored name kaisa bhi ho
    ch = next((c for c in channels if c.lower() == ch_input.lower()), None)
    if ch is None:
        # Already removed? Phir bhi blocklist pakka karo.
        mark_channel_removed(ch_input)
        await update.message.reply_text(
            f"@{ch_input} tracked list me nahi tha — blocklist me daal diya. Iski koi post nahi hogi.")
        return
    channels.remove(ch); save_channels(channels)
    mark_channel_removed(ch)

    ch_lower = ch.lower()

    # Collect all tracked calls for this channel BEFORE removing them
    bl = load_trending_blacklist()
    blocked_cas = []
    removed_call_keys = []
    for call_key, call in list(tracked_calls.items()):
        if call.get("channel", "").lower() != ch_lower: continue
        removed_call_keys.append(call_key)
        ca = call.get("ca", "")
        if ca and ca.lower() not in bl:
            bl.add(ca.lower())
            blocked_cas.append(ca)

    # Remove tracked calls + milestones from memory so monitoring_job stops alerting
    for call_key in removed_call_keys:
        tracked_calls.pop(call_key, None)
        sent_milestones.pop(call_key, None)
        milestone_posts.pop(call_key, None)
    if removed_call_keys:
        _save_tracked()
        _save_milestones()
        _save_milestone_posts()

    # NOTE: seen IDs ko DELETE nahi karte. Pehle delete karte the, is wajah se
    # channel ki saari purani posts dobara "nayi" lagti thin aur bot flood kar
    # deta tha. Ab woh saari IDs seen hi rehti hain (aur channel blocklist me
    # bhi hai), is liye ek bhi purani post kabhi post nahi hogi.
    _save_seen()

    # Pending (media ka intezaar karti hui) alerts bhi is channel ke liye hata do
    try:
        for _pk in list(pending_media_alerts.keys()):
            if str(_pk).split("_", 1)[0].lower() == ch_lower:
                pending_media_alerts.pop(_pk, None)
        _save_pending_media()
    except Exception as _e_pm:
        logger.warning(f"removechannel pending clear: {_e_pm}")

    if blocked_cas:
        save_trending_blacklist(bl)

    # Build reply message
    parts = [f"✅ @{ch} removed — permanently blocked.",
             "🚫 Is channel ki ab koi post nahi hogi (na purani, na nayi). "
             "Dobara chalu karna ho to <code>/addchannel</code> karein."]
    if removed_call_keys:
        parts.append(f"🗑 {len(removed_call_keys)} tracked call(s) delete ho gayi — ab koi alert nahi aayega.")
    if blocked_cas:
        parts.append(
            f"⛔ {len(blocked_cas)} token(s) trending se bhi block kar diye gaye:\n"
            + "\n".join(f"  • <code>{ca}</code>" for ca in blocked_cas[:5])
            + ("\n  ..." if len(blocked_cas) > 5 else "")
        )
    await update.message.reply_text("\n\n".join(parts), parse_mode="HTML")

@owner_only
async def cmd_mychannels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = load_channels()
    if not channels: await update.message.reply_text("No channels tracked."); return
    await update.message.reply_text(
        "📡 <b>Tracked Channels:</b>\n\n" + "\n".join(f"{i+1}. @{c}" for i, c in enumerate(channels)),
        parse_mode="HTML")

@owner_only
async def cmd_givepoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Give (or take) points to/from a channel manually.
    Usage: /givepoints @channel 50
           /givepoints @channel -20  (to deduct)"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "📊 <b>Points Manually Dene Ka Tarika:</b>\n\n"
            "<code>/givepoints @channelname 50</code> — 50 points do\n"
            "<code>/givepoints @channelname -20</code> — 20 points kato\n\n"
            "100 points milne par channel Champion KOL list mein aa jata hai.",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip()
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ Amount number hona chahiye. Example: /givepoints @channel 50"); return
    new_total = await give_manual_points(channel, amount)
    action = f"+{amount}" if amount >= 0 else str(amount)
    await update.message.reply_text(
        f"✅ <b>Points Updated!</b>\n\n"
        f"Channel: @{channel}\n"
        f"Change: <b>{action} points</b>\n"
        f"New Total: <b>{new_total}/{POINTS_FOR_CHAMPION} points</b>\n\n"
        f"{'🏆 Champion KOL list mein aa gaya!' if new_total >= POINTS_FOR_CHAMPION else f'{POINTS_FOR_CHAMPION - new_total} aur points chahiye Champion list ke liye.'}",
        parse_mode="HTML")

@owner_only
async def cmd_checkpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check points for a channel or all channels.
    Usage: /checkpoints @channel  OR  /checkpoints (shows top 20)"""
    if context.args:
        channel = context.args[0].lstrip("@").strip()
        pts = get_channel_points(channel)
        pts_data = load_channel_points()
        entry = pts_data.get(channel.lower(), {})
        awarded_calls = len(entry.get("awarded_tiers", {}))
        deducted_calls = len(entry.get("deducted_calls", []))
        await update.message.reply_text(
            f"📊 <b>Points for @{channel}</b>\n\n"
            f"Total Points: <b>{pts}/{POINTS_FOR_CHAMPION}</b>\n"
            f"Calls with points: {awarded_calls}\n"
            f"Failed calls (deducted): {deducted_calls}\n\n"
            f"{'✅ Champion KOL list mein hai!' if pts >= POINTS_FOR_CHAMPION else f'❌ {POINTS_FOR_CHAMPION - pts} aur points chahiye.'}",
            parse_mode="HTML")
    else:
        pts_data = load_channel_points()
        if not pts_data:
            await update.message.reply_text("📊 Kisi channel ke abhi points nahi hain."); return
        sorted_pts = sorted(
            [(ch, d.get("points", 0)) for ch, d in pts_data.items()],
            key=lambda x: x[1], reverse=True
        )[:20]
        lines = [f"📊 <b>Channel Points (Top 20)</b>\n"]
        for i, (ch, pts) in enumerate(sorted_pts, 1):
            star = "🏆" if pts >= POINTS_FOR_CHAMPION else "  "
            lines.append(f"{star} {i}. @{ch} — <b>{pts}/{POINTS_FOR_CHAMPION}</b>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_zerocolpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zero out all points for a specific KOL channel.
    Usage: /zerocolpoints @channel"""
    if not context.args:
        await update.message.reply_text(
            "📊 <b>KOL Points Zero Karne Ka Tarika:</b>\n\n"
            "<code>/zerocolpoints @channelname</code>\n\n"
            "This will reset that channel's points, awarded tiers, and deduction history to zero.",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip()
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        save_channel_points(pts_data)
    await update.message.reply_text(
        f"✅ <b>Points Reset!</b>\n\n"
        f"@{channel} ke sab points zero kar diye gaye.\n"
        f"Awarded tiers aur deduction history bhi clear ho gayi.",
        parse_mode="HTML")

@owner_only
async def cmd_resetallpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zero out ALL KOL channels' points at once.
    /resetallpoints          — confirmation maango
    /resetallpoints confirm  — sab points zero karo
    """
    if not context.args or context.args[0].lower() != "confirm":
        pts_data = load_channel_points()
        total_channels = len([ch for ch, d in pts_data.items() if d.get("points", 0) > 0])
        await update.message.reply_text(
            "⚠️ <b>Sab Channels ke Points Reset</b>\n\n"
            f"Abhi <b>{total_channels}</b> channel(s) ke points hain.\n\n"
            "Yeh command <b>SARE</b> KOL channels ke points, awarded tiers, "
            "aur deduction history ek saath zero kar deta hai.\n\n"
            "Confirm karne ke liye:\n<code>/resetallpoints confirm</code>",
            parse_mode="HTML"
        )
        return

    async with _points_lock:
        save_channel_points({})
    await update.message.reply_text(
        "✅ <b>Sab KOL Points Reset!</b>\n\n"
        "Har channel ke points, awarded tiers, aur deduction history zero ho gayi.\n\n"
        "Champion/Leaderboard lists ab refresh hongi — "
        "channels dobara points earn karke wapas ayenge.",
        parse_mode="HTML"
    )


@admin_only
async def cmd_freezecall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Freeze a call. Usage: /freezecall CA
    Optionally: /freezecall @channel CA  (freeze only that channel's call)

    /freezecall CA (no @channel) does TWO things:
      1. Freezes every currently-tracked entry for that CA (stops their
         milestone alerts) — same as before.
      2. Adds the CA to the permanent global ban list, so if ANY caller
         (existing or new) posts this CA again later, _process_new_call
         refuses it before it's ever tracked. This is what makes freeze
         actually stick across multiple callers.
    /freezecall @channel CA stays scoped to that one channel's tracked
    entry only and does NOT touch the global ban list."""
    if not context.args:
        await update.message.reply_text(
            "⛔ <b>Call Freeze</b>\n\n"
            "Usage:\n"
            "<code>/freezecall CA</code> — freezes for ALL callers\n"
            "<code>/freezecall @channel CA</code> — freezes only that channel\n\n"
            "Ye command ek suspicious call ke sab future milestone alerts rokh degi.",
            parse_mode="HTML"); return

    if len(context.args) >= 2 and context.args[0].startswith("@"):
        target_channel = context.args[0].lstrip("@").lower()
        ca = context.args[1].strip()
    else:
        target_channel = None
        ca = context.args[0].strip()

    frozen_keys = []
    for call_key, call in tracked_calls.items():
        if call.get("ca", "").lower() != ca.lower(): continue
        if target_channel and call.get("channel", "").lower() != target_channel: continue
        call["frozen"] = True
        frozen_keys.append(f"@{call.get('channel')} — {call.get('symbol','?')}")

    if frozen_keys:
        _save_tracked()

    global_note = ""
    if not target_channel:
        bans = load_global_ca_bans()
        bans.add(ca.lower())
        save_global_ca_bans(bans)
        global_note = "\n🌐 CA globally banned — koi bhi caller ye CA dobara post kare, bot use skip kar dega."

    if not frozen_keys and not global_note:
        await update.message.reply_text(
            f"❌ CA <code>{ca}</code> abhi tracked calls mein nahi mila.\n"
            "Pehle /addmissedcall se add karo, ya CA check karo.",
            parse_mode="HTML"); return

    lines = "\n".join(f"  • {k}" for k in frozen_keys) if frozen_keys else "  (koi tracked entry nahi mila abhi)"
    msg = await update.message.reply_text(
        f"⛔ <b>Call Frozen!</b>\n\n{lines}{global_note}\n\n"
        f"Unfreeze karne ke liye: <code>/unfreezecall {ca}</code>",
        parse_mode="HTML")

    # Freezing must also retroactively pull this CA's X out of Champions/
    # Leaderboard (champion_total_x / _calc_leaderboard_scores now skip
    # frozen calls) — refresh both posts immediately instead of waiting for
    # the next scheduled job, so the channel's 100x etc. disappears right away.
    if frozen_keys:
        refresh_notes = []
        try:
            if await _update_champions_with_premium_emojis(context.bot):
                cfg_set("last_champions_update", datetime.utcnow().isoformat())
                refresh_notes.append("Champions (134) ✅")
            else:
                refresh_notes.append("Champions (134) ⚠️")
        except Exception as e:
            logger.warning(f"freezecall: champions refresh failed: {e}")
            refresh_notes.append("Champions (134) ⚠️")
        try:
            if await _update_leaderboard_with_premium_emojis(context.bot):
                cfg_set("last_leaderboard_update", datetime.utcnow().isoformat())
                refresh_notes.append("Leaderboard (136) ✅")
            else:
                refresh_notes.append("Leaderboard (136) ⚠️")
        except Exception as e:
            logger.warning(f"freezecall: leaderboard refresh failed: {e}")
            refresh_notes.append("Leaderboard (136) ⚠️")
        try:
            await msg.edit_text(
                f"⛔ <b>Call Frozen!</b>\n\n{lines}{global_note}\n\n"
                f"Unfreeze karne ke liye: <code>/unfreezecall {ca}</code>\n\n"
                f"🔄 " + " | ".join(refresh_notes),
                parse_mode="HTML")
        except Exception:
            pass


@admin_only
async def cmd_unfreezecall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unfreeze a previously frozen call — resume milestone alerts and,
    for a CA-wide (no @channel) unfreeze, remove it from the global ban list.
    Usage: /unfreezecall CA"""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/unfreezecall CA</code>",
            parse_mode="HTML"); return

    if len(context.args) >= 2 and context.args[0].startswith("@"):
        target_channel = context.args[0].lstrip("@").lower()
        ca = context.args[1].strip()
    else:
        target_channel = None
        ca = context.args[0].strip()

    unfrozen_keys = []
    for call_key, call in tracked_calls.items():
        if call.get("ca", "").lower() != ca.lower(): continue
        if target_channel and call.get("channel", "").lower() != target_channel: continue
        if call.get("frozen"):
            call.pop("frozen", None)
            # A previous version could classify an external-source quote with
            # zero liquidity as a liquidity pull and persist `rugged=True`.
            # Clearing the complete stop state makes /unfreezecall actually
            # recover those false positives; real rugs should not be unfrozen.
            call.pop("rugged", None)
            call.pop("rug_reason", None)
            call.pop("rugged_at", None)
            unfrozen_keys.append(f"@{call.get('channel')} — {call.get('symbol','?')}")

    if unfrozen_keys:
        _save_tracked()

    global_note = ""
    if not target_channel:
        bans = load_global_ca_bans()
        if ca.lower() in bans:
            bans.discard(ca.lower())
            save_global_ca_bans(bans)
            global_note = "\n🌐 CA global ban hata diya gaya — future callers ab dobara track honge."

    if not unfrozen_keys and not global_note:
        await update.message.reply_text(
            f"❌ <code>{ca}</code> ya toh frozen nahi tha, ya tracked nahi mila.",
            parse_mode="HTML"); return

    lines = "\n".join(f"  • {k}" for k in unfrozen_keys) if unfrozen_keys else "  (koi tracked entry frozen nahi tha)"
    msg = await update.message.reply_text(
        f"✅ <b>Call Unfrozen!</b>\n\n{lines}{global_note}",
        parse_mode="HTML")

    # Mirror of the freeze-side refresh: unfreezing brings the call's X back
    # into Champions/Leaderboard, so refresh both posts immediately too.
    if unfrozen_keys:
        refresh_notes = []
        try:
            if await _update_champions_with_premium_emojis(context.bot):
                cfg_set("last_champions_update", datetime.utcnow().isoformat())
                refresh_notes.append("Champions (134) ✅")
            else:
                refresh_notes.append("Champions (134) ⚠️")
        except Exception as e:
            logger.warning(f"unfreezecall: champions refresh failed: {e}")
            refresh_notes.append("Champions (134) ⚠️")
        try:
            if await _update_leaderboard_with_premium_emojis(context.bot):
                cfg_set("last_leaderboard_update", datetime.utcnow().isoformat())
                refresh_notes.append("Leaderboard (136) ✅")
            else:
                refresh_notes.append("Leaderboard (136) ⚠️")
        except Exception as e:
            logger.warning(f"unfreezecall: leaderboard refresh failed: {e}")
            refresh_notes.append("Leaderboard (136) ⚠️")
        try:
            await msg.edit_text(
                f"✅ <b>Call Unfrozen!</b>\n\n{lines}{global_note}\n\n"
                f"🔄 " + " | ".join(refresh_notes),
                parse_mode="HTML")
        except Exception:
            pass


@owner_only
async def cmd_addmissedcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually add a call that the bot missed tracking.
    Usage: /addmissedcall @channel CA x_achieved [entry_mc] [token_name] [kol_tg_link] [chain]
    Example: /addmissedcall @SomeKOL So1ABC...xyz 100
             /addmissedcall @SomeKOL So1ABC...xyz 100 5K PEPE https://t.me/SomeKOL/123 RH
    chain = SOL / ETH / BNB / BASE / RH / TON (optional — auto-detected if not given)
    NOTE: owner ke commands se chalne par bot hamesha step-by-step wizard kholta hai —
    ek hi msg me saari details likhne ki zaroorat nahi. Wizard andar se isi
    function ko poore args ke saath call karta hai."""
    if len(context.args) < 8:
        await start_missedcall_wizard(update, context); return
    channel = context.args[0].lstrip("@").strip()
    ca = context.args[1].strip()
    _x_raw = context.args[2].strip().lower().rstrip("x")
    auto_x = _x_raw in ("auto", "a", "0", "")
    x_achieved = 0
    if not auto_x:
        try:
            x_achieved = float(_x_raw)
        except ValueError:
            await update.message.reply_text(
                "⚠️ x_achieved number ya <code>auto</code> hona chahiye. Example: 100, 1, 0.5 ya auto",
                parse_mode="HTML"); return
        # OWNER FIX: pehle yahan hard gate tha "x_achieved kam se kam 2 hona
        # chahiye" — is wajah se flat calls (MC kabhi move hi nahi hua, e.g.
        # 4K → 4K = 1x) ya dropped/rugged calls (0.5x) ko fix karna namumkin
        # tha, sirf ≥2x wali calls hi manually add ho sakti thin. Ab koi bhi
        # outcome record kiya ja sakta hai — flat (1), rug (<1), ya pump
        # (≥2) — bot sirf ≥2 par hi milestone alert bhejega (neeche
        # "alert_x_val >= 2" check), lekin call ab sahi entry/outcome ke
        # saath tracked/archived zaroor ho jayegi.
        if x_achieved <= 0:
            await update.message.reply_text(
                "⚠️ x_achieved 0 se zyada hona chahiye (1 = flat/no move, 0.5 = 50% neeche, ya <code>auto</code>).",
                parse_mode="HTML"); return

    # Optional 4th argument: manual entry_mc override (for pump.fun / dead tokens)
    manual_entry_mc = 0.0
    if len(context.args) >= 4:
        manual_entry_mc = parse_mc_string(context.args[3])

    # Optional 5th argument: token name / symbol override
    manual_symbol = ""
    if len(context.args) >= 5:
        manual_symbol = context.args[4].strip().upper()
        if manual_symbol in ("AUTO", "-", ""):
            manual_symbol = ""

    # Optional 6th argument: KOL Telegram post link override
    manual_kol_tg_link = ""
    if len(context.args) >= 6:
        manual_kol_tg_link = context.args[5].strip()
        if manual_kol_tg_link.lower() in ("auto", "-", "skip", ""):
            manual_kol_tg_link = ""

    # Optional 7th argument: chain override — FIX for wrong chain/emoji bug
    # e.g. RH, SOL, ETH, BNB, BASE, TON
    manual_chain_arg = ""
    _valid_chains = {"SOL", "ETH", "BNB", "BSC", "BASE", "RH", "TON", "EVM"}
    if len(context.args) >= 7:
        _chain_inp = context.args[6].strip().upper()
        if _chain_inp in ("AUTO", "-", ""):
            manual_chain_arg = ""
        elif _chain_inp in _valid_chains:
            manual_chain_arg = _chain_inp
        else:
            await update.message.reply_text(
                f"⚠️ Invalid chain: <b>{_chain_inp}</b>\n\n"
                f"Valid chains: SOL, ETH, BNB, BASE, RH, TON\n\n"
                f"Example: <code>/addmissedcall @channel CA 50 5K TOKEN link RH</code>",
                parse_mode="HTML"); return

    manual_ath_mc = parse_mc_string(context.args[7])
    if manual_entry_mc <= 0 or manual_ath_mc <= 0:
        await update.message.reply_text("⚠️ Call MC aur ATH MC dono valid honay chahiye."); return
    mc_ratio = manual_ath_mc / manual_entry_mc
    if abs(mc_ratio - x_achieved) / max(x_achieved, 1) > 0.15:
        await update.message.reply_text(
            f"⚠️ Values match nahi kartin: {fmt_mc(manual_entry_mc)} → {fmt_mc(manual_ath_mc)} "
            f"takriban {fmt_x(mc_ratio)} banta hai, {fmt_x(x_achieved)} nahi. "
            "Call MC, ATH MC ya exact X dobara check karein."); return

    # FIX: Auto-detect chain from CA pattern as a best-guess fallback
    def _detect_chain_from_ca(addr: str) -> str:
        """Guess chain from contract address format."""
        if addr.startswith("0x") and len(addr) in (42, 66):
            return "ETH"   # EVM address — ETH/BNB/BASE/RH; DexScreener will narrow it down
        if re.match(r'^(EQ|UQ)[A-Za-z0-9_-]{46}$', addr):
            return "TON"
        # Solana: base58, 32–44 chars, no 0x prefix
        return "SOL"

    channels = load_channels()
    if channel.lower() not in [c.lower() for c in channels]:
        await update.message.reply_text(
            f"⚠️ @{channel} tracked channels mein nahi hai.\n"
            f"Pehle /addchannel {channel} karo."); return

    msg = await update.message.reply_text(f"⏳ DexScreener se {ca[:12]}... ka data fetch ho raha hai...")
    dex = await fetch_dexscreener(ca)
    call_key = f"{channel}_{ca}"

    if dex and dex.get("mcap"):
        cur_mc_val = dex["mcap"]
        # FIX: agar owner ne asli call MC di hai to WOHI entry hai. Pehle bot
        # entry ko live_mc / x se back-calculate karta tha, is liye X galat
        # nikalta tha (entry 453.2K, live 1.8M → bot 2x kehta tha, asal 4x).
        if manual_entry_mc > 0:
            entry_mc = manual_entry_mc
        else:
            entry_mc = cur_mc_val / x_achieved  # x_achieved already validated > 0 above
        entry_fmt = fmt_mc(entry_mc)
        cur_fmt = fmt_mc(cur_mc_val)
        chain_val = manual_chain_arg or dex["chain"]
        symbol_val = manual_symbol or dex.get("symbol", "UNKNOWN")
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": entry_mc,
                "entry_price": (
                    (dex.get("price", 0) * (entry_mc / cur_mc_val))
                    if (dex.get("price") and cur_mc_val > 0 and entry_mc > 0)
                    else (dex.get("price", 0) / x_achieved if dex.get("price") else 0)
                ),
                "entry_src": "owner" if manual_entry_mc > 0 else "auto",
                "entry_locked": manual_entry_mc > 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
                **({"kol_tg_link": manual_kol_tg_link} if manual_kol_tg_link else {}),
            }
            _save_tracked()
        else:
            # Update symbol/link even if call was already tracked
            if manual_symbol:   tracked_calls[call_key]["symbol"] = manual_symbol
            if manual_kol_tg_link: tracked_calls[call_key]["kol_tg_link"] = manual_kol_tg_link
            _save_tracked()
    elif manual_entry_mc > 0:
        # Manual entry MC provided — calculate values without DexScreener
        entry_mc  = manual_entry_mc
        entry_fmt = fmt_mc(entry_mc)
        cur_mc_val = entry_mc * x_achieved
        cur_fmt   = fmt_mc(cur_mc_val)
        # FIX: Use manual chain arg first, then auto-detect from CA pattern
        chain_val = manual_chain_arg or _detect_chain_from_ca(ca)
        symbol_val = manual_symbol or "UNKNOWN"
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": entry_mc, "entry_price": 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
                **({"kol_tg_link": manual_kol_tg_link} if manual_kol_tg_link else {}),
            }
            _save_tracked()
        else:
            if manual_chain_arg: tracked_calls[call_key]["chain"] = chain_val
            if manual_symbol:    tracked_calls[call_key]["symbol"] = manual_symbol
            if manual_kol_tg_link: tracked_calls[call_key]["kol_tg_link"] = manual_kol_tg_link
            _save_tracked()
    else:
        # No DexScreener data and no manual MC — warn user
        entry_mc  = 0; entry_fmt = "N/A"; cur_fmt = "N/A"
        # FIX: Use manual chain arg first, then auto-detect from CA pattern
        chain_val = manual_chain_arg or _detect_chain_from_ca(ca)
        symbol_val = manual_symbol or "UNKNOWN"
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": 0, "entry_price": 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
                **({"kol_tg_link": manual_kol_tg_link} if manual_kol_tg_link else {}),
            }
            _save_tracked()
        else:
            if manual_chain_arg: tracked_calls[call_key]["chain"] = chain_val
            if manual_symbol:    tracked_calls[call_key]["symbol"] = manual_symbol
            if manual_kol_tg_link: tracked_calls[call_key]["kol_tg_link"] = manual_kol_tg_link
            _save_tracked()

    # ── AUTO-X + AUTO-ATH ────────────────────────────────────────────────
    # Owner sirf call MC de — bot khud live MC aur asli candle ATH dekh kar
    # sahi X nikaalta hai, peak store karta hai aur usi X ka alert karta hai.
    call_ref = tracked_calls[call_key]
    if manual_entry_mc > 0:
        call_ref["entry_mc"]     = manual_entry_mc
        call_ref["entry_fmt"]    = fmt_mc(manual_entry_mc)
        call_ref["entry_src"]    = "owner"
        call_ref["entry_locked"] = True
        entry_mc, entry_fmt = manual_entry_mc, call_ref["entry_fmt"]
    if manual_chain_arg:
        call_ref["chain"] = manual_chain_arg
        chain_val = manual_chain_arg

    # Owner values are authoritative for repaired calls; live data is only used
    # later to continue tracking future milestones.
    true_ratio = float(x_achieved)
    peak_mc_val = float(manual_ath_mc)
    call_ref["last_ratio"] = round(true_ratio, 4)
    call_ref["owner_reported_x"] = round(true_ratio, 4)
    call_ref["owner_reported_ath_mc"] = peak_mc_val
    _update_peak(call_ref, true_ratio, peak_mc_val)
    _save_tracked()
    hit = milestones_for_ratio(true_ratio)
    alert_x_val = max(hit) if hit else 0
    auto_note = (f"\n\n✅ <b>Owner-confirmed:</b> {fmt_mc(entry_mc)} → "
                 f"{fmt_mc(peak_mc_val)} = <b>{fmt_x(true_ratio)}</b>; "
                 f"initial alert <b>{alert_x_val}X+</b>")

    # Mark all milestones up to x_achieved as hit
    milestones_to_mark = [m for m in get_milestones() if m <= true_ratio]
    newly_marked = []
    for ms in milestones_to_mark:
        if ms not in sent_milestones[call_key]:
            sent_milestones[call_key].add(ms)
            newly_marked.append(ms)
            await award_points_for_milestone(channel, call_key, ms)
    _save_milestones()

    # Post alert for the x_achieved milestone to main channel
    call_data = tracked_calls[call_key]
    # Strict ordering: dropped-call post must exist before the X update.
    await send_dropped_alert(
        context.bot, channel, call_data.get("msg_id", 0), ca,
        call_data.get("chain", chain_val), call_data.get("entry_fmt", "N/A"),
        call_data.get("symbol", "UNKNOWN"), respect_skip=False)
    if alert_x_val >= 2:
        await send_alert(
            context.bot, channel, call_data.get("msg_id", 0), alert_x_val,
            call_data.get("chain", chain_val), call_data.get("entry_fmt", "N/A"),
            fmt_mc(manual_ath_mc), ca, call_data.get("symbol", "UNKNOWN"))

    new_pts = get_channel_points(channel)
    dex_note = ""
    chain_warn = ""
    if not (dex and dex.get("mcap")) and manual_entry_mc <= 0:
        dex_note = "\n\n⚠️ DexScreener data nahi mila. Entry MC N/A hai.\nAgar entry MC pata ho toh yeh use karein:\n<code>/addmissedcall @{} {} {} 5K</code>".format(channel, ca, x_achieved)
        if not manual_chain_arg:
            chain_warn = f"\n\n💡 <b>Chain auto-detect: {chain_val}</b> — Agar chain galat lage (jaise RH token tha lekin SOL dikhaya) toh chain argument add karein:\n<code>/addmissedcall @{channel} {ca} {x_achieved} 5K TOKEN link RH</code>"
    elif not (dex and dex.get("mcap")) and manual_entry_mc > 0:
        dex_note = f"\n\n📌 Manual entry MC use kiya: {entry_fmt}"
        if not manual_chain_arg:
            chain_warn = f"\n\n💡 Chain auto-detect: <b>{chain_val}</b> — Galat lage toh add karein: <code>... {x_achieved} {fmt_mc(entry_mc)} {symbol_val} link {chain_val}</code>"
    await msg.edit_text(
        f"✅ <b>Missed Call Added!</b>\n\n"
        f"Channel: @{channel}\n"
        f"CA: <code>{ca}</code>\n"
        f"Chain: <b>{chain_val}</b>{'  ✅ (manual)' if manual_chain_arg else '  ⚡ (auto-detect)'}\n"
        f"Symbol: {symbol_val}\n"
        f"Call MC: {entry_fmt} → ATH MC: {fmt_mc(manual_ath_mc)}\n"
        f"Exact X: <b>{fmt_x(true_ratio)}</b> · Alert: <b>{alert_x_val}X+</b>{auto_note}\n"
        f"Milestones Marked: {len(newly_marked)}\n\n"
        f"📊 @{channel} ke ab <b>{new_pts}/{POINTS_FOR_CHAMPION} points</b> hain.{dex_note}{chain_warn}",
        parse_mode="HTML")

@owner_only
async def cmd_adjustcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually fix the entry MC of a tracked call (when bot tracked late).
    Usage: /adjustcall @channel CA entry_mc
    Example: /adjustcall @SomeKOL So1ABC123 100K
    This is useful when the channel called at 100K but bot tracked at 120K+."""
    if len(context.args) < 3:
        await update.message.reply_text(
            "📡 <b>Call Entry MC Fix Karne Ka Tarika:</b>\n\n"
            "<code>/adjustcall @channelname CONTRACT_ADDRESS entry_mc</code>\n\n"
            "Example:\n"
            "<code>/adjustcall @SomeKOL So1ABC123 100K</code>\n"
            "<code>/adjustcall @SomeKOL 0xABC... 50K</code>\n\n"
            "⚠️ Yeh tab use karo jab channel ne call 100K pe ki lekin bot ne 120K+ pe late track ki.\n"
            "entry_mc = woh actual MC jo channel ne call ki thi (e.g. 50K, 100K, 1M, 500000)",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    ca = context.args[1].strip()
    new_entry_mc = parse_mc_string(context.args[2])
    if new_entry_mc <= 0:
        await update.message.reply_text("⚠️ Valid entry MC dain. Example: 100K, 1M, 500000"); return

    call_key = f"{channel}_{ca}"
    if call_key not in tracked_calls:
        # Try case-insensitive search
        found_key = next((k for k in tracked_calls if k.lower() == call_key.lower()), None)
        if found_key:
            call_key = found_key
        else:
            # See /fixmc for why: a pruned/rugged call lives in the archive,
            # not tracked_calls, until restored — check there before giving up.
            archive = load_call_archive()
            arc_key = next((k for k in archive if k.lower() == call_key.lower()), None)
            if arc_key:
                call = dict(archive[arc_key])
                call.setdefault("msg_id", 0)
                call.setdefault("channel", channel)
                call.setdefault("ca", ca)
                tracked_calls[arc_key] = call
                archive.pop(arc_key, None)
                save_call_archive(archive)
                call_key = arc_key
                logger.info(f"/adjustcall restored {arc_key} from archive back into live tracking")
            else:
                await update.message.reply_text(
                    f"⚠️ Yeh call tracked nahi hai: @{channel} / <code>{ca}</code>\n\n"
                    f"Archive mein bhi nahi mili. Pehle <code>/addmissedcall @{channel} {ca} ...</code> "
                    f"se add karein (upar poora CA copy kar lein).", parse_mode="HTML"); return

    call = tracked_calls[call_key]
    old_entry_fmt = call.get("entry_fmt", "N/A")
    old_entry_mc  = call.get("entry_mc", 0)

    # Update entry MC and recalculate entry_fmt
    call["entry_mc"]  = new_entry_mc
    call["entry_fmt"] = fmt_mc(new_entry_mc)
    if call.get("entry_price", 0) > 0:
        # Also scale entry_price proportionally
        if old_entry_mc > 0:
            ratio = new_entry_mc / old_entry_mc
            call["entry_price"] = call["entry_price"] * ratio

    _save_tracked()
    await update.message.reply_text(
        f"✅ <b>Entry MC Updated!</b>\n\n"
        f"Channel: @{call.get('channel', channel)}\n"
        f"CA: <code>{ca}</code>\n"
        f"Symbol: {call.get('symbol','UNKNOWN')}\n"
        f"Old Entry MC: <b>{old_entry_fmt}</b>\n"
        f"New Entry MC: <b>{call['entry_fmt']}</b>\n\n"
        f"Ab se milestones is nayi entry MC se calculate honge.",
        parse_mode="HTML")

@owner_only
async def cmd_recheckx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Galat X alerts theek karo — asli ATH X dobara calculate karke missing
    X alerts post karta hai.

    /recheckx @channel CA   → sirf ek call
    /recheckx @channel      → us channel ki sab tracked calls (max 40)
    """
    if not context.args:
        await update.message.reply_text(
            "🔁 <b>X Alerts Recheck / Fix</b>\n\n"
            "<code>/recheckx @channel CA</code> — ek call ka asli X dobara nikaalo\n"
            "<code>/recheckx @channel</code> — us channel ki sab calls check karo\n\n"
            "Bot entry MC + live MC + real candle ATH dekh kar asli X nikaalta hai "
            "aur jo X alerts miss ho gaye the wo post kar deta hai.",
            parse_mode="HTML"); return

    channel = context.args[0].lstrip("@").strip().lower()
    ca_arg  = context.args[1].strip() if len(context.args) >= 2 else ""

    keys = []
    for k, v in tracked_calls.items():
        if (v.get("channel", "") or "").lower() != channel:
            continue
        if ca_arg and (v.get("ca", "") or "").lower() != ca_arg.lower():
            continue
        keys.append(k)
    if not keys:
        await update.message.reply_text(
            f"⚠️ Koi tracked call nahi mili: @{channel}"
            + (f" / {ca_arg[:16]}…" if ca_arg else "")); return
    keys = keys[:40]

    msg = await update.message.reply_text(f"⏳ {len(keys)} call(s) recheck ho rahi hain…")
    lines, posted_total = [], 0

    for call_key in keys:
        call = tracked_calls[call_key]
        ca   = call.get("ca", "")
        try:
            ratio, peak_mc = await compute_true_x(call)
        except Exception as e:
            lines.append(f"• {call.get('symbol','?')} — error: {type(e).__name__}")
            continue
        if ratio <= 0:
            lines.append(f"• {call.get('symbol','?')} — data N/A")
            continue
        call["last_ratio"] = round(ratio, 4)
        _update_peak(call, ratio, peak_mc)
        missing = [m for m in milestones_for_ratio(ratio) if m not in sent_milestones[call_key]]
        if not missing:
            lines.append(f"• {call.get('symbol','?')} — {fmt_x(ratio)} ✅ (already posted)")
            continue
        missing.sort()
        posted_here = 0
        for ms in missing:
            try:
                await award_points_for_milestone(call.get("channel", channel), call_key, ms)
            except Exception as e_p:
                logger.error(f"recheckx points: {e_p}")
            entry_mc = float(call.get("entry_mc", 0) or 0)
            ms_mc = peak_mc if (peak_mc > 0 and ms == missing[-1]) else (entry_mc * ms if entry_mc > 0 else 0)
            try:
                await send_alert(
                    context.bot, call.get("channel", channel), call.get("msg_id", 0), ms,
                    call.get("chain", "SOL"), call.get("entry_fmt", "N/A"),
                    fmt_mc(ms_mc) if ms_mc > 0 else "N/A", ca, call.get("symbol", "UNKNOWN"))
                sent_milestones[call_key].add(ms)
                posted_here += 1
            except Exception as e_a:
                logger.error(f"recheckx alert: {e_a}")
            await asyncio.sleep(1.2)
        posted_total += posted_here
        lines.append(f"• {call.get('symbol','?')} — {fmt_x(ratio)} → 📣 "
                      f"{', '.join(str(m)+'X' for m in missing)} posted")
    _save_milestones(); _save_tracked()

    body = "\n".join(lines[:40]) or "—"
    await msg.edit_text(
        f"🔁 <b>Recheck Complete</b>\n\nChannel: @{channel}\n"
        f"Checked: {len(keys)} | Naye alerts: <b>{posted_total}</b>\n\n{body}",
        parse_mode="HTML")


@owner_only
async def cmd_forcex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually ek X alert post karo (jab bot ne galat/kam X mention kiya ho).
    Usage: /forcex @channel CA x [mc]"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "🎯 <b>Force X Alert</b>\n\n"
            "<code>/forcex @channel CA x [peak_mc]</code>\n\n"
            "Example: <code>/forcex @KOL So1ABC 4 1.8M</code>\n\n"
            "Bot us X ka alert post karega aur milestone mark kar dega.",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    ca      = context.args[1].strip()
    try:
        x_val = int(float(context.args[2].lower().rstrip("x")))
    except ValueError:
        await update.message.reply_text("⚠️ X number hona chahiye. Example: 4"); return
    peak_mc = parse_mc_string(context.args[3]) if len(context.args) >= 4 else 0.0

    call_key = next((k for k, v in tracked_calls.items()
                     if (v.get("channel", "") or "").lower() == channel
                     and (v.get("ca", "") or "").lower() == ca.lower()), None)
    if not call_key:
        await update.message.reply_text(
            "⚠️ Yeh call tracked nahi hai. Pehle <code>/addmissedcall</code> karein.",
            parse_mode="HTML"); return
    call = tracked_calls[call_key]
    entry_mc = float(call.get("entry_mc", 0) or 0)
    if peak_mc <= 0 and entry_mc > 0:
        peak_mc = entry_mc * x_val
    for ms in get_milestones():
        if ms <= x_val and ms not in sent_milestones[call_key]:
            sent_milestones[call_key].add(ms)
            try: await award_points_for_milestone(call.get("channel", channel), call_key, ms)
            except Exception: pass
    if entry_mc > 0:
        _update_peak(call, x_val, peak_mc)
        call["last_ratio"] = float(x_val)
    _save_milestones(); _save_tracked()
    await send_alert(
        context.bot, call.get("channel", channel), call.get("msg_id", 0), x_val,
        call.get("chain", "SOL"), call.get("entry_fmt", "N/A"),
        fmt_mc(peak_mc) if peak_mc > 0 else "N/A", ca, call.get("symbol", "UNKNOWN"))
    await update.message.reply_text(
        f"✅ <b>{x_val}X alert posted</b>\n\n@{channel} — {call.get('symbol','?')}\n"
        f"Entry: {call.get('entry_fmt','N/A')} → {fmt_mc(peak_mc) if peak_mc > 0 else 'N/A'}",
        parse_mode="HTML")


@owner_only
async def cmd_fixmc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fix a LATE-tracked call: set the real entry MC the KOL called at, edit the
    already-posted 'Dropped a Call' alert, and continue tracking from that MC.

    Usage: /fixmc @channel CA correct_entry_mc
    Example: /fixmc @SomeKOL So1ABC123 100K
    """
    if len(context.args) < 5:
        await update.message.reply_text(
            "🔧 <b>Late Call Ka MC Fix Karein</b>\n\n"
            "<code>/fixmc @channelname CONTRACT_ADDRESS entry_mc</code>\n\n"
            "Example:\n"
            "<code>/fixmc @SomeKOL So1ABC123 100K</code>\n\n"
            "Bot ne agar call 140K pe track ki lekin caller ne 100K pe di thi, "
            "yeh command:\n"
            "1️⃣ entry MC 100K set karega\n"
            "2️⃣ purani 'Dropped a Call' post ko edit kar ke sahi MC dikhayega\n"
            "3️⃣ usi MC se aage ka X tracking dobara start karega",
            parse_mode="HTML"); return

    channel = context.args[0].lstrip("@").strip().lower()
    ca      = context.args[1].strip()
    new_mc  = parse_mc_string(context.args[2])
    owner_ath_mc = parse_mc_string(context.args[3])
    try: owner_x = float(context.args[4].lower().rstrip("x"))
    except ValueError: owner_x = 0.0
    if new_mc <= 0 or owner_ath_mc <= 0 or owner_x < 2:
        await update.message.reply_text("⚠️ Valid call MC, ATH MC aur exact X dain."); return
    mc_ratio = owner_ath_mc / new_mc
    if abs(mc_ratio - owner_x) / max(owner_x, 1) > 0.15:
        await update.message.reply_text(
            f"⚠️ Values match nahi kartin: {fmt_mc(new_mc)} → {fmt_mc(owner_ath_mc)} "
            f"takriban {fmt_x(mc_ratio)} banta hai, {fmt_x(owner_x)} nahi. "
            "Call MC, ATH MC ya exact X dobara check karein."); return

    call_key = f"{channel}_{ca}"
    if call_key not in tracked_calls:
        found = next((k for k in tracked_calls if k.lower() == call_key.lower()), None)
        if not found:
            # OWNER FIX (2026-09-14): a call that pumped hard and then got
            # pruned/rugged before the owner got to /fixmc it used to be a
            # dead end here — tracked_calls_cleanup_job had already moved it
            # into the permanent archive, so it looked "never tracked" even
            # though the bot did track it. Check the archive and restore it
            # into live tracking instead of forcing a fresh /addmissedcall
            # (which would create a second, separate record for the same call).
            archive = load_call_archive()
            arc_key = next((k for k in archive if k.lower() == call_key.lower()), None)
            if arc_key:
                call = dict(archive[arc_key])
                call.setdefault("msg_id", 0)
                call.setdefault("channel", channel)
                call.setdefault("ca", ca)
                tracked_calls[arc_key] = call
                archive.pop(arc_key, None)
                save_call_archive(archive)
                call_key = arc_key
                logger.info(f"/fixmc restored {arc_key} from archive back into live tracking")
            else:
                await update.message.reply_text(
                    f"⚠️ Yeh call tracked nahi hai: @{channel} / <code>{ca}</code>\n\n"
                    f"Archive mein bhi nahi mili. Pehle <code>/addmissedcall @{channel} {ca} ...</code> "
                    f"se add karein (upar poora CA copy kar lein).", parse_mode="HTML"); return
        else:
            call_key = found
    call = tracked_calls[call_key]

    old_fmt = call.get("entry_fmt", "N/A")
    old_mc  = float(call.get("entry_mc", 0) or 0)

    # Live data → derive a consistent entry price for the new entry MC
    dex = None
    try:
        _invalidate_dex_cache(call.get("ca", ca))
        dex = await asyncio.wait_for(fetch_dexscreener(call.get("ca", ca)), timeout=15)
    except Exception:
        dex = None
    cur_mc    = float((dex or {}).get("mcap", 0) or 0)
    cur_price = float((dex or {}).get("price", 0) or 0)

    if cur_mc > 0 and cur_price > 0:
        call["entry_price"] = cur_price * (new_mc / cur_mc)
    elif old_mc > 0 and call.get("entry_price", 0):
        call["entry_price"] = call["entry_price"] * (new_mc / old_mc)
    call["entry_mc"]     = new_mc
    call["entry_fmt"]    = fmt_mc(new_mc)
    call["entry_src"]    = "owner"
    call["entry_locked"] = True
    if dex and dex.get("symbol"):
        call["symbol"] = dex["symbol"]

    # Owner-confirmed historical peak overrides unreliable live/current MC.
    ratio = owner_x
    call.pop("peak_ratio", None); call.pop("peak_mc", None); call.pop("peak_mc_fmt", None)
    call["last_ratio"] = round(ratio, 4)
    call["owner_reported_x"] = round(ratio, 4)
    call["owner_reported_ath_mc"] = owner_ath_mc
    _update_peak(call, ratio, owner_ath_mc)

    # Milestones already passed at the corrected entry are recorded SILENTLY
    # (no spam), future ones alert normally in real time.
    silent = []
    if ratio > 0:
        initial_x = max(milestones_for_ratio(ratio)) if milestones_for_ratio(ratio) else 0
        for ms in get_milestones():
            if ms < initial_x and ratio >= ms and ms not in sent_milestones[call_key]:
                sent_milestones[call_key].add(ms); silent.append(ms)
        _save_milestones()
    _save_tracked()

    # ── Edit the original "Dropped a Call" post with the corrected MC ────────
    edited = "—"
    edit_err = ""
    post_id = call.get("drop_post_id")
    if post_id:
        try:
            text = build_dropped_alert(
                call.get("channel", channel), call.get("msg_id", 0), call.get("ca", ca),
                call.get("chain", "SOL"), call["entry_fmt"], call.get("symbol", ""))
            _ck = (call.get("chain", "SOL") or "").upper()
            _em = DROPPED_CHAIN_EMOJIS.get("ETH" if _ck == "EVM" else _ck, DROPPED_CALL_EMOJI)
            ok = False
            if userbot_client:
                if call.get("drop_post_media"):
                    ok = await _userbot_edit_caption_with_premium_emoji(
                        TARGET_CHANNEL, post_id, text, [_em, _em],
                        forced_pack=_DROPPED_CALL_PACK)
                else:
                    ok = await _userbot_edit_with_premium_emoji(
                        TARGET_CHANNEL, post_id, text,
                        forced_pack=_DROPPED_CALL_PACK)
            if not ok:
                try:
                    if call.get("drop_post_media"):
                        await context.bot.edit_message_caption(
                            chat_id=TARGET_CHANNEL, message_id=post_id,
                            caption=text, parse_mode="HTML")
                    else:
                        await context.bot.edit_message_text(
                            chat_id=TARGET_CHANNEL, message_id=post_id,
                            text=text, parse_mode="HTML",
                            disable_web_page_preview=True)
                    ok = True
                except Exception as e_b:
                    _emsg = str(e_b).lower()
                    # Telegram "message is not modified" = text pehle se sahi hai.
                    # Yeh failure nahi hai, is liye ise success count karo.
                    if "not modified" in _emsg:
                        ok = True
                        edit_err = ""
                    else:
                        edit_err = f"{type(e_b).__name__}: {e_b}"
                        logger.error(f"/fixmc post edit failed: {e_b}")
            edited = "✅ post edited" if ok else f"⚠️ post edit failed — {edit_err or 'unknown'}"
        except Exception as e:
            logger.error(f"/fixmc edit crash: {e}")
            edited = f"⚠️ post edit failed — {type(e).__name__}: {e}"
    else:
        edited = "ℹ️ original post id saved nahi tha (sirf tracking fix hui)"

    initial_x = max(milestones_for_ratio(ratio)) if milestones_for_ratio(ratio) else 0
    initial_posted = False
    if initial_x >= 2 and initial_x not in sent_milestones[call_key]:
        initial_posted = bool(await send_alert(
            context.bot, call.get("channel", channel), call.get("msg_id", 0), initial_x,
            call.get("chain", "SOL"), call.get("entry_fmt", "N/A"), fmt_mc(owner_ath_mc),
            call.get("ca", ca), call.get("symbol", "UNKNOWN")))
        if initial_posted:
            sent_milestones[call_key].add(initial_x); _save_milestones()

    await update.message.reply_text(
        f"✅ <b>Late Call Fixed!</b>\n\n"
        f"📡 Channel: @{call.get('channel', channel)}\n"
        f"🪙 Symbol: {call.get('symbol','UNKNOWN')}\n"
        f"🔗 CA: <code>{call.get('ca', ca)}</code>\n\n"
        f"Old Entry: <b>{old_fmt}</b>\n"
        f"New Entry: <b>{call['entry_fmt']}</b>\n"
        f"Owner ATH MC: <b>{fmt_mc(owner_ath_mc)}</b>\n"
        f"Exact X: <b>{fmt_x(ratio)}</b> · Alert: <b>{initial_x}X+</b> "
        f"({'posted' if initial_posted else 'already recorded / failed'})\n"
        f"Silently marked: {', '.join(str(m)+'X' for m in silent) if silent else '—'}\n\n"
        f"📝 {edited}\n"
        f"🔄 Aage ka tracking isi entry MC se real-time chalega.",
        parse_mode="HTML")


@owner_only
async def cmd_setkolowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set which Telegram user gets DM alerts for a KOL channel.
    Multi-step: bot asks for channel name then user username.
    Or direct: /setkolowner @channel @user"""
    uid = update.effective_user.id
    # Direct usage: /setkolowner @channel @user
    if len(context.args) >= 2:
        ch_input   = context.args[0].lstrip("@").strip().lower()
        user_input = context.args[1].lstrip("@").strip()
        channels = load_channels()
        if ch_input not in [c.lower() for c in channels]:
            await update.message.reply_text(
                f"⚠️ @{ch_input} tracked nahi hai. Pehle /addchannel karo."); return
        d = load_users_dict()
        found_id = None
        for uid_str, udata in d.items():
            if (udata.get("username") or "").lower() == user_input.lower():
                found_id = int(uid_str)
                break
        if not found_id:
            await update.message.reply_text(
                f"⚠️ @{user_input} ka ID nahi mila.\n\n"
                f"User ne pehle bot pe /start karna chahiye.\n\n"
                f"Agar numeric ID pata ho:\n"
                f"<code>/setkolownerid {ch_input} NUMERIC_ID</code>",
                parse_mode="HTML"); return
        kol_owners = load_kol_owners()
        kol_owners[ch_input] = found_id
        save_kol_owners(kol_owners)
        await update.message.reply_text(
            f"✅ <b>KOL Owner Set!</b>\n\n"
            f"Channel: @{ch_input}\n"
            f"Owner: @{user_input} (ID: {found_id})\n\n"
            f"Ab se @{ch_input} ki sab call milestones directly @{user_input} ke DM mein jaengi.",
            parse_mode="HTML"); return
    # Interactive multi-step flow
    owner_edit_state[uid] = {"state": ST_SETKOLOWNER_CH}
    await update.message.reply_text(
        "🔔 <b>KOL Call Alert Setup</b>\n\n"
        "Step 1/2: Kaunse channel ki calls ka alert set karna hai?\n\n"
        "Channel ka username bhejo (e.g. @ChannelName):",
        parse_mode="HTML")

@owner_only
async def cmd_setkolownerid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set KOL owner by direct Telegram user ID (when username lookup fails).
    Usage: /setkolownerid @channel NUMERIC_USER_ID"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: <code>/setkolownerid @channel NUMERIC_USER_ID</code>\n\n"
            "Example: <code>/setkolownerid SomeKOL 123456789</code>",
            parse_mode="HTML"); return
    ch_input = context.args[0].lstrip("@").strip().lower()
    try:
        user_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ User ID numeric hona chahiye."); return
    channels = load_channels()
    if ch_input not in [c.lower() for c in channels]:
        await update.message.reply_text(
            f"⚠️ @{ch_input} tracked nahi hai."); return
    kol_owners = load_kol_owners()
    kol_owners[ch_input] = user_id
    save_kol_owners(kol_owners)
    await update.message.reply_text(
        f"✅ <b>KOL Owner Set!</b>\n\n"
        f"Channel: @{ch_input}\n"
        f"User ID: <code>{user_id}</code>\n\n"
        f"Ab se @{ch_input} ki sab call milestones is user ke DM mein jaengi.",
        parse_mode="HTML")

@owner_only
async def cmd_removekolowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove KOL owner mapping for a channel.
    Usage: /removekolowner @channel"""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/removekolowner @channel</code>", parse_mode="HTML"); return
    ch_input = context.args[0].lstrip("@").strip().lower()
    kol_owners = load_kol_owners()
    if ch_input in kol_owners:
        del kol_owners[ch_input]
        save_kol_owners(kol_owners)
        await update.message.reply_text(f"✅ @{ch_input} ka KOL owner mapping hata diya gaya.")
    else:
        await update.message.reply_text(f"⚠️ @{ch_input} ka koi owner set nahi tha.")

@owner_only
async def cmd_listkolowners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all KOL channel → owner mappings."""
    kol_owners = load_kol_owners()
    if not kol_owners:
        await update.message.reply_text("ℹ️ Koi KOL owner set nahi hai."); return
    d = load_users_dict()
    # Build reverse username lookup
    id_to_uname = {int(uid): udata.get("username","") for uid, udata in d.items()}
    lines = ["🔔 <b>KOL Owner Mappings</b>\n"]
    for ch, owner_id in sorted(kol_owners.items()):
        uname = id_to_uname.get(owner_id, "")
        uname_str = f"@{uname}" if uname else f"ID:{owner_id}"
        lines.append(f"• @{ch} → {uname_str}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_xcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check how much a token has moved since it was shared in a channel.
    Usage: /xcheck @channel CA
    Example: /xcheck @SomeKOL So1ABC123xyz
    Shows: when the call was made, entry MC, current MC, and how many X it went."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "📊 <b>Token Performance Check</b>\n\n"
            "Usage: <code>/xcheck @channel CA</code>\n\n"
            "Example:\n"
            "<code>/xcheck @SomeKOL So1ABC123xyz</code>\n\n"
            "Yeh batayega:\n"
            "• Call kab ki gayi thi\n"
            "• Entry MC kya tha\n"
            "• Abhi MC kitna hai\n"
            "• Kitne X ho gaya",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    ca = context.args[1].strip()
    call_key = f"{channel}_{ca}"
    # Try case-insensitive search
    if call_key not in tracked_calls:
        found_key = next((k for k in tracked_calls if k.lower() == call_key.lower()), None)
        if found_key:
            call_key = found_key
    if call_key not in tracked_calls:
        await update.message.reply_text(
            f"⚠️ Yeh call tracked nahi hai.\n\n"
            f"Channel: @{channel}\nCA: <code>{ca[:20]}...</code>",
            parse_mode="HTML"); return
    call = tracked_calls[call_key]
    msg = await update.message.reply_text("⏳ Current data fetch ho raha hai...")
    dex = await fetch_dexscreener(ca)
    entry_mc  = call.get("entry_mc", 0)
    entry_fmt = call.get("entry_fmt", "N/A")
    symbol    = (call.get("symbol","") or "TOKEN").upper()
    chain     = call.get("chain","SOL")
    tracked_since = call.get("tracked_since","")
    # Calculate time since call
    time_str = "N/A"
    if tracked_since:
        try:
            ts_dt = datetime.fromisoformat(tracked_since)
            delta = datetime.utcnow() - ts_dt
            hours = int(delta.total_seconds() // 3600)
            mins  = int((delta.total_seconds() % 3600) // 60)
            if hours >= 24:
                days = hours // 24
                time_str = f"{days}d {hours%24}h ago"
            elif hours > 0:
                time_str = f"{hours}h {mins}m ago"
            else:
                time_str = f"{mins}m ago"
        except Exception:
            time_str = "N/A"
    cur_mc_fmt = "N/A"
    x_val_str  = "N/A"
    if dex and dex.get("mcap", 0) > 0:
        cur_mc = dex["mcap"]
        cur_mc_fmt = dex.get("mcap_fmt", fmt_mc(cur_mc))
        if entry_mc > 0:
            x_val = cur_mc / entry_mc
            x_val_str = fmt_x(x_val)
    # Best milestone hit so far
    milestones_hit = sorted(sent_milestones.get(call_key, set()))
    best_x_str = f"{max(milestones_hit)}X" if milestones_hit else "Not hit yet"
    dex_path   = CHAIN_TO_DEXPATH.get(chain.upper(), "ethereum")
    chart_url  = f"https://dexscreener.com/{dex_path}/{ca}"
    await msg.edit_text(
        f"📊 <b>@{channel} — ${symbol}</b>\n\n"
        f"⏱ Called: <b>{time_str}</b>\n"
        f"⛓ Chain: <b>{chain}</b>\n"
        f"📌 Entry MC: <b>{entry_fmt}</b>\n"
        f"💰 Current MC: <b>{cur_mc_fmt}</b>\n"
        f"📈 Current X: <b>{x_val_str}</b>\n"
        f"🏆 Best Milestone: <b>{best_x_str}</b>\n\n"
        f"CA: <code>{ca}</code>\n"
        f'<a href="{chart_url}">📊 Chart</a>',
        parse_mode="HTML", disable_web_page_preview=True)

@owner_only
async def cmd_addx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set X/Twitter handle for a KOL channel.
    Usage: /addx @channel xhandle
    Example: /addx @SomeKOL WizardScan
    What it does: Adds a clickable X (Twitter) link next to the KOL's name in:
      • Post 136 (Leaderboard) — KOL name ke saath 𝕏 icon
      • Post 134 (Champions)   — KOL name ke saath 𝕏 icon
    Viewers can click to go directly to the KOL's X profile."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "📋 <b>X Handle Set Karne Ka Tarika:</b>\n\n"
            "<code>/addx @channel xhandle</code>\n\n"
            "Example:\n<code>/addx @SomeKOL WizardScan</code>\n\n"
            "Handle @ ke bina likhain (e.g. WizardScan, not @WizardScan)\n\n"
            "━━━━━━━━━━━━━━━\n"
            "💡 <b>Yeh kya karta hai?</b>\n\n"
            "Jab yeh set hota hai toh post 136 (Leaderboard) aur post 134 (Champions) mein "
            "KOL ke naam ke saath ek <b>𝕏</b> icon appear hota hai. "
            "Viewers us par click karke directly KOL ka X (Twitter) profile dekh sakte hain.\n\n"
            "Use /xlist to see all set X handles.",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    handle  = _clean_x_handle(context.args[1])
    if not channel or not handle:
        await update.message.reply_text(
            "⚠️ Sirf asli X handle ya x.com profile link dein. Telegram/t.me link accept nahi hota.")
        return
    x_acc = load_x_accounts()
    x_acc[channel] = handle
    save_x_accounts(x_acc)
    await update.message.reply_text(
        f"✅ <b>X Handle Set!</b>\n\n"
        f"Channel: @{channel}\n"
        f"X: <a href=\"https://x.com/{html.escape(handle)}\">@{html.escape(handle)}</a>\n\n"
        f"<b>Kya hoga ab:</b>\n"
        f"• Post 136 (Leaderboard) mein @{channel} ke naam ke saath <b>𝕏</b> link aayega\n"
        f"• Post 134 (Champions) mein bhi <b>𝕏</b> link aayega\n"
        f"• Viewers click karke directly X profile dekh sakte hain\n\n"
        f"Agla update cycle (2 min) ke baad visible hoga.",
        parse_mode="HTML", disable_web_page_preview=True)

@owner_only
async def cmd_removex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove X/Twitter handle for a KOL channel.
    Usage: /removex @channel"""
    if not context.args:
        await update.message.reply_text("Usage: <code>/removex @channel</code>", parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip().lower()
    x_acc = load_x_accounts()
    if channel in x_acc:
        del x_acc[channel]
        save_x_accounts(x_acc)
        await update.message.reply_text(f"✅ @{channel} ka X handle hata diya.")
    else:
        await update.message.reply_text(f"⚠️ @{channel} ka koi X handle set nahi tha.")

@owner_only
async def cmd_xtrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

@owner_only
async def cmd_updateleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Updating leaderboard post (136) via userbot with premium emojis...")
    ok = await _update_leaderboard_with_premium_emojis(context.bot)
    if ok:
        cfg_set("last_leaderboard_update", datetime.utcnow().isoformat())
        await update.message.reply_text("✅ Leaderboard (post 136) updated with premium emojis!")
    else:
        await update.message.reply_text(
            "⚠️ Could not edit post 136. Make sure userbot is connected (/userbotlogin) "
            "and has access to the channel.")

@owner_only
async def cmd_refreshleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh leaderboard post 136 — RESETS scores to zero, only counts calls from now onward."""
    # FIX (2026-10-11): this used to only set leaderboard_reset_since (a date),
    # but _calc_leaderboard_scores() prefers the per-call snapshot
    # (lb_milestone_snapshot) from an earlier reset, so old X's kept showing and
    # the list never went to zero. Use the same snapshot reset as
    # /resetleaderboard so every existing call's old milestones are excluded.
    now = _perform_leaderboard_reset()
    cfg_set("last_leaderboard_reset",  now.isoformat())
    cfg_set("last_leaderboard_update", "")
    msg = await update.message.reply_text("⏳ Leaderboard scores reset ho rahe hain aur post 136 update ho raha hai...")
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_leaderboard_update", now.isoformat())
            await msg.edit_text(
                "✅ <b>Leaderboard Reset + Refresh!</b>\n\n"
                "Purane sab scores zero ho gaye.\n"
                "Post 136 update ho gayi — ab sirf nayi calls count hongi. 🏆",
                parse_mode="HTML")
        else:
            ub_status = "✅ Connected" if (userbot_client and userbot_client.is_connected()) else "❌ Not connected"
            await msg.edit_text(
                f"⚠️ Scores reset hue lekin post 136 edit nahi hua.\n\n"
                f"Userbot: {ub_status}\n\n"
                f"Agar userbot connected nahi: /userbotcheck se confirm karo.",
                parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

@owner_only
async def cmd_updatechampions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Updating champions post (134) with premium emojis...")
    ok = await _update_champions_with_premium_emojis(context.bot)
    if ok:
        cfg_set("last_champions_update", datetime.utcnow().isoformat())
        await update.message.reply_text("✅ Champions (post 134) updated with premium emojis!")
    else:
        await update.message.reply_text("⚠️ Could not edit post 134. Check bot admin permissions in @WizardScan.")

@owner_only
async def cmd_refreshchampions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full champions reset + refresh post 134.
    - Saare channel points zero ho jaate hain
    - Purane tracked call keys exclude ho jaate hain (old X records nahi dikhenge)
    - Post 134 seedha fresh (empty) list se update hoti hai
    """
    now = datetime.utcnow()

    # 1. Snapshot max milestone per call_key at reset time.
    #    build_champions_text() will only count milestones ABOVE this snapshot —
    #    so new records on existing calls appear after reset.
    c = load_config()
    champ_snap = {}
    for ck, _ in tracked_calls.items():
        ms = list(sent_milestones.get(ck, set()))
        champ_snap[ck] = max(ms) if ms else 0
    c["champion_milestone_snapshot"] = champ_snap
    c["champion_excluded_call_keys"] = []  # clear legacy blanket list
    c["last_champions_update"] = ""
    c["last_champions_reset"]  = now.isoformat()
    save_config(c)

    # 2. Reset ALL channel points to zero
    async with _points_lock:
        save_channel_points({})

    msg = await update.message.reply_text("⏳ Champions reset ho rahi hai — points zero, purane records exclude, post 134 update ho raha hai...")
    try:
        ok = await _update_champions_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_champions_update", now.isoformat())
            await msg.edit_text(
                "✅ <b>Champions (post 134) reset + refresh!</b>\n\n"
                "• Sare points zero ho gaye\n"
                "• Purane records (1000x etc.) ab nahi dikhenge\n"
                "• Post 134 fresh ho gayi — ab se sirf naye calls count honge 🏆",
                parse_mode="HTML")
        else:
            await msg.edit_text(
                "⚠️ Points reset hue lekin post 134 edit nahi hua.\n"
                "Userbot check karo: /userbotcheck",
                parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

@owner_only
async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show live trending tokens from DexScreener — owner only."""
    msg = await update.message.reply_text("⏳ Fetching trending from DexScreener...")
    try:
        chain_tokens = await fetch_trending()
        text = build_trending_text(chain_tokens)
        await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await msg.edit_text(f"⚠️ DexScreener fetch failed: {e}")

@owner_only
async def cmd_refreshtrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh trending post 135 — reset timer, fetch fresh data, update immediately."""
    cfg_set("last_trending_update", "")
    save_trending_cache({})  # clear cache so full fresh list is fetched
    msg = await update.message.reply_text("⏳ DexScreener/GeckoTerminal se bilkul naya data fetch ho raha hai...")
    try:
        # Fetch once and pass directly — avoids double-fetch inside _update_trending_with_premium_emojis
        chain_tokens = await fetch_trending()
        save_trending_cache(chain_tokens)  # store for next price-only cycle
        total = sum(len(v) for v in chain_tokens.values())
        breakdown = " | ".join(f"{c}:{len(chain_tokens.get(c,[]))}" for c in ["SOL","ETH","BNB","BASE"])
        ok = await _update_trending_with_premium_emojis(context.bot, chain_tokens=chain_tokens)
        if ok:
            cfg_set("last_trending_update", datetime.utcnow().isoformat())
            await msg.edit_text(
                f"✅ Trending (post 135) refresh ho gayi!\n"
                f"📊 {breakdown}\n"
                f"Total: {total} tokens."
            )
        else:
            await msg.edit_text(
                f"⚠️ Data fetch hua ({total} tokens) lekin post 135 edit nahi hua.\n"
                f"📊 {breakdown}\n"
                f"Userbot check karo: /userbotcheck"
            )
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: {e}")

@owner_only
async def _do_chain_trend_reset(update, context, chain_key: str):
    """Helper: reset ONE chain's trending tokens and update only the relevant post(s).
    Other chains are NOT touched — they keep their existing cached tokens."""
    msg = await update.message.reply_text(f"⏳ {chain_key} trending reset ho rahi hai...")
    try:
        blacklist = load_trending_blacklist()
        ok135 = ok2 = False

        # ── Chains that appear in post 135 (SOL/ETH/BNB/BASE) ────────────────
        if chain_key in ("SOL", "ETH", "BNB", "BASE"):
            # Load existing cache so other chains are preserved
            cache = load_trending_cache()
            if not cache:
                cache = {"SOL": [], "ETH": [], "BNB": [], "BASE": []}
            # Fetch fresh tokens for ONLY this chain
            try:
                if chain_key == "SOL":
                    fresh = await asyncio.to_thread(_fetch_trending_sync)
                    cache["SOL"] = fresh.get("SOL", [])
                elif chain_key == "ETH":
                    cache["ETH"] = await asyncio.to_thread(_fetch_gecko_chain, "eth", "ETH", blacklist)
                elif chain_key in ("BNB", "BSC"):
                    new_bnb = await asyncio.to_thread(_fetch_gecko_chain, "bsc", "BNB", blacklist)
                    cache["BNB"] = new_bnb
                elif chain_key == "BASE":
                    cache["BASE"] = await asyncio.to_thread(_fetch_gecko_chain, "base", "BASE", blacklist)
            except Exception as fe:
                logger.warning(f"Chain {chain_key} fresh-fetch failed: {fe}")
            _inject_pinned_tokens(cache)
            save_trending_cache(cache)
            ok135 = await _update_trending_with_premium_emojis(context.bot, chain_tokens=cache)

        # ── Chains that appear in posts 3560/3562 (SOL/ETH/BSC/RH/BASE/TON) ──
        if chain_key in ("SOL", "ETH", "BSC", "BNB", "RH", "BASE", "TON"):
            cache2 = load_trending2_cache()
            if not cache2:
                cache2 = {"SOL": [], "ETH": [], "BSC": [], "RH": [], "BASE": [], "TON": []}
            try:
                if chain_key == "SOL":
                    fresh2 = await asyncio.to_thread(_fetch_trending2_sync)
                    cache2["SOL"] = fresh2.get("SOL", [])
                elif chain_key == "ETH":
                    cache2["ETH"] = await asyncio.to_thread(_fetch_gecko_chain, "eth", "ETH", blacklist)
                elif chain_key in ("BNB", "BSC"):
                    new_bsc = await asyncio.to_thread(_fetch_gecko_chain, "bsc", "BSC", blacklist)
                    cache2["BSC"] = new_bsc
                elif chain_key == "RH":
                    cache2["RH"] = await asyncio.to_thread(_fetch_gecko_chain, "robinhood", "RH", blacklist)
                elif chain_key == "BASE":
                    cache2["BASE"] = await asyncio.to_thread(_fetch_gecko_chain, "base", "BASE", blacklist)
                elif chain_key == "TON":
                    fresh2 = await asyncio.to_thread(_fetch_trending2_sync)
                    cache2["TON"] = fresh2.get("TON", [])
            except Exception as fe:
                logger.warning(f"Chain {chain_key} fresh-fetch (trending2) failed: {fe}")
            _inject_pinned_tokens(cache2)
            save_trending2_cache(cache2)
            res = await _update_trending2_posts(context.bot, chain_tokens2=cache2)
            ok2 = any(res.values())

        await msg.edit_text(
            f"✅ <b>{chain_key} Trending Reset!</b>\n\n"
            f"Post 135: {'✅' if ok135 else '—'}\n"
            f"Posts 3560/3562: {'✅' if ok2 else '—'}\n\n"
            f"Sirf <b>{chain_key}</b> tokens reset hue. Baaki chains unchanged.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.edit_text(f"❌ {chain_key} reset failed: {e}")


@owner_only
async def cmd_trendingkols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 tracked KOLs ranked by highest X milestone (live, real-time)."""
    numbers = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    top10 = _calc_trending_kols()
    lines = ["🔥 <b>LEADERBOARD KOLS:</b>\n"]
    for i, row in enumerate(top10):
        if i >= 10: break
        ch       = row["channel"]
        best_x   = row["best_x"]
        num      = numbers[i] if i < len(numbers) else f"{i+1}."
        x_str    = f"{best_x}X" if best_x > 0 else "—"
        # Plain username only — no t.me link, no WizardScan post link below
        line = f"{num} @{html.escape(ch)} ➡️ {x_str}"
        lines.append(line)
    if len(top10) == 0:
        lines.append("<i>No data yet. Milestones being tracked...</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML",
                                    disable_web_page_preview=True)

@admin_only
async def cmd_setrankingemojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set premium custom emoji IDs for leaderboard rankings (1-10). Admin+Owner."""
    if not context.args:
        current = cfg_get("ranking_emojis", [])
        await update.message.reply_text(
            "📋 <b>Ranking Emojis (Post 136)</b>\n\n"
            "Positions 1-10 k liye premium emoji IDs set karo.\n\n"
            "Usage: <code>/setrankingemojis ID1 ID2 ID3 ... ID10</code>\n\n"
            "Emoji IDs kaise milti hain: kisi custom emoji wali message @getidsbot ko forward karo.\n\n"
            f"Current: <code>{'  '.join(str(e) for e in current) or 'not set (using regular numbers)'}</code>",
            parse_mode="HTML"); return
    ids = [arg.strip() for arg in " ".join(context.args).split() if arg.strip()]
    cfg_set("ranking_emojis", ids[:10])
    await update.message.reply_text(f"✅ Ranking emojis set for {len(ids[:10])} positions.\n\nUse /updateleaderboard to apply to post 136.")

@owner_only
async def cmd_setstartmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setstartmedia — reply to a video/photo to set it as /start media."""
    msg = update.message.reply_to_message
    if not msg:
        await update.message.reply_text(
            "📹 <b>Set Start Video/Photo</b>\n\n"
            "Kisi video ya photo ko reply karo aur /setstartmedia bhejo.\n\n"
            "Example:\n1. Video bhejo bot ko\n2. Us video ko reply karo /setstartmedia se",
            parse_mode="HTML"); return
    if msg.video:
        file_id = msg.video.file_id; ftype = "video"
    elif msg.photo:
        file_id = msg.photo[-1].file_id; ftype = "photo"
    elif msg.animation:
        file_id = msg.animation.file_id; ftype = "video"
    else:
        await update.message.reply_text("⚠️ Sirf video ya photo reply karo."); return
    cfg_set("start_media", {"file_id": file_id, "type": ftype})
    await update.message.reply_text(f"✅ /start ka {ftype} set ho gaya!\n\nAb /start try karo.")

@owner_only
async def cmd_clearstartmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clearstartmedia — remove /start media (reverts to default video)."""
    cfg_set("start_media", None)
    await update.message.reply_text("✅ /start ki media hata di gayi. Ab default video use hogi.")

@owner_only
async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/postnow — manually post hashtag image to channel right now."""
    await update.message.reply_text("⏳ Hashtag image channel pe post ho rhi hai...")
    try:
        await _post_hashtag_to_channel(context.bot)
        await update.message.reply_text("✅ Hashtag image @WizardScan pe post ho gayi!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Post nahi ho sakti: {e}\n\nMake sure bot channel ka admin hai.")

@owner_only
async def cmd_myusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_users_dict(); subs = load_subscriptions()
    await update.message.reply_text(
        f"👥 Total users: <b>{len(d)}</b>\n🔔 DM subscribers: <b>{len(subs)}</b>", parse_mode="HTML")

@owner_only
async def cmd_resetusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all users from users.json — fresh start. Real users re-added when they interact."""
    args = context.args
    if not args or args[0].lower() != "confirm":
        d = load_users_dict()
        await update.message.reply_text(
            f"⚠️ <b>Reset Users</b>\n\n"
            f"Abhi <b>{len(d)}</b> users hain.\n\n"
            f"Yeh sab delete ho jayenge. Sirf woh users wapas aayenge jo bot pe command use karenge.\n\n"
            f"Confirm karne ke liye:\n<code>/resetusers confirm</code>",
            parse_mode="HTML"
        ); return

    save_users_dict({})  # clear completely
    await update.message.reply_text(
        "✅ <b>Done!</b> Sab users reset ho gaye.\n\n"
        "Ab jab bhi koi user koi bhi command use karega, woh automatically count hoga.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_resetmembercount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset the bot member/user counter to zero. Users are recounted as they interact."""
    save_users_dict({})
    await update.message.reply_text(
        "✅ <b>Member count reset ho gaya!</b>\n\n"
        "Ab counter zero se shuru hoga. Jab bhi koi user bot se interact karega, "
        "woh automatically count mein add ho jayega.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_users_dict()
    channels = load_channels(); subs = load_subscriptions()
    ub_status = "❌ Not connected"
    if userbot_client:
        try: me = await userbot_client.get_me(); ub_status = f"✅ @{me.username}"
        except Exception: ub_status = "⚠️ Error"
    config = load_config()
    last_lb = config.get("last_leaderboard_update","never")[:10]
    last_ch = config.get("last_champions_update","never")[:10]
    last_tr = config.get("last_trending_update","never")[:16]

    # Split: users with @username first, then ID-only
    with_uname = []
    no_uname   = []
    for k, v in d.items():
        uname = v.get("username") or ""
        fname = v.get("name") or ""
        if uname:
            with_uname.append(f"@{uname}" + (f" ({html.escape(fname)})" if fname else ""))
        else:
            no_uname.append(f"ID:{k}" + (f" ({html.escape(fname)})" if fname else ""))

    all_lines = [f"• {l}" for l in with_uname] + [f"• {l}" for l in no_uname]

    # ── Header message ────────────────────────────────────────────────────────
    header = (
        f"📊 <b>Bot Stats</b>\n\n"
        f"👥 Total users: <b>{len(d)}</b>\n"
        f"✅ With @username: <b>{len(with_uname)}</b>\n"
        f"🔔 DM subscribers: <b>{len(subs)}</b>\n"
        f"📡 Tracked channels: <b>{len(channels)}</b>\n"
        f"🎯 Active calls: <b>{len(tracked_calls)}</b>\n"
        f"🔔 Milestones fired: <b>{sum(len(v) for v in sent_milestones.values())}</b>\n"
        f"🤖 Userbot: <b>{ub_status}</b>\n\n"
        f"📅 LB: <b>{last_lb}</b> | Champs: <b>{last_ch}</b> | Trending: <b>{last_tr}</b>"
    )
    await update.message.reply_text(header, parse_mode="HTML")

    # ── Paginated user list — 50 per message ─────────────────────────────────
    if not all_lines:
        await update.message.reply_text("👥 <b>Users:</b>\nNo users yet.", parse_mode="HTML")
        return

    PAGE = 50
    total_pages = (len(all_lines) + PAGE - 1) // PAGE
    for page_num in range(total_pages):
        chunk = all_lines[page_num * PAGE : (page_num + 1) * PAGE]
        page_text = (
            f"👥 <b>Users ({page_num+1}/{total_pages})</b>\n\n"
            + "\n".join(chunk)
        )
        await update.message.reply_text(page_text, parse_mode="HTML")
        await asyncio.sleep(0.3)  # avoid flood limit

@owner_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_users_dict()
    # FIX: drop fake placeholder entries (negative IDs from the old data-loss
    # recovery, never real Telegram accounts) so they never show up as pickable
    # usernames and never get counted in "all" — see ST_BROADCAST_PICK note.
    d = {k: v for k, v in d.items() if not v.get("_placeholder")}
    if not d:
        await update.message.reply_text("No users found."); return
    uid_state = update.effective_user.id
    owner_edit_state[uid_state] = {"state": ST_BROADCAST_PICK, "all_users": d}
    # Show only usernames — skip users without a username
    with_username = [(k, v) for k, v in d.items() if v.get("username")]
    no_username   = len(d) - len(with_username)
    lines = [f"@{v['username']}" for k, v in with_username]
    no_uname_note = f"\n⚠️ {no_username} user(s) have no username (will still receive if 'all' is used)." if no_username else ""

    # Show EVERY user, splitting across multiple pages — max 100 members per
    # page (owner requirement), and also capped by CHUNK_CHARS as a safety
    # margin so no single page ever risks exceeding Telegram's 4096-char limit
    # even if usernames are unusually long.
    CHUNK_CHARS = 3500  # safe margin under Telegram's 4096-char limit
    CHUNK_MAX_MEMBERS = 100  # hard cap: max 100 members shown per page
    chunks, cur = [], []
    cur_len = 0
    for line in lines:
        if cur and (len(cur) >= CHUNK_MAX_MEMBERS or cur_len + len(line) + 1 > CHUNK_CHARS):
            chunks.append(cur); cur = []; cur_len = 0
        cur.append(line); cur_len += len(line) + 1
    if cur:
        chunks.append(cur)
    if not chunks:
        chunks = [[]]

    total_parts = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        users_list = "\n".join(chunk)
        part_label = f" (Page {i}/{total_parts})" if total_parts > 1 else ""
        header = f"📢 <b>Broadcast — {len(d)} Users{part_label}</b>\n\n"
        body = f"<pre>{users_list}</pre>" if users_list else "<i>(no usernames in this part)</i>"
        footer = ""
        if i == total_parts:
            footer = (
                f"{no_uname_note}\n\n"
                f"Reply with usernames (comma-separated):\n"
                f"Example: <code>@user1, @user2</code>\n\n"
                f"Or send <code>all</code> to broadcast to everyone."
            )
        await update.message.reply_text(header + body + footer, parse_mode="HTML")
        if total_parts > 1 and i < total_parts:
            await asyncio.sleep(0.3)  # avoid hitting Telegram's flood limit while paginating

def _resolve_broadcast_id(arg_id):
    """Return (bid, record) for the given id, or the latest broadcast if arg_id is empty. None if not found."""
    d = load_broadcasts()
    items = d.get("items", {})
    if not items:
        return None, None
    if arg_id:
        rec = items.get(str(arg_id))
        return (str(arg_id), rec) if rec else (None, None)
    latest_bid = max(items.keys(), key=lambda k: int(k))
    return latest_bid, items[latest_bid]

@owner_only
async def cmd_editbroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit a previously sent broadcast in every recipient's DM at once.
    Usage: /editbroadcast [id]  — id optional, defaults to the latest broadcast."""
    arg_id = context.args[0].strip() if context.args else ""
    bid, rec = _resolve_broadcast_id(arg_id)
    if not rec:
        await update.message.reply_text(
            "⚠️ Koi broadcast nahi mila" + (f" ID <code>{arg_id}</code> ke saath." if arg_id else " abhi tak.") +
            " Use <code>/listbroadcasts</code> to see recent ones.",
            parse_mode="HTML")
        return
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_EDIT_BROADCAST_MSG, "broadcast_id": bid}
    n = len(rec.get("messages", []))
    kind = "media caption" if rec.get("is_media") else "text"
    await update.message.reply_text(
        f"✏️ <b>Edit Broadcast {bid}</b> ({n} DMs, {kind})\n\n"
        f"Ab jo bhi naya {'caption' if rec.get('is_media') else 'text'} bhejoge, "
        f"wo har recipient ki DM mein us purane message ki jagah edit ho jayega.\n\n"
        f"Cancel karne ke liye /cancel.",
        parse_mode="HTML")

@owner_only
async def cmd_deletebroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a previously sent broadcast from every recipient's DM at once.
    Usage: /deletebroadcast [id]  — id optional, defaults to the latest broadcast."""
    arg_id = context.args[0].strip() if context.args else ""
    bid, rec = _resolve_broadcast_id(arg_id)
    if not rec:
        await update.message.reply_text(
            "⚠️ Koi broadcast nahi mila" + (f" ID <code>{arg_id}</code> ke saath." if arg_id else " abhi tak.") +
            " Use <code>/listbroadcasts</code> to see recent ones.",
            parse_mode="HTML")
        return
    messages = rec.get("messages", [])
    status = await update.message.reply_text(f"⏳ Deleting broadcast {bid} from {len(messages)} DMs...")
    ok = fail = 0
    for i, m in enumerate(messages):
        try:
            await context.bot.delete_message(m["chat_id"], m["message_id"])
            ok += 1
        except Exception as e:
            fail += 1
            logger.warning(f"deletebroadcast: could not delete {m} — {e}")
        if (i + 1) % 25 == 0:
            await asyncio.sleep(1)
    # Remove it from the log so it can't be edited/deleted again by mistake.
    d = load_broadcasts()
    d.get("items", {}).pop(bid, None)
    save_broadcasts(d)
    await status.edit_text(
        f"🗑️ <b>Broadcast {bid} Deleted</b>\n"
        f"✅ Removed: {ok} | ⚠️ Couldn't remove (already deleted/blocked): {fail}",
        parse_mode="HTML")

@owner_only
async def cmd_listbroadcasts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent broadcast IDs so the owner can target one with /editbroadcast or /deletebroadcast."""
    d = load_broadcasts()
    items = d.get("items", {})
    if not items:
        await update.message.reply_text("Koi broadcast history nahi mili.")
        return
    ids = sorted(items.keys(), key=lambda k: int(k), reverse=True)[:15]
    lines = []
    for bid in ids:
        rec = items[bid]
        n = len(rec.get("messages", []))
        when = rec.get("sent_at", "")[:16].replace("T", " ")
        kind = "media" if rec.get("is_media") else "text"
        lines.append(f"  • <code>{bid}</code> — {n} DMs, {kind}, {when} UTC")
    await update.message.reply_text(
        "📋 <b>Recent Broadcasts</b> (newest first)\n\n" + "\n".join(lines) +
        "\n\n<code>/editbroadcast ID</code> ya <code>/deletebroadcast ID</code>",
        parse_mode="HTML")

@owner_only
async def cmd_mediabroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a photo or video with caption to ALL bot users at once."""
    d = load_users_dict()
    # FIX: exclude fake placeholder entries from the displayed count too.
    d = {k: v for k, v in d.items() if not v.get("_placeholder")}
    if not d:
        await update.message.reply_text("No users found."); return
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_MEDIABROADCAST_MSG}
    await update.message.reply_text(
        f"📸 <b>Media Broadcast — {len(d)} Users</b>\n\n"
        f"Ab photo ya video bhejain caption ke saath.\n"
        f"Bot yeh sab users ko forward karega.\n\n"
        f"⚡ Rate limiting active hai — 10k+ users safe hain.\n"
        f"❌ Deleted/blocked accounts auto-skip honge.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_showtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the current alert post template clearly."""
    cur = cfg_get("alert_template", DEFAULT_TEMPLATE)
    emojis = cfg_get("alert_emoji_ids", {})
    emoji_info = []
    if emojis.get("pos1"): emoji_info.append(f"• Pehla 🔮 (Pos1): <code>{emojis['pos1']}</code>")
    if emojis.get("pos2"): emoji_info.append(f"• Doosra 🔮 (Pos2): <code>{emojis['pos2']}</code>")
    if emojis.get("global"): emoji_info.append(f"• Global (sab): <code>{emojis['global']}</code>")
    if emojis.get("low"): emoji_info.append(f"• LOW (2x–49x): <code>{emojis['low']}</code>")
    if emojis.get("high"): emoji_info.append(f"• HIGH (50x+): <code>{emojis['high']}</code>")
    emoji_str = "\n".join(emoji_info) if emoji_info else "  (koi nahi — /getemoji se set karo)"

    await update.message.reply_text(
        "📋 <b>CURRENT ALERT TEMPLATE</b>\n\n"
        "<b>Template text:</b>\n"
        f"<pre>{cur[:1200]}</pre>\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "<b>Premium Emojis set:</b>\n"
        f"{emoji_str}\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "💡 <b>Tips:</b>\n"
        "• Template mein <b>🔮</b> likho — wahan premium emoji lagegi\n"
        "• Jitne chahein utne 🔮 likhein (har position alag emoji)\n"
        "• Template change karein: /edittemplate\n"
        "• Emojis set karein: /getemoji",
        parse_mode="HTML"
    )

@owner_only
async def cmd_settemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """New command: send template WITH premium emojis already in it — bot auto-captures emoji IDs."""
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_SETTEMPLATE_EM}
    await update.message.reply_text(
        "✏️ <b>TEMPLATE + PREMIUM EMOJI SETUP</b>\n\n"
        "Apna template bhejo — jahan premium emoji chahiye wahan seedha woh premium emoji use karo "
        "(apne premium account se type karo).\n\n"
        "Bot automatically un emojis ka ID capture kar lega aur sab alerts mein use karega.\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "📌 <b>Variables (text mein likhein):</b>\n"
        "<code>{channel}</code> — KOL channel\n"
        "<code>{x}</code> — Multiplier (2, 10, 200...)\n"
        "<code>{symbol}</code> — Token name\n"
        "<code>{chain}</code> — Chain (SOL/ETH/BSC/BASE)\n"
        "<code>{entry}</code> — Entry market cap\n"
        "<code>{current}</code> — Current market cap\n"
        "<code>{ca}</code> — Contract address\n"
        "<code>{kol_link}</code> — KOL post link\n"
        "<code>{bot_link}</code> — Bot link\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "💡 <b>2 tarike:</b>\n"
        "1️⃣ Premium emoji type karo → bot ID save karega\n"
        "2️⃣ Sirf 🔮 likho → pehle se set emoji IDs use honge\n\n"
        "⬇️ <b>Ab apna template bhejo:</b>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_edittemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = cfg_get("alert_template", DEFAULT_TEMPLATE)
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_TEMPLATE}
    await update.message.reply_text(
        "✏️ <b>TEMPLATE EDITOR — @WizardScan Alert</b>\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "<b>Variables:</b>\n"
        "<code>{channel}</code> — KOL channel\n"
        "<code>{x}</code> — Multiplier (2, 5, 10...)\n"
        "<code>{symbol}</code> — Token symbol\n"
        "<code>{chain}</code> — Chain (SOL, ETH, BSC, BASE)\n"
        "<code>{entry}</code> — Entry market cap\n"
        "<code>{current}</code> — Current market cap\n"
        "<code>{ca}</code> — Contract address\n"
        "<code>{kol_link}</code> — KOL post link\n"
        "<code>{tg_link}</code> — @WizardScan link\n"
        "<code>{bot_link}</code> — Bot link\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "🔮 = <b>Premium emoji</b> (position 1,2 = chain emoji; 3,4,5 = KOL/X/BOT)\n"
        "Set chain emojis: <code>/setchainemoji sol EMOJI_ID</code>\n"
        "Set any slot: <code>/setemojislot 3 EMOJI_ID</code>\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "<b>Current template:</b>\n"
        f"<pre>{cur[:900]}</pre>\n\n"
        "⬇️ <b>Naya template bhejo:</b>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_editxtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

@owner_only
async def cmd_setchainemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set chain-specific premium emoji. Usage: /setchainemoji sol EMOJI_ID"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚙️ <b>Chain Emoji Setup</b>\n\n"
            "Usage: <code>/setchainemoji CHAIN EMOJI_ID</code>\n\n"
            "Chains: <code>sol</code> | <code>eth</code> | <code>bsc</code> | <code>base</code>\n\n"
            "Example:\n"
            "<code>/setchainemoji sol 5872901489860550331</code>\n"
            "<code>/setchainemoji bsc 5872901489860550331</code>\n\n"
            "Yeh emoji template mein pehle do 🔮 jagah use hogi (chain ke hisab se).",
            parse_mode="HTML"
        ); return
    chain_key = context.args[0].lower().strip()
    emoji_id  = context.args[1].strip()
    valid = {"sol","eth","bsc","base","bnb"}
    if chain_key not in valid:
        await update.message.reply_text(f"❌ Invalid chain. Use: sol / eth / bsc / base"); return
    if chain_key == "bnb": chain_key = "bsc"
    try: int(emoji_id)
    except ValueError:
        await update.message.reply_text("❌ Emoji ID must be a number."); return
    c = load_config()
    chain_emojis = c.get("chain_emoji_ids", {})
    chain_emojis[chain_key] = emoji_id
    c["chain_emoji_ids"] = chain_emojis
    save_config(c)
    await update.message.reply_text(
        f"✅ <b>{chain_key.upper()} chain emoji set!</b>\n\nID: <code>{emoji_id}</code>\n\nAb alerts mein pehle 2 🔮 yahi emoji use hongi.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_setemojislot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set emoji at any 🔮 slot. Usage: /setemojislot 3 EMOJI_ID"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚙️ <b>Emoji Slot Setup</b>\n\n"
            "Usage: <code>/setemojislot SLOT EMOJI_ID</code>\n\n"
            "Current template slots:\n"
            "🔮 Slot 1 = Chain emoji (pehla)\n"
            "🔮 Slot 2 = Chain emoji (Ca: line)\n"
            "🔮 Slot 3 = KOL link emoji\n"
            "🔮 Slot 4 = BOT link emoji\n\n"
            "Example: <code>/setemojislot 3 5872901489860550331</code>\n\n"
            "Note: Slots 1&2 chain emoji ke zariye control hoti hain.\n"
            "Use /setchainemoji for chain slots.",
            parse_mode="HTML"
        ); return
    try:
        slot = int(context.args[0])
        emoji_id = context.args[1].strip()
        int(emoji_id)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /setemojislot SLOT_NUMBER EMOJI_ID"); return
    slot_to_key = {1: "pos1", 2: "pos2", 3: "pos3", 4: "pos4"}
    if slot not in slot_to_key:
        await update.message.reply_text("❌ Slot must be 1-4."); return
    key = slot_to_key[slot]
    c = load_config()
    emojis = c.get("alert_emoji_ids", {})
    emojis[key] = emoji_id
    c["alert_emoji_ids"] = emojis
    save_config(c)
    slot_name = {1:"Chain (pos1)",2:"Chain (pos2)",3:"KOL",4:"BOT"}.get(slot,"")
    await update.message.reply_text(
        f"✅ <b>Slot {slot} ({slot_name}) set!</b>\n\nEmoji ID: <code>{emoji_id}</code>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_setemojipack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lock the premium emoji pack permanently so it never auto-rotates/reshuffles.
    Usage: /setemojipack red|blue|white|purple|green
           /setemojipack off   (go back to auto-rotation every 10 posts)"""
    valid = [p["name"] for p in EMOJI_PACKS]
    if not context.args:
        c = load_config()
        current = c.get("locked_emoji_pack") or "off (auto-rotating)"
        await update.message.reply_text(
            "🎨 <b>Emoji Pack Lock</b>\n\n"
            f"Available: {', '.join(valid)}\n"
            f"Current: <b>{current}</b>\n\n"
            "Usage: <code>/setemojipack red</code>\n"
            "Ya rotation wapas on karne ke liye: <code>/setemojipack off</code>",
            parse_mode="HTML"
        ); return
    choice = context.args[0].strip().lower()
    c = load_config()
    if choice == "off":
        c.pop("locked_emoji_pack", None)
        save_config(c)
        await update.message.reply_text("✅ Emoji pack ab auto-rotate hoga (har 10 posts par badlega).")
        return
    if choice not in valid:
        await update.message.reply_text(f"❌ Ghalat pack. Options: {', '.join(valid)}, ya off"); return
    c["locked_emoji_pack"] = choice
    save_config(c)
    await update.message.reply_text(
        f"✅ <b>Emoji pack lock ho gaya: {choice}</b>\n\nAb yeh hamesha yehi rahega, "
        "chahe bot restart ho ya post count kuch bhi ho jaye.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_resettracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe EVERY tracked call + its sent-milestone history so the bot starts
    completely fresh. This is the fix for "purana atka hua record" (e.g. a
    call that already hit 21X-25X a day or two ago) — after this, that old
    call is gone entirely: it stops updating, and there is nothing left for
    it to resurface from, ever. Every call still active in a source channel
    will simply be picked up again as a brand-new call the next time it
    posts, tracked from 1x like normal — it is not blocked or ignored, it
    just has no old history attached to it anymore."""
    n_calls = len(tracked_calls)
    n_ms    = sum(len(v) for v in sent_milestones.values())
    tracked_calls.clear()
    sent_milestones.clear()
    _save_tracked()
    _save_milestones()
    await update.message.reply_text(
        f"✅ <b>Tracking reset.</b>\n\n"
        f"{n_calls} tracked calls aur unki {n_ms} milestone entries clear ho gayi hain.\n"
        f"Koi purana record (21X-25X waghera) ab dobara kabhi nahi aayega — "
        f"ab sirf naye calls track honge, hamesha 1x se fresh.",
        parse_mode="HTML"
    )

def _perform_leaderboard_reset():
    """Shared reset logic used by both /resetleaderboard (manual) and the
    automatic 24-hour reset in leaderboard_job(). Snapshot-based: records the
    MAX milestone per call_key at reset time so existing calls can still earn
    NEW milestones after the reset without their old X bleeding back in."""
    now = datetime.utcnow()
    c = load_config()
    c["last_leaderboard_update"] = ""
    c["leaderboard_reset_date"] = now.isoformat()
    c["leaderboard_reset_since"] = now.isoformat()
    c["trending_kols_reset_since"] = now.isoformat()
    lb_snap = {}
    for ck, _ in tracked_calls.items():
        ms = list(sent_milestones.get(ck, set()))
        lb_snap[ck] = max(ms) if ms else 0
    c["lb_milestone_snapshot"] = lb_snap
    c["lb_excluded_call_keys"] = []  # clear legacy blanket list
    save_config(c)
    return now

@owner_only
async def cmd_resetleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually reset leaderboard (and trending KOLs list) and immediately update post 136."""
    now = _perform_leaderboard_reset()
    msg = await update.message.reply_text(
        "⏳ Leaderboard + Trending KOLs reset ho raha hai, post 136 abhi update ho raha hai...",
        parse_mode="HTML"
    )
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_leaderboard_update", now.isoformat())
            await msg.edit_text(
                "✅ <b>Leaderboard Reset!</b>\n\n"
                "Post 136 foran update ho gayi — naya record ab live hai. 🏆\n"
                "Purana record (10000x etc.) ab nahi aayega — sirf reset ke baad ke naye calls count honge.",
                parse_mode="HTML"
            )
        else:
            await msg.edit_text(
                "⚠️ Reset hua lekin post 136 edit nahi hua.\nUserbot check karo: /userbotcheck",
                parse_mode="HTML"
            )
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


@owner_only
async def cmd_setleaderboardtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set leaderboard template. {rank1_link}..{rank10_link}, {rank1_x}..{rank10_x}"""
    uid = update.effective_user.id
    cur = cfg_get("leaderboard_template","")
    owner_edit_state[uid] = {"state": ST_LEADERBOARD_TMPL}
    await update.message.reply_text(
        "📋 <b>Leaderboard Template Setup</b>\n\n"
        "Post 136 ka template set karo. Bot channel names aur X values fill karega.\n\n"
        "<b>Variables jo use kar sakte ho:</b>\n"
        "• <code>{rank1_link}</code> → <a href='t.me/ch'>@ch</a> (hyperlink)\n"
        "• <code>{rank1_channel}</code> → @channelname (plain text)\n"
        "• <code>{rank1_x}</code> → 100X\n"
        "• rank1 se rank10 tak\n\n"
        "<b>🔮</b> = premium emoji placeholder (apni emojis ke liye)\n\n"
        "<b>Current template:</b>\n"
        f"<pre>{(cur or '(koi nahi set)')[:700]}</pre>\n\n"
        "⬇️ <b>Pura template paste karo:</b>",
        parse_mode="HTML", disable_web_page_preview=True)

@owner_only
async def cmd_clearleaderboardtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear custom leaderboard template — use default format."""
    cf = load_config(); cf.pop("leaderboard_template",""); save_config(cf)
    await update.message.reply_text("✅ Leaderboard template clear. Default format use hogi.\n\nFormat: 1️⃣ @channel 100X")

@owner_only
async def cmd_setrangetemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a custom template for an X range, e.g. /setrangetemplate 100 499
    Overrides the built-in hardcoded tier text for that range."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "✏️ <b>Range Template Setup</b>\n\n"
            "Usage: <code>/setrangetemplate LOW HIGH</code>\n"
            "Example: <code>/setrangetemplate 100 499</code>\n\n"
            "Phir agla message template text bhejo — jo bhi variables aur 🔮\n"
            "chahiye woh isi mein daal do (jaise /settemplate mein).\n\n"
            "Dekhne ke liye: /listrangetemplates\n"
            "Hatane ke liye: /delrangetemplate LOW HIGH",
            parse_mode="HTML"
        ); return
    try:
        low = int(context.args[0]); high = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye. Usage: /setrangetemplate 100 499"); return
    if low > high:
        await update.message.reply_text("❌ LOW, HIGH se bara nahi ho sakta."); return
    owner_edit_state[update.effective_user.id] = {"state": ST_RANGE_TMPL, "low": low, "high": high}
    await update.message.reply_text(
        f"✏️ <b>{low}X – {high}X Template</b>\n\n⬇️ Ab apna template bhejo (text, variables aur 🔮 ke saath):",
        parse_mode="HTML"
    )

@owner_only
async def cmd_listrangetemplates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ranges = load_config().get("range_templates", [])
    if not ranges:
        await update.message.reply_text("Koi custom range template set nahi hai.\n\n/setrangetemplate LOW HIGH se banao."); return
    lines = ["📋 <b>Custom Range Templates</b>\n"]
    for r in ranges:
        preview = (r.get("template","") or "")[:80].replace("\n"," ")
        lines.append(f"• <b>{r.get('low')}X – {r.get('high')}X</b>: <code>{preview}...</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_delrangetemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /delrangetemplate LOW HIGH"); return
    try:
        low = int(context.args[0]); high = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye."); return
    c = load_config()
    ranges = c.get("range_templates", [])
    new_ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
    if len(new_ranges) == len(ranges):
        await update.message.reply_text(f"Koi template {low}X–{high}X ke liye nahi mila."); return
    c["range_templates"] = new_ranges; save_config(c)
    await update.message.reply_text(f"✅ {low}X–{high}X template hata diya. Ab hardcoded/default use hoga.")

@owner_only
async def cmd_setkollang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tag a KOL/channel with a language: /setkollang CHANNEL chinese|vietnamese|turkish|russian|english
    Purely organisational — lets the owner see which KOLs have custom-language
    templates set up via /setkoltemplate. Untagged (or 'english') KOLs keep
    getting the normal English default/tiered templates, unchanged."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "🌐 <b>KOL Language Tag</b>\n\n"
            "Usage: <code>/setkollang CHANNEL chinese|vietnamese|turkish|russian|english</code>\n"
            "Example: <code>/setkollang somekol chinese</code>\n\n"
            "Ye tag karne ke baad /setkoltemplate aur /setkoldroppedtemplate is CHANNEL "
            "pe jo bhi set karo, wo <b>us language ke SAB tagged KOLs</b> pe apply hoga — "
            "har channel ke liye alag se set karne ki zaroorat nahi.\n\n"
            "Ek sath kai KOLs tag karne ke liye: <code>/addkolstolang chinese</code>\n"
            "Dekhne ke liye: /listkollang",
            parse_mode="HTML"
        ); return
    channel = context.args[0].lstrip("@").strip().lower()
    lang = context.args[1].strip().lower()
    if lang not in ("chinese", "vietnamese", "turkish", "russian", "english"):
        await update.message.reply_text("❌ Language chinese, vietnamese, turkish, russian, ya english honi chahiye."); return
    c = load_config()
    langs = c.get("channel_language", {})
    if lang == "english":
        langs.pop(channel, None)
    else:
        langs[channel] = lang
    c["channel_language"] = langs
    save_config(c)
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺", "english": "🇬🇧"}[lang]
    await update.message.reply_text(f"✅ @{channel} ab {flag} <b>{lang}</b> tag ho gaya.", parse_mode="HTML")

@owner_only
async def cmd_listkollang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    langs = load_config().get("channel_language", {})
    if not langs:
        await update.message.reply_text("Koi KOL language-tagged nahi hai.\n\n/setkollang CHANNEL chinese|vietnamese|turkish|russian se banao."); return
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}
    lines = ["🌐 <b>KOL Language Tags</b>\n"]
    for ch, lang in langs.items():
        lines.append(f"• {flag.get(lang,'')} @{ch} — <b>{lang}</b>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_addkolstolang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guided flow to tag several KOLs with one language, one at a time:
    /addkolstolang chinese|vietnamese|turkish|russian, then just send usernames one by one —
    bot confirms each and asks for the next, until /done."""
    if not context.args or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text(
            "🌐 <b>Bulk KOL Language Tagging</b>\n\n"
            "Usage: <code>/addkolstolang chinese</code> ya <code>/addkolstolang vietnamese</code>\n\n"
            "Phir bas ek ek KOL ka username bhejte jao — bot har baar tag kar k agla mangega. "
            "Khatam karne ke liye /done bhejo.",
            parse_mode="HTML"
        ); return
    lang = context.args[0].strip().lower()
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_BULK_LANG, "lang": lang, "count": 0}
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
    await update.message.reply_text(
        f"{flag} <b>{lang.title()} tagging shuru</b>\n\n"
        f"Pehle KOL ka username bhejo (bina @ ya @ ke sath, dono chalega):",
        parse_mode="HTML"
    )

def _lang_kol_count(lang, config=None):
    config = config or load_config()
    return len([k for k, v in (config.get("channel_language", {}) or {}).items() if v == lang])

@owner_only
async def cmd_setlangtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the X-alert range template for a WHOLE language group directly —
    no channel name needed: /setlangtemplate chinese|vietnamese|turkish|russian LOW HIGH"""
    if len(context.args) < 3 or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text(
            "✏️ <b>Language-Group Template Setup</b>\n\n"
            "Usage: <code>/setlangtemplate chinese|vietnamese|turkish|russian LOW HIGH</code>\n"
            "Example: <code>/setlangtemplate chinese 2 99</code>\n\n"
            "Iske liye kisi channel ka naam batane ki zaroorat nahi — jo bhi type "
            "(Chinese/Vietnamese/Turkish/Russian) tag hai, sab pe seedha lagega.\n\n"
            "Dekhne ke liye: /listlangtemplates chinese\n"
            "Hatane ke liye: /dellangtemplate chinese LOW HIGH",
            parse_mode="HTML"
        ); return
    lang = context.args[0].strip().lower()
    try:
        low = int(context.args[1]); high = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye. Usage: /setlangtemplate chinese 100 499"); return
    if low > high:
        await update.message.reply_text("❌ LOW, HIGH se bara nahi ho sakta."); return
    _cnt = _lang_kol_count(lang)
    owner_edit_state[update.effective_user.id] = {
        "state": ST_LANG_RANGE_TMPL, "lang": lang, "low": low, "high": high
    }
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
    await update.message.reply_text(
        f"✏️ <b>{flag} {lang.title()} group — {low}X – {high}X Template</b>\n\n"
        f"Abhi {_cnt} KOL(s) is tag mein hain — jo template ab bhejo, sab pe lagega.\n\n"
        f"⬇️ Template bhejo (text, variables aur 🔮 ke saath):",
        parse_mode="HTML"
    )

@owner_only
async def cmd_listlangtemplates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text("Usage: /listlangtemplates chinese|vietnamese|turkish|russian"); return
    lang = context.args[0].strip().lower()
    ranges = load_config().get("lang_range_templates", {}).get(lang, [])
    if not ranges:
        await update.message.reply_text(f"{lang.title()} group ke liye koi template nahi hai.\n\n/setlangtemplate {lang} LOW HIGH se banao."); return
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
    lines = [f"📋 <b>{flag} {lang.title()} group — Templates</b>\n"]
    for r in ranges:
        preview = (r.get("template","") or "")[:80].replace("\n"," ")
        lines.append(f"• <b>{r.get('low')}X – {r.get('high')}X</b>: <code>{preview}...</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_dellangtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3 or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text("Usage: /dellangtemplate chinese|vietnamese|turkish|russian LOW HIGH"); return
    lang = context.args[0].strip().lower()
    try:
        low = int(context.args[1]); high = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye."); return
    c = load_config()
    all_ranges = c.get("lang_range_templates", {})
    ranges = all_ranges.get(lang, [])
    new_ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
    if len(new_ranges) == len(ranges):
        await update.message.reply_text(f"Koi template {lang} {low}X–{high}X ke liye nahi mila."); return
    if new_ranges:
        all_ranges[lang] = new_ranges
    else:
        all_ranges.pop(lang, None)
    c["lang_range_templates"] = all_ranges; save_config(c)
    await update.message.reply_text(f"✅ {lang.title()} group {low}X–{high}X template hata diya. Ab global/default use hoga.")

@owner_only
async def cmd_setlangdroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the dropped-call template for a WHOLE language group directly —
    no channel name needed: /setlangdroppedtemplate chinese|vietnamese|turkish|russian"""
    if not context.args or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text(
            "📣 <b>Language-Group Dropped-Call Template</b>\n\n"
            "Usage: <code>/setlangdroppedtemplate chinese|vietnamese|turkish|russian</code>\n"
            "Example: <code>/setlangdroppedtemplate chinese</code>\n\n"
            "Iske liye kisi channel ka naam batane ki zaroorat nahi — is tag ke sab "
            "KOLs ki pehli call pe yahi template lagega.\n\n"
            "Dekhne ke liye: /showlangdroppedtemplate chinese",
            parse_mode="HTML"
        ); return
    lang = context.args[0].strip().lower()
    uid = update.effective_user.id
    cur = load_config().get("lang_dropped_templates", {}).get(lang, "")
    _cnt = _lang_kol_count(lang)
    owner_edit_state[uid] = {"state": ST_LANG_DROPPED_TMPL, "lang": lang}
    flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
    await update.message.reply_text(
        f"📋 <b>{flag} {lang.title()} group — Dropped-Call Template Setup</b>\n\n"
        f"Abhi {_cnt} KOL(s) is tag mein hain. Jab inme se koi pehli baar call kare "
        f"to jo post jaye us ka template set karo (is group ki language mein).\n\n"
        "<b>Available variables:</b>\n"
        "• <code>{channel}</code> → KOL channel name\n"
        "• <code>{symbol}</code> → token symbol\n"
        "• <code>{chain}</code> → blockchain (SOL/ETH/BNB/BASE)\n"
        "• <code>{entry}</code> → entry market cap\n"
        "• <code>{ca}</code> → contract address\n"
        "• <code>{chart_url}</code> → DexScreener chart link\n"
        "• <code>{kol_link}</code> → link to KOL post\n"
        "• <code>{bot_link}</code> → bot link\n\n"
        f"<b>Current {lang} template:</b>\n<pre>{html.escape(cur) if cur else '(using global/default)'}</pre>\n\n"
        "New template bhejo ya /cancel karo:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_showlangdroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text("Usage: /showlangdroppedtemplate chinese|vietnamese|turkish|russian"); return
    lang = context.args[0].strip().lower()
    cur = load_config().get("lang_dropped_templates", {}).get(lang, "")
    if not cur:
        await update.message.reply_text(f"{lang.title()} group ke liye koi dropped-call template nahi hai — global/default use ho raha hai."); return
    await update.message.reply_text(
        f"📋 <b>{lang.title()} group — Dropped-Call Template:</b>\n\n<pre>{html.escape(cur)}</pre>",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearlangdroppedtemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].strip().lower() not in ("chinese", "vietnamese", "turkish", "russian"):
        await update.message.reply_text("Usage: /clearlangdroppedtemplate chinese|vietnamese|turkish|russian"); return
    lang = context.args[0].strip().lower()
    c = load_config()
    templates = c.get("lang_dropped_templates", {})
    if lang not in templates:
        await update.message.reply_text(f"{lang.title()} group ke liye koi dropped-call template set nahi tha."); return
    templates.pop(lang, None)
    c["lang_dropped_templates"] = templates
    save_config(c)
    await update.message.reply_text(f"✅ {lang.title()} group ka dropped-call template hata diya. Ab global/default use hoga.")

@owner_only
async def cmd_setkoltemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a custom template for ONE KOL's X range: /setkoltemplate CHANNEL LOW HIGH
    Overrides the global range/tiered/default template for that channel+range only —
    every other KOL keeps using the normal template. Meant for /setkollang-tagged
    KOLs so their own posts can go out in their own language."""
    if len(context.args) < 3:
        await update.message.reply_text(
            "✏️ <b>KOL Range Template Setup</b>\n\n"
            "Usage: <code>/setkoltemplate CHANNEL LOW HIGH</code>\n"
            "Example: <code>/setkoltemplate somekol 2 99</code>\n\n"
            "Phir agla message us KOL ke liye template text bhejo (Chinese/Vietnamese\n"
            "jo bhi chahiye) — variables aur 🔮 /settemplate jaisa hi.\n\n"
            "Dekhne ke liye: /listkoltemplates CHANNEL\n"
            "Hatane ke liye: /delkoltemplate CHANNEL LOW HIGH",
            parse_mode="HTML"
        ); return
    channel = context.args[0].lstrip("@").strip().lower()
    try:
        low = int(context.args[1]); high = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye. Usage: /setkoltemplate CHANNEL 100 499"); return
    if low > high:
        await update.message.reply_text("❌ LOW, HIGH se bara nahi ho sakta."); return
    owner_edit_state[update.effective_user.id] = {
        "state": ST_KOL_RANGE_TMPL, "channel": channel, "low": low, "high": high
    }
    await update.message.reply_text(
        f"✏️ <b>@{channel} — {low}X – {high}X Template</b>\n\n⬇️ Ab is KOL ka template bhejo (text, variables aur 🔮 ke saath):",
        parse_mode="HTML"
    )

@owner_only
async def cmd_listkoltemplates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /listkoltemplates CHANNEL"); return
    channel = context.args[0].lstrip("@").strip().lower()
    c = load_config()
    lang = (c.get("channel_language", {}) or {}).get(channel)
    if lang and lang != "english":
        ranges = c.get("lang_range_templates", {}).get(lang, [])
        header = f"📋 <b>{lang.title()} group (via @{channel}) — Custom Templates</b>\n"
    else:
        ranges = c.get("channel_range_templates", {}).get(channel, [])
        header = f"📋 <b>@{channel} — Custom Templates</b>\n"
    if not ranges:
        await update.message.reply_text(f"@{channel} ke liye koi custom template nahi hai.\n\n/setkoltemplate {channel} LOW HIGH se banao."); return
    lines = [header]
    for r in ranges:
        preview = (r.get("template","") or "")[:80].replace("\n"," ")
        lines.append(f"• <b>{r.get('low')}X – {r.get('high')}X</b>: <code>{preview}...</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_delkoltemplate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /delkoltemplate CHANNEL LOW HIGH"); return
    channel = context.args[0].lstrip("@").strip().lower()
    try:
        low = int(context.args[1]); high = int(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ LOW aur HIGH numbers honi chahiye."); return
    c = load_config()
    lang = (c.get("channel_language", {}) or {}).get(channel)
    store_key = lang if (lang and lang != "english") else channel
    cfg_key = "lang_range_templates" if (lang and lang != "english") else "channel_range_templates"
    all_ranges = c.get(cfg_key, {})
    ranges = all_ranges.get(store_key, [])
    new_ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
    if len(new_ranges) == len(ranges):
        await update.message.reply_text(f"Koi template @{channel} {low}X–{high}X ke liye nahi mila."); return
    if new_ranges:
        all_ranges[store_key] = new_ranges
    else:
        all_ranges.pop(store_key, None)
    c[cfg_key] = all_ranges; save_config(c)
    _scope = f"{lang.title()} group" if (lang and lang != "english") else f"@{channel}"
    await update.message.reply_text(f"✅ {_scope} {low}X–{high}X template hata diya. Ab global/default use hoga.")

@owner_only
async def cmd_editmilestone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /editmilestone <X>"); return
    ms  = context.args[0]; cur = load_config().get("milestone_templates",{}).get(ms,"(using global template)")
    owner_edit_state[update.effective_user.id] = {"state": ST_MILESTONE_TMPL, "milestone": ms}
    await update.message.reply_text(
        f"✏️ <b>Edit {ms}X Template</b>\n\nCurrent:\n<pre>{cur[:600]}</pre>\n\nSend new template:", parse_mode="HTML")

@owner_only
async def cmd_clearmilestone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /clearmilestone <X>"); return
    ms = context.args[0]; c = load_config(); mt = c.get("milestone_templates",{})
    if ms in mt: del mt[ms]; c["milestone_templates"] = mt; save_config(c); await update.message.reply_text(f"✅ {ms}X template removed.")
    else: await update.message.reply_text(f"No custom template for {ms}X.")

@owner_only
async def cmd_listmilestones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ms_templates = load_config().get("milestone_templates",{})
    lines = [f"{'✅' if str(ms) in ms_templates else '⬜'} <b>{ms}X</b>" for ms in get_milestones()]
    await update.message.reply_text("📋 <b>Milestones:</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_setmilestones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"📊 Current: <code>{', '.join(str(x) for x in get_milestones())}</code>\n\n"
            "Set: <code>/setmilestones 2,3,5,10,50</code>\nReset: <code>/setmilestones default</code>",
            parse_mode="HTML"); return
    raw = " ".join(context.args)
    if raw.lower() == "default":
        c = load_config(); c.pop("custom_milestones",None); save_config(c)
        await update.message.reply_text("✅ Default milestones restored."); return
    try:
        milestones = sorted(set(int(x.strip()) for x in raw.replace(","," ").split() if x.strip()))
        cfg_set("custom_milestones", milestones)
        await update.message.reply_text(f"✅ Milestones updated:\n<code>{', '.join(str(x) for x in milestones)}</code>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("⚠️ Format: /setmilestones 2,3,5,10,50")

@owner_only
async def cmd_setmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /setmedia <X>"); return
    owner_edit_state[update.effective_user.id] = {"state": ST_SET_MEDIA, "milestone": context.args[0]}
    await update.message.reply_text(f"📸 Send photo or video for {context.args[0]}X alerts:")

@owner_only
async def cmd_clearmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /clearmedia <X>"); return
    ms = context.args[0]; c = load_config(); media = c.get("milestone_media",{})
    if ms in media: del media[ms]; c["milestone_media"] = media; save_config(c); await update.message.reply_text(f"✅ Media for {ms}X removed.")
    else: await update.message.reply_text(f"No media for {ms}X.")

@owner_only
async def cmd_listmedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media = cfg_get("milestone_media",{})
    if not media: await update.message.reply_text("No media set. Use /setmedia <X>"); return
    lines = [f"✅ <b>{k}X</b> — {v.get('type','photo')}" for k,v in sorted(media.items(),key=lambda x:int(x[0]) if x[0].isdigit() else 0)]
    await update.message.reply_text("🖼️ <b>Milestone Media:</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_editbutton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valid = {"kol_request","promo_hub","fast_track","chat_us","leaderboard","alert_rules"}
    if not context.args or context.args[0] not in valid:
        await update.message.reply_text(
            "Usage: /editbutton <id>\n\nIDs:\n" + "\n".join(f"• <code>{b}</code>" for b in valid), parse_mode="HTML"); return
    btn = context.args[0]; cur = cfg_get("button_texts",{}).get(btn,"(not set)")
    owner_edit_state[update.effective_user.id] = {"state": ST_EDIT_BTN, "button": btn}
    await update.message.reply_text(f"✏️ <b>Edit '{btn}'</b>\n\nCurrent:\n<pre>{cur[:600]}</pre>\n\nSend new text:", parse_mode="HTML")

@owner_only
async def cmd_settext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the info text of a public command: /settext subscribe|history|linkme|linkinfo|submit"""
    if not context.args or context.args[0].lower().lstrip("/") not in PUBLIC_TEXT_CMDS:
        await update.message.reply_text(
            "✏️ <b>Public command ka text set karo</b>\n\n"
            "Usage: <code>/settext CMD</code>\n\n"
            "CMD: " + " · ".join(f"<code>{c}</code>" for c in PUBLIC_TEXT_CMDS) + "\n\n"
            "Example: <code>/settext subscribe</code>\n"
            "Phir naya text bhejo (HTML allowed).\n\n"
            "Dekhne ke liye: <code>/showtext CMD</code>\n"
            "Default par wapas: <code>/cleartext CMD</code>",
            parse_mode="HTML"); return
    cmdkey = context.args[0].lower().lstrip("/")
    cur = (cfg_get("public_texts", {}) or {}).get(cmdkey) or "(default text — abhi set nahi)"
    owner_edit_state[update.effective_user.id] = {"state": ST_SET_PUBLIC_TEXT, "cmd": cmdkey}
    await update.message.reply_text(
        f"✏️ <b>/{cmdkey}</b> ka naya text bhejo:\n\n"
        f"<b>Abhi:</b>\n<pre>{html.escape(cur[:700])}</pre>\n\n"
        f"(/cancel se rok sakte ho)", parse_mode="HTML")


@owner_only
async def cmd_showtext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower().lstrip("/") not in PUBLIC_TEXT_CMDS:
        await update.message.reply_text(
            "Usage: <code>/showtext CMD</code>\n\nCMD: "
            + " · ".join(f"<code>{c}</code>" for c in PUBLIC_TEXT_CMDS), parse_mode="HTML"); return
    cmdkey = context.args[0].lower().lstrip("/")
    defaults = {"subscribe": DEFAULT_SUBSCRIBE_INFO, "history": DEFAULT_HISTORY_INFO,
                "linkme": DEFAULT_LINKME_INFO, "linksmart": DEFAULT_LINKSMART_INFO,
                "linkinfo": DEFAULT_LINKME_INFO,
                "submit": DEFAULT_KOL_REQUEST}
    txt = get_public_text(cmdkey, defaults.get(cmdkey, ""))
    is_custom = bool((cfg_get("public_texts", {}) or {}).get(cmdkey))
    await update.message.reply_text(
        f"📄 <b>/{cmdkey}</b> ({'custom' if is_custom else 'default'}):\n\n" + txt,
        parse_mode="HTML", disable_web_page_preview=True)


@owner_only
async def cmd_cleartext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower().lstrip("/") not in PUBLIC_TEXT_CMDS:
        await update.message.reply_text(
            "Usage: <code>/cleartext CMD</code>\n\nCMD: "
            + " · ".join(f"<code>{c}</code>" for c in PUBLIC_TEXT_CMDS), parse_mode="HTML"); return
    cmdkey = context.args[0].lower().lstrip("/")
    c = load_config(); pt = c.get("public_texts", {})
    pt.pop(cmdkey, None); c["public_texts"] = pt; save_config(c)
    await update.message.reply_text(f"✅ /{cmdkey} ka text default par wapas aa gaya.")


@owner_only
async def cmd_editbtnlabel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: await update.message.reply_text("Usage: /editbtnlabel <id> <label>"); return
    btn, label = context.args[0], " ".join(context.args[1:])
    c = load_config(); lbls = c.get("button_labels",{}); lbls[btn] = label
    c["button_labels"] = lbls; save_config(c)
    await update.message.reply_text(f"✅ {btn} label → {label}")

@owner_only
async def cmd_editstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_edit_state[update.effective_user.id] = {"state": ST_EDIT_START}
    await update.message.reply_text("✏️ <b>Edit /start</b>\n\nSend new text, photo, or video:", parse_mode="HTML")

@owner_only
async def cmd_editcommandtext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_edit_state[update.effective_user.id] = {"state": ST_EDIT_CMD}
    await update.message.reply_text("✏️ <b>Edit /command text</b>\n\nSend new text, photo, or video:", parse_mode="HTML")


@owner_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active editing state."""
    uid = update.effective_user.id
    if uid in wizard_state:
        wizard_state.pop(uid, None)
        await update.message.reply_text("❌ Wizard cancelled.")
        return
    if uid in owner_edit_state and owner_edit_state[uid].get("state"):
        s = owner_edit_state[uid].get("state","")
        owner_edit_state[uid] = {"state": None}
        await update.message.reply_text(f"✅ <b>Cancelled</b> — <code>{s}</code> band ho gaya.", parse_mode="HTML")
    else:
        await update.message.reply_text("ℹ️ Koi active state nahi thi.")

@owner_only
async def cmd_addcmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /addcmd <name>"); return
    name = context.args[0].lstrip("/").lower()
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_CMD2, "cmd_name": name}
    await update.message.reply_text(f"Send response for <code>/{name}</code>:", parse_mode="HTML")

@owner_only
async def cmd_removecmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /removecmd <name>"); return
    name = context.args[0].lstrip("/").lower(); c = load_config(); cmds = c.get("custom_commands",{})
    if name in cmds: del cmds[name]; c["custom_commands"] = cmds; save_config(c); await update.message.reply_text(f"✅ /{name} removed.")
    else: await update.message.reply_text(f"/{name} not found.")

@owner_only
async def cmd_listcmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmds = cfg_get("custom_commands",{})
    if not cmds: await update.message.reply_text("No custom commands."); return
    await update.message.reply_text("📋 <b>Custom Commands:</b>\n\n" + "\n".join(f"• <code>/{k}</code>" for k in cmds), parse_mode="HTML")

@owner_only
async def cmd_testalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ms = int(context.args[0]) if context.args else 2
    await update.message.reply_text(f"📤 Sending test {ms}X alert to {TARGET_CHANNEL}...")
    try:
        await send_alert(context.bot,"TestChannel","1",ms,"SOL","$10K","$20K","TestCA123","TEST")
        await update.message.reply_text(f"✅ Test alert sent!")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: <code>{e}</code>", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════
# ZARD EXTRAS v1  — read-only stats commands + manual premium DM tier.
# Nothing here touches tracking/alert logic. Every function is wrapped so a
# failure can never affect the main bot.
#   Public : /kolstats <channel>  /token <ca>  /topdaily [hours]  /topkols [days]  /premium
#   Premium: /alertme <X>  (DM on every X-milestone >= X, all channels)
#   Owner  : /mediakit [days]  /addpremium <id> <days>  /delpremium <id>  /premiumlist
# ═══════════════════════════════════════════════════════════════════════════
def _ex_ts(call):
    try:
        return datetime.fromisoformat(str(call.get("tracked_since", "")).replace("Z", ""))
    except Exception:
        return None

def _ex_valid(call):
    """A call counts only if entry MC is a real, confirmed number."""
    try:
        return float(call.get("entry_mc", 0) or 0) > 0 and call.get("entry_confirmed", True) is not False
    except Exception:
        return False

def _ex_calls(days=None, hours=None):
    out = []
    now = datetime.utcnow()
    for _k, c in list(tracked_calls.items()):
        try:
            if not isinstance(c, dict) or not _ex_valid(c):
                continue
            ts = _ex_ts(c)
            if (days or hours):
                if ts is None:
                    continue
                span = timedelta(days=days) if days else timedelta(hours=hours)
                if now - ts > span:
                    continue
            out.append(c)
        except Exception:
            continue
    return out

def _ex_x(c):
    try:
        return float(call_peak_ratio(c))
    except Exception:
        return 0.0

def _ex_summary(calls):
    n = len(calls)
    xs = [_ex_x(c) for c in calls]
    wins = [x for x in xs if x >= 2]
    return {
        "n": n,
        "wins": len(wins),
        "rate": (100.0 * len(wins) / n) if n else 0.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "best": max(xs) if xs else 0.0,
    }

def _ex_name(c):
    sym = (c.get("symbol") or "").strip()
    return html.escape(f"${sym.upper()}" if sym else (c.get("ca", "")[:6] + "…"))

def _ex_fx(x):
    try:
        return f"{x:.1f}x" if x < 10 else f"{int(x)}x"
    except Exception:
        return "-"

def _ex_norm_channel(s):
    return str(s or "").strip().lstrip("@").replace("https://t.me/", "").strip("/").lower()

async def cmd_kolstats(update, context):
    try:
        if not context.args:
            await update.message.reply_text("Usage: <code>/kolstats @channel</code>", parse_mode="HTML"); return
        ch = _ex_norm_channel(context.args[0])
        mine = [c for c in _ex_calls() if _ex_norm_channel(c.get("channel")) == ch]
        if not mine:
            await update.message.reply_text(f"⚠️ @{html.escape(ch)} ka koi tracked call nahi mila."); return
        s = _ex_summary(mine)
        mine.sort(key=lambda c: _ex_ts(c) or datetime.min, reverse=True)
        last = "\n".join(f"• {_ex_name(c)} — <b>{_ex_fx(_ex_x(c))}</b>" for c in mine[:5])
        await update.message.reply_text(
            f"📊 <b>@{html.escape(str(mine[0].get('channel') or ch))}</b>\n\n"
            f"🎯 Calls: <b>{s['n']}</b>\n"
            f"✅ 2x+ hit rate: <b>{s['rate']:.0f}%</b> ({s['wins']}/{s['n']})\n"
            f"📈 Avg winner: <b>{_ex_fx(s['avg_win'])}</b> | Best: <b>{_ex_fx(s['best'])}</b>\n\n"
            f"🕒 <b>Last 5 calls</b>\n{last}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_kolstats failed: {e}")

async def cmd_token(update, context):
    try:
        if not context.args:
            await update.message.reply_text("Usage: <code>/token CONTRACT_ADDRESS</code>", parse_mode="HTML"); return
        ca = context.args[0].strip().lower()
        hits = [c for c in _ex_calls() if str(c.get("ca", "")).lower() == ca]
        if not hits:
            await update.message.reply_text("⚠️ Is token ki koi tracked call nahi mili."); return
        hits.sort(key=lambda c: _ex_ts(c) or datetime.max)
        first = hits[0]
        rows = "\n".join(
            f"{i+1}. @{html.escape(str(c.get('channel','?')))} — entry {html.escape(str(c.get('entry_fmt','?')))}"
            f" → <b>{_ex_fx(_ex_x(c))}</b>" for i, c in enumerate(hits[:10]))
        await update.message.reply_text(
            f"🔎 <b>{_ex_name(first)}</b> ({html.escape(str(first.get('chain','?')))})\n\n"
            f"🥇 First call: <b>@{html.escape(str(first.get('channel','?')))}</b>\n"
            f"👥 Total callers: <b>{len(hits)}</b>\n"
            f"🚀 Best X: <b>{_ex_fx(max(_ex_x(c) for c in hits))}</b>\n\n{rows}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_token failed: {e}")

async def cmd_topdaily(update, context):
    try:
        hrs = 24
        if context.args:
            try: hrs = max(1, min(168, int(context.args[0])))
            except Exception: pass
        calls = sorted(_ex_calls(hours=hrs), key=_ex_x, reverse=True)[:10]
        if not calls:
            await update.message.reply_text("Abhi koi call nahi mili."); return
        rows = "\n".join(f"{i+1}. {_ex_name(c)} — <b>{_ex_fx(_ex_x(c))}</b> (@{html.escape(str(c.get('channel','?')))})"
                         for i, c in enumerate(calls))
        await update.message.reply_text(f"🏆 <b>Top calls (last {hrs}h)</b>\n\n{rows}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_topdaily failed: {e}")

async def cmd_topkols(update, context):
    try:
        days = 7
        if context.args:
            try: days = max(1, min(90, int(context.args[0])))
            except Exception: pass
        by = {}
        for c in _ex_calls(days=days):
            by.setdefault(_ex_norm_channel(c.get("channel")), []).append(c)
        rows = []
        for ch, lst in by.items():
            if len(lst) >= 3:
                s = _ex_summary(lst); rows.append((s["rate"], s["n"], ch, s))
        rows.sort(reverse=True)
        if not rows:
            await update.message.reply_text("Abhi itna data nahi (kam az kam 3 calls chahiye)."); return
        out = "\n".join(f"{i+1}. @{html.escape(ch)} — <b>{r:.0f}%</b> hit ({s['wins']}/{n}), best {_ex_fx(s['best'])}"
                        for i, (r, n, ch, s) in enumerate(rows[:10]))
        await update.message.reply_text(f"🥇 <b>Top KOLs (last {days}d, 2x+ hit rate)</b>\n\n{out}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_topkols failed: {e}")

# ── Media kit (owner) — real numbers for advertisers ─────────────────────────
@owner_only
async def cmd_mediakit(update, context):
    try:
        days = 30
        if context.args:
            try: days = max(1, min(365, int(context.args[0])))
            except Exception: pass
        calls = _ex_calls(days=days)
        s = _ex_summary(calls)
        chains = {}
        for c in calls:
            k = str(c.get("chain", "?")).upper(); chains[k] = chains.get(k, 0) + 1
        chain_txt = " | ".join(f"{k} {v}" for k, v in sorted(chains.items(), key=lambda kv: -kv[1])) or "-"
        best = sorted(calls, key=_ex_x, reverse=True)[:5]
        best_txt = "\n".join(f"• {_ex_name(c)} — {_ex_fx(_ex_x(c))}" for c in best) or "-"
        users = len(load_users_dict()); subs = len(load_subscriptions() or []); chs = len(load_channels())
        await update.message.reply_text(
            f"📣 <b>WIZARD SCAN — MEDIA KIT ({days}d)</b>\n\n"
            f"📡 KOL channels tracked: <b>{chs}</b>\n"
            f"👥 Bot users: <b>{users}</b> | 🔔 DM subscribers: <b>{subs}</b>\n"
            f"🎯 Calls tracked: <b>{s['n']}</b>\n"
            f"✅ 2x+ calls: <b>{s['wins']}</b> ({s['rate']:.0f}%)\n"
            f"📈 Avg winner: <b>{_ex_fx(s['avg_win'])}</b> | Best: <b>{_ex_fx(s['best'])}</b>\n"
            f"⛓ Chains: {chain_txt}\n\n🚀 <b>Best calls</b>\n{best_txt}\n\n"
            f"<i>Sab numbers bot ke apne tracked data se hain — screenshot lekar advertisers ko bhej sakte hain.</i>",
            parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_mediakit failed: {e}")

# ── Premium (manual grant by owner) ──────────────────────────────────────────
def _ex_prem_all():
    try: return dict(load_config().get("premium_users", {}) or {})
    except Exception: return {}

def _ex_prem_get(uid):
    rec = _ex_prem_all().get(str(uid))
    if not isinstance(rec, dict): return None
    try:
        if datetime.fromisoformat(rec.get("exp", "")) < datetime.utcnow(): return None
    except Exception:
        return None
    return rec

def is_premium(uid):
    return _ex_prem_get(uid) is not None

@owner_only
async def cmd_addpremium(update, context):
    try:
        if len(context.args) < 2:
            await update.message.reply_text("Usage: <code>/addpremium USER_ID DAYS</code>", parse_mode="HTML"); return
        uid = str(int(context.args[0])); days = max(1, min(3650, int(context.args[1])))
        cfg = load_config(); pu = dict(cfg.get("premium_users", {}) or {})
        cur = pu.get(uid) if isinstance(pu.get(uid), dict) else {}
        base = datetime.utcnow()
        try:
            old = datetime.fromisoformat(cur.get("exp", ""))
            if old > base: base = old
        except Exception: pass
        pu[uid] = {"exp": (base + timedelta(days=days)).isoformat(), "min_x": int(cur.get("min_x", 5) or 5)}
        cfg["premium_users"] = pu; save_config(cfg)
        await update.message.reply_text(f"✅ Premium: <code>{uid}</code> — {days} din (expiry {pu[uid]['exp'][:10]})", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {html.escape(str(e))}")

@owner_only
async def cmd_delpremium(update, context):
    try:
        if not context.args:
            await update.message.reply_text("Usage: <code>/delpremium USER_ID</code>", parse_mode="HTML"); return
        uid = str(int(context.args[0])); cfg = load_config(); pu = dict(cfg.get("premium_users", {}) or {})
        if pu.pop(uid, None) is None:
            await update.message.reply_text("⚠️ Ye user premium list mein nahi."); return
        cfg["premium_users"] = pu; save_config(cfg)
        await update.message.reply_text(f"🗑 Premium hata diya: <code>{uid}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {html.escape(str(e))}")

@owner_only
async def cmd_premiumlist(update, context):
    try:
        rows = []
        for uid, rec in _ex_prem_all().items():
            if isinstance(rec, dict):
                ok = "✅" if _ex_prem_get(uid) else "⌛"
                rows.append(f"{ok} <code>{uid}</code> — {str(rec.get('exp',''))[:10]} (≥{rec.get('min_x',5)}x)")
        await update.message.reply_text("👑 <b>Premium users</b>\n\n" + ("\n".join(rows) if rows else "Koi nahi."), parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_premiumlist failed: {e}")

async def cmd_premium(update, context):
    try:
        rec = _ex_prem_get(update.effective_user.id)
        if rec:
            await update.message.reply_text(
                f"👑 <b>Premium active</b> — {str(rec.get('exp',''))[:10]} tak\n\n"
                f"Abhi DM threshold: <b>{rec.get('min_x',5)}x</b>\n"
                f"Badalne ke liye: <code>/alertme 10</code>", parse_mode="HTML")
        else:
            await update.message.reply_text(
                "👑 <b>Wizard Scan Premium</b>\n\n"
                "• Tamam tracked channels ke X alerts seedha aapke DM mein\n"
                "• Apna X-threshold khud chunein (<code>/alertme 10</code>)\n\n"
                "Premium ke liye owner se rabta karein.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cmd_premium failed: {e}")

async def cmd_alertme(update, context):
    try:
        uid = str(update.effective_user.id)
        if not _ex_prem_get(uid):
            await update.message.reply_text("🔒 Ye sirf premium users ke liye hai. /premium dekhein."); return
        if not context.args:
            await update.message.reply_text("Usage: <code>/alertme 5</code> (kam az kam 2)", parse_mode="HTML"); return
        x = max(2, min(1000, int(context.args[0])))
        cfg = load_config(); pu = dict(cfg.get("premium_users", {}) or {})
        rec = dict(pu.get(uid) or {}); rec["min_x"] = x; pu[uid] = rec; cfg["premium_users"] = pu; save_config(cfg)
        await update.message.reply_text(f"✅ Ab aap ko {x}x aur us se upar ke har alert ka DM milega.")
    except Exception as e:
        await update.message.reply_text(f"❌ {html.escape(str(e))}")

async def _ex_premium_dm(bot, call_key, call, ms, cur_fmt, record_only=False):
    """Fire-and-forget DM to premium users. Never raises, never blocks alerts."""
    try:
        if record_only: return
        pu = _ex_prem_all()
        if not pu: return
        text = (f"🔔 <b>{_ex_name(call)} hit {ms}x</b>\n"
                f"⛓ {html.escape(str(call.get('chain','?')))} | Caller: @{html.escape(str(call.get('channel','?')))}\n"
                f"Entry {html.escape(str(call.get('entry_fmt','?')))} → Now {html.escape(str(cur_fmt))}\n"
                f"<code>{html.escape(str(call.get('ca','')))}</code>")
        for uid, rec in pu.items():
            if not _ex_prem_get(uid): continue
            if ms < int(rec.get("min_x", 5) or 5): continue
            try:
                await bot.send_message(int(uid), text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.warning(f"premium dm failed: {e}")

def register_extras(app):
    for name, fn in (("kolstats", cmd_kolstats), ("token", cmd_token), ("topdaily", cmd_topdaily),
                     ("topkols", cmd_topkols), ("mediakit", cmd_mediakit), ("premium", cmd_premium),
                     ("alertme", cmd_alertme), ("addpremium", cmd_addpremium),
                     ("delpremium", cmd_delpremium), ("premiumlist", cmd_premiumlist)):
        try:
            app.add_handler(CommandHandler(name, fn))
        except Exception as e:
            logger.error(f"register_extras {name} failed: {e}")
