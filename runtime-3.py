# ===== runtime.py — Userbot login, force-join, handle_message, wizards (/fixx), buybot, main() =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).


@owner_only
async def cmd_userbotlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _login_client, _userbot_login
    if not OWNER_API_ID or not OWNER_API_HASH:
        await update.message.reply_text("❌ OWNER_API_ID or OWNER_API_HASH not set."); return
    if userbot_client:
        try: me = await userbot_client.get_me(); await update.message.reply_text(f"✅ Already connected: @{me.username}"); return
        except Exception: pass
    phone = context.args[0].strip() if context.args else OWNER_PHONE
    if not phone: await update.message.reply_text("Usage: /userbotlogin +1234567890"); return
    await _send_otp_now(update, phone)

@owner_only
async def cmd_userbotresend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = context.args[0].strip() if context.args else (_userbot_login.get("phone") or OWNER_PHONE)
    _userbot_login.clear(); await _send_otp_now(update, phone)

@owner_only
async def cmd_userbotlogout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global userbot_client
    if not userbot_client: await update.message.reply_text("⚠️ Not connected."); return
    await userbot_client.disconnect(); userbot_client = None
    try: os.remove(USERBOT_SESSION_FILE)
    except Exception: pass
    await update.message.reply_text("✅ Userbot disconnected.")

async def _register_realtime_after_login(update=None, bot=None):
    """FIX (2026-10-07): after a manual userbot login (/userbotlogin OTP, 2FA,
    /qrlogin, /reconnectuserbot) the Telethon realtime handlers (new-post +
    deleted-post events) were NEVER registered — they only got registered at
    process startup or when the watchdog saw a lost connection. Result: calls
    tracked only by slow polling and no deleted-post DM until the next redeploy."""
    try:
        b = bot
        if b is None and update is not None:
            try: b = update.message.get_bot()
            except Exception: b = None
        b = b or _bot_ref
        if b is None or not userbot_client:
            return
        await setup_realtime_monitoring(b)
        logger.info("✅ Realtime monitoring registered after userbot login")
    except Exception as e:
        logger.warning(f"register realtime after login failed: {e}")

@owner_only
async def cmd_reconnectuserbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reconnect userbot using saved SESSION_STRING — no OTP needed."""
    global userbot_client
    await update.message.reply_text("⏳ Userbot reconnect ho raha hai SESSION_STRING se...")
    try:
        if userbot_client:
            try: await userbot_client.disconnect()
            except Exception: pass
            userbot_client = None
        await init_userbot()
        if userbot_client:
            await _register_realtime_after_login(update=update, bot=context.bot)
            me = await userbot_client.get_me()
            await update.message.reply_text(f"✅ Userbot reconnect ho gaya: @{me.username}\n\nAb premium emojis kaam karein ge.")
        else:
            await update.message.reply_text(
                "❌ Reconnect fail hua.\n\n"
                "SESSION_STRING Replit Secrets mein check karo ya /userbotlogin se login karo.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

@owner_only
async def cmd_forceupdateposts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually force-update posts 135 (trending), 136 (leaderboard), 134 (champions), 3560+3562 (new trending)."""
    msg = await update.message.reply_text("⏳ Posts 135/136/134/3560/3562 update ho rahe hain...")
    results = []
    try:
        ok = await _update_trending_with_premium_emojis(context.bot)
        results.append(f"📊 Post 135 (Trending): {'✅ Updated' if ok else '❌ Failed'}")
    except Exception as e:
        results.append(f"📊 Post 135 (Trending): ❌ {e}")
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        results.append(f"🏆 Post 136 (Leaderboard): {'✅ Updated' if ok else '❌ Failed'}")
    except Exception as e:
        results.append(f"🏆 Post 136 (Leaderboard): ❌ {e}")
    try:
        ok = await _update_champions_with_premium_emojis(context.bot)
        results.append(f"🔮 Post 134 (Champions): {'✅ Updated' if ok else '❌ Failed'}")
    except Exception as e:
        results.append(f"🔮 Post 134 (Champions): ❌ {e}")
    try:
        res2 = await _update_trending2_posts(context.bot)
        ok1  = res2.get(POST_TRENDING_1, False)
        ok2  = res2.get(POST_TRENDING_2, False)
        results.append(f"📈 Post {POST_TRENDING_1} (SOL/ETH/BSC): {'✅ Updated' if ok1 else '❌ Failed'}")
        results.append(f"📈 Post {POST_TRENDING_2} (RH/BASE/TON): {'✅ Updated' if ok2 else '❌ Failed'}")
    except Exception as e:
        results.append(f"📈 Posts {POST_TRENDING_1}/{POST_TRENDING_2}: ❌ {e}")
    await msg.edit_text(
        "📡 <b>Force Update Complete:</b>\n\n" + "\n".join(results),
        parse_mode="HTML"
    )

@owner_only
async def cmd_markseen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark all current posts in all tracked channels as seen (prevents old post tracking)."""
    msg = await update.message.reply_text("⏳ Sabhi tracked channels ki existing posts mark ho rahi hain...")
    channels = load_channels()
    total = 0
    for ch in channels:
        try:
            posts = await fetch_channel_posts(ch)
            for post in posts:
                seen_message_ids[ch].add(post["id"])
            total += len(posts)
        except Exception as e:
            logger.warning(f"markseen failed for @{ch}: {e}")
    _save_seen()
    await msg.edit_text(
        f"✅ <b>Done!</b>\n\n{len(channels)} channels mein {total} posts mark ho gayi hain.\n"
        f"Ab sirf naye posts track honge.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_userbotcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not userbot_client: await update.message.reply_text("❌ Userbot not connected."); return
    try:
        me = await userbot_client.get_me()
        await update.message.reply_text(f"✅ Userbot: @{me.username} ({me.id})")
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

@owner_only
async def cmd_qrlogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """QR code login — no OTP needed. Scan with Telegram app."""
    global userbot_client
    if not OWNER_API_ID or not OWNER_API_HASH:
        await update.message.reply_text("❌ USERBOT_API_ID/HASH not set."); return
    if userbot_client:
        try:
            me = await userbot_client.get_me()
            await update.message.reply_text(f"✅ Already connected: @{me.username}"); return
        except Exception: pass

    msg = await update.message.reply_text(
        "📲 <b>QR Code Login shuru ho raha hai...</b>\n\n"
        "Abhi aapke Telegram par QR code aayega.\n"
        "Telegram → Settings → Devices → Link Desktop Device → Scan karo",
        parse_mode="HTML"
    )
    asyncio.create_task(_qrlogin_task(update.effective_chat.id, msg.message_id, context))

async def _qrlogin_task(chat_id, status_msg_id, context):
    global userbot_client
    import base64, time
    from datetime import datetime
    bot = context.bot
    link_msg_id = None
    client = None
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.auth import ExportLoginTokenRequest, AcceptLoginTokenRequest
        from telethon.tl.types import auth as tl_auth

        client = TelegramClient(StringSession(), OWNER_API_ID, OWNER_API_HASH)
        await client.connect()

        attempt = 0
        while True:
            attempt += 1
            try:
                result = await client(ExportLoginTokenRequest(
                    api_id=OWNER_API_ID, api_hash=OWNER_API_HASH, except_ids=[]
                ))
            except Exception as e:
                await bot.send_message(chat_id, f"❌ Error: {e}")
                await client.disconnect(); return

            if isinstance(result, tl_auth.LoginTokenMigrateTo):
                await client._switch_dc(result.dc_id)
                try: await client(AcceptLoginTokenRequest(token=result.token))
                except Exception: pass
                continue

            if isinstance(result, tl_auth.LoginTokenSuccess):
                break

            token_b64 = base64.urlsafe_b64encode(result.token).decode()
            tg_link   = f"tg://login?token={token_b64}"
            exp       = result.expires
            exp_ts    = exp.timestamp() if isinstance(exp, datetime) else float(exp)

            text = (
                f"🔑 <b>Login Link — {attempt}. Koshish</b>\n\n"
                "👇 <b>PHONE PAR YEH LINK TAP KARO:</b>\n\n"
                f"<a href='{tg_link}'>✅ TAP HERE TO LOGIN</a>\n\n"
                "Tap karte hi Telegram ek popup dikhayega:\n"
                "<b>\"Allow login? Confirm\"</b> — Confirm dabao, ho gaya!\n\n"
                f"⏳ <i>Auto-renew hoga — koi rush nahi</i>"
            )
            try:
                if link_msg_id:
                    await bot.edit_message_text(chat_id=chat_id, message_id=link_msg_id,
                                                text=text, parse_mode="HTML",
                                                disable_web_page_preview=True)
                else:
                    sent = await bot.send_message(chat_id, text, parse_mode="HTML",
                                                  disable_web_page_preview=True)
                    link_msg_id = sent.message_id
            except Exception: pass

            # Poll until scanned or expired
            scanned = False
            while time.time() < exp_ts - 1:
                await asyncio.sleep(2)
                try:
                    chk = await client(ExportLoginTokenRequest(
                        api_id=OWNER_API_ID, api_hash=OWNER_API_HASH, except_ids=[]
                    ))
                    if isinstance(chk, tl_auth.LoginTokenSuccess):
                        scanned = True; break
                    if isinstance(chk, tl_auth.LoginTokenMigrateTo):
                        await client._switch_dc(chk.dc_id)
                        try:
                            r2 = await client(AcceptLoginTokenRequest(token=chk.token))
                            if hasattr(r2, 'user'): scanned = True; break
                        except Exception: pass
                except Exception: pass
            if scanned: break

        # ── Success ──
        me = await client.get_me()
        session_str = client.session.save()
        userbot_client = client
        save_userbot_session(session_str)
        await _register_realtime_after_login(bot=bot)

        try:
            if link_msg_id: await bot.delete_message(chat_id, link_msg_id)
        except Exception: pass

        await bot.send_message(
            chat_id,
            f"✅ <b>Login ho gaya!</b> @{me.username or me.first_name}\n\n"
            f"🎉 Userbot active — premium emojis ON!\n\n"
            f"📋 <b>SESSION_STRING copy karo (Replit → Secrets mein save karo):</b>\n\n"
            f"<code>{session_str}</code>",
            parse_mode="HTML"
        )
        logger.info(f"✅ QR/Link Login success: @{me.username}")

    except Exception as e:
        logger.error(f"qrlogin task error: {e}")
        try: await bot.send_message(chat_id, f"❌ Login failed: {e}")
        except Exception: pass
        if client:
            try: await client.disconnect()
            except Exception: pass

LOGIN_STATE_FILE  = _dp("login_state.json")   # persists phone + hash across restarts
LOGIN_SESSION_NAME = "temp_login"        # SQLite session file (not StringSession)

def _save_login_state(phone, phone_code_hash):
    import json
    with open(LOGIN_STATE_FILE, "w") as f:
        json.dump({"phone": phone, "phone_code_hash": phone_code_hash}, f)

def _load_login_state():
    import json
    try:
        with open(LOGIN_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _clear_login_state():
    try: os.remove(LOGIN_STATE_FILE)
    except Exception: pass
    # Remove SQLite session file too
    for ext in ("", ".session"):
        try: os.remove(LOGIN_SESSION_NAME + ext)
        except Exception: pass

async def _get_or_create_login_client():
    """Return existing _login_client or recreate from SQLite session file."""
    global _login_client
    from telethon import TelegramClient
    if _login_client and _login_client.is_connected():
        return _login_client
    # Recreate from persistent SQLite session
    _login_client = TelegramClient(LOGIN_SESSION_NAME, OWNER_API_ID, OWNER_API_HASH)
    await _login_client.connect()
    return _login_client

async def _send_otp_now(update, phone):
    global _login_client, _userbot_login
    await update.message.reply_text(f"📨 Sending OTP to <code>{phone}</code>...", parse_mode="HTML")
    try:
        from telethon import TelegramClient
        if _login_client:
            try: await _login_client.disconnect()
            except Exception: pass
        # Use SQLite session (not StringSession) — survives bot restarts
        _login_client = TelegramClient(LOGIN_SESSION_NAME, OWNER_API_ID, OWNER_API_HASH)
        await _login_client.connect()
        result = await _login_client.send_code_request(phone)
        # Persist to file — survives restarts
        _save_login_state(phone, result.phone_code_hash)
        _userbot_login.update({"state": ST_USERBOT_OTP, "phone": phone, "phone_code_hash": result.phone_code_hash})
        await update.message.reply_text(
            "✅ <b>OTP bheja gaya!</b>\n\n"
            "📌 <b>Code kahan milega?</b>\n"
            "Telegram app kholo → <b>\"Telegram\"</b> wali official chat dhundo "
            "(Contacts mein sabse upar hoti hai) → wahan 5-digit code hoga.\n\n"
            "<i>(SMS nahi aata — Telegram apni app ke andar hi code bhejta hai)</i>\n\n"
            "⬇️ Woh 5-digit code yahan bhejo:",
            parse_mode="HTML"
        )
    except Exception as e:
        err = str(e)
        _userbot_login.clear()
        _clear_login_state()
        if "FLOOD_WAIT" in err:
            wait = ''.join(filter(str.isdigit, err)) or "kuch"
            await update.message.reply_text(
                f"⏳ <b>Telegram ne temporarily block kiya hai OTP ke liye.</b>\n\n"
                f"<b>{wait} seconds</b> baad dobara try karo: /userbotresend",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Failed: <code>{e}</code>", parse_mode="HTML")

async def _handle_userbot_login_flow(update, msg_text):
    global userbot_client, _login_client, _userbot_login
    state = _userbot_login.get("state")

    # If bot restarted, reload state from file and recreate client
    if not _userbot_login.get("phone_code_hash"):
        saved = _load_login_state()
        if saved.get("phone_code_hash"):
            _userbot_login.update({"state": ST_USERBOT_OTP,
                                   "phone": saved["phone"],
                                   "phone_code_hash": saved["phone_code_hash"]})
            state = ST_USERBOT_OTP

    if state == ST_USERBOT_OTP:
        otp = msg_text.strip().replace(" ","")
        try:
            client = await _get_or_create_login_client()
            await client.sign_in(phone=_userbot_login["phone"], code=otp,
                                 phone_code_hash=_userbot_login["phone_code_hash"])
            from telethon.sessions import StringSession
            session_str = StringSession.save(client.session)
            save_userbot_session(session_str)
            userbot_client = client; _login_client = None
            await _register_realtime_after_login(update=update)
            _userbot_login.clear(); _clear_login_state()
            me = await userbot_client.get_me()
            await update.message.reply_text(
                f"🎉 <b>Userbot connected: @{me.username}!</b>\n\n"
                f"✅ Premium emojis ab kaam karengy!",
                parse_mode="HTML"
            )
        except Exception as e:
            err = str(e)
            if "SessionPasswordNeeded" in err or "two-step" in err.lower():
                _userbot_login["state"] = ST_USERBOT_2FA
                await update.message.reply_text("🔐 2FA enabled hai. Cloud password daalo:")
            elif "PHONE_CODE_EXPIRED" in err:
                _userbot_login.clear(); _clear_login_state()
                await update.message.reply_text(
                    "⏰ <b>Code expire ho gaya.</b>\n\n"
                    "👉 Dobara /userbotresend bhejo — naya code aayega.",
                    parse_mode="HTML"
                )
            elif "PHONE_CODE_INVALID" in err:
                await update.message.reply_text("❌ Wrong code — dobara try karo.")
            else:
                _userbot_login.clear(); _clear_login_state()
                await update.message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")
    elif state == ST_USERBOT_2FA:
        try:
            client = await _get_or_create_login_client()
            await client.sign_in(password=msg_text.strip())
            from telethon.sessions import StringSession
            session_str = StringSession.save(client.session)
            save_userbot_session(session_str)
            userbot_client = client; _login_client = None
            await _register_realtime_after_login(update=update)
            _userbot_login.clear(); _clear_login_state()
            me = await userbot_client.get_me()
            await update.message.reply_text(
                f"🎉 <b>Userbot connected: @{me.username}!</b>\n\n"
                f"✅ Premium emojis ab kaam karengy!",
                parse_mode="HTML"
            )
        except Exception as e:
            _userbot_login.clear(); _clear_login_state()
            await update.message.reply_text(f"❌ 2FA error: <code>{e}</code>", parse_mode="HTML")

# ─── Message handler ──────────────────────────────────────────────────────────
def _is_private_update(update) -> bool:
    """True only for 1-to-1 DM chats with the bot."""
    ch = update.effective_chat
    return bool(ch and ch.type == "private")


# ─── Force-join gate (channel membership required) ────────────────────────────
FORCE_JOIN_TEXT = (
    "<b>Before using any command, you must be a member of the channel. "
    "Please subscribe to @WizardScan first.</b>"
)
FORCE_JOIN_KB = InlineKeyboardMarkup([
    [_pkb("🔮 Main Channel 🔮", url=TG_CHANNEL_LINK)],
    [_pkb("🔮 Done Wizrad 🔮", callback_data="force_join_done")],
])
FORCE_JOIN_FAIL_TEXT = (
    "<b>You have not joined the channel yet, but you clicked Done Wizrad.</b>\n\n"
    "Please join @WizardScan first and then click the Done Wizrad button again."
)
FORCE_JOIN_OK_TEXT = (
    "<b>🔮 Verified! Thank you for joining @WizardScan.</b>\n\n"
    "You can now use all bot commands. Send /command to open the Command Center."
)
_join_ok_cache = {}          # uid -> ts of last successful membership check
JOIN_CACHE_TTL = 300         # seconds

async def _userbot_is_channel_member(uid: int):
    """Userbot (Telethon) se membership check — jab bot API fail kare
    (bot channel me admin na ho, ya 'member list inaccessible')."""
    try:
        if not (userbot_client and userbot_client.is_connected()):
            return None
        from telethon.tl.functions.channels import GetParticipantRequest
        try:
            await userbot_client(GetParticipantRequest(TARGET_CHANNEL, int(uid)))
            return True
        except Exception as e:
            if "not a participant" in str(e).lower() or "USER_NOT_PARTICIPANT" in str(e):
                return False
            return None
    except Exception:
        return None


_join_warn_ts = {"ts": 0.0}      # owner ko config-warning bar bar na jaye

async def _membership_status(bot, uid: int):
    """True  = pakka member hai
       False = pakka member NAHI hai (Telegram ne left/kicked bataya)
       None  = check hi nahi ho saka (bot admin nahi / API error / flood)"""
    last_exc = None
    for attempt in range(3):
        try:
            cm = await bot.get_chat_member(TARGET_CHANNEL, uid)
            status = str(getattr(cm, "status", "") or "").lower()
            if status in ("member", "administrator", "creator", "owner"):
                return True
            if status == "restricted":
                # restricted user tab tak member hai jab tak is_member True hai
                return bool(getattr(cm, "is_member", True))
            if status in ("left", "kicked", "banned"):
                return False
            # unknown/empty status → transient glitch, retry
        except Exception as e:
            last_exc = e
            m = str(e).lower()
            # Ye errors ka matlab "user member nahi" NAHI hota — ye config/API
            # masle hain, in par user ko block karna hi asli bug tha.
            if any(k in m for k in ("member list is inaccessible", "chat not found",
                                    "not enough rights", "chat admin required",
                                    "user not found", "forbidden", "chat_admin_required")):
                break
        if attempt < 2:
            await asyncio.sleep(1.0)
    if last_exc:
        logger.warning(f"membership check inconclusive for {uid}: {last_exc}")
    return None


async def _warn_owner_membership_broken(bot, err_hint=""):
    """Agar bot @WizardScan me admin nahi hai to membership check kabhi
    reliable nahi hoga — owner ko (ghante me ek baar) bata do."""
    try:
        if time.time() - _join_warn_ts["ts"] < 3600:
            return
        _join_warn_ts["ts"] = time.time()
        for oid in OWNER_IDS:
            try:
                await bot.send_message(
                    oid,
                    "⚠️ <b>Force-join check kaam nahi kar raha</b>\n\n"
                    f"Bot {TARGET_CHANNEL} me membership verify nahi kar pa raha "
                    "(aksar iska matlab: bot us channel me <b>admin</b> nahi hai).\n\n"
                    "Abhi ke liye users ko block nahi kiya ja raha (warna asli members "
                    "bhi block ho jate the). Bot ko channel me admin bana do — "
                    "gate phir se sakhti se chalne lagega."
                    + (f"\n\n<code>{html.escape(str(err_hint)[:200])}</code>" if err_hint else ""),
                    parse_mode="HTML")
            except Exception:
                pass
    except Exception:
        pass


async def _is_channel_member(bot, uid: int, fresh: bool = False) -> bool:
    ts = _join_ok_cache.get(uid)
    if not fresh and ts and (time.time() - ts) < JOIN_CACHE_TTL:
        return True

    st = await _membership_status(bot, uid)

    # Bot API se pata na chala → userbot (Telethon) se confirm karo
    if st is None:
        ub = await _userbot_is_channel_member(uid)
        if ub is not None:
            st = ub

    if st is True:
        _join_ok_cache[uid] = time.time()
        return True
    if st is False:
        _join_ok_cache.pop(uid, None)
        return False

    # ── Inconclusive ──────────────────────────────────────────────────────
    # Pehle yahan "False" return hota tha — isi wajah se channel join karne
    # ke baad bhi bot kehta tha "aap ne join nahi kiya". Ab hum user ko block
    # nahi karte; sirf owner ko warning jati hai ke bot ko channel me admin
    # banaye taake check dobara reliable ho jaye.
    await _warn_owner_membership_broken(bot)
    _join_ok_cache[uid] = time.time()
    return True

# Owner requirement: force-join ON — user ko @WizardScan join karna zaroori
# hai, warna koi command (except /start) use nahi ho sakta.
FORCE_JOIN_ENABLED = True

async def force_join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force-join gate (disabled — membership ab required nahi hai)."""
    if not FORCE_JOIN_ENABLED:
        return
    try:
        if not _is_private_update(update):
            return
        user = update.effective_user
        if not user or user.is_bot:
            return
        uid = user.id
        if uid in OWNER_IDS:
            return

        q = update.callback_query
        if q is not None:
            if q.data == "force_join_done":
                try: await q.answer()
                except Exception: pass
                _join_ok_cache.pop(uid, None)
                ok = await _is_channel_member(context.bot, uid, fresh=True)
                if not ok:
                    # Telegram ko join propagate hone do, phir dobara dekho.
                    await asyncio.sleep(1.5)
                    ok = await _is_channel_member(context.bot, uid, fresh=True)
                if ok:
                    _join_ok_cache[uid] = time.time()
                    await q.message.reply_text(FORCE_JOIN_OK_TEXT, parse_mode="HTML")
                else:
                    await q.message.reply_text(FORCE_JOIN_FAIL_TEXT, parse_mode="HTML",
                                               reply_markup=FORCE_JOIN_KB)
                raise ApplicationHandlerStop
            if await _is_channel_member(context.bot, uid):
                return
            try: await q.answer()
            except Exception: pass
            await q.message.reply_text(FORCE_JOIN_TEXT, parse_mode="HTML",
                                       reply_markup=FORCE_JOIN_KB)
            raise ApplicationHandlerStop

        msg = update.effective_message
        text = (getattr(msg, "text", "") or "") if msg else ""
        if text.startswith("/"):
            cmd = text.split()[0].lstrip("/").split("@")[0].lower()
            if cmd == "start":
                return
        if await _is_channel_member(context.bot, uid):
            return
        if msg:
            await msg.reply_text(FORCE_JOIN_TEXT, parse_mode="HTML",
                                 reply_markup=FORCE_JOIN_KB)
        raise ApplicationHandlerStop
    except ApplicationHandlerStop:
        raise
    except Exception as e:
        logger.warning(f"force_join_gate: {e}")


async def on_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User channel join/leave kare to membership cache turant update ho —
    phir gate use dobara join karne ko nahi kehta."""
    try:
        cmu = update.chat_member
        if not cmu:
            return
        chat = cmu.chat
        target = str(TARGET_CHANNEL).lstrip("@").lower()
        if str(chat.id) != target and (chat.username or "").lower() != target:
            return
        uid = cmu.new_chat_member.user.id
        status = getattr(cmu.new_chat_member, "status", "")
        if status in ("member", "administrator", "creator", "owner"):
            _join_ok_cache[uid] = time.time()
            logger.info(f"join-gate: user {uid} joined @{target} (cache updated)")
        else:
            _join_ok_cache.pop(uid, None)
    except Exception as e:
        logger.debug(f"on_channel_member_update: {e}")


async def block_non_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hard kill-switch: the bot does NOTHING in groups / lounges / supergroups.
    Registered in handler group -1 so it runs before every other handler and
    stops the update chain completely (no commands, no CA tracking, no replies)."""
    if _is_private_update(update):
        return
    # Buy Bot feature ke liye sirf ye group updates allowed hain:
    #  • bot ko group me add/remove karna (my_chat_member)
    #  • /buybot, /stopbuybot, /buybothelp commands
    if getattr(update, "my_chat_member", None):
        return
    _m = update.effective_message
    _t = (getattr(_m, "text", "") or "") if _m else ""
    # Buy Bot feature permanently removed — no group command is allowed.
    raise ApplicationHandlerStop


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    msg = update.message
    if not msg or not uid: return
    # Bot is DM-only — ignore groups/lounges entirely
    if not _is_private_update(update): return

    # Custom commands
    if msg.text and msg.text.startswith("/"):
        cmd_name = msg.text.split()[0].lstrip("/").split("@")[0].lower()
        custom   = cfg_get("custom_commands",{})
        if cmd_name in custom:
            await msg.reply_text(custom[cmd_name], parse_mode="HTML"); return

    # ── Button wizards (missed call / X fix / buy bot) ───────────────────────
    if uid in wizard_state:
        try:
            if (msg.photo or msg.video or msg.animation or msg.document) and \
                    await wizard_handle_media(update, context, msg):
                return
            if msg.text and await wizard_handle_text(update, context, msg):
                return
        except Exception as e_wiz:
            logger.error(f"wizard text error: {e_wiz}")
            await msg.reply_text("⚠️ Something went wrong. Send /cancel and start again.")
            return

    # Userbot login flow
    if uid in OWNER_IDS and _userbot_login.get("state") in (ST_USERBOT_OTP, ST_USERBOT_2FA):
        if msg.text: await _handle_userbot_login_flow(update, msg.text)
        return

    # Owner edit states
    if uid in OWNER_IDS and uid in owner_edit_state and owner_edit_state[uid].get("state"):
        si = owner_edit_state[uid]; state = si["state"]

        if state == ST_BULK_LANG:
            lang = si["lang"]
            text_val = (msg.text or "").strip()
            if not text_val:
                await msg.reply_text("⚠️ Username bhejo (ya /done)."); return
            if text_val.lower() in ("/done", "done"):
                owner_edit_state[uid] = {"state": None}
                flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
                await msg.reply_text(
                    f"✅ {flag} <b>{lang.title()} tagging khatam!</b> Total {si.get('count',0)} KOL(s) tag hue.\n\n"
                    f"/listkollang se list dekh sakte ho.",
                    parse_mode="HTML"
                ); return
            channel = text_val.lstrip("@").strip().lower()
            if not channel:
                await msg.reply_text("⚠️ Sahi username bhejo (ya /done)."); return
            c = load_config()
            langs = c.get("channel_language", {})
            langs[channel] = lang
            c["channel_language"] = langs
            save_config(c)
            si["count"] = si.get("count", 0) + 1
            owner_edit_state[uid] = si
            flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}[lang]
            await msg.reply_text(
                f"✅ @{channel} {flag} <b>{lang}</b> ho gaya ({si['count']} tag hue abhi tak).\n\n"
                f"Agle KOL ka username bhejo, ya /done bhejo khatam karne ke liye:",
                parse_mode="HTML"
            ); return

        if state == ST_LANG_RANGE_TMPL:
            lang = si["lang"]; low = si["low"]; high = si["high"]
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            c = load_config()
            all_ranges = c.get("lang_range_templates", {})
            ranges = all_ranges.get(lang, [])
            ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
            ranges.append({"low": low, "high": high, "template": text_val})
            all_ranges[lang] = ranges
            c["lang_range_templates"] = all_ranges
            save_config(c)
            owner_edit_state[uid] = {"state": None}
            _cnt = _lang_kol_count(lang, c)
            flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}.get(lang, "")
            await msg.reply_text(
                f"✅ <b>{flag} {lang.title()} group — {low}X–{high}X template saved!</b>\n\n"
                f"{_cnt} KOL(s) ke posts abhi se is text se jaenge, jab tak /dellangtemplate {lang} {low} {high} se hatao.",
                parse_mode="HTML"
            ); return

        if state == ST_LANG_DROPPED_TMPL:
            lang = si["lang"]
            owner_edit_state[uid] = {"state": None}
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            c = load_config()
            templates = c.get("lang_dropped_templates", {})
            templates[lang] = text_val
            c["lang_dropped_templates"] = templates
            save_config(c)
            _cnt = _lang_kol_count(lang, c)
            flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}.get(lang, "")
            await msg.reply_text(
                f"✅ <b>{flag} {lang.title()} group — Dropped-Call Template Saved!</b>\n\n"
                f"{_cnt} KOL(s) mein se koi bhi pehli baar call kare to isi text se post hogi.\n"
                f"/showlangdroppedtemplate {lang} se dekh sakte ho.",
                parse_mode="HTML"
            ); return

        if state == ST_SETTEMPLATE_EM:
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo (sirf text, koi media nahi)."); return
            entities = list(msg.entities or msg.caption_entities or [])
            # Extract custom premium emoji IDs in order of appearance
            custom_ents = sorted(
                [e for e in entities if e.type == "custom_emoji"],
                key=lambda e: e.offset
            )
            custom_emoji_ids = [e.custom_emoji_id for e in custom_ents]
            # Replace each custom emoji in the text with 🔮 (using UTF-16 offsets)
            processed_text = text_val
            if custom_ents:
                text_utf16 = text_val.encode('utf-16-le')
                parts = []
                prev = 0
                for ent in custom_ents:
                    s = ent.offset * 2
                    e_ = (ent.offset + ent.length) * 2
                    parts.append(text_utf16[prev:s])
                    parts.append('🔮'.encode('utf-16-le'))
                    prev = e_
                parts.append(text_utf16[prev:])
                processed_text = b''.join(parts).decode('utf-16-le')
            # Save template text (with 🔮 placeholders)
            cfg_set("alert_template", processed_text)
            # Save captured emoji IDs
            if custom_emoji_ids:
                c = load_config()
                emoji_map = {f"pos{i+1}": eid for i, eid in enumerate(custom_emoji_ids)}
                c["alert_emoji_ids"] = emoji_map
                # pos1 = chain emoji (used for all chains unless overridden)
                chain_ids = c.get("chain_emoji_ids", {})
                for chain in ["sol", "eth", "bsc", "base"]:
                    if not chain_ids.get(chain):
                        chain_ids[chain] = custom_emoji_ids[0]
                c["chain_emoji_ids"] = chain_ids
                save_config(c)
            owner_edit_state[uid] = {"state": None}
            emoji_note = (
                f"\n\n🎯 <b>{len(custom_emoji_ids)} premium emoji ID(s) captured aur save ho gayi!</b>\n" +
                "\n".join(f"  Pos{i+1}: <code>{eid}</code>" for i, eid in enumerate(custom_emoji_ids))
            ) if custom_emoji_ids else "\n\n⚠️ Koi premium emoji detect nahi hua — pehle se set emoji IDs use honge."
            await msg.reply_text(
                f"✅ <b>Template saved!</b>{emoji_note}\n\n"
                f"<b>Saved template:</b>\n<pre>{processed_text[:800]}</pre>",
                parse_mode="HTML"
            ); return

        if state == ST_TEMPLATE:
            text_val = msg.text or msg.caption
            if text_val:
                cfg_set("alert_template", text_val)
            if msg.photo or msg.video:
                fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
                ftype = "photo" if msg.photo else "video"
                c = load_config(); media = c.get("milestone_media",{}); media["global"] = {"type":ftype,"file_id":fid}
                c["milestone_media"] = media; save_config(c)
                _clear_pending_media(None)
            if not text_val and not (msg.photo or msg.video):
                await msg.reply_text("⚠️ Template text bhejo (ya photo/video with caption)."); return
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text("✅ @WizardScan template saved!\n\nPreview ke liye /showtemplate use karo."); return

        elif state == ST_RANGE_TMPL:
            low = si["low"]; high = si["high"]
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            c = load_config()
            ranges = c.get("range_templates", [])
            ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
            ranges.append({"low": low, "high": high, "template": text_val})
            c["range_templates"] = ranges
            save_config(c)
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(
                f"✅ <b>{low}X–{high}X template saved!</b>\n\nAb yeh range hamesha isi text ko use karega, "
                f"jab tak /delrangetemplate {low} {high} se hatao.",
                parse_mode="HTML"
            ); return

        elif state == ST_KOL_RANGE_TMPL:
            channel = si["channel"]; low = si["low"]; high = si["high"]
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            c = load_config()
            lang = (c.get("channel_language", {}) or {}).get(channel)
            if lang and lang != "english":
                all_ranges = c.get("lang_range_templates", {})
                store_key = lang
            else:
                all_ranges = c.get("channel_range_templates", {})
                store_key = channel
            ranges = all_ranges.get(store_key, [])
            ranges = [r for r in ranges if not (int(r.get("low",-1)) == low and int(r.get("high",-1)) == high)]
            ranges.append({"low": low, "high": high, "template": text_val})
            all_ranges[store_key] = ranges
            if lang and lang != "english":
                c["lang_range_templates"] = all_ranges
            else:
                c["channel_range_templates"] = all_ranges
            save_config(c)
            owner_edit_state[uid] = {"state": None}
            if lang and lang != "english":
                _grp_count = len([k for k, v in (c.get("channel_language", {}) or {}).items() if v == lang])
                flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}.get(lang, "")
                await msg.reply_text(
                    f"✅ <b>{flag} {lang.title()} group — {low}X–{high}X template saved!</b>\n\n"
                    f"Ye {_grp_count} {lang}-tagged KOL(s) ke posts pe abhi se apply hoga (sirf @{channel} pe nahi — "
                    f"sab {lang} KOLs pe), jab tak /delkoltemplate {channel} {low} {high} se hatao.",
                    parse_mode="HTML"
                ); return
            await msg.reply_text(
                f"✅ <b>@{channel} — {low}X–{high}X template saved!</b>\n\nAb sirf isi KOL ke is range ke posts "
                f"is text ko use karenge, jab tak /delkoltemplate {channel} {low} {high} se hatao.",
                parse_mode="HTML"
            ); return

        elif state == ST_LEADERBOARD_TMPL:
            text_val = msg.text or msg.caption
            if not text_val: await msg.reply_text("⚠️ Template text bhejo."); return
            cfg_set("leaderboard_template", text_val.strip())
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text("✅ Leaderboard template saved!\n/updateleaderboard se update karo."); return

        elif state == ST_MILESTONE_TMPL:
            ms = si["milestone"]; text_val = msg.text or msg.caption
            if text_val:
                if not _is_real_template(text_val):
                    await msg.reply_text(
                        "⚠️ Yeh template nahi lagta (sirf number ya bohot chhota text).\n"
                        "Media save ho jayega, magar text template save nahi kiya — warna alert me sirf yehi likha aata.\n"
                        "Pura template bhejo (jaise /settemplate me), ya /clearmilestone " + str(ms) + " karo."
                    )
                else:
                    c = load_config(); mt = c.get("milestone_templates",{}); mt[ms] = text_val; c["milestone_templates"] = mt; save_config(c)
            if msg.photo or msg.video:
                fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
                ftype = "photo" if msg.photo else "video"
                c = load_config(); media = c.get("milestone_media",{}); media[ms] = {"type":ftype,"file_id":fid}; c["milestone_media"] = media; save_config(c)
                _clear_pending_media(ms)
            if not text_val and not (msg.photo or msg.video): await msg.reply_text("⚠️ Send template text or media."); return
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ {ms}X template saved!"); return

        elif state == ST_SET_MEDIA:
            ms = si["milestone"]
            if msg.photo:   fid, ftype = msg.photo[-1].file_id, "photo"
            elif msg.video: fid, ftype = msg.video.file_id, "video"
            else:           await msg.reply_text("⚠️ Send photo or video."); return
            c = load_config(); media = c.get("milestone_media",{}); media[ms] = {"type":ftype,"file_id":fid}
            if msg.caption and _is_real_template(msg.caption):
                mt = c.get("milestone_templates",{}); mt[ms] = msg.caption; c["milestone_templates"] = mt
            c["milestone_media"] = media; save_config(c)
            _clear_pending_media(ms)
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ {ftype.capitalize()} saved for {ms}X!"); return

        elif state == ST_SET_HS_MEDIA:
            color = si["color"]
            if msg.photo:   fid, ftype = msg.photo[-1].file_id, "photo"
            elif msg.video: fid, ftype = msg.video.file_id, "video"
            else:           await msg.reply_text("⚠️ Send photo or video."); return
            c = load_config(); media = c.get("hot_streak_media", {})
            media[color] = {"type": ftype, "file_id": fid}
            c["hot_streak_media"] = media; save_config(c)
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ {ftype.capitalize()} saved for {color.upper()} Hot Streak Alert!"); return

        elif state == ST_EDIT_BTN:
            btn = si["button"]
            if msg.text:
                c = load_config(); bt = c.get("button_texts",{}); bt[btn] = msg.text; c["button_texts"] = bt; save_config(c)
                owner_edit_state[uid] = {"state": None}
                await msg.reply_text(f"✅ '{btn}' text updated!"); return

        elif state == ST_EDIT_START:
            c = load_config(); saved = []; text_val = msg.text or msg.caption
            if text_val: c["start_text"] = text_val; saved.append("text")
            if msg.photo or msg.video:
                fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
                ftype = "photo" if msg.photo else "video"
                c["start_media"] = {"type":ftype,"file_id":fid}; saved.append(ftype)
            if not saved: await msg.reply_text("⚠️ Send text or media."); return
            save_config(c); owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ /start {' + '.join(saved)} saved!"); return

        elif state == ST_EDIT_CMD:
            c = load_config(); saved = []; text_val = msg.text or msg.caption
            if text_val: c["command_text"] = text_val; saved.append("text")
            if msg.photo or msg.video:
                fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
                ftype = "photo" if msg.photo else "video"
                c["menu_media"] = {"type":ftype,"file_id":fid}; saved.append(ftype)
            if not saved: await msg.reply_text("⚠️ Send text or media."); return
            save_config(c); owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ /command {' + '.join(saved)} saved!"); return

        elif state == ST_ADD_CMD2:
            name = si["cmd_name"]
            if msg.text:
                c = load_config(); cmds = c.get("custom_commands",{}); cmds[name] = msg.text
                c["custom_commands"] = cmds; save_config(c)
                owner_edit_state[uid] = {"state": None}
                await msg.reply_text(f"✅ /{name} added!"); return

        elif state == ST_BROADCAST_PICK:
            sel_text = (msg.text or "").strip()
            d = si.get("all_users", load_users_dict())
            # FIX: entries created by _seed_known_members() during the old data-loss
            # recovery are placeholders with fake negative IDs (never real Telegram
            # accounts) — they can never receive a DM. Sending to them was the reason
            # almost every broadcast came back "Sent: 0". Real users upsert over these
            # once they interact, so it's always safe to skip whatever placeholders remain.
            d = {k: v for k, v in d.items() if not v.get("_placeholder")}
            if sel_text.lower() == "all":
                selected_ids = [int(k) for k in d.keys()]
            else:
                parts = [p.strip().lstrip("@") for p in sel_text.replace(",", "\n").split("\n") if p.strip()]
                selected_ids = []
                for k, v in d.items():
                    uname = (v.get("username") or "").lower()
                    if uname and uname in [p.lower() for p in parts]:
                        selected_ids.append(int(k))
                    elif k in parts:
                        selected_ids.append(int(k))
            if not selected_ids:
                await msg.reply_text("⚠️ No matching users found. Try again or send <code>all</code>.", parse_mode="HTML"); return
            owner_edit_state[uid] = {"state": ST_BROADCAST_MSG, "targets": selected_ids}
            await msg.reply_text(
                f"✅ <b>{len(selected_ids)} users selected.</b>\n\n"
                f"Now send your broadcast message (text, photo, or video with caption).\n\n"
                f"💡 Bold/italic/links aur premium emoji seedha Telegram ke apne formatting/emoji "
                f"se laga kar bhejein — jo bhi type karein wahi sab users tak jayega, koi HTML tag "
                f"ya emoji ID manually set karne ki zaroorat nahi.",
                parse_mode="HTML"
            ); return

        elif state == ST_BROADCAST_MSG:
            targets = si.get("targets", [])
            owner_edit_state[uid] = {"state": None}
            ok = fail = blocked = 0
            sent_messages = []  # [{"chat_id": tid, "message_id": mid}, ...] — for /editbroadcast & /deletebroadcast
            async def _do_send(tid, plain=False):
                # Owner requirement: whatever the owner typed — bold/italic/
                # links/premium emoji — should reach every user exactly as
                # typed, without the owner ever having to look up a
                # custom_emoji_id by hand. Telegram attaches that formatting
                # (including premium emoji) as `entities` on the incoming
                # message; passing those straight through to send_message /
                # send_photo / send_video reproduces it for every recipient
                # (premium emoji render for all users regardless of whether
                # the recipient themself has Telegram Premium — only the
                # sender needs it). This also means the owner does NOT type
                # literal HTML tags anymore — use Telegram's own formatting
                # toolbar (bold/italic/emoji picker) when composing the
                # broadcast message.
                cap = re.sub(r"<[^>]+>", "", msg.caption or "") if plain else (msg.caption or "")
                txt = re.sub(r"<[^>]+>", "", msg.text or "") if plain else (msg.text or "")
                cap_ents = None if plain else (msg.caption_entities or None)
                txt_ents = None if plain else (msg.entities or None)
                if msg.photo:
                    sent = await context.bot.send_photo(tid, photo=msg.photo[-1].file_id, caption=cap, caption_entities=cap_ents)
                elif msg.video:
                    sent = await context.bot.send_video(tid, video=msg.video.file_id, caption=cap, caption_entities=cap_ents)
                elif msg.text:
                    sent = await context.bot.send_message(tid, txt, entities=txt_ents)
                else:
                    sent = await context.bot.forward_message(tid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                # Owner requirement: every broadcast auto-pins itself in the
                # recipient's DM. Pinning can fail for reasons that have
                # nothing to do with the broadcast itself (bot blocked right
                # after sending, weird chat state) — never let that count as
                # a failed send.
                try:
                    await context.bot.pin_chat_message(tid, sent.message_id, disable_notification=True)
                except Exception as e_pin:
                    logger.warning(f"Broadcast auto-pin failed for {tid}: {e_pin}")
                sent_messages.append({"chat_id": tid, "message_id": sent.message_id})
            for i, tid in enumerate(targets):
                try:
                    try:
                        await _do_send(tid)
                    except RetryAfter as e_flood:
                        # FIX: Telegram flood-control (429) used to fall straight into
                        # the generic except below and get counted as "Failed" — and
                        # since the wait was never honoured, EVERY subsequent send hit
                        # the same flood-wait immediately, cascading into "almost all
                        # failed" broadcasts. Now: wait it out once, then retry this
                        # same user before giving up.
                        wait_s = float(getattr(e_flood, "retry_after", 3)) + 1
                        logger.warning(f"Broadcast flood-wait: sleeping {wait_s}s (tid={tid})")
                        await asyncio.sleep(wait_s)
                        await _do_send(tid)
                    except BadRequest as e_bad:
                        err_str = str(e_bad).lower()
                        if "parse" in err_str or "entities" in err_str:
                            # FIX: unescaped HTML in the broadcast text/caption (a stray
                            # < or & the owner typed) made Telegram reject the message
                            # for every single recipient — one bad character was enough
                            # to turn a 99-user broadcast into 98 "Failed". Retry once
                            # as plain text instead of failing outright.
                            await _do_send(tid, plain=True)
                        else:
                            raise
                    ok += 1
                except Exception as e:
                    err_str = str(e).lower()
                    if isinstance(e, Forbidden) or any(x in err_str for x in ("deactivated", "deleted", "blocked", "bot was kicked", "user not found", "chat not found", "forbidden")):
                        blocked += 1
                    else:
                        fail += 1
                        # FIX: previously swallowed silently, so a systemic bug (bad
                        # HTML, wrong parse_mode, etc.) affecting every send left no
                        # trace in Railway logs. Now it's visible for real diagnosis.
                        logger.error(f"Broadcast send failed for {tid}: {e}")
                # Rate limiting: 25 msg/sec max — sleep every 25 sends (Telegram limit)
                if (i + 1) % 25 == 0:
                    await asyncio.sleep(1)
            # Save every recipient's {chat_id, message_id} so the owner can
            # later /editbroadcast or /deletebroadcast this exact send —
            # across every DM at once, no matter how many people it went to.
            bid = record_broadcast(sent_messages, is_media=bool(msg.photo or msg.video))
            await msg.reply_text(
                f"📢 <b>Broadcast Done</b> (ID: <code>{bid}</code>)\n"
                f"✅ Sent: {ok} | 🚫 Blocked/Deleted: {blocked} | ❌ Failed: {fail}\n\n"
                f"Galti ho gayi ho to:\n"
                f"<code>/editbroadcast {bid}</code> — sab ki DM me edit karo\n"
                f"<code>/deletebroadcast {bid}</code> — sab ki DM se delete karo\n"
                f"(ID chhod kar bhi chala sakte ho — khud latest broadcast pakar lega)",
                parse_mode="HTML"
            ); return

        elif state == ST_EDIT_BROADCAST_MSG:
            bid = si.get("broadcast_id")
            owner_edit_state[uid] = {"state": None}
            d = load_broadcasts()
            rec = d.get("items", {}).get(bid)
            if not rec:
                await msg.reply_text(f"⚠️ Broadcast {bid} ab log mein nahi mila — shayad delete ho chuka hai.")
                return
            is_media = rec.get("is_media")
            new_txt  = msg.text or msg.caption or ""
            new_ents = msg.entities if msg.text else msg.caption_entities
            ok = fail = 0
            for i, m in enumerate(rec.get("messages", [])):
                try:
                    if is_media:
                        await context.bot.edit_message_caption(
                            chat_id=m["chat_id"], message_id=m["message_id"],
                            caption=new_txt, caption_entities=new_ents)
                    else:
                        await context.bot.edit_message_text(
                            chat_id=m["chat_id"], message_id=m["message_id"],
                            text=new_txt, entities=new_ents)
                    ok += 1
                except BadRequest as e_bad:
                    err_str = str(e_bad).lower()
                    if "not modified" in err_str:
                        ok += 1  # identical content — not a real failure
                    else:
                        fail += 1
                        logger.warning(f"editbroadcast: could not edit {m} — {e_bad}")
                except Exception as e:
                    fail += 1
                    logger.warning(f"editbroadcast: could not edit {m} — {e}")
                if (i + 1) % 25 == 0:
                    await asyncio.sleep(1)
            await msg.reply_text(
                f"✏️ <b>Broadcast {bid} Edited</b>\n"
                f"✅ Updated: {ok} | ⚠️ Couldn't update (already deleted/blocked): {fail}",
                parse_mode="HTML")
            return

        elif state == ST_MEDIABROADCAST_MSG:
            # Validate FIRST — only clear state after we confirm valid media
            if not (msg.photo or msg.video):
                await msg.reply_text(
                    "⚠️ Sirf photo ya video bhejain caption ke saath.\n"
                    "Text-only support nahi hai is command mein.\n\n"
                    "Dobara photo/video bhejain ya /cancel se cancel karein."
                ); return
            # Valid media — now clear state and start broadcast
            owner_edit_state[uid] = {"state": None}
            d = load_users_dict()
            # FIX: skip fake placeholder IDs from the old data-loss recovery — see
            # the matching note in ST_BROADCAST_PICK above.
            targets = [int(k) for k, v in d.items() if not v.get("_placeholder")]
            ok = fail = blocked = 0
            sent_messages = []  # for /editbroadcast & /deletebroadcast
            status_msg = await msg.reply_text(f"⏳ Sending to {len(targets)} users...")
            async def _do_media_send(tid, plain=False):
                cap = re.sub(r"<[^>]+>", "", msg.caption or "") if plain else (msg.caption or "")
                cap_ents = None if plain else (msg.caption_entities or None)
                if msg.photo:
                    sent = await context.bot.send_photo(tid, photo=msg.photo[-1].file_id, caption=cap, caption_entities=cap_ents)
                elif msg.video:
                    sent = await context.bot.send_video(tid, video=msg.video.file_id, caption=cap, caption_entities=cap_ents)
                else:
                    return
                # Owner requirement: every broadcast auto-pins itself in the recipient's DM.
                try:
                    await context.bot.pin_chat_message(tid, sent.message_id, disable_notification=True)
                except Exception as e_pin:
                    logger.warning(f"Media broadcast auto-pin failed for {tid}: {e_pin}")
                sent_messages.append({"chat_id": tid, "message_id": sent.message_id})
            for i, tid in enumerate(targets):
                try:
                    try:
                        await _do_media_send(tid)
                    except RetryAfter as e_flood:
                        # See matching fix note in ST_BROADCAST_MSG above.
                        wait_s = float(getattr(e_flood, "retry_after", 3)) + 1
                        logger.warning(f"Media broadcast flood-wait: sleeping {wait_s}s (tid={tid})")
                        await asyncio.sleep(wait_s)
                        await _do_media_send(tid)
                    except BadRequest as e_bad:
                        err_str = str(e_bad).lower()
                        if "parse" in err_str or "entities" in err_str:
                            await _do_media_send(tid, plain=True)
                        else:
                            raise
                    ok += 1
                except Exception as e:
                    err_str = str(e).lower()
                    if isinstance(e, Forbidden) or any(x in err_str for x in ("deactivated", "deleted", "blocked", "bot was kicked", "user not found", "chat not found", "forbidden")):
                        blocked += 1
                    else:
                        fail += 1
                        logger.error(f"Media broadcast send failed for {tid}: {e}")
                # Rate limiting — 25 msg/sec (Telegram safe limit)
                if (i + 1) % 25 == 0:
                    await asyncio.sleep(1)
                # Progress update every 500 users
                if (i + 1) % 500 == 0:
                    try:
                        await status_msg.edit_text(f"⏳ Progress: {i+1}/{len(targets)} users done...")
                    except Exception:
                        pass
            bid = record_broadcast(sent_messages, is_media=True)
            summary = (
                f"📸 <b>Media Broadcast Done!</b> (ID: <code>{bid}</code>)\n"
                f"✅ Sent: {ok} | 🚫 Blocked/Deleted: {blocked} | ❌ Failed: {fail}\n\n"
                f"<code>/editbroadcast {bid}</code> | <code>/deletebroadcast {bid}</code>"
            )
            try:
                await status_msg.edit_text(summary, parse_mode="HTML")
            except Exception:
                await msg.reply_text(summary, parse_mode="HTML")
            return

        if state == ST_XEM_BULK:
            low, high = int(si["low"]), int(si["high"])
            vals = _parse_bulk_xem(msg.text or "")
            if not vals:
                await msg.reply_text(
                    "⚠️ Koi ID pehchani nahi gayi. Format check karo:\n"
                    "<code>Bnb: 5843674404914994680</code> (ek line per emoji).",
                    parse_mode="HTML"); return
            owner_edit_state[uid] = {"state": None}
            pack = _xem_build_pack(low, high, vals)
            c = load_config()
            packs = [r for r in c.get("x_emoji_packs", [])
                     if not (int(r.get("low", 0)) == low and int(r.get("high", 0)) == high)]
            packs.append({"low": low, "high": high, "pack": pack,
                          "set_at": datetime.utcnow().isoformat()})
            packs.sort(key=lambda r: int(r.get("low", 0)))
            c["x_emoji_packs"] = packs
            save_config(c)
            _missing = [label for label, key in
                        [("BNB","chain.BNB"),("ETH","chain.ETH"),("SOL","chain.SOL"),
                         ("TON","chain.TON"),("BASE","chain.BASE"),("RH","chain.RH"),
                         ("ARC","chain.ARC"),
                         ("Crystal ball","crystal"),("Teer","teer"),("Caller","caller"),
                         ("Twitter","twitter"),("Trade","trade"),("Details","details"),
                         ("Dex","dex"),("Dev","dev")]
                        if key not in vals]
            _miss_note = f"\n\n⚠️ Ye set nahi huye (built-in default use hoga): {', '.join(_missing)}" if _missing else ""
            await msg.reply_text(
                f"🎉 <b>Ho gaya!</b> {len(vals)}/{len(XEM_STEPS)} emoji IDs mil gayeen.\n\n"
                f"<b>{low}X – {high}X</b> ki saari alerts ab in emojis ke saath jayengi.\n\n"
                f"{_xem_pack_summary(pack)}{_miss_note}\n\n"
                f"📋 /listxemojis · 🗑 /removexemojis N", parse_mode="HTML"); return

        if state == ST_XEM_RANGE:
            raw  = (msg.text or "").strip()
            nums = re.findall(r"\d+", raw)
            if len(nums) < 2:
                await msg.reply_text(
                    "⚠️ Do number bhejo — kitne X se kitne X tak.\n"
                    "Misal: <code>31 40</code>", parse_mode="HTML"); return
            low, high = int(nums[0]), int(nums[1])
            if low > high:
                low, high = high, low
            if low < 2:
                await msg.reply_text("⚠️ Range 2X se shuru hoti hai. Dobara bhejo."); return
            owner_edit_state[uid] = {"state": ST_XEM_EMOJI, "low": low,
                                     "high": high, "idx": 0, "vals": {}}
            await msg.reply_text(
                f"✅ Range set: <b>{low}X – {high}X</b>\n\n"
                f"Ab main ek ek kar ke 14 emoji IDs maangunga.\n\n"
                f"1/{len(XEM_STEPS)} — <b>{XEM_STEPS[0][1]}</b> ki ID bhejo:\n"
                f"(blue pack wali hi rakhni ho to /skip)", parse_mode="HTML"); return

        elif state == ST_XEM_EMOJI:
            raw  = (msg.text or "").strip()
            idx  = int(si.get("idx", 0))
            vals = dict(si.get("vals") or {})
            key, label = XEM_STEPS[idx]
            if raw.lower() in ("/skip", "skip"):
                pass
            else:
                digits = re.sub(r"\D", "", raw)
                # Agar owner ne seedha premium emoji bheji ho to uski ID le lo
                try:
                    for _e in (msg.entities or []):
                        if _e.type == "custom_emoji" and _e.custom_emoji_id:
                            digits = str(_e.custom_emoji_id); break
                except Exception:
                    pass
                if not digits or len(digits) < 10:
                    await msg.reply_text(
                        f"⚠️ Ye sahi emoji ID nahi lagti.\n\n"
                        f"<b>{label}</b> ki ID sirf numbers me hoti hai, jaise "
                        f"<code>5818848205453927129</code>.\n\n"
                        f"Dobara bhejo, ya /skip.", parse_mode="HTML"); return
                vals[key] = int(digits)
            idx += 1
            if idx < len(XEM_STEPS):
                owner_edit_state[uid] = {"state": ST_XEM_EMOJI, "low": si["low"],
                                         "high": si["high"], "idx": idx, "vals": vals}
                await msg.reply_text(
                    f"✅ Save.\n\n{idx + 1}/{len(XEM_STEPS)} — "
                    f"<b>{XEM_STEPS[idx][1]}</b> ki ID bhejo:\n"
                    f"(ya /skip)", parse_mode="HTML"); return
            owner_edit_state[uid] = {"state": None}
            low, high = int(si["low"]), int(si["high"])
            pack = _xem_build_pack(low, high, vals)
            c = load_config()
            packs = [r for r in c.get("x_emoji_packs", [])
                     if not (int(r.get("low", 0)) == low and int(r.get("high", 0)) == high)]
            packs.append({"low": low, "high": high, "pack": pack,
                          "set_at": datetime.utcnow().isoformat()})
            packs.sort(key=lambda r: int(r.get("low", 0)))
            c["x_emoji_packs"] = packs
            save_config(c)
            await msg.reply_text(
                f"🎉 <b>Ho gaya!</b>\n\n"
                f"<b>{low}X – {high}X</b> ki saari alerts ab in emojis ke saath "
                f"jayengi.\n\n{_xem_pack_summary(pack)}\n"
                f"📋 /listxemojis · 🗑 /removexemojis N", parse_mode="HTML"); return

        elif state == ST_DROPSKIP_CH:
            ch = _norm_channel_arg(msg.text or "")
            if not ch or " " in ch:
                await msg.reply_text("⚠️ Sahi channel username bhejo, jaise <code>@kolname</code> (ya /cancel).",
                                     parse_mode="HTML"); return
            known = {str(c).lstrip("@").lower() for c in _ranking_channels()}
            note = "" if ch in known else "\n⚠️ Ye channel abhi tracked list me nahi mila — username check kar lein."
            owner_edit_state[uid] = {"state": ST_DROPSKIP_PAT, "ch": ch}
            await msg.reply_text(
                f"📉 <b>@{html.escape(ch)}</b> ke liye pattern chuno:{note}\n\n"
                f"Ya khud likho: <code>post skip</code> — jaise <code>1 4</code> "
                f"(1 post, phir 4 chhor do).",
                parse_mode="HTML", reply_markup=_dropskip_preset_kb(ch)); return

        elif state == ST_DROPSKIP_PAT:
            nums = re.findall(r"\d+", msg.text or "")
            if len(nums) < 2 or int(nums[0]) < 1:
                await msg.reply_text("⚠️ Do numbers bhejo: <code>post skip</code>, jaise <code>1 2</code> (ya /cancel).",
                                     parse_mode="HTML"); return
            ch = si.get("ch", "")
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(_dropskip_apply(ch, int(nums[0]), int(nums[1])), parse_mode="HTML"); return

        elif state == ST_SETPROMOLINK:
            owner_edit_state[uid] = {"state": None}
            raw = (msg.text or "").strip()
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if len(lines) < 2:
                await msg.reply_text("⚠️ Send text and URL on separate lines.\nFormat:\n<code>Your text\nhttps://url</code>", parse_mode="HTML"); return
            promo_text = lines[0]
            promo_url  = lines[-1]
            if not promo_url.startswith("http"):
                await msg.reply_text("⚠️ Last line must be a valid URL starting with http/https."); return
            _old = load_config().get("promo_link") or {}
            cfg_set("promo_link", {"text": promo_text, "url": promo_url,
                                   "emoji_id": _old.get("emoji_id") or PROMO_EMOJI_ID,
                                   "set_at": datetime.utcnow().isoformat()})
            owner_edit_state[uid] = {"state": ST_SETPROMO_EMOJI}
            await msg.reply_text(
                f"✅ <b>Promo Link Set for 12 Hours</b>\n\n"
                f"Text: {html.escape(promo_text)}\nURL: {html.escape(promo_url)}\n\n"
                f"🔮 <b>Ab premium emoji ki ID bhejo</b> — jo emoji is text ke "
                f"aage lagegi.\n\n"
                f"• Emoji ID aisi hoti hai: <code>5773906204539493747</code>\n"
                f"• Purani/default emoji rakhni ho to bhejo: /skip\n\n"
                f"ℹ️ Ye link har post ke sabse neeche (Champion/Leaderboard KOL "
                f"ke baad, 1 khali line chhod kar) show hoga.",
                parse_mode="HTML"
            ); return

        elif state == ST_SETPROMO_EMOJI:
            raw = (msg.text or "").strip()
            promo = load_config().get("promo_link") or {}
            if raw.lower() in ("/skip", "skip"):
                owner_edit_state[uid] = {"state": None}
                await msg.reply_text(
                    f"✅ Default premium emoji use hogi "
                    f"(<code>{promo.get('emoji_id') or PROMO_EMOJI_ID}</code>).",
                    parse_mode="HTML"); return
            digits = re.sub(r"\D", "", raw)
            if not digits or len(digits) < 10:
                await msg.reply_text(
                    "⚠️ Ye sahi emoji ID nahi lagti.\n\n"
                    "Premium emoji ID sirf numbers me hoti hai, jaise "
                    "<code>5773906204539493747</code>.\n\n"
                    "Dobara bhejo, ya /skip likho.", parse_mode="HTML"); return
            owner_edit_state[uid] = {"state": None}
            promo["emoji_id"] = int(digits)
            cfg_set("promo_link", promo)
            await msg.reply_text(
                f"✅ <b>Premium emoji set!</b>\n\n"
                f"ID: <code>{digits}</code>\n\n"
                f"Ab promo line har post ke sabse neeche is emoji ke saath "
                f"show hogi.", parse_mode="HTML"); return

        elif state == ST_SET_PUBLIC_TEXT:
            cmdkey = si.get("cmd")
            new_txt = msg.text or msg.caption
            if not new_txt:
                await msg.reply_text("⚠️ Text bhejo (photo/video ke liye /setcommandmedia use karo)."); return
            owner_edit_state[uid] = {"state": None}
            c = load_config(); pt = c.get("public_texts", {})
            pt[cmdkey] = new_txt; c["public_texts"] = pt; save_config(c)
            await msg.reply_text(
                f"✅ <b>/{cmdkey}</b> ka text update ho gaya!\n\n"
                f"Dekhne ke liye: <code>/showtext {cmdkey}</code>\n"
                f"Default par wapas: <code>/cleartext {cmdkey}</code>",
                parse_mode="HTML"); return

        elif state == ST_ADD_DROPPED_VID:
            owner_edit_state[uid] = {"state": None}
            if msg.video or msg.animation:
                media_obj = msg.video or msg.animation
                ftype = "gif" if msg.animation else "video"
                vids = load_config().get("dropped_videos", [])
                if len(vids) >= 20:
                    await msg.reply_text(
                        "⚠️ Maximum 20 Dropped-Call videos allowed.\n"
                        "Use /removedroppedvideo N to remove one first."
                    ); return
                vids.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set("dropped_videos", vids)
                await msg.reply_text(
                    f"✅ <b>Dropped-Call Video #{len(vids)} Added!</b>\n\n"
                    f"Total stored: <b>{len(vids)}/20</b>\n"
                    f"Har nayi tracked call par rotate hogi.\n\n"
                    f"Use /listdroppedvideos to see all · /adddroppedvideo to add more",
                    parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Please send a video file. No changes made."); return

        elif state == ST_ADD_CP_MEDIA:
            owner_edit_state[uid] = {"state": None}
            media_obj = msg.video or msg.animation or (msg.photo[-1] if msg.photo else None)
            if media_obj:
                ftype = "photo" if msg.photo and not (msg.video or msg.animation) else (
                        "gif" if msg.animation else "video")
                med = load_config().get("cp_media", [])
                if len(med) >= CP_MAX_MEDIA:
                    await msg.reply_text(
                        f"⚠️ Maximum {CP_MAX_MEDIA} CheesePad videos already stored.\n"
                        f"/removecpmedia N se ek hatao pehle."
                    ); return
                med.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set("cp_media", med)
                await msg.reply_text(
                    f"✅ <b>CheesePad Video #{len(med)} Added!</b>\n\n"
                    f"Total stored: <b>{len(med)}/{CP_MAX_MEDIA}</b>\n"
                    f"Har CheesePad post par rotate hogi.\n\n"
                    f"/listcpmedia · /addcpmedia",
                    parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Video / GIF / photo bhejo. Koi change nahi hua."); return

        elif state in (ST_ADD_CPD_MEDIA, ST_ADD_PSD_MEDIA):
            kind = "cp" if state == ST_ADD_CPD_MEDIA else "ps"
            mx   = CPD_MAX_MEDIA if kind == "cp" else PSD_MAX_MEDIA
            label = "🧀 CheesePad" if kind == "cp" else "🩷 PinkSale"
            owner_edit_state[uid] = {"state": None}
            media_obj = msg.video or msg.animation or (msg.photo[-1] if msg.photo else None)
            if media_obj:
                ftype = "photo" if msg.photo and not (msg.video or msg.animation) else (
                        "gif" if msg.animation else "video")
                med = load_config().get(f"{kind}d_media", [])
                if len(med) >= mx:
                    await msg.reply_text(
                        f"⚠️ Maximum {mx} {label} details media already stored.\n"
                        f"/remove{kind}dmedia N se ek hatao pehle."); return
                med.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set(f"{kind}d_media", med)
                await msg.reply_text(
                    f"✅ <b>{label} Details Media #{len(med)} Added!</b>\n\n"
                    f"Total stored: <b>{len(med)}/{mx}</b>\n"
                    f"Bot ke andar aane wali token details ke sath rotate hogi.\n\n"
                    f"/list{kind}dmedia · /add{kind}dmedia", parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Video / GIF / photo bhejo. Koi change nahi hua."); return

        elif state == ST_ADD_PS_MEDIA:
            owner_edit_state[uid] = {"state": None}
            media_obj = msg.video or msg.animation or (msg.photo[-1] if msg.photo else None)
            if media_obj:
                ftype = "photo" if msg.photo and not (msg.video or msg.animation) else (
                        "gif" if msg.animation else "video")
                med = load_config().get("ps_media", [])
                if len(med) >= PS_MAX_MEDIA:
                    await msg.reply_text(
                        f"⚠️ Maximum {PS_MAX_MEDIA} PinkSale media already stored.\n"
                        f"/removepsmedia N se ek hatao pehle."
                    ); return
                med.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set("ps_media", med)
                await msg.reply_text(
                    f"✅ <b>PinkSale Media #{len(med)} Added!</b>\n\n"
                    f"Total stored: <b>{len(med)}/{PS_MAX_MEDIA}</b>\n"
                    f"Har PinkSale post par rotate hongi.\n\n"
                    f"/listpsmedia · /addpsmedia",
                    parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Video / GIF / photo bhejo. Koi change nahi hua."); return

        elif state == ST_DROPPED_TMPL:
            owner_edit_state[uid] = {"state": None}
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            # FIX: previously this saved msg.text/caption raw, which silently
            # drops any premium-emoji entity info Telegram attaches when the
            # owner types/picks a premium emoji directly — the underlying
            # placeholder character got saved with no ID, so it rendered as a
            # wrong/plain emoji later ("emojis kharab ho rahi hain"). Now we
            # capture those custom_emoji entities and bake each one in as an
            # explicit [[emoji:ID]] tag at its exact position (UTF-16 safe) —
            # prepare_owner_emojis() already understands this syntax at send
            # time, so the owner never has to look up/paste a numeric emoji
            # ID by hand; picking the premium emoji is enough.
            entities = list(msg.entities or msg.caption_entities or [])
            custom_ents = sorted(
                [e for e in entities if e.type == "custom_emoji"],
                key=lambda e: e.offset
            )
            if custom_ents:
                text_utf16 = text_val.encode('utf-16-le')
                parts = []
                prev = 0
                for ent in custom_ents:
                    s = ent.offset * 2
                    e_ = (ent.offset + ent.length) * 2
                    parts.append(text_utf16[prev:s])
                    parts.append(f"[[emoji:{ent.custom_emoji_id}]]".encode('utf-16-le'))
                    prev = e_
                parts.append(text_utf16[prev:])
                text_val = b''.join(parts).decode('utf-16-le')
            cfg_set("dropped_call_template", text_val)
            await msg.reply_text(
                "✅ <b>Dropped-Call Template Saved!</b>\n\n"
                "Ab jab bhi koi KOL pehli baar call kare ga, isi template se post hogi.\n"
                "/showdroppedtemplate se dekh sakte ho.",
                parse_mode="HTML"); return

        elif state == ST_KOL_DROPPED_TMPL:
            channel = si["channel"]
            owner_edit_state[uid] = {"state": None}
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            entities = list(msg.entities or msg.caption_entities or [])
            custom_ents = sorted(
                [e for e in entities if e.type == "custom_emoji"],
                key=lambda e: e.offset
            )
            if custom_ents:
                text_utf16 = text_val.encode('utf-16-le')
                parts = []
                prev = 0
                for ent in custom_ents:
                    s = ent.offset * 2
                    e_ = (ent.offset + ent.length) * 2
                    parts.append(text_utf16[prev:s])
                    parts.append(f"[[emoji:{ent.custom_emoji_id}]]".encode('utf-16-le'))
                    prev = e_
                parts.append(text_utf16[prev:])
                text_val = b''.join(parts).decode('utf-16-le')
            c = load_config()
            lang = (c.get("channel_language", {}) or {}).get(channel)
            if lang and lang != "english":
                templates = c.get("lang_dropped_templates", {})
                templates[lang] = text_val
                c["lang_dropped_templates"] = templates
                save_config(c)
                _grp_count = len([k for k, v in (c.get("channel_language", {}) or {}).items() if v == lang])
                flag = {"chinese": "🇨🇳", "vietnamese": "🇻🇳", "turkish": "🇹🇷", "russian": "🇷🇺"}.get(lang, "")
                await msg.reply_text(
                    f"✅ <b>{flag} {lang.title()} group — Dropped-Call Template Saved!</b>\n\n"
                    f"Ye {_grp_count} {lang}-tagged KOL(s) sab pe apply hoga jab bhi koi unme se pehli baar call kare "
                    f"(sirf @{channel} pe nahi).",
                    parse_mode="HTML"
                ); return
            templates = c.get("channel_dropped_templates", {})
            templates[channel] = text_val
            c["channel_dropped_templates"] = templates
            save_config(c)
            await msg.reply_text(
                f"✅ <b>@{channel} — Dropped-Call Template Saved!</b>\n\n"
                f"Ab jab yeh KOL pehli baar call kare ga, isi (uski apni language wali) template se post hogi.\n"
                f"/showkoldroppedtemplate {channel} se dekh sakte ho.",
                parse_mode="HTML"); return

    # /setkolowner multi-step state handler
    if uid in OWNER_IDS and uid in owner_edit_state:
        si_ko = owner_edit_state.get(uid, {})
        if si_ko.get("state") == ST_SETKOLOWNER_CH:
            owner_edit_state[uid] = {"state": None}
            ch_input = (msg.text or "").strip().lstrip("@")
            if not ch_input:
                await msg.reply_text("⚠️ Channel username valid nahi hai."); return
            channels = load_channels()
            if ch_input.lower() not in [c.lower() for c in channels]:
                await msg.reply_text(
                    f"⚠️ @{ch_input} tracked channels mein nahi hai.\n"
                    "Sirf tracked channels ke owners set ho sakte hain."); return
            owner_edit_state[uid] = {"state": ST_SETKOLOWNER_USER, "channel": ch_input.lower()}
            await msg.reply_text(
                f"✅ Channel: @{ch_input}\n\n"
                "Ab us user ka Telegram username bhejo jise is channel ki sab call updates milein.\n"
                "(Format: @username  ya sirf username)",
                parse_mode="HTML"); return
        elif si_ko.get("state") == ST_SETKOLOWNER_USER:
            owner_edit_state[uid] = {"state": None}
            pending_ch = si_ko.get("channel","")
            user_input = (msg.text or "").strip().lstrip("@")
            if not user_input:
                await msg.reply_text("⚠️ Valid username nahi mila."); return
            # Lookup user ID by username from our users DB
            d = load_users_dict()
            found_id = None
            for uid_str, udata in d.items():
                if (udata.get("username") or "").lower() == user_input.lower():
                    found_id = int(uid_str)
                    break
            if not found_id:
                await msg.reply_text(
                    f"⚠️ @{user_input} ka Telegram ID nahi mila bot ke users mein.\n\n"
                    "User ne pehle bot se /start karna chahiye taake bot uska ID store kare.\n\n"
                    "Agar aap directly numeric ID jaante ho, use karein:\n"
                    f"<code>/setkolownerid {pending_ch} NUMERIC_USER_ID</code>",
                    parse_mode="HTML"); return
            kol_owners = load_kol_owners()
            kol_owners[pending_ch] = found_id
            save_kol_owners(kol_owners)
            await msg.reply_text(
                f"✅ <b>KOL Owner Set!</b>\n\n"
                f"Channel: @{pending_ch}\n"
                f"Owner: @{user_input} (ID: {found_id})\n\n"
                f"Ab se @{pending_ch} ki har call milestone alert directly @{user_input} ke DM mein jaegi.",
                parse_mode="HTML"); return

    # Channel / Twitter lookup
    if msg.text and not msg.text.startswith("/"):
        handled = await handle_lookup(update, msg.text)
        if handled: return

    # (Group CA lookup feature removed)

# ─── Post init ────────────────────────────────────────────────────────────────
async def post_init(application: Application):
    # ── SPEED FIX ────────────────────────────────────────────────────────
    # Har blocking network call asyncio.to_thread() se chalti hai — scan_job,
    # monitoring_job (bulk+priority), refresh_channel_calls_live (/record),
    # leaderboard_job, champions_job sab isi SHARED thread pool se guzarte
    # hain. Pool chhota ho to yeh sab ek dusre ko block karte hain: /record
    # 15s tak la ho jata hai, nayi calls ka entry-MC fetch late hota hai, aur
    # monitoring tick ka dex-check thread na milne ki wajah se skip ho kar
    # X alert miss/late kar deta hai — bawajood iske ke per-call timeout
    # chhota tha, thread hi available nahi tha shuru karne ke liye.
    # Pool bara kiya (24 → 96 default) taake in sab jobs ko humesha khaali
    # thread mile aur koi bhi cheez doosri ke peeche queue mein na atke.
    try:
        _loop = asyncio.get_running_loop()
        _loop.set_default_executor(
            ThreadPoolExecutor(max_workers=int(os.environ.get("WIZARD_MAX_WORKERS", "96") or 96), thread_name_prefix="wizard"))
    except Exception as _e_ex:
        logger.warning(f"executor resize failed: {_e_ex}")
    await application.bot.set_my_commands([
        BotCommand("start",     "Welcome & Bot Info"),
        BotCommand("command",   "Command Center"),
        BotCommand("history",   "Call history of a KOL channel"),
        BotCommand("linkme",    "Link your channel for alerts"),
        BotCommand("linksmart", "Link your channel for solid alerts only"),
        # /subscribe and /unsubscribe removed from the public "/" menu — they're
        # already reachable via the PS button, so listing them here was redundant.
        # The underlying CommandHandlers stay registered below, so the button
        # (and the commands themselves, if typed manually) still work exactly
        # as before — only the menu listing is gone.
    ])
    # Pre-seed known members so they always appear in /myusers even after data loss
    _seed_known_members()
    # Run one-time migration: remove X/Twitter links from stored templates
    try:
        _migrate_remove_x_from_templates()
    except Exception as _me:
        logger.warning(f"Template migration failed: {_me}")
    logger.info("✅ Bot commands menu set")

# ─── Global error handler ─────────────────────────────────────────────────────
async def _global_error_handler(update, context):
    """Catch every unhandled handler/job exception so one bad update can never
    kill the process (the #1 reason the Railway container kept restarting)."""
    err = context.error
    logger.error(f"Unhandled error: {type(err).__name__}: {err}", exc_info=err)


# ─── Railway health check server ──────────────────────────────────────────────
def _start_health_server():
    """Railway web services kill a deploy that never binds $PORT.
    A tiny stdlib HTTP server on a daemon thread keeps the deploy healthy."""
    port = int(os.environ.get("PORT", "0") or 0)
    if not port:
        return
    try:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","service":"wizard-scan-bot"}')
            def log_message(self, *a):  # silence access logs
                pass

        srv = HTTPServer(("0.0.0.0", port), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logger.info(f"✅ Health server listening on :{port}")
    except Exception as e:
        logger.warning(f"Health server failed: {e}")



# ═══════════════════════════════════════════════════════════════════════════════
# BUTTON WIZARDS  (missed call / X fix / buy bots)  +  BUY BOT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# Har flow ek hi sawal ek waqt me poochta hai aur jahan mumkin ho buttons deta
# hai. Long "usage" text walls ki jagah ye wizards use hote hain.

wizard_state: dict = {}          # uid -> {"flow","step","data"}

BUYBOTS_FILE = _dp("buy_bots.json")
def load_buybots():   return load_json(BUYBOTS_FILE, {})
def save_buybots(d):  save_json(BUYBOTS_FILE, d)

BUYBOT_CHAINS = [
    ("SOL",  "Solana",     "solana",    "https://solscan.io/tx/"),
    ("ETH",  "Ethereum",   "eth",       "https://etherscan.io/tx/"),
    ("BNB",  "BSC",        "bsc",       "https://bscscan.com/tx/"),
    ("BASE", "Base",       "base",      "https://basescan.org/tx/"),
    ("TON",  "TON",        "ton",       "https://tonviewer.com/transaction/"),
    ("RH",   "Robinhood",  "robinhood", "https://robinhoodchain.blockscout.com/tx/"),
]
BUYBOT_CHAIN_MAP = {c[0]: {"label": c[1], "gecko": c[2], "tx": c[3]} for c in BUYBOT_CHAINS}
BUYBOT_DEXPATH   = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc",
                    "BASE": "base", "TON": "ton", "RH": "robinhood"}
BUYBOT_EMOJIS    = ["🟢", "🔮", "🚀", "🔥", "💊", "💎"]
BUYBOT_MAX_EMOJIS = 60

# Buy-size tiers — each tier can carry its own media (photo / GIF / video).
BUYBOT_TIERS = [
    ("low",  "Small buys",  "up to $500"),
    ("mid",  "Mid buys",    "$501 – $999"),
    ("high", "Big buys",    "$1,000 and above"),
]
def _buy_tier_key(usd: float) -> str:
    if usd >= 1000: return "high"
    if usd >= 501:  return "mid"
    return "low"


# ─── Generic wizard helpers ───────────────────────────────────────────────────
MISSEDCALL_STEPS = ["channel", "ca", "entry_mc", "ath_mc", "x", "symbol", "link", "chain", "confirm"]
FIXX_STEPS       = ["channel", "ca", "x", "confirm"]
BUYBOT_STEPS     = ["ca", "chain", "symbol", "min_buy", "emoji", "emoji_step",
                    "media_low", "media_mid", "media_high", "link", "confirm"]
BBEDIT_STEPS     = ["value"]

LATEMC_STEPS     = ["channel", "ca", "entry_mc", "ath_mc", "x", "confirm"]
FREEZE_STEPS     = ["channel", "ca", "confirm"]
XCHECK_STEPS     = ["channel", "ca", "confirm"]

RHFIX_STEPS      = ["post", "entry_mc", "now_mc", "x", "confirm"]

FLOW_STEPS = {"rhfix": RHFIX_STEPS, "missedcall": MISSEDCALL_STEPS, "fixx": FIXX_STEPS,
              "latemc": LATEMC_STEPS, "freeze": FREEZE_STEPS, "xcheck": XCHECK_STEPS,
              "buybot": BUYBOT_STEPS, "bbedit": BBEDIT_STEPS}


def _wiz_rows(pairs, per_row=3):
    """pairs: list of (label, value) -> InlineKeyboard rows with wiz:<value>."""
    rows, row = [], []
    for label, val in pairs:
        row.append(InlineKeyboardButton(label, callback_data=f"wiz:{val}"))
        if len(row) == per_row:
            rows.append(row); row = []
    if row: rows.append(row)
    return rows


def _wiz_kb(pairs, per_row=3, back=True):
    rows = _wiz_rows(pairs, per_row)
    tail = []
    if back: tail.append(InlineKeyboardButton("⬅️ Back", callback_data="wiz:__back"))
    tail.append(InlineKeyboardButton("❌ Cancel", callback_data="wiz:__cancel"))
    rows.append(tail)
    return InlineKeyboardMarkup(rows)


def _wiz_prompt(flow, step, data):
    """Return (text, keyboard) for one wizard step."""
    if flow == "missedcall":
        if step == "channel":
            return ("📡 <b>Skipped Call — Step 1/7</b>\n\nKOL channel ka username bhejo.\n"
                    "Example: <code>@SomeKOL</code>", _wiz_kb([], back=False))
        if step == "ca":
            return ("📡 <b>Step 2/7</b>\n\nToken ka <b>contract address</b> bhejo.", _wiz_kb([]))
        if step == "entry_mc":
            return ("📡 <b>Step 3/8</b>\n\n<b>Call MC</b> bhejo — jis market cap par KOL ne call di thi.\n"
                    "Example: <code>35K</code> · <code>1.2M</code>", _wiz_kb([]))
        if step == "ath_mc":
            return ("📈 <b>Step 4/8</b>\n\nCall ke baad token ka <b>highest/ATH MC</b> kitna gaya?\n"
                    "Live MC nahi — asal highest MC bhejo. Example: <code>129.5K</code>", _wiz_kb([]))
        if step == "x":
            return ("🚀 <b>Step 5/8</b>\n\nToken exact kitna X gaya? Decimal bhi bhej sakte ho.\n"
                    "Example: <code>3.7x</code>. Is par initial alert <b>3X+</b> hoga aur aage ke alerts normal track honge.",
                    _wiz_kb([]))
        if step == "symbol":
            return ("📡 <b>Step 6/8</b>\n\nToken ka <b>naam / symbol</b> bhejo (e.g. <code>PEPE</code>).",
                    _wiz_kb([("⚡ Auto", "auto")], per_row=1))
        if step == "link":
            return ("📡 <b>Step 7/8</b>\n\nKOL ki original call post ka <b>Telegram link</b> bhejo.\n"
                    "Example: <code>https://t.me/SomeKOL/123</code>",
                    _wiz_kb([("⏭ Skip", "skip")], per_row=1))
        if step == "chain":
            return ("📡 <b>Step 8/8</b>\n\n<b>Chain</b> chuno (RH tokens ke liye hamesha RH chuno).",
                    _wiz_kb([("SOL", "SOL"), ("ETH", "ETH"), ("BNB", "BNB"),
                             ("BASE", "BASE"), ("RH", "RH"), ("TON", "TON"),
                             ("⚡ Auto", "auto")]))
        if step == "confirm":
            d = data
            _emc_raw = d.get('entry_mc', '')
            _x_raw   = d.get('x', '')
            _achieved_line = ""
            if _emc_raw and _emc_raw != "auto" and _x_raw and _x_raw != "auto":
                try:
                    _emc_val = parse_mc_string(str(_emc_raw))
                    _x_val   = float(_x_raw)
                    if _emc_val > 0 and _x_val > 0:
                        _achieved_line = (f"Achieved MC (auto-calc): "
                                           f"<b>{fmt_mc(_emc_val * _x_val)}</b>\n")
                except Exception:
                    pass
            return (
                "✅ <b>Confirm Missed Call</b>\n\n"
                f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                f"CA: <code>{html.escape(d.get('ca',''))}</code>\n"
                f"Call MC: <b>{d.get('entry_mc','—')}</b>\n"
                f"ATH MC: <b>{d.get('ath_mc','—')}</b>\n"
                f"Exact X: <b>{d.get('x','—')}X</b>\n"
                f"Symbol: <b>{d.get('symbol','auto')}</b>\n"
                f"KOL link: {html.escape(d.get('link','') or '—')}\n"
                f"Chain: <b>{d.get('chain','auto')}</b>",
                _wiz_kb([("✅ Add Call", "__confirm")], per_row=1))

    if flow == "fixx":
        if step == "channel":
            return ("🛠 <b>X Update Fix — Step 1/3</b>\n\nKis KOL channel ki call fix karni hai?\n"
                    "Username bhejo, e.g. <code>@SomeKOL</code>", _wiz_kb([], back=False))
        if step == "ca":
            return ("🛠 <b>Step 2/3</b>\n\nUs call ka <b>contract address</b> bhejo.", _wiz_kb([]))
        if step == "x":
            return ("🛠 <b>Step 3/3</b>\n\nKaunsa X post karna hai? Number bhejo (e.g. <code>3</code>)\n"
                    "Ya <b>Auto</b> dabao — bot live/ATH data se saare missing X post kar dega.",
                    _wiz_kb([("🤖 Auto (all missing X)", "auto"), ("2X", "2"), ("3X", "3"),
                             ("5X", "5"), ("10X", "10")]))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm X Fix</b>\n\n"
                    f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                    f"CA: <code>{html.escape(d.get('ca',''))}</code>\n"
                    f"X: <b>{d.get('x','auto')}</b>\n\n"
                    "Bot missing X alerts channel me post karega (media na ho to text alert).",
                    _wiz_kb([("✅ Post X Update", "__confirm")], per_row=1))

    if flow == "latemc":
        if step == "channel":
            return ("🔧 <b>Late Track Fix — Step 1/3</b>\n\nKis KOL channel ki call late track hui?\n"
                    "Username bhejo, e.g. <code>@SomeKOL</code>", _wiz_kb([], back=False))
        if step == "ca":
            return ("🔧 <b>Step 2/3</b>\n\nUs token ka <b>contract address</b> bhejo.", _wiz_kb([]))
        if step == "entry_mc":
            return ("🔧 <b>Step 3/5</b>\n\n<b>Asli call MC</b> bhejo — jis MC par KOL ne call di thi.\n"
                    "Example: <code>35K</code> · <code>1.2M</code>", _wiz_kb([]))
        if step == "ath_mc":
            return ("📈 <b>Step 4/5</b>\n\nCall ke baad token ka <b>highest/ATH MC</b> kitna gaya?\n"
                    "Example: <code>129.5K</code>", _wiz_kb([]))
        if step == "x":
            return ("🚀 <b>Step 5/5</b>\n\nToken exact kitna X gaya? Decimal allowed hai.\n"
                    "Example: <code>3.7x</code> — initial alert 3X+ show karega.", _wiz_kb([]))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm Late Call Fix</b>\n\n"
                    f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                    f"CA: <code>{html.escape(d.get('ca',''))}</code>\n"
                    f"Call MC: <b>{html.escape(str(d.get('entry_mc','')))}</b>\n"
                    f"ATH MC: <b>{html.escape(str(d.get('ath_mc','')))}</b>\n"
                    f"Exact X: <b>{html.escape(str(d.get('x','')))}X</b>\n\n"
                    "Bot owner ke diye hue MC/X ko use karega, dropped-call post pehle set karega, "
                    "phir highest completed X alert post karke aage tracking jari rakhega.",
                    _wiz_kb([("✅ Fix Karo", "__confirm")], per_row=1))

    if flow == "freeze":
        if step == "channel":
            return ("⛔ <b>Call Freeze — Step 1/2</b>\n\nKis channel ki call rokni hai?\n"
                    "Username bhejo, e.g. <code>@SomeKOL</code>", _wiz_kb([], back=False))
        if step == "ca":
            return ("⛔ <b>Step 2/2</b>\n\nUs call ka <b>contract address</b> bhejo.", _wiz_kb([]))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm Freeze</b>\n\n"
                    f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                    f"CA: <code>{html.escape(d.get('ca',''))}</code>\n\n"
                    "Is call ke aage koi X alert nahi jayegi.",
                    _wiz_kb([("⛔ Freeze Karo", "__confirm")], per_row=1))

    if flow == "xcheck":
        if step == "channel":
            return ("📊 <b>Call Check — Step 1/2</b>\n\nKis channel ki call check karni hai?\n"
                    "Username bhejo, e.g. <code>@SomeKOL</code>", _wiz_kb([], back=False))
        if step == "ca":
            return ("📊 <b>Step 2/2</b>\n\nUs call ka <b>contract address</b> bhejo.", _wiz_kb([]))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm Check</b>\n\n"
                    f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                    f"CA: <code>{html.escape(d.get('ca',''))}</code>",
                    _wiz_kb([("📊 Check Karo", "__confirm")], per_row=1))

    if flow == "rhfix":
        if step == "post":
            return ("🛠 <b>RH galat MC fix — Step 1/4</b>\n\n"
                    "Jis channel ki call galat track hui hai, uski <b>post ka link</b> bhejo.\n"
                    "Example: <code>https://t.me/TomDegenInsights/464</code>\n\n"
                    "(Link na ho to token ka contract address bhi bhej sakte ho.)",
                    _wiz_kb([], back=False))
        if step == "entry_mc":
            return ("🛠 <b>Step 2/4</b>\n\n"
                    f"Call mili: <b>{html.escape(str(data.get('symbol') or '?'))}</b> "
                    f"@{html.escape(str(data.get('channel','')))} "
                    f"(chain: {html.escape(str(data.get('chain','?')))})\n"
                    f"Bot ne entry MC lagayi: <b>{html.escape(str(data.get('old_entry','?')))}</b> "
                    f"· bot ka X record: <b>{html.escape(str(data.get('old_x','?')))}</b>\n\n"
                    "KOL ne <b>kis MC par call di thi</b>? (asli call MC)\n"
                    "Example: <code>50K</code> · <code>1.2M</code>",
                    _wiz_kb([]))
        if step == "now_mc":
            return ("🛠 <b>Step 3/4</b>\n\n"
                    f"Call MC: <b>{html.escape(str(data.get('entry_mc','')))}</b>\n\n"
                    "Call ke baad MC <b>kitna upar gaya</b>? Chart par <b>abhi MC kitna hai</b> woh bhejo.\n"
                    "• Agar chart abhi bhi call MC par hi hai (pump nahi hua) to <b>Abhi bhi wahi</b> dabao.\n"
                    "• Ya bot ka live MC lene ke liye <b>Live MC</b> dabao.",
                    _wiz_kb([("📉 Abhi bhi wahi (pump nahi hua)", "same"),
                             ("🔄 Live MC lo", "live")], per_row=1))
        if step == "x":
            cx = data.get("calc_x", "")
            note = ""
            try:
                if float(cx) <= 1.05:
                    note = "\nℹ️ Chart call MC par hi hai — call ke baad pump nahi hua, X = <b>1x</b> banta hai."
            except Exception:
                pass
            btns = [(f"✅ {cx}x sahi hai", "ok")] if cx else []
            return ("🛠 <b>Step 4/4</b>\n\n"
                    f"Call MC <b>{html.escape(str(data.get('entry_mc','')))}</b> → abhi MC "
                    f"<b>{html.escape(str(data.get('now_mc','')))}</b>\n"
                    f"Is hisab se X = <b>{html.escape(str(cx))}x</b>{note}\n\n"
                    "Agar yeh X sahi hai to button dabao, warna sahi <b>X</b> type karo "
                    "(e.g. <code>1</code> ya <code>2.4</code>).",
                    _wiz_kb(btns, per_row=1))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm — RH galat MC fix</b>\n\n"
                    f"Channel: <b>@{html.escape(str(d.get('channel','')))}</b> · "
                    f"{html.escape(str(d.get('symbol') or '?'))}\n"
                    f"Purani entry: <b>{html.escape(str(d.get('old_entry','?')))}</b> "
                    f"(X record {html.escape(str(d.get('old_x','?')))})\n"
                    f"Sahi call MC: <b>{html.escape(str(d.get('entry_mc','')))}</b>\n"
                    f"Abhi MC: <b>{html.escape(str(d.get('now_mc','')))}</b>\n"
                    f"Sahi X: <b>{html.escape(str(d.get('x','')))}x</b>\n\n"
                    "Bot yeh karega:\n"
                    "1️⃣ purane galat X/alerts ka record + unke points hata dega\n"
                    "2️⃣ ranking/leaderboard se purana X hata dega\n"
                    "3️⃣ sahi MC se naya record shuru karega\n"
                    "4️⃣ channel ki 'Dropped a Call' post par sahi MC edit karega",
                    _wiz_kb([("✅ Fix Karo", "__confirm")], per_row=1))

    if flow == "buybot":
        g = data.get("group_title", "your group")
        tot = len(BUYBOT_STEPS) - 1
        def _n(step_name):
            return f"{BUYBOT_STEPS.index(step_name) + 1}/{tot}"
        if step == "ca":
            return (f"🤖 <b>Buy Bot Setup — Step {_n('ca')}</b>\nGroup: <b>{html.escape(str(g))}</b>\n\n"
                    "Send the <b>token contract address</b> you want buy alerts for.",
                    _wiz_kb([], back=False))
        if step == "chain":
            return (f"🤖 <b>Step {_n('chain')}</b>\n\nSelect the <b>chain</b> of this token.",
                    _wiz_kb([(c[1], c[0]) for c in BUYBOT_CHAINS] + [("⚡ Auto detect", "auto")], per_row=2))
        if step == "symbol":
            return (f"🤖 <b>Step {_n('symbol')}</b>\n\nSend the <b>token name / symbol</b> to show in alerts "
                    "(e.g. <code>PEPE</code>).",
                    _wiz_kb([("⚡ Auto", "auto")], per_row=1))
        if step == "min_buy":
            return (f"🤖 <b>Step {_n('min_buy')}</b>\n\n<b>Minimum buy</b> that triggers an alert (USD).\n"
                    "Pick one or send your own number.",
                    _wiz_kb([("$10", "10"), ("$25", "25"), ("$50", "50"),
                             ("$100", "100"), ("$250", "250"), ("$500", "500")], per_row=3))
        if step == "emoji":
            return (f"🤖 <b>Step {_n('emoji')}</b>\n\nWhich <b>emoji</b> should the buy bar use?\n"
                    "Pick one below, or just send any emoji — <b>premium emojis are supported</b>.",
                    _wiz_kb([(e, f"e{e}") for e in BUYBOT_EMOJIS], per_row=3))
        if step == "emoji_step":
            return (f"🤖 <b>Step {_n('emoji_step')}</b>\n\nOne emoji per how many <b>USD</b> bought?\n"
                    "Example: $10 → a $100 buy shows 10 emojis.",
                    _wiz_kb([("$5", "5"), ("$10", "10"), ("$25", "25"),
                             ("$50", "50"), ("$100", "100"), ("$250", "250")], per_row=3))
        if step.startswith("media_"):
            tkey  = step.split("_", 1)[1]
            label = dict((t[0], (t[1], t[2])) for t in BUYBOT_TIERS)[tkey]
            btns  = [("⏭ No media for this size", "skip")]
            if tkey != "low" and data.get("media_low"):
                btns.append(("♻️ Same as smaller buys", "same"))
            return (f"🤖 <b>Step {_n(step)}</b>\n\n"
                    f"<b>{label[0]} — {label[1]}</b>\n\n"
                    "Send the <b>photo, GIF or video</b> you want attached to buy alerts of this size.\n"
                    "Every buy size can use a different visual.",
                    _wiz_kb(btns, per_row=1))
        if step == "link":
            return (f"🤖 <b>Step {_n('link')}</b>\n\nSend a <b>website or Telegram link</b> for the alert "
                    "button, or skip it.", _wiz_kb([("⏭ Skip", "skip")], per_row=1))
        if step == "confirm":
            d = data
            def _mshow(k):
                m = d.get(f"media_{k}") or {}
                return f"{m.get('type','—')}" if m else "—"
            return ("✅ <b>Confirm Buy Bot</b>\n\n"
                    f"Group: <b>{html.escape(str(d.get('group_title','')))}</b>\n"
                    f"Token: <b>{html.escape(str(d.get('symbol','auto')))}</b>\n"
                    f"Chain: <b>{d.get('chain','auto')}</b>\n"
                    f"CA: <code>{html.escape(str(d.get('ca','')))}</code>\n"
                    f"Min buy: <b>${d.get('min_buy','10')}</b>\n"
                    f"Emoji: {d.get('emoji_display','🟢')} (1 per ${d.get('emoji_step','10')})\n"
                    f"Media — small: {_mshow('low')} | mid: {_mshow('mid')} | big: {_mshow('high')}\n"
                    f"Link: {html.escape(str(d.get('link','') or '—'))}",
                    _wiz_kb([("✅ Activate Buy Bot", "__confirm")], per_row=1))

    if flow == "bbedit":
        key = data.get("key", "")
        if key == "min_buy":
            return ("💵 <b>Trigger amount</b>\n\nSend the new minimum buy in USD, or pick one.",
                    _wiz_kb([("$10", "10"), ("$25", "25"), ("$50", "50"),
                             ("$100", "100"), ("$250", "250"), ("$500", "500")], per_row=3, back=False))
        if key == "emoji_step":
            return ("📐 <b>Emoji value</b>\n\nOne emoji per how many USD? Send a number or pick one.",
                    _wiz_kb([("$5", "5"), ("$10", "10"), ("$25", "25"),
                             ("$50", "50"), ("$100", "100"), ("$250", "250")], per_row=3, back=False))
        if key == "emoji":
            return ("😀 <b>Buy emoji</b>\n\nPick one or send any emoji (premium emojis work too).",
                    _wiz_kb([(e, f"e{e}") for e in BUYBOT_EMOJIS], per_row=3, back=False))
        if key == "link":
            return ("🌐 <b>Project link</b>\n\nSend the link for the alert button, or remove it.",
                    _wiz_kb([("🗑 Remove link", "skip")], per_row=1, back=False))
        if key == "symbol":
            return ("🏷 <b>Token label</b>\n\nSend the token name / symbol to show in alerts.",
                    _wiz_kb([], back=False))
        if key == "ca":
            return ("🔁 <b>Change token</b>\n\nSend the new <b>contract address</b>. "
                    "Chain will be detected automatically.", _wiz_kb([], back=False))
        if key == "chain":
            return ("⛓ <b>Chain</b>\n\nSelect the chain of this token.",
                    _wiz_kb([(c[1], c[0]) for c in BUYBOT_CHAINS], per_row=2, back=False))
        if key.startswith("media_"):
            tkey  = key.split("_", 1)[1]
            label = dict((t[0], (t[1], t[2])) for t in BUYBOT_TIERS)[tkey]
            return (f"🖼 <b>{label[0]} — {label[1]}</b>\n\n"
                    "Send the photo, GIF or video for buy alerts of this size.",
                    _wiz_kb([("🗑 Remove media", "skip")], per_row=1, back=False))
        return ("Send the new value.", _wiz_kb([], back=False))

    return ("…", _wiz_kb([]))


async def _wiz_send(target, flow, step, data):
    text, kb = _wiz_prompt(flow, step, data)
    await target.reply_text(text, parse_mode="HTML", reply_markup=kb,
                            disable_web_page_preview=True)


async def _wiz_goto(target, uid, flow, step, data):
    wizard_state[uid] = {"flow": flow, "step": step, "data": data}
    await _wiz_send(target, flow, step, data)


async def _wiz_next(target, uid):
    st    = wizard_state.get(uid)
    if not st: return
    steps = FLOW_STEPS[st["flow"]]
    i     = steps.index(st["step"])
    if i + 1 >= len(steps): return
    await _wiz_goto(target, uid, st["flow"], steps[i + 1], st["data"])


async def _wiz_back(target, uid):
    st    = wizard_state.get(uid)
    if not st: return
    steps = FLOW_STEPS[st["flow"]]
    i     = max(0, steps.index(st["step"]) - 1)
    await _wiz_goto(target, uid, st["flow"], steps[i], st["data"])


def _wiz_set(uid, key, value):
    wizard_state[uid]["data"][key] = value


# ─── Wizard entry points ──────────────────────────────────────────────────────
@owner_only
async def start_missedcall_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "missedcall", "channel", {})


@owner_only
async def cmd_fixx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button wizard: force-post X updates the bot never sent (2X/3X etc.)."""
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "fixx", "channel", {})


@owner_only
async def cmd_latemc_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button wizard: late-tracked call ka asli entry MC set karo."""
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "latemc", "channel", {})


@owner_only
async def cmd_rhfix_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rhfix — RH call ka galat MC/X fix karne ka button wizard."""
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "rhfix", "post", {})


@owner_only
async def cmd_freeze_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "freeze", "channel", {})


@owner_only
async def cmd_checkcall_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "xcheck", "channel", {})


# ─── Wizard input handling ────────────────────────────────────────────────────
async def wizard_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, msg) -> bool:
    uid = update.effective_user.id
    st  = wizard_state.get(uid)
    if not st: return False
    text = (msg.text or "").strip()
    if not text: return False
    if text.startswith("/"):
        if text.split()[0].lstrip("/").lower() in ("cancel", "stop"):
            wizard_state.pop(uid, None)
            await msg.reply_text("❌ Cancelled."); return True
        return False

    flow, step, data = st["flow"], st["step"], st["data"]

    if flow in ("missedcall", "fixx", "latemc", "freeze", "xcheck"):
        if step == "channel":
            ch = text.lstrip("@").strip().split("/")[-1]
            channels = load_channels()
            if ch.lower() not in [c.lower() for c in channels]:
                await msg.reply_text(
                    f"⚠️ @{html.escape(ch)} tracked channels me nahi hai.\n"
                    f"Pehle <code>/addchannel {html.escape(ch)}</code> karo, ya dobara username bhejo.",
                    parse_mode="HTML"); return True
            _wiz_set(uid, "channel", ch)
        elif step == "ca":
            _wiz_set(uid, "ca", text)
        elif step in ("entry_mc", "ath_mc"):
            if parse_mc_string(text) <= 0:
                await msg.reply_text("⚠️ Valid MC bhejo, e.g. 35K / 129.5K / 1.2M."); return True
            _wiz_set(uid, step, text)
        elif step == "x":
            try:
                xv = float(text.lower().rstrip("x").strip())
            except ValueError:
                await msg.reply_text("⚠️ Exact X number bhejo, e.g. 3.7x ya 1 (flat) ya 0.5 (rug)."); return True
            if xv <= 0:
                await msg.reply_text("⚠️ X 0 se zyada hona chahiye (1 = flat/no move)."); return True
            _wiz_set(uid, "x", str(xv).rstrip("0").rstrip("."))
        elif step == "symbol":
            _wiz_set(uid, "symbol", text.upper()[:20])
        elif step == "link":
            _wiz_set(uid, "link", text)
        else:
            return True
        await _wiz_next(msg, uid); return True

    if flow == "rhfix":
        if step == "post":
            found = _rhfix_find_call(text)
            if not found:
                await msg.reply_text(
                    "⚠️ Is link/CA se koi tracked call nahi mili.\n"
                    "Post ka sahi link (t.me/channel/123) ya token ka CA bhejo.")
                return True
            _k, _c = found
            _wiz_set(uid, "call_key", _k)
            _wiz_set(uid, "channel", _c.get("channel", ""))
            _wiz_set(uid, "ca", _c.get("ca", ""))
            _wiz_set(uid, "symbol", _c.get("symbol", ""))
            _wiz_set(uid, "chain", _c.get("chain", "?"))
            _wiz_set(uid, "old_entry", _c.get("entry_fmt", "N/A"))
            _wiz_set(uid, "old_x", fmt_x(call_peak_ratio(_c)) if call_peak_ratio(_c) > 0 else "—")
        elif step == "entry_mc":
            if parse_mc_string(text) <= 0:
                await msg.reply_text("⚠️ Valid MC bhejo, e.g. 50K / 129.5K / 1.2M."); return True
            _wiz_set(uid, "entry_mc", text)
        elif step == "now_mc":
            v = parse_mc_string(text)
            if v <= 0:
                await msg.reply_text("⚠️ Valid MC bhejo, e.g. 50K / 129.5K / 1.2M, ya buttons use karo."); return True
            _rhfix_set_now(uid, v)
        elif step == "x":
            try:
                xv = float(text.lower().rstrip("x").strip())
            except ValueError:
                await msg.reply_text("⚠️ X number bhejo, e.g. 1 ya 2.4"); return True
            if xv <= 0:
                await msg.reply_text("⚠️ X 0 se zyada hona chahiye (1 = pump nahi hua)."); return True
            _wiz_set(uid, "x", str(round(xv, 4)).rstrip("0").rstrip("."))
        else:
            return True
        await _wiz_next(msg, uid); return True

    if flow == "buybot":
        if step == "ca":
            _wiz_set(uid, "ca", text)
        elif step == "symbol":
            _wiz_set(uid, "symbol", text.upper()[:20])
        elif step in ("min_buy", "emoji_step"):
            try:
                v = float(re.sub(r"[^0-9.]", "", text) or 0)
            except ValueError:
                v = 0
            if v <= 0:
                await msg.reply_text("⚠️ Please send a number, e.g. 50"); return True
            _wiz_set(uid, step, str(int(v)))
        elif step == "emoji":
            ents = [e for e in (msg.entities or []) if e.type == "custom_emoji"]
            if ents:
                _wiz_set(uid, "emoji_id", ents[0].custom_emoji_id)
                _wiz_set(uid, "emoji", text.strip()[:4] or "🟢")
                _wiz_set(uid, "emoji_display", text.strip()[:4] or "🟢")
            else:
                _wiz_set(uid, "emoji_id", "")
                _wiz_set(uid, "emoji", text.strip()[:4])
                _wiz_set(uid, "emoji_display", text.strip()[:4])
        elif step.startswith("media_"):
            await msg.reply_text("⚠️ Please send a photo, GIF or video — or tap the skip button.")
            return True
        elif step == "link":
            if not text.startswith("http"):
                await msg.reply_text("⚠️ Please send a full link starting with https://"); return True
            _wiz_set(uid, "link", text)
        else:
            return True
        await _wiz_next(msg, uid); return True

    if flow == "bbedit":
        key = data.get("key", "")
        val = text
        if key in ("min_buy", "emoji_step"):
            try:
                v = float(re.sub(r"[^0-9.]", "", text) or 0)
            except ValueError:
                v = 0
            if v <= 0:
                await msg.reply_text("⚠️ Please send a number, e.g. 50"); return True
            val = int(v)
        elif key == "emoji":
            ents = [e for e in (msg.entities or []) if e.type == "custom_emoji"]
            val = {"emoji": text.strip()[:4] or "🟢",
                   "emoji_id": ents[0].custom_emoji_id if ents else ""}
        elif key == "symbol":
            val = text.upper()[:20]
        elif key == "link":
            if not text.startswith("http"):
                await msg.reply_text("⚠️ Please send a full link starting with https://"); return True
        elif key.startswith("media_"):
            await msg.reply_text("⚠️ Please send a photo, GIF or video."); return True
        wizard_state.pop(uid, None)
        await _bb_apply_and_show(msg, data.get("chat_id"), key, val)
        return True

    return False


async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    val   = query.data.split(":", 1)[1]
    st    = wizard_state.get(uid)
    if not st:
        await query.message.reply_text("This setup expired. Send /buybot to start again."); return
    flow, step, data = st["flow"], st["step"], st["data"]

    if val == "__cancel":
        wizard_state.pop(uid, None)
        await query.message.reply_text("❌ Cancelled."); return
    if val == "__back":
        await _wiz_back(query.message, uid); return
    if val == "__confirm":
        wizard_state.pop(uid, None)
        if flow == "missedcall":
            await _run_missedcall(update, context, query.message, data)
        elif flow == "fixx":
            await _run_fixx(update, context, query.message, data)
        elif flow == "latemc":
            await _run_latemc(update, context, query.message, data)
        elif flow == "freeze":
            await _run_freeze(update, context, query.message, data)
        elif flow == "xcheck":
            await _run_xcheck(update, context, query.message, data)
        elif flow == "rhfix":
            await _run_rhfix(update, context, query.message, data)
        elif flow == "buybot":
            await _run_buybot_activate(update, context, query.message, data)
        return

    # value buttons
    if flow == "rhfix" and step == "now_mc" and val in ("same", "live"):
        if val == "same":
            _rhfix_set_now(uid, parse_mc_string(str(data.get("entry_mc", ""))))
        else:
            live = 0.0
            try:
                _invalidate_dex_cache(data.get("ca", ""))
                _dx = await asyncio.wait_for(fetch_dexscreener(data.get("ca", "")), timeout=15)
                live = float((_dx or {}).get("mcap", 0) or 0)
            except Exception:
                live = 0.0
            if live <= 0:
                await query.message.reply_text(
                    "⚠️ Live MC nahi mila (data source busy). Chart se MC dekh kar type karke bhejo.")
                return
            _rhfix_set_now(uid, live)
        await _wiz_next(query.message, uid); return
    if flow == "rhfix" and step == "x" and val == "ok":
        _wiz_set(uid, "x", str(data.get("calc_x", "1")))
        await _wiz_next(query.message, uid); return
    if step == "emoji" and val.startswith("e"):
        _wiz_set(uid, "emoji", val[1:]); _wiz_set(uid, "emoji_display", val[1:])
        _wiz_set(uid, "emoji_id", "")
    elif flow == "bbedit":
        key = data.get("key", "")
        wizard_state.pop(uid, None)
        if key == "emoji" and val.startswith("e"):
            new = {"emoji": val[1:], "emoji_id": ""}
        elif val == "skip":
            new = None if key.startswith("media_") else ""
        elif key in ("min_buy", "emoji_step"):
            new = int(float(val))
        else:
            new = val
        await _bb_apply_and_show(query.message, data.get("chat_id"), key, new)
        return
    elif step.startswith("media_") and val in ("skip", "same"):
        if val == "same":
            _wiz_set(uid, step, data.get("media_low") or None)
        else:
            _wiz_set(uid, step, None)
    elif val in ("auto", "skip"):
        _wiz_set(uid, step, "" if val == "skip" else "auto")
        if step == "chain": _wiz_set(uid, "chain", "auto")
    else:
        _wiz_set(uid, step, val)
    await _wiz_next(query.message, uid)


# ─── Wizard runners ───────────────────────────────────────────────────────────
class _ArgCtx:
    """Minimal context stand-in so wizards can reuse existing arg-based commands."""
    def __init__(self, bot, args):
        self.bot = bot
        self.args = args


# ─── RH wrong-MC fix (owner button flow) ──────────────────────────────────────
def _rhfix_find_call(text: str):
    """Find a tracked call from a t.me/<channel>/<msg_id> post link or a CA.
    Returns (call_key, call) or None. Falls back to the archive."""
    text = (text or "").strip()
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})/(\d+)", text)
    ch, mid, ca = None, None, None
    if m:
        ch, mid = m.group(1).lower(), m.group(2)
    elif re.fullmatch(r"[A-Za-z0-9]{25,}", text):
        ca = text.lower()
    else:
        return None
    def _scan(d):
        hits = []
        for k, v in d.items():
            if ch is not None:
                if (v.get("channel", "") or "").lower() == ch and str(v.get("msg_id", "")) == mid:
                    hits.append((k, v))
            elif (v.get("ca", "") or "").lower() == ca:
                hits.append((k, v))
        return hits
    hits = _scan(tracked_calls)
    if not hits:
        try:
            hits = _scan(load_call_archive())
        except Exception:
            hits = []
    if ca is not None and len(hits) > 1:
        return None   # CA called by many channels — ask for the post link instead
    return hits[0] if hits else None


def _rhfix_set_now(uid, now_mc_val: float):
    d = wizard_state[uid]["data"]
    entry = parse_mc_string(str(d.get("entry_mc", "")))
    d["now_mc_val"] = float(now_mc_val)
    d["now_mc"] = fmt_mc(now_mc_val)
    d["calc_x"] = str(round(now_mc_val / entry, 2)).rstrip("0").rstrip(".") if entry > 0 else ""


async def _rhfix_revoke_points(channel: str, call_key: str) -> int:
    """Take back exactly the points this call earned (replays the award math)."""
    removed = 0
    async with _points_lock:
        pts_data = load_channel_points()
        e = pts_data.get(channel.lower())
        if not e:
            return 0
        awarded = e.get("awarded_tiers", {})
        tiers = awarded.pop(call_key, [])
        so_far = []
        for t in tiers:
            tier_pts = next((tp for tt, tp in POINT_TIERS if tt == t), 0)
            inc = tier_pts - sum(tp for tt, tp in POINT_TIERS if tt in so_far)
            if inc > 0:
                removed += inc
            so_far.append(t)
        e["awarded_tiers"] = awarded
        e["points"] = max(0, e.get("points", 0) - removed)
        pts_data[channel.lower()] = e
        save_channel_points(pts_data)
    return removed


async def _run_rhfix(update, context, target_msg, d):
    call_key = d.get("call_key", "")
    call = tracked_calls.get(call_key)
    if call is None:
        archive = load_call_archive()
        if call_key in archive:
            call = dict(archive[call_key])
            tracked_calls[call_key] = call
            archive.pop(call_key, None)
            save_call_archive(archive)
    if call is None:
        await target_msg.reply_text("⚠️ Call ab tracked/archive mein nahi mili. Dobara try karo."); return
    channel   = call.get("channel", d.get("channel", ""))
    entry_mc  = parse_mc_string(str(d.get("entry_mc", "")))
    now_mc    = float(d.get("now_mc_val", 0) or 0) or parse_mc_string(str(d.get("now_mc", "")))
    try:
        ratio = float(d.get("x", "1"))
    except ValueError:
        ratio = 1.0
    if entry_mc <= 0 or ratio <= 0:
        await target_msg.reply_text("⚠️ Call MC / X valid nahi — dobara try karo."); return
    await target_msg.reply_text("⏳ Fix ho raha hai…")

    old_fmt = call.get("entry_fmt", "N/A")
    old_x   = call_peak_ratio(call)
    old_ms  = sorted(sent_milestones.get(call_key, set()))
    old_mc  = float(call.get("entry_mc", 0) or 0)

    # 1) Wipe the wrong record: points + milestones + peak + ranking snapshots
    revoked = await _rhfix_revoke_points(channel, call_key)
    sent_milestones[call_key] = set()
    for k in ("peak_ratio", "peak_mc", "peak_mc_fmt", "peak_at", "last_ratio"):
        call.pop(k, None)
    cfg = load_config()
    for k in ("lb_milestone_snapshot", "champion_milestone_snapshot"):
        mp = cfg.get(k) or {}
        if call_key in mp:
            mp.pop(call_key, None); cfg[k] = mp
    for k in ("lb_excluded_call_keys", "champion_excluded_call_keys"):
        lst = cfg.get(k) or []
        if call_key in lst:
            cfg[k] = [x for x in lst if x != call_key]
    save_config(cfg)

    # 2) New record from the owner-confirmed MC
    cur_mc = cur_price = 0.0
    try:
        _invalidate_dex_cache(call.get("ca", ""))
        _dx = await asyncio.wait_for(fetch_dexscreener(call.get("ca", "")), timeout=15)
        cur_mc    = float((_dx or {}).get("mcap", 0) or 0)
        cur_price = float((_dx or {}).get("price", 0) or 0)
        if _dx and _dx.get("symbol") and not call.get("symbol"):
            call["symbol"] = _dx["symbol"]
    except Exception:
        pass
    if cur_mc > 0 and cur_price > 0:
        call["entry_price"] = cur_price * (entry_mc / cur_mc)
    elif old_mc > 0 and call.get("entry_price", 0):
        call["entry_price"] = call["entry_price"] * (entry_mc / old_mc)
    call["entry_mc"]        = entry_mc
    call["entry_fmt"]       = fmt_mc(entry_mc)
    call["entry_src"]       = "owner"
    call["entry_locked"]    = True
    call["entry_confirmed"] = True
    call["dex_pending"]     = False
    call["alert_pending"]   = False
    call["owner_reported_x"] = round(ratio, 4)
    call["owner_fixed_at"]  = datetime.utcnow().isoformat()
    call["last_ratio"]      = round(ratio, 4)
    if ratio >= 1:
        _update_peak(call, ratio, now_mc if now_mc > 0 else entry_mc * ratio)

    # 3) Milestones the corrected X really reached: recorded silently, points re-awarded
    new_ms = milestones_for_ratio(ratio)
    for ms in sorted(new_ms):
        sent_milestones[call_key].add(ms)
        try:
            await award_points_for_milestone(channel, call_key, ms)
        except Exception as e_p:
            logger.error(f"rhfix points: {e_p}")
    _save_milestones(); _save_tracked()

    # 4) Correct the already-posted "Dropped a Call" message
    edited = "ℹ️ original post id saved nahi tha (sirf tracking fix hui)"
    post_id = call.get("drop_post_id")
    if post_id:
        try:
            text = build_dropped_alert(
                channel, call.get("msg_id", 0), call.get("ca", ""),
                call.get("chain", "SOL"), call["entry_fmt"], call.get("symbol", ""))
            _ck = (call.get("chain", "SOL") or "").upper()
            _em = DROPPED_CHAIN_EMOJIS.get("ETH" if _ck == "EVM" else _ck, DROPPED_CALL_EMOJI)
            ok = False
            if userbot_client:
                if call.get("drop_post_media"):
                    ok = await _userbot_edit_caption_with_premium_emoji(
                        TARGET_CHANNEL, post_id, text, [_em, _em], forced_pack=_DROPPED_CALL_PACK)
                else:
                    ok = await _userbot_edit_with_premium_emoji(
                        TARGET_CHANNEL, post_id, text, forced_pack=_DROPPED_CALL_PACK)
            if not ok:
                try:
                    if call.get("drop_post_media"):
                        await context.bot.edit_message_caption(
                            chat_id=TARGET_CHANNEL, message_id=post_id, caption=text, parse_mode="HTML")
                    else:
                        await context.bot.edit_message_text(
                            chat_id=TARGET_CHANNEL, message_id=post_id, text=text,
                            parse_mode="HTML", disable_web_page_preview=True)
                    ok = True
                except Exception as e_b:
                    if "not modified" in str(e_b).lower():
                        ok = True
                    else:
                        edited = f"⚠️ post edit failed — {type(e_b).__name__}: {e_b}"
            if ok:
                edited = "✅ 'Dropped a Call' post edit ho gayi"
        except Exception as e:
            edited = f"⚠️ post edit failed — {type(e).__name__}: {e}"

    # 5) Refresh ranking posts so the old X disappears immediately
    notes = []
    try:
        if await _update_champions_with_premium_emojis(context.bot):
            cfg_set("last_champions_update", datetime.utcnow().isoformat()); notes.append("Champions ✅")
    except Exception as e:
        logger.warning(f"rhfix champions refresh: {e}"); notes.append("Champions ⚠️")
    try:
        if await _update_leaderboard_with_premium_emojis(context.bot):
            cfg_set("last_leaderboard_update", datetime.utcnow().isoformat()); notes.append("Leaderboard ✅")
    except Exception as e:
        logger.warning(f"rhfix leaderboard refresh: {e}"); notes.append("Leaderboard ⚠️")

    await target_msg.reply_text(
        f"✅ <b>RH galat MC fix ho gaya</b>\n\n"
        f"📡 @{html.escape(str(channel))} · {html.escape(str(call.get('symbol') or '?'))}\n"
        f"Purani entry: <b>{html.escape(str(old_fmt))}</b> (X record {fmt_x(old_x) if old_x > 0 else '—'}, "
        f"alerts: {', '.join(str(m)+'X' for m in old_ms) if old_ms else '—'})\n"
        f"Nayi entry: <b>{html.escape(call['entry_fmt'])}</b> → X: <b>{fmt_x(ratio)}</b>\n"
        f"Points wapas liye: <b>{revoked}</b>\n"
        f"Naye milestones (silent): {', '.join(str(m)+'X' for m in sorted(new_ms)) if new_ms else '—'}\n\n"
        f"📝 {html.escape(edited)}\n"
        f"🔄 Refresh: {' · '.join(notes) if notes else '—'}\n\n"
        f"ℹ️ Agar channel mein galat X alert (jaise 2X) pehle ja chuka hai, usay manually delete kar do.\n"
        f"Aage ka tracking isi sahi entry MC se chalega.",
        parse_mode="HTML")


async def _run_missedcall(update, context, target_msg, d):
    args = [d.get("channel", ""), d.get("ca", ""), d.get("x") or "2",
            d.get("entry_mc") or "0",
            d.get("symbol") or "-",
            d.get("link") or "-",
            d.get("chain") or "auto",
            d.get("ath_mc") or "0"]
    await target_msg.reply_text("⏳ Adding missed call…")
    # cmd_addmissedcall replies through update.message — build a shim
    class _U:
        effective_user = update.effective_user
        message = target_msg
    await cmd_addmissedcall(_U(), _ArgCtx(context.bot, args))


def _wiz_shim(update, target_msg):
    class _U:
        effective_user = update.effective_user
        message = target_msg
    return _U()


async def _run_latemc(update, context, target_msg, d):
    args = [d.get("channel", ""), d.get("ca", ""), str(d.get("entry_mc", "")),
            str(d.get("ath_mc", "")), str(d.get("x", ""))]
    await target_msg.reply_text("⏳ Entry MC fix ho raha hai…")
    await cmd_fixmc(_wiz_shim(update, target_msg), _ArgCtx(context.bot, args))


async def _run_freeze(update, context, target_msg, d):
    args = [d.get("channel", ""), d.get("ca", "")]
    await cmd_freezecall(_wiz_shim(update, target_msg), _ArgCtx(context.bot, args))


async def _run_xcheck(update, context, target_msg, d):
    args = [d.get("channel", ""), d.get("ca", "")]
    await target_msg.reply_text("⏳ Call check ho rahi hai…")
    await cmd_xcheck(_wiz_shim(update, target_msg), _ArgCtx(context.bot, args))


async def _run_fixx(update, context, target_msg, d):
    """Recompute the real X for a call and force-post every missing milestone."""
    channel = d.get("channel", "")
    ca      = d.get("ca", "")
    want    = d.get("x") or "auto"
    call_key = next((k for k, c in tracked_calls.items()
                     if c.get("channel", "").lower() == channel.lower()
                     and c.get("ca", "").lower() == ca.lower()), None)
    if not call_key:
        await target_msg.reply_text(
            f"⚠️ Yeh call tracked nahi hai (@{html.escape(channel)}).\n"
            f"Pehle /addmissedcall (ya /missedcall wizard) se add karo.", parse_mode="HTML"); return
    call = tracked_calls[call_key]
    status = await target_msg.reply_text("⏳ Live data check ho raha hai…")
    try:
        ratio, peak_mc = await compute_true_x(call)
    except Exception as e:
        logger.error(f"fixx compute_true_x: {e}")
        ratio, peak_mc = 0.0, 0.0
    if ratio > 0:
        call["last_ratio"] = round(ratio, 4)
        _update_peak(call, ratio, peak_mc)
        _save_tracked()

    if want == "auto":
        targets = [m for m in milestones_for_ratio(ratio) if m not in sent_milestones.get(call_key, set())]
    else:
        try:    targets = [int(float(want))]
        except (TypeError, ValueError): targets = []
    targets = sorted(set(targets))[-4:]
    if not targets:
        await status.edit_text(
            f"ℹ️ Koi missing X nahi mila. Abhi ratio: <b>{fmt_x(ratio)}</b>.\n"
            f"Manually X post karna ho to wizard me number chuno.", parse_mode="HTML"); return

    cur_fmt = fmt_mc(peak_mc) if peak_mc > 0 else call.get("entry_fmt", "N/A")
    posted = []
    silent_ms = []
    for ms in targets:
        res = await send_alert(
            context.bot, call.get("channel"), call.get("msg_id", 0), ms,
            call.get("chain", "SOL"), call.get("entry_fmt", "N/A"), cur_fmt,
            call.get("ca", ""), call.get("symbol", "UNKNOWN"))
        if res:
            # "silent" = milestone recorded but not posted (no media for that X)
            if res != "silent":
                posted.append(ms)
            else:
                silent_ms.append(ms)
            sent_milestones[call_key].add(ms)
            await award_points_for_milestone(call.get("channel"), call_key, ms)
        await asyncio.sleep(1.5)
    _save_milestones()
    await status.edit_text(
        f"✅ <b>X Update Posted</b>\n\n@{html.escape(channel)} — "
        f"{html.escape(str(call.get('symbol','?')))}\n"
        f"Real ratio: <b>{fmt_x(ratio)}</b>\n"
        f"Posted: <b>{', '.join(str(m) + 'X' for m in posted) or 'none'}</b>"
        + (f"\nRecorded only (no media set): <b>{', '.join(str(m) + 'X' for m in silent_ms)}</b>"
           if silent_ms else ""),
        parse_mode="HTML")


# ─── Buy bot engine ───────────────────────────────────────────────────────────
def _resolve_token_pool(ca: str, chain_hint: str = ""):
    """Best pair for a token: {chain, gecko, pair, symbol, mc, price}."""
    rev = {"solana": "SOL", "ethereum": "ETH", "eth": "ETH", "bsc": "BNB",
           "base": "BASE", "ton": "TON", "robinhood": "RH"}
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
                         headers=HEADERS, timeout=15)
        pairs = (r.json().get("pairs") or []) if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"buybot dexscreener lookup failed: {e}")
        pairs = []
    if chain_hint and pairs:
        want = BUYBOT_CHAIN_MAP.get(chain_hint, {}).get("gecko", "")
        alias = {want, "ethereum" if want == "eth" else want}
        same = [p for p in pairs if (p.get("chainId") or "").lower() in alias]
        if same: pairs = same
    if pairs:
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        cid  = (best.get("chainId") or "").lower()
        return {
            "chain":  rev.get(cid, chain_hint or "SOL"),
            "gecko":  "eth" if cid == "ethereum" else cid,
            "pair":   best.get("pairAddress", ""),
            "symbol": (best.get("baseToken") or {}).get("symbol", "TOKEN"),
            "mc":     float(best.get("marketCap") or best.get("fdv") or 0),
            "price":  float(best.get("priceUsd") or 0),
        }

    # GeckoTerminal fallback (Robinhood / TON / very new pools)
    nets = ([BUYBOT_CHAIN_MAP[chain_hint]["gecko"]] if chain_hint in BUYBOT_CHAIN_MAP
            else [c[2] for c in BUYBOT_CHAINS])
    for net in nets:
        data = _gecko_get_json(
            f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{ca}/pools?page=1",
            tries=2)
        pools = (data or {}).get("data") or []
        if not pools: continue
        def _liq(p):
            try:    return float(((p.get("attributes") or {}).get("reserve_in_usd")) or 0)
            except Exception: return 0.0
        best = max(pools, key=_liq)
        a    = best.get("attributes") or {}
        name = (a.get("name") or "TOKEN").split("/")[0].strip()
        try:    mc = float(a.get("market_cap_usd") or a.get("fdv_usd") or 0)
        except Exception: mc = 0.0
        try:    px = float(a.get("base_token_price_usd") or 0)
        except Exception: px = 0.0
        return {"chain": rev.get(net, chain_hint or "SOL"), "gecko": net,
                "pair": a.get("address", ""), "symbol": name[:20] or "TOKEN",
                "mc": mc, "price": px}
    return None


def _fetch_pool_buys(gecko_net: str, pair: str, min_usd: float):
    """Recent BUY trades of a pool (GeckoTerminal). Returns [] on failure."""
    if not (gecko_net and pair): return []
    url = (f"https://api.geckoterminal.com/api/v2/networks/{gecko_net}/pools/{pair}/trades"
           f"?trade_volume_in_usd_greater_than={int(max(min_usd, 0))}")
    data = _gecko_get_json(url, tries=3)
    if not data:
        logger.warning(f"buybot trades fetch empty: {gecko_net}/{str(pair)[:10]}")
        return []
    out = []
    for t in ((data or {}).get("data") or []):
        a = t.get("attributes") or {}
        if (a.get("kind") or "").lower() != "buy": continue
        try:    usd = float(a.get("volume_in_usd") or 0)
        except Exception: usd = 0.0
        try:    amt = float(a.get("to_token_amount") or 0)
        except Exception: amt = 0.0
        try:    px = float(a.get("price_to_in_usd") or 0)
        except Exception: px = 0.0
        out.append({"tx": a.get("tx_hash", ""), "usd": usd, "amt": amt,
                    "ts": a.get("block_timestamp", ""),
                    "from": a.get("tx_from_address", ""), "price": px})
    out.sort(key=lambda x: x["ts"])
    return out


def _buy_emoji_bar(cfgb, usd):
    step  = max(float(cfgb.get("emoji_step") or 10), 1)
    count = max(1, min(int(usd // step), BUYBOT_MAX_EMOJIS))
    eid   = cfgb.get("emoji_id")
    ch    = cfgb.get("emoji") or "🟢"
    if eid:
        return "".join(f'<tg-emoji emoji-id="{eid}">{html.escape(ch)}</tg-emoji>' for _ in range(count))
    return html.escape(ch) * count


def _buy_tier_media(cfgb, usd):
    """Media for this buy size, falling back to the nearest configured tier."""
    media = cfgb.get("media") or {}
    order = {"high": ["high", "mid", "low"], "mid": ["mid", "low", "high"],
             "low":  ["low", "mid", "high"]}[_buy_tier_key(usd)]
    for k in order:
        m = media.get(k)
        if m and m.get("file_id"):
            return m
    return None


def _buy_alert_text(cfgb, trade, mc, price):
    sym   = html.escape(str(cfgb.get("symbol", "TOKEN")))
    chain = cfgb.get("chain", "SOL")
    meta  = BUYBOT_CHAIN_MAP.get(chain, BUYBOT_CHAIN_MAP["SOL"])
    bar   = _buy_emoji_bar(cfgb, trade["usd"])
    tier  = _buy_tier_key(trade["usd"])
    head  = f"<b>{sym} Buy!</b>"
    if tier == "high" and cfgb.get("whale_line", True):
        head = f"🐳 <b>BIG {sym} BUY!</b>"
    text = f"{head}\n{bar}\n\n💵 <b>${trade['usd']:,.2f}</b>\n"
    if trade.get("amt"):
        text += f"🪙 {trade['amt']:,.0f} {sym}\n"
    if cfgb.get("price_line", True):
        p = price or trade.get("price") or 0
        if p > 0:
            text += "💲 Price $" + f"{p:.10f}".rstrip("0").rstrip(".") + "\n"
    if cfgb.get("mc_line", True) and mc > 0:
        text += f"💰 Market Cap {fmt_mc(mc)}\n"
    text += f"⛓ {meta['label']}"
    return text


def _buy_alert_kb(cfgb, trade):
    chain = cfgb.get("chain", "SOL")
    meta  = BUYBOT_CHAIN_MAP.get(chain, BUYBOT_CHAIN_MAP["SOL"])
    rows  = [[InlineKeyboardButton("📈 Chart",
              url=f"https://dexscreener.com/{BUYBOT_DEXPATH.get(chain,'solana')}/{cfgb.get('ca')}")]]
    if trade.get("tx"):
        rows[0].append(InlineKeyboardButton("🧾 TX", url=meta["tx"] + trade["tx"]))
    if cfgb.get("link"):
        rows.append([InlineKeyboardButton("🌐 Project", url=cfgb["link"])])
    return InlineKeyboardMarkup(rows)


async def _post_buy_alert(bot, chat_id, cfgb, trade, mc, price):
    text  = _buy_alert_text(cfgb, trade, mc, price)
    kb    = _buy_alert_kb(cfgb, trade)
    media = _buy_tier_media(cfgb, trade["usd"])

    async def _send(body, with_media=True):
        if media and with_media:
            mt, fid, cap = media.get("type"), media.get("file_id"), body[:1024]
            if mt == "photo":
                return await bot.send_photo(chat_id, fid, caption=cap, parse_mode="HTML", reply_markup=kb)
            if mt == "animation":
                return await bot.send_animation(chat_id, fid, caption=cap, parse_mode="HTML", reply_markup=kb)
            if mt == "video":
                return await bot.send_video(chat_id, fid, caption=cap, parse_mode="HTML", reply_markup=kb)
        return await bot.send_message(chat_id, body, parse_mode="HTML",
                                      disable_web_page_preview=True, reply_markup=kb)

    try:
        await _send(text)
        return True
    except Exception as e:
        logger.warning(f"buybot send failed {chat_id}: {e}")
    plain = re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)
    for attempt in (lambda: _send(plain), lambda: _send(plain, with_media=False)):
        try:
            await attempt()
            return True
        except Exception as e2:
            logger.warning(f"buybot fallback send failed {chat_id}: {e2}")
    return False


BUYBOT_ENABLED = False   # Buy Bot feature permanently removed

# ─── Buy bot settings panel ───────────────────────────────────────────────────
def _bb_panel(cfgb):
    cid  = cfgb.get("chat_id")
    def B(label, key): return InlineKeyboardButton(label, callback_data=f"bb:{cid}:{key}")
    tick = lambda v: "✅" if v else "❌"
    media = cfgb.get("media") or {}
    mcount = sum(1 for k in ("low", "mid", "high") if (media.get(k) or {}).get("file_id"))
    return InlineKeyboardMarkup([
        [B(f"🖼 Visuals ({mcount}/3)", "media"), B(f"💵 Trigger ${cfgb.get('min_buy',10)}", "min_buy")],
        [B(f"{cfgb.get('emoji','🟢')} Buy Emoji", "emoji"),
         B(f"📐 Emoji Value ${cfgb.get('emoji_step',10)}", "emoji_step")],
        [B(f"{tick(cfgb.get('price_line', True))} Price Row", "t_price_line"),
         B(f"{tick(cfgb.get('mc_line', True))} Market Cap Row", "t_mc_line")],
        [B(f"{tick(cfgb.get('whale_line', True))} Big Buy Callout", "t_whale_line"),
         B(f"🏷 Label: {cfgb.get('symbol','TOKEN')}", "symbol")],
        [B("🌐 Project Link", "link"), B(f"⛓ Chain: {cfgb.get('chain','SOL')}", "chain")],
        [B("🔁 Swap Token", "ca")],
        [B("🧪 Send Test Alert", "test")],
        [B("🟢 Alerts: Running" if cfgb.get("active") else "⏸ Alerts: Paused", "t_active")],
        [B("🔄 Run Setup Again", "rerun")],
    ])


def _bb_panel_text(cfgb):
    last = cfgb.get("last_poll", "")
    err  = cfgb.get("last_error", "")
    return ("🤖 <b>Buy Bot Control Panel</b>\n\n"
            f"Group: <b>{html.escape(str(cfgb.get('group_title','')))}</b>\n"
            f"Token: <b>{html.escape(str(cfgb.get('symbol','TOKEN')))}</b> "
            f"({cfgb.get('chain','SOL')})\n"
            f"CA: <code>{html.escape(str(cfgb.get('ca','')))}</code>\n"
            f"Alerts sent: <b>{cfgb.get('alerts_sent',0)}</b>\n"
            + (f"Last check: {html.escape(str(last)[:19])}\n" if last else "")
            + (f"⚠️ {html.escape(str(err))}\n" if err else "")
            + "\nTap any button below to change a setting.")


def _bb_media_panel(cfgb):
    cid   = cfgb.get("chat_id")
    media = cfgb.get("media") or {}
    rows  = []
    for k, name, rng in BUYBOT_TIERS:
        has = (media.get(k) or {}).get("type")
        rows.append([InlineKeyboardButton(
            f"{'✅' if has else '➕'} {name} ({rng})" + (f" — {has}" if has else ""),
            callback_data=f"bb:{cid}:media_{k}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"bb:{cid}:panel")])
    return InlineKeyboardMarkup(rows)


async def bb_show_panel(target, chat_id, edit=False):
    cfgb = load_buybots().get(str(chat_id))
    if not cfgb:
        await target.reply_text("No buy bot found for that group. Send /buybot to set one up."); return
    text, kb = _bb_panel_text(cfgb), _bb_panel(cfgb)
    if edit:
        try:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb,
                                   disable_web_page_preview=True)
            return
        except Exception:
            pass
    await target.reply_text(text, parse_mode="HTML", reply_markup=kb,
                            disable_web_page_preview=True)


async def _bb_apply_and_show(target, chat_id, key, value):
    bots = load_buybots(); cfgb = bots.get(str(chat_id))
    if not cfgb:
        await target.reply_text("That buy bot no longer exists."); return
    if key == "emoji" and isinstance(value, dict):
        cfgb["emoji"]    = value.get("emoji") or "🟢"
        cfgb["emoji_id"] = value.get("emoji_id") or ""
    elif key.startswith("media_"):
        cfgb.setdefault("media", {})[key.split("_", 1)[1]] = value or None
    elif key == "ca":
        info = await asyncio.to_thread(_resolve_token_pool, str(value), "")
        if not info:
            await target.reply_text("⚠️ I couldn't find that token. Send /buybotsettings to try again."); return
        cfgb.update({"ca": str(value), "chain": info["chain"], "gecko": info["gecko"],
                     "pair": info["pair"], "symbol": info["symbol"],
                     "last_ts": "", "seen_tx": []})
    elif key == "chain":
        cfgb["chain"] = value
        cfgb["gecko"] = BUYBOT_CHAIN_MAP.get(value, {}).get("gecko", "solana")
        info = await asyncio.to_thread(_resolve_token_pool, cfgb.get("ca", ""), value)
        if info:
            cfgb.update({"pair": info["pair"], "gecko": info["gecko"]})
        cfgb["last_ts"] = ""; cfgb["seen_tx"] = []
    else:
        cfgb[key] = value
    bots[str(chat_id)] = cfgb; save_buybots(bots)
    await target.reply_text("✅ Saved.")
    await bb_show_panel(target, chat_id)


async def cmd_buybotcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnostics: is the data source returning buys for this pool?"""
    user = update.effective_user
    bots = load_buybots()
    mine = [(cid, c) for cid, c in bots.items()
            if c.get("owner_id") == user.id or user.id in OWNER_IDS]
    if not mine:
        await update.message.reply_text("No buy bot found. Send /buybot first."); return
    out = []
    for cid, c in mine[:5]:
        gnet = c.get("gecko") or BUYBOT_CHAIN_MAP.get(c.get("chain", "SOL"), {}).get("gecko", "solana")
        pool = c.get("pair")
        if not pool:
            info = await asyncio.to_thread(_resolve_token_pool, c.get("ca", ""), c.get("chain", ""))
            pool = (info or {}).get("pair", "")
            if info:
                c.update({"pair": info["pair"], "gecko": info["gecko"]}); gnet = info["gecko"]
                bots[cid] = c; save_buybots(bots)
        trades = await asyncio.to_thread(_fetch_pool_buys, gnet, pool,
                                        float(c.get("min_buy") or 10)) if pool else []
        out.append(
            f"<b>{html.escape(str(c.get('symbol','?')))}</b> ({c.get('chain','?')}) — "
            f"{'🟢 running' if c.get('active') else '⏸ paused'}\n"
            f"Pool: <code>{html.escape(str(pool or 'not found'))}</code>\n"
            f"Buys visible now: <b>{len(trades)}</b> (min ${c.get('min_buy',10)})\n"
            f"Alerts sent: {c.get('alerts_sent',0)}"
            + (f"\n⚠️ {html.escape(str(c.get('last_error')))}" if c.get("last_error") else ""))
    await update.message.reply_text("🧪 <b>Buy Bot Check</b>\n\n" + "\n\n".join(out),
                                    parse_mode="HTML")


async def wizard_handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE, msg) -> bool:
    """Capture photo / GIF / video for buy-alert tiers."""
    uid = update.effective_user.id
    st  = wizard_state.get(uid)
    if not st: return False
    flow, step, data = st["flow"], st["step"], st["data"]
    key = step if flow == "buybot" else data.get("key", "")
    if not key.startswith("media_"): return False
    if msg.photo:       m = {"type": "photo",     "file_id": msg.photo[-1].file_id}
    elif msg.animation: m = {"type": "animation", "file_id": msg.animation.file_id}
    elif msg.video:     m = {"type": "video",     "file_id": msg.video.file_id}
    elif msg.document and (msg.document.mime_type or "").startswith(("image/", "video/")):
        m = {"type": "animation" if "gif" in (msg.document.mime_type or "") else "video",
             "file_id": msg.document.file_id}
    else:
        return False
    if flow == "buybot":
        _wiz_set(uid, step, m)
        await msg.reply_text(f"✅ {m['type'].title()} saved for this buy size.")
        await _wiz_next(msg, uid)
    else:
        wizard_state.pop(uid, None)
        await _bb_apply_and_show(msg, data.get("chat_id"), key, m)
    return True


async def _run_buybot_activate(update, context, target_msg, d):
    status = await target_msg.reply_text("⏳ Checking the token…")
    ca    = d.get("ca", "")
    chain = d.get("chain") if d.get("chain") not in ("", "auto", None) else ""
    info  = await asyncio.to_thread(_resolve_token_pool, ca, chain)
    if not info:
        await status.edit_text(
            "⚠️ I couldn't find a live pool for this token. "
            "Check the contract address / chain and send /buybot again.")
        return
    chat_id = str(d.get("chat_id"))
    user    = update.effective_user
    bots    = load_buybots()
    symbol  = d.get("symbol") if d.get("symbol") not in ("", "auto", None) else info["symbol"]
    media   = {k: (d.get(f"media_{k}") or None) for k, _n, _r in BUYBOT_TIERS}
    bots[chat_id] = {
        "chat_id": int(chat_id),
        "group_title": d.get("group_title", ""),
        "owner_id": user.id, "owner_username": user.username or "",
        "ca": ca, "chain": chain or info["chain"], "gecko": info["gecko"],
        "pair": info["pair"], "symbol": symbol,
        "min_buy": int(float(d.get("min_buy") or 10)),
        "emoji": d.get("emoji") or "🟢",
        "emoji_id": d.get("emoji_id") or "",
        "emoji_step": int(float(d.get("emoji_step") or 10)),
        "media": media,
        "link": d.get("link") or "",
        "price_line": True, "mc_line": True, "whale_line": True,
        "active": True, "alerts_sent": 0,
        "created": datetime.utcnow().isoformat(),
        "last_ts": "", "seen_tx": [], "last_error": "", "last_poll": "",
    }
    save_buybots(bots)
    mset = ", ".join(f"{k}: {(media.get(k) or {}).get('type','—')}" for k in ("low", "mid", "high"))
    await status.edit_text(
        f"✅ <b>Buy Bot Activated</b>\n\n"
        f"Group: <b>{html.escape(str(d.get('group_title','')))}</b>\n"
        f"Token: <b>{html.escape(symbol)}</b> ({bots[chat_id]['chain']})\n"
        f"Min buy: <b>${bots[chat_id]['min_buy']}</b>\n"
        f"Emoji: {d.get('emoji_display','🟢')} per ${bots[chat_id]['emoji_step']}\n"
        f"Media — {html.escape(mset)}\n\n"
        f"Buy alerts will start appearing in your group.\n"
        f"Use /buybotsettings to fine-tune, /buybotcheck to test the data feed.",
        parse_mode="HTML")
    await bb_show_panel(target_msg, chat_id)
    try:
        await context.bot.send_message(
            int(chat_id),
            f"🤖 <b>Buy Bot is live for {html.escape(symbol)}!</b>\n"
            f"Every buy above ${bots[chat_id]['min_buy']} will be posted here.",
            parse_mode="HTML")
    except Exception as e:
        logger.warning(f"buybot group confirm failed: {e}")
    await notify_owners(
        context.bot,
        f"🤖 <b>New Buy Bot Activated</b>\n\n"
        f"User: @{html.escape(user.username or str(user.id))}\n"
        f"Group: <b>{html.escape(str(d.get('group_title','')))}</b> (<code>{chat_id}</code>)\n"
        f"Token: <b>{html.escape(symbol)}</b> / {bots[chat_id]['chain']}\n"
        f"CA: <code>{html.escape(ca)}</code>\n"
        f"Min buy: ${bots[chat_id]['min_buy']}")

async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot added to a group -> offer the Buy Bot setup button."""
    try:
        cm = update.my_chat_member
        if not cm: return
        chat = cm.chat
        if chat.type not in ("group", "supergroup"): return
        new = cm.new_chat_member
        if new.status not in ("member", "administrator"): return
        me = await context.bot.get_me()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "🤖 Set up Buy Bot", url=f"https://t.me/{me.username}?start=buybot_{chat.id}")]])
        await context.bot.send_message(
            chat.id,
            "🤖 <b>Buy Bot ready</b>\n\nTap below to set up buy alerts for this group — "
            "I'll ask for the token, chain, minimum buy and emoji step by step.",
            parse_mode="HTML", reply_markup=kb)
        await notify_owners(
            context.bot,
            f"➕ Bot added to group <b>{html.escape(chat.title or '')}</b> "
            f"(<code>{chat.id}</code>) by @{html.escape((cm.from_user.username or str(cm.from_user.id)))}")
    except Exception as e:
        logger.warning(f"on_bot_added: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set! Railway → Variables me BOT_TOKEN add karo.")
        raise SystemExit(1)
    if not OWNER_IDS:
        logger.warning("⚠️ OWNER_ID not set — owner commands disabled.")
    _start_health_server()
    _apply_emoji_overrides()   # restore owner's saved CheesePad/PinkSale emoji IDs

    # ── SPEED FIX ────────────────────────────────────────────────────────
    # concurrent_updates() ke bagair python-telegram-bot ek waqt me sirf EK
    # update process karta hai. Ek slow command (DexScreener/Gecko fetch)
    # baaki sab commands ko block kar deti thi → "bot hang ho gaya" wala issue.
    _req = HTTPXRequest(connection_pool_size=512, connect_timeout=15.0,
                        read_timeout=45.0, write_timeout=45.0, pool_timeout=20.0)
    _req_upd = HTTPXRequest(connection_pool_size=64, connect_timeout=15.0,
                            read_timeout=45.0, write_timeout=45.0, pool_timeout=20.0)
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(post_init)
           .concurrent_updates(512)
           .request(_req)
           .get_updates_request(_req_upd)
           .build())

    # Public commands
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("command",   cmd_command))
    app.add_handler(CommandHandler("history",   cmd_history))
    app.add_handler(CommandHandler("weekly",    cmd_weekly))
    app.add_handler(CommandHandler("15days",    cmd_15days))
    app.add_handler(CommandHandler("30days",    cmd_30days))
    app.add_handler(CommandHandler("topcalls",  cmd_topcalls))
    app.add_handler(CommandHandler("setlookupmedia",   cmd_setlookupmedia))
    app.add_handler(CommandHandler("clearlookupmedia", cmd_clearlookupmedia))
    app.add_handler(CommandHandler("linkinfo",  cmd_linkinfo))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("linkme",    cmd_linkme))
    app.add_handler(CommandHandler("linksmart", cmd_linksmart))
    app.add_handler(CommandHandler("submit",    cmd_submit))

    # Callbacks — specific patterns FIRST, then generic catch-all
    app.add_handler(CallbackQueryHandler(cb_setemoji, pattern=r"^setemoji:"))
    app.add_handler(CallbackQueryHandler(cb_ownerhelp, pattern=r"^oh:"))
    app.add_handler(CallbackQueryHandler(cb_x_alert_details, pattern=r"^xbtn:"))
    app.add_handler(CallbackQueryHandler(deleted_post_decision_callback, pattern=r"^dpd:"))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Owner — userbot
    app.add_handler(CommandHandler("reconnectuserbot", cmd_reconnectuserbot))
    app.add_handler(CommandHandler("forceupdateposts", cmd_forceupdateposts))
    app.add_handler(CommandHandler("markseen",         cmd_markseen))
    app.add_handler(CommandHandler("userbotlogin",  cmd_userbotlogin))
    app.add_handler(CommandHandler("userbotresend", cmd_userbotresend))
    app.add_handler(CommandHandler("userbotlogout", cmd_userbotlogout))
    app.add_handler(CommandHandler("userbotcheck",  cmd_userbotcheck))
    app.add_handler(CommandHandler("qrlogin",       cmd_qrlogin))

    # Owner — channels
    app.add_handler(CommandHandler("mychannels",    cmd_mychannels))
    app.add_handler(CommandHandler("addchannel",    cmd_addchannel))
    app.add_handler(CommandHandler("special",       cmd_special))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))


    # Owner — post updates
    app.add_handler(CommandHandler("updateleaderboard", cmd_updateleaderboard))
    app.add_handler(CommandHandler("updatechampions",   cmd_updatechampions))
    # app.add_handler(CommandHandler("trending",          cmd_trending))        # disabled
    app.add_handler(CommandHandler("setrankingemojis",  cmd_setrankingemojis))
    # app.add_handler(CommandHandler("refreshtrending",      cmd_refreshtrending))  # disabled
    app.add_handler(CommandHandler("refreshleaderboard",   cmd_refreshleaderboard))
    app.add_handler(CommandHandler("refreshchampions",     cmd_refreshchampions))
    # REMOVED (owner request): /refreshtrending2
    # REMOVED (owner request): /ownerhelpt
    # REMOVED (owner request): /resetsoltrend
    # REMOVED (owner request): /resetethtrend
    # REMOVED (owner request): /resetbsctrend
    # REMOVED (owner request): /resetbasetrend
    # REMOVED (owner request): /resettontrend
    # REMOVED (owner request): /resetrhtrend
    # REMOVED (owner request): /pintrending
    # REMOVED (owner request): /unpintrending
    # REMOVED (owner request): /listpinned
    # app.add_handler(CommandHandler("blocktrending",        cmd_blocktrending))    # disabled
    # app.add_handler(CommandHandler("unblocktrending",      cmd_unblocktrending))  # disabled
    # app.add_handler(CommandHandler("listblockedtrending",  cmd_listblockedtrending))  # disabled
    app.add_handler(CommandHandler("givepoints",        cmd_givepoints))
    app.add_handler(CommandHandler("checkpoints",       cmd_checkpoints))
    app.add_handler(CommandHandler("zerocolpoints",     cmd_zerocolpoints))
    app.add_handler(CommandHandler("resetallpoints",    cmd_resetallpoints))
    app.add_handler(CommandHandler("freezecall",        cmd_freezecall))
    app.add_handler(CommandHandler("dropskip",          cmd_dropskip))
    app.add_handler(CommandHandler("unfreezecall",      cmd_unfreezecall))
    app.add_handler(CommandHandler("addmissedcall",     cmd_addmissedcall))
    app.add_handler(CommandHandler("setkolowner",       cmd_setkolowner))
    app.add_handler(CommandHandler("setkolownerid",     cmd_setkolownerid))
    app.add_handler(CommandHandler("removekolowner",    cmd_removekolowner))
    app.add_handler(CommandHandler("listkolowners",     cmd_listkolowners))
    app.add_handler(CommandHandler("xcheck",            cmd_xcheck))

    # Admin + Owner — emoji management
    app.add_handler(CommandHandler("setalertemoji",     cmd_setalertemoji))
    app.add_handler(CommandHandler("listalertemojis",   cmd_listalertemojis))
    app.add_handler(CommandHandler("clearalertemoji",   cmd_clearalertemoji))

    # CheesePad / PinkSale — owner-editable premium emoji IDs
    app.add_handler(CommandHandler("setcpemoji",    cmd_setcpemoji))
    app.add_handler(CommandHandler("listcpemojis",  cmd_listcpemojis))
    app.add_handler(CommandHandler("clearcpemoji",  cmd_clearcpemoji))
    app.add_handler(CommandHandler("setpsemoji",    cmd_setpsemoji))
    app.add_handler(CommandHandler("listpsemojis",  cmd_listpsemojis))
    app.add_handler(CommandHandler("clearpsemoji",  cmd_clearpsemoji))

    # CheesePad / PinkSale — owner-editable templates (channel post + details page)
    app.add_handler(CommandHandler("setcptemplate",   cmd_setcptemplate))
    app.add_handler(CommandHandler("cptemplate",      cmd_cptemplate))
    app.add_handler(CommandHandler("resetcptemplate", cmd_resetcptemplate))
    app.add_handler(CommandHandler("setpstemplate",   cmd_setpstemplate))
    app.add_handler(CommandHandler("pstemplate",      cmd_pstemplate))
    app.add_handler(CommandHandler("resetpstemplate", cmd_resetpstemplate))
    app.add_handler(CommandHandler("cptemplatevars",  cmd_cptemplatevars))
    app.add_handler(CommandHandler("pstemplatevars",  cmd_pstemplatevars))
    app.add_handler(CommandHandler("templatehelp",    cmd_templatehelp))
    app.add_handler(CommandHandler("templates",       cmd_templatepanel))
    app.add_handler(CommandHandler("templatepanel",   cmd_templatepanel))

    # Owner — admin management
    app.add_handler(CommandHandler("addadmin",          cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin",       cmd_removeadmin))
    app.add_handler(CommandHandler("listadmins",        cmd_listadmins))

    # Owner — users
    app.add_handler(CommandHandler("myusers",        cmd_myusers))
    app.add_handler(CommandHandler("resetmembercount", cmd_resetmembercount))
    app.add_handler(CommandHandler("resetusers",     cmd_resetusers))
    app.add_handler(CommandHandler("broadcast",      cmd_broadcast))
    app.add_handler(CommandHandler("mediabroadcast", cmd_mediabroadcast))
    app.add_handler(CommandHandler("editbroadcast",   cmd_editbroadcast))
    app.add_handler(CommandHandler("deletebroadcast", cmd_deletebroadcast))
    app.add_handler(CommandHandler("listbroadcasts",  cmd_listbroadcasts))
    app.add_handler(CommandHandler("mystats",        cmd_mystats))

    # Owner — templates
    app.add_handler(CommandHandler("settemplate",    cmd_settemplate))
    app.add_handler(CommandHandler("edittemplate",   cmd_edittemplate))
    app.add_handler(CommandHandler("setrangetemplate",   cmd_setrangetemplate))
    app.add_handler(CommandHandler("listrangetemplates", cmd_listrangetemplates))
    app.add_handler(CommandHandler("delrangetemplate",   cmd_delrangetemplate))
    app.add_handler(CommandHandler("setkollang",          cmd_setkollang))
    app.add_handler(CommandHandler("addkolstolang",       cmd_addkolstolang))
    app.add_handler(CommandHandler("setlangtemplate",         cmd_setlangtemplate))
    app.add_handler(CommandHandler("listlangtemplates",       cmd_listlangtemplates))
    app.add_handler(CommandHandler("dellangtemplate",          cmd_dellangtemplate))
    app.add_handler(CommandHandler("setlangdroppedtemplate",   cmd_setlangdroppedtemplate))
    app.add_handler(CommandHandler("showlangdroppedtemplate",  cmd_showlangdroppedtemplate))
    app.add_handler(CommandHandler("clearlangdroppedtemplate", cmd_clearlangdroppedtemplate))
    app.add_handler(CommandHandler("listkollang",         cmd_listkollang))
    app.add_handler(CommandHandler("setkoltemplate",      cmd_setkoltemplate))
    app.add_handler(CommandHandler("listkoltemplates",    cmd_listkoltemplates))
    app.add_handler(CommandHandler("delkoltemplate",      cmd_delkoltemplate))
    app.add_handler(CommandHandler("editmilestone",  cmd_editmilestone))
    app.add_handler(CommandHandler("clearmilestone", cmd_clearmilestone))
    app.add_handler(CommandHandler("listmilestones", cmd_listmilestones))
    app.add_handler(CommandHandler("setmilestones",  cmd_setmilestones))

    # Owner — media
    app.add_handler(CommandHandler("setmedia",       cmd_setmedia))
    app.add_handler(CommandHandler("clearmedia",     cmd_clearmedia))
    app.add_handler(CommandHandler("listmedia",      cmd_listmedia))
    app.add_handler(CommandHandler("sethsmedia",     cmd_sethsmedia))
    app.add_handler(CommandHandler("clearhsmedia",   cmd_clearhsmedia))
    app.add_handler(CommandHandler("listhsmedia",    cmd_listhsmedia))
    app.add_handler(CommandHandler("testalerths",    cmd_testalerths))
    app.add_handler(CommandHandler("hschains",       cmd_hschains))
    app.add_handler(CommandHandler("hscheck",        cmd_hscheck))
    app.add_handler(CommandHandler("setstartmedia",   cmd_setstartmedia))
    app.add_handler(CommandHandler("clearstartmedia", cmd_clearstartmedia))
    app.add_handler(CommandHandler("postnow",         cmd_postnow))

    # Owner — texts
    app.add_handler(CommandHandler("editbutton",      cmd_editbutton))
    app.add_handler(CommandHandler("settext",         cmd_settext))
    app.add_handler(CommandHandler("showtext",        cmd_showtext))
    app.add_handler(CommandHandler("cleartext",       cmd_cleartext))
    app.add_handler(CommandHandler("editbtnlabel",    cmd_editbtnlabel))
    app.add_handler(CommandHandler("editstart",       cmd_editstart))
    app.add_handler(CommandHandler("editcommandtext", cmd_editcommandtext))

    # Owner — custom commands
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    app.add_handler(CommandHandler("addcmd",    cmd_addcmd))
    app.add_handler(CommandHandler("removecmd", cmd_removecmd))
    app.add_handler(CommandHandler("listcmds",  cmd_listcmds))

    # Owner — misc
    app.add_handler(CommandHandler("testalert",          cmd_testalert))
    register_extras(app)
    app.add_handler(CommandHandler("setxemojis",         cmd_setxemojis))
    app.add_handler(CommandHandler("setxemojisbulk",     cmd_setxemojisbulk))
    app.add_handler(CommandHandler("listxemojis",        cmd_listxemojis))
    app.add_handler(CommandHandler("removexemojis",      cmd_removexemojis))
    app.add_handler(CommandHandler("ownerhelp",          cmd_ownerhelp))
    app.add_handler(CommandHandler("ownerhelpfull",      cmd_ownerhelp_full))
    app.add_handler(CommandHandler("premiumguide",       cmd_premiumguide))
    app.add_handler(CommandHandler("getemoji",           cmd_getemoji))
    app.add_handler(CommandHandler("debugemoji",         cmd_debugemoji))
    app.add_handler(CommandHandler("showtemplate",       cmd_showtemplate))
    app.add_handler(CommandHandler("editxtemplate",      cmd_editxtemplate))
    app.add_handler(CommandHandler("setchainemoji",      cmd_setchainemoji))
    app.add_handler(CommandHandler("setemojislot",       cmd_setemojislot))
    app.add_handler(CommandHandler("setemojipack",       cmd_setemojipack))
    app.add_handler(CommandHandler("resettracking",      cmd_resettracking))
    app.add_handler(CommandHandler("resetleaderboard",   cmd_resetleaderboard))
    app.add_handler(CommandHandler("setleaderboardtemplate", cmd_setleaderboardtemplate))
    app.add_handler(CommandHandler("clearleaderboardtemplate", cmd_clearleaderboardtemplate))
    # app.add_handler(CommandHandler("trendingKols",       cmd_trendingkols))  # disabled
    # app.add_handler(CommandHandler("trendingkols",       cmd_trendingkols))  # disabled
    app.add_handler(CommandHandler("setcommandvideo",    cmd_setcommandvideo))
    app.add_handler(CommandHandler("setxalertvideo",     cmd_setxalertvideo))
    app.add_handler(CommandHandler("clearxalertvideo",   cmd_clearxalertvideo))
    app.add_handler(CommandHandler("setpromo",           cmd_setpromo))
    app.add_handler(CommandHandler("stoppromo",          cmd_stoppromo))
    app.add_handler(CommandHandler("setpromolink",        cmd_setpromolink))
    app.add_handler(CommandHandler("clearpromolink",      cmd_clearpromolink))
    app.add_handler(CommandHandler("pendingkols",         cmd_pendingkols))
    app.add_handler(CommandHandler("previewtemplate",     cmd_previewtemplate))

    # Owner — new commands (Page 2)
    app.add_handler(CommandHandler("joinkols",             cmd_joinkols))
    app.add_handler(CommandHandler("setdroppedtemplate",  cmd_setdroppedtemplate))
    app.add_handler(CommandHandler("setkoldroppedtemplate",   cmd_setkoldroppedtemplate))
    app.add_handler(CommandHandler("showkoldroppedtemplate",  cmd_showkoldroppedtemplate))
    app.add_handler(CommandHandler("clearkoldroppedtemplate", cmd_clearkoldroppedtemplate))
    app.add_handler(CommandHandler("showdroppedtemplate", cmd_showdroppedtemplate))
    app.add_handler(CommandHandler("cleardroppedtemplate",cmd_cleardroppedtemplate))
    app.add_handler(CommandHandler("adddroppedvideo",     cmd_adddroppedvideo))
    app.add_handler(CommandHandler("listdroppedvideos",   cmd_listdroppedvideos))
    app.add_handler(CommandHandler("removedroppedvideo",  cmd_removedroppedvideo))
    app.add_handler(CommandHandler("cleardroppedvideos",  cmd_cleardroppedvideos))
    app.add_handler(CommandHandler("ownerhelpps",         cmd_ownerhelpps))
    app.add_handler(CommandHandler("pscall",              cmd_pscall))
    app.add_handler(CommandHandler("pstest",              cmd_pstest))
    app.add_handler(CommandHandler("psdebug",             cmd_psdebug))
    app.add_handler(CommandHandler("psclear",             cmd_psclear))
    app.add_handler(CommandHandler("addpsmedia",          cmd_addpsmedia))
    app.add_handler(CommandHandler("addcpmedia",          cmd_addcpmedia))
    app.add_handler(CommandHandler("listcpmedia",         cmd_listcpmedia))
    app.add_handler(CommandHandler("removecpmedia",       cmd_removecpmedia))
    app.add_handler(CommandHandler("clearcpmedia",        cmd_clearcpmedia))
    app.add_handler(CommandHandler("cpclear",             cmd_cpclear))
    app.add_handler(CommandHandler("addcpdmedia",         cmd_addcpdmedia))
    app.add_handler(CommandHandler("listcpdmedia",        cmd_listcpdmedia))
    app.add_handler(CommandHandler("removecpdmedia",      cmd_removecpdmedia))
    app.add_handler(CommandHandler("clearcpdmedia",       cmd_clearcpdmedia))
    app.add_handler(CommandHandler("addpsdmedia",         cmd_addpsdmedia))
    app.add_handler(CommandHandler("listpsdmedia",        cmd_listpsdmedia))
    app.add_handler(CommandHandler("removepsdmedia",      cmd_removepsdmedia))
    app.add_handler(CommandHandler("clearpsdmedia",       cmd_clearpsdmedia))
    app.add_handler(CommandHandler("listpsmedia",         cmd_listpsmedia))
    app.add_handler(CommandHandler("removepsmedia",       cmd_removepsmedia))
    app.add_handler(CommandHandler("clearpsmedia",        cmd_clearpsmedia))
    app.add_handler(CommandHandler("setpsref",            cmd_setpsref))
    app.add_handler(CommandHandler("cptest",              cmd_cptest))
    app.add_handler(CommandHandler("cpcall",              cmd_cpcall))
    app.add_handler(CommandHandler("setaffiliate",        cmd_setaffiliate))
    app.add_handler(CommandHandler("showpsref",           cmd_showpsref))
    app.add_handler(CommandHandler("clearpsref",          cmd_clearpsref))
    app.add_handler(CommandHandler("pslist",              cmd_pslist))
    app.add_handler(CommandHandler("pswatch",             cmd_pswatch))
    app.add_handler(CommandHandler("psdrop",              cmd_psdrop))
    app.add_handler(CommandHandler("debugscan",           cmd_debugscan))
    app.add_handler(CommandHandler("testdropped",         cmd_testdropped))
    app.add_handler(CommandHandler("diag",                cmd_diag))
    app.add_handler(CommandHandler("ownerhelp2",          cmd_ownerhelp2))
    app.add_handler(CommandHandler("backupnow",           cmd_backupnow))
    app.add_handler(CommandHandler("prunerugged",         cmd_prunerugged))
    app.add_handler(CommandHandler("restorenow",          cmd_restorenow))
    app.add_handler(CommandHandler("setbuttonmedia",     cmd_setbuttonmedia))
    app.add_handler(CommandHandler("clearbuttonmedia",   cmd_clearbuttonmedia))
    app.add_handler(CommandHandler("missedcall",         cmd_addmissedcall))
    app.add_handler(CommandHandler("addx",               cmd_addx))
    app.add_handler(CommandHandler("settwitter",         cmd_addx))
    app.add_handler(CommandHandler("removex",            cmd_removex))
    app.add_handler(CommandHandler("addpostlink",        cmd_addpostlink))
    app.add_handler(CommandHandler("removepostlink",     cmd_removepostlink))
    app.add_handler(CommandHandler("listpostlinks",      cmd_listpostlinks))
    app.add_handler(CommandHandler("addpriority",        cmd_addpriority))
    app.add_handler(CommandHandler("removepriority",     cmd_removepriority))
    app.add_handler(CommandHandler("listpriority",       cmd_listpriority))
    app.add_handler(CommandHandler("setcommandmedia",    cmd_setcommandmedia))
    app.add_handler(CommandHandler("clearcommandmedia",  cmd_clearcommandmedia))
    app.add_handler(CommandHandler("setkolacceptmedia",   cmd_setkolacceptmedia))
    app.add_handler(CommandHandler("clearkolacceptmedia", cmd_clearkolacceptmedia))
    app.add_handler(CommandHandler("setsubmitconfirmmedia",   cmd_setsubmitconfirmmedia))
    app.add_handler(CommandHandler("clearsubmitconfirmmedia", cmd_clearsubmitconfirmmedia))

    # ── Button wizards + Buy Bots ────────────────────────────────────────────
    app.add_handler(CommandHandler("addmissedcallw",  start_missedcall_wizard))
    app.add_handler(CommandHandler("missedcallw",     start_missedcall_wizard))
    app.add_handler(CommandHandler("fixx",            cmd_fixx))
    # 🆘 Problem Fix Center — ek hi solid entry point (buttons + step-by-step)
    app.add_handler(CommandHandler("fix",             cmd_fixpanel))
    app.add_handler(CommandHandler("problem",         cmd_fixpanel))
    app.add_handler(CommandHandler("latemc",          cmd_latemc_wizard))
    app.add_handler(CommandHandler("rhfix",           cmd_rhfix_wizard))
    app.add_handler(CommandHandler("checkcall",       cmd_checkcall_wizard))
    app.add_handler(CommandHandler("xupdate",         cmd_fixx))
    app.add_handler(CallbackQueryHandler(wizard_callback, pattern=r"^wiz:"), group=-2)
    app.add_handler(ChatMemberHandler(on_bot_added, ChatMemberHandler.MY_CHAT_MEMBER), group=-2)
    app.add_handler(ChatMemberHandler(on_channel_member_update, ChatMemberHandler.CHAT_MEMBER), group=-2)

    # ── Group / lounge kill-switch (must be the very first handler) ──────────
    app.add_handler(TypeHandler(Update, block_non_private), group=-1)
    # ── Force-join gate: no command/button (except /start) without membership ──
    # group=-3 so it runs BEFORE the -2 callback handlers (wizard / buybot),
    # otherwise those buttons bypass the gate completely.
    app.add_handler(TypeHandler(Update, force_join_gate), group=-3)
    logger.info("FORCE-JOIN GATE v2 (strict) active -> %s", TARGET_CHANNEL)

    # General messages (private chats only)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION
         | filters.Document.ALL) & filters.ChatType.PRIVATE,
        handle_message))

    # Jobs
    app.job_queue.run_repeating(scan_job,            interval=max(1, int(os.environ.get("SCAN_INTERVAL_SECONDS", "2") or 2)), first=5,
                                job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 1})
    app.job_queue.run_repeating(monitoring_job,      interval=max(1, int(os.environ.get("MONITOR_INTERVAL_SECONDS", "2") or 2)), first=15,
                                name="monitoring_job",
                                job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 1})   # real-time: bulk DexScreener refresh + milestone alerts
    # Fast lane: special/priority KOL channels checked every second on their own
    # schedule so their calls never queue behind the full tracking list.
    app.job_queue.run_repeating(monitoring_job,      interval=1,         first=16,
                                data="priority", name="priority_monitoring_job",
                                job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 1})
    # Outer safety net: catches the bulk lane getting permanently wedged
    # (APScheduler "maximum number of running instances reached" with zero
    # successful ticks) — a state the in-body 45s timeout cannot always clear.
    # Runs on its own schedule, independent of the bulk lane, so it keeps
    # ticking even while the bulk lane is stuck.
    app.job_queue.run_repeating(_bulk_lane_watchdog_job, interval=10, first=35,
                                name="bulk_lane_watchdog_job",
                                job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5})
    app.job_queue.run_repeating(pending_media_job,   interval=15,        first=25)   # release held X alerts once media is uploaded
    app.job_queue.run_repeating(ath_backfill_job,    interval=15,        first=30)   # real ATH recovery + missed-X alerts from candle highs
    app.job_queue.run_repeating(tracked_calls_cleanup_job, interval=300, first=90,   # har 5 min: drop rugged calls older than RUGGED_CALL_MAX_AGE_HOURS
                                name="tracked_calls_cleanup_job",
                                job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 30})
    app.job_queue.run_repeating(hot_streak_resolve_job, interval=45, first=40,   # hot-streak: settle dead pending calls + fire due streak alerts
                                name="hot_streak_resolve_job")

    app.job_queue.run_repeating(leaderboard_job,     interval=max(5, int(os.environ.get("LEADERBOARD_INTERVAL_SECONDS", "20") or 20)), first=45)    # slowed from 5s: frequent edits were flooding the shared userbot connection
    app.job_queue.run_repeating(champions_job,       interval=max(5, int(os.environ.get("CHAMPIONS_INTERVAL_SECONDS", "20") or 20)), first=50)    # slowed from 5s: frequent edits were flooding the shared userbot connection
    app.job_queue.run_repeating(channel_meta_job,    interval=600, first=90)   # subscriber counts + avatars for the website — every 10 min, paced 0.3s/channel
    # trending_job (Dexscreener Trending post) REMOVED permanently — owner request.
    # trending2_job (posts 3560+3562, Dexscreener trend lists) DISABLED — owner
    # request to cut bot load. Re-enable this line if the feature is needed again.
    # app.job_queue.run_repeating(trending2_job,        interval=120,       first=90)
    app.job_queue.run_repeating(backup_job,          interval=3600,      first=60)   # har 60 min Telegram backup
    app.job_queue.run_repeating(ps_watch_job,         interval=180,       first=120)  # PinkSale: presale end → live X tracking
    app.add_handler(CommandHandler("checkdeleted", cmd_checkdeleted))
    app.job_queue.run_repeating(deleted_posts_sweep_job, interval=120, first=60,   # backstop: detect deleted caller posts even if the realtime event is missed
                                name="deleted_posts_sweep_job")
    app.job_queue.run_repeating(userbot_watchdog_job, interval=45,        first=45)   # har 45s: userbot alive check + auto-reconnect (backstop for the immediate _kick_userbot_reconnect)

    # Startup: notify owner if userbot not connected
    async def _startup_notify(app_ref):
        global BOT_READY, STARTUP_STAGE
        # Redeploy protection: until the startup pre-scan finishes, ONLY posts
        # published after this boot are processed. Purani posts sirf 'seen' mark
        # hoti hain — koi purana record dobara post nahi hota.
        BOT_READY = False
        STARTUP_STAGE = "initializing userbot"

        async def _step(name, coro, timeout):
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Startup step '{name}' timed out after {timeout}s — continuing")
            except Exception as e_step:
                logger.warning(f"Startup step '{name}' failed: {type(e_step).__name__}: {e_step}")
            return None

        await _step("init_userbot", init_userbot(), 120)

        # Register live events before slower restore/catch-up work. This closes
        # the startup window in which fresh calls used to be missed.
        STARTUP_STAGE = "enabling realtime"
        await _step("realtime_monitoring", setup_realtime_monitoring(app_ref.bot),
                    int(os.environ.get("RT_SETUP_TIMEOUT", "300") or 300))

        # Railway ke liye: startup pe Telegram backup se data restore karo
        if userbot_client:
            STARTUP_STAGE = "restoring data"
            await _step("restore_data", restore_data_from_telegram(userbot_client), 120)
            # Silently pre-mark already-passed milestones so monitoring_job
            # does NOT fire duplicate alerts on first run after restart.
            await _step("silent_catchup", _silent_catchup_milestones(), 120)

        # Mark only genuinely old posts as seen. Recent unseen posts must flow
        # through scan_job, otherwise calls made during a restart are skipped.
        async def _prescan_all():
            all_channels = load_channels()
            if not all_channels:
                return
            logger.info(f"Startup pre-scan: marking current posts as seen for {len(all_channels)} channel(s)...")
            sem_pre = asyncio.Semaphore(8)

            async def _preseen(ch):
                async with sem_pre:
                    try:
                        # OWNER FIX (2026-09-19, casinoinsiderhere/1099 incident):
                        # this pre-scan runs BEFORE any post ever reaches
                        # process_new_call, so marking a special channel's old
                        # posts as "seen" here silently killed them no matter
                        # what cutoff process_new_call itself used. Special
                        # channels are now skipped entirely in this pre-scan —
                        # their backlog flows through to scan_job/
                        # process_new_call instead, which now has no age
                        # cutoff for special channels either, so a real call
                        # always gets alerted regardless of how long the bot
                        # was down for.
                        special_set = set(load_special_channels())
                        if ch.lower() in special_set:
                            return
                        posts = await asyncio.wait_for(fetch_channel_posts(ch), timeout=30)
                        mins = float(os.environ.get("CALL_CATCHUP_MINUTES", "30") or 30)
                        cutoff = time.time() - max(60.0, mins * 60)
                        for post in posts:
                            try:
                                post_ts = datetime.fromisoformat(
                                    str(post.get("date") or "").replace("Z", "+00:00")).timestamp()
                            except (TypeError, ValueError):
                                post_ts = 0
                            if post_ts and post_ts < cutoff:
                                seen_message_ids[ch.lower()].add(str(post["id"]))
                    except Exception as e_scan:
                        logger.warning(f"Pre-scan failed for @{ch}: {e_scan}")

            await asyncio.gather(*[_preseen(ch) for ch in all_channels])
            _save_seen()
            logger.info("✅ Startup pre-scan complete — old post flooding prevented")

        STARTUP_STAGE = "pre-scanning"
        await _step("pre_scan", _prescan_all(), 150)

        # From this second on, only genuinely NEW posts are tracked.
        BOT_READY = True
        STARTUP_STAGE = "ready"
        logger.info("🟢 BOT_READY — realtime call tracking active")
        asyncio.create_task(_prewarm_media_cache(app_ref.bot))
        asyncio.create_task(_start_second_userbot(app_ref.bot))

        # Start session web generator on port 3002 (proxied via api-server at /api/session)
        try:
            import importlib.util as _ilu
            _sw_spec = _ilu.spec_from_file_location("session_web", "session_web.py")
            _sw = _ilu.module_from_spec(_sw_spec)
            import os as _os
            _os.environ.setdefault("SESSION_BASE", "/api/session")
            _sw_spec.loader.exec_module(_sw)
            from aiohttp import web as _aio_web
            _runner = _aio_web.AppRunner(_sw.app)
            await _runner.setup()
            _site = _aio_web.TCPSite(_runner, "0.0.0.0", 3002)
            await _site.start()
            logger.info("✅ Session web generator started on :3002 → /api/session")
        except Exception as _e_sw:
            logger.warning(f"Session web server failed to start: {_e_sw}")

        # WIZRAD public data API — powers the website's leaderboard/new-listings/
        # twitter cards. Isolated try/except: if it fails, the bot itself is
        # completely unaffected and keeps running as before.
        try:
            import importlib.util as _ilu2
            _api_spec = _ilu2.spec_from_file_location("api_server", "api_server.py")
            _api_mod  = _ilu2.module_from_spec(_api_spec)
            _api_spec.loader.exec_module(_api_mod)
            await _api_mod.start_api_server()
            logger.info("✅ WIZRAD public API started on :8085")
        except Exception as _e_api:
            logger.warning(f"WIZRAD API server failed to start: {_e_api}")


        if not userbot_client and OWNER_ID:
            try:
                await app_ref.bot.send_message(
                    OWNER_ID,
                    "⚠️ <b>Userbot connect nahi hua!</b>\n\n"
                    "Premium emojis kaam nahi karein ge jab tak userbot connect na ho.\n\n"
                    "👉 <b>/userbotlogin</b> bhejo — OTP aayega, enter karo, done!\n\n"
                    "Session ek baar set hone ke baad automatically save ho jaayegi.",
                    parse_mode="HTML"
                )
            except Exception: pass
        elif userbot_client and OWNER_ID:
            try:
                me = await userbot_client.get_me()
                await app_ref.bot.send_message(
                    OWNER_ID,
                    f"✅ <b>Bot started!</b>\nUserbot: @{me.username} connected. Premium emojis ready.",
                    parse_mode="HTML"
                )
            except Exception: pass

    # IMPORTANT: do NOT overwrite the builder's post_init — chain both, otherwise
    # the command menu / seeding step silently never runs.
    async def _background_startup(app_ref):
        global BOT_READY, STARTUP_STAGE
        try:
            await _startup_notify(app_ref)
        except Exception as e:
            BOT_READY = True
            STARTUP_STAGE = f"degraded: {type(e).__name__}"
            logger.error(f"background startup failed: {type(e).__name__}: {e}")

    async def _combined_post_init(app_ref):
        try:
            await post_init(app_ref)
        except Exception as e:
            logger.error(f"post_init failed: {e}")
        # Telegram polling must start immediately. Restore, pre-scan and Telethon
        # setup can take minutes on a cold Railway deploy, so never await them here.
        asyncio.create_task(_background_startup(app_ref), name="wizard-background-startup")
        logger.info("✅ Command polling released; background startup continues separately")

    app.post_init = _combined_post_init
    app.add_error_handler(_global_error_handler)

    logger.info(f"✅ WIZARD SCAN Bot starting — Owner: {OWNER_ID}")
    logger.info("🏷 CODE VERSION: 2026-10-11-rtcover-nofloodpoll-prewarm-captionfix (if you see this line, the NEW bot.py is running)")
    # Resilient polling: transient Telegram/network failures must not end the
    # process, otherwise Railway counts it as a crash and restart-loops.
    backoff = 5
    while True:
        try:
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False,
            )
            break  # clean shutdown
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"Polling crashed: {type(e).__name__}: {e} — restarting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
