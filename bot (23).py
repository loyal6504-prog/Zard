import os
import re
import json
import time
import logging
import requests
import urllib.parse
import asyncio
from datetime import datetime
from collections import defaultdict
from bs4 import BeautifulSoup
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputFile
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.error import TelegramError

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
TARGET_CHANNEL  = "@WizardScan"
TG_CHANNEL_LINK = "https://t.me/WizardScan"
BOT_LINK        = "https://t.me/WIZARD_SCAN_BOT"

USERS_FILE      = "users.json"
CHANNELS_FILE   = "channels.json"
BOT_CONFIG_FILE = "bot_config.json"

X_MILESTONES = [2, 3, 4, 5, 10, 15, 20, 30, 50, 100, 200, 500, 1000, 5000, 10000, 100000]

SUPPORTED_CHAINS = {
    "ethereum": "ETH",
    "bsc":      "BNB",
    "base":     "BASE",
    "solana":   "SOL",
}

IMG_COMMAND   = "attached_assets/IMG_20260605_071524_631_1780625749888.jpg"
IMG_PROMO     = "attached_assets/IMG_20260605_071508_452_1780625749951.jpg"
IMG_CONTACT   = "attached_assets/IMG_20260605_071512_338_1780625749928.jpg"
IMG_FASTTRACK = "attached_assets/IMG_20260605_071503_665_1780625749970.jpg"
VID_START     = "attached_assets/VID_20260605_071529_340_1780625749832.mp4"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── In-memory state ──────────────────────────────────────────────────────────
seen_message_ids = defaultdict(set)
tracked_calls    = {}
sent_milestones  = defaultdict(set)

# Editing state for owner
owner_edit_state = {}   # owner_id → {"state": str, ...}

# Image generation state per user
user_img_state   = {}   # user_id → {"state": "waiting"}

# States
ST_NONE           = None
ST_TEMPLATE       = "edit_template"
ST_MILESTONE_TMPL = "edit_milestone_tmpl"
ST_SET_MEDIA      = "set_media"
ST_EDIT_BTN       = "edit_button"
ST_EDIT_START     = "edit_start"
ST_EDIT_CMD       = "edit_command_text"
ST_ADD_CMD2       = "add_command_response"
IMG_WAITING       = "waiting_prompt"

ETH_CA_PATTERN = re.compile(r'0x[a-fA-F0-9]{40}')
SOL_CA_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

# ─── Persistence ──────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_users():     return load_json(USERS_FILE, [])
def save_users(u):    save_json(USERS_FILE, u)
def load_channels():  return load_json(CHANNELS_FILE, [])
def save_channels(c): save_json(CHANNELS_FILE, c)
def load_config():    return load_json(BOT_CONFIG_FILE, {})
def save_config(c):   save_json(BOT_CONFIG_FILE, c)

def add_user(uid: int):
    users = load_users()
    if uid not in users:
        users.append(uid)
        save_users(users)

def cfg_get(key, default=None):
    return load_config().get(key, default)

def cfg_set(key, value):
    c = load_config()
    c[key] = value
    save_config(c)

# ─── DexScreener (async, runs in thread) ─────────────────────────────────────
def _fetch_dexscreener_sync(ca: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            url  = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
            resp = requests.get(url, timeout=12, headers=HEADERS)
            if resp.status_code != 200:
                time.sleep(2)
                continue
            pairs = resp.json().get("pairs") or []
            supported = [
                p for p in pairs
                if p.get("chainId", "").lower() in SUPPORTED_CHAINS
                and (p.get("liquidity", {}).get("usd") or 0) > 500
            ]
            if not supported:
                return {}
            best = sorted(
                supported,
                key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0,
                reverse=True
            )[0]
            chain  = SUPPORTED_CHAINS[best.get("chainId", "").lower()]
            mc_raw = best.get("marketCap") or best.get("fdv") or 0
            mc     = float(mc_raw)
            if mc <= 0:
                return {}
            symbol = best.get("baseToken", {}).get("symbol", "")
            return {"chain": chain, "mcap": mc, "mcap_fmt": fmt_mc(mc), "symbol": symbol}
        except Exception as e:
            logger.warning(f"DexScreener attempt {attempt+1}: {e}")
            time.sleep(2)
    return {}

async def fetch_dexscreener(ca: str) -> dict:
    return await asyncio.to_thread(_fetch_dexscreener_sync, ca)

def fmt_mc(value: float) -> str:
    if not value:
        return "N/A"
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:.0f}"

# ─── Channel scraping (async) ─────────────────────────────────────────────────
def _fetch_posts_sync(channel: str) -> list:
    try:
        resp = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        posts = []
        for div in soup.find_all("div", class_="tgme_widget_message"):
            attr   = div.get("data-post", "")
            msg_id = attr.split("/")[-1] if "/" in attr else attr
            td     = div.find("div", class_="tgme_widget_message_text")
            text   = td.get_text(separator="\n") if td else ""
            posts.append({"id": msg_id, "text": text})
        return posts
    except Exception as e:
        logger.error(f"Fetch {channel}: {e}")
        return []

async def fetch_channel_posts(channel: str) -> list:
    return await asyncio.to_thread(_fetch_posts_sync, channel)

def extract_ca(text: str):
    eth = ETH_CA_PATTERN.findall(text)
    if eth:
        return ("EVM", eth[0].lower())
    for s in SOL_CA_PATTERN.findall(text):
        if not s.startswith("0x") and 32 <= len(s) <= 44:
            return ("SOL", s)
    return None

def is_call_message(text: str) -> bool:
    if not text or len(text) < 10:
        return False
    tl  = text.lower()
    kw  = ["buy","long","entry","target","tp ","gem"," call","launch","listed",
           "mcap","market cap","ca:","contract","bullish","ape","snipe","early",
           "presale","stealth","kol","dexscreener","dextools","birdeye",
           "pump.fun","bullx","photon"]
    has_kw  = any(k in tl for k in kw)
    has_evm = bool(ETH_CA_PATTERN.search(text))
    has_sol = any(32 <= len(s) <= 44 for s in SOL_CA_PATTERN.findall(text) if not s.startswith("0x"))
    return has_kw or has_evm or has_sol

# ─── Alert builder ────────────────────────────────────────────────────────────
DEFAULT_TEMPLATE = (
    "🔮 <b>@{channel} KOL Hit {x}X</b>\n"
    "🪄 <b>${symbol}</b>\n\n"
    "<b>{chain}</b> play called at {entry} market cap. "
    "Current Market Cap stands at {current}. "
    "Clean execution. Tracking for the next move.\n\n"
    "<b>🔮 {entry}   ➤➤   {current}</b>\n\n"
    "Ca: <code>{ca}</code>\n\n"
    '<a href="{kol_link}">➤ KOL</a>\n'
    '<a href="{tg_link}">➤ TG</a>\n'
    '<a href="{bot_link}">➤ BOT</a>'
)

def build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol):
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    # Per-milestone template takes priority, then global template, then default
    ms_templates = config.get("milestone_templates", {})
    template = (
        ms_templates.get(str(x_val))
        or config.get("alert_template")
        or DEFAULT_TEMPLATE
    )
    try:
        return template.format(
            channel=channel, x=x_val, symbol=symbol or "TOKEN", chain=chain,
            entry=entry_fmt, current=current_fmt, ca=ca,
            kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK
        )
    except KeyError as e:
        logger.warning(f"Template format error: {e}")
        return DEFAULT_TEMPLATE.format(
            channel=channel, x=x_val, symbol=symbol or "TOKEN", chain=chain,
            entry=entry_fmt, current=current_fmt, ca=ca,
            kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK
        )

async def send_alert(bot: Bot, channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol):
    text       = build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol)
    config     = load_config()
    media_info = config.get("milestone_media", {}).get(str(x_val))
    try:
        if media_info and media_info.get("file_id"):
            fid   = media_info["file_id"]
            ftype = media_info.get("type", "photo")
            if ftype == "photo":
                await bot.send_photo(chat_id=TARGET_CHANNEL, photo=fid,
                                     caption=text, parse_mode="HTML")
            else:
                await bot.send_video(chat_id=TARGET_CHANNEL, video=fid,
                                     caption=text, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=TARGET_CHANNEL, text=text,
                                   parse_mode="HTML", disable_web_page_preview=True)
        logger.info(f"✅ {x_val}X alert → @{channel}")
    except TelegramError as e:
        logger.error(f"❌ Alert failed: {e}")
        try:
            await bot.send_message(chat_id=TARGET_CHANNEL, text=text,
                                   parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e2:
            logger.error(f"❌ Fallback failed: {e2}")

# ─── Monitoring job (FIXED: no time.sleep — uses asyncio.sleep) ───────────────
async def monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        bot      = context.bot
        channels = load_channels()

        # Scan channels for new calls
        for channel in channels:
            try:
                posts = await fetch_channel_posts(channel)
                logger.info(f"@{channel}: {len(posts)} posts")
                for post in posts:
                    msg_id = post["id"]
                    text   = post["text"]
                    if msg_id in seen_message_ids[channel]:
                        continue
                    seen_message_ids[channel].add(msg_id)
                    if not is_call_message(text):
                        continue
                    result = extract_ca(text)
                    if not result:
                        continue
                    _, ca    = result
                    call_key = f"{channel}_{ca}"
                    if call_key in tracked_calls:
                        continue
                    dex = await fetch_dexscreener(ca)
                    if not dex or not dex.get("mcap"):
                        continue
                    tracked_calls[call_key] = {
                        "channel": channel, "msg_id": msg_id, "ca": ca,
                        "chain": dex["chain"], "entry_mc": dex["mcap"],
                        "entry_fmt": dex["mcap_fmt"], "symbol": dex.get("symbol", ""),
                        "tracked_since": datetime.utcnow().isoformat(),
                    }
                    logger.info(f"📌 {ca[:10]}... @{channel} {dex['chain']} {dex['mcap_fmt']}")
            except Exception as e:
                logger.error(f"Channel scan error @{channel}: {e}")
            await asyncio.sleep(1)  # ✅ non-blocking

        # Check milestones
        logger.info(f"Milestone check ({len(tracked_calls)} tracked)...")
        for call_key, call in list(tracked_calls.items()):
            try:
                dex = await fetch_dexscreener(call["ca"])
                cur = dex.get("mcap", 0)
                if not cur or not call["entry_mc"]:
                    continue
                ratio   = cur / call["entry_mc"]
                cur_fmt = fmt_mc(cur)
                for ms in X_MILESTONES:
                    if ratio >= ms and ms not in sent_milestones[call_key]:
                        sent_milestones[call_key].add(ms)
                        logger.info(f"🚀 {call['channel']} {ms}X! {call['entry_fmt']} → {cur_fmt}")
                        await send_alert(bot, call["channel"], call["msg_id"], ms,
                                         call["chain"], call["entry_fmt"], cur_fmt,
                                         call["ca"], call["symbol"])
                        await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Milestone check error {call_key}: {e}")
            await asyncio.sleep(0.3)  # ✅ non-blocking

    except Exception as e:
        logger.error(f"monitoring_job crash: {e}")

# ─── AI Image Generation (Stable Horde — free, no API key needed) ────────────
HORDE_API = "https://stablehorde.net/api/v2"
HORDE_KEY = "0000000000"  # anonymous key — free

def _generate_image_sync(prompt: str) -> bytes | None:
    try:
        headers = {
            "apikey": HORDE_KEY,
            "Content-Type": "application/json",
            "Client-Agent": "WizardScanBot:1.0:telegram",
        }
        payload = {
            "prompt": prompt,
            "params": {
                "width": 512,
                "height": 512,
                "steps": 25,
                "n": 1,
                "sampler_name": "k_euler_a",
            },
            "models": ["stable_diffusion"],
            "shared": True,
        }
        # Submit job
        r = requests.post(f"{HORDE_API}/generate/async",
                          json=payload, headers=headers, timeout=20)
        if r.status_code != 202:
            logger.error(f"Horde submit failed: {r.status_code} {r.text[:200]}")
            return None
        job_id = r.json().get("id")
        if not job_id:
            return None
        logger.info(f"Horde job submitted: {job_id}")

        # Poll until done (max 4 min)
        for attempt in range(48):
            time.sleep(5)
            check = requests.get(f"{HORDE_API}/generate/check/{job_id}",
                                 headers=headers, timeout=10)
            if check.status_code == 200 and check.json().get("done"):
                logger.info(f"Horde job done after {(attempt+1)*5}s")
                break
        else:
            logger.error("Horde job timed out")
            return None

        # Fetch result
        result = requests.get(f"{HORDE_API}/generate/status/{job_id}",
                              headers=headers, timeout=15)
        if result.status_code != 200:
            return None
        generations = result.json().get("generations", [])
        if not generations:
            return None

        img_url = generations[0].get("img", "")
        if not img_url:
            return None

        # Download image (URL or base64)
        if img_url.startswith("http"):
            img_resp = requests.get(img_url, timeout=30)
            return img_resp.content if img_resp.status_code == 200 else None
        else:
            import base64
            return base64.b64decode(img_url)

    except Exception as e:
        logger.error(f"Image gen error: {e}")
        return None

async def generate_image(prompt: str) -> bytes | None:
    return await asyncio.to_thread(_generate_image_sync, prompt)

# ─── /imagine command ─────────────────────────────────────────────────────────
async def cmd_imagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # If description provided inline: /imagine a wizard casting spells
    if context.args:
        prompt = " ".join(context.args)
        await _do_generate(update, user.id, prompt)
        return
    # Otherwise ask for description
    user_img_state[user.id] = {"state": IMG_WAITING}
    await update.message.reply_text(
        "🎨 <b>AI Image Generator</b>\n\n"
        "Describe the image you want to create.\n"
        "You can write in <b>any language</b> — Urdu, English, Arabic, Hindi, Chinese etc.\n\n"
        "<i>Example: A wizard standing on a mountain with glowing crystals at night</i>\n\n"
        "⏳ Generation takes <b>30-90 seconds</b>. Please be patient.\n\n"
        "Send your description now:",
        parse_mode="HTML"
    )

async def _do_generate(update: Update, uid: int, prompt: str):
    wait_msg = await update.message.reply_text(
        "🔮 <b>Generating your image...</b>\n\n"
        "⏳ This takes 30–90 seconds. Please wait...",
        parse_mode="HTML"
    )
    img_bytes = await generate_image(prompt)
    try:
        await wait_msg.delete()
    except Exception:
        pass
    if img_bytes:
        user_img_state[uid] = {"state": IMG_WAITING}  # keep state for editing
        await update.message.reply_photo(
            photo=img_bytes,
            caption=(
                "✅ <b>Here's your image!</b>\n\n"
                "🔮 To <b>edit</b> or generate a new image, just send a new description.\n"
                "Send /start or /command to exit image mode."
            ),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "❌ Image generation failed. Please try again with a different description."
        )

# ─── /start ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id)
    # Clear any image state
    user_img_state.pop(user.id, None)
    logger.info(f"User /start: {user.id} @{user.username}")
    welcome_text = cfg_get("start_text", "🔮 Welcome to <b><u>WIZARD SCAN</u></b>")
    try:
        with open(VID_START, "rb") as v:
            await update.message.reply_video(video=v, caption=welcome_text, parse_mode="HTML")
        logger.info("Video + caption sent")
    except Exception as e:
        logger.error(f"Video failed: {e}")
        try:
            with open(VID_START, "rb") as v:
                await update.message.reply_video(video=v)
        except Exception:
            pass
        await update.message.reply_text(welcome_text, parse_mode="HTML")

# ─── /command ─────────────────────────────────────────────────────────────────
def build_command_keyboard():
    config = load_config()
    labels = config.get("button_labels", {})
    def lbl(k, d): return labels.get(k, d)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("kol_request",  "🔮 Request your KOL 🔮"), callback_data="kol_request")],
        [InlineKeyboardButton(lbl("promo_hub",    "🔮 Promotion HUB 🔮"),    callback_data="promo_hub")],
        [InlineKeyboardButton(lbl("tracked_kols", "🔮 Tracked KOLs 🔮"),     callback_data="tracked_kols")],
        [InlineKeyboardButton(lbl("leaderboard",  "🔮 Leaderboard 🔮"),      callback_data="leaderboard")],
        [InlineKeyboardButton(lbl("fast_track",   "🔮 Fast Track 🔮"),       callback_data="fast_track")],
        [InlineKeyboardButton(lbl("chat_us",      "🔮  Chat With Us  🔮"),   callback_data="chat_us")],
    ])

async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_img_state.pop(update.effective_user.id, None)
    caption  = cfg_get("command_text", "🔮 <b>Wizard Scan Command Center</b>")
    keyboard = build_command_keyboard()
    sent_msg = None
    try:
        with open(IMG_COMMAND, "rb") as img:
            sent_msg = await update.message.reply_photo(
                photo=img, caption=caption,
                parse_mode="HTML", reply_markup=keyboard,
            )
    except Exception:
        sent_msg = await update.message.reply_text(
            caption, parse_mode="HTML", reply_markup=keyboard,
        )
    if sent_msg:
        try:
            await context.bot.pin_chat_message(
                chat_id=update.effective_chat.id,
                message_id=sent_msg.message_id,
                disable_notification=True
            )
        except Exception:
            pass

# ─── Contact buttons ──────────────────────────────────────────────────────────
CONTACT_BUTTONS = InlineKeyboardMarkup([
    [InlineKeyboardButton("Owner",   url="https://t.me/W_S_CEO")],
    [InlineKeyboardButton("Admin",   url="https://t.me/Wizard_Scan")],
    [InlineKeyboardButton("Channel", url="https://t.me/WizardScan")],
])

# ─── Callbacks ────────────────────────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    data   = query.data
    btexts = cfg_get("button_texts", {})

    if data == "kol_request":
        text = btexts.get("kol_request", "🔮 <b>Request Your KOL Tracking</b>\n\nContact our team:")
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

    elif data == "promo_hub":
        cap = btexts.get("promo_hub", "🔮 <b>ADVERTISE WITH US</b>")
        try:
            with open(IMG_PROMO, "rb") as img:
                await query.message.reply_photo(photo=img, caption=cap,
                                                parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
        except Exception:
            await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

    elif data == "tracked_kols":
        channels = load_channels()
        lines    = "\n".join([f"🔮 @{ch}" for ch in channels])
        await query.message.reply_text(
            f"🔮 <b>Tracked KOLs — {len(channels)} Active</b>\n\n{lines}\n\n"
            "<i>Monitored 24/7. Alerts fire at 2x, 3x, 4x and beyond.</i>",
            parse_mode="HTML"
        )

    elif data == "leaderboard":
        results = []
        for key, call in tracked_calls.items():
            best = max(sent_milestones.get(key, [0]), default=0)
            if best > 0:
                results.append((call["channel"], call.get("symbol",""), best, call["entry_fmt"]))
        results.sort(key=lambda x: x[2], reverse=True)
        top = results[:10]
        if not top:
            await query.message.reply_text("🔮 <b>Leaderboard</b>\n\nNo milestones hit yet.", parse_mode="HTML")
            return
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        lines  = ["🔮 <b>Top Performing Calls</b>\n"]
        for i, (ch, sym, best, entry) in enumerate(top):
            s = f" ${sym}" if sym else ""
            lines.append(f"{medals[i]} @{ch}{s} — <b>{best}X</b> (from {entry})")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif data == "fast_track":
        cap = btexts.get("fast_track", "🔮 <b>FAST TRACK ACCESS</b>")
        try:
            with open(IMG_FASTTRACK, "rb") as img:
                await query.message.reply_photo(photo=img, caption=cap,
                                                parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
        except Exception:
            await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

    elif data == "chat_us":
        cap = btexts.get("chat_us", "🔮 <b>CONTACT WIZARD SCAN</b>")
        try:
            with open(IMG_CONTACT, "rb") as img:
                await query.message.reply_photo(photo=img, caption=cap,
                                                parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
        except Exception:
            await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

# ─── Owner-only decorator ─────────────────────────────────────────────────────
def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != OWNER_ID:
            await update.message.reply_text("⛔ Owner only.")
            return
        await func(update, context)
    return wrapper

# ─── Owner: channel management ────────────────────────────────────────────────
@owner_only
async def cmd_mychannels(update, context):
    channels = load_channels()
    lines = [f"{i+1}. @{ch}" for i, ch in enumerate(channels)]
    await update.message.reply_text(
        f"📡 <b>Tracked Channels ({len(channels)}):</b>\n\n" + "\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_addchannel(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /addchannel username"); return
    ch = context.args[0].lstrip("@").strip()
    channels = load_channels()
    if ch in channels:
        await update.message.reply_text(f"@{ch} already tracked."); return
    channels.append(ch); save_channels(channels)
    await update.message.reply_text(f"✅ @{ch} added! Tracking {len(channels)} channels.")

@owner_only
async def cmd_removechannel(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /removechannel username"); return
    ch = context.args[0].lstrip("@").strip()
    channels = load_channels()
    if ch not in channels:
        await update.message.reply_text(f"@{ch} not found."); return
    channels.remove(ch); save_channels(channels)
    await update.message.reply_text(f"✅ @{ch} removed!")

# ─── Owner: users & broadcast ─────────────────────────────────────────────────
@owner_only
async def cmd_myusers(update, context):
    users = load_users()
    await update.message.reply_text(f"👥 <b>Total Bot Users: {len(users)}</b>", parse_mode="HTML")

@owner_only
async def cmd_broadcast(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /broadcast message"); return
    msg   = " ".join(context.args)
    users = load_users()
    sent = fail = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, f"📢 <b>WIZARD SCAN</b>\n\n{msg}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
    await update.message.reply_text(f"✅ Done! Sent: {sent} | Failed: {fail}")

@owner_only
async def cmd_mystats(update, context):
    users = load_users(); channels = load_channels()
    await update.message.reply_text(
        f"📊 <b>WIZARD SCAN Stats</b>\n\n"
        f"👥 Users: {len(users)}\n"
        f"📡 Channels: {len(channels)}\n"
        f"📌 Tracked calls: {len(tracked_calls)}\n"
        f"🚀 Alerts sent: {sum(len(v) for v in sent_milestones.values())}",
        parse_mode="HTML"
    )

# ─── Owner: edit global alert template ────────────────────────────────────────
@owner_only
async def cmd_edittemplate(update, context):
    current = cfg_get("alert_template") or DEFAULT_TEMPLATE
    owner_edit_state[OWNER_ID] = {"state": ST_TEMPLATE}
    await update.message.reply_text(
        "✏️ <b>Edit Global Alert Template</b>\n\n"
        "Current:\n<pre>" + current + "</pre>\n\n"
        "📌 Placeholders: <code>{channel}</code> <code>{x}</code> <code>{symbol}</code> "
        "<code>{chain}</code> <code>{entry}</code> <code>{current}</code> "
        "<code>{ca}</code> <code>{kol_link}</code> <code>{tg_link}</code> <code>{bot_link}</code>\n\n"
        "Send new template:",
        parse_mode="HTML"
    )

# ─── Owner: per-milestone template ────────────────────────────────────────────
@owner_only
async def cmd_editmilestone(update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage: /editmilestone <X>\n\nExample: /editmilestone 2\n\n"
            f"Valid: {' '.join(str(m) for m in X_MILESTONES)}"
        )
        return
    ms = context.args[0].strip()
    try:
        if int(ms) not in X_MILESTONES:
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ Invalid milestone."); return

    config  = load_config()
    current = config.get("milestone_templates", {}).get(ms) or "(using global template)"
    owner_edit_state[OWNER_ID] = {"state": ST_MILESTONE_TMPL, "milestone": ms}
    await update.message.reply_text(
        f"✏️ <b>Edit {ms}X Alert Template</b>\n\nCurrent:\n<pre>{current}</pre>\n\n"
        "📌 Same placeholders apply. Leave blank to use global template.\n\n"
        "Send new template for <b>" + ms + "X</b> alerts:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearmilestone(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /clearmilestone <X>  e.g. /clearmilestone 2"); return
    ms = context.args[0].strip()
    c  = load_config()
    mt = c.get("milestone_templates", {})
    if ms in mt:
        del mt[ms]; c["milestone_templates"] = mt; save_config(c)
        await update.message.reply_text(f"✅ Custom template for {ms}X removed. Using global now.")
    else:
        await update.message.reply_text(f"No custom template for {ms}X.")

@owner_only
async def cmd_listmilestones(update, context):
    mt  = cfg_get("milestone_templates", {})
    med = cfg_get("milestone_media", {})
    lines = []
    for ms in X_MILESTONES:
        key   = str(ms)
        tmpl  = "✅ custom template" if key in mt  else "—"
        media = f"✅ {med[key]['type']}" if key in med else "—"
        lines.append(f"<b>{ms}X</b> | Template: {tmpl} | Media: {media}")
    await update.message.reply_text(
        "📋 <b>Milestone Overview</b>\n\n" + "\n".join(lines), parse_mode="HTML"
    )

# ─── Owner: milestone media ────────────────────────────────────────────────────
@owner_only
async def cmd_setmedia(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /setmedia <X>  e.g. /setmedia 10"); return
    ms = context.args[0].strip()
    try:
        if int(ms) not in X_MILESTONES: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid milestone."); return
    owner_edit_state[OWNER_ID] = {"state": ST_SET_MEDIA, "milestone": ms}
    await update.message.reply_text(
        f"📸 Send a <b>photo or video</b> for <b>{ms}X alerts</b>:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearmedia(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /clearmedia <X>"); return
    ms = context.args[0].strip()
    c  = load_config(); media = c.get("milestone_media", {})
    if ms in media:
        del media[ms]; c["milestone_media"] = media; save_config(c)
        await update.message.reply_text(f"✅ Media for {ms}X removed.")
    else:
        await update.message.reply_text(f"No media for {ms}X.")

@owner_only
async def cmd_listmedia(update, context):
    media = cfg_get("milestone_media", {})
    if not media:
        await update.message.reply_text("No media set yet. Use /setmedia <X>"); return
    lines = [f"✅ <b>{k}X</b> — {v.get('type','photo')}" for k, v in sorted(media.items(), key=lambda x: int(x[0]))]
    await update.message.reply_text("🖼️ <b>Milestone Media:</b>\n\n" + "\n".join(lines), parse_mode="HTML")

# ─── Owner: edit button texts & labels ────────────────────────────────────────
@owner_only
async def cmd_editbutton(update, context):
    valid = {"kol_request","promo_hub","fast_track","chat_us"}
    if not context.args or context.args[0] not in valid:
        opts = "\n".join([f"• <code>{b}</code>" for b in valid])
        await update.message.reply_text(
            f"Usage: /editbutton <button_id>\n\nAvailable:\n{opts}", parse_mode="HTML"); return
    btn = context.args[0]
    cur = cfg_get("button_texts", {}).get(btn, "(not set)")
    owner_edit_state[OWNER_ID] = {"state": ST_EDIT_BTN, "button": btn}
    await update.message.reply_text(
        f"✏️ <b>Edit '{btn}' text</b>\n\nCurrent:\n<pre>{cur[:600]}</pre>\n\nSend new text:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_editbtnlabel(update, context):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /editbtnlabel <button_id> <new label>\n"
            "IDs: kol_request promo_hub tracked_kols leaderboard fast_track chat_us"); return
    btn = context.args[0]; label = " ".join(context.args[1:])
    c = load_config(); lbls = c.get("button_labels", {}); lbls[btn] = label
    c["button_labels"] = lbls; save_config(c)
    await update.message.reply_text(f"✅ Label updated: {btn} → {label}")

@owner_only
async def cmd_editstart(update, context):
    cur = cfg_get("start_text", "")
    owner_edit_state[OWNER_ID] = {"state": ST_EDIT_START}
    await update.message.reply_text(
        f"✏️ <b>Edit /start text</b>\n\nCurrent:\n<pre>{cur[:600]}</pre>\n\nSend new text:",
        parse_mode="HTML"
    )

@owner_only
async def cmd_editcommandtext(update, context):
    cur = cfg_get("command_text", "")
    owner_edit_state[OWNER_ID] = {"state": ST_EDIT_CMD}
    await update.message.reply_text(
        f"✏️ <b>Edit /command text</b>\n\nCurrent:\n<pre>{cur}</pre>\n\nSend new text:",
        parse_mode="HTML"
    )

# ─── Owner: custom commands ────────────────────────────────────────────────────
@owner_only
async def cmd_addcmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /addcmd <name>  then send response text"); return
    name = context.args[0].lstrip("/").lower()
    owner_edit_state[OWNER_ID] = {"state": ST_ADD_CMD2, "cmd_name": name}
    await update.message.reply_text(f"Send response text for <code>/{name}</code>:", parse_mode="HTML")

@owner_only
async def cmd_removecmd(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /removecmd <name>"); return
    name = context.args[0].lstrip("/").lower()
    c = load_config(); cmds = c.get("custom_commands", {})
    if name in cmds:
        del cmds[name]; c["custom_commands"] = cmds; save_config(c)
        await update.message.reply_text(f"✅ /{name} removed.")
    else:
        await update.message.reply_text(f"/{name} not found.")

@owner_only
async def cmd_listcmds(update, context):
    cmds = cfg_get("custom_commands", {})
    if not cmds:
        await update.message.reply_text("No custom commands. Use /addcmd"); return
    lines = [f"• <code>/{k}</code>" for k in cmds]
    await update.message.reply_text("📋 <b>Custom Commands:</b>\n\n" + "\n".join(lines), parse_mode="HTML")

# ─── Owner help ───────────────────────────────────────────────────────────────
@owner_only
async def cmd_ownerhelp(update, context):
    await update.message.reply_text(
        "🔮 <b>OWNER COMMANDS</b>\n\n"
        "📡 <b>Channels</b>\n"
        "/mychannels · /addchannel · /removechannel\n\n"
        "👥 <b>Users</b>\n"
        "/myusers · /mystats · /broadcast\n\n"
        "✏️ <b>Alert Templates</b>\n"
        "/edittemplate — global template\n"
        "/editmilestone 2 — custom 2X template\n"
        "/editmilestone 10 — custom 10X template\n"
        "/clearmilestone 2 — remove custom template\n"
        "/listmilestones — overview of all milestones\n\n"
        "🖼️ <b>Milestone Media</b>\n"
        "/setmedia 2 — photo/video for 2X\n"
        "/clearmedia 2 — remove media\n"
        "/listmedia — view all set media\n\n"
        "🎛️ <b>Buttons &amp; Texts</b>\n"
        "/editbutton promo_hub\n"
        "/editbutton fast_track\n"
        "/editbutton chat_us\n"
        "/editbutton kol_request\n"
        "/editbtnlabel promo_hub New Label\n"
        "/editstart — /start welcome text\n"
        "/editcommandtext — /command center text\n\n"
        "⚡ <b>Custom Commands</b>\n"
        "/addcmd name · /removecmd name · /listcmds\n\n"
        "🎨 <b>AI Images</b>\n"
        "/imagine description — generate AI image\n\n"
        "/ownerhelp — this list",
        parse_mode="HTML"
    )

# ─── Message handler: image gen + owner editing ───────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    msg = update.message
    if not msg or not uid:
        return

    # Custom command triggers
    if msg.text and msg.text.startswith("/"):
        cmd_name = msg.text.split()[0].lstrip("/").split("@")[0].lower()
        custom_cmds = cfg_get("custom_commands", {})
        if cmd_name in custom_cmds:
            await msg.reply_text(custom_cmds[cmd_name], parse_mode="HTML")
            return

    # Owner editing state
    if uid == OWNER_ID and uid in owner_edit_state and owner_edit_state[uid].get("state"):
        state_info = owner_edit_state[uid]
        state      = state_info["state"]

        if state == ST_TEMPLATE:
            if msg.text:
                cfg_set("alert_template", msg.text)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text("✅ Global alert template updated!")
            return

        elif state == ST_MILESTONE_TMPL:
            ms = state_info["milestone"]
            if msg.text:
                c = load_config()
                mt = c.get("milestone_templates", {})
                mt[ms] = msg.text
                c["milestone_templates"] = mt
                save_config(c)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text(f"✅ Custom template for {ms}X saved!")
            return

        elif state == ST_SET_MEDIA:
            ms = state_info["milestone"]
            if msg.photo:
                fid = msg.photo[-1].file_id; ftype = "photo"
            elif msg.video:
                fid = msg.video.file_id; ftype = "video"
            else:
                await msg.reply_text("⚠️ Send a photo or video."); return
            c = load_config()
            media = c.get("milestone_media", {})
            media[ms] = {"type": ftype, "file_id": fid}
            c["milestone_media"] = media; save_config(c)
            owner_edit_state[uid] = {"state": ST_NONE}
            await msg.reply_text(f"✅ {ftype.capitalize()} saved for {ms}X alerts!")
            return

        elif state == ST_EDIT_BTN:
            btn = state_info["button"]
            if msg.text:
                c = load_config(); bt = c.get("button_texts", {})
                bt[btn] = msg.text; c["button_texts"] = bt; save_config(c)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text(f"✅ Button '{btn}' text updated!")
            return

        elif state == ST_EDIT_START:
            if msg.text:
                cfg_set("start_text", msg.text)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text("✅ /start text updated!")
            return

        elif state == ST_EDIT_CMD:
            if msg.text:
                cfg_set("command_text", msg.text)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text("✅ /command text updated!")
            return

        elif state == ST_ADD_CMD2:
            name = state_info["cmd_name"]
            if msg.text:
                c = load_config(); cmds = c.get("custom_commands", {})
                cmds[name] = msg.text; c["custom_commands"] = cmds; save_config(c)
                owner_edit_state[uid] = {"state": ST_NONE}
                await msg.reply_text(f"✅ Command <code>/{name}</code> added!", parse_mode="HTML")
            return

    # AI image generation — user waiting state
    if uid in user_img_state and user_img_state[uid].get("state") == IMG_WAITING:
        if msg.text and not msg.text.startswith("/"):
            await _do_generate(update, uid, msg.text)
            return

# ─── Register commands in Telegram menu ───────────────────────────────────────
async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start",   "Welcome & Bot Info"),
        BotCommand("command", "Command Center"),
        BotCommand("imagine", "Generate AI image"),
    ])
    logger.info("✅ Bot commands menu set")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!"); return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Public
    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("command",         cmd_command))
    app.add_handler(CommandHandler("imagine",         cmd_imagine))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Owner — channels
    app.add_handler(CommandHandler("mychannels",      cmd_mychannels))
    app.add_handler(CommandHandler("addchannel",      cmd_addchannel))
    app.add_handler(CommandHandler("removechannel",   cmd_removechannel))

    # Owner — users
    app.add_handler(CommandHandler("myusers",         cmd_myusers))
    app.add_handler(CommandHandler("broadcast",       cmd_broadcast))
    app.add_handler(CommandHandler("mystats",         cmd_mystats))

    # Owner — templates
    app.add_handler(CommandHandler("edittemplate",    cmd_edittemplate))
    app.add_handler(CommandHandler("editmilestone",   cmd_editmilestone))
    app.add_handler(CommandHandler("clearmilestone",  cmd_clearmilestone))
    app.add_handler(CommandHandler("listmilestones",  cmd_listmilestones))

    # Owner — media
    app.add_handler(CommandHandler("setmedia",        cmd_setmedia))
    app.add_handler(CommandHandler("clearmedia",      cmd_clearmedia))
    app.add_handler(CommandHandler("listmedia",       cmd_listmedia))

    # Owner — texts & buttons
    app.add_handler(CommandHandler("editbutton",      cmd_editbutton))
    app.add_handler(CommandHandler("editbtnlabel",    cmd_editbtnlabel))
    app.add_handler(CommandHandler("editstart",       cmd_editstart))
    app.add_handler(CommandHandler("editcommandtext", cmd_editcommandtext))

    # Owner — custom commands
    app.add_handler(CommandHandler("addcmd",          cmd_addcmd))
    app.add_handler(CommandHandler("removecmd",       cmd_removecmd))
    app.add_handler(CommandHandler("listcmds",        cmd_listcmds))

    # Owner help
    app.add_handler(CommandHandler("ownerhelp",       cmd_ownerhelp))

    # General message handler (image gen + owner editing)
    app.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.VIDEO,
        handle_message
    ))

    # Monitoring every 60s
    app.job_queue.run_repeating(monitoring_job, interval=60, first=30)

    logger.info(f"✅ Bot starting — Owner: {OWNER_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
