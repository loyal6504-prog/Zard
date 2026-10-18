# =====================================================================
# PATCH for commands.py  (Zard repo)
# STEP 1: Neeche wala "HELPERS + TEXTS" block commands.py me
#         `async def cmd_start` se PEHLE paste karo.
# STEP 2: button_callback me purani branches replace karo (STEP 2 block).
# STEP 3: cmd_submit / cmd_subscribe me chhota sa edit (STEP 3 block).
# =====================================================================


# ───────────────────── STEP 1: HELPERS + TEXTS ─────────────────────

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


# ───────────────── STEP 2: button_callback branches ─────────────────
# button_callback me in branches ko REPLACE karo:
#   "command_menu", "kol_request", "promo_hub", "leaderboard",
#   "alert_rules", "dm_alerts", "fast_track", "chat_us"
# ("tracked_kols" ke liye neeche alag note hai). Baaki branches (kreq| etc.) waise hi rahen.

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

    # tracked_kols: apni purani branch rakho, bas 2 changes:
    #   1) kb = InlineKeyboardMarkup([[_pkb("🔮 MAIN CHANNEL 🔮", url="https://t.me/WizardScan")],
    #                                 [_back_btn()]])
    #   2) branch ke bilkul aakhir me:  await _del_msg(query.message)

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


# ───────────────── STEP 3: direct /submit & /subscribe ─────────────────
# cmd_submit ke andar `if not context.args:` block me:
#   - `text = ...` wali line ko replace karo:   text = KOL_REQUEST_TEXT
#   - `kb = InlineKeyboardMarkup([...Fast Track..., ...Chat Us...])` ko replace karo:
#         kb = InlineKeyboardMarkup([[_owner_btn()], [_back_btn()]])
#
# cmd_subscribe ke andar `if not context.args:` block me:
#   - `text = get_public_text("subscribe", DEFAULT_SUBSCRIBE_INFO)`  ->  text = DM_ALERTS_TEXT
