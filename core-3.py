# ===== core.py — Imports, config, storage, userbot, DEX/market data, CA extraction, premium-emoji sender =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).
import os
import re
import io
import json
import time
import uuid
import html
import hashlib
import logging
import requests
import asyncio
import functools
from datetime import datetime, timedelta
from collections import defaultdict, deque
from bs4 import BeautifulSoup
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, TypeHandler,
    ApplicationHandlerStop, ChatMemberHandler,
)
from telegram.error import TelegramError, RetryAfter, BadRequest, Forbidden
from telegram.request import HTTPXRequest
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
# httpx (used internally by python-telegram-bot) logs every request at INFO,
# including the full URL — which contains the bot token
# (https://api.telegram.org/bot<TOKEN>/getUpdates). That was leaking the token
# into Railway logs on every single poll. Silencing it to WARNING stops the
# leak without losing any real error visibility.
logging.getLogger("httpx").setLevel(logging.WARNING)
# httpx (used internally by python-telegram-bot) logs every request at INFO,
# including the full URL — which contains the bot token
# (https://api.telegram.org/bot<TOKEN>/getUpdates). That was leaking the token
# into Railway logs on every single poll. Silencing it to WARNING stops the
# leak without losing any real error visibility.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Startup gate ──────────────────────────────────────────────────────────────
# While False, NO new call is processed and NO alert is posted. It is flipped to
# True only after the startup pre-scan has marked every existing channel post as
# "seen". This is what stops the flood of old posts after every Railway redeploy.
BOT_READY   = False
BOT_START_TS = time.time()
STARTUP_STAGE = "boot"

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
OWNER_ID_2 = int(os.environ.get("OWNER_ID_2", "0"))
OWNER_IDS  = [oid for oid in [OWNER_ID, OWNER_ID_2] if oid != 0]
# Telegram backup chat — set BACKUP_CHAT_ID env var, or defaults to OWNER_ID
BACKUP_CHAT_ID = int(os.environ.get("BACKUP_CHAT_ID", OWNER_ID or 0))
BACKUP_TAG     = "BOT_BACKUP_v1"
# Bot ka apna user ID (token ka pehla hissa) — userbot is ID se bot ka chat dhundta hai
BOT_USER_ID    = int(BOT_TOKEN.split(":")[0]) if BOT_TOKEN and ":" in BOT_TOKEN else 0
OWNER_API_ID   = int(os.environ.get("USERBOT_API_ID", "") or os.environ.get("OWNER_API_ID", "0") or "0")
OWNER_API_HASH = os.environ.get("USERBOT_API_HASH", "") or os.environ.get("OWNER_API_HASH", "")
OWNER_PHONE    = os.environ.get("USERBOT_PHONE", "") or os.environ.get("OWNER_PHONE", "")

TARGET_CHANNEL  = "@WizardScan"
TG_CHANNEL_LINK = "https://t.me/WizardScan"
BOT_LINK        = "https://t.me/WIZARD_SCAN_BOT"
X_CHANNEL_LINK  = "https://t.me/WizardscanX"
X_ALERT_CHANNEL = ""  # X removed

# ─── Premium emoji on buttons ──────────────────────────────────────────────
# Telegram Bot API supports InlineKeyboardButton(icon_custom_emoji_id=...),
# which shows a custom/premium emoji as a native icon before the button's
# label (Telegram-side requirement: the bot owner must have Telegram Premium,
# or the bot must have a Fragment-purchased username — otherwise Telegram
# just ignores the icon and shows the button with plain text).
# All buttons in this bot use the 🔮 crystal-ball as branding, so instead of
# editing every InlineKeyboardButton(...) call by hand, _pkb() is a drop-in
# wrapper: it strips the plain 🔮 out of the label text and asks Telegram to
# render this premium emoji as the button's icon instead (single icon,
# BEFORE the text only — Telegram has no field for a second icon after the
# text). If the installed python-telegram-bot version is too old to know
# about icon_custom_emoji_id, or Telegram rejects it for any reason, it
# falls back to the original plain-🔮-both-sides label so buttons never break.
WIZ_BTN_EMOJI_ID = 5429257017728459986

_PKB_FALLBACK_LOGGED = {"done": False}

def _pkb(text, **kwargs):
    clean = re.sub(r"\s{2,}", " ", text.replace("🔮", "")).strip()
    try:
        return InlineKeyboardButton(clean or text, icon_custom_emoji_id=WIZ_BTN_EMOJI_ID, **kwargs)
    except TypeError as e:
        if not _PKB_FALLBACK_LOGGED["done"]:
            _PKB_FALLBACK_LOGGED["done"] = True
            try:
                import telegram as _tg_mod
                _ptb_ver = getattr(_tg_mod, "__version__", "unknown")
            except Exception:
                _ptb_ver = "unknown"
            logger.error(
                f"⚠️ icon_custom_emoji_id REJECTED by installed python-telegram-bot "
                f"(version={_ptb_ver}) — buttons falling back to plain 🔮 text. "
                f"requirements.txt needs a newer python-telegram-bot that supports "
                f"InlineKeyboardButton(icon_custom_emoji_id=...). Error: {e}"
            )
        # Older python-telegram-bot without icon_custom_emoji_id support.
        return InlineKeyboardButton(text, **kwargs)

def _icon_kb(text, icon_id, **kwargs):
    """Same safe-fallback pattern as _pkb(), but takes any custom emoji ID —
    for buttons that need a theme-specific icon instead of the fixed 🔮
    WIZ_BTN_EMOJI_ID (e.g. Hot Streak Alert's per-color 'View KOL' button)."""
    try:
        return InlineKeyboardButton(text, icon_custom_emoji_id=icon_id, **kwargs)
    except TypeError as e:
        if not _PKB_FALLBACK_LOGGED["done"]:
            _PKB_FALLBACK_LOGGED["done"] = True
            try:
                import telegram as _tg_mod
                _ptb_ver = getattr(_tg_mod, "__version__", "unknown")
            except Exception:
                _ptb_ver = "unknown"
            logger.error(
                f"⚠️ icon_custom_emoji_id REJECTED by installed python-telegram-bot "
                f"(version={_ptb_ver}) — buttons falling back to plain text. "
                f"requirements.txt needs a newer python-telegram-bot that supports "
                f"InlineKeyboardButton(icon_custom_emoji_id=...). Error: {e}"
            )
        return InlineKeyboardButton(text, **kwargs)

# Channel post IDs for auto-update
POST_TRENDING    = 135
POST_LEADERBOARD = 136
POST_CHAMPIONS   = 134   # moved from 101339 (2026-10-17)
POST_TRENDING_1  = 3560   # SOL + ETH + BSC trending (tokens 1-15)
POST_TRENDING_2  = 3562   # Robinhood + BASE + TON trending (tokens 16-30)

# Premium emoji IDs for new trending post 3560 (positions 1-15)
TRENDING2_EMOJIS_POS = {
    1:  5997041433582771180,
    2:  5996630843299208589,
    3:  5996651562221444470,
    4:  5997030232308063346,
    5:  5994684063472950720,
    6:  5996751574829898863,
    7:  5996820298601602863,
    8:  5996895748292091460,
    9:  5994803716966850151,
    10: 5996680656329907526,
    11: 5996586313078283620,
    12: 5996979577463774764,
    13: 5994808896697409956,
    14: 5997096288905077806,
    15: 5996926856740216806,
    # positions 16-30 for post 3562
    16: 5999182938636295448,
    17: 5996815157525751350,
    18: 5998989699467714710,
    19: 5996964184300985351,
    20: 5996607147964636937,
    21: 5999300878438243947,
    22: 5999226708648009426,
    23: 5999176637919279146,
    24: 5999344077219307323,
    25: 5999144876636118289,
    26: 5996733888154576402,
    27: 5999311869259553343,
    28: 5996818529075077224,
    29: 5999155257572072966,
    30: 5996565667170493190,
}

# Chain header emojis for new trending posts
TRENDING2_CHAIN_EMOJIS = {
    "SOL":  5999041428053826576,
    "ETH":  5996776519999955154,
    "BSC":  5998847136618259004,
    "RH":   5996639403169030164,
    "BASE": 5999317663170436624,
    "TON":  5994646315005387738,
}
# MC/arrow emoji for new trending posts
TRENDING2_ARROW_EMOJI = 5996709080423472380
TRENDING2_MC_EMOJI    = 5996883503340330760

# ─── File paths ───────────────────────────────────────────────────────────────
# DATA_DIR lets all persistent bot data live on a Railway Volume (survives redeploys).
# Set DATA_DIR=/app/data as an env var once a Volume is mounted there; defaults to "."
# so it still works unchanged when running locally / without a volume.
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
def _dp(name):  # data path helper
    return os.path.join(DATA_DIR, name)

USERS_FILE            = _dp("users.json")
CHANNELS_FILE         = _dp("channels.json")
BOT_CONFIG_FILE       = _dp("bot_config.json")
USERBOT_SESSION_FILE  = _dp("userbot_session.txt")
X_ACCOUNTS_FILE       = _dp("x_accounts.json")
SPECIAL_CHANNELS_FILE = _dp("special_channels.json")
SUBSCRIPTIONS_FILE    = _dp("subscriptions.json")
LINKED_CHANNELS_FILE  = _dp("linked_channels.json")
PENDING_REQUESTS_FILE = _dp("pending_requests.json")
TRACKED_FILE          = _dp("tracked_calls.json")
MILESTONES_FILE       = _dp("sent_milestones.json")
SEEN_FILE             = _dp("seen_messages.json")
ADMINS_FILE           = _dp("admins.json")
MILESTONE_POSTS_FILE  = _dp("milestone_posts.json")
MILESTONE_TIMES_FILE  = _dp("milestone_times.json")   # {call_key: {str(x): iso_ts}}
PS_PROJECTS_FILE      = _dp("ps_projects.json")       # PinkSale project detail cache
XALERT_DETAILS_FILE   = _dp("xalert_details.json")    # short-id -> call_key, for the X-alert "Details" deep-link
CHANNEL_SUBS_FILE     = _dp("channel_subs.json")
CHANNEL_META_FILE     = _dp("channel_meta.json")   # subscriber counts + avatar flags, refreshed by channel_meta_job (low-frequency, read-only Telegram calls)
MOMENTUM_SENT_FILE    = _dp("momentum_sent.json")
MOMENTUM_REPORTS_FILE = _dp("momentum_reports.json")   # snapshot of X-Ray rows per momentum post
BROADCASTS_FILE       = _dp("broadcasts.json")   # per-broadcast {chat_id, message_id} list so owner can later edit/delete a broadcast from every recipient's DM
XRAY_ARCHIVE_FILE     = _dp("xray_archive.json")        # permanent per-channel milestone record for X-Ray Report
CALL_ARCHIVE_FILE     = _dp("call_archive.json")        # permanent /history record — rugged calls get archived here before being pruned from tracked_calls
CHANNEL_POINTS_FILE   = _dp("channel_points.json")
TRENDING_BLACKLIST_FILE  = _dp("trending_blacklist.json")
GLOBAL_CA_BAN_FILE       = _dp("global_ca_bans.json")   # CAs frozen for ALL callers — checked before any new call is ever tracked
PINNED_TRENDING_FILE     = _dp("pinned_trending.json")
KOL_OWNERS_FILE         = _dp("kol_owners.json")   # channel.lower() -> telegram user_id of owner
TRENDING_CACHE_FILE   = _dp("trending_cache.json")
TRENDING2_CACHE_FILE  = _dp("trending2_cache.json")

# ─── Images ───────────────────────────────────────────────────────────────────
IMG_PROMO     = "attached_assets/IMG_20260613_095837_780_1781330812860.jpg"   # Promotion Hub
IMG_CONTACT   = "attached_assets/IMG_20260613_095833_447_1781330812885.jpg"   # Contact / Chat
IMG_LEADERBOARD = "attached_assets/IMG_20260708_021612_436_1783459182111.jpg" # Leaderboard
IMG_FASTTRACK = "attached_assets/IMG_20260613_095829_072_1781330812925.jpg"   # Fast Track
IMG_KOLREQUEST = "attached_assets/IMG_20260613_095820_581_1781330812951.jpg"  # Request KOL
IMG_REQUEST_SUBMITTED = "attached_assets/IMG_REQUEST_SUBMITTED.png"          # /submit confirmation banner
IMG_LINKME    = "attached_assets/IMG_LINKME.png"                              # /linkme info
IMG_TRACKED   = "attached_assets/IMG_20260704_021900_436_1783113819561.jpg"   # Tracked KOLs
IMG_HISTORY   = "attached_assets/IMG_HISTORY.png"                             # /history info
IMG_ALERT     = "attached_assets/IMG_20260613_095809_422_1781330812978.jpg"   # Alert Rules
IMG_XCOMMAND  = "attached_assets/file_00000000ef9872078bbbb84ad23477b8_1781330813005.png"  # X/Twitter
VID_START     = "attached_assets/VID_20260613_095844_594_1781330812838.mp4"   # Start video (5-sec)
VID_X_ALERT_DETAILS = "attached_assets/wizard-scan-call-signal-red-printed-v5.mp4"   # X-alert "Details" video
VID_PROMO     = "attached_assets/VID_20260613_095756_826_1781330812992.mp4"   # Command/promo video
VID_COMMAND   = "attached_assets/VID_COMMAND.mp4"                             # /command menu video
VID_XRAY      = "attached_assets/hailuo-2_3_X-Ray_Scan_text_ma_effects_dal_dain._Crystall_ball__1782604190889.mp4"  # X-Ray Report reply video
VID_HISTORY   = "attached_assets/hailuo-2_3_Clouds_ma_motion_add_krain_crow_ma_motion_add_krain_1782605705389.mp4"  # Call History reply video
VID_CHAT_US   = "attached_assets/hailuo-2_3_Wizard_Scan_Text_ma_wave_motion_add_kr_krain._Wizar_1783457473002.mp4"  # Chat With Us reply video
IMG_HASHTAG   = "hashtag.png"  # Hashtag post image (same folder as bot.py)

# ─── Momentum Active videos (rotating) ────────────────────────────────────────
VID_MOMENTUM_LIST = [
    "attached_assets/hailuo-2_3_Photo_ma_effects_dal_dain_aur_objects_ma_motivation_1782603110946.mp4",
    "attached_assets/hailuo-2_3_Text_aur_baki_cheezon_ma_powerful_effects_aur_motio_1782603110959.mp4",
    "attached_assets/hailuo-2_3_Momentum_Active_text_ma_powerful_effects_dal_dain.__1782603110982.mp4",
    "attached_assets/hailuo-2_3_Momentum_Active_ma_effects_aur_motion_add_krain._Cr_1782603110994.mp4",
    "attached_assets/hailuo-2_3_Crystal_ball_ma_effects_dal_dain._Aur_skull_ma_vibr_1782603111012.mp4",
]

HASHTAG_CAPTION = (
    "#WizardScan #Crypto #CryptoCalls #CryptoAlerts #KOL #KOLCalls "
    "#CryptoTracking #Memecoins #Altcoins #GemHunter #CryptoCommunity "
    "#CryptoSignals #Moonshots #DeFi #Solana #Ethereum #BNBChain #BaseChain "
    "#OnChain #TokenCalls #CryptoGems #Trading #BullRun #MarketCap "
    "#100xGems #CryptoAlpha #Blockchain #TrendingTokens #KOLTracker "
    "#CallerLeaderboard #WizardCommunity #CryptoWizards #DYOR #Pumps"
)

X_MILESTONES = [2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 35, 40, 45, 50,
                55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 150, 200, 250, 300,
                500, 600, 700, 800, 900, 1000, 2000, 3000, 4000, 5000,
                6000, 7000, 8000, 9000, 10000,
                11000, 12000, 13000, 14000, 15000, 20000, 25000, 30000,
                40000, 50000, 60000, 70000, 80000, 90000, 100000]
# Extreme values above 1000X are held back instead of being auto-credited.
# This is a safety boundary, not a claim that higher returns are impossible.
MAX_MILESTONE = 1_000

SUPPORTED_CHAINS = {"ethereum": "ETH", "bsc": "BNB", "base": "BASE", "solana": "SOL",
                    "robinhood": "RH", "ton": "TON", "arc": "ARC"}   # Robinhood L2 (EVM, OP-Stack); ARC = Circle's new L1 (EVM, launched 2026-09-16)
CHAIN_TO_DEXPATH = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base",
                    "RH": "robinhood", "TON": "ton", "ARC": "arc"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# NOTE: standard EVM addresses are 0x + 40 hex chars, but some newer chains
# listed on DexScreener (e.g. Robinhood/"RH" tokenized-stock chain) use a
# 32-byte address (0x + 64 hex chars) instead. The 64-hex alt is tried first,
# and both branches use a negative lookahead so a 64-hex address is never
# mis-matched as just its first 40 hex chars (which would silently track the
# wrong/truncated address).
ETH_CA_PATTERN = re.compile(r'0x[a-fA-F0-9]{64}(?![a-fA-F0-9])|0x[a-fA-F0-9]{40}(?![a-fA-F0-9])')
SOL_CA_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
TON_CA_PATTERN = re.compile(r'\b(EQ|UQ)[A-Za-z0-9_-]{46}\b')  # TON user-friendly addresses (48 chars)
TW_LINK_RE     = re.compile(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)', re.IGNORECASE)
TG_MENTION_RE  = re.compile(r'^@([A-Za-z0-9_]{3,32})\s*$')

# ─── States ───────────────────────────────────────────────────────────────────
ST_NONE = ST_TEMPLATE = ST_MILESTONE_TMPL = ST_SET_MEDIA = None
ST_EDIT_BTN = ST_EDIT_START = ST_EDIT_CMD = ST_ADD_CMD2 = None
ST_USERBOT_OTP = ST_USERBOT_2FA = ST_BTN_NAME = ST_BTN_URL = None
ST_TEMPLATE         = "edit_template"
ST_LEADERBOARD_TMPL = "edit_leaderboard_tmpl"
ST_RANGE_TMPL        = "edit_range_tmpl"
ST_KOL_RANGE_TMPL     = "edit_kol_range_tmpl"   # per-channel (KOL) range template — /setkoltemplate

# Premium emoji IDs for leaderboard post 136
LEADERBOARD_PREMIUM_EMOJIS = {
    "star":  5807621337035842393,
    "arrow": 5809813380969537713,
    1:  5807696606337703890,
    2:  5807710826974420795,
    3:  5807617338421289285,
    4:  5807698311439721429,
    5:  5807461821950467873,
    6:  5807463114735624394,
    7:  5807614559577449387,
    8:  5807410527156052455,
    9:  5807690301325712919,
    10: 5807897739656175948,
}

# Premium emoji IDs for champions post 134
CHAMPIONS_PREMIUM_EMOJIS = {
    "star":  5807491057792853272,
    "arrow": 5807952487604297661,
    "bolt":  6044166381690166762,   # ⚡️ — 3 info points in the champions post
    1:  5807615306901759629,
    2:  5807631344309642763,
    3:  5807396667296588812,
    4:  5807712914328526729,
    5:  5807614774325813553,
    6:  5807561465191737490,
    7:  5807686972726058383,
    8:  5807454391657045517,
    9:  5807632997872049875,
    10: 5807816251241670522,
}

# Leading emoji of the Champion KOL badge in X alerts:
#   "🔮 CHAMP KOL - Rank # 🔮"  (first 🔮 = this id, last 🔮 = rank number emoji)
CHAMP_BADGE_LEAD_EMOJI_ID = 5823636561413021540
CHAMPIONS_WEB_LINK = "https://wizardscanlive.com/#champions"

# Premium emoji tags for user-facing bot messages (parse_mode="HTML")
PE_CRYSTAL = '<tg-emoji emoji-id="5888670372093502546">🔮</tg-emoji>'
PE_WAND    = '<tg-emoji emoji-id="5260426225599405269">🪄</tg-emoji>'
PE_ARROW   = '<tg-emoji emoji-id="5823462288820020063">➤</tg-emoji>'

# ─── Call-history premium emoji pack ─────────────────────────────────────────
# These IDs are intentionally separate from the alert/trending emoji packs.
HISTORY_TITLE_EMOJI = 6043914791095902818
HISTORY_ARROW_EMOJI = 6044088458098514935
# Shown right after the token symbol, before the x-multiplier (e.g. "$LOPA 🔮 1.4x")
HISTORY_X_EMOJI      = '<tg-emoji emoji-id="5809928529042743187">🔮</tg-emoji>'
# Replaces PE_WAND for "View Post" links — history/record view only, not global
# NOTE: this custom emoji's real base/fallback glyph is 🔮 (crystal ball), not
# 🪄 — confirmed by the owner. The fallback character here must match, or
# Telegram won't render it correctly for non-Premium viewers.
HISTORY_WAND_EMOJI   = '<tg-emoji emoji-id="5809723989815205224">🔮</tg-emoji>'
HISTORY_CHAIN_EMOJIS = {
    "BASE": 6042127904312140234,
    "ETH":  6043930978827640511,
    "TON":  6042026268206048350,
    "SOL":  6044027306354155516,
    "RH":   6044076483729694171,
    "BNB":  6044153037226778009,
    "BSC":  6044153037226778009,
    "ARC":  5861585530935843716,
}
# Detailed lookup view (typing @channelname directly) — stat-line icons
LOOKUP_BEST_CALL_EMOJI = 5846193940105012285
LOOKUP_POINTS_EMOJI    = 5846051003593401449
LOOKUP_CALLS_EMOJI     = 5845744321453631630
LOOKUP_ALPHA_EMOJI     = 5845688435339175067
LOOKUP_CONSISTENCY_EMOJI = 5846117180449497406
LOOKUP_LAST5_EMOJI     = 6044189703362584375
LOOKUP_FOOTER_EMOJI    = 6044119257308995249

def _history_emoji(emoji_id, fallback):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

def _display_handle(value):
    """Display a Telegram username with a capitalized first character."""
    raw = str(value or "").lstrip("@").strip()
    return raw[:1].upper() + raw[1:].lower() if raw else raw

def _history_chain_emoji(chain):
    key = str(chain or "").upper()
    return _history_emoji(HISTORY_CHAIN_EMOJIS.get(key, HISTORY_CHAIN_EMOJIS["SOL"]), "🔮")

def _history_arrow_emoji():
    return _history_emoji(HISTORY_ARROW_EMOJI, "➤")

# Premium emoji IDs for trending post 135
TRENDING_PREMIUM_EMOJIS = {
    "SOL":   5818711831652343968,
    "BASE":  5818705539525255782,
    "ETH":   5821222313051301501,
    "BNB":   5820961269234016758,
    "TON":   5996819753140756074,   # TON green emoji
    "arrow": 5823462288820020063,   # arrow separator between token name and MC
    1:  5821233187908492644,
    2:  5818765681952301203,
    3:  5821435678436631722,
    4:  5820945364970118156,
    5:  5821055556651065265,
    6:  5821457668669185609,
    7:  5821379616228515432,
    8:  5821401091064995694,
    9:  5821320251190550541,
    10: 5821001508782611959,
    11: 5823306407276977559,
    12: 5823285306102652498,
    13: 5823250426673241974,
    14: 5823535711285943606,
    15: 5823301407935045875,
    16: 5821131246859723209,
    17: 5823664190937636546,
    18: 5820910872087765095,
    19: 5823393766911778754,
    20: 5823595432806194944,
}

# ─── Alert premium emoji packs (rotate every 10 posts) ────────────────────────
TEER_EMOJI_ID = 5807638920631951079  # locked — never stored in config so restore can't change it
MC_ENTRY_EMOJI_ID = 5816433179703124832  # locked — 2nd 🔮 (entry MC) — same across all color packs
MOMENTUM_ACTIVE_EMOJI_ID = 5920079442159344914

# ─── Trade (Maestro) premium emoji IDs (per colour) ──────────────────────────
MAE_EMOJIS = {
    "red":    5814610803604594885,
    "blue":   5801054392169799401,
    "white":  5999292610626199656,
    "purple": 5805269198196188467,
    "green":  5803375340956949524,
    "dropped":5807410153493901098,   # special: dropped-call post MAE
}
MAESTRO_REFLINK_BASE = "https://t.me/maestro?start="
MAESTRO_REF_SUFFIX   = "_r-wizard_scan"   # appended after CA
MAESTRO_PLAIN_REFLINK = "https://t.me/maestro?start=r-wizard_scan"

# ── X-alert inline buttons (Caller · Twitter · Trade · Dex · Details · Dev) ──
WIZARD_X_FALLBACK_LINK = "https://x.com/Cheesy_Wolf"   # jab KOL ka X handle set na ho — WizardScan ka Twitter delete ho gya (2026-09-26)
WIZARD_DEV_LINK        = "https://t.me/Wizard_Scan"   # @Wizard_Scan

# NOTE on key names (owner ke naye naam):
#   mae  == Trade    (Maestro trade link)
#   kol  == Caller   (KOL ki original call post)
#   bot  == Details  (Details button/link)
# Purane keys (mae/kol/bot) code me har jagah use hote hain, is liye wo waise
# hi rakhe gaye hain — saath me naye alias keys (trade/caller/details) aur
# naye twitter/dex/dev emoji IDs bhi add kiye gaye hain.
EMOJI_PACKS = [
    {   # Sirf yahi emojis — har 10 posts wali rotation band.
        # Owner baad me naye packs isi list me add karega.
        "name": "default",
        "crystal": 5816433179703124832,   # Crystal ball (entry MC)
        "kol":     5816473758554136304,   # Caller
        "mae":     5816930983592599467,   # Trade
        "bot":     5816604050682028040,   # Details
        "caller":  5816473758554136304,
        "trade":   5816930983592599467,
        "details": 5816604050682028040,
        "twitter": 5816676712938741891,
        "dex":     5816447254310954356,
        "dev":     5816456703239004710,
        "chain": {
            "SOL":  5816406349042425264,
            "ETH":  5816735725789388508,
            "BNB":  5816541305504800959,
            "BSC":  5816541305504800959,
            "BASE": 5816735725789388508,  # BASE emoji nahi diya gaya — ETH fallback
            "RH":   5818717423699763201,
            "TON":  5816609367851542120,
            "ARC":  5861777584693453399,
        },
    },
    {   # BLUE pack — 11X aur us se upar wali X alerts ke liye.
        "name": "blue",
        "teer":    5818848205453927129,   # Teer (MC separator)
        "crystal": 5816826345304367540,   # Crystal ball (entry MC)
        "kol":     5864095960795193775,   # Caller
        "mae":     5866050647656241980,   # Trade
        "bot":     5863725747499181228,   # Details
        "caller":  5864095960795193775,
        "trade":   5866050647656241980,
        "details": 5863725747499181228,
        "twitter": 5866085209258076126,
        "dex":     5863945855983166730,
        "dev":     5864070027782661496,
        "chain": {
            "SOL":  5865982877367279051,
            "ETH":  5865974768469026405,
            "BNB":  5866260216290483682,
            "BSC":  5866260216290483682,
            "BASE": 5866335262254047661,
            "RH":   5866254272055747007,
            "TON":  5866181502424850828,
            "ARC":  5866195366579281407,
        },
    },
]

# GOLD pack — 76X..99X X alerts only. Kept OUT of EMOJI_PACKS on purpose so the
# auto-rotation (count // 10) used by non-X posts is unchanged.
X76_PACK = {
    "name":    "x76_99",
    "teer":    5924885884225725783,
    "crystal": 5922528720274333196,
    "kol":     5924840202953563651,   # Caller
    "mae":     5924887348809572269,   # Trade
    "bot":     5925032716272672656,   # Details
    "caller":  5924840202953563651,
    "trade":   5924887348809572269,
    "details": 5925032716272672656,
    "twitter": 5924797287640342361,
    "dex":     5924716748413608464,
    "dev":     5924778419849011394,
    "chain": {
        "SOL":  5922628608328737076,
        "ETH":  5926897127216128491,
        "BNB":  5924529380465319917,
        "BSC":  5924529380465319917,
        "BASE": 5922793719756498359,
        "RH":   5925025792785391919,
        "TON":  5922492423505715776,
        "ARC":  5926935975195320004,
    },
}

# ── X-based pack rules ────────────────────────────────────────────────────
# 2X se 10X tak  -> "default" (red pack)
# 11X se upar    -> "blue" pack
# Owner /ownerhelp ke button se apni custom ranges (jaise 31-40) add kar
# sakta hai — wo config["x_emoji_packs"] me save hoti hain aur in built-in
# rules se pehle check hoti hain.
X_PACK_RULES = [
    (2, 10, "default"),
    (11, 75, "blue"),
    (76, 99, "x76_99"),
    (100, 10 ** 9, "blue"),
]

# Custom x-range pack wizard ke steps (owner se isi tarteeb me maange jaate hain)
XEM_STEPS = [
    ("chain.BNB", "BNB chain emoji"),
    ("chain.ETH", "ETH chain emoji"),
    ("chain.SOL", "SOL chain emoji"),
    ("chain.TON", "TON chain emoji"),
    ("chain.BASE", "BASE chain emoji"),
    ("chain.RH", "RH (Robinhood) chain emoji"),
    ("chain.ARC", "ARC chain emoji"),
    ("crystal", "Crystal ball emoji (entry MC)"),
    ("teer", "Teer emoji (dono MC ke darmiyan)"),
    ("caller", "Caller emoji"),
    ("twitter", "Twitter emoji"),
    ("trade", "Trade emoji"),
    ("dex", "DEX emoji"),
    ("details", "Details emoji"),
    ("dev", "DEV emoji"),
]
ST_XEM_RANGE = "xem_range"
ST_XEM_EMOJI = "xem_emoji"
ST_XEM_BULK  = "xem_bulk"   # /setxemojisbulk — paste all 14 IDs in one message

ST_X_TEMPLATE      = "edit_x_template"
ST_ADD_DROPPED_VID = "add_dropped_vid"
ST_DROPPED_TMPL    = "edit_dropped_tmpl"
ST_KOL_DROPPED_TMPL = "edit_kol_dropped_tmpl"   # per-channel dropped-call template — /setkoldroppedtemplate
ST_BULK_LANG        = "bulk_lang_tag"           # guided multi-KOL language tagging — /addkolstolang
ST_LANG_RANGE_TMPL  = "edit_lang_range_tmpl"    # direct language-group range template — /setlangtemplate
ST_LANG_DROPPED_TMPL = "edit_lang_dropped_tmpl" # direct language-group dropped-call template — /setlangdroppedtemplate
ST_ADD_PS_MEDIA    = "add_ps_media"
ST_ADD_CP_MEDIA    = "add_cp_media"
ST_ADD_CPD_MEDIA   = "add_cpd_media"   # CheesePad DETAILS (bot ke andar) media
ST_ADD_PSD_MEDIA   = "add_psd_media"   # PinkSale DETAILS (bot ke andar) media

# ─── Dropped Call feature ─────────────────────────────────────────────────────
DROPPED_CALL_EMOJI = 5807843069017464354  # SOL chain emoji — default fallback when chain unknown

# Locked emojis for the "1️⃣ Token Symbol / Current MC / Chain Symbol ➡️ value"
# rows in the dropped-call template. These must NEVER be filled from the
# positional chain-emoji list (that list is reserved for the header/footer
# 🔮🔮 only) — see the hard-coded context lock in _build_premium_entities().
DROPPED_NUM_EMOJI   = 5877468380125990242   # "1️⃣" numbering emoji (used 3x)
DROPPED_ARROW_EMOJI = 5884123981706956210   # "➡️" arrow emoji (used 3x)

# Chain-specific emojis for the two header 🔮 in dropped-call posts
DROPPED_CHAIN_EMOJIS = {
    "SOL":  5807843069017464354,
    "ETH":  5809865010771402720,
    "BNB":  5807602357575361585,
    "BSC":  5807602357575361585,
    "BASE": 5809822241487069151,
    "RH":   5807444096620444016,
    "TON":  5807455851945927306,  # TON drop call emoji
    "ARC":  5861661362878423604,
}

# Specific emoji IDs for KOL / MAE / BOT links in dropped-call posts
DROPPED_KOL_EMOJI = 5810115394479857144
DROPPED_MAE_EMOJI = 5807410153493901098   # Special Maestro emoji for dropped-call posts
DROPPED_BOT_EMOJI = 5807874018551799876

# Forced pack used by dropped-call alert so KOL/MAE/BOT get their own emojis
_DROPPED_CALL_PACK = {
    "name": "dropped", "kol": DROPPED_KOL_EMOJI,
    "mae": DROPPED_MAE_EMOJI, "bot": DROPPED_BOT_EMOJI,
}

DEFAULT_DROPPED_TEMPLATE = (
    "🔮 <b>@{channel} Dropped a Call</b> 🔮\n\n"
    '<tg-emoji emoji-id="5877468380125990242">1️⃣</tg-emoji>Token Symbol  <tg-emoji emoji-id="5884123981706956210">➡️</tg-emoji>  ${symbol}\n'
    '<tg-emoji emoji-id="5877468380125990242">1️⃣</tg-emoji>Current MC      <tg-emoji emoji-id="5884123981706956210">➡️</tg-emoji>  {entry}\n'
    '<tg-emoji emoji-id="5877468380125990242">1️⃣</tg-emoji>Chain Symbol    <tg-emoji emoji-id="5884123981706956210">➡️</tg-emoji>  {chain}\n\n'
    "We've started tracking it and will continue to send performance alerts "
    "as the token progresses. Stay tuned!\n\n"
    "Ca: <code>{ca}</code>\n\n"
    '🔮<a href="{mae_link}">MAE</a>\n'
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="{bot_link}">BOT</a>'
)
ST_MILESTONE_TMPL = "edit_milestone_tmpl"
ST_SET_MEDIA     = "set_media"
ST_SET_HS_MEDIA  = "set_hs_media"
ST_EDIT_BTN      = "edit_button"
ST_EDIT_START    = "edit_start"
ST_EDIT_CMD      = "edit_command_text"
ST_ADD_CMD2      = "add_command_response"
ST_USERBOT_OTP      = "userbot_otp"
ST_USERBOT_2FA      = "userbot_2fa"
ST_SETTEMPLATE_EM   = "settemplate_em"
ST_BROADCAST_PICK      = "broadcast_pick"
ST_BROADCAST_MSG       = "broadcast_msg"
ST_EDIT_BROADCAST_MSG  = "edit_broadcast_msg"   # /editbroadcast — next message replaces the stored broadcast in every recipient's DM
ST_MEDIABROADCAST_MSG  = "mediabroadcast_msg"
ST_SETPROMOLINK        = "set_promo_link"
ST_SETPROMO_EMOJI      = "set_promo_emoji"
ST_DROPSKIP_CH         = "dropskip_channel"   # /dropskip — owner types the KOL channel
ST_DROPSKIP_PAT        = "dropskip_pattern"   # /dropskip — owner types "post skip" (e.g. 1 2)
ST_SET_PUBLIC_TEXT     = "set_public_text"
ST_SETKOLOWNER_CH      = "setkolowner_channel"
ST_SETKOLOWNER_USER    = "setkolowner_user"
ST_SETKOLOWNER_PENDING_CH = ""  # temp storage per-user

# ─── Persistence ──────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def load_users_dict():
    """Load users as dict {str(id): {id, username, name}}. Auto-converts old list format."""
    raw = load_json(USERS_FILE, {})
    if isinstance(raw, list):
        return {str(uid): {"id": uid, "username": None, "name": None} for uid in raw}
    return raw

def save_users_dict(d): save_json(USERS_FILE, d)

def load_users():
    """Return list of user IDs (backward compat)."""
    return [int(k) for k in load_users_dict().keys()]

def save_users(u): pass  # kept for backward compat — use save_users_dict

# ─── Broadcast log (for /editbroadcast & /deletebroadcast) ────────────────────
def load_broadcasts():
    """{'next_id': int, 'items': {id_str: {'sent_at':..., 'is_media':bool, 'messages':[{'chat_id':int,'message_id':int}]}}}"""
    return load_json(BROADCASTS_FILE, {"next_id": 1, "items": {}})

def save_broadcasts(d): save_json(BROADCASTS_FILE, d)

def record_broadcast(messages, is_media=False):
    """Save a completed broadcast's per-recipient message IDs and return its id."""
    d = load_broadcasts()
    bid = str(d.get("next_id", 1))
    d["items"][bid] = {
        "sent_at": datetime.utcnow().isoformat(),
        "is_media": is_media,
        "messages": messages,  # [{"chat_id": tid, "message_id": mid}, ...]
    }
    d["next_id"] = int(bid) + 1
    save_broadcasts(d)
    return bid

def load_channels():
    """Return tracked channels, self-healing case-insensitive duplicates
    (e.g. 'SomeKOL' and 'somekol' both stored) that caused the same channel
    to show twice under Tracked KOLs and be scanned/monitored twice."""
    raw = load_json(CHANNELS_FILE, [])
    seen, deduped, dup_found = set(), [], False
    for c in raw:
        key = str(c).lstrip("@").lower()
        if key in seen:
            dup_found = True
            continue
        seen.add(key)
        deduped.append(c)
    if dup_found:
        save_json(CHANNELS_FILE, deduped)
    return deduped
def save_channels(c):        save_json(CHANNELS_FILE, c)

# ── Permanently removed channels ─────────────────────────────────────────────
# Jab owner /removechannel karta hai to channel yahan aa jata hai. Jab tak
# channel is list me hai, uski KOI post process nahi hoti — na purani na nayi.
REMOVED_CHANNELS_FILE = _dp("removed_channels.json")
def load_removed_channels() -> set:
    try:
        return {str(c).lstrip("@").lower() for c in load_json(REMOVED_CHANNELS_FILE, [])}
    except Exception:
        return set()
def save_removed_channels(s):
    save_json(REMOVED_CHANNELS_FILE, sorted({str(c).lstrip("@").lower() for c in s}))
_REMOVED_CHANNELS = load_removed_channels()
def is_channel_removed(ch) -> bool:
    return str(ch or "").lstrip("@").lower() in _REMOVED_CHANNELS
def mark_channel_removed(ch):
    _REMOVED_CHANNELS.add(str(ch).lstrip("@").lower())
    save_removed_channels(_REMOVED_CHANNELS)
def unmark_channel_removed(ch):
    _REMOVED_CHANNELS.discard(str(ch).lstrip("@").lower())
    save_removed_channels(_REMOVED_CHANNELS)
def load_config():           return load_json(BOT_CONFIG_FILE, {})
def save_config(c):          save_json(BOT_CONFIG_FILE, c)

# ── Priority (special) KOL channels ──────────────────────────────────────────
# Owner /addpriority se channel add karta hai. Ye channels har scan aur har
# monitoring tick me SABSE PEHLE process hote hain, aur inke liye ek extra
# 1-second fast-lane job chalti hai — inki koi call skip ya late na ho.
PRIORITY_CHANNELS_FILE = _dp("priority_channels.json")
def load_priority_channels() -> list:
    try:
        return [str(c).lstrip("@") for c in load_json(PRIORITY_CHANNELS_FILE, [])]
    except Exception:
        return []
def save_priority_channels(lst):
    save_json(PRIORITY_CHANNELS_FILE, sorted({str(c).lstrip("@") for c in lst}))
def priority_channels_lower() -> set:
    return {c.lower() for c in load_priority_channels()}
def is_priority_channel(ch) -> bool:
    return str(ch or "").lstrip("@").lower() in priority_channels_lower()
def _clean_x_handle(value):
    """Return a safe bare X handle; reject Telegram/other URLs and junk."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if any(part in lowered for part in ("t.me/", "telegram.me/", "telegram.dog/")):
        return ""
    # Accept x.com/twitter.com profile URLs, but never arbitrary URLs.
    url_match = re.fullmatch(
        r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/?(?:\?.*)?",
        raw, re.IGNORECASE)
    if url_match:
        raw = url_match.group(1)
    elif "://" in raw or "/" in raw:
        return ""
    raw = raw.lstrip("@").strip()
    return raw if re.fullmatch(r"[A-Za-z0-9_]{1,15}", raw) else ""

def load_x_accounts():
    data = load_json(X_ACCOUNTS_FILE, {})
    if isinstance(data, list):
        # Migrate old list format → dict {channel_lower: x_handle}
        result = {}
        for item in data:
            if isinstance(item, dict):
                ch = item.get("channel","").lstrip("@").lower()
                x  = (item.get("x","") or item.get("twitter","") or item.get("handle","")).lstrip("@")
                if ch and x:
                    clean = _clean_x_handle(x)
                    if clean:
                        result[ch] = clean
        if result:
            save_json(X_ACCOUNTS_FILE, result)
        return result
    if not isinstance(data, dict):
        return {}
    # Sanitize legacy data on every read so an old t.me value can never leak
    # into Leaderboard/Champions after a backup restore.
    cleaned = {}
    for channel, value in data.items():
        handle = _clean_x_handle(value)
        if handle:
            cleaned[str(channel).lstrip("@").lower()] = handle
    if cleaned != data:
        save_json(X_ACCOUNTS_FILE, cleaned)
    return cleaned
def save_x_accounts(x):      save_json(X_ACCOUNTS_FILE, x)

def _get_channel_x_handle(channel):
    """Return X/Twitter handle for a channel (without @), or '' if not set."""
    return _clean_x_handle(load_x_accounts().get(channel.lstrip("@").lower(), ""))
def load_special_channels():
    raw = load_json(SPECIAL_CHANNELS_FILE, [])
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(
        str(ch).lstrip("@").strip().lower() for ch in raw if str(ch).strip()
    ))
def save_special_channels(channels):
    save_json(SPECIAL_CHANNELS_FILE, list(dict.fromkeys(channels)))
def load_subscriptions():    return load_json(SUBSCRIPTIONS_FILE, [])
def save_subscriptions(s):   save_json(SUBSCRIPTIONS_FILE, s)
def load_channel_subs():     return load_json(CHANNEL_SUBS_FILE, {})
def save_channel_subs(s):    save_json(CHANNEL_SUBS_FILE, s)
def load_momentum_sent():    return load_json(MOMENTUM_SENT_FILE, {})
def save_momentum_sent(s):   save_json(MOMENTUM_SENT_FILE, s)
def load_momentum_reports():  return load_json(MOMENTUM_REPORTS_FILE, {})
def save_momentum_reports(d): save_json(MOMENTUM_REPORTS_FILE, d)
def save_momentum_snapshot(channel, x_val, rows):
    return  # Momentum Active feature permanently removed
    """Freeze the exact call list used in a MOMENTUM ACTIVE post so the
    X-Ray Report button always shows the same records later."""
    try:
        d = load_momentum_reports()
        d[f"{channel.lower()}_{int(x_val)}"] = {
            "channel": channel.lower(), "x": int(x_val),
            "saved_at": datetime.utcnow().isoformat(), "rows": rows,
        }
        save_momentum_reports(d)
    except Exception as e:
        logger.warning(f"save_momentum_snapshot failed: {e}")
def load_xray_archive():   return load_json(XRAY_ARCHIVE_FILE, {})
def save_xray_archive(d):  save_json(XRAY_ARCHIVE_FILE, d)

def load_call_archive():   return load_json(CALL_ARCHIVE_FILE, {})
def save_call_archive(d):  save_json(CALL_ARCHIVE_FILE, d)

def record_xray_milestone(channel, x_val, row):
    return  # X-Ray feature permanently removed
    """Permanently remember every milestone a channel hit, so the X-Ray Report
    button always has records even if tracked_calls / sent_milestones are
    cleaned or restored from an older backup."""
    try:
        if not channel or not row:
            return
        d   = load_xray_archive()
        key = f"{channel.lower()}_{int(x_val)}"
        rows = d.get(key) or []
        ca   = (row.get("ca") or "").lower()
        for existing in rows:
            if (existing.get("ca") or "").lower() == ca and ca:
                existing.update({k: v for k, v in row.items() if v})
                break
        else:
            rows.append(row)
        d[key] = rows[-60:]
        save_xray_archive(d)
    except Exception as e:
        logger.warning(f"record_xray_milestone failed: {e}")

def get_xray_archive_rows(channel, x_val):
    try:
        return list((load_xray_archive().get(f"{channel.lower()}_{int(x_val)}") or []))
    except Exception:
        return []

def get_momentum_snapshot(channel, x_val):
    try:
        return (load_momentum_reports().get(f"{channel.lower()}_{int(x_val)}") or {}).get("rows") or []
    except Exception:
        return []
def load_channel_points():
    """Load points. Points can NEVER be negative — any legacy negative value
    stored on disk is clamped to 0 on read."""
    data = load_json(CHANNEL_POINTS_FILE, {})
    try:
        for _k, _v in data.items():
            if isinstance(_v, dict) and _v.get("points", 0) < 0:
                _v["points"] = 0
    except Exception:
        pass
    return data
def save_channel_points(p):
    try:
        for _k, _v in p.items():
            if isinstance(_v, dict) and _v.get("points", 0) < 0:
                _v["points"] = 0
    except Exception:
        pass
    save_json(CHANNEL_POINTS_FILE, p)
def load_trending_blacklist():   return set(load_json(TRENDING_BLACKLIST_FILE, []))
def save_trending_blacklist(s):  save_json(TRENDING_BLACKLIST_FILE, sorted(s))
# Global CA ban: separate from per-tracked-call "frozen" flag. /freezecall CA
# (no @channel) writes here too, and _process_new_call checks this BEFORE a
# new tracked_calls entry is ever created — so a banned CA is refused for
# every caller/channel, not just the ones that already had a tracked entry
# at the moment /freezecall ran. Per-channel freeze (/freezecall @ch CA)
# deliberately does NOT touch this — that stays scoped to one channel.
def load_global_ca_bans():   return set(x.lower() for x in load_json(GLOBAL_CA_BAN_FILE, []))
def save_global_ca_bans(s):  save_json(GLOBAL_CA_BAN_FILE, sorted(s))
def load_pinned_trending():      return load_json(PINNED_TRENDING_FILE, {})
def save_pinned_trending(d):     save_json(PINNED_TRENDING_FILE, d)
def load_trending_cache():    return load_json(TRENDING_CACHE_FILE, {})
def save_trending_cache(d):   save_json(TRENDING_CACHE_FILE, d)
def load_trending2_cache():   return load_json(TRENDING2_CACHE_FILE, {})
def save_trending2_cache(d):  save_json(TRENDING2_CACHE_FILE, d)
def load_kol_owners():           return load_json(KOL_OWNERS_FILE, {})
def save_kol_owners(o):          save_json(KOL_OWNERS_FILE, o)

# Single asyncio lock to protect channel_points.json from concurrent read-modify-write
import asyncio as _asyncio
_points_lock = _asyncio.Lock()
def load_linked_channels():  return load_json(LINKED_CHANNELS_FILE, {})
def save_linked_channels(l): save_json(LINKED_CHANNELS_FILE, l)
def load_pending():          return load_json(PENDING_REQUESTS_FILE, {})
def save_pending(p):         save_json(PENDING_REQUESTS_FILE, p)
def load_admins():           return load_json(ADMINS_FILE, [])
def save_admins(a):          save_json(ADMINS_FILE, a)

def is_admin_or_owner(uid):
    return uid in OWNER_IDS or uid in load_admins()

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not is_admin_or_owner(update.effective_user.id):
            await update.message.reply_text("⛔ Admin/Owner only."); return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def _inc_channel_post_count():
    c = load_config()
    c["channel_post_count"] = c.get("channel_post_count", 0) + 1
    save_config(c)
    return c["channel_post_count"]

def cfg_get(key, default=None): return load_config().get(key, default)
def cfg_set(key, value):
    c = load_config(); c[key] = value; save_config(c)

# ─── Telegram Backup / Restore (Railway pe volume nahi — data Telegram pe save) ─
_ALL_DATA_FILES = None   # lazily built after all path vars defined

def _get_data_files():
    global _ALL_DATA_FILES
    if _ALL_DATA_FILES is None:
        _ALL_DATA_FILES = {
            "channels":        CHANNELS_FILE,
            "config":          BOT_CONFIG_FILE,
            "tracked":         TRACKED_FILE,
            "milestones":      MILESTONES_FILE,
            "seen":            SEEN_FILE,
            "milestone_posts": MILESTONE_POSTS_FILE,
            "channel_points":  CHANNEL_POINTS_FILE,
            "kol_owners":      KOL_OWNERS_FILE,
            "trending_cache":  TRENDING_CACHE_FILE,
            "trending2_cache": TRENDING2_CACHE_FILE,
            "trending_bl":     TRENDING_BLACKLIST_FILE,
            "global_ca_bans":  GLOBAL_CA_BAN_FILE,
            "subscriptions":   SUBSCRIPTIONS_FILE,
            "channel_subs":    CHANNEL_SUBS_FILE,
            "admins":          ADMINS_FILE,
            "x_accounts":      X_ACCOUNTS_FILE,
            "special_channels": SPECIAL_CHANNELS_FILE,
            "linked_channels": LINKED_CHANNELS_FILE,
            "pending":         PENDING_REQUESTS_FILE,
            "momentum_sent":   MOMENTUM_SENT_FILE,
            "momentum_reports": MOMENTUM_REPORTS_FILE,
            "xray_archive":    XRAY_ARCHIVE_FILE,
            # ← users.json was previously missing from backup — members got wiped on /restorenow
            "users":           USERS_FILE,
            "buy_bots":        BUYBOTS_FILE,
            # Added 2026-10-02 (Railway migration): these were NOT in the backup, so a
            # move to a new host lost /history and the per-X timestamps.
            "call_archive":    CALL_ARCHIVE_FILE,
            "milestone_times": MILESTONE_TIMES_FILE,
            "ps_projects":     PS_PROJECTS_FILE,
            "xalert_details":  XALERT_DETAILS_FILE,
            "priority_channels": PRIORITY_CHANNELS_FILE,
            "removed_channels":  REMOVED_CHANNELS_FILE,
            "hot_streak_state":  HOT_STREAK_STATE_FILE,
            "pending_media":     PENDING_MEDIA_FILE,
            "broadcasts":        BROADCASTS_FILE,
        }
    return _ALL_DATA_FILES

async def backup_data_to_telegram(bot):
    """Sab JSON files pack karo aur BACKUP_CHAT_ID pe document bhejo."""
    if not BACKUP_CHAT_ID:
        logger.warning("backup_data_to_telegram: BACKUP_CHAT_ID not set, skipping.")
        return
    backup = {"_timestamp": datetime.utcnow().isoformat()}
    for key, filepath in _get_data_files().items():
        backup[key] = load_json(filepath, None)
    raw = json.dumps(backup, ensure_ascii=False, indent=2).encode()
    doc = io.BytesIO(raw)
    doc.name = "bot_backup.json"
    try:
        await bot.send_document(
            BACKUP_CHAT_ID,
            document=doc,
            caption=f"#{BACKUP_TAG} {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        )
        logger.info("✅ Telegram backup sent.")
    except Exception as e:
        logger.warning(f"Telegram backup failed: {e}")

async def restore_data_from_telegram(userbot_cl):
    """Telethon se latest backup dhundo aur files restore karo."""
    if not userbot_cl:
        return False
    try:
        # Bot ne backup OWNER_ID (user's Telegram) pe bheja.
        # Userbot (owner's account) ke liye woh messages BOT ke chat mein dikhte hain.
        # Agar custom BACKUP_CHAT_ID set hai to wahi use karo.
        if BACKUP_CHAT_ID and BACKUP_CHAT_ID != OWNER_ID:
            peers_to_try = [BACKUP_CHAT_ID]
        else:
            # Try both: bot user ID aur owner's Saved Messages / direct search
            peers_to_try = [p for p in [BOT_USER_ID, OWNER_ID] if p]
            # FIX (2026-10-11, leaderboard/champions vanish after redeploy):
            # restore failed on many deploys with "Could not find the input
            # entity for PeerUser(<bot id>)" — a numeric user id can't be
            # resolved when the Telethon session has no cached access_hash for
            # the bot yet. The bot then started with EMPTY data (no tracked
            # calls / points / milestones) so leaderboard + champions went
            # blank. A @username is always resolvable, so try it first.
            try:
                _bot_uname = BOT_LINK.rstrip("/").split("/")[-1].strip()
            except Exception:
                _bot_uname = ""
            if _bot_uname:
                peers_to_try = [_bot_uname] + peers_to_try

        backup_msg = None
        tried_peer = None
        for peer in peers_to_try:
            try:
                # search param chhod do — last 200 messages manually scan karo
                msgs = await userbot_cl.get_messages(peer, limit=200)
                for m in msgs:
                    caption = getattr(m, "text", "") or ""
                    if m.document and BACKUP_TAG in caption:
                        backup_msg = m
                        tried_peer = peer
                        break
                if backup_msg:
                    break
                logger.info(f"restore: peer {peer} mein backup nahi mila ({len(msgs)} msgs check kiye)")
            except Exception as pe:
                logger.warning(f"restore: peer {peer} try karte waqt error: {pe}")

        if not backup_msg:
            logger.info("ℹ️ Kisi bhi peer mein backup nahi mila.")
            return False

        data_bytes = await userbot_cl.download_media(backup_msg, bytes)
        backup = json.loads(data_bytes.decode())
        restored = 0
        for key, filepath in _get_data_files().items():
            if key in backup and backup[key] is not None:
                save_json(filepath, backup[key])
                restored += 1
        logger.info(f"✅ {restored} files Telegram backup se restore ho gaye (peer={tried_peer}, ts={backup.get('_timestamp', '?')})")
        # NOTE: this used to force-clear config["dropped_call_template"] on every
        # restore so a stale/broken template from an old backup couldn't stick
        # around. But restore_data_from_telegram() runs on EVERY bot startup —
        # so it was also wiping the owner's current, intentionally-set custom
        # template (with their own emoji IDs) back to DEFAULT_DROPPED_TEMPLATE
        # on every single restart/redeploy. Owner-set templates should persist
        # like any other setting, so this forced clear is removed.
        # In-memory globals reload
        global seen_message_ids, tracked_calls, sent_milestones, milestone_posts
        seen_message_ids = _load_seen()
        tracked_calls    = _load_tracked()
        sent_milestones  = _load_milestones()
        milestone_posts  = _load_milestone_posts()
        # Re-seed known members so they're never lost even if backup was old
        _seed_known_members()
        return True
    except Exception as e:
        logger.warning(f"Telegram restore failed: {e}")
        return False

async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic backup — har 5 min mein chalti hai (Railway restarts se protect karta hai)."""
    await backup_data_to_telegram(context.bot)

def add_user(uid: int, username: str = None, name: str = None):
    d = load_users_dict()
    key = str(uid)
    changed = False
    if key not in d:
        d[key] = {"id": uid, "username": username, "name": name}
        changed = True
    elif username and d[key].get("username") != username:
        d[key]["username"] = username
        d[key]["name"] = name
        changed = True
    if changed:
        save_users_dict(d)

# ─── Known-member seed ────────────────────────────────────────────────────────
# Usernames of members who were in the bot before a data loss event.
# They get stable placeholder IDs (always negative, never conflict with real IDs).
# When they interact with the bot again, add_user() will upsert their real Telegram ID.
_KNOWN_MEMBERS = []  # cleared — real users counted only when they interact with bot

def _username_placeholder_id(username: str) -> int:
    """Return a stable negative placeholder ID for a username-only member entry."""
    import hashlib
    h = int(hashlib.md5(username.lower().encode()).hexdigest(), 16)
    return -(h % (10 ** 9))

def _seed_known_members():
    """Add known members to users.json using stable placeholder IDs.
    Safe to call multiple times — skips entries that already have a real (positive) ID."""
    d = load_users_dict()
    changed = False
    for uname in _KNOWN_MEMBERS:
        pid = _username_placeholder_id(uname)
        pid_key = str(pid)
        # Check if this username already exists under any real (positive) ID
        already_real = any(
            v.get("username", "").lower() == uname.lower() and int(k) > 0
            for k, v in d.items()
        )
        if already_real:
            continue  # real entry exists — don't overwrite with placeholder
        if pid_key not in d:
            d[pid_key] = {"id": pid, "username": uname, "name": uname, "_placeholder": True}
            changed = True
    if changed:
        save_users_dict(d)

def get_milestones():
    cfg = load_config()
    stored = cfg.get("custom_milestones")
    if stored:
        try: base = sorted(set(int(x) for x in stored))
        except Exception: base = list(X_MILESTONES)
    else:
        base = list(X_MILESTONES)
    # Also include X values for which milestone_media is set, so those X levels are always tracked
    try:
        media_keys = cfg.get("milestone_media", {}).keys()
        extra = [int(k) for k in media_keys if k.lstrip('-').isdigit() and int(k) <= MAX_MILESTONE]
        if extra:
            base = sorted(set(base) | set(extra))
    except Exception:
        pass
    return base


# ─── Milestone media gating ───────────────────────────────────────────────────
# Rule: an X milestone is only PUBLIC (channel post, leaderboard, champions,
# trending) when media has been uploaded for that X level (or a global media
# exists). Milestones without media are still recorded internally, so /record
# and history always show the true X.
def media_milestone_levels():
    """Return set of X levels that have media configured, or None if a global
    media is set (meaning every level is publishable)."""
    try:
        cfg = load_config()
        mm  = cfg.get("milestone_media", {}) or {}
    except Exception:
        return None
    g = mm.get("global")
    if isinstance(g, dict) and g.get("file_id"):
        return None
    levels = set()
    for k, v in mm.items():
        if str(k).lstrip('-').isdigit() and isinstance(v, dict) and v.get("file_id"):
            levels.add(int(k))
    return levels

def milestone_has_media(x) -> bool:
    """True only if media is configured for this X level (or a global media
    exists). Milestones without media are recorded internally but NOT posted."""
    levels = media_milestone_levels()
    if levels is None:
        return True
    try:
        return int(x) in levels
    except Exception:
        return False


def display_x(x):
    """Cap an X value down to the highest milestone that has media configured.
    Used for leaderboard / champions / trending so un-published milestones
    never appear publicly."""
    try: x = int(x)
    except Exception: return 0
    # Media is no longer required for public visibility — leaderboard /
    # champions / trending always show the true X instantly.
    return x

PENDING_MEDIA_FILE = _dp("pending_media_alerts.json")

def _load_pending_media():
    try:
        with open(PENDING_MEDIA_FILE) as f:
            return {k: set(v) for k, v in json.load(f).items()}
    except Exception:
        return {}

def _save_pending_media():
    try:
        with open(PENDING_MEDIA_FILE, "w") as f:
            json.dump({k: sorted(v) for k, v in pending_media_alerts.items()}, f)
    except Exception:
        pass

pending_media_alerts = _load_pending_media()

def _add_pending_media(call_key, x_val):
    """Remember a milestone that was recorded but not posted (no media yet)."""
    if not call_key:
        return
    pending_media_alerts.setdefault(call_key, set()).add(int(x_val))
    _save_pending_media()

def _clear_pending_media(level=None):
    """Owner ne naya media set kiya → purana backlog kabhi post na ho.
    Sirf naye records par alert jayega. level=None → poora backlog saaf."""
    try:
        removed = 0
        dropped = []          # (call_key, x) — inhe "already sent" mark karna hai
        if level is None:
            for k, s in list(pending_media_alerts.items()):
                for x in list(s or []):
                    dropped.append((k, int(x))); removed += 1
            pending_media_alerts.clear()
        else:
            lv = int(level)
            for k in list(pending_media_alerts.keys()):
                s = pending_media_alerts.get(k) or set()
                if lv in s:
                    s.discard(lv); dropped.append((k, lv)); removed += 1
                if not s:
                    pending_media_alerts.pop(k, None)
                else:
                    pending_media_alerts[k] = s
        _save_pending_media()
        # Sirf pending list se hatana kaafi nahi — warna monitoring job wahi
        # purana milestone dobara detect kar ke post kar deta. Isliye unhe
        # "sent" mark kar dete hain: record rehta hai, alert nahi jata.
        if dropped:
            try:
                for k, x in dropped:
                    sent_milestones[k].add(int(x))
                _save_milestones()
            except Exception:
                pass
            logger.info(f"🧹 Cleared {removed} old held X alert(s) after media set "
                        f"(level={level if level is not None else 'all'})")
        return removed
    except Exception as e:
        logger.warning(f"_clear_pending_media: {e}")
        return 0

# ─── Tracked data ─────────────────────────────────────────────────────────────
def _load_tracked():
    try:
        with open(TRACKED_FILE) as f: return json.load(f)
    except Exception: return {}

def _save_tracked():
    try:
        with open(TRACKED_FILE, "w") as f: json.dump(tracked_calls, f, ensure_ascii=False)
    except Exception: pass

def _load_milestones():
    try:
        with open(MILESTONES_FILE) as f:
            return defaultdict(set, {k: set(v) for k, v in json.load(f).items()})
    except Exception: return defaultdict(set)

def _save_milestones():
    try:
        with open(MILESTONES_FILE, "w") as f:
            json.dump({k: list(v) for k, v in sent_milestones.items()}, f)
    except Exception: pass

def _load_seen():
    try:
        with open(SEEN_FILE) as f:
            return defaultdict(set, {k: set(v) for k, v in json.load(f).items()})
    except Exception: return defaultdict(set)

_SEEN_MAX_PER_CHANNEL = 600   # keep memory + backup payload bounded on Railway

def _prune_seen():
    """Keep only the newest N ids per channel. Without this the set grows
    forever and the container eventually gets OOM-killed by Railway."""
    for ch, ids in list(seen_message_ids.items()):
        if len(ids) <= _SEEN_MAX_PER_CHANNEL:
            continue
        try:
            keep = sorted(ids, key=lambda x: int(x))[-_SEEN_MAX_PER_CHANNEL:]
        except Exception:
            keep = list(ids)[-_SEEN_MAX_PER_CHANNEL:]
        seen_message_ids[ch] = set(keep)

def _save_seen():
    try:
        _prune_seen()
        with open(SEEN_FILE, "w") as f:
            json.dump({k: list(v) for k, v in seen_message_ids.items()}, f)
    except Exception: pass

def _load_milestone_posts():
    try:
        with open(MILESTONE_POSTS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_milestone_posts():
    try:
        with open(MILESTONE_POSTS_FILE, "w") as f:
            json.dump(milestone_posts, f)
    except Exception:
        pass

seen_message_ids = _load_seen()
tracked_calls    = _load_tracked()
sent_milestones  = _load_milestones()
milestone_posts  = _load_milestone_posts()   # {call_key: {str(x_val): wizard_scan_post_id}}

def _load_milestone_times():
    try:
        with open(MILESTONE_TIMES_FILE) as f: return json.load(f)
    except Exception: return {}

def _save_milestone_times():
    try:
        with open(MILESTONE_TIMES_FILE, "w") as f: json.dump(milestone_times, f)
    except Exception: pass

milestone_times  = _load_milestone_times()

# ═══════════════════════════════════════════════════════════════════════════
# FAST-SAVE LAYER (latency fix)
# Pehle har naye call / milestone par poori JSON file event-loop par likhi
# jati thi (seen_message_ids = sekron channels x 600 ids, tracked_calls =
# hazaron tokens). Wo blocking write hi asal wajah thi ke scan_job hard-timeout
# hota tha aur monitoring_job "max instances reached" par skip hota raha —
# yani calls late track hoti thin aur X alerts der se jati thin.
# Ab save sirf "dirty" mark hota hai aur ek background thread har 1-2s me
# actual disk write karta hai. App logic bilkul same, sirf blocking khatam.
# ═══════════════════════════════════════════════════════════════════════════
import threading as _threading

_ORIG_SAVERS = {
    "tracked":         _save_tracked,
    "seen":            _save_seen,
    "milestones":      _save_milestones,
    "milestone_posts": _save_milestone_posts,
    "milestone_times": _save_milestone_times,
}
_SAVE_DIRTY   = set()
_SAVE_LOCK    = _threading.Lock()
_SAVE_INTERVAL = float(os.environ.get("DISK_FLUSH_SECONDS", "2") or 2)

def _mark_dirty(name):
    with _SAVE_LOCK:
        _SAVE_DIRTY.add(name)

def _flush_saves():
    with _SAVE_LOCK:
        names = list(_SAVE_DIRTY)
        _SAVE_DIRTY.clear()
    for n in names:
        fn = _ORIG_SAVERS.get(n)
        if not fn:
            continue
        for _try in range(3):
            try:
                fn()
                break
            except RuntimeError:
                # dict mutated while dumping — retry a moment later
                time.sleep(0.05)
            except Exception:
                break

def _save_flusher_loop():
    while True:
        time.sleep(_SAVE_INTERVAL)
        try:
            _flush_saves()
        except Exception:
            pass

_threading.Thread(target=_save_flusher_loop, daemon=True,
                  name="wizard-disk-flusher").start()

def _save_tracked():         _mark_dirty("tracked")
def _save_seen():            _mark_dirty("seen")
def _save_milestones():      _mark_dirty("milestones")
def _save_milestone_posts(): _mark_dirty("milestone_posts")
def _save_milestone_times(): _mark_dirty("milestone_times")

def flush_all_to_disk():
    """Backup / shutdown se pehle sab kuch foran disk par likh do."""
    with _SAVE_LOCK:
        _SAVE_DIRTY.update(_ORIG_SAVERS.keys())
    _flush_saves()

def _record_milestone_time(call_key, ms):
    """Remember WHEN an X was hit. Leaderboard / Trending-KOL lists use this so an
    older call that pumps today still counts for today's ranking."""
    try:
        milestone_times.setdefault(str(call_key), {})[str(int(ms))] = datetime.utcnow().isoformat()
        _save_milestone_times()
    except Exception:
        pass

def _milestones_since(call_key, reset_dt):
    """Milestones of a call that were reached AFTER reset_dt.
    Milestones without a recorded timestamp (legacy data) are treated as old."""
    ms_all = list(sent_milestones.get(call_key, set()))
    if not reset_dt:
        return ms_all
    times = milestone_times.get(str(call_key), {})
    out = []
    for m in ms_all:
        ts = times.get(str(int(m)))
        if not ts:
            continue
        try:
            if datetime.fromisoformat(ts) >= reset_dt:
                out.append(m)
        except Exception:
            continue
    return out
owner_edit_state = {}
_userbot_login   = {}
userbot_client   = None
_userbot_edit_lock = asyncio.Lock()  # serializes userbot edit_message calls across
                                      # leaderboard/champions/trending/dropped-alert jobs
                                      # so they queue one-at-a-time instead of firing
                                      # simultaneously and tripping Telegram's flood limit

async def _locked_userbot_edit(*args, **kwargs):
    """Same as userbot_client.edit_message(...) but serialized via _userbot_edit_lock."""
    async with _userbot_edit_lock:
        return await userbot_client.edit_message(*args, **kwargs)
_userbot_init_lock = None
_login_client    = None
_bot_ref         = None  # set on startup; used by realtime message handler

# ─── Points System (Champion KOL List) ────────────────────────────────────────
# Points awarded per milestone tier (for champion kols list only)
POINT_TIERS = [
    (250, 90),   # Slightly harder than before
    (100, 65),
    (50,  45),
    (25,  30),
    (10,  18),
    (5,    9),
    (2,    4),
]
POINTS_FOR_CHAMPION = 100  # Points needed to appear in champion kols list
POINTS_DEDUCT_FAILED = 10  # Deducted if call never hits 2X
CALL_FAIL_HOURS = 48       # Deduct if a call has not reached 2X within 48 hours

def get_point_tier_reward(x_val):
    """Return (tier_threshold, points) for the given x_val, or (0, 0)."""
    for threshold, pts in POINT_TIERS:
        if x_val >= threshold:
            return threshold, pts
    return 0, 0

def get_channel_points(channel):
    """Return total points for a channel (never negative)."""
    pts = load_channel_points()
    return max(0, pts.get(channel.lower(), {}).get("points", 0))

def _is_deleted_blocked(call) -> bool:
    """A call whose original Telegram post was deleted by the caller is BLOCKED
    (no X alert, no points, not counted in Champions/Leaderboard/Trending)
    until the owner approves it with the "Yes" button (delete_decision == "yes").
    The call still stays in the channel record, tagged as deleted."""
    try:
        return bool(call) and bool(call.get("post_deleted")) and call.get("delete_decision") != "yes"
    except Exception:
        return False

async def award_points_for_milestone(channel, call_key, x_val):
    """Award incremental points when a milestone is hit. Only awards once per tier per call.
    Uses asyncio lock to prevent concurrent read-modify-write corruption.
    Skips call_keys that existed before the last champions/leaderboard reset."""
    tier_threshold, tier_pts = get_point_tier_reward(x_val)
    if tier_pts <= 0:
        return 0
    # Deleted-post calls earn no points unless the owner approved them.
    if _is_deleted_blocked(tracked_calls.get(call_key)):
        return 0
    # Skip point awards only for call_keys excluded by champion reset.
    # Leaderboard reset (lb_excluded_call_keys) must NOT block champion points —
    # they are separate systems with independent reset cycles.
    _cfg_excl = load_config()
    champ_snap = _cfg_excl.get("champion_milestone_snapshot", {})
    champ_excl = set(_cfg_excl.get("champion_excluded_call_keys", []))
    if call_key in champ_excl:
        # Legacy blanket exclusion (pre-snapshot resets) — block entirely
        return 0
    if call_key in champ_snap:
        # Snapshot-based exclusion: only block if this milestone was already
        # present at reset time (i.e. tier_threshold <= snapshot max)
        snap_max = champ_snap[call_key]
        tier_threshold, _ = get_point_tier_reward(x_val)
        if tier_threshold <= snap_max:
            return 0
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        entry = pts_data[key]
        awarded = entry.get("awarded_tiers", {})
        call_awarded = awarded.get(call_key, [])
        if tier_threshold in call_awarded:
            return 0  # Already awarded this tier for this call
        # A late recovery returns the 10 points deducted at 48 hours. This is
        # done atomically with the milestone reward, so restarts cannot pay it twice.
        deducted = entry.get("deducted_calls", [])
        amounts  = entry.get("deducted_amounts", {})
        restored = 0
        if tier_threshold >= 2 and call_key in deducted:
            deducted.remove(call_key)
            entry["deducted_calls"] = deducted
            # Restore only what was ACTUALLY taken (floor-at-zero may have taken less)
            back = amounts.pop(call_key, POINTS_DEDUCT_FAILED)
            entry["deducted_amounts"] = amounts
            entry["points"] = max(0, entry.get("points", 0) + back)
            restored = back
        # Points increment = current tier - highest previously awarded tier for this call
        prev_pts = sum(tp for tt, tp in POINT_TIERS if tt in call_awarded)
        new_pts = tier_pts - prev_pts
        if new_pts <= 0:
            if restored:
                pts_data[key] = entry
                save_channel_points(pts_data)
            return 0
        entry["points"] = max(0, entry.get("points", 0) + new_pts)
        call_awarded.append(tier_threshold)
        awarded[call_key] = call_awarded
        entry["awarded_tiers"] = awarded
        pts_data[key] = entry
        save_channel_points(pts_data)
    return new_pts + restored

async def deduct_points_for_failed_call(channel, call_key):
    """Deduct 10 points if a call never hit 2X. Only deducts once per call.
    Points can never go below 0."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        entry = pts_data[key]
        deducted = entry.get("deducted_calls", [])
        if call_key in deducted:
            return  # Already deducted
        cur  = max(0, entry.get("points", 0))
        take = min(POINTS_DEDUCT_FAILED, cur)   # never go negative
        entry["points"] = cur - take
        amounts = entry.get("deducted_amounts", {})
        amounts[call_key] = take
        entry["deducted_amounts"] = amounts
        deducted.append(call_key)
        entry["deducted_calls"] = deducted
        pts_data[key] = entry
        save_channel_points(pts_data)

async def deduct_points_for_deleted_post(channel, call_key):
    """Deduct 10 points once when the original KOL post is deleted.
    Points can never go below 0."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        entry = pts_data[key]
        deleted = entry.get("deleted_post_calls", [])
        if call_key in deleted:
            return False
        cur = max(0, entry.get("points", 0))
        take = min(10, cur)
        entry["points"] = cur - take
        deleted.append(call_key)
        entry["deleted_post_calls"] = deleted
        _amts = entry.get("deleted_post_amounts", {})
        _amts[call_key] = take
        entry["deleted_post_amounts"] = _amts
        pts_data[key] = entry
        save_channel_points(pts_data)
        return True

async def restore_points_for_deleted_post(channel, call_key):
    """Owner approved a deleted-post call ("Yes"): give back the points that were
    taken when the post was deleted (only the amount actually taken)."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        entry = pts_data.get(key)
        if not entry:
            return 0
        deleted = entry.get("deleted_post_calls", [])
        if call_key not in deleted:
            return 0
        deleted.remove(call_key)
        entry["deleted_post_calls"] = deleted
        back = int((entry.get("deleted_post_amounts", {}) or {}).pop(call_key, 0) or 0)
        entry["points"] = max(0, entry.get("points", 0)) + back
        pts_data[key] = entry
        save_channel_points(pts_data)
        return back

async def give_manual_points(channel, amount):
    """Manually give points to a channel (owner command)."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        pts_data[key]["points"] = max(0, pts_data[key].get("points", 0) + amount)
        total = pts_data[key]["points"]
        save_channel_points(pts_data)
    return total

# ─── Utilities ────────────────────────────────────────────────────────────────
def safe_format(template, **kwargs):
    # User-facing templates should never render a fully lowercase @username.
    # Keep stored channel keys lowercase; capitalize only the display value.
    if "channel" in kwargs:
        kwargs["channel"] = _display_handle(kwargs["channel"])
    lk = {k.lower(): v for k, v in kwargs.items()}
    return re.sub(r'\{([A-Za-z_][A-Za-z0-9_]*)\}',
                  lambda m: str(lk[m.group(1).lower()]) if m.group(1).lower() in lk else m.group(0),
                  template)

def fmt_mc(value):
    """Format market cap without a currency sign, e.g. 12.5K MC or 3.45M MC."""
    if not value: return "N/A"
    if value >= 1_000_000_000: return f"{value/1_000_000_000:.2f}B MC"
    elif value >= 1_000_000:   return f"{value/1_000_000:.2f}M MC"
    elif value >= 1_000:       return f"{value/1_000:.1f}K MC"
    return f"{value:.0f} MC"

def _mc_no_label(s):
    """Strip the trailing ' MC' label off an already fmt_mc()-formatted string,
    e.g. '12.1K MC' -> '12.1K'. Used in spots where the surrounding template
    text already says 'MC' on its own (dropped-call header, x-alert entry/
    current, channel history), so the number isn't doubled up with 'MC MC'."""
    if not isinstance(s, str):
        return s
    if s.endswith(" MC"):
        return s[:-3]
    return s

def parse_mc_string(s):
    """Parse MC strings like '5K', '$5K', '1.5M', '50000' into a float.
    Returns 0.0 if parsing fails."""
    if not s: return 0.0
    s = s.strip().lstrip("$").strip().upper()
    try:
        if   s.endswith("B"): return float(s[:-1]) * 1_000_000_000
        elif s.endswith("M"): return float(s[:-1]) * 1_000_000
        elif s.endswith("K"): return float(s[:-1]) * 1_000
        else:                 return float(s)
    except Exception: return 0.0

# ── Entry MC straight from the KOL's own post ────────────────────────────────
# Most KOL calls print the market cap ("MC: $15.4K", "Market Cap 15k", "💰 $15K").
# Using that value as our entry MC means our X multiple matches the KOL's real
# entry even if DexScreener indexing or our alert is a few seconds/minutes late.
_MC_TEXT_PATTERNS = [
    r"(?:market\s*cap|mkt\s*cap|mcap|m\.?c\.?)\s*[:\-=~@]?\s*\$?\s*([0-9][0-9,.]*\s*[kKmMbB]?)",
    r"\$?\s*([0-9][0-9,.]*\s*[kKmMbB])\s*(?:mc|mcap|market\s*cap|mkt\s*cap)\b",
    # "MC 200K" / "CAP: $200k" / "MC @ 200k"
    r"\b(?:mc|mcap|cap)\b\s*[:\-=~@]?\s*\$?\s*([0-9][0-9,.]*\s*[kKmMbB])",
    # "called at 200k", "entry 200k", "aped at 200k", "in at 200k"
    r"(?:called|call|entry|enter(?:ed)?|ape[d]?|bought|buy|in)\s*(?:at|@|:)?\s*\$?\s*([0-9][0-9,.]*\s*[kKmMbB])\b",
    # NOTE: a bare "@ 200k" / "at 200k" catch-all pattern used to live here.
    # It matched ANY number following "at"/"@" anywhere in the post — stop-loss
    # levels, take-profit targets, follower counts, unrelated numbers — not
    # just the market cap. That was the source of "called at 40K, bot tracked
    # at 5K" (grabbed an unrelated "5K" from elsewhere in the message) which
    # then made normal 2-3X moves compute as absurd 10000X/200M alerts. Removed
    # — patterns above already cover every legitimate "MC:"/"called at"/"entry"
    # phrasing; anything not caught by those isn't safely attributable to MC.
]

def extract_mc_from_text(text: str) -> float:
    """Return the market cap written inside a KOL post, or 0.0 if none found."""
    if not text:
        return 0.0
    # FIX (2026-09-25, "$LABRAT called at 40K MC, bot tracked at 8.7K"
    # incident): the caller's own post read "💰 **Market Cap:** $47,584.09" —
    # a completely normal, parseable MC line — but Telegram markdown's bold
    # markers sit literally between "Cap:" and the "$" ("Cap:**  $47,584.09"),
    # and none of the _MC_TEXT_PATTERNS regexes tolerate "**" in that gap. The
    # whole extraction silently returned "found NOTHING", so _reconcile_entry_mc
    # had no hint to trust and fell back to a live DexScreener quote that was
    # still catching up on this brand-new Robinhood-chain pool (8.7K instead
    # of the real ~47.6K) — the caller's real, correctly-formatted entry was
    # simply thrown away by formatting, not a suspicious/lowball value. Bold
    # (**text**), italic (_text_) and inline-code (`text`) markers add zero
    # information for MC purposes, so strip them before matching.
    text = re.sub(r"[*_`]+", "", text)
    for pat in _MC_TEXT_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = (m.group(1) or "").replace(",", "").replace(" ", "")
            val = parse_mc_string(raw)
            # sanity window: ignore junk like "MC: 5" or absurd values
            if 500 <= val <= 100_000_000_000:
                return val
    return 0.0

# Tolerance band for trusting the MC written in a KOL post.
# BUG FIX (2026-09-14, cheat-vector report): this was 5.0 — a caller could
# write an MC up to 5x LOWER than the real live chart and the bot would lock
# it in as permanently "confirmed", no verification ever run again. That is a
# direct cheat: post a fake-low MC, every later update then reads as an
# inflated X/trending score that never happened. Tightened to a much smaller
# band — genuine indexing-lag drift is normally well under this, a deliberate
# lowball is not.
_MC_HINT_TOLERANCE = float(os.environ.get("MC_HINT_TOLERANCE", "1.5") or 1.5)
# How long after a call a live chart quote may still be treated as "the entry".
# Beyond this the token has already moved, so a KOL-stated MC (when present)
# always wins — otherwise a 2-3 min late quote logs 90K for a 200K call.
ENTRY_MC_FRESH_SECONDS = float(os.environ.get("ENTRY_MC_FRESH_SECONDS", "20") or 20)

def _reconcile_entry_mc(hint_mc: float, live_mc: float, live_is_fdv: bool = False):
    """Pick the entry MC. Returns (mc, source) where source is 'post',
    'post_unconfirmed', 'live', or 'live_fdv'.

    'post' wins outright when it is HIGHER than or equal to live — that
    removes the "called at 15K, tracked at 25K" drift, and a higher entry can
    never inflate the caller's own X multiple in their favor, so there is no
    fraud risk in trusting it immediately with no further checks.

    A hint that is LOWER than live — the "claims 5K, really trading at 300K"
    spoof case, whether from genuine indexing lag or a deliberate lowball to
    farm a fake X — is never trusted outright anymore, even within tolerance.
    It is returned as 'post_unconfirmed': used immediately so tracking isn't
    delayed, but callers must gate milestone alerts on it and run it through
    the same independent cross-check ladder used for unconfirmed live reads,
    revising upward if an independent source disagrees. This is what actually
    closes the cheat: a genuinely-early low hint gets confirmed and left
    alone; a deliberately-low hint gets caught and corrected before it can
    farm a fake X or trending slot.

    FIX 2: live_is_fdv -- when the live read only had a fully-diluted-value
    (max/total supply) available, not a real circulating marketCap, it is NOT
    trustworthy ground truth either, so it can't be used to reject the hint —
    but that does NOT make the hint itself trustworthy. It still only gets
    used provisionally, same as above.
    """
    try:
        hint_mc = float(hint_mc or 0)
        live_mc = float(live_mc or 0)
    except (TypeError, ValueError):
        return (0.0, "live")
    if hint_mc <= 0:
        # BUG FIX (2026-09-14, DOGGO/minibangcall incident): with no post hint
        # to sanity-check against, this used to return live_mc as plain "live"
        # even when live_is_fdv was True — silently defeating the whole point
        # of the FDV flag (see the note above: FDV is NOT reliable ground
        # truth). That is exactly how a real ~75K entry got locked in at an
        # FDV-derived 36.9K and looked like a fake instant 2X seconds later.
        # Tag it "live_fdv" instead so callers know this number is unconfirmed
        # and must not gate milestone alerts until an independent cross-check
        # lands.
        if live_is_fdv:
            return (live_mc, "live_fdv")
        return (live_mc, "live")
    if live_mc <= 0:
        return (hint_mc, "post")          # nothing to compare against yet
    if hint_mc >= live_mc:
        return (hint_mc, "post")          # hint higher/equal -- always trust it, no fraud risk
    if live_is_fdv:
        logger.info(f"📎 entry-MC: provisionally using post hint {fmt_mc(hint_mc)} — "
                    f"live read {fmt_mc(live_mc)} is FDV-derived so it can't "
                    f"confirm or reject the hint either way; will cross-check "
                    f"independently before trusting it fully")
        return (hint_mc, "post_unconfirmed")
    ratio = live_mc / hint_mc
    if ratio <= _MC_HINT_TOLERANCE:
        logger.info(f"📎 entry-MC: post hint {fmt_mc(hint_mc)} is {ratio:.2f}x below "
                    f"live {fmt_mc(live_mc)} — within tolerance but using "
                    f"provisionally pending independent cross-check, not blindly")
        return (hint_mc, "post_unconfirmed")
    return (live_mc, "live")              # hint suspiciously LOW → trust the live chart



# ═══════════════════════════════════════════════════════════════════════════
# RUG / FAKE-MARKETCAP PROTECTION
# A rugged token (dev pulled liquidity) can still report a huge marketCap or
# FDV on DexScreener. Without these checks the bot would proudly announce
# "1000X — token at $2B" for a coin that is actually dead at $200 liquidity.
# Every milestone alert must pass _rug_status() first.
# ═══════════════════════════════════════════════════════════════════════════
RUG_MIN_LIQUIDITY  = float(os.environ.get("RUG_MIN_LIQUIDITY",  "150"))    # USD
RUG_LIQ_DROP_RATIO = float(os.environ.get("RUG_LIQ_DROP_RATIO", "0.15"))   # vs peak liq
MAX_MC_LIQ_RATIO   = float(os.environ.get("MAX_MC_LIQ_RATIO",   "5000"))   # mc / liquidity
MAX_PLAUSIBLE_MC   = float(os.environ.get("MAX_PLAUSIBLE_MC",   "2000000000"))  # $2B
RUG_MIN_VOL_H24    = float(os.environ.get("RUG_MIN_VOL_H24",    "50"))     # USD
MC_PRICE_PUMP_GUARD = os.environ.get("MC_PRICE_PUMP_GUARD", "1").strip().lower() not in ("0", "false", "off")
# A token that is actively traded is ALIVE, regardless of how thin the single
# pool DexScreener reports happens to be. Real trading volume is the strongest
# liveness proof we have, so it overrides the pool-depth heuristics below.
LIVE_MIN_VOL_H24   = float(os.environ.get("LIVE_MIN_VOL_H24",   "500"))    # USD


def _rug_status(call, dex):
    """Return (ok: bool, reason: str).

    ok=False means: do NOT post any alert for this call on this tick.
    Truly dead tokens are permanently marked rugged+frozen so they never
    generate another alert.

    Every HARD-stop reason is logged with the raw numbers that caused it —
    this is what shows up in Railway logs when a channel's live /record
    check displays a real X but the milestone never posts to the channel,
    so the owner can see exactly why a specific tick was blocked.
    """
    try:
        liq = float(dex.get("liquidity_usd", 0) or 0)
        mc  = float(dex.get("mcap", 0) or 0)
        vol = float(dex.get("volume_h24", 0) or 0)
    except (TypeError, ValueError):
        logger.warning(f"🚫 rug-gate bad_data {call.get('symbol','?')} @{call.get('channel','?')} "
                       f"raw_dex={dex!r}")
        return (False, "bad_data")

    if call.get("rugged"):
        logger.info(f"⏸️ rug-gate already_rugged {call.get('symbol','?')} @{call.get('channel','?')} "
                    f"(frozen={call.get('frozen')}, reason={call.get('rug_reason')}) — "
                    f"use /unfreezecall {call.get('ca','')} to resume if this was a false positive")
        return (False, "already_rugged")

    # remember the healthiest liquidity we ever saw for this call
    try:
        peak_liq = float(call.get("peak_liq", 0) or 0)
    except (TypeError, ValueError):
        peak_liq = 0.0
    if liq > peak_liq:
        call["peak_liq"] = liq
        peak_liq = liq

    if mc <= 0:
        logger.warning(f"🚫 rug-gate no_mc {call.get('symbol','?')} @{call.get('channel','?')} "
                       f"liq=${liq:.0f} vol24h=${vol:.0f} — will retry next tick")
        return (False, "no_mc")
    if mc > MAX_PLAUSIBLE_MC:
        logger.warning(f"🚫 rug-gate absurd_mc {call.get('symbol','?')} @{call.get('channel','?')} "
                       f"mc=${mc:.0f} (cap ${MAX_PLAUSIBLE_MC:.0f}) liq=${liq:.0f} vol24h=${vol:.0f} "
                       f"— will retry next tick")
        return (False, "absurd_mc")

    # Alternative providers intentionally return price/MC but do not expose
    # the pair's liquidity and 24h volume in the same response shape as
    # DexScreener.  Treating their zero-filled fields as "liquidity pulled"
    # froze perfectly live calls before they could reach the milestone engine.
    # MC is still checked above; only the unavailable pool-health checks are
    # skipped for a clearly identified external-source quote.
    if dex.get("_source") and dex.get("_source") != "dexscreener":
        return (True, "external_source")

    # Liquidity pulled → rug. Permanent stop.
    if liq <= 0 or (peak_liq > 0 and liq < peak_liq * RUG_LIQ_DROP_RATIO
                    and liq < RUG_MIN_LIQUIDITY * 4):
        # LIVENESS GUARD: a real rug kills trading immediately, so liq=$0
        # (or a liq collapse) alongside strong real 24h volume can't be an
        # actual rug — it's a DexScreener data glitch/pool-swap blip on the
        # liquidity field specifically. Permanently freezing those calls was
        # exactly the bug that made real 9x/10x calls stop alerting for
        # hours: they hit one bad liq=0 tick, got frozen forever, and only
        # ever resumed if the owner manually ran /unfreezecall. Treat this
        # as a transient miss instead — it auto-retries next tick and
        # resumes on its own once the liquidity field comes back, with no
        # manual action needed and no real rug ever slipping through
        # (vol < LIVE_MIN_VOL_H24 still falls through to the permanent path
        # below, unchanged).
        # OWNER REQUIREMENT (2026-10-09): liquidity exactly $0 = RUG, even if
        # vol24h is high (24h volume is history and a rugged token still shows
        # it). Exact-zero liquidity skips the liveness guard and goes through
        # the RUG_CONFIRM_STRIKES path below, which freezes the call; the
        # cleanup job then drops it from tracking. Partial liq collapse
        # (liq>0 but tiny vs peak) keeps the old volume guard.
        # Refinement after live logs (2026-10-09): peak_liq == 0 means this call
        # NEVER had any liquidity reading (e.g. pump.fun bonding-curve tokens
        # whose pool DexScreener does not report yet) - that is "unknown",
        # not "pulled", so it keeps the old volume liveness guard. Only a call
        # that HAD liquidity (peak_liq > 0) and is now exactly $0 is a rug.
        if vol >= LIVE_MIN_VOL_H24 and (liq > 0 or peak_liq <= 0):
            call["_rug_strikes"] = 0
            logger.warning(f"⏳ rug-gate liq_glitch {call.get('symbol','?')} @{call.get('channel','?')} "
                           f"liq=${liq:.0f} peak=${peak_liq:.0f} mc=${mc:.0f} vol24h=${vol:.0f} "
                           f"— vol24h proves it's alive, NOT rugging (data glitch) — will retry next tick")
            return (False, "liq_glitch_retry")
        # FIX (freeze bug, second layer): the volume guard above only saves a
        # call when THAT SAME tick also shows healthy volume. A malformed/
        # partial DexScreener payload (common right when a 429 storm is
        # recovering) can report BOTH liq=0 and vol=0 in the same bad tick —
        # that combination used to hit the permanent-rug branch immediately
        # below on a single reading. Require RUG_CONFIRM_STRIKES consecutive
        # bad ticks (default 3) before making it permanent; any tick with
        # healthy liquidity resets the counter to zero.
        RUG_CONFIRM_STRIKES = max(1, int(os.environ.get("RUG_CONFIRM_STRIKES", "3") or 3))
        strikes = int(call.get("_rug_strikes", 0) or 0) + 1
        call["_rug_strikes"] = strikes
        if strikes < RUG_CONFIRM_STRIKES:
            logger.info(f"🟡 rug-gate strike {strikes}/{RUG_CONFIRM_STRIKES} "
                        f"{call.get('symbol','?')} @{call.get('channel','?')} "
                        f"liq=${liq:.0f} peak=${peak_liq:.0f} vol24h=${vol:.0f} — "
                        f"not yet permanent, retrying")
            return (False, "liquidity_pulled_pending")
        call["rugged"] = True
        call["frozen"] = True
        call["rug_reason"] = "liquidity_pulled"
        call["rugged_at"] = datetime.utcnow().isoformat()
        logger.warning(f"🛑 RUG detected {call.get('symbol','?')} @{call.get('channel','?')} "
                       f"liq=${liq:.0f} peak=${peak_liq:.0f} mc=${mc:.0f} vol24h=${vol:.0f} "
                       f"(confirmed over {strikes} consecutive ticks) — "
                       f"alerts stopped PERMANENTLY. If this token is actually still live "
                       f"(DexScreener glitch/pool switch), fix with: "
                       f"/unfreezecall {call.get('ca','')}  then  "
                       f"/recheckx @{call.get('channel','?')} {call.get('ca','')}")
        return (False, "liquidity_pulled")
    elif call.get("_rug_strikes"):
        call["_rug_strikes"] = 0

    # ── LIVENESS OVERRIDE ───────────────────────────────────────────────
    # Real 24h volume = real buyers and sellers right now. DexScreener often
    # reports only one thin pool for a multi-pool token, which made the
    # depth heuristics below silently swallow perfectly live calls.
    if vol >= LIVE_MIN_VOL_H24 and liq > 0:
        return (True, "ok")

    if liq < RUG_MIN_LIQUIDITY and vol < RUG_MIN_VOL_H24:
        return (False, "low_liquidity")

    # Phantom marketcap: DexScreener FDV/marketCap of a dead token vs. its real
    # liquidity — only meaningful when the chart is also dead.
    if mc > liq * MAX_MC_LIQ_RATIO and vol < LIVE_MIN_VOL_H24:
        logger.warning(f"⚠️ Ignoring phantom MC {fmt_mc(mc)} vs liq ${liq:.0f} "
                       f"for {call.get('symbol','?')} @{call.get('channel','?')}")
        return (False, "mc_liquidity_mismatch")

    # Dead chart (no trading in 24h) — nothing real to celebrate.
    if vol < RUG_MIN_VOL_H24:
        return (False, "no_volume")

    return (True, "ok")


def fmt_x(x: float) -> str:
    """Format X multiplier as readable decimal string (e.g. 1.25x, 2.7x, 123x)."""
    if x <= 0: return "1x"
    if x >= 100:   return f"{x:.0f}x"
    elif x >= 2:   return f"{x:.1f}x"
    elif x > 1.01: return f"{x:.1f}x"
    return "1x"

def _safe_html_cap(text, limit=1024):
    """Trim caption to limit without breaking open HTML tags."""
    if not text or len(text) <= limit:
        return text or ""
    cut = text[:limit]
    # If we sliced inside an open tag, back up to before that '<'
    last_open = cut.rfind("<")
    if last_open != -1 and ">" not in cut[last_open:]:
        cut = cut[:last_open]
    return cut.rstrip()

async def send_photo_safe(target, photo_path, caption, parse_mode="HTML", reply_markup=None):
    """Send photo from file path. Caption safely trimmed to 1024 chars. Returns message or None."""
    try:
        cap = _safe_html_cap(caption)
        with open(photo_path, "rb") as f:
            return await target.reply_photo(photo=f, caption=cap,
                                             parse_mode=parse_mode, reply_markup=reply_markup)
    except FileNotFoundError:
        logger.warning(f"Image not found: {photo_path}")
        return None
    except Exception as e:
        logger.warning(f"send_photo_safe ({photo_path}): {e}")
        return None

async def send_video_safe(target, video_path, caption, parse_mode="HTML", reply_markup=None):
    try:
        cap = caption[:1024] if caption else ""
        with open(video_path, "rb") as f:
            return await target.reply_video(video=f, caption=cap,
                                             parse_mode=parse_mode, reply_markup=reply_markup)
    except FileNotFoundError:
        logger.warning(f"Video not found: {video_path}")
        return None
    except Exception as e:
        logger.warning(f"send_video_safe ({video_path}): {e}")
        return None

# ─── Default texts ────────────────────────────────────────────────────────────
DEFAULT_START_TEXT = (
    f'<b>{_history_emoji(5424907147865513527, "🔮")} WIZARD SCAN – Your Crypto Radar</b>\n\n'
    "WIZRAD SCAN tracks crypto callers in real time, monitors their calls and automatically detects important milestones as they happen.\n\n"
    "Wizrad Scan doesn't only support English alerts. You can receive alerts in Chinese, Vietnamese, Turkish, Russian, and more.\n\n"
    "Currently supports 7 chains: BNB, ETH, SOL, Base, TON, RH & ARC. More trending chains will be added as they gain traction.\n\n"
    f'{_history_emoji(5204161750294873136, "🪄")} KOL Ranking & Leaderboards\n'
    f'{_history_emoji(5204161750294873136, "🪄")} Cheesepad Token Tracking\n'
    f'{_history_emoji(5204161750294873136, "🪄")} PinkSale Token Tracking\n'
    f'{_history_emoji(5204161750294873136, "🪄")} Profit Alerts via DM\n'
    f'{_history_emoji(5204161750294873136, "🪄")} Performance history tracking\n'
    f'{_history_emoji(5204161750294873136, "🪄")} No noise. No hype. Just results.\n\n'
    "Click the Command button below to get started and explore the other available commands."
)

DEFAULT_COMMAND_TEXT = (
    "<b>🔮 Wizard Scan Command Center</b>\n\n"
    "Welcome to the Wizard Command Center. Use the buttons below to explore tracking tools, rankings, channel analytics, and caller statistics.\n\n"
    "<b>🔮 Choose an option to continue:</b>"
)

DEFAULT_KOL_REQUEST = (
    "<b>🔮 REQUEST YOUR KOL</b>\n\n"
    "Summon a KOL to be tracked on Wizard Scan.\n\n"
    "Send: <code>/submit @channelname</code>\n\n"
    "Contact our team to request a KOL channel for tracking.\n\n"
    "<b>🔮 How it works:</b>\n\n"
    "Our team manually reviews every request. No guarantee when. Could be today, 1 week, or 1 month. We receive 1000+ KOL requests. Patience is key.\n\n"
    "⚠️ Channels that spam or post low-quality calls will be rejected.\n\n"
    "<b>🪄 Want priority review?\n"
    "🪄 Click Fast Track below:</b>"
)

DEFAULT_PROMO_HUB = (
    "<b>🔮 ADVERTISE WITH US</b>\n\n"
    "Promote your project or channel through the WIZARD SCAN ecosystem. Our network includes active callers, experienced traders, project builders, and crypto communities.\n\n"
    "🪄 Pinned Post on @WizardScan\n"
    "🪄 Trending Project Listings\n"
    "🪄 Project Promotions\n"
    "🪄 Channel Promotions\n"
    "🪄 User DM Campaigns\n"
    "🪄 Trending KOLs Listings\n\n"
    "<b>For pricing and campaign details, contact our team.</b>"
)

DEFAULT_CHAT_US = (
    "<b>🔮 CONTACT WIZARD SCAN</b>\n\n"
    "Need assistance, partnership info, channel tracking, or promotional services? Our team is here.\n\n"
    "<b>🪄 Contact our team:</b>"
)

DEFAULT_FAST_TRACK = (
    "<b>🔮 FAST TRACK ACCESS</b>\n\n"
    "Skip the waiting list and receive priority review for your channel.\n\n"
    "Standard tracking requests have no guaranteed review timeframe due to high volume. 1,000+ channels are currently awaiting review.\n\n"
    "<b>🔮 Fast Track provides:</b>\n\n"
    "🪄 Priority Review\n"
    "🪄 Lifetime Tracking\n"
    "🪄 Faster Channel Approval\n\n"
    "<b>Contact our team for Fast Track access.</b>"
)

DEFAULT_LEADERBOARD = (
    "<b>🔮 LEADERBOARD KOLS</b>\n\n"
    "The Leaderboard ranks KOL channels by their highest confirmed call multiplier — tracked live by Wizard Scan. "
    "It's easy to make it onto the KOL leaderboard since it doesn't use a points-based system.\n\n"
    "📌 View live rankings → <a href=\"https://t.me/WizardScan/136\">Post 136</a>\n\n\n"
    "<b>🔮 CHAMPION KOLS</b>\n\n"
    "Reaching the Champion KOLs leaderboard is more challenging. To qualify, you must first earn 100 points. "
    "The Champion KOLs leaderboard resets every 7 days, and every 7 days, the points of all KOLs who have reached "
    "100 points will be reset, giving everyone a fresh chance to compete.\n\n"
    "📌 View live Champions → <a href=\"https://t.me/WizardScan/134\">Post 134</a>\n\n\n"
    "<b>🔮 How points are earned:</b>\n\n"
    "▪ Call hits 2X–4X → +4 pts\n"
    "▪ Call hits 5X–9X → +9 pts\n"
    "▪ Call hits 10X–24X → +18 pts\n"
    "▪ Call hits 25X–49X → +30 pts\n"
    "▪ Call hits 50X–99X → +45 pts\n"
    "▪ Call hits 100X–249X → +65 pts\n"
    "▪ Call hits 250X+ → +90 pts\n\n"
    "▪ Points are awarded per call, once per tier — a call that climbs from 2X to 10X only keeps its highest tier reward.\n"
    "▪ A call that fails to reach 2X within 48 hours → −10 pts\n"
    "▪ Only calls made AFTER the last reset count towards the running 7-day cycle.\n\n"
    "<b>🔮 Champion requirements:</b>\n\n"
    "▪ 100 points minimum to enter the Champion KOLs list\n"
    "▪ Full points reset every 7 days — everyone restarts from 0\n"
    "▪ Rugged / fake-marketcap calls are never counted"
)

DEFAULT_ALERT_RULES = (
    "<b>🔮 ALERT RULES</b>\n\n"
    "Wizard Scan tracks KOL calls and sends alerts at key milestones:\n\n"
    "<b>🔮 Alert Schedule:</b>\n\n"
    "🪄 2X, 3X, 4X, 5X\n"
    "🪄 Every +5X from 10X to 100X\n"
    "🪄 Every +50X from 100X to 500X\n"
    "🪄 Every +100X from 500X to 1,000X\n"
    "🪄 Every +500X from 1,000X to 10,000X\n"
    "🪄 Every +1,000X above 10,000X\n\n"
    "No spam. Just real milestones.\n\n"
    "Use /command for more options."
)

DEFAULT_XCOMMAND = (
    "<b>🔮 WIZARD SCAN X</b>\n\n"
    "Wizard Scan now tracks Twitter (X) accounts for calls and contract addresses.\n\n"
    "📌 How to get listed:\n"
    "DM the OWNER with the X account you want tracked. Manual review required.\n\n"
    "<b>🔮 Twitter Alerts Channel:</b>\n"
    ""
    "📌 Tracked X Accounts:\n"
    "/xlist — View all X accounts currently tracked by Wizard Scan"
)

DEFAULT_HISTORY_INFO = (
    f'{_history_emoji(5449444558020565509, "🔮")}<b>CHANNEL HISTORY LOOKUP</b>\n\n'
    "Instantly look up the full call history of any tracked KOL channel. How to use:\n\n"
    "• Simply type: /history @channelname\n"
    "• Example: /history @BlahBlahKOL\n"
    "• Or paste the @channel name directly in the chat.\n\n"
    f'{_history_emoji(5449444558020565509, "🔮")}<b>HOPE YOU LIKE IT</b>\n\n'
    "Just make sure the KOL you're checking is already being tracked by our bot."
)

DEFAULT_LINKME_INFO = (
    f'{_history_emoji(5823636561413021540, "🔮")}<b>LINK YOUR KOL FOR ALL ALERTS</b>\n\n'
    "With /linkme, all our KOL X Alerts and Hot Streak Alerts will be auto-forwarded to your channel.\n\n"
    f'{_history_emoji(5873177493048927315, "🔮")}<b>Setup Tutorial:</b>\n\n'
    "Step 1: Add @WIZARD_SCAN_BOT as admin\n"
    "Step 2: Send /linksmart @yourKOL here\n\n"
    "Example: /linkme @BlahBlahKOL\n\n"
    "<b><i>Want only solid X Alerts instead of spam? Just check /linksmart once.</i></b>"
)

DEFAULT_LINKSMART_INFO = (
    f'{_history_emoji(6044119257308995249, "📔")} <b>LINK SMART — CHANNEL ALERTS</b>\n\n'
    "Link your KOL channel and get selected milestone alerts forwarded automatically. "
    "Bot will forward only these milestones: 5X, 10X, 20X, 30X..... 100X, After 100X, all alerts forward normally.\n\n"
    f'{_history_emoji(6044371908760182103, "🔥")}<b>Setup Tutorial:</b>\n\n'
    "Step 1: Add @WIZARD_SCAN_BOT as admin\n"
    "Step 2: Send /linksmart @yourKOL here\n\n"
    "Example: /linksmart @BlahBlahKOL\n\n"
    f'{_history_emoji(5846117571291521144, "⚠️")} <b>Only Tracked Channels Are Eligible.</b>'
)

# Apply premium emoji tags to all user-facing default templates.
# InlineKeyboardButton labels and channel-post templates (userbot entity injection)
# are intentionally NOT included here — they use plain emoji characters.
def _pe(text: str) -> str:
    return text.replace("🔮", PE_CRYSTAL).replace("🪄", PE_WAND).replace("➤", PE_ARROW)

DEFAULT_START_TEXT   = _pe(DEFAULT_START_TEXT)
DEFAULT_COMMAND_TEXT = _pe(DEFAULT_COMMAND_TEXT)
DEFAULT_KOL_REQUEST  = _pe(DEFAULT_KOL_REQUEST)
DEFAULT_PROMO_HUB    = _pe(DEFAULT_PROMO_HUB)
DEFAULT_CHAT_US      = _pe(DEFAULT_CHAT_US)
DEFAULT_FAST_TRACK   = _pe(DEFAULT_FAST_TRACK)
DEFAULT_LEADERBOARD  = _pe(DEFAULT_LEADERBOARD)
DEFAULT_ALERT_RULES  = _pe(DEFAULT_ALERT_RULES)
DEFAULT_XCOMMAND     = _pe(DEFAULT_XCOMMAND)
DEFAULT_SUBSCRIBE_INFO = (
    "<b>🔮 DM ALERTS</b>\n\n"
    "If you want alerts from a specific KOL channel to appear here, send:\n\n"
    "<code>/subscribe @channelname</code>\n\n"
    "Example: <code>/subscribe @SomeCryptoKOL</code>\n\n"
    "Send <code>/unsubscribe @channelname</code> to unsubscribe."
)
DEFAULT_SUBSCRIBE_INFO = _pe(DEFAULT_SUBSCRIBE_INFO)
DEFAULT_HISTORY_INFO = _pe(DEFAULT_HISTORY_INFO)
DEFAULT_LINKME_INFO  = _pe(DEFAULT_LINKME_INFO)
DEFAULT_LINKSMART_INFO = _pe(DEFAULT_LINKSMART_INFO)

# ─── Keyboards ────────────────────────────────────────────────────────────────
CONTACT_BUTTONS = InlineKeyboardMarkup([
    [_pkb("🔮 X 🔮",       url="https://x.com/Cheesy_Wolf")],
    [_pkb("🔮 ADMIN 🔮",   url="https://t.me/Wizard_Scan")],
    [_pkb("🔮 CHANNEL 🔮", url="https://t.me/WizardScan")],
])

CHAT_US_BUTTON = InlineKeyboardMarkup([
    [_pkb("🔮  Chat With Us  🔮", callback_data="chat_us")]
])

def build_command_keyboard():
    config = load_config(); labels = config.get("button_labels", {})
    def lbl(k, d): return labels.get(k, d)
    return InlineKeyboardMarkup([
        [_pkb(lbl("kol_request",  "🔮 Request your KOL 🔮"),  callback_data="kol_request")],
        [_pkb(lbl("promo_hub",    "🔮 Promotion HUB 🔮"),     callback_data="promo_hub")],
        [_pkb(lbl("leaderboard",  "🔮 Leaderboard 🔮"),       callback_data="leaderboard")],
        [_pkb(lbl("dm_alerts",    "🔮 DM Alerts 🔮"),         callback_data="dm_alerts")],
        [_pkb(lbl("fast_track",   "🔮 Fast Track 🔮"),        callback_data="fast_track")],
        [_pkb(lbl("chat_us",      "🔮  Chat With Us  🔮"),    callback_data="chat_us")],
    ])

def history_keyboard(channel, detail=False):
    ch = channel[:28]
    tag = "|d" if detail else ""  # marks buttons from the new detailed lookup view
    return InlineKeyboardMarkup([
        [
            _pkb("🔮BNB",  callback_data=f"h|{ch}|bnb{tag}"),
            _pkb("🔮ETH",  callback_data=f"h|{ch}|eth{tag}"),
            _pkb("🔮SOL",  callback_data=f"h|{ch}|sol{tag}"),
        ],
        [
            _pkb("🔮BASE", callback_data=f"h|{ch}|base{tag}"),
            _pkb("🔮TON",  callback_data=f"h|{ch}|ton{tag}"),
            _pkb("🔮RH",   callback_data=f"h|{ch}|rh{tag}"),
        ],
        [
            _pkb("🔮ARC",  callback_data=f"h|{ch}|arc{tag}"),
            _pkb("🔮TOP",  callback_data=f"h|{ch}|top{tag}"),
            _pkb("🔮ALL",  callback_data=f"h|{ch}|all{tag}"),
        ],
    ])

# ─── Userbot ──────────────────────────────────────────────────────────────────
def load_userbot_session():
    try:
        with open(USERBOT_SESSION_FILE) as f: return f.read().strip()
    except Exception: return ""

def save_userbot_session(s):
    with open(USERBOT_SESSION_FILE, "w") as f: f.write(s)

def _install_telethon_exception_handler():
    """Install a custom asyncio exception handler that swallows Telethon v1.44.0's
    known internal bugs (coroutine-not-awaited, event-loop-closed) so they don't
    crash the Railway process.

    Telethon v1.44.0 has two confirmed bugs:
      1. MTProtoSender._reconnect coroutine created but never awaited → RuntimeWarning
      2. TelegramBaseClient._disconnect_coro coroutine never awaited → RuntimeWarning
    Both are benign for our use-case — the watchdog job handles reconnection.
    """
    loop = asyncio.get_event_loop()

    def _handler(loop, context):
        msg       = context.get("message", "")
        exc       = context.get("exception")
        exc_str   = str(exc) if exc else ""
        # Known Telethon v1.44.0 benign errors — log as debug, don't crash
        telethon_noise = [
            "Event loop is closed",
            "coroutine 'MTProtoSender",
            "coroutine 'TelegramBaseClient",
            "_reconnect",
            "_disconnect_coro",
            "was never awaited",
            "NoneType can't be used in 'await'",
        ]
        if any(s in msg or s in exc_str for s in telethon_noise):
            logger.debug(f"[Telethon noise suppressed] {msg or exc_str}")
            return
        # All other exceptions: use the default handler
        loop.default_exception_handler(context)

    try:
        loop.set_exception_handler(_handler)
        logger.info("✅ Asyncio exception handler installed (Telethon crash suppression active)")
    except Exception as e:
        logger.warning(f"Could not install asyncio exception handler: {e}")


async def init_userbot():
    """Initialize (or re-initialize) the Telethon userbot client.

    Config hardened for Railway long-running deployments:
    - connection_retries=10  : retry network drops up to 10× before giving up
    - retry_delay=5          : 5s between retries
    - auto_reconnect=True    : Telethon auto-reconnects on connection drops
    - catch_up=False         : don't replay missed updates on reconnect (saves RAM/CPU)
    - receive_updates=True   : required for NewMessage event handler
    - flood_sleep_threshold=60 : auto-sleep on FLOOD_WAIT up to 60s
    """
    global userbot_client, _userbot_init_lock
    session_str = load_userbot_session() or os.environ.get("SESSION_STRING", "").strip()
    if not session_str or not OWNER_API_ID or not OWNER_API_HASH:
        logger.warning("⚠️ Userbot session not found.")
        return
    if _userbot_init_lock is None:
        _userbot_init_lock = asyncio.Lock()
    async with _userbot_init_lock:
        # post_init and the watchdog used to enter here together, disconnecting
        # each other's MTProto transport and causing `_connection.recv()` on None.
        if userbot_client and userbot_client.is_connected():
            return
        candidate = None
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            candidate = TelegramClient(
                StringSession(session_str),
                OWNER_API_ID,
                OWNER_API_HASH,
                connection_retries=3,
                retry_delay=3,
                auto_reconnect=True,
                catch_up=False,
                receive_updates=True,
                flood_sleep_threshold=60,
            )
            await asyncio.wait_for(candidate.connect(), timeout=45)
            if not await asyncio.wait_for(candidate.is_user_authorized(), timeout=15):
                logger.warning("Userbot: session exists but not authorized")
                await candidate.disconnect()
                return
            me = await asyncio.wait_for(candidate.get_me(), timeout=15)
            userbot_client = candidate
            logger.info(f"✅ Userbot: @{me.username or me.id}")
            _install_telethon_exception_handler()
        except Exception as e:
            logger.error(f"Userbot init failed: {type(e).__name__}: {e}")
            if candidate:
                try:
                    await candidate.disconnect()
                except Exception:
                    pass
            userbot_client = None

# ─── DexScreener ─────────────────────────────────────────────────────────────
# In-memory cache: {ca: (result_dict, expiry_timestamp)}
# TTL = 20s so milestone_job gets fresh data on every 15s tick without hammering API
_dex_cache: dict = {}
# The bulk refresh runs once per monitoring tick.  Keep the cache alive for
# slightly longer than one tick so the individual checks below never fall back
# to opening one HTTP request per token after the bulk request has completed.
_DEX_CACHE_TTL = 2.5

# Chain slug map for v3 endpoint (DexScreener chainId → v3 slug)
_DEX_V3_CHAIN = {
    "solana": "solana", "ethereum": "ethereum", "bsc": "bsc",
    "base": "base", "ton": "ton", "arbitrum": "arbitrum",
}

def _pick_market_cap(pairs: list) -> tuple[float, bool]:
    """Return (market_cap, is_fdv) — the most trustworthy market cap from a
    DexScreener pairs list, and whether it had to fall back to FDV.

    DexScreener reports marketCap/fdv per pair and a single stale pair can be
    badly wrong. We therefore:
      1. take marketCap from the deepest-liquidity pair,
      2. cross-check it against the median marketCap of all pairs of the same
         token — if it is more than 3x off, trust the median instead,
      3. never substitute FDV for real market cap AS IF it were real.

    FIX (RH-chain "called at 19K, bot shows 119K instantly" incident,
    2026-09-24): point 3 used to mean "return 0.0" whenever no pair had a
    real marketCap field — which DexScreener commonly never populates for
    Robinhood-chain (tokenized-stock) pairs, only `fdv`. A silent 0.0 here
    means _reconcile_entry_mc sees "nothing to compare the caller's post-hint
    against" and blindly LOCKS IN that hint as fully "confirmed", with the
    entire cross-check/correction machinery (_confirm_entry_mc, the pre-post
    settle check, the _mc_is_fdv ratio guard) never engaging at all — because
    all of that machinery only ever triggers off an explicit _mc_is_fdv flag,
    and this path never set one. A caller posting (or a bot mis-parsing) a
    lowballed "called at 2.7K" MC then rode straight through to a real 65K
    chart as a fake "24X", with zero of the anti-cheat checks built for
    exactly this scenario ever firing.
    Now: FDV is still never trusted AS real MC (nothing changes for the
    normal case), but it IS returned as a flagged, provisional number instead
    of a bare 0 — so callers correctly route it through the same
    live_fdv/post_unconfirmed provisional path already built for alt sources
    (Birdeye/GeckoTerminal), which locks it in only after an independent
    cross-check confirms it.
    """
    mcs = []
    for p in pairs:
        try:
            v = float(p.get("marketCap") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            mcs.append(v)
    if mcs:
        primary = mcs[0]
        if len(mcs) >= 3:
            ordered = sorted(mcs)
            median = ordered[len(ordered) // 2]
            if median > 0 and (primary > median * 3 or primary * 3 < median):
                return (median, False)
        return (primary, False)
    # No pair has a real circulating marketCap — fall back to the
    # deepest-liquidity pair's FDV, flagged, rather than a bare 0.0.
    for p in sorted(pairs, key=lambda p: (p.get("liquidity", {}) or {}).get("usd", 0) or 0,
                     reverse=True):
        try:
            fdv = float(p.get("fdv") or 0)
        except (TypeError, ValueError):
            fdv = 0.0
        if fdv > 0:
            return (fdv, True)
    return (0.0, False)


def _parse_dex_pairs(pairs: list, min_liquidity: int, token_address: str = "") -> dict | None:
    """Parse a DexScreener pairs list into our internal dict. Returns None if unusable."""
    sup = [p for p in pairs if p.get("chainId","").lower() in SUPPORTED_CHAINS
           and (p.get("liquidity",{}).get("usd") or 0) >= min_liquidity]
    # priceUsd always describes baseToken.  A searched contract can also occur
    # as quoteToken; treating that pair as the call token silently tracks the
    # other asset (often SOL/WETH) and leaves the call stuck around 1x.
    if token_address:
        wanted = token_address.lower()
        sup = [p for p in sup if ((p.get("baseToken") or {}).get("address") or "").lower() == wanted]
    if not sup:
        return None
    # Cross-chain address collision guard: the exact same literal address can
    # exist as a completely unrelated pair on more than one EVM chain (vanity/
    # CREATE2 deployments, or plain coincidence). Sorting by liquidity alone
    # in that case can silently lock onto a totally different, much bigger
    # project on another chain — e.g. a fresh 435 MC KOL call getting matched
    # to an unrelated $1.5M pool elsewhere that happens to share this address.
    # A freshly-posted call is virtually always the NEWEST pool, so when
    # multiple different chains match, narrow down to the chain whose pool
    # was created most recently before ranking by liquidity within it.
    chains_seen = {p.get("chainId", "").lower() for p in sup}
    if len(chains_seen) > 1:
        dated = [p for p in sup if p.get("pairCreatedAt")]
        if dated:
            newest_chain = max(dated, key=lambda p: p.get("pairCreatedAt") or 0).get("chainId", "").lower()
            same_chain = [p for p in sup if p.get("chainId", "").lower() == newest_chain]
            if same_chain and len(same_chain) < len(sup):
                logger.warning(
                    f"⚠️ Cross-chain address collision for {token_address[:14]}… "
                    f"across {sorted(chains_seen)} — restricting to newest pool's "
                    f"chain ({newest_chain}) to avoid tracking the wrong project")
                sup = same_chain
    sup = sorted(sup, key=lambda p: p.get("liquidity",{}).get("usd",0) or 0, reverse=True)
    best = sup[0]
    chain  = SUPPORTED_CHAINS[best.get("chainId","").lower()]
    price  = float(best.get("priceUsd") or 0)
    mc, mc_is_fdv = _pick_market_cap(sup)
    symbol = best.get("baseToken",{}).get("symbol","")
    if price <= 0 and mc <= 0 and not symbol:
        return None
    price_change_h1  = float((best.get("priceChange") or {}).get("h1") or 0)
    price_change_h24 = float((best.get("priceChange") or {}).get("h24") or 0)
    volume_h24       = float((best.get("volume") or {}).get("h24") or 0)
    liquidity_usd    = float((best.get("liquidity") or {}).get("usd") or 0)
    txns_h24_buys    = int((best.get("txns") or {}).get("h24", {}).get("buys") or 0)
    txns_h24_sells   = int((best.get("txns") or {}).get("h24", {}).get("sells") or 0)
    listed_at        = best.get("pairCreatedAt")
    socials = best.get("info",{}).get("socials") or []
    tg_link = ""
    for s in socials:
        if "t.me" in (s.get("url","")) or "telegram" in (s.get("type","")).lower():
            tg_link = s.get("url",""); break
    return {
        "chain":           chain,
        "mcap":            mc,
        "mcap_fmt":        fmt_mc(mc) if mc > 0 else "N/A",
        "price":           price,
        "symbol":          symbol,
        "name":            best.get("baseToken",{}).get("name",""),
        "pair_addr":       best.get("pairAddress",""),
        "tg_link":         tg_link,
        "price_change_h1": price_change_h1,
        "price_change_h24":price_change_h24,
        "volume_h24":      volume_h24,
        "liquidity_usd":   liquidity_usd,
        "txns_buys":       txns_h24_buys,
        "txns_sells":      txns_h24_sells,
        "listed_at":       listed_at,
        "_partial":        (price <= 0 and mc <= 0),
        "_mc_is_fdv":      bool(mc_is_fdv and mc > 0),
    }

# ── Shared HTTP session with connection pooling + automatic retries ──────────
_dex_session = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _dex_retry = Retry(
        total=1, connect=1, read=1, backoff_factor=0.25,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    _dex_adapter = HTTPAdapter(max_retries=_dex_retry, pool_connections=64, pool_maxsize=128)
    _dex_session.mount("https://", _dex_adapter)
    _dex_session.mount("http://", _dex_adapter)
except Exception as _e_adapter:  # urllib3 API change — plain session still works
    logger.warning(f"DexScreener retry adapter unavailable: {_e_adapter}")
_dex_session.headers.update(HEADERS)

# Negative cache: CA -> expiry. Stops us hammering DexScreener for tokens
# that are simply not indexed yet (main source of rate-limit 429 storms).
_dex_miss_cache: dict = {}
_DEX_MISS_TTL = 3           # seconds — short enough for fresh pairs, long enough to avoid storms
_DEX_CACHE_MAX = 4000       # hard cap so long-running Railway deploys never OOM
# A second quote path is used for tokens that are still below 2X.  This catches
# the common case where DexScreener serves an old 1X snapshot while Gecko/
# DexPaprika already has the live move.  The per-token cooldown keeps it cheap.
_monitor_alt_probe_at: dict = {}

# ── DexScreener circuit breaker ─────────────────────────────────────────────
# Jab DexScreener 429 (rate limit) deta hai to _DEX_429_UNTIL future timestamp
# set ho jata hai. Jab tak breaker open hai, _dex_get BINA request kiye 429
# return karta hai aur _fetch_dex_sync seedha alternate dex sources
# (GeckoTerminal -> DexPaprika -> Birdeye -> Moralis -> CoinMarketCap) se data
# leta hai. Is se "429 -> sleep -> retry" storms khatam hote hain jo monitoring
# ticks ko wedge kar ke alerts skip kara rahe thay.
_DEX_429_UNTIL = 0.0
_DEX_429_COOLDOWN = int(os.environ.get("DEX_429_COOLDOWN_SECONDS", "20") or 20)

# OWNER FIX (2026-09-14, casinoinsiderhere/1009 bizzradio incident): logs
# showed DexScreener returning 429 on almost EVERY tick, for both the bulk
# batch endpoint and individual per-CA lookups, nearly continuously — not
# just for one token. This is very likely a shared contributor behind the
# "20s late", "MC stuck at 1x" and "missed the pump" reports.
#
# REVERTED (2026-09-13, same day): a shared blocking time.sleep()-based
# pacer was added here to pre-emptively throttle outgoing requests. That
# blocked whichever of the bot's limited worker threads called it — and
# with request demand far higher than the pacer's allowance, most of the
# thread pool ended up permanently parked inside that sleep, starving every
# other job (Telegram command handling included) of a free worker thread.
# Result: monitoring_job/priority_monitoring_job/scan_job all started
# hitting their hard-timeouts and "maximum number of running instances
# reached", and the bot stopped responding to commands entirely. A
# thread-blocking approach is fundamentally unsafe on a shared, finite
# thread pool — any real fix here needs to be non-blocking (e.g. an
# async-native limiter, or simply relying on the reactive breaker below
# and accepting some 429s) rather than making callers wait in line.
# Removed until that can be done safely; the reactive breaker (which was
# already here and does not block anything) is the only throttle again.

def _dex_breaker_open() -> bool:
    return time.time() < _DEX_429_UNTIL

def _dex_trip_breaker():
    global _DEX_429_UNTIL
    _DEX_429_UNTIL = time.time() + _DEX_429_COOLDOWN


# ── Proactive request-budget limiter ────────────────────────────────────────
# OWNER FIX (2026-09-14): the breaker above is REACTIVE — it only slows down
# AFTER DexScreener has already 429'd us, and then blacks out ALL requests for
# a flat 20s. Live logs show DexScreener 429-ing on almost every tick, which
# means by the time the breaker trips we've already sent far more requests
# than DexScreener will accept, every single second. That's the same request
# storm that causes: (a) stale/conflicting price data (alt sources get used
# during the blackout and sometimes disagree with the real, current MC), and
# (b) monitoring ticks overlapping and getting skipped ("maximum number of
# running instances reached"), which is what makes 2x-10x alerts arrive late.
# This limiter caps requests to a steady budget PER ROLLING MINUTE, spread
# evenly, so we stop causing 429s in the first place instead of just reacting
# to them. It is deliberately non-blocking (no sleep, no thread parked
# waiting) — a request over budget is declined instantly and the caller's
# existing Birdeye/alt-source fallback covers it, exactly like a real 429
# would, just without actually triggering one.
_DEX_RATE_LOCK = _threading.Lock()
_DEX_RATE_TIMESTAMPS = deque()
_DEX_MAX_REQUESTS_PER_MINUTE = max(1, int(os.environ.get("DEX_MAX_REQUESTS_PER_MINUTE", "260") or 260))
# Raised 180 -> 260 (2026-09-17): DexScreener's real documented limit for
# these endpoints is ~300/min. 180 was overly conservative AND was being
# wasted by the hot-call fast-lane bug above (fixed) that burned 40 requests
# where ~1-2 batched ones would do. With that fixed, there's real headroom —
# 260 keeps a safety margin under 300 without leaving speed on the table.

# FIX (2026-10-13): adaptive budget + visibility.
# Before: a flat 260/min budget plus a flat blackout after every real 429, and no
# way to tell "DexScreener really said 429" apart from "our own budget ran out"
# (both were logged as "429 ... breaker open"). Now:
#  * every request outcome is counted and ONE summary line is logged per minute;
#  * on a REAL 429 the effective budget drops 20% (floor 60/min) and then creeps
#    back up (+5%/30s) after a minute without 429s - so we settle just under the
#    real limit instead of bouncing between storms and 20s blackouts.
_DEX_STATS = {"ok": 0, "real429": 0, "budget_denied": 0, "breaker_denied": 0, "neterr": 0}
_DEX_STATS_AT = time.time()
_DEX_EFF_BUDGET = float(_DEX_MAX_REQUESTS_PER_MINUTE)
_DEX_LAST_429_AT = 0.0
_DEX_LAST_DEC_AT = 0.0
_DEX_LAST_ADJ_AT = time.time()
_DEX_BUDGET_FLOOR = 60
# Adaptive cut is OFF by default (speed stays exactly as configured). Turn it on
# later with the Railway variable DEX_ADAPTIVE_BUDGET=1 once the per-minute
# stats line has shown that real 429s (not our own budget) are the problem.
_DEX_ADAPTIVE = (os.environ.get("DEX_ADAPTIVE_BUDGET", "0") or "0").strip() == "1"

def _dex_stat(key: str):
    _DEX_STATS[key] = _DEX_STATS.get(key, 0) + 1

def _dex_stats_maybe_log():
    global _DEX_STATS_AT
    now = time.time()
    if now - _DEX_STATS_AT < 60:
        return
    snap = dict(_DEX_STATS)
    for k in list(_DEX_STATS):
        _DEX_STATS[k] = 0
    _DEX_STATS_AT = now
    logger.info(
        f"📊 DexScreener last min: ok={snap.get('ok',0)} real_429={snap.get('real429',0)} "
        f"budget_denied={snap.get('budget_denied',0)} breaker_denied={snap.get('breaker_denied',0)} "
        f"net_err={snap.get('neterr',0)} | budget={int(_DEX_EFF_BUDGET)}/{_DEX_MAX_REQUESTS_PER_MINUTE} "
        f"cooldown={_DEX_429_COOLDOWN}s adaptive={'on' if _DEX_ADAPTIVE else 'off'}")

def _dex_effective_budget() -> int:
    global _DEX_EFF_BUDGET, _DEX_LAST_ADJ_AT
    now = time.time()
    if (_DEX_EFF_BUDGET < _DEX_MAX_REQUESTS_PER_MINUTE
            and now - _DEX_LAST_429_AT > 60 and now - _DEX_LAST_ADJ_AT > 30):
        _DEX_EFF_BUDGET = min(float(_DEX_MAX_REQUESTS_PER_MINUTE),
                              _DEX_EFF_BUDGET + max(5.0, _DEX_MAX_REQUESTS_PER_MINUTE * 0.05))
        _DEX_LAST_ADJ_AT = now
    return int(_DEX_EFF_BUDGET)

def _dex_on_real_429():
    """Called when DexScreener itself returned HTTP 429."""
    global _DEX_EFF_BUDGET, _DEX_LAST_429_AT, _DEX_LAST_DEC_AT
    now = time.time()
    _DEX_LAST_429_AT = now
    if not _DEX_ADAPTIVE:
        return   # observe only - never lowers the request budget
    # several in-flight requests can 429 together - cut only once per 10s
    if now - _DEX_LAST_DEC_AT >= 10:
        _DEX_LAST_DEC_AT = now
        _DEX_EFF_BUDGET = max(float(_DEX_BUDGET_FLOOR), _DEX_EFF_BUDGET * 0.8)

def _dex_rate_budget_ok() -> bool:
    now = time.time()
    budget = _dex_effective_budget()
    with _DEX_RATE_LOCK:
        while _DEX_RATE_TIMESTAMPS and now - _DEX_RATE_TIMESTAMPS[0] > 60:
            _DEX_RATE_TIMESTAMPS.popleft()
        if len(_DEX_RATE_TIMESTAMPS) >= budget:
            return False
        _DEX_RATE_TIMESTAMPS.append(now)
        return True


# Same idea for Birdeye. Free-tier Birdeye accounts share ONE account-wide
# budget (commonly 60 requests/minute) across every endpoint — token_overview
# (single-CA fallback) AND multi_price (bulk refresh) both draw from it. Cap
# comfortably under that so this account doesn't start 429-ing too, right
# when it's needed most (DexScreener's breaker being open).
_BIRDEYE_RATE_LOCK = _threading.Lock()
_BIRDEYE_RATE_TIMESTAMPS = deque()
_BIRDEYE_MAX_REQUESTS_PER_MINUTE = max(1, int(os.environ.get("BIRDEYE_MAX_REQUESTS_PER_MINUTE", "50") or 50))

def _birdeye_rate_budget_ok() -> bool:
    now = time.time()
    with _BIRDEYE_RATE_LOCK:
        while _BIRDEYE_RATE_TIMESTAMPS and now - _BIRDEYE_RATE_TIMESTAMPS[0] > 60:
            _BIRDEYE_RATE_TIMESTAMPS.popleft()
        if len(_BIRDEYE_RATE_TIMESTAMPS) >= _BIRDEYE_MAX_REQUESTS_PER_MINUTE:
            return False
        _BIRDEYE_RATE_TIMESTAMPS.append(now)
        return True


def _dex_cache_gc():
    """Evict expired / overflowing cache entries (prevents unbounded RAM growth)."""
    import time as _time
    now = _time.time()
    for cache in (_dex_cache, _dex_miss_cache):
        if len(cache) < _DEX_CACHE_MAX:
            continue
        for k in [k for k, v in list(cache.items())
                  if (v[1] if isinstance(v, tuple) else v) < now]:
            cache.pop(k, None)
        while len(cache) > _DEX_CACHE_MAX:
            try:
                cache.pop(next(iter(cache)))
            except StopIteration:
                break


def _dex_get(url: str, timeout: int = 5, fresh: bool = False):
    """GET with shared session. fresh=True bypasses intermediary HTTP caches."""
    try:
        _dex_stats_maybe_log()
    except Exception:
        pass
    if _dex_breaker_open():
        _dex_stat("breaker_denied")
        return 429  # breaker open: request bhejne ke baghair rate-limited batao
    if not _dex_rate_budget_ok():
        _dex_stat("budget_denied")
        return 429  # apna hi budget khatam — DexScreener ko hit kiye baghair fallback pe jao
    try:
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"} if fresh else None
        if fresh:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}_ts={int(time.time() * 1000)}"
        resp = _dex_session.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        _dex_stat("neterr")
        logger.warning(f"DexScreener network error ({url.split('/')[-1][:16]}…): {e}")
        return None
    if resp.status_code == 429:
        _dex_stat("real429")
        _dex_on_real_429()
        _dex_trip_breaker()
        return 429
    if resp.status_code != 200:
        return None
    _dex_stat("ok")
    try:
        return resp.json()
    except Exception:
        return None


def _dex_candidate_chains(ca: str) -> list:
    """Which DexScreener chains can this address exist on? Keeps the
    chain-scoped lookup to 1-4 cheap requests instead of brute-forcing."""
    # len==42: standard EVM (0x+40hex). len==66: Robinhood/"RH" 32-byte
    # address format (0x+64hex) seen on DexScreener chart links — without
    # this, a correctly-extracted Robinhood CA still failed here and fell
    # through to being queried as if it were a Solana address.
    if ca.startswith("0x") and len(ca) in (42, 66):
        return ["ethereum", "bsc", "base", "robinhood"]
    if ca.startswith(("EQ", "UQ")):
        return ["ton"]
    return ["solana"]


# ── Pair/pool address → token mint resolver ──────────────────────────────────
# KOL posts often carry a dexscreener PAIR link (dexscreener.com/solana/<pair>)
# or a GeckoTerminal pool link. Those addresses are NOT token mints, so a plain
# /latest/dex/tokens/<addr> lookup returns nothing and the call used to be
# skipped. This resolver asks DexScreener's pairs endpoint and returns the
# pair's baseToken address. Result (including "not a pair") is cached.
_pair_token_cache: dict = {}
_PAIR_RESOLVE_NEG_TTL = 90  # seconds — brand-new pairs aren't indexed by
                            # DexScreener's /pairs endpoint instantly; a miss
                            # this early must NOT be permanent (CobraGems etc.
                            # incident — calls stuck at dex_pending forever).

def _resolve_pair_to_token(addr: str):
    """Return the baseToken mint for a pair/pool address, else None."""
    if not addr:
        return None
    cached = _pair_token_cache.get(addr)
    if cached is not None:
        token_c, expires_at = cached
        if token_c or time.time() < expires_at:
            return token_c
        # negative result expired — fall through and try again
    token = None
    try:
        for slug in (_dex_candidate_chains(addr) or []):
            d = _dex_get(f"https://api.dexscreener.com/latest/dex/pairs/{slug}/{addr}",
                         timeout=8)
            if d == 429:
                break
            if not isinstance(d, dict):
                continue
            prs = d.get("pairs") or ([d.get("pair")] if d.get("pair") else [])
            for p in prs:
                if not isinstance(p, dict):
                    continue
                base = ((p.get("baseToken") or {}).get("address") or "").strip()
                if base and base.lower() != addr.lower():
                    token = base
                    break
            if token:
                break
    except Exception as e:
        logger.debug(f"pair→token resolve failed {addr[:12]}…: {e}")
    # FIX (2026-09-25, maybac_whales RH pair-link calls stuck dex_pending
    # forever incident): logs showed several Robinhood-chain calls where the
    # KOL only posted a dexscreener.com/robinhood/<pairAddress> link (no
    # separate contract line) sitting at dex_pending=True for 30-60+ minutes
    # straight and never resolving — silently excluded from tracking,
    # Leaderboard and Champions the whole time even though other chains'
    # pair-links resolved in seconds. DexScreener's OWN /latest/dex/pairs/
    # <chain>/<addr> endpoint (above) can badly lag or never index a
    # brand-new Robinhood-chain pool. GeckoTerminal indexes the "robinhood"
    # network independently (already relied on elsewhere in this file for
    # RH-chain cross-checks) and exposes the base token on a plain pool
    # lookup, so it is now tried as a second, independent resolver instead
    # of leaving the call stuck forever on DexScreener alone.
    if not token:
        _GT_PAIR_SLUG = {"robinhood": "robinhood", "solana": "solana", "ethereum": "eth",
                         "bsc": "bsc", "base": "base", "ton": "ton", "arc": "arc"}
        try:
            for slug in (_dex_candidate_chains(addr) or []):
                net = _GT_PAIR_SLUG.get(slug)
                if not net:
                    continue
                gd = _gt_get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{addr}")
                base_id = ((((gd or {}).get("data") or {}).get("relationships") or {})
                           .get("base_token") or {}).get("data", {}).get("id", "") or ""
                if "_" in base_id:
                    base_addr = base_id.split("_", 1)[1].strip()
                    if base_addr and base_addr.lower() != addr.lower():
                        token = base_addr
                        logger.info(f"🔗 Pair address resolved via GeckoTerminal (DexScreener "
                                    f"had no pool data): {addr[:10]}… → token {token[:10]}…")
                        break
        except Exception as e:
            logger.debug(f"pair→token GT resolve failed {addr[:12]}…: {e}")
    if len(_pair_token_cache) > 4000:
        _pair_token_cache.clear()
    # Positive resolves are permanent (a pair's base token never changes).
    # Negative resolves expire quickly so a too-early attempt gets retried
    # once DexScreener/GeckoTerminal actually indexes the pair.
    _pair_token_cache[addr] = (token, time.time() + _PAIR_RESOLVE_NEG_TTL)
    return token


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-SOURCE PRICE / MCAP AGGREGATOR
# DexScreener alone misses brand-new or thinly-indexed tokens, which is why some
# KOL calls were skipped or picked up late. When DexScreener has nothing, we now
# fall back — in order — to GeckoTerminal, DexPaprika, Birdeye, Moralis and
# CoinMarketCap. Any source that answers gives us symbol + price + market cap so
# the call gets tracked immediately instead of being dropped.
#
# Railway env vars (all optional; keyless sources always work):
#   BIRDEYE_API_KEY, MORALIS_API_KEY, CMC_API_KEY, EXTRA_SOURCES=1
# ══════════════════════════════════════════════════════════════════════════════
BIRDEYE_API_KEY = os.environ.get("BIRDEYE_API_KEY", "").strip()
MORALIS_API_KEY = os.environ.get("MORALIS_API_KEY", "").strip()
CMC_API_KEY     = (os.environ.get("CMC_API_KEY", "")
                   or os.environ.get("COINMARKETCAP_API_KEY", "")).strip()
EXTRA_SOURCES_ENABLED = os.environ.get("EXTRA_SOURCES", "1").strip().lower() not in ("0", "false", "off")

# chain-key maps per provider
_GT_NET   = {"SOL": "solana", "ETH": "eth", "BNB": "bsc", "BASE": "base", "TON": "ton", "ARC": "arc"}
_DP_NET   = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base", "TON": "ton", "ARC": "arc"}
_BE_CHAIN = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base"}
_MO_EVM   = {"ETH": "eth", "BNB": "bsc", "BASE": "base", "ARC": "arc"}
_SLUG_TO_DISP = {"solana": "SOL", "ethereum": "ETH", "eth": "ETH", "bsc": "BNB",
                 "base": "BASE", "ton": "TON", "robinhood": "RH", "arc": "ARC"}


def _alt_result(chain, symbol, price, mcap, name="", source="", mc_is_fdv=False):
    """Build a dict in the exact shape _parse_dex_pairs() returns.

    mc_is_fdv: True when `mcap` was only obtainable via a fully-diluted-value
    fallback (no real circulating marketCap field was available from this
    provider). FDV is computed off max/total supply, not circulating supply,
    and can read 10-100x+ higher than the token's real market cap for tokens
    with large locked/uncirculated allocations — this is what caused fresh
    listings (e.g. Robinhood-chain tokens) to show as millions in MC when the
    caller's real entry was only thousands. Callers that need entry-MC
    accuracy (e.g. _reconcile_entry_mc) use this flag to avoid trusting an
    FDV-derived number over the KOL's own stated MC.
    """
    try:
        price = float(price or 0)
    except Exception:
        price = 0.0
    try:
        mcap = float(mcap or 0)
    except Exception:
        mcap = 0.0
    if price <= 0 and mcap <= 0 and not symbol:
        return None
    dexpath = CHAIN_TO_DEXPATH.get(chain, "solana") if "CHAIN_TO_DEXPATH" in globals() else "solana"
    return {
        "chain":            chain,
        "mcap":             mcap,
        "mcap_fmt":         fmt_mc(mcap) if mcap > 0 else "N/A",
        "price":            price,
        "symbol":           symbol or "",
        "name":             name or symbol or "",
        "pair_addr":        "",
        "tg_link":          "",
        "price_change_h1":  0.0,
        "price_change_h24": 0.0,
        "volume_h24":       0.0,
        "liquidity_usd":    0.0,
        "txns_buys":        0,
        "txns_sells":       0,
        "listed_at":        None,
        "_partial":         (price <= 0 and mcap <= 0),
        "_source":          source or "alt",
        "_dexpath":         dexpath,
        "_mc_is_fdv":       bool(mc_is_fdv and mcap > 0),
    }


def _alt_chains_for(ca: str) -> list:
    """Display-chain candidates for a contract address."""
    return [_SLUG_TO_DISP.get(s, "SOL") for s in _dex_candidate_chains(ca)
            if s in _SLUG_TO_DISP]


def _src_geckoterminal(ca: str, chains: list):
    for ch in chains:
        net = _GT_NET.get(ch)
        if not net:
            continue
        try:
            r = _dex_session.get(
                f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{ca}",
                headers={"Accept": "application/json"}, timeout=8)
            if r.status_code != 200:
                continue
            a = (r.json().get("data") or {}).get("attributes") or {}
            price   = float(a.get("price_usd") or 0)
            mc_real = float(a.get("market_cap_usd") or 0)
            mc_fdv  = float(a.get("fdv_usd") or 0)
            mc      = mc_real or mc_fdv
            res   = _alt_result(ch, a.get("symbol") or "", price, mc,
                                a.get("name") or "", "geckoterminal",
                                mc_is_fdv=(mc_real <= 0 and mc_fdv > 0))
            if res:
                return res
        except Exception as e:
            logger.debug(f"GeckoTerminal source failed ({ch}): {e}")
    return None


def _src_dexpaprika(ca: str, chains: list):
    for ch in chains:
        net = _DP_NET.get(ch)
        if not net:
            continue
        try:
            r = _dex_session.get(
                f"https://api.dexpaprika.com/networks/{net}/tokens/{ca}",
                headers={"Accept": "application/json"}, timeout=8)
            if r.status_code != 200:
                continue
            d  = r.json() or {}
            sm = d.get("summary") or {}
            price   = float(sm.get("price_usd") or d.get("price_usd") or 0)
            mc_real = float(d.get("market_cap_usd") or sm.get("market_cap_usd") or 0)
            mc_fdv  = float(d.get("fdv") or 0)
            mc      = mc_real or mc_fdv
            res = _alt_result(ch, d.get("symbol") or "", price, mc,
                              d.get("name") or "", "dexpaprika",
                              mc_is_fdv=(mc_real <= 0 and mc_fdv > 0))
            if res:
                return res
        except Exception as e:
            logger.debug(f"DexPaprika source failed ({ch}): {e}")
    return None


def _src_birdeye(ca: str, chains: list):
    if not BIRDEYE_API_KEY:
        return None
    for ch in chains:
        bchain = _BE_CHAIN.get(ch)
        if not bchain:
            continue
        if not _birdeye_rate_budget_ok():
            continue  # account-wide budget used up this minute — skip, try next chain/next tick
        try:
            r = _dex_session.get(
                "https://public-api.birdeye.so/defi/token_overview",
                params={"address": ca},
                headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": bchain,
                         "accept": "application/json"},
                timeout=8)
            if r.status_code != 200:
                continue
            d = (r.json() or {}).get("data") or {}
            if not d:
                continue
            price   = float(d.get("price") or 0)
            mc_real = float(d.get("marketCap") or d.get("mc") or d.get("realMc") or 0)
            mc_fdv  = float(d.get("fdv") or 0)
            mc      = mc_real or mc_fdv
            res = _alt_result(ch, d.get("symbol") or "", price, mc,
                              d.get("name") or "", "birdeye",
                              mc_is_fdv=(mc_real <= 0 and mc_fdv > 0))
            if res:
                return res
        except Exception as e:
            logger.debug(f"Birdeye source failed ({ch}): {e}")
    return None


def _src_moralis(ca: str, chains: list):
    if not MORALIS_API_KEY:
        return None
    hdr = {"X-API-Key": MORALIS_API_KEY, "accept": "application/json"}
    for ch in chains:
        try:
            if ch == "SOL":
                r = _dex_session.get(
                    f"https://solana-gateway.moralis.io/token/mainnet/{ca}/price",
                    headers=hdr, timeout=8)
                if r.status_code != 200:
                    continue
                d = r.json() or {}
                price = float(d.get("usdPrice") or 0)
                sym   = ""
                mc    = 0.0
                try:
                    rm = _dex_session.get(
                        f"https://solana-gateway.moralis.io/token/mainnet/{ca}/metadata",
                        headers=hdr, timeout=8)
                    if rm.status_code == 200:
                        m   = rm.json() or {}
                        sym = m.get("symbol") or ""
                        supply = float(m.get("supply") or 0)
                        dec    = int(m.get("decimals") or 0) if str(m.get("decimals") or "").isdigit() else 0
                        if supply > 0 and price > 0:
                            if dec and supply > 10 ** 12:
                                supply = supply / (10 ** dec)
                            mc = supply * price
                except Exception:
                    pass
                res = _alt_result("SOL", sym, price, mc, sym, "moralis")
                if res:
                    return res
            else:
                evm = _MO_EVM.get(ch)
                if not evm:
                    continue
                r = _dex_session.get(
                    f"https://deep-index.moralis.io/api/v2.2/erc20/{ca}/price",
                    params={"chain": evm}, headers=hdr, timeout=8)
                if r.status_code != 200:
                    continue
                d = r.json() or {}
                price = float(d.get("usdPrice") or 0)
                sym   = (d.get("tokenSymbol") or "")
                res = _alt_result(ch, sym, price, 0, d.get("tokenName") or sym, "moralis")
                if res:
                    return res
        except Exception as e:
            logger.debug(f"Moralis source failed ({ch}): {e}")
    return None


def _src_coinmarketcap(ca: str, chains: list):
    if not CMC_API_KEY:
        return None
    hdr = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    try:
        r = _dex_session.get(
            "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info",
            params={"address": ca}, headers=hdr, timeout=10)
        if r.status_code != 200:
            return None
        data = (r.json() or {}).get("data") or {}
        if not data:
            return None
        cid, info = next(iter(data.items()))
        if isinstance(info, list):
            info = info[0] if info else {}
        sym  = info.get("symbol") or ""
        name = info.get("name") or ""
        rq = _dex_session.get(
            "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest",
            params={"id": cid}, headers=hdr, timeout=10)
        price = mc = 0.0
        if rq.status_code == 200:
            q = ((rq.json() or {}).get("data") or {}).get(str(cid)) or {}
            if isinstance(q, list):
                q = q[0] if q else {}
            usd   = (q.get("quote") or {}).get("USD") or {}
            price = float(usd.get("price") or 0)
            mc    = float(usd.get("market_cap") or usd.get("fully_diluted_market_cap") or 0)
        ch = (chains or ["SOL"])[0]
        return _alt_result(ch, sym, price, mc, name, "coinmarketcap")
    except Exception as e:
        logger.debug(f"CoinMarketCap source failed: {e}")
    return None


def _fetch_alt_sources_sync(ca: str):
    """DexScreener ke fail hone par baaki sources try karo (order = fastest first)."""
    if not ca or not EXTRA_SOURCES_ENABLED:
        return None
    chains = _alt_chains_for(ca) or ["SOL"]
    # Birdeye first ONLY for chain-unambiguous addresses (SOL/TON) — it's a
    # paid, independently-indexed source and far more reliable than the free
    # geckoterminal/dexpaprika endpoints (which often return mc=N/A for
    # fresh/low-cap tokens). For EVM (0x...) addresses the chain itself is a
    # guess across ETH/BSC/Base/Robinhood, and Birdeye just takes the first
    # chain with any data — which can be a wrong/unrelated token on a
    # different chain with a very different MC. So for those, try the
    # chain-neutral sources first and treat Birdeye as a last resort there.
    is_evm_ambiguous = ca.startswith("0x") and len(ca) in (42, 66)
    order = ((_src_geckoterminal, _src_dexpaprika, _src_birdeye,
              _src_moralis, _src_coinmarketcap) if is_evm_ambiguous else
             (_src_birdeye, _src_geckoterminal, _src_dexpaprika,
              _src_moralis, _src_coinmarketcap))
    for fn in order:
        try:
            res = fn(ca, chains)
        except Exception as e:
            logger.debug(f"alt source {getattr(fn, '__name__', '?')} error: {e}")
            res = None
        if res and (res.get("price", 0) > 0 or res.get("mcap", 0) > 0 or res.get("symbol")):
            logger.info(f"🛰️ Fallback source {res.get('_source')} hit for {ca[:12]}… "
                        f"({res.get('symbol','?')} mc={res.get('mcap_fmt','N/A')})")
            return res
    return None


def _fetch_entry_sync(ca: str, chain_guess: str = "SOL", min_liquidity: int = 0) -> dict | None:
    """Entry-price fetch used ONLY for locking in a NEW CALL's entry MC.

    Birdeye-first is safe ONLY for unambiguous single-chain addresses (SOL,
    TON) — there's only one place the token can live, so whatever Birdeye
    returns for that address is correct.

    EVM (0x...) addresses are different: the SAME hex address format is
    valid on Ethereum, BSC, Base AND Robinhood simultaneously, so we have to
    GUESS which of those 4 chains the token is actually on
    (_dex_candidate_chains tries all 4). Birdeye (and geckoterminal/
    dexpaprika) just return the first chain that has ANY data for that
    address — if the same address coincidentally exists as an unrelated
    contract on a different EVM chain, that wrong chain's price/MC gets
    used (this is what produced the 111K-vs-real-22K case). DexScreener
    does NOT have this problem: it validates real liquidity/pairs per chain
    instead of blindly trusting the first hit, so for EVM addresses it stays
    primary. Birdeye is still used — as the alt-source fallback inside
    _fetch_dex_sync — for tokens DexScreener genuinely has nothing on.
    """
    if not ca:
        return None
    is_evm_ambiguous = ca.startswith("0x") and len(ca) in (42, 66)
    if not is_evm_ambiguous:
        # Single-chain address (SOL/TON) — Birdeye first is safe and avoids
        # DexScreener's rate-limit problem entirely.
        chains = _alt_chains_for(ca) or [chain_guess]
        try:
            be_res = _src_birdeye(ca, chains)
        except Exception as e:
            logger.debug(f"Birdeye entry-fetch error: {e}")
            be_res = None
        if be_res and (be_res.get("mcap", 0) > 0 or be_res.get("price", 0) > 0):
            return be_res
    # EVM addresses (chain ambiguous), or Birdeye had nothing yet — use the
    # normal DexScreener + alt-source waterfall, which resolves the chain
    # correctly via real liquidity/pairs.
    try:
        return _fetch_dex_sync(ca, 1, min_liquidity)
    except Exception as e:
        logger.debug(f"DexScreener entry-fetch fallback error: {e}")
        return None


def _better_mc_result(a: dict | None, b: dict | None) -> dict | None:
    """Pick the more trustworthy of two dex-shaped MC reads.

    A real (non-FDV) marketCap always beats an FDV-derived one, regardless of
    which is numerically higher — FDV can be 10-100x+ inflated relative to
    real circulating MC. Within the same tier (both real, or both FDV-only),
    the higher reading wins: indexing lag only ever biases a read LOW, never
    high, so taking the higher of two same-tier reads can never inflate the
    caller's own X in their favor.
    """
    a_mc = (a or {}).get("mcap", 0) or 0
    b_mc = (b or {}).get("mcap", 0) or 0
    if a_mc <= 0:
        return b
    if b_mc <= 0:
        return a
    a_fdv = bool((a or {}).get("_mc_is_fdv"))
    b_fdv = bool((b or {}).get("_mc_is_fdv"))
    if a_fdv and not b_fdv:
        return b
    if b_fdv and not a_fdv:
        return a
    return a if a_mc >= b_mc else b


def _confirm_mc_cross_check(ca: str, chain_guess: str = "SOL") -> dict | None:
    """Re-check a token's MC against an INDEPENDENT provider, not just DexScreener
    asked twice. DexScreener's own indexer can still be settling well after our
    first read, so re-querying only DexScreener risks repeating the exact same
    stale (or FDV-only) number. Returns a dex-shaped dict (mcap/price/symbol)
    or None.

    EVM (0x...) addresses are chain-ambiguous (same address format valid on
    ETH/BSC/Base/Robinhood) — a chain-guessing alt source just returns the
    first chain with any data for that address, which can be a totally
    unrelated token on a different chain with a wildly different MC.
    DexScreener resolves the chain correctly (real liquidity/pairs), so for
    EVM addresses its number is trusted UNLESS it has nothing real to offer
    either — no data at all, OR only an FDV fallback (see _pick_market_cap),
    which is exactly the case this cross-check exists to correct.

    FIX (RH-chain cross-check no-op, 2026-09-24): Birdeye (_BE_CHAIN) has no
    entry at all for "RH" — Birdeye simply does not index the Robinhood
    chain — so `be_res` was always None for every Robinhood-chain call, and
    this whole function silently did nothing but re-ask DexScreener the same
    question it had just asked. GeckoTerminal DOES index the "robinhood"
    network (_GT_NET / _GECKO_NET), so it is now tried as well whenever
    Birdeye comes back empty, giving RH-chain calls an actual independent
    second opinion for the first time.
    """
    dex_res = None
    alt_res = None
    # FIX (2026-10-11, CENTRY "278K shown, real 400K"): while DexScreener was rate-limited
    # (429 breaker open) _fetch_dex_sync() silently answered from its alt sources — mostly a
    # lagging GeckoTerminal quote — and this function then trusted that number as
    # "real, chain-resolved DexScreener MC", so the cross-check just confirmed the stale value.
    # allow_alt=False => dex_res is ONLY ever a genuine DexScreener answer (None under 429).
    try:
        dex_res = _fetch_dex_sync(ca, 1, 0, allow_alt=False)
    except Exception:
        pass
    _alt_cands = []
    try:
        import concurrent.futures as _cf
        _chs = _alt_chains_for(ca) or [chain_guess]
        with _cf.ThreadPoolExecutor(max_workers=2) as _ex:
            _futs = [_ex.submit(_src_birdeye, ca, _chs), _ex.submit(_src_geckoterminal, ca, _chs)]
            for _f in _futs:
                try:
                    _r = _f.result(timeout=6)
                    if _r:
                        _alt_cands.append(_r)
                except Exception:
                    pass
    except Exception:
        pass
    # Provider lag only ever biases a quote LOW, so of the independent alt sources keep the
    # highest real (non-FDV) MC; fall back to any non-empty answer.
    _real = [r for r in _alt_cands if (r.get("mcap", 0) or 0) > 0 and not r.get("_mc_is_fdv")]
    if _real:
        alt_res = max(_real, key=lambda r: r.get("mcap", 0) or 0)
    else:
        alt_res = next((r for r in _alt_cands if (r.get("mcap", 0) or 0) > 0), None) or (_alt_cands[0] if _alt_cands else None)
    dex_mc = (dex_res or {}).get("mcap", 0) or 0
    dex_is_fdv = bool((dex_res or {}).get("_mc_is_fdv"))
    is_evm_ambiguous = ca.startswith("0x") and len(ca) in (42, 66)
    if is_evm_ambiguous:
        if dex_mc > 0 and not dex_is_fdv:
            return dex_res  # real, chain-resolved circulating MC — trust it outright
        return _better_mc_result(alt_res, dex_res)
    return _better_mc_result(dex_res, alt_res)


def _fetch_dex_sync(ca, retries=2, min_liquidity=500, allow_alt=True):
    """Fetch live token data from DexScreener.

    Order of operations:
      1. Fresh positive cache hit (< 20s)  -> return instantly
      2. Fresh negative cache hit (< 45s)  -> return None without a request
      3. /latest/dex/tokens/{ca}           -> primary, all chains
      4. /latest/dex/search?q={ca}         -> fallback for freshly-created pairs
      5. 429 -> exponential backoff, then retry
    """
    import time as _time
    if not ca:
        return None

    cached = _dex_cache.get(ca)
    if cached:
        result, exp = cached
        if _time.time() < exp:
            return result

    miss_exp = _dex_miss_cache.get(ca)
    if miss_exp and _time.time() < miss_exp:
        return None

    for attempt in range(max(1, retries)):
        data = _dex_get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
        if data == 429:
            # Rate-limited (breaker _dex_get me trip ho chuka hai). DexScreener
            # ko sleep kar ke dobara MAT bajao — wahi backoff storms thay jo
            # ticks wedge kar ke calls skip kara rahe thay. Seedha doosri dex
            # sources se data lo.
            logger.warning(f"DexScreener 429 (ca={ca[:12]}…) — alt dex sources try ho rahi hain")
            alt429 = _fetch_alt_sources_sync(ca) if allow_alt else None
            if alt429:
                _dex_cache[ca] = (alt429, _time.time() + _DEX_CACHE_TTL)
                _dex_miss_cache.pop(ca, None)
                _dex_cache_gc()
                return alt429
            # Alt bhi fail — 5s negative cache taake koi storm na bane.
            _dex_miss_cache[ca] = _time.time() + 5
            _dex_cache_gc()
            return None

        pairs = (data or {}).get("pairs") or []

        # Fallback: brand-new pairs sometimes only show on the search endpoint.
        if not pairs:
            sdata = _dex_get(f"https://api.dexscreener.com/latest/dex/search?q={ca}")
            if sdata and sdata != 429:
                pairs = [
                    p for p in (sdata.get("pairs") or [])
                    if ca.lower() in (
                        (p.get("baseToken", {}).get("address", "") or "").lower(),
                        (p.get("quoteToken", {}).get("address", "") or "").lower(),
                    )
                ]

        if pairs:
            result = _parse_dex_pairs(pairs, min_liquidity, ca)
            if result is None:
                # Pairs exist but all below the liquidity floor — retry without it
                # so the UI still shows a live price instead of "N/A".
                result = _parse_dex_pairs(pairs, 0, ca)
        else:
            result = None

        # The /latest endpoint caps its answer at 30 pairs, so a multi-chain
        # token can come back with zero pairs on a chain we support. Ask the
        # chain-scoped v1 endpoint directly for the chains this CA can live on.
        if not result:
            scoped = []
            for chain_slug in _dex_candidate_chains(ca):
                cdata = _dex_get(f"https://api.dexscreener.com/tokens/v1/{chain_slug}/{ca}", timeout=10)
                if cdata == 429:
                    break
                if isinstance(cdata, list) and cdata:
                    scoped.extend(cdata)
            if scoped:
                result = (_parse_dex_pairs(scoped, min_liquidity, ca)
                          or _parse_dex_pairs(scoped, 0, ca))

        if True:
            if result:
                _dex_cache[ca] = (result, _time.time() + _DEX_CACHE_TTL)
                _dex_miss_cache.pop(ca, None)
                _dex_cache_gc()
                return result

        # ── PAIR-ADDRESS FALLBACK ────────────────────────────────────────────
        # Bohat se KOLs (jaise @Big_Whales_Call) dexscreener ka PAIR/POOL link
        # post karte hain: dexscreener.com/solana/<pairAddress>. Wo address
        # token mint nahi hota, is liye /latest/dex/tokens/<addr> khali aata
        # tha aur poori call "no dex data yet" keh kar skip ho jati thi.
        # Yahan us address ko pairs endpoint se resolve kar ke asli baseToken
        # mint nikalte hain, phir usi se normal lookup chalta hai.
        if not result:
            _tok = _resolve_pair_to_token(ca)
            if _tok and _tok.lower() != (ca or "").lower():
                logger.info(f"🔗 Pair address resolved: {ca[:10]}… → token {_tok[:10]}…")
                result = _fetch_dex_sync(_tok, retries=1,
                                         min_liquidity=min_liquidity,
                                         allow_alt=allow_alt)
                if result:
                    result["resolved_ca"] = _tok

        if result:
            _dex_cache[ca] = (result, _time.time() + _DEX_CACHE_TTL)
            _dex_miss_cache.pop(ca, None)
            _dex_cache_gc()
            return result

        # DexScreener has nothing — try the slower alternate providers only on
        # normal lookup/new-call paths. The 1-second monitoring loop passes
        # allow_alt=False so fallback APIs cannot wedge instant alerts.
        alt = _fetch_alt_sources_sync(ca) if allow_alt else None
        if alt:
            _dex_cache[ca] = (alt, _time.time() + _DEX_CACHE_TTL)
            _dex_miss_cache.pop(ca, None)
            _dex_cache_gc()
            return alt

        # Not indexed anywhere yet -> short negative cache, no more retries this round.
        _dex_miss_cache[ca] = _time.time() + _DEX_MISS_TTL
        _dex_cache_gc()
        return None

    return None


_BIRDEYE_BULK_429_UNTIL = 0.0
_BIRDEYE_BULK_COOLDOWN  = int(os.environ.get("BIRDEYE_BULK_429_COOLDOWN_SECONDS", "15") or 15)

def _bulk_refresh_birdeye_sync(cas: list, chain_map: dict) -> int:
    """DexScreener breaker open hone par bulk batch ko Birdeye multi_price se
    refresh karta hai — /defi/multi_price ek request mein 100 tak addresses
    accept karta hai. chain_map: {ca: 'SOL'|'ETH'|'BNB'|'BASE', ...} — jo
    tracked_calls['chain'] se already pata hai, guess nahi karna padta.
    NOTE: this account's key is the FREE tier (60 req/min total, shared with
    _src_birdeye's single-CA calls) — see _birdeye_rate_budget_ok(), which
    every sub-batch request below checks first so this fallback can't blow
    through the account's own limit and start 429-ing itself.
    """
    import time as _time
    if not BIRDEYE_API_KEY or not cas:
        return 0
    global _BIRDEYE_BULK_429_UNTIL
    if _time.time() < _BIRDEYE_BULK_429_UNTIL:
        return 0
    by_chain: dict = {}
    for ca in cas:
        bchain = _BE_CHAIN.get((chain_map or {}).get(ca, "SOL"), "solana")
        by_chain.setdefault(bchain, []).append(ca)
    updated = 0
    for bchain, addrs in by_chain.items():
        for i in range(0, len(addrs), 100):
            sub = addrs[i:i + 100]
            if not _birdeye_rate_budget_ok():
                logger.debug("Birdeye bulk refresh: account budget used up this minute, skipping rest")
                return updated
            try:
                r = _dex_session.get(
                    "https://public-api.birdeye.so/defi/multi_price",
                    params={"list_address": ",".join(sub)},
                    headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": bchain,
                             "accept": "application/json"},
                    timeout=8)
            except Exception as e:
                logger.debug(f"Birdeye bulk refresh network error: {e}")
                continue
            if r.status_code == 429:
                _BIRDEYE_BULK_429_UNTIL = _time.time() + _BIRDEYE_BULK_COOLDOWN
                logger.warning("Birdeye bulk refresh 429 — cooling down, DexScreener single-CA fallback will cover this tick")
                return updated
            if r.status_code != 200:
                continue
            data = (r.json() or {}).get("data") or {}
            disp_chain = _SLUG_TO_DISP.get(bchain, (chain_map or {}).get(sub[0], "SOL"))
            for addr, d in data.items():
                if not d:
                    continue
                price = float(d.get("value") or d.get("price") or 0)
                if price <= 0:
                    continue
                result = _alt_result(disp_chain, d.get("symbol") or "", price,
                                      float(d.get("marketCap") or d.get("mc") or 0),
                                      d.get("name") or "", "birdeye_bulk")
                if result:
                    # Actual tracked chain (not the display slug) so the caller's
                    # symbol/chain assignment on the call stays correct.
                    result["chain"] = (chain_map or {}).get(addr, disp_chain)
                    _dex_cache[addr] = (result, _time.time() + _DEX_CACHE_TTL)
                    _dex_miss_cache.pop(addr, None)
                    updated += 1
    if updated:
        logger.info(f"🐦 Birdeye bulk refresh filled {updated} token(s) while DexScreener breaker was open")
    return updated


def _bulk_refresh_dex_sync(cas: list, min_liquidity: int = 0, chain_map: dict = None) -> int:
    """Refresh the DexScreener cache for many tokens with few requests.

    /latest/dex/tokens/{a,b,c} accepts up to 30 comma-separated addresses, so
    tracking 300 tokens costs ~10 requests instead of 300. This is what makes
    real-time (5 s) tracking possible without hitting rate limits.
    """
    import time as _time
    uniq, seen_local = [], set()
    for ca in cas or []:
        if ca and ca not in seen_local:
            seen_local.add(ca)
            uniq.append(ca)
    updated = 0
    for i in range(0, len(uniq), 30):
        batch = uniq[i:i + 30]
        data = _dex_get(f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}", fresh=True)
        if data == 429:
            # Breaker open ho chuka hai — baaki batches bhi 429 hi hongi.
            # Is batch aur baaki sab ko Birdeye se cover karo (paisay already
            # diye hue hain isi ke liye) taake tokens poori tarah stale na
            # rahein jab tak DexScreener cooldown khatam nahi hota.
            remaining = uniq[i:]
            try:
                updated += _bulk_refresh_birdeye_sync(remaining, chain_map or {})
            except Exception as e_be:
                logger.warning(f"Birdeye bulk fallback failed: {e_be}")
            _now_w = _time.time()
            if _now_w - globals().get("_BULK429_LOG_AT", 0.0) >= 10:
                globals()["_BULK429_LOG_AT"] = _now_w
                logger.warning("DexScreener 429 bulk refresh — is tick ke baaki batches skip "
                               "(breaker open / budget khatam; details: '📊 DexScreener last min' line)")
            break
        pairs = (data or {}).get("pairs") or []
        if not pairs:
            continue
        grouped: dict = {}
        wanted = {c.lower(): c for c in batch}
        for p in pairs:
            # DexScreener's priceUsd belongs to baseToken only. Never assign a
            # quote-token pair to a tracked CA (that tracks SOL/WETH by mistake).
            addr = ((p.get("baseToken") or {}).get("address") or "").lower()
            if addr in wanted:
                grouped.setdefault(wanted[addr], []).append(p)
        for ca, plist in grouped.items():
            result = _parse_dex_pairs(plist, min_liquidity, ca)
            if result:
                _dex_cache[ca] = (result, _time.time() + _DEX_CACHE_TTL)
                _dex_miss_cache.pop(ca, None)
                updated += 1
        if i + 30 < len(uniq):
            _time.sleep(0.08)
    _dex_cache_gc()
    return updated


def _invalidate_dex_cache(ca: str):
    """Force the next fetch to be genuinely fresh.

    IMPORTANT: clear BOTH positive and negative caches. A negative-cache entry
    is what can otherwise make a brand-new caller stay alert_pending for 45s
    even though DexScreener starts returning the token immediately afterwards.
    """
    if not ca:
        return
    _dex_cache.pop(ca, None)
    _dex_miss_cache.pop(ca, None)

def _get_cached_dex(ca: str):
    """Return only the current bulk-refresh result; never perform network I/O.

    The monitoring loop used to call _fetch_dex_sync once per tracked token
    after it had already done a bulk request.  On a cache miss that spawned
    dozens of slow requests (and fallback-provider requests) in the same tick,
    causing the next tick to overlap and making X alerts arrive late.
    """
    if not ca:
        return None
    cached = _dex_cache.get(ca)
    if not cached:
        return None
    result, expiry = cached
    if time.time() >= expiry:
        return None
    return result

async def fetch_dexscreener(ca, *, allow_alt=True, retries=2, min_liquidity=500):
    return await asyncio.to_thread(_fetch_dex_sync, ca, retries, min_liquidity, allow_alt)


def _fetch_token_info_fallback_sync(ca: str, chain_guess: str) -> dict:
    """Fallback token info fetch when DexScreener has no/partial data.
    For SOL tokens : Jupiter → pump.fun → DexScreener search.
    For EVM tokens : GeckoTerminal (specific chain first) → DexScreener search.
    `chain_guess` may be the already-resolved chain ("ETH","BNB","BASE","SOL")
    or the raw guess ("EVM","SOL").
    Returns dict with 'symbol' and optionally 'mcap'/'mcap_fmt'/'chain', or {}.
    """
    # Normalise chain_guess → GeckoTerminal network string + display chain
    _GECKO_NET = {"ETH": "eth", "BNB": "bsc", "BASE": "base", "RH": "robinhood", "ARC": "arc",
                  "ethereum": "eth", "bsc": "bsc", "base": "base", "robinhood": "robinhood", "arc": "arc"}
    _CHAIN_DISP = {"eth": "ETH", "bsc": "BNB", "base": "BASE", "robinhood": "RH", "arc": "ARC"}

    result = {}
    try:
        # ══════════════════════════════════════════════════════════════════
        # SOL path
        # ══════════════════════════════════════════════════════════════════
        if chain_guess in ("SOL", "solana"):
            # 1. Jupiter token list
            try:
                r = requests.get(f"https://tokens.jup.ag/token/{ca}",
                                 headers=HEADERS, timeout=8)
                if r.status_code == 200:
                    d   = r.json()
                    sym = d.get("symbol") or d.get("name") or ""
                    if sym:
                        result["symbol"] = sym
                        result["chain"]  = "SOL"
                        logger.info(f"🔍 Jupiter fallback: symbol={sym} for {ca[:12]}...")
            except Exception as e:
                logger.debug(f"Jupiter fallback failed: {e}")

            # 2. Pump.fun API (covers tokens not yet on Jupiter)
            if not result.get("symbol"):
                try:
                    r = requests.get(f"https://frontend-api.pump.fun/coins/{ca}",
                                     headers=HEADERS, timeout=8)
                    if r.status_code == 200:
                        d   = r.json()
                        sym = d.get("symbol") or ""
                        mc  = float(d.get("market_cap") or 0)
                        if sym:
                            result["symbol"] = sym
                            result["chain"]  = "SOL"
                            if mc > 0:
                                result["mcap"]     = mc
                                result["mcap_fmt"] = fmt_mc(mc)
                            logger.info(f"🔍 Pump.fun fallback: sym={sym} mc={mc} for {ca[:12]}...")
                except Exception as e:
                    logger.debug(f"Pump.fun fallback failed: {e}")

            # 3. DexScreener search endpoint (catches tokens with symbol but no direct hit)
            if not result.get("symbol") or not result.get("mcap"):
                try:
                    r = requests.get(
                        f"https://api.dexscreener.com/latest/dex/search?q={ca}",
                        headers=HEADERS, timeout=10)
                    if r.status_code == 200:
                        for p in (r.json().get("pairs") or []):
                            if p.get("baseToken", {}).get("address", "").lower() != ca.lower():
                                continue
                            sym = p.get("baseToken", {}).get("symbol", "")
                            mc  = float(p.get("marketCap") or 0)
                            if sym:
                                if not result.get("symbol"):
                                    result["symbol"] = sym
                                if mc > 0 and not result.get("mcap"):
                                    result["mcap"]     = mc
                                    result["mcap_fmt"] = fmt_mc(mc)
                                result.setdefault("chain", SUPPORTED_CHAINS.get(
                                    p.get("chainId","").lower(), "SOL"))
                                logger.info(f"🔍 DEX search SOL fallback: sym={sym} mc={mc} for {ca[:12]}...")
                                break
                except Exception as e:
                    logger.debug(f"DexScreener SOL search fallback failed: {e}")

        # ══════════════════════════════════════════════════════════════════
        # TON path
        # ══════════════════════════════════════════════════════════════════
        elif chain_guess == "TON":
            # DexScreener supports TON directly
            try:
                r = requests.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
                    headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    for p in (r.json().get("pairs") or []):
                        if p.get("chainId","").lower() != "ton":
                            continue
                        sym = p.get("baseToken",{}).get("symbol","")
                        mc  = float(p.get("marketCap") or 0)
                        if sym:
                            result["symbol"] = sym
                            result["chain"]  = "TON"
                            if mc > 0:
                                result["mcap"]     = mc
                                result["mcap_fmt"] = fmt_mc(mc)
                            logger.info(f"🔍 DexScreener TON: sym={sym} mc={mc} for {ca[:12]}...")
                            break
            except Exception as e:
                logger.debug(f"DexScreener TON fallback failed: {e}")

            # GeckoTerminal TON fallback
            if not result.get("symbol"):
                try:
                    r = requests.get(
                        f"https://api.geckoterminal.com/api/v2/networks/ton/tokens/{ca}",
                        headers={"Accept": "application/json"}, timeout=8)
                    if r.status_code == 200:
                        attr = r.json().get("data", {}).get("attributes", {})
                        sym  = attr.get("symbol") or attr.get("name") or ""
                        mc   = float(attr.get("market_cap_usd") or 0)
                        if sym:
                            result["symbol"] = sym
                            result["chain"]  = "TON"
                            if mc > 0:
                                result["mcap"]     = mc
                                result["mcap_fmt"] = fmt_mc(mc)
                            logger.info(f"🔍 GeckoTerminal TON: sym={sym} mc={mc} for {ca[:12]}...")
                except Exception as e:
                    logger.debug(f"GeckoTerminal TON fallback failed: {e}")

        # ══════════════════════════════════════════════════════════════════
        # EVM path  (ETH / BNB / BASE — or unknown "EVM")
        # ══════════════════════════════════════════════════════════════════
        else:
            # Determine which networks to try, most-likely first
            # RH (Robinhood) is EVM-compatible; GeckoTerminal uses "robinhood" network key
            _GECKO_NET["RH"] = "robinhood"
            known_net = _GECKO_NET.get(chain_guess)
            if known_net:
                nets_to_try = [known_net] + [n for n in ("eth", "bsc", "base", "robinhood")
                                              if n != known_net]
            else:
                nets_to_try = ["eth", "bsc", "base", "robinhood"]

            # 1. GeckoTerminal token endpoint — often has fdv_usd / market_cap_usd
            for network in nets_to_try:
                if result.get("symbol") and result.get("mcap"):
                    break
                try:
                    r = requests.get(
                        f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{ca}",
                        headers={"Accept": "application/json"}, timeout=8)
                    if r.status_code != 200:
                        continue
                    attr = r.json().get("data", {}).get("attributes", {})
                    sym  = attr.get("symbol") or attr.get("name") or ""
                    mc   = float(attr.get("market_cap_usd") or 0)
                    if sym:
                        if not result.get("symbol"):
                            result["symbol"] = sym
                            result["chain"]  = _CHAIN_DISP.get(network, "EVM")
                        if mc > 0 and not result.get("mcap"):
                            result["mcap"]     = mc
                            result["mcap_fmt"] = fmt_mc(mc)
                        logger.info(
                            f"🔍 GeckoTerminal ({network}): sym={sym} mc={mc} for {ca[:12]}...")
                except Exception as e:
                    logger.debug(f"GeckoTerminal {network} failed: {e}")

            # 2. GeckoTerminal top pools for the token — pools have reliable fdv/mc data
            if not result.get("mcap"):
                for network in (nets_to_try if result.get("chain") is None
                                else [_GECKO_NET.get(result["chain"], "eth")]):
                    try:
                        r = requests.get(
                            f"https://api.geckoterminal.com/api/v2/networks/{network}"
                            f"/tokens/{ca}/pools?page=1",
                            headers={"Accept": "application/json"}, timeout=8)
                        if r.status_code != 200:
                            continue
                        for pool in (r.json().get("data") or []):
                            attr = pool.get("attributes", {})
                            mc   = float(attr.get("market_cap_usd") or 0)
                            sym  = (attr.get("name") or "").split(" / ")[0].strip()
                            if mc > 0:
                                if not result.get("mcap"):
                                    result["mcap"]     = mc
                                    result["mcap_fmt"] = fmt_mc(mc)
                                if sym and not result.get("symbol"):
                                    result["symbol"] = sym
                                result.setdefault("chain", _CHAIN_DISP.get(network, "EVM"))
                                logger.info(
                                    f"🔍 GeckoTerminal pools ({network}): sym={sym} mc={mc} for {ca[:12]}...")
                                break
                        if result.get("mcap"):
                            break
                    except Exception as e:
                        logger.debug(f"GeckoTerminal pools {network} failed: {e}")

            # 3. DexScreener search — catches tokens where direct endpoint had no MC
            if not result.get("symbol") or not result.get("mcap"):
                try:
                    r = requests.get(
                        f"https://api.dexscreener.com/latest/dex/search?q={ca}",
                        headers=HEADERS, timeout=10)
                    if r.status_code == 200:
                        for p in (r.json().get("pairs") or []):
                            if p.get("baseToken", {}).get("address", "").lower() != ca.lower():
                                continue
                            chain_id = p.get("chainId", "").lower()
                            if chain_id not in SUPPORTED_CHAINS:
                                continue
                            sym = p.get("baseToken", {}).get("symbol", "")
                            mc  = float(p.get("marketCap") or 0)
                            if sym and not result.get("symbol"):
                                result["symbol"] = sym
                                result["chain"]  = SUPPORTED_CHAINS[chain_id]
                            if mc > 0 and not result.get("mcap"):
                                result["mcap"]     = mc
                                result["mcap_fmt"] = fmt_mc(mc)
                            if result.get("symbol") and result.get("mcap"):
                                break
                        if result.get("symbol") or result.get("mcap"):
                            logger.info(
                                f"🔍 DEX search EVM fallback: sym={result.get('symbol')} "
                                f"mc={result.get('mcap_fmt')} for {ca[:12]}...")
                except Exception as e:
                    logger.debug(f"DexScreener EVM search fallback failed: {e}")

    except Exception as e:
        logger.warning(f"_fetch_token_info_fallback_sync crash: {e}")

    return result

def _extract_tg_link(links):
    """Find a Telegram group/channel link from DexScreener links list."""
    if not links: return ""
    for link in links:
        url = link.get("url","")
        ltype = link.get("type","").lower()
        label = link.get("label","").lower()
        if "t.me" in url or "telegram" in ltype or "telegram" in label:
            return url
    return ""

def _extract_tg_link_from_pairs(ca):
    """Fallback: get TG link from DexScreener pair data."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}", timeout=10, headers=HEADERS)
        if r.status_code != 200: return ""
        for pair in (r.json().get("pairs") or []):
            info = pair.get("info", {}) or {}
            for s in (info.get("socials") or []):
                u = s.get("url",""); t = s.get("type","").lower()
                if "t.me" in u or "telegram" in t: return u
            for lnk in (info.get("links") or []):
                u = lnk.get("url",""); t = lnk.get("type","").lower()
                if "t.me" in u or "telegram" in t: return u
    except Exception: pass
    return ""

def _gecko_get_json(url: str, tries: int = 3):
    """GeckoTerminal GET with retries.

    GeckoTerminal often answers HTTP 200 with a rate-limit body
    ({"status": {"error_code": 429}}). The old code treated that as valid data,
    so SOL / RH trending silently came back empty. Now we detect it and retry.
    """
    GECKO_HEADERS = {"Accept": "application/json"}
    for attempt in range(max(1, tries)):
        try:
            r = requests.get(url, headers=GECKO_HEADERS, timeout=15)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1)); continue
            if r.status_code != 200:
                logger.warning(f"Gecko {url.split('/networks/')[-1][:24]}: HTTP {r.status_code}")
                return None
            data = r.json()
            err = ((data or {}).get("status") or {}).get("error_code")
            if err:
                logger.warning(f"Gecko rate-limited ({err}) — retry {attempt+1}")
                time.sleep(2.0 * (attempt + 1)); continue
            if not (data or {}).get("data"):
                time.sleep(1.0 * (attempt + 1)); continue
            return data
        except Exception as e:
            logger.warning(f"Gecko fetch failed: {e}")
            time.sleep(1.0 * (attempt + 1))
    return None


def _fetch_gecko_chain(gecko_network: str, dex_chain_key: str, blacklist: set) -> list:
    """Fetch top-5 trending tokens for one chain via GeckoTerminal.
    Returns list of {symbol, mc_fmt, tg_url, dex_url, has_tg} dicts.
    """
    GECKO_HEADERS = {"Accept": "application/json"}
    DEXPATH = {"ETH": "ethereum", "BNB": "bsc", "BASE": "base", "SOL": "solana"}
    results = []
    data = _gecko_get_json(
        f"https://api.geckoterminal.com/api/v2/networks/{gecko_network}/trending_pools"
        f"?page=1&include=base_token")
    if not data:
        return results

    # Build included base_token lookup: id -> {symbol, address, telegram}
    # Accept ANY type — GeckoTerminal has used "token" and "base_token" at different times
    included_map: dict = {}
    for item in (data.get("included") or []):
        attr = item.get("attributes", {})
        item_id = item.get("id", "")
        if not item_id: continue
        included_map[item_id] = {
            "symbol":   attr.get("symbol", ""),
            "address":  attr.get("address", ""),
            "telegram": attr.get("telegram_chat_url") or "",
        }

    def _ca_from_rel_id(rel_id: str) -> str:
        """GeckoTerminal rel IDs are like 'eth_0xabc…' or 'base_0xabc…'.
        Strip the network prefix to get the raw contract address as fallback."""
        if not rel_id: return ""
        if "_" in rel_id:
            parts = rel_id.split("_", 1)
            candidate = parts[1]
            # Basic sanity: looks like an address (hex) or Solana pubkey (base58)
            if len(candidate) > 20:
                return candidate
        return ""

    seen_tokens: set = set()
    for pool in (data.get("data") or []):
        attr = pool.get("attributes", {})

        # Market cap / FDV — try all available fields; fall back to volume as a last resort
        mc_raw = (attr.get("market_cap_usd") or attr.get("fdv_usd")
                  or attr.get("reserve_in_usd") or 0)
        try: mc = float(mc_raw)
        except Exception: mc = 0.0
        # If still zero, use 24h volume as a rough proxy so the pool isn't silently dropped
        if mc <= 0:
            try: mc = float(attr.get("volume_usd", {}).get("h24") or 0)
            except Exception: mc = 0.0
        if mc <= 0: continue

        # Get base token info — try included_map first, then parse rel_id as fallback
        rel_id = (
            pool.get("relationships", {})
            .get("base_token", {})
            .get("data", {})
            .get("id", "")
        )
        token_info = included_map.get(rel_id, {})
        ca  = token_info.get("address", "") or _ca_from_rel_id(rel_id)
        sym = (token_info.get("symbol")
               or attr.get("name", "TOKEN").split(" / ")[0]
               or attr.get("base_token_symbol", "TOKEN"))

        if not ca or ca.lower() in blacklist: continue
        if ca in seen_tokens: continue
        seen_tokens.add(ca)

        tg  = token_info.get("telegram", "")
        dex = f"https://dexscreener.com/{DEXPATH.get(dex_chain_key, gecko_network)}/{ca}"
        results.append({
            "symbol":  sym,
            "mc_fmt":  fmt_mc(mc),
            "tg_url":  tg,
            "dex_url": dex,
            "has_tg":  bool(tg),
            "_ca":     ca,   # temp field for DexScreener TG supplement
        })
        if len(results) >= 10: break  # collect up to 10, sort below

    # ── Supplement missing TG links via DexScreener batch lookup ─────────────
    no_tg = [r for r in results if not r["tg_url"] and r.get("_ca")]
    if no_tg:
        batch_cas = [r["_ca"] for r in no_tg]
        try:
            rs = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch_cas)}",
                headers=HEADERS, timeout=15
            )
            if rs.status_code == 200:
                tg_map: dict = {}
                for p in (rs.json().get("pairs") or []):
                    ca_key = p.get("baseToken", {}).get("address", "")
                    if ca_key and ca_key not in tg_map:
                        info = p.get("info") or {}
                        for s in (info.get("socials") or []):
                            u = s.get("url", ""); t = s.get("type", "").lower()
                            if "t.me" in u or "telegram" in t:
                                tg_map[ca_key] = u; break
                        if ca_key not in tg_map:
                            for lnk in (info.get("links") or []):
                                u = lnk.get("url", ""); t = lnk.get("type", "").lower()
                                if "t.me" in u or "telegram" in t:
                                    tg_map[ca_key] = u; break
                for r in results:
                    if not r["tg_url"] and r.get("_ca") and r["_ca"] in tg_map:
                        r["tg_url"] = tg_map[r["_ca"]]
                        r["has_tg"] = True
        except Exception as e:
            logger.warning(f"DexScreener TG supplement {gecko_network}: {e}")

    # Remove temp field
    for r in results: r.pop("_ca", None)

    # TG-first sort, return top 5
    results.sort(key=lambda x: (0 if x["has_tg"] else 1))
    return results[:5]


def _inject_pinned_tokens(chain_tokens: dict):
    """Mutate chain_tokens in-place: insert owner-pinned tokens at position 0.
    Pins expire after 24 hours. Fetches live MC from DexScreener for each pinned CA.
    Works for both old posts (SOL/ETH/BNB/BASE) and new posts (SOL/ETH/BSC/RH/BASE/TON)."""
    try:
        pins = load_pinned_trending()
        now  = datetime.utcnow()
        changed = False
        for chain_key, pin in list(pins.items()):
            try:
                pinned_at = datetime.fromisoformat(pin.get("pinned_at",""))
                if now - pinned_at > timedelta(hours=24):
                    del pins[chain_key]; changed = True; continue
            except Exception: pass
            ca      = pin.get("ca","")
            tg_link = pin.get("tg_link","")
            sym     = pin.get("symbol","PIN")
            mc_fmt  = pin.get("mc_fmt","N/A")
            dex_url = pin.get("dex_url","")

            # Fetch live MC from DexScreener
            try:
                r = requests.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{ca}",
                    timeout=10, headers=HEADERS)
                if r.status_code == 200:
                    pairs = r.json().get("pairs") or []
                    if pairs:
                        best = max(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0))
                        mc_raw = float(best.get("marketCap") or best.get("fdv") or 0)
                        if mc_raw > 0:
                            mc_fmt = fmt_mc(mc_raw)
                            sym    = best.get("baseToken",{}).get("symbol", sym)
                        if not dex_url:
                            # Build dex_url from pair chain info
                            pair_chain = best.get("chainId","").lower()
                            dex_url = f"https://dexscreener.com/{pair_chain}/{ca}"
                        # Update stored mc_fmt and symbol for next cycle
                        pin["mc_fmt"]  = mc_fmt
                        pin["symbol"]  = sym
                        pin["dex_url"] = dex_url
                        changed = True
            except Exception:
                pass

            pinned_token = {
                "symbol": sym, "mc_fmt": mc_fmt,
                "tg_url": tg_link, "dex_url": dex_url, "has_tg": bool(tg_link),
                "_pinned": True,
            }
            # Insert at index 0 for every variant of the chain key that matches
            for ck in (chain_tokens if isinstance(chain_tokens, dict) else {}).keys():
                if ck.upper() == chain_key.upper():
                    lst = list(chain_tokens[ck])
                    # Remove any old pinned entry first
                    lst = [t for t in lst if not t.get("_pinned")]
                    chain_tokens[ck] = [pinned_token] + lst
                    break

        if changed:
            save_pinned_trending(pins)
    except Exception as e:
        logger.warning(f"_inject_pinned_tokens: {e}")


def _fetch_trending_sync():
    """Fetch trending tokens.
    SOL  → DexScreener token-boosts (works well for Solana).
    ETH / BNB / BASE → GeckoTerminal trending_pools (reliable per-chain endpoint).
    """
    chain_tokens: dict = {"ETH": [], "BNB": [], "SOL": [], "BASE": []}
    blacklist = load_trending_blacklist()

    # ── SOL: DexScreener boosts (unchanged, works perfectly) ─────────────────
    try:
        resp = requests.get(
            "https://api.dexscreener.com/token-boosts/top/v1",
            timeout=20, headers=HEADERS
        )
        if resp.status_code == 200:
            raw = resp.json()
            if isinstance(raw, list):
                sol_candidates = []
                seen = set()
                for token in raw:
                    if token.get("chainId", "").lower() != "solana": continue
                    ca = token.get("tokenAddress", "")
                    if not ca or ca in seen or ca.lower() in blacklist: continue
                    seen.add(ca)
                    tg = _extract_tg_link(token.get("links") or [])
                    sol_candidates.append((ca, tg))

                # Batch pairs lookup for SOL
                sol_results = []
                for batch_start in range(0, min(len(sol_candidates), 60), 10):
                    batch = sol_candidates[batch_start: batch_start + 10]
                    cas   = [ca for ca, _ in batch]
                    tg_map = {ca: tg for ca, tg in batch}
                    try:
                        r = requests.get(
                            f"https://api.dexscreener.com/latest/dex/tokens/{','.join(cas)}",
                            timeout=15, headers=HEADERS
                        )
                        if r.status_code != 200: continue
                        pairs_data = r.json().get("pairs") or []
                    except Exception as e:
                        logger.warning(f"SOL batch pairs failed: {e}"); continue

                    best_per_ca: dict = {}
                    for pair in pairs_data:
                        ca_key = pair.get("baseToken", {}).get("address", "")
                        if ca_key not in best_per_ca:
                            best_per_ca[ca_key] = pair
                        else:
                            liq_new = pair.get("liquidity", {}).get("usd", 0) or 0
                            liq_old = best_per_ca[ca_key].get("liquidity", {}).get("usd", 0) or 0
                            if liq_new > liq_old:
                                best_per_ca[ca_key] = pair
                    for ca in cas:
                        pair = best_per_ca.get(ca)
                        if not pair: continue
                        mc = float(pair.get("marketCap") or pair.get("fdv") or 0)
                        if mc <= 0: continue
                        tg = tg_map.get(ca, "")
                        if not tg:
                            info = pair.get("info") or {}
                            for s in (info.get("socials") or []):
                                u = s.get("url",""); t = s.get("type","").lower()
                                if "t.me" in u or "telegram" in t: tg = u; break
                        sol_results.append({
                            "symbol":  pair.get("baseToken", {}).get("symbol", "TOKEN"),
                            "mc_fmt":  fmt_mc(mc),
                            "tg_url":  tg,
                            "dex_url": f"https://dexscreener.com/solana/{ca}",
                            "has_tg":  bool(tg),
                        })
                    time.sleep(0.2)

                sol_results.sort(key=lambda x: (0 if x["has_tg"] else 1))
                chain_tokens["SOL"] = sol_results[:5]
    except Exception as e:
        logger.warning(f"SOL trending fetch failed: {e}")

    # ── ETH / BNB / BASE: GeckoTerminal (reliable, per-chain trending) ───────
    gecko_map = [
        ("eth",  "ETH"),
        ("bsc",  "BNB"),
        ("base", "BASE"),
    ]
    for gecko_net, chain_key in gecko_map:
        try:
            chain_tokens[chain_key] = _fetch_gecko_chain(gecko_net, chain_key, blacklist)
        except Exception as e:
            logger.warning(f"GeckoTerminal {gecko_net} failed: {e}")
        time.sleep(0.3)  # polite pause between chain calls

    # ── Inject pinned tokens at position 0 for their chain ─────────────────────
    _inject_pinned_tokens(chain_tokens)
    return chain_tokens

async def fetch_trending():
    return await asyncio.to_thread(_fetch_trending_sync)

def _calc_trending_kols():
    """Top 10 tracked channels sorted by highest X milestone (descending).
    Respects trending_kols_reset_since — only counts calls tracked AFTER last reset.
    Returns list of dicts: {channel, best_x, wizard_post_id}"""
    channels = load_channels()

    cfg_now = load_config()
    # Primary exclusion: call_keys snapshot at reset time (most reliable)
    excluded_keys = set(cfg_now.get("lb_excluded_call_keys", []))
    # Secondary: date-based filter (catches calls added after reset but before snapshot)
    tk_reset_since = cfg_now.get("trending_kols_reset_since", "")
    tk_reset_dt = None
    if tk_reset_since:
        try: tk_reset_dt = datetime.fromisoformat(tk_reset_since)
        except Exception: tk_reset_dt = None

    scores = []
    for ch in channels:
        best_x = 0
        best_call_key = None
        for call_key, call in tracked_calls.items():
            if call.get("channel", "").lower() != ch.lower(): continue
            # Frozen calls must not count toward the trending-KOLs ranking.
            if call.get("frozen"): continue
            if _is_deleted_blocked(call): continue
            # FIX: an OLD call that pumps TODAY must still count. Previously any
            # call tracked before the last reset (or present in the reset
            # snapshot) was dropped completely, so KOLs who X-ed today never
            # showed up in the trending-KOL list. Now the X timestamp decides.
            _is_old = (call_key in excluded_keys)
            if not _is_old and tk_reset_dt:
                ts_str = call.get("tracked_since", "")
                try:
                    _is_old = bool(ts_str) and datetime.fromisoformat(ts_str) < tk_reset_dt
                except Exception:
                    _is_old = True
            if _is_old:
                ms = _milestones_since(call_key, tk_reset_dt)
                if not ms:
                    continue
            else:
                ms = list(sent_milestones.get(call_key, set()))
            mx = display_x(max(ms)) if ms else 0
            # also honour a recorded ATH peak that never got an alert
            _pk = display_x(int(call_peak_ratio(call)))
            if _pk > mx:
                mx = _pk
            if mx > best_x:
                best_x = mx
                best_call_key = call_key
        # Only include channels that have reached at least 2x after reset
        if best_x < 2:
            continue
        # Get WizardScan post ID for this channel's highest X milestone
        wizard_post_id = None
        if best_call_key:
            posts = milestone_posts.get(best_call_key, {})
            wizard_post_id = posts.get(str(best_x))
        scores.append({"channel": ch, "best_x": best_x, "wizard_post_id": wizard_post_id})
    scores.sort(key=lambda x: x["best_x"], reverse=True)
    return scores[:10]

# ─── Channel scraping ─────────────────────────────────────────────────────────
def _fetch_posts_sync(channel):
    try:
        resp = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=15)
        if resp.status_code != 200: return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        posts = []
        for div in soup.find_all("div", class_="tgme_widget_message"):
            attr   = div.get("data-post","")
            msg_id = attr.split("/")[-1] if "/" in attr else attr
            td     = div.find("div", class_="tgme_widget_message_text")
            text   = td.get_text(separator="\n") if td else ""
            time_el = div.find("time")
            post_date = time_el.get("datetime") if time_el else None
            posts.append({"id": msg_id, "text": text, "date": post_date})
        return posts
    except Exception as e:
        logger.error(f"Fetch {channel}: {e}"); return []

import time as _t
# Channels currently under Telegram FloodWait — scanning them again too soon
# gets the whole userbot rate-limited and silently kills call detection.
_FLOOD_UNTIL: dict = {}
_SCAN_CURSOR = {"i": 0}

# Channels whose iter_messages call itself hung/timed out. A SINGLE bad
# channel (banned/kicked/broken peer) must NOT take down the whole userbot
# connection — that was causing a reconnect-storm where the client kept
# reconnecting only to immediately re-hang on the same bad channel, starving
# every other channel of scan time. Skip just this channel for a cooldown.
_TIMEOUT_UNTIL: dict = {}
_TIMEOUT_COOLDOWN_SECONDS = int(os.environ.get("TIMEOUT_COOLDOWN_SECONDS", "60") or 60)
# Only treat it as a genuinely dead SOCKET (worth a full reconnect) if
# several DIFFERENT channels time out within a short rolling window.
_RECENT_TIMEOUTS: list = []
_DISTINCT_TIMEOUT_WINDOW_SECONDS = 15
_DISTINCT_TIMEOUT_THRESHOLD = int(os.environ.get("DISTINCT_TIMEOUT_THRESHOLD", "8") or 8)

# Debounce so a burst of simultaneous per-channel timeouts (common — many
# channels stall together when the underlying MTProto socket dies) triggers
# only ONE reconnect attempt, not one per channel.
_LAST_RECONNECT_KICK = {"t": 0.0}
_RECONNECT_DEBOUNCE_SECONDS = 10

def _kick_userbot_reconnect(reason: str):
    """Fire-and-forget immediate reconnect. Without this, a dead MTProto
    socket is only noticed by userbot_watchdog_job (every few minutes), and
    every scan/monitoring tick in between silently returns no data — this is
    what caused calls to get skipped for long stretches instead of erroring
    loudly. init_userbot() already holds its own lock and no-ops if a live
    connection exists, so calling it opportunistically here is safe even if
    several channels time out in the same tick."""
    now = _t.time()
    if now - _LAST_RECONNECT_KICK["t"] < _RECONNECT_DEBOUNCE_SECONDS:
        return
    _LAST_RECONNECT_KICK["t"] = now
    logger.warning(f"🔄 Kicking immediate userbot reconnect (reason: {reason})")
    asyncio.create_task(_immediate_userbot_reconnect(), name="userbot-immediate-reconnect")

async def _immediate_userbot_reconnect():
    try:
        await init_userbot()
        if userbot_client and userbot_client.is_connected():
            logger.info("✅ Immediate reconnect succeeded — realtime monitoring resuming")
    except Exception as e:
        logger.warning(f"Immediate reconnect attempt failed: {type(e).__name__}: {e}")


async def _fetch_posts_via_userbot(channel: str, limit: int = 50, min_id: int = 0) -> list:
    """REALTIME-GRADE: Fetch recent posts via Telethon MTProto instead of t.me/s scraping.
    Much faster, more reliable, fetches full message text including hidden links.
    Falls back to t.me/s if userbot unavailable.

    min_id (added 2026-09-18, missed-posts bug): when >0, fetches EVERY message
    newer than this ID (Telethon's min_id), not just the newest `limit` of them.
    Without this, a channel that posted more than `limit` times during a scan
    gap (flood-wait, timeout, busy queue) permanently lost the older posts in
    that burst — they simply fell outside the "last N" snapshot and were never
    fetched again. `limit` still applies as a safety cap for a very long outage."""
    global userbot_client
    if not userbot_client or not userbot_client.is_connected():
        # Don't just silently return empty every tick until the 5-min watchdog
        # notices — kick a reconnect attempt right away (debounced).
        _kick_userbot_reconnect("client missing/disconnected on scan")
        return []
    # Skip channels that recently hung — no point retrying them every 2s and
    # burning the scan_job time budget / triggering another false reconnect.
    until = _TIMEOUT_UNTIL.get(channel.lower())
    if until and _t.time() < until:
        return []
    async def _collect():
        posts = []
        _kwargs = {"limit": limit}
        if min_id:
            _kwargs["min_id"] = min_id
        async for msg in userbot_client.iter_messages(channel, **_kwargs):
            if not msg: continue
            msg_id = str(msg.id)
            raw_text = msg.text or msg.message or ""
            # Also try to get caption from media
            if not raw_text and hasattr(msg, 'caption') and msg.caption:
                raw_text = msg.caption
            # Extract text from message entities (inline buttons etc have URLs)
            if msg.entities:
                try:
                    from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
                    for ent in msg.entities:
                        if isinstance(ent, MessageEntityTextUrl):
                            raw_text += f" {ent.url}"
                        elif isinstance(ent, MessageEntityUrl):
                            offset, length = ent.offset, ent.length
                            raw_text += f" {msg.message[offset:offset+length]}" if msg.message else ""
                except Exception:
                    pass
            posts.append({"id": msg_id, "text": raw_text,
                          "date": msg.date.isoformat() if getattr(msg, "date", None) else None})
        return posts
    try:
        # HARD TIMEOUT: iter_messages() can hang forever if the MTProto socket
        # is silently dead (is_connected() still reports True). Without this,
        # a single stalled connection wedges scan_job permanently — every
        # future tick then gets skipped ("maximum number of running instances
        # reached") until the process is restarted. This was the root cause
        # of multi-hour alert delays.
        return await asyncio.wait_for(
            _collect(),
            timeout=float(os.environ.get("USERBOT_FETCH_TIMEOUT_SECONDS", "5") or 5))
    except asyncio.TimeoutError:
        ch_key = channel.lower()
        _TIMEOUT_UNTIL[ch_key] = _t.time() + _TIMEOUT_COOLDOWN_SECONDS
        now = _t.time()
        _RECENT_TIMEOUTS.append((now, ch_key))
        # keep only recent, distinct-channel timeouts in the rolling window
        del _RECENT_TIMEOUTS[:-50]  # bound memory
        recent = [c for t, c in _RECENT_TIMEOUTS if now - t <= _DISTINCT_TIMEOUT_WINDOW_SECONDS]
        distinct_recent = set(recent)

        if len(distinct_recent) >= _DISTINCT_TIMEOUT_THRESHOLD:
            # Several DIFFERENT channels hung within a few seconds — that's
            # the real socket-dead signature. Reconnect the whole client.
            logger.warning(f"⏱️ Userbot iter_messages timed out @{channel} — "
                            f"{len(distinct_recent)} channels hung recently, forcing reconnect")
            try:
                await userbot_client.disconnect()
            except Exception:
                pass
            _kick_userbot_reconnect(f"iter_messages timeout @{channel} ({len(distinct_recent)} channels hung)")
        else:
            # Just this one channel — likely banned/kicked/broken peer.
            # Skip it for a cooldown, leave the live connection alone so
            # every OTHER channel keeps scanning normally.
            logger.warning(f"⏱️ Userbot iter_messages timed out @{channel} — "
                            f"skipping this channel for {_TIMEOUT_COOLDOWN_SECONDS}s (connection kept alive)")
        return []
    except Exception as e:
        name = type(e).__name__
        if "FloodWait" in name:
            secs = getattr(e, "seconds", 0) or 0
            _FLOOD_UNTIL[channel.lower()] = _t.time() + secs + 2
            _FLOOD_UNTIL["__global__"] = _t.time() + min(secs, 60)
            logger.warning(f"⏳ FloodWait {secs}s @{channel} — scan paused for this channel")
        else:
            _msg_lower = str(e).lower()
            # FIX (2026-09-17): "No user has X as username" / "you lack
            # permission" / "banned from it" are PERMANENT until the owner
            # fixes the channel (renamed KOL, bot removed/banned) — not a
            # transient glitch. Before this, these got retried on EVERY
            # single 8s scan_job tick forever, burning a GetHistoryRequest
            # call each time and contributing to the Telegram flood-wait
            # storm that was delaying every other channel's posts. Reuse the
            # existing _TIMEOUT_UNTIL cooldown (already checked above) with a
            # much longer window — cheap to retry occasionally in case the
            # owner fixes the channel, without hammering it every tick.
            if any(m in _msg_lower for m in
                   ("no user has", "you lack permission", "banned from it")):
                _PERM_ERROR_COOLDOWN = float(os.environ.get("PERM_ERROR_COOLDOWN_SECONDS", "1800") or 1800)
                _TIMEOUT_UNTIL[channel.lower()] = _t.time() + _PERM_ERROR_COOLDOWN
                logger.warning(f"Userbot fetch @{channel}: {e} — "
                                f"pausing this channel for {int(_PERM_ERROR_COOLDOWN)}s")
            else:
                logger.warning(f"Userbot fetch @{channel}: {e}")
        return []

async def fetch_channel_posts(channel):
    """Fetch recent posts. Prefers Telethon MTProto (fast/reliable), falls back to t.me/s scraping."""
    # Try Telethon first — like SpyDefi/Kolscope, direct MTProto is far more reliable
    now = _t.time()
    if _FLOOD_UNTIL.get(channel.lower(), 0) > now or _FLOOD_UNTIL.get("__global__", 0) > now:
        return await asyncio.to_thread(_fetch_posts_sync, channel)
    if userbot_client and userbot_client.is_connected():
        # Gap-proof cursor (2026-09-18): derive min_id from the highest message
        # ID we've ever actually seen for this channel, so a scan after a gap
        # (flood-wait, timeout, busy tick) fetches EVERYTHING new since then —
        # not just the last N. limit is now a safety cap for very long gaps,
        # not the everyday mechanism (raised 5 -> 40 for that reason).
        try:
            _seen_ids = seen_message_ids.get(channel.lower()) or seen_message_ids.get(channel) or set()
            _min_id = max((int(x) for x in _seen_ids), default=0)
        except Exception:
            _min_id = 0
        posts = await _fetch_posts_via_userbot(
            channel,
            limit=max(3, int(os.environ.get("SCAN_POST_LIMIT", "40") or 40)),
            min_id=_min_id)
        if posts:
            return posts
    # Fallback: t.me/s HTML scraping (only ~20 posts, slow, but works without userbot)
    return await asyncio.to_thread(_fetch_posts_sync, channel)


# Labelled contract lines like "Ca: <addr>", "CA - <addr>", "Contract: <addr>",
# "Token Address: <addr>", "Mint: <addr>".  These are ALWAYS the real token
# address, so they win over any address found inside a chart URL (DexScreener
# chart links usually carry the PAIR/POOL address, not the token — using that
# address makes the token look untrackable on the API).
_LABELED_CA_RE = re.compile(
    r'(?:^|[\s\(\[])(?:ca|c\.a|contract(?:\s*address)?|token(?:\s*address)?|mint|address)'
    r'\s*[:：\-–—=]?\s*'
    r'`?([1-9A-HJ-NP-Za-km-z]{32,50}|0x[a-fA-F0-9]{64}|0x[a-fA-F0-9]{40})`?',
    re.IGNORECASE)

# Any bare URL — stripped before the generic scan so a pool address inside a
# dexscreener/dextools link is never mistaken for the token contract.
_URL_STRIP_RE = re.compile(r'https?://\S+|(?:www|t)\.me/\S+', re.IGNORECASE)


def _classify_ca(ca):
    """(chain, ca) for a raw address string, or None."""
    if not ca:
        return None
    if ca.startswith("0x") and len(ca) in (42, 66):
        return ("EVM", ca.lower())
    if re.match(r'^(EQ|UQ)[A-Za-z0-9_-]{46}$', ca):
        return ("TON", ca)
    if 32 <= len(ca) <= 44 and not ca.startswith("0x"):
        return ("SOL", ca)
    return None


def extract_ca(text):
    if not text:
        return None
    # 1) Explicit "Ca: ..." style line always wins.
    for m in _LABELED_CA_RE.finditer(text):
        r = _classify_ca(m.group(1))
        if r:
            return r
    # 2) Generic scan, but with URLs removed (pair addresses live in URLs).
    clean = _URL_STRIP_RE.sub(" ", text)
    for scope in (clean, text):
        # TON first — check before SOL to avoid misidentification (TON addresses can match SOL pattern)
        for m in TON_CA_PATTERN.finditer(scope):
            return ("TON", m.group(0))
        eth = ETH_CA_PATTERN.findall(scope)
        if eth:
            return ("EVM", eth[0].lower())
        for s in SOL_CA_PATTERN.findall(scope):
            if not s.startswith("0x") and 32 <= len(s) <= 44:
                return ("SOL", s)
    return None


# ─── Extract CA from chart / DEX links ────────────────────────────────────────
_DEX_LINK_CHAIN_MAP = {
    "solana": "SOL", "sol": "SOL",
    "ethereum": "EVM", "eth": "EVM",
    "bsc": "EVM", "bnb": "EVM", "binance": "EVM",
    "base": "EVM",
    "robinhood": "EVM", "rh": "EVM",
    "ton": "TON",
}

# Regex: dexscreener.com/{chain}/{ca}  OR  dextools.io/app/en/{chain}/pair-explorer/{ca}
# OR birdeye.so/token/{ca}  OR bullx.io with address={ca}  OR photon-sol.trac.so/{ca}
# Address patterns — covers SOL (base58, 32-44 chars), TON (EQ/UQ base64, 48 chars), EVM (0x hex, 42 chars)
_SOL_TON_ADDR = r'[1-9A-HJ-NP-Za-km-z]{32,50}'   # base58-like; covers both SOL (32-44) and TON EQ/UQ (48)
# 0x + 64 hex (Robinhood/"RH" 32-byte addresses) tried before 0x + 40 hex
# (standard EVM) so a long address is never truncated to its first 40 chars.
_EVM_ADDR     = r'0x[a-fA-F0-9]{64}(?![a-fA-F0-9])|0x[a-fA-F0-9]{40}(?![a-fA-F0-9])'
_ANY_CA       = rf'({_SOL_TON_ADDR}|{_EVM_ADDR})'

_DEXSCREENER_RE = re.compile(
    r'dexscreener\.com/([a-z0-9_-]+)/(' + _SOL_TON_ADDR + r'|' + _EVM_ADDR + r')',
    re.IGNORECASE
)
_DEXTOOLS_RE = re.compile(
    r'dextools\.io/app(?:/[a-z]+)?/([a-z0-9_-]+)/pair-explorer/' + _ANY_CA,
    re.IGNORECASE
)
_BULLX_RE = re.compile(
    r'bullx\.io[^\s]*[?&]address=' + _ANY_CA,
    re.IGNORECASE
)
_PHOTON_RE = re.compile(
    r'photon-sol\.trac\.so/(' + _SOL_TON_ADDR + r')',
    re.IGNORECASE
)
_BIRDEYE_RE = re.compile(
    r'birdeye\.so/token/' + _ANY_CA,
    re.IGNORECASE
)
# Gmgn, ave.ai (also common chart links in KOL posts)
_GMGN_RE = re.compile(
    r'gmgn\.ai/[a-z0-9]*/token/(?:[a-z]*/)?(' + _SOL_TON_ADDR + r'|' + _EVM_ADDR + r')',
    re.IGNORECASE
)
_AVE_RE = re.compile(
    r'ave\.ai/token/' + _ANY_CA,
    re.IGNORECASE
)

def extract_ca_from_links(text):
    """Try to extract a CA from known DEX/chart links in the message text.
    Returns (chain_guess, ca) like extract_ca(), or None if nothing found."""
    if not text:
        return None

    def _valid_ca(ca, chain_guess="SOL"):
        """Validate and normalise a CA string. Returns (chain, ca) or None."""
        if not ca: return None
        if ca.startswith("0x") and len(ca) in (42, 66):
            # 42 = standard EVM (0x+40hex); 66 = Robinhood/"RH" 32-byte
            # address format (0x+64hex) seen on DexScreener chart links.
            return ("EVM", ca.lower())
        # TON: EQ/UQ + 46 base64url chars = 48 total
        if re.match(r'^(EQ|UQ)[A-Za-z0-9_-]{46}$', ca):
            return ("TON", ca)
        # SOL: base58, 32-44 chars (never starts with 0x)
        if 32 <= len(ca) <= 44 and not ca.startswith("0x"):
            return (chain_guess, ca)
        return None

    # DexScreener: dexscreener.com/{chain}/{ca}
    for m in _DEXSCREENER_RE.finditer(text):
        chain_raw = m.group(1).lower()
        ca = m.group(2)
        chain_guess = _DEX_LINK_CHAIN_MAP.get(chain_raw, "EVM")
        r = _valid_ca(ca, chain_guess)
        if r: return r

    # DexTools
    for m in _DEXTOOLS_RE.finditer(text):
        chain_raw = m.group(1).lower()
        ca = m.group(2)
        chain_guess = _DEX_LINK_CHAIN_MAP.get(chain_raw, "EVM")
        r = _valid_ca(ca, chain_guess)
        if r: return r

    # BullX
    for m in _BULLX_RE.finditer(text):
        r = _valid_ca(m.group(1), "SOL")
        if r: return r

    # Photon (Solana only)
    for m in _PHOTON_RE.finditer(text):
        r = _valid_ca(m.group(1), "SOL")
        if r: return r

    # Birdeye
    for m in _BIRDEYE_RE.finditer(text):
        r = _valid_ca(m.group(1), "SOL")
        if r: return r

    # Gmgn
    for m in _GMGN_RE.finditer(text):
        r = _valid_ca(m.group(1), "SOL")
        if r: return r

    # Ave.ai
    for m in _AVE_RE.finditer(text):
        r = _valid_ca(m.group(1), "SOL")
        if r: return r

    return None

_EMOJI_MARKER_RE = re.compile(r"\[\[emoji:(\d{5,25})\]\]")
_TG_EMOJI_TAG_RE = re.compile(r'<tg-emoji\s+emoji-id="(\d{5,25})"\s*>(.*?)</tg-emoji>',
                              re.IGNORECASE | re.DOTALL)

def owner_emoji_markers_to_html(text: str) -> str:
    """Bot-API path: [[emoji:ID]] → <tg-emoji emoji-id="ID">🔮</tg-emoji>."""
    if not text:
        return text
    return _EMOJI_MARKER_RE.sub(lambda m: f'<tg-emoji emoji-id="{m.group(1)}">🔮</tg-emoji>', text)

def strip_owner_emoji_markers(text: str) -> str:
    """Plain fallback: remove custom-emoji syntax, leave the 🔮 placeholder."""
    if not text:
        return text
    text = _EMOJI_MARKER_RE.sub("🔮", text)
    return _TG_EMOJI_TAG_RE.sub(lambda m: (m.group(2) or "🔮"), text)

def prepare_owner_emojis(text: str):
    """Userbot path. Convert the owner's own premium-emoji syntax
    (`[[emoji:ID]]` or `<tg-emoji emoji-id="ID">x</tg-emoji>`) into plain 🔮
    markers, remembering which marker index must use which explicit ID.

    Returns (html_text_with_markers, {marker_index: emoji_id}).
    """
    if not text:
        return text, {}
    out = []
    explicit = {}
    idx = 0          # running count of 🔮 markers already emitted
    pos = 0
    pattern = re.compile(
        r"\[\[emoji:(?P<a>\d{5,25})\]\]"
        r'|<tg-emoji\s+emoji-id="(?P<b>\d{5,25})"\s*>(?P<inner>.*?)</tg-emoji>',
        re.IGNORECASE | re.DOTALL)
    for m in pattern.finditer(text):
        chunk = text[pos:m.start()]
        out.append(chunk)
        idx += chunk.count("🔮")
        eid = m.group("a") or m.group("b")
        explicit[idx] = int(eid)
        out.append("🔮")
        idx += 1
        pos = m.end()
    tail = text[pos:]
    out.append(tail)
    return "".join(out), explicit



# Used by _build_premium_entities' MC-separator lock: matches a real number
# token (optionally $-prefixed, optional K/M/B suffix) ONLY when it starts
# right after whitespace/start-of-string/'>' — so "12.1K" (preceded by a
# space) matches, but the trailing digits of a username like
# "@musterseed99" (preceded by a letter, no boundary) do NOT.
_MC_VALUE_BEFORE_RE = re.compile(r'(?:^|[\s>])\$?\d[\d,]*\.?\d*\s*[KMBkmb]?$')

def _build_premium_entities(plain_text, base_entities, emoji_ids, forced_pack=None,
                            explicit=None):
    """Replace each 🔮 in plain_text with the next emoji_id from the list.
    emoji_ids may be a single str/int or a list. Falls back to last id when list runs out.

    Special-cases (in priority order):
    1. 🔮KOL / 🔮BOT  → pack kol/bot emoji (always, regardless of position)
    2. 🔮X or 🔮 X    → pack x emoji (locked — works even if template edited)
    3. "Champion KOL - Rank # 🔮" / "Leaderboard KOL - Rank # 🔮"
       → us KOL ki rank numbering premium emoji (auto-detected)
    4. (reserved)
    5. MC separator 🔮 (between entry MC and current MC) → TEER_EMOJI_ID=5346105514575025401 (locked)
    6. all other 🔮s  → next item from non-badge portion of emoji_ids

    Badge emojis (star + rank, appended at end of emoji_ids by send_alert) are
    auto-detected from the text so they NEVER get consumed by template 🔮 placeholders,
    even when the owner edits the template with extra 🔮s.
    """
    from telethon.tl.types import MessageEntityCustomEmoji
    if not isinstance(emoji_ids, list):
        emoji_ids = [emoji_ids]
    emoji_ids = [e for e in emoji_ids if e]
    # Promo link text (set via /setpromolink) — its 🔮 must ALWAYS render
    # PROMO_EMOJI_ID, never a positional id.
    _promo_txt = ""
    _promo_emoji_id = PROMO_EMOJI_ID
    try:
        _p = load_config().get("promo_link") or {}
        if _p.get("text"):
            _promo_txt = html.unescape(str(_p["text"])).strip()
        if _p.get("emoji_id"):
            _promo_emoji_id = int(_p["emoji_id"])
    except Exception:
        _promo_txt = _promo_txt or ""

    pack = forced_pack if forced_pack is not None else _get_alert_pack()
    kol_id = pack.get("kol")
    bot_id = pack.get("bot")
    mae_id = pack.get("mae") or 0
    if not emoji_ids and not kol_id and not bot_id and not mae_id:
        return base_entities or []

    # ── Badge auto-detection ───────────────────────────────────────────────────
    # Naya badge format (hamesha sabse aakhir me lagta hai):
    #   "Champion KOL - Rank # 🔮"  /  "Leaderboard KOL - Rank # 🔮"
    # Sirf 1 badge emoji hoti hai (rank numbering emoji) — koi star 🔮 nahi.
    #   non_badge_ids — consumed by regular template 🔮s via pos_index
    #   badge_ids     — reserved for the rank 🔮, never consumed by template 🔮s
    _badge_type = None
    if 'LEAD KOL - Rank #' in plain_text:
        _badge_type = 'leaderboard'
    elif 'CHAMP KOL - Rank #' in plain_text:
        _badge_type = 'champion'
    badge_count = (2 if _badge_type == 'champion' else 1) if _badge_type else 0

    if badge_count and len(emoji_ids) >= badge_count:
        non_badge_ids = emoji_ids[:-badge_count]
        badge_ids     = emoji_ids[-badge_count:]
    else:
        non_badge_ids = emoji_ids
        badge_ids     = []

    # ── Dropped-call STRUCTURAL role map (language independent) ───────────────
    # FIX: the 1️⃣/➡️ lock above only recognised English/Chinese/Vietnamese
    # label text, so every other language (Turkish, Russian, Hindi, Arabic,
    # Spanish, ...) fell through to the positional chain-emoji list and the
    # chain emoji showed up 8x instead of 2x. Roles are now derived from the
    # template LAYOUT instead of label wording: the first line holding 🔮 is
    # the header (chain emoji), and every later line holding exactly two 🔮 is
    # a "1️⃣ label ➡️ value" row → (NUM, ARROW). MAE/KOL/BOT lines have a
    # single 🔮 so they are untouched.
    _drop_roles = {}
    if pack.get('name') == 'dropped':
        _off = 0
        _hdr_seen = False
        for _ln in plain_text.split('\n'):
            _pos = [_off + _m for _m in range(len(_ln)) if _ln.startswith('🔮', _m)]
            if _pos:
                if not _hdr_seen:
                    _hdr_seen = True
                elif len(_pos) == 2:
                    _drop_roles[_pos[0]] = DROPPED_NUM_EMOJI
                    _drop_roles[_pos[1]] = DROPPED_ARROW_EMOJI
            _off += len(_ln) + 1

    custom_entities = []
    explicit = explicit or {}
    pos_index = 0
    marker_idx = 0
    utf16_off = 0
    i = 0
    PLACEHOLDER = '🔮'
    PH_LEN = len(PLACEHOLDER)
    emoji_u16 = len(PLACEHOLDER.encode('utf-16-le')) // 2
    _badge_rank_pending = False  # True after we emitted the badge star emoji

    while i < len(plain_text):
        if plain_text[i:i+PH_LEN] == PLACEHOLDER:
            rest = plain_text[i+PH_LEN:]
            eid  = None
            _this_marker = marker_idx
            marker_idx += 1

            if _this_marker in explicit and i not in _drop_roles:
                # (dropped-call 1️⃣/➡️ slots are locked structurally — a stored lang template
                #  carrying the chain emoji id there must NOT win, that was the 8x bug)
                # Owner ne khud is jagah premium emoji ID di hai — wahi use hogi.
                eid = explicit[_this_marker]

            elif _promo_txt and rest.lstrip(' ').startswith(_promo_txt):
                # 🔮 <promo text> → owner ka chuna hua premium emoji
                # (/setpromolink me di gayi emoji ID), warna default.
                eid = _promo_emoji_id

            elif rest.startswith('KOL') and kol_id:
                # 🔮KOL → pack kol emoji
                eid = kol_id

            elif rest.startswith('BOT') and bot_id:
                # 🔮BOT → pack bot emoji
                eid = bot_id

            elif mae_id and rest.startswith('MAE'):
                # 🔮MAE → pack mae (Maestro) emoji — replaces the old 🔮X slot
                eid = mae_id

            elif rest.lstrip(' ').startswith('Caller') and pack.get('caller'):
                # 🔮Caller → pack caller emoji (X-alert in-text link row)
                # .lstrip(' ') tolerates "🔮 Caller" (with space) too, not just "🔮Caller"
                eid = pack.get('caller')

            elif rest.lstrip(' ').startswith('Twitter') and pack.get('twitter'):
                eid = pack.get('twitter')

            elif rest.lstrip(' ').startswith('Trade') and pack.get('trade'):
                eid = pack.get('trade')

            elif rest.lstrip(' ').upper().startswith('DEX') and pack.get('dex'):
                eid = pack.get('dex')

            elif rest.lstrip(' ').startswith('Details') and pack.get('details'):
                eid = pack.get('details')

            elif rest.lstrip(' ').upper().startswith('DEV') and pack.get('dev'):
                eid = pack.get('dev')

            elif (_badge_type == 'champion' and len(badge_ids) >= 2
                  and rest.lstrip(' ').startswith('CHAMP KOL')):
                # "🔮 CHAMP KOL - Rank # 🔮" → leading badge emoji
                eid = badge_ids[0]

            elif badge_ids and plain_text[:i].endswith('Rank # '):
                # "🔮 CHAMP KOL - Rank # 🔮" / "Leaderboard KOL - Rank # 🔮"
                # → us KOL ki rank numbering premium emoji (badge ka aakhri id)
                eid = badge_ids[-1]

            elif i in _drop_roles:
                # Language-independent dropped-call lock (see _drop_roles above)
                eid = _drop_roles[i]

            elif (pack.get('name') == 'dropped' and rest.startswith(
                    ('Token Symbol', 'Current MC', 'Chain Symbol',
                     '代币符号', '当前市值', '链符号',
                     'Ký hiệu Token', 'Vốn hóa hiện tại', 'Ký hiệu Chain'))):
                # HARD LOCK (dropped-call only): the "1️⃣" before these three
                # labels must always be DROPPED_NUM_EMOJI — never the
                # positional chain-emoji list. This is a context lock (not
                # dependent on the `explicit` map), so it self-heals even if
                # the stored dropped_call_template ever loses/corrupts its
                # saved tg-emoji IDs (that corruption is exactly what made
                # this emoji show as the chain emoji 8x instead of 2x).
                # FIX: originally only matched the English label text, so a
                # Chinese/Vietnamese/Turkish/Russian-tagged dropped-call template (translated
                # labels) never tripped this lock at all — EVERY 🔮 in that
                # template (header + all 3 label/arrow pairs) fell through to
                # the generic 2-item chain rotation instead, which is exactly
                # what produced "chain emoji 8x instead of 2x".
                eid = DROPPED_NUM_EMOJI

            elif (pack.get('name') == 'dropped' and plain_text[:i].rstrip().endswith(
                    ('Token Symbol', 'Current MC', 'Chain Symbol',
                     '代币符号', '当前市值', '链符号',
                     'Ký hiệu Token', 'Vốn hóa hiện tại', 'Ký hiệu Chain'))):
                # HARD LOCK (dropped-call only): the "➡️" right after those
                # same three labels must always be DROPPED_ARROW_EMOJI.
                # Same Chinese/Vietnamese label fix as above.
                eid = DROPPED_ARROW_EMOJI

            else:
                # ── MC separator lock ─────────────────────────────────────────
                # The 🔮 between entry MC and current MC must ALWAYS be TEER_EMOJI_ID.
                # Detect by context: preceded by MC value (ends K/M/B/digit) and
                # followed (after spaces) by $ or digit.
                before_stripped = plain_text[:i].rstrip()
                after_stripped  = rest.lstrip()
                # Require a REAL number token right before the 🔮 (start-of-
                # string or whitespace immediately before the digit run),
                # not just "last character is a digit". A bare last-char
                # check false-positives on anything ending in a digit that
                # ISN'T a market-cap value — e.g. a KOL username like
                # "@musterseed99" — which mis-fires the teer lock, skips
                # advancing pos_index, and desyncs every 🔮 after it (this is
                # exactly what flipped the number-badge/arrow order in the
                # leaderboard from rank 4 onward).
                if (before_stripped
                        and _MC_VALUE_BEFORE_RE.search(before_stripped)
                        and after_stripped
                        and after_stripped[0] in '$0123456789'):
                    eid = _pack_teer(pack)
                    # NOTE: pos_index NOT advanced — teer slot is not counted against
                    # the positional list, keeping badge emoji alignment correct.
                elif non_badge_ids:
                    if pos_index >= len(non_badge_ids):
                        # DIAG: this 🔮 didn't match ANY special case above
                        # (Caller/Twitter/Trade/DEX/Details/DEV/KOL/BOT/MAE/
                        # badge/teer-lock) and the positional list has run
                        # out, so it's about to repeat the LAST id (teer) —
                        # this is exactly the "wrong emoji on custom-template
                        # button labels" bug. Logging the text right after
                        # the 🔮 pinpoints which label text failed to match.
                        logger.warning(
                            f"🔮 emoji placeholder #{pos_index} fell through to "
                            f"positional fallback (reusing last id) — text after "
                            f"placeholder: {rest[:20]!r}")
                    eid = non_badge_ids[min(pos_index, len(non_badge_ids) - 1)]
                    pos_index += 1

            if eid:
                custom_entities.append(MessageEntityCustomEmoji(
                    offset=utf16_off, length=emoji_u16,
                    document_id=int(eid)
                ))
            utf16_off += emoji_u16
            i += PH_LEN
            continue
        char_u16 = len(plain_text[i].encode('utf-16-le')) // 2
        utf16_off += char_u16
        i += 1
    return (base_entities or []) + custom_entities

def _tl_buttons_from_keyboard(keyboard):
    """PTB InlineKeyboardMarkup → Telethon ReplyInlineMarkup (URL buttons only)."""
    if not keyboard:
        return None
    try:
        from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonUrl
        rows = []
        for row in keyboard.inline_keyboard:
            btns = [KeyboardButtonUrl(text=b.text, url=b.url) for b in row if getattr(b, "url", None)]
            if btns:
                rows.append(KeyboardButtonRow(buttons=btns))
        return ReplyInlineMarkup(rows=rows) if rows else None
    except Exception as e:
        logger.warning(f"keyboard convert failed: {e}")
        return None

async def _userbot_send_with_premium_emoji(chat, text, emoji_id=None, link_preview=False, forced_pack=None, keyboard=None):
    """Send via userbot. Returns sent Message object on success, None on failure."""
    if not userbot_client:
        return None
    tl_buttons = _tl_buttons_from_keyboard(keyboard)
    try:
        text, _explicit = prepare_owner_emojis(text)
        if emoji_id or _explicit:
            try:
                from telethon.extensions.html import parse as tl_html_parse
                plain_text, base_entities = tl_html_parse(text)
                all_entities = _build_premium_entities(plain_text, base_entities, emoji_id,
                                                       forced_pack=forced_pack, explicit=_explicit)
                msg = await userbot_client.send_message(
                    chat, plain_text,
                    formatting_entities=all_entities,
                    link_preview=link_preview,
                    buttons=tl_buttons
                )
                return msg
            except Exception as e:
                logger.warning(f"Custom emoji send failed, fallback: {e}")
                if tl_buttons is not None:
                    # Buttons zaroori hain (X alert) — userbot se nahi gaye to
                    # None return karo taake Bot API fallback buttons ke saath bheje.
                    return None
        msg = await userbot_client.send_message(chat, text, parse_mode="html",
                                                link_preview=link_preview, buttons=tl_buttons)
        return msg
    except Exception as e:
        logger.error(f"userbot send: {e}")
        return None

async def _userbot_edit_with_premium_emoji(chat, msg_id, text, emoji_ids_for_ranking=None, forced_pack=None):
    """Edit message via userbot. If emoji_ids_for_ranking given, replaces number emojis."""
    if not userbot_client:
        return False
    try:
        if emoji_ids_for_ranking:
            try:
                from telethon.tl.types import MessageEntityCustomEmoji
                from telethon.extensions.html import parse as tl_html_parse
                plain_text, base_entities = tl_html_parse(text)
                default_nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
                custom_entities = []
                for idx, eid in enumerate(emoji_ids_for_ranking[:10]):
                    if not eid: continue
                    target = default_nums[idx] if idx < len(default_nums) else f"{idx+1}."
                    utf16_off = 0; i = 0
                    while i < len(plain_text):
                        seg = plain_text[i:i+len(target)]
                        if seg == target:
                            eid_u16 = len(target.encode('utf-16-le')) // 2
                            custom_entities.append(MessageEntityCustomEmoji(
                                offset=utf16_off, length=eid_u16,
                                document_id=int(eid)
                            ))
                            break
                        char_u16 = len(plain_text[i].encode('utf-16-le')) // 2
                        utf16_off += char_u16; i += 1
                all_entities = (base_entities or []) + custom_entities
                await _locked_userbot_edit(chat, msg_id, plain_text,
                                                   formatting_entities=all_entities,
                                                   link_preview=False)
                return True
            except Exception as e:
                logger.warning(f"Premium emoji edit failed, fallback: {e}")
        if forced_pack or _EMOJI_MARKER_RE.search(text or "") or _TG_EMOJI_TAG_RE.search(text or ""):
            try:
                from telethon.extensions.html import parse as tl_html_parse
                _txt, _explicit = prepare_owner_emojis(text)
                plain_text, base_entities = tl_html_parse(_txt)
                all_entities = _build_premium_entities(plain_text, base_entities, [],
                                                       forced_pack=forced_pack, explicit=_explicit)
                await _locked_userbot_edit(chat, msg_id, plain_text,
                                                   formatting_entities=all_entities,
                                                   link_preview=False)
                return True
            except Exception as e:
                logger.warning(f"forced_pack edit failed, fallback: {e}")
        await _locked_userbot_edit(chat, msg_id, text, parse_mode="html", link_preview=False)
        return True
    except Exception as e:
        logger.error(f"userbot edit {msg_id}: {e}")
        return False

async def _userbot_edit_caption_with_premium_emoji(chat, msg_id, text, emoji_id, forced_pack=None):
    """Edit a media message's caption via userbot. emoji_id = single id or list (one per 🔮)."""
    text, _explicit = prepare_owner_emojis(text)
    if not userbot_client or not (emoji_id or _explicit):
        logger.warning(f"Skipping emoji edit: userbot={userbot_client is not None}, emoji_id={emoji_id}")
        return False
    try:
        from telethon.extensions.html import parse as tl_html_parse
        plain_text, base_entities = tl_html_parse(text)
        all_entities = _build_premium_entities(plain_text, base_entities, emoji_id,
                                               forced_pack=forced_pack, explicit=_explicit)
        logger.info(f"Userbot editing msg {msg_id} with emoji_id={emoji_id}, entities={len(all_entities)}")
        await _locked_userbot_edit(
            chat, msg_id, plain_text,
            formatting_entities=all_entities,
            link_preview=False
        )
        logger.info(f"✅ Userbot emoji edit SUCCESS for msg {msg_id}")
        return True
    except Exception as e:
        logger.warning(f"Caption premium emoji edit FAILED: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════════
# MEDIA CACHE (X alert instant fix)
# Pehle HAR alert par: bot API se getFile -> Railway par 500KB+ download ->
# userbot se dobara upload (5 chunks). Wahi 5-15 second lag tha, aur isi wajah
# se rt_worker ka 25s hard-timeout hit hota tha => "skipping this item" =>
# kuch channels ki X alert kabhi post hi nahi hoti thi.
# Ab: file ek dafa download hoti hai, aur pehli upload ke baad Telegram ka
# apna media reference reuse hota hai — agla alert sub-second me chala jata hai.
# ═══════════════════════════════════════════════════════════════════════════
_MEDIA_FILE_CACHE = {}   # file_id -> local path (downloaded once)
_MEDIA_TL_CACHE   = {}   # file_id -> telethon media object (no re-upload)
_MEDIA_CACHE_DIR  = os.path.join(DATA_DIR, "media_cache") if "DATA_DIR" in dir() else "/tmp/wizard_media"

def _media_cache_dir():
    try:
        os.makedirs(_MEDIA_CACHE_DIR, exist_ok=True)
        return _MEDIA_CACHE_DIR
    except Exception:
        os.makedirs("/tmp/wizard_media", exist_ok=True)
        return "/tmp/wizard_media"

def invalidate_media_cache(file_id=None):
    """Owner naya media set kare to purana cache foran chhod do."""
    if file_id:
        _MEDIA_TL_CACHE.pop(file_id, None)
        _MEDIA_FILE_CACHE.pop(file_id, None)
    else:
        _MEDIA_TL_CACHE.clear()
        _MEDIA_FILE_CACHE.clear()

async def _userbot_send_media_with_emoji(bot_app, chat, file_id, file_type, text, emoji_id, keyboard=None, forced_pack=None, _retry=True):
    """Send media via userbot with premium emoji — NO edit = NO pencil mark.
    Media ek dafa download/upload hoti hai, phir cache se instant."""
    import os
    tmp_path = None
    # FIX (2026-10-13): keep the ORIGINAL text (with <tg-emoji> tags). The code
    # below overwrites `text` with the placeholder version; if the send fails with
    # "stale file reference" the retry used to get that already-converted text, so
    # prepare_owner_emojis() found no tags -> every placeholder fell back to the
    # positional id list (CALL 1/2/3 got the banner's last tile instead of their emoji).
    _orig_text = text
    try:
        cached_media = _MEDIA_TL_CACHE.get(file_id)
        if cached_media is None:
            tmp_path = _MEDIA_FILE_CACHE.get(file_id)
            if not tmp_path or not os.path.exists(tmp_path):
                suffix = '.mp4' if file_type == 'video' else '.jpg'
                safe_id = re.sub(r'[^A-Za-z0-9_-]', '', str(file_id))[-64:]
                tmp_path = os.path.join(_media_cache_dir(), f"{safe_id}{suffix}")
                tg_file = await bot_app.get_file(file_id)
                await tg_file.download_to_drive(tmp_path)
                _MEDIA_FILE_CACHE[file_id] = tmp_path

        from telethon.extensions.html import parse as tl_html_parse
        text, _explicit = prepare_owner_emojis(text)
        plain_text, base_ents = tl_html_parse(text)
        all_entities = _build_premium_entities(plain_text, base_ents, emoji_id,
                                               forced_pack=forced_pack, explicit=_explicit)

        # Convert PTB InlineKeyboardMarkup → Telethon buttons
        tl_buttons = None
        if keyboard:
            try:
                from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonUrl
                rows = []
                for row in keyboard.inline_keyboard:
                    btns = [KeyboardButtonUrl(text=b.text, url=b.url) for b in row if b.url]
                    if btns:
                        rows.append(KeyboardButtonRow(buttons=btns))
                if rows:
                    tl_buttons = ReplyInlineMarkup(rows=rows)
            except Exception:
                pass

        msg = await userbot_client.send_file(
            chat, cached_media if cached_media is not None else tmp_path,
            caption=plain_text,
            formatting_entities=all_entities,
            buttons=tl_buttons,
            supports_streaming=True
        )
        # Pehli kamyab upload ke baad Telegram ka media reference cache kar lo —
        # agli dafa upload bilkul nahi hoga (instant post).
        if cached_media is None and msg is not None:
            try:
                if getattr(msg, "media", None) is not None:
                    _MEDIA_TL_CACHE[file_id] = msg.media
            except Exception:
                pass
        logger.info(f"✅ Userbot media send SUCCESS (no pencil) file_type={file_type} "
                    f"cached={cached_media is not None}")
        return msg
    except Exception as e:
        # Stale media reference (file reference expired) -> cache clear karke
        # agli koshish fresh upload se hogi. FIX: pehle sirf cache clear hota
        # tha lekin usi call mein give up ho jata tha -> seedha Bot API (no
        # emoji) fallback. Ab cache clear hote hi FORAN ek retry (fresh
        # download+upload ke saath) hota hai, taake premium emoji wala send
        # hi succeed ho jaye is call mein — Bot API tak jaana hi na pade.
        if _retry and ("reference" in str(e).lower() or "FILE_REFERENCE" in str(e)):
            invalidate_media_cache(file_id)
            logger.warning(f"Userbot media send: stale file reference, retrying fresh once: {e}")
            return await _userbot_send_media_with_emoji(
                bot_app, chat, file_id, file_type, _orig_text, emoji_id,
                keyboard=keyboard, forced_pack=forced_pack, _retry=False)
        logger.error(f"Userbot media send failed: {e}")
        return None

async def _prewarm_media_cache(bot_app):
    """After startup, upload every configured dropped/milestone video ONCE (to the
    userbot's Saved Messages) so the first real alert after a restart is already
    cached (cached=True) instead of a 5-15s download+upload."""
    try:
        await asyncio.sleep(15)
        if not userbot_client:
            return
        cfg = load_config()
        items = list(cfg.get("dropped_videos", []) or [])
        mm = cfg.get("milestone_media", {}) or {}
        items += [m for m in mm.values() if isinstance(m, dict)]
        done = set()
        for m in items:
            fid = (m or {}).get("file_id") if isinstance(m, dict) else None
            if not fid or fid in done or fid in _MEDIA_TL_CACHE:
                continue
            done.add(fid)
            try:
                ftype = (m.get("type") or "video")
                tmp = _MEDIA_FILE_CACHE.get(fid)
                if not tmp or not os.path.exists(tmp):
                    suffix = ".mp4" if ftype == "video" else ".jpg"
                    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", str(fid))[-64:]
                    tmp = os.path.join(_media_cache_dir(), f"{safe_id}{suffix}")
                    tg_file = await bot_app.get_file(fid)
                    await tg_file.download_to_drive(tmp)
                    _MEDIA_FILE_CACHE[fid] = tmp
                msg = await asyncio.wait_for(
                    userbot_client.send_file("me", tmp, supports_streaming=True), timeout=60)
                if msg is not None and getattr(msg, "media", None) is not None:
                    _MEDIA_TL_CACHE[fid] = msg.media
            except Exception as e_pw:
                logger.warning(f"media prewarm skipped one item: {type(e_pw).__name__}: {e_pw}")
            await asyncio.sleep(2)
        logger.info(f"🔥 Media cache pre-warmed: {len(_MEDIA_TL_CACHE)} item(s) cached")
    except Exception as e:
        logger.warning(f"media prewarm failed: {e}")

async def _start_second_userbot(bot):
    """Optional 2nd Telegram account used ONLY to receive new posts (realtime) from
    channels the main userbot could not join (banned / join timeout). It never
    sends or edits anything. Inactive unless SESSION_STRING_2 is set on Railway.
    Optional: USERBOT_API_ID_2 / USERBOT_API_HASH_2 (default: same as main)."""
    global userbot_client2
    s2 = os.environ.get("SESSION_STRING_2", "").strip()
    if not s2:
        return
    try:
        api_id = int(os.environ.get("USERBOT_API_ID_2", "") or 0) or OWNER_API_ID
        api_hash = os.environ.get("USERBOT_API_HASH_2", "") or OWNER_API_HASH
        if not api_id or not api_hash:
            logger.warning("2nd userbot: API id/hash missing — skipped")
            return
        await asyncio.sleep(90)   # let the main userbot finish its own joins first
        from telethon import TelegramClient, events, utils as _tlu
        from telethon.sessions import StringSession
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
        c = TelegramClient(StringSession(s2), api_id, api_hash,
                           connection_retries=3, retry_delay=3, auto_reconnect=True,
                           catch_up=False, receive_updates=True, flood_sleep_threshold=60)
        await asyncio.wait_for(c.connect(), timeout=45)
        if not await asyncio.wait_for(c.is_user_authorized(), timeout=15):
            logger.warning("2nd userbot: session not authorized — skipped")
            await c.disconnect()
            return
        me = await asyncio.wait_for(c.get_me(), timeout=15)
        userbot_client2 = c
        logger.info(f"✅ 2nd userbot connected: @{me.username or me.id} (monitor-only)")
        id_user = {}

        @c.on(events.NewMessage(func=lambda e: bool(getattr(e, "is_channel", False))))
        async def _h2(event):
            try:
                cid = getattr(event, "chat_id", None)
                username = id_user.get(cid)
                if username is None:
                    chat = await event.get_chat()
                    username = (getattr(chat, "username", None) or "").lower()
                    id_user[cid] = username
                if username not in [x.lower() for x in load_channels()]:
                    return
                msg_id = str(event.id)
                if msg_id in seen_message_ids[username]:
                    return
                text = event.raw_text or event.text or ""
                for ent in (getattr(event.message, "entities", None) or []):
                    if isinstance(ent, MessageEntityTextUrl) and getattr(ent, "url", None):
                        text += " " + ent.url
                    elif isinstance(ent, MessageEntityUrl):
                        raw_msg = getattr(event.message, "message", None) or ""
                        off = int(getattr(ent, "offset", 0) or 0)
                        ln = int(getattr(ent, "length", 0) or 0)
                        if raw_msg and ln > 0:
                            text += " " + raw_msg[off:off + ln]
                await _process_new_call(bot, username, msg_id, text, getattr(event, "date", None))
            except Exception as e_h2:
                logger.error(f"2nd userbot handler: {type(e_h2).__name__}: {e_h2}")

        while True:
            try:
                missing = [ch.lower() for ch in load_channels()
                           if ch.lower() not in _RT_COVERED and ch.lower() not in _RT_COVERED2]
                if missing:
                    logger.info(f"2nd userbot: trying {len(missing)} channel(s) the main userbot doesn't cover")
                for ch in missing:
                    try:
                        ent = await asyncio.wait_for(c.get_entity(ch), timeout=10)
                        if getattr(ent, "left", False):
                            await asyncio.wait_for(c(JoinChannelRequest(ch)), timeout=15)
                            ent = await asyncio.wait_for(c.get_entity(ch), timeout=10)
                        id_user[_tlu.get_peer_id(ent)] = ch
                        id_user[getattr(ent, "id", 0)] = ch
                        _RT_COVERED2.add(ch)
                        _RT_COVERED.add(ch)   # realtime now exists -> polling can stay slow
                        logger.info(f"2nd userbot: realtime active for @{ch}")
                    except Exception as e_c:
                        logger.warning(f"2nd userbot: @{ch} not covered: {type(e_c).__name__}: {e_c}")
                    await asyncio.sleep(1.5)
            except Exception as e_loop:
                logger.warning(f"2nd userbot loop: {e_loop}")
            await asyncio.sleep(300)
    except Exception as e:
        logger.error(f"2nd userbot failed: {type(e).__name__}: {e}")

# ─── Owner only ───────────────────────────────────────────────────────────────
# OWNER FIX (2026-09-23, crash-loop incident): this used to be defined at line
# ~12921, ~4000 lines AFTER its first use at the old line 8940
# (@owner_only above cmd_testalerths). Python decorators execute top-to-bottom
# at module import time, so the interpreter hit "@owner_only" before the name
# owner_only existed anywhere in the module namespace → NameError: name
# 'owner_only' is not defined, crashing on every single boot (crash-loop, not
# an env/variable issue — Railway variables were never the problem). Moved up
# here, right before its first of 203 uses in the file. functools/logger/
# OWNER_IDS are all defined near the top of the file already, so this is safe.
def owner_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if uid is None or uid not in OWNER_IDS:
            logger.warning(f"owner_only blocked uid={uid} for /{func.__name__} (OWNER_IDS={OWNER_IDS})")
            if update.message:
                await update.message.reply_text("⛔ Owner only.")
            return
        return await func(update, context)
    return wrapper
