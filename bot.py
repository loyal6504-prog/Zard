import os
import re
import io
import json
import time
import uuid
import html
import logging
import requests
import asyncio
import functools
from datetime import datetime, timedelta
from collections import defaultdict
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

# Channel post IDs for auto-update
POST_TRENDING    = 135
POST_LEADERBOARD = 136
POST_CHAMPIONS   = 137
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
CHANNEL_SUBS_FILE     = _dp("channel_subs.json")
MOMENTUM_SENT_FILE    = _dp("momentum_sent.json")
MOMENTUM_REPORTS_FILE = _dp("momentum_reports.json")   # snapshot of X-Ray rows per momentum post
XRAY_ARCHIVE_FILE     = _dp("xray_archive.json")        # permanent per-channel milestone record for X-Ray Report
CALL_ARCHIVE_FILE     = _dp("call_archive.json")        # permanent /history record — rugged calls get archived here before being pruned from tracked_calls
CHANNEL_POINTS_FILE   = _dp("channel_points.json")
TRENDING_BLACKLIST_FILE  = _dp("trending_blacklist.json")
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
IMG_LINKME    = "attached_assets/IMG_LINKME.png"                              # /linkme info
IMG_TRACKED   = "attached_assets/IMG_20260704_021900_436_1783113819561.jpg"   # Tracked KOLs
IMG_HISTORY   = "attached_assets/IMG_HISTORY.png"                             # /history info
IMG_ALERT     = "attached_assets/IMG_20260613_095809_422_1781330812978.jpg"   # Alert Rules
IMG_XCOMMAND  = "attached_assets/file_00000000ef9872078bbbb84ad23477b8_1781330813005.png"  # X/Twitter
VID_START     = "attached_assets/VID_20260613_095844_594_1781330812838.mp4"   # Start video (5-sec)
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
                    "robinhood": "RH", "ton": "TON"}   # Robinhood L2 (EVM, OP-Stack)
CHAIN_TO_DEXPATH = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base",
                    "RH": "robinhood", "TON": "ton"}

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
ST_ADD_MOMENTUM_VID = "add_momentum_vid"
ST_ADD_XRAY_VID     = "add_xray_vid"
ST_RANGE_TMPL        = "edit_range_tmpl"

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

# Premium emoji IDs for champions post 137
CHAMPIONS_PREMIUM_EMOJIS = {
    "star":  5807491057792853272,
    "arrow": 5807952487604297661,
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

# Premium emoji tags for user-facing bot messages (parse_mode="HTML")
PE_CRYSTAL = '<tg-emoji emoji-id="5361837567463399422">🔮</tg-emoji>'
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
}

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
TEER_EMOJI_ID = 5346105514575025401  # locked — never stored in config so restore can't change it
MC_ENTRY_EMOJI_ID = 5823350993332477003  # locked — 2nd 🔮 (entry MC) — same across all color packs
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
WIZARD_X_FALLBACK_LINK = "https://x.com/WizardScan"   # jab KOL ka X handle set na ho
WIZARD_DEV_LINK        = "https://t.me/Wizard_Scan"   # @Wizard_Scan

# NOTE on key names (owner ke naye naam):
#   mae  == Trade    (Maestro trade link)
#   kol  == Caller   (KOL ki original call post)
#   bot  == Details  (Details button/link)
# Purane keys (mae/kol/bot) code me har jagah use hote hain, is liye wo waise
# hi rakhe gaye hain — saath me naye alias keys (trade/caller/details) aur
# naye twitter/dex/dev emoji IDs bhi add kiye gaye hain.
EMOJI_PACKS = [
    {   # 0: Red
        "name": "red",
        "crystal": 5909248376452424773,
        "kol":     5814573411619316285,   # Caller
        "mae":     5814610803604594885,   # Trade
        "bot":     5814697287066067318,   # Details
        "caller":  5814573411619316285,
        "trade":   5814610803604594885,
        "details": 5814697287066067318,
        "twitter": 5814499903254045900,
        "dex":     5814582873432268001,
        "dev":     5816756655165022752,
        "chain": {
            "SOL":  5816843430684270530,
            "ETH":  5814294049766513664,
            "BNB":  5814610378402833934,
            "BSC":  5814610378402833934,
            "BASE": 5814332670112440509,
            "RH":   5816863161764027861,  # Robinhood red
            "TON":  5814396699484889103,  # TON red
        },
    },
    {   # 1: Blue
        "name": "blue",
        "crystal": 5909212560720142417,
        "kol":     5803081316085801545,   # Caller
        "mae":     5801054392169799401,   # Trade
        "bot":     5802911415769506568,   # Details
        "caller":  5803081316085801545,
        "trade":   5801054392169799401,
        "details": 5802911415769506568,
        "twitter": 5802998410332086571,
        "dex":     5803086001895121190,
        "dev":     5803008043943731220,
        "chain": {
            "SOL":  5802918300602081472,
            "ETH":  5801004278491390616,
            "BNB":  5803070604437367738,
            "BSC":  5803070604437367738,
            "BASE": 5803016552273947641,
            "RH":   5802918588364890768,  # Robinhood blue
            "TON":  5800914535149740087,  # TON blue
        },
    },
    {   # 2: White (black & white)
        "name": "white",
        "crystal": 5911492432440073438,
        "kol":     5909266939301076138,   # Caller
        "mae":     5999292610626199656,   # Trade
        "bot":     5908863744951197586,   # Details
        "caller":  5909266939301076138,
        "trade":   5999292610626199656,
        "details": 5908863744951197586,
        "twitter": 5906532432407960996,
        "dex":     5816955086949064793,
        "dev":     5816410854463118837,
        "chain": {
            "SOL":  5908783523552042618,
            "ETH":  5908947999324645137,
            "BNB":  5909194796735406168,
            "BSC":  5909194796735406168,
            "BASE": 5908768143274155036,
            "RH":   5962801335823770424,  # Robinhood white
            "TON":  5996599829340363765,  # TON white
        },
    },
    {   # 3: Purple
        "name": "purple",
        "crystal": 5909214897182352449,
        "kol":     5805305430540297057,   # Caller
        "mae":     5805269198196188467,   # Trade
        "bot":     5805630954701595827,   # Details
        "caller":  5805305430540297057,
        "trade":   5805269198196188467,
        "details": 5805630954701595827,
        "twitter": 5805186064809205451,
        "dex":     5805271753701729563,
        "dev":     5816582975277506336,
        "chain": {
            "SOL":  5805436955323802128,
            "ETH":  5805353766102245705,
            "BNB":  5805369185034837562,
            "BSC":  5805369185034837562,
            "BASE": 5805173218562023798,
            "RH":   5805254131450912818,  # Robinhood purple
            "TON":  5807694622062813430,  # TON purple
        },
    },
    {   # 5: Orange
        "name": "orange",
        "crystal": 5803327589510554639,  # "X" emoji
        "kol":     5803030674126413855,   # Caller
        "mae":     5805296913620148696,   # Trade
        "bot":     5803228178197520439,   # Details
        "caller":  5803030674126413855,
        "trade":   5805296913620148696,
        "details": 5803228178197520439,
        "chain": {
            "SOL":  5800813508929003878,
            "ETH":  5803046020044563495,
            "BNB":  5800776675289473027,
            "BSC":  5800776675289473027,
            # BASE not supplied — falls back to ETH automatically (see
            # _get_alert_emoji_ids: pack["chain"].get(chain_key) or .get("ETH")).
            "RH":   5803008086893419387,  # Robinhood orange
            "TON":  5803089523768302947,  # TON orange
        },
    },
    {   # 4: Green
        "name": "green",
        "crystal": 5998815727522422466,
        "kol":     5802988952814099800,   # Caller
        "mae":     5803375340956949524,   # Trade
        "bot":     5800742203881955444,   # Details
        "caller":  5802988952814099800,
        "trade":   5803375340956949524,
        "details": 5800742203881955444,
        "twitter": 5803018102757137717,
        "dex":     5803051234134859176,
        "dev":     5802994304343350183,
        "chain": {
            "SOL":  5803072593007223702,
            "ETH":  5802960971102167764,
            "BNB":  5800982580316611108,
            "BSC":  5800982580316611108,
            "BASE": 5803191361737858109,
            "RH":   5803200140651012461,  # Robinhood green
            "TON":  5803430484042063560,  # TON green
        },
    },
]

ST_X_TEMPLATE      = "edit_x_template"
ST_ADD_DROPPED_VID = "add_dropped_vid"
ST_DROPPED_TMPL    = "edit_dropped_tmpl"
ST_ADD_PS_MEDIA    = "add_ps_media"
ST_ADD_CP_MEDIA    = "add_cp_media"
ST_ADD_CPD_MEDIA   = "add_cpd_media"   # CheesePad DETAILS (bot ke andar) media
ST_ADD_PSD_MEDIA   = "add_psd_media"   # PinkSale DETAILS (bot ke andar) media

# ─── Dropped Call feature ─────────────────────────────────────────────────────
DROPPED_CALL_EMOJI = 5807843069017464354  # SOL chain emoji — default fallback when chain unknown

# Chain-specific emojis for the two header 🔮 in dropped-call posts
DROPPED_CHAIN_EMOJIS = {
    "SOL":  5807843069017464354,
    "ETH":  5809865010771402720,
    "BNB":  5807602357575361585,
    "BSC":  5807602357575361585,
    "BASE": 5809822241487069151,
    "RH":   5807444096620444016,
    "TON":  5807455851945927306,  # TON drop call emoji
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
ST_EDIT_BTN      = "edit_button"
ST_EDIT_START    = "edit_start"
ST_EDIT_CMD      = "edit_command_text"
ST_ADD_CMD2      = "add_command_response"
ST_USERBOT_OTP      = "userbot_otp"
ST_USERBOT_2FA      = "userbot_2fa"
ST_SETTEMPLATE_EM   = "settemplate_em"
ST_BROADCAST_PICK      = "broadcast_pick"
ST_BROADCAST_MSG       = "broadcast_msg"
ST_MEDIABROADCAST_MSG  = "mediabroadcast_msg"
ST_SETPROMOLINK        = "set_promo_link"
ST_SETPROMO_EMOJI      = "set_promo_emoji"
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

async def award_points_for_milestone(channel, call_key, x_val):
    """Award incremental points when a milestone is hit. Only awards once per tier per call.
    Uses asyncio lock to prevent concurrent read-modify-write corruption.
    Skips call_keys that existed before the last champions/leaderboard reset."""
    tier_threshold, tier_pts = get_point_tier_reward(x_val)
    if tier_pts <= 0:
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
        entry["points"] = max(0, cur - 10)
        deleted.append(call_key)
        entry["deleted_post_calls"] = deleted
        pts_data[key] = entry
        save_channel_points(pts_data)
        return True

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
    if not value: return "N/A"
    if value >= 1_000_000_000: return f"${value/1_000_000_000:.2f}B"
    elif value >= 1_000_000:   return f"${value/1_000_000:.2f}M"
    elif value >= 1_000:       return f"${value/1_000:.1f}K"
    return f"${value:.0f}"

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
    # bare "@ 200k" / "at $200K"
    r"(?:@|\bat\b)\s*\$?\s*([0-9][0-9,.]*\s*[kKmMbB])\b",
]

def extract_mc_from_text(text: str) -> float:
    """Return the market cap written inside a KOL post, or 0.0 if none found."""
    if not text:
        return 0.0
    for pat in _MC_TEXT_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = (m.group(1) or "").replace(",", "").replace(" ", "")
            val = parse_mc_string(raw)
            # sanity window: ignore junk like "MC: 5" or absurd values
            if 500 <= val <= 100_000_000_000:
                return val
    return 0.0

# Tolerance band for trusting the MC written in a KOL post.
# The post value is only used when the live chart MC is in the same ballpark
# (max 3x apart). A post claiming "5K" on a token trading at 300K — or a stale
# copy-paste MC — falls outside the band and is discarded in favour of live data.
_MC_HINT_TOLERANCE = 5.0
# How long after a call a live chart quote may still be treated as "the entry".
# Beyond this the token has already moved, so a KOL-stated MC (when present)
# always wins — otherwise a 2-3 min late quote logs 90K for a 200K call.
ENTRY_MC_FRESH_SECONDS = float(os.environ.get("ENTRY_MC_FRESH_SECONDS", "20") or 20)

def _reconcile_entry_mc(hint_mc: float, live_mc: float):
    """Pick the entry MC. Returns (mc, source) where source is 'post' or 'live'.

    'post' wins when it is plausible vs live data, OR when it is HIGHER than
    live — that removes the "called at 15K, tracked at 25K" drift without
    letting a wrong/spoofed number in the post corrupt the X multiple.

    FIX: this used to compare in both directions symmetrically, which broke
    the common case of a token just launching -- DexScreener/live quotes read
    artificially LOW for the first few seconds (pool not fully indexed yet),
    so a real "called at 120K" hint was getting outvoted by a bogus "2K" live
    read and thrown away, producing a fake 60X out of nothing. A hint that is
    HIGHER than live can never inflate the caller's own X multiple in their
    favor (a higher entry only ever makes X smaller), so there is no fraud
    risk in trusting it outright. Only a hint that is suspiciously LOWER than
    live -- the actual "claims 5K, really trading at 300K" spoof case this
    function exists to catch -- still goes through the tolerance check.
    """
    try:
        hint_mc = float(hint_mc or 0)
        live_mc = float(live_mc or 0)
    except (TypeError, ValueError):
        return (0.0, "live")
    if hint_mc <= 0:
        return (live_mc, "live")
    if live_mc <= 0:
        return (hint_mc, "post")          # nothing to compare against yet
    if hint_mc >= live_mc:
        return (hint_mc, "post")          # hint higher/equal -- always trust it, no fraud risk
    ratio = live_mc / hint_mc
    if ratio <= _MC_HINT_TOLERANCE:
        return (hint_mc, "post")          # plausible → honour the KOL's entry
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
        call["rugged"] = True
        call["frozen"] = True
        call["rug_reason"] = "liquidity_pulled"
        call["rugged_at"] = datetime.utcnow().isoformat()
        logger.warning(f"🛑 RUG detected {call.get('symbol','?')} @{call.get('channel','?')} "
                       f"liq=${liq:.0f} peak=${peak_liq:.0f} mc=${mc:.0f} vol24h=${vol:.0f} — "
                       f"alerts stopped PERMANENTLY. If this token is actually still live "
                       f"(DexScreener glitch/pool switch), fix with: "
                       f"/unfreezecall {call.get('ca','')}  then  "
                       f"/recheckx @{call.get('channel','?')} {call.get('ca','')}")
        return (False, "liquidity_pulled")

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
    "<b>🔮 WIZARD SCAN – Your Crypto Radar</b>\n\n"
    "While others hunt for the next gem, WIZARD SCAN watches the callers.\n\n"
    "Our system tracks selected Telegram crypto callers around the clock and automatically detects when their calls start printing serious gains.\n\n"
    "The moment a tracked call reaches a major milestone, WIZARD SCAN delivers an instant alert to your DM and announces it in the channel.\n\n"
    "🪄 Caller leaderboards & reputation.\n"
    "🪄 Real-time winner detection.\n"
    "🪄 Instant DM alerts.\n"
    "🪄 Automated channel updates.\n"
    "🪄 Performance history tracking.\n"
    "🪄 No noise. No hype. Just results.\n\n"
    "<b>See who is actually delivering winning calls. Click Command to explore what this bot is capable of.</b>"
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
    "📌 View live Champions → <a href=\"https://t.me/WizardScan/137\">Post 137</a>\n\n\n"
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
    "<b>🔮 CHANNEL HISTORY LOOKUP</b>\n\n"
    "Instantly look up the full call history of any tracked KOL channel. How to use:\n\n"
    "🪄 Simply type: <code>/history @channelname</code>\n\n"
    "🪄 Example: <code>/history @SomeCryptoKOL</code>\n\n"
    "🪄 Or paste the @channel name directly in the chat.\n\n"
    "🔮 Once you do that, the bot will display the KOL's call history. Just make sure the KOL you're checking is already being tracked by our bot."
)

DEFAULT_LINKME_INFO = (
    "<b>🔮 LINK YOUR CHANNEL FOR ALERTS</b>\n\n"
    "If your KOL channel is tracked by Wizard Scan, you can link it to automatically receive all milestone alerts — directly inside your own channel.\n\n"
    "<b>🔮 How it works:</b>\n\n"
    "🪄 Wizard Scan tracks your KOL channel for winning calls\n"
    "🪄 When a call hits a milestone (2X, 10X, 100X...), Wizard Scan posts an alert\n"
    "🪄 With /linkme, that same alert is ALSO forwarded to your channel automatically\n"
    "🪄 Your community gets instant updates without leaving your channel\n\n"
    "<b>🔮 Setup (2 steps):</b>\n\n"
    "Step 1: Add @WIZARD_SCAN_BOT as admin to your channel (with post permission)\n"
    "Step 2: Send this command:\n"
    "<code>/linkme @your_kol_channel</code>\n\n"
    "Example:\n"
    "<code>/linkme @SomeCryptoKOL</code>\n\n"
    "<i>Only channels already tracked by Wizard Scan are eligible. Contact our team to request tracking first if needed.</i>"
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

# ─── Keyboards ────────────────────────────────────────────────────────────────
CONTACT_BUTTONS = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔮 X 🔮",       url="https://x.com/WizardScan")],
    [InlineKeyboardButton("🔮 ADMIN 🔮",   url="https://t.me/Wizard_Scan")],
    [InlineKeyboardButton("🔮 CHANNEL 🔮", url="https://t.me/WizardScan")],
])

CHAT_US_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔮  Chat With Us  🔮", callback_data="chat_us")]
])

def build_command_keyboard():
    config = load_config(); labels = config.get("button_labels", {})
    def lbl(k, d): return labels.get(k, d)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("kol_request",  "🔮 Request your KOL 🔮"),  callback_data="kol_request")],
        [InlineKeyboardButton(lbl("promo_hub",    "🔮 Promotion HUB 🔮"),     callback_data="promo_hub")],
        [InlineKeyboardButton(lbl("tracked_kols", "🔮 Tracked KOLs 🔮"),      callback_data="tracked_kols")],
        [InlineKeyboardButton(lbl("leaderboard",  "🔮 Leaderboard 🔮"),       callback_data="leaderboard")],
        [InlineKeyboardButton(lbl("dm_alerts",    "🔮 DM Alerts 🔮"),         callback_data="dm_alerts")],
        [InlineKeyboardButton(lbl("fast_track",   "🔮 Fast Track 🔮"),        callback_data="fast_track")],
        [InlineKeyboardButton(lbl("chat_us",      "🔮  Chat With Us  🔮"),    callback_data="chat_us")],
    ])

def history_keyboard(channel):
    ch = channel[:28]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔮BNB",  callback_data=f"h|{ch}|bnb"),
            InlineKeyboardButton("🔮ETH",  callback_data=f"h|{ch}|eth"),
            InlineKeyboardButton("🔮SOL",  callback_data=f"h|{ch}|sol"),
        ],
        [
            InlineKeyboardButton("🔮BASE", callback_data=f"h|{ch}|base"),
            InlineKeyboardButton("🔮TON",  callback_data=f"h|{ch}|ton"),
            InlineKeyboardButton("🔮RH",   callback_data=f"h|{ch}|rh"),
        ],
        [
            InlineKeyboardButton("🔮TOP",  callback_data=f"h|{ch}|top"),
            InlineKeyboardButton("🔮ALL",  callback_data=f"h|{ch}|all"),
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

def _pick_market_cap(pairs: list) -> float:
    """Return the most trustworthy market cap from a DexScreener pairs list.

    DexScreener reports marketCap/fdv per pair and a single stale pair can be
    badly wrong. We therefore:
      1. take marketCap from the deepest-liquidity pair,
      2. cross-check it against the median marketCap of all pairs of the same
         token — if it is more than 3x off, trust the median instead,
      3. never substitute FDV for real market cap.
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
                return median
        return primary
    return 0.0


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
    sup = sorted(sup, key=lambda p: p.get("liquidity",{}).get("usd",0) or 0, reverse=True)
    best = sup[0]
    chain  = SUPPORTED_CHAINS[best.get("chainId","").lower()]
    price  = float(best.get("priceUsd") or 0)
    mc     = _pick_market_cap(sup)
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

def _dex_breaker_open() -> bool:
    return time.time() < _DEX_429_UNTIL

def _dex_trip_breaker():
    global _DEX_429_UNTIL
    _DEX_429_UNTIL = time.time() + _DEX_429_COOLDOWN


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
    if _dex_breaker_open():
        return 429  # breaker open: request bhejne ke baghair rate-limited batao
    try:
        headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"} if fresh else None
        if fresh:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}_ts={int(time.time() * 1000)}"
        resp = _dex_session.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        logger.warning(f"DexScreener network error ({url.split('/')[-1][:16]}…): {e}")
        return None
    if resp.status_code == 429:
        _dex_trip_breaker()
        return 429
    if resp.status_code != 200:
        return None
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

def _resolve_pair_to_token(addr: str):
    """Return the baseToken mint for a pair/pool address, else None."""
    if not addr:
        return None
    if addr in _pair_token_cache:
        return _pair_token_cache[addr]
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
    if len(_pair_token_cache) > 4000:
        _pair_token_cache.clear()
    _pair_token_cache[addr] = token
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
_GT_NET   = {"SOL": "solana", "ETH": "eth", "BNB": "bsc", "BASE": "base", "TON": "ton"}
_DP_NET   = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base", "TON": "ton"}
_BE_CHAIN = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base"}
_MO_EVM   = {"ETH": "eth", "BNB": "bsc", "BASE": "base"}
_SLUG_TO_DISP = {"solana": "SOL", "ethereum": "ETH", "eth": "ETH", "bsc": "BNB",
                 "base": "BASE", "ton": "TON", "robinhood": "RH"}


def _alt_result(chain, symbol, price, mcap, name="", source=""):
    """Build a dict in the exact shape _parse_dex_pairs() returns."""
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
            price = float(a.get("price_usd") or 0)
            mc    = float(a.get("market_cap_usd") or a.get("fdv_usd") or 0)
            res   = _alt_result(ch, a.get("symbol") or "", price, mc,
                                a.get("name") or "", "geckoterminal")
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
            price = float(sm.get("price_usd") or d.get("price_usd") or 0)
            mc    = float(d.get("market_cap_usd") or sm.get("market_cap_usd")
                          or d.get("fdv") or 0)
            res = _alt_result(ch, d.get("symbol") or "", price, mc,
                              d.get("name") or "", "dexpaprika")
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
            price = float(d.get("price") or 0)
            mc    = float(d.get("marketCap") or d.get("mc") or d.get("realMc") or d.get("fdv") or 0)
            res = _alt_result(ch, d.get("symbol") or "", price, mc,
                              d.get("name") or "", "birdeye")
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
    for fn in (_src_geckoterminal, _src_dexpaprika, _src_birdeye,
               _src_moralis, _src_coinmarketcap):
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
    refresh karta hai (paid API, already purchased) instead of us batch ko
    poora chhorne ke — /defi/multi_price ek request mein 100 tak addresses
    accept karta hai. chain_map: {ca: 'SOL'|'ETH'|'BNB'|'BASE', ...} — jo
    tracked_calls['chain'] se already pata hai, guess nahi karna padta.
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
            logger.warning("DexScreener 429 bulk refresh — is tick ke baaki batches skip (breaker open)")
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
    _GECKO_NET = {"ETH": "eth", "BNB": "bsc", "BASE": "base", "RH": "robinhood",
                  "ethereum": "eth", "bsc": "bsc", "base": "base", "robinhood": "robinhood"}
    _CHAIN_DISP = {"eth": "ETH", "bsc": "BNB", "base": "BASE", "robinhood": "RH"}

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
_DISTINCT_TIMEOUT_THRESHOLD = 3

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


async def _fetch_posts_via_userbot(channel: str, limit: int = 50) -> list:
    """REALTIME-GRADE: Fetch recent posts via Telethon MTProto instead of t.me/s scraping.
    Much faster, more reliable, fetches full message text including hidden links.
    Falls back to t.me/s if userbot unavailable."""
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
        async for msg in userbot_client.iter_messages(channel, limit=limit):
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
            logger.warning(f"Userbot fetch @{channel}: {e}")
        return []

async def fetch_channel_posts(channel):
    """Fetch recent posts. Prefers Telethon MTProto (fast/reliable), falls back to t.me/s scraping."""
    # Try Telethon first — like SpyDefi/Kolscope, direct MTProto is far more reliable
    now = _t.time()
    if _FLOOD_UNTIL.get(channel.lower(), 0) > now or _FLOOD_UNTIL.get("__global__", 0) > now:
        return await asyncio.to_thread(_fetch_posts_sync, channel)
    if userbot_client and userbot_client.is_connected():
        posts = await _fetch_posts_via_userbot(
            channel, limit=max(3, int(os.environ.get("SCAN_POST_LIMIT", "5") or 5)))
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

# ═══════════════════════════════════════════════════════════════════════════
# LAUNCHPAD SUPPORT — PinkSale / GemPad / CheesePad
# Agar koi tracked caller in launchpads ka project post kare to bot us sale ki
# poori details (soft cap, hard cap, min/max buy, sale type, rate, dates) fetch
# kar ke channel me post karta hai.
# ═══════════════════════════════════════════════════════════════════════════

_LP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
          "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"}

_LP_CHAIN_ALIASES = {
    "bsc": "BSC", "bnb": "BSC", "binance": "BSC", "56": "BSC", "97": "BSC-Testnet",
    "eth": "ETH", "ethereum": "ETH", "1": "ETH",
    "base": "BASE", "8453": "BASE",
    "arb": "ARBITRUM", "arbitrum": "ARBITRUM", "42161": "ARBITRUM",
    "polygon": "POLYGON", "matic": "POLYGON", "137": "POLYGON",
    "avax": "AVAX", "avalanche": "AVAX", "43114": "AVAX",
    "solana": "SOL", "sol": "SOL",
    "ton": "TON", "core": "CORE", "opbnb": "opBNB", "pulse": "PULSECHAIN",
}

def _lp_chain_norm(v):
    if not v: return ""
    return _LP_CHAIN_ALIASES.get(str(v).strip().lower(), str(v).strip().upper())

# Any pinksale.finance link. Path shapes seen in the wild:
#   /launchpad/0x..            /launchpad/bsc/0x..      /solana/launchpad/<addr>
#   /fairlaunch/0x..           /en/launchpad/0x..       /launchpad/0x..?chain=BSC
#   /pinklock/..  (ignored)    /base/fairlaunch/0x..
# One tolerant matcher instead of a fixed segment order — a strict pattern was
# why only some callers' PinkSale posts got alerted.
_PINKSALE_RE = re.compile(
    r'(?:https?://)?(?:[\w-]+\.)*pinksale\.(?:finance|com|io|app|net|org|xyz|co|fun)/[^\s<>"\']*'
    r'(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})[^\s<>"\']*', re.IGNORECASE)
_PS_ADDR_RE  = re.compile(r'(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})')
_PS_TYPE_RE  = re.compile(r'/(launchpad|fairlaunch|fair-launch|presale|sale)(?:/|$|\?)', re.IGNORECASE)

# gempad.app/presale?address=0x..&chain=.. | gempad.app/launchpad/0x..
_GEMPAD_RE = re.compile(
    r'(?:https?://)?(?:www\.)?gempad\.(?:app|io)/[^\s]*?'
    r'(?:address=|presale/|launchpad/|sale/)((?:0x[a-fA-F0-9]{40})|[1-9A-HJ-NP-Za-km-z]{32,44})'
    r'(?:[^\s]*)?', re.IGNORECASE)
# CheesePad links, any TLD / any path shape:
#   cheesepad.ai/sale/bsc/0x..     cheesepad.io/launchpad/0x..
#   www.cheesepad.ai/sale/0x..?ref=..   app.cheesepad.ai/presale/0x..
_CHEESEPAD_RE = re.compile(
    r'(?:https?://)?(?:[\w-]+\.)*cheese(?:pad|-pad|_pad)?\.'
    r'(?:ai|io|app|finance|xyz|com|net|org|fun|co|gg|pro|money|top|live|club)/[^\s<>"\']*'
    r'(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})[^\s<>"\']*', re.IGNORECASE)


_LP_CHAIN_QS_RE = re.compile(r'[?&](?:chain|chainId|network|net)=([A-Za-z0-9-]+)', re.IGNORECASE)

def detect_launchpad(text: str):
    """Detect a PinkSale / GemPad / CheesePad sale link in a KOL post.
    Returns {platform, url, address, chain} or None."""
    if not text:
        return None
    for platform, rx in (("PinkSale", _PINKSALE_RE), ("GemPad", _GEMPAD_RE), ("CheesePad", _CHEESEPAD_RE)):
        m = rx.search(text)
        if not m:
            continue
        url = m.group(0)
        if not url.lower().startswith("http"):
            url = "https://" + url.lstrip("/")
        if platform in ("PinkSale", "CheesePad"):
            path = re.sub(r'^https?://', '', url, flags=re.IGNORECASE)
            path = path.split("/", 1)[1] if "/" in path else ""
            am = _PS_ADDR_RE.search(path)
            addr = am.group(1) if am else ""
            if not addr:
                continue
            tm = _PS_TYPE_RE.search("/" + path)
            sale_type = (tm.group(1) if tm else "").replace("-", " ")
            chain = ""
            for seg in path.split("?")[0].split("/"):
                s = seg.strip().lower()
                if s and s in _LP_CHAIN_ALIASES:
                    chain = _lp_chain_norm(s)
                    break
        else:
            addr = m.group(1); sale_type = ""; chain = ""

        if not chain:
            q = _LP_CHAIN_QS_RE.search(url)
            if q: chain = _lp_chain_norm(q.group(1))
        if not chain:
            chain = "SOL" if not addr.startswith("0x") else "BSC"
        return {"platform": platform, "url": url, "address": addr,
                "chain": chain, "sale_type_hint": (sale_type or "").title()}
    return None

# ── Field extraction helpers ────────────────────────────────────────────────
_LP_KEYMAP = {
    "softcap":   ("soft_cap",  ("softcap", "soft_cap", "softCap", "minCap", "min_cap")),
    "hardcap":   ("hard_cap",  ("hardcap", "hard_cap", "hardCap", "maxCap", "max_cap")),
    "minbuy":    ("min_buy",   ("minbuy", "min_buy", "minBuy", "minContribution", "min_contribution",
                                "minBuyPerUser", "minAmount")),
    "maxbuy":    ("max_buy",   ("maxbuy", "max_buy", "maxBuy", "maxContribution", "max_contribution",
                                "maxBuyPerUser", "maxAmount")),
    "saletype":  ("sale_type", ("saletype", "sale_type", "saleType", "type", "poolType", "pool_type")),
    "rate":      ("rate",      ("rate", "presaleRate", "presale_rate", "tokenRate", "swapRate")),
    "listing":   ("listing_rate", ("listingRate", "listing_rate")),
    "liquidity": ("liquidity", ("liquidity", "liquidityPercent", "liquidity_percent", "lpPercent")),
    "lock":      ("lock_time", ("lockTime", "lock_time", "liquidityLockup", "lockup", "lockDays")),
    "start":     ("start_time", ("startTime", "start_time", "openTime", "start")),
    "end":       ("end_time",  ("endTime", "end_time", "closeTime", "end")),
    "token":     ("token",     ("tokenAddress", "token_address", "token", "tokenCA")),
    "symbol":    ("symbol",    ("symbol", "tokenSymbol", "token_symbol")),
    "name":      ("name",      ("name", "tokenName", "token_name", "poolName")),
    "currency":  ("currency",  ("currency", "baseToken", "payToken", "quoteSymbol")),
    "telegram":  ("telegram",  ("telegram", "telegramUrl", "telegram_url", "telegramLink",
                                "telegram_chat_url", "tgLink", "tg")),
    "twitter":   ("twitter",   ("twitter", "twitterUrl", "twitter_url", "twitterLink",
                                "x", "xUrl", "x_url")),
    "website":   ("website",   ("website", "websiteUrl", "website_url", "websiteLink",
                                "site", "web")),
    "supply":    ("supply",    ("totalSupply", "total_supply", "supply", "tokenSupply",
                                "formattedTotalSupply")),
}

_LP_TG_RE   = re.compile(r'https?://(?:www\.)?t\.me/[A-Za-z0-9_+/]{3,}', re.IGNORECASE)
_LP_TW_RE   = re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]{2,}', re.IGNORECASE)
_LP_SITE_RE = re.compile(r'https?://(?:www\.)?[A-Za-z0-9-]+\.[A-Za-z]{2,}(?:/[^\s"\'<>]*)?')
_LP_SITE_SKIP = ("t.me", "twitter.com", "x.com", "pinksale", "cheesepad", "gempad",
                 "telegram.org", "youtube.com", "google.", "cloudflare", "gstatic",
                 "jsdelivr", "githubusercontent", "dexscreener", "dextools", "medium.com",
                 "discord", "instagram", "facebook", "tiktok", "linktr")

def _lp_parse_socials(text: str, out: dict) -> dict:
    """Pull Telegram / Twitter / Website links out of HTML or the KOL post."""
    if not text:
        return out
    if not out.get("telegram"):
        m = _LP_TG_RE.search(text)
        if m:
            out["telegram"] = m.group(0)
    if not out.get("twitter"):
        m = _LP_TW_RE.search(text)
        if m:
            out["twitter"] = m.group(0)
    if not out.get("website"):
        for m in _LP_SITE_RE.finditer(text):
            u = m.group(0).rstrip('.,)"\'')
            low = u.lower()
            if any(s in low for s in _LP_SITE_SKIP):
                continue
            if len(u) > 90:
                continue
            out["website"] = u
            break
    return out

def _lp_walk_json(obj, out, depth=0):
    """Recursively pull known sale fields out of any JSON shape."""
    if depth > 6 or out is None:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                _lp_walk_json(v, out, depth + 1); continue
            lk = str(k)
            for _, (field, aliases) in _LP_KEYMAP.items():
                if lk in aliases and v not in (None, "", 0) and not out.get(field):
                    out[field] = v
    elif isinstance(obj, list):
        for it in obj[:20]:
            _lp_walk_json(it, out, depth + 1)
    return out

_LP_LABELS = [
    ("soft_cap",     r'soft\s*cap'),
    ("hard_cap",     r'hard\s*cap'),
    ("min_buy",      r'min(?:imum)?\s*(?:buy|contribution|amount)'),
    ("max_buy",      r'max(?:imum)?\s*(?:buy|contribution|amount)'),
    ("sale_type",    r'sale\s*type|pool\s*type|presale\s*type'),
    ("rate",         r'presale\s*rate|swap\s*rate|token\s*rate|(?<!listing\s)rate'),
    ("listing_rate", r'listing\s*rate'),
    ("liquidity",    r'liquidity(?:\s*percent)?'),
    ("lock_time",    r'(?:liquidity\s*)?lock(?:up|\s*time|\s*period)?'),
    ("start_time",   r'start\s*(?:time|date)|sale\s*starts?'),
    ("end_time",     r'end\s*(?:time|date)|sale\s*ends?'),
]

# Amount fields must never be filled with a bare, unit-less number scraped out
# of random page text — that was the reason PinkSale posts showed nonsense like
# "Hard Cap 4 SOL / Soft Cap 5 SOL" for a sale that has no hard cap at all.
_LP_AMOUNT_FIELDS = ("soft_cap", "hard_cap", "min_buy", "max_buy")
_LP_AMOUNT_OK_RE = re.compile(
    r'^\s*\d[\d.,]*\s*(BNB|ETH|SOL|TON|MATIC|AVAX|CORE|USDT|USDC|BUSD|USD1|WBNB|WETH)\b',
    re.IGNORECASE)

def _lp_parse_labeled(text: str, out: dict, skip=()) -> dict:
    """Pull 'Soft Cap: 50 BNB' style values from HTML or from the KOL post itself."""
    if not text:
        return out
    flat = re.sub(r'<[^>]+>', ' ', text)
    flat = re.sub(r'&nbsp;?', ' ', flat)
    flat = re.sub(r'[ \t\u00a0]+', ' ', flat)
    for field, pattern in _LP_LABELS:
        if out.get(field) or field in skip:
            continue
        m = re.search(r'(?:' + pattern + r')\s*[:\-–=]?\s*([^\n\r<|]{1,60})', flat, re.IGNORECASE)
        if m:
            val = m.group(1).strip(" .•·-–—|")
            if not val or re.fullmatch(r'[^0-9A-Za-z]+', val):
                continue
            if field in _LP_AMOUNT_FIELDS and not _LP_AMOUNT_OK_RE.match(val):
                # "4" / "N/A" / "--" style junk → field stays empty, and the
                # post then honestly says "No Hard Cap" / "No Min Buy".
                continue
            out[field] = val[:60]
    return out


def _lp_sanity_amounts(out: dict) -> dict:
    """Drop impossible cap / buy-limit combinations instead of publishing them."""
    try:
        soft = _ps_num(out.get("soft_cap"))
        hard = _ps_num(out.get("hard_cap"))
        mnb  = _ps_num(out.get("min_buy"))
        mxb  = _ps_num(out.get("max_buy"))
        # A hard cap can never be smaller than the soft cap → the scrape is wrong.
        if soft and hard and hard < soft:
            out.pop("hard_cap", None)
            out.pop("soft_cap", None)
        if mnb is not None and mnb <= 0:
            out.pop("min_buy", None)
        if mxb is not None and mxb <= 0:
            out.pop("max_buy", None)
        mnb = _ps_num(out.get("min_buy")); mxb = _ps_num(out.get("max_buy"))
        if mnb and mxb and mxb < mnb:
            out.pop("max_buy", None)
        for f in ("soft_cap", "hard_cap"):
            n = _ps_num(out.get(f))
            if n is not None and n <= 0:
                out.pop(f, None)
    except Exception:
        pass
    return out

def _lp_fmt_val(v):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        try:
            if 1_000_000_000_000 <= v < 4_000_000_000_000:  # ms timestamp
                return datetime.utcfromtimestamp(v / 1000).strftime("%d %b %Y %H:%M UTC")
            if 1_000_000_000 <= v < 4_000_000_000:          # sec timestamp
                return datetime.utcfromtimestamp(v).strftime("%d %b %Y %H:%M UTC")
        except Exception:
            pass
        if float(v).is_integer():
            return f"{int(v):,}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    s = str(v).strip()
    return s[:70]

_LP_API_CANDIDATES = {
    "PinkSale": [
        "https://api.pinksale.finance/api/v1/pool/{chain}/{addr}",
        "https://api.pinksale.finance/api/v1/pool/{addr}?chain={chain}",
        "https://api.pinksale.finance/api/v1/pools/{addr}",
    ],
    "GemPad": [
        "https://api.gempad.app/api/presale/{addr}",
        "https://api.gempad.app/api/v1/sale/{addr}?chain={chain}",
        "https://gempad.app/api/presale/{addr}",
    ],
    "CheesePad": [
        "https://api.cheesepad.ai/api/v1/sale/{addr}?chain={chain}",
        "https://api.cheesepad.ai/api/sale/{addr}",
        "https://www.cheesepad.ai/api/sale/{chain}/{addr}",
        "https://www.cheesepad.ai/api/sale/{addr}",
        "https://api.cheesepad.io/api/presale/{addr}",
        "https://cheesepad.io/api/presale/{addr}",
        "https://api.cheesepad.app/api/v1/sale/{addr}",
    ],

}

# ── On-chain sale reader (EVM) ──────────────────────────────────────────────
# PinkSale's public API is closed and the website is a JS app, so scraping gave
# N/A for soft/hard cap, min/max buy and dates. The presale contract itself is
# public, so we just read it over a free RPC — no API key, no env variable.
_PS_RPCS = {
    "BSC":      ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io",
                 "https://rpc.ankr.com/bsc"],
    "ETH":      ["https://eth.llamarpc.com", "https://rpc.ankr.com/eth", "https://cloudflare-eth.com"],
    "BASE":     ["https://mainnet.base.org", "https://base.llamarpc.com"],
    "ARBITRUM": ["https://arb1.arbitrum.io/rpc"],
    "POLYGON":  ["https://polygon-rpc.com"],
    "AVAX":     ["https://api.avax.network/ext/bc/C/rpc"],
    "CORE":     ["https://rpc.coredao.org"],
    "opBNB":    ["https://opbnb-mainnet-rpc.bnbchain.org"],
}
# selector → field. Several aliases per field: PinkSale has many pool versions.
_PS_SELECTORS = [
    ("0x906a26e0", "soft_cap"),          # softCap()
    ("0xfb86a404", "hard_cap"),          # hardCap()
    ("0xaaffadf3", "min_buy"),           # minContribution()
    ("0x7107d7a6", "min_buy"),           # minBuy()
    ("0x8d3d6576", "max_buy"),           # maxContribution()
    ("0x70db69d6", "max_buy"),           # maxBuy()
    ("0x78e97925", "start_time"),        # startTime()
    ("0x3197cbb6", "end_time"),          # endTime()
    ("0xc5c4744c", "raised"),            # totalRaised()
    ("0x9f550293", "raised"),            # getTotalRaised()
    ("0xc59ee1dc", "raised"),            # raisedAmount()
    ("0x716b301d", "raised"),            # totalBnbRaised()
    ("0xb0683755", "liquidity"),         # liquidityPercent()
    ("0xa724bd30", "lock_time"),         # liquidityLockDays()
    ("0x2c4e722e", "rate"),              # rate()
    ("0xa20ecbbb", "listing_rate"),      # listingRate()
    ("0x184d69ab", "whitelist"),         # isWhitelistEnabled()
    ("0x51fb012d", "whitelist"),         # whitelistEnabled()
]
_PS_SEL_TOKEN = ("0xfc0c546a", "0x9d76ea58")   # token() / tokenAddress()
_PS_SEL_NAME, _PS_SEL_SYMBOL, _PS_SEL_DEC = "0x06fdde03", "0x95d89b41", "0x313ce567"

def _ps_rpc_batch(rpc: str, calls: list, timeout=10):
    """calls = [(to, data)] → list of hex results (None on failure)."""
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"]}
               for i, (to, data) in enumerate(calls)]
    try:
        r = requests.post(rpc, json=payload, timeout=timeout,
                          headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return [None] * len(calls)
        data = r.json()
        if isinstance(data, dict):
            data = [data]
        res = [None] * len(calls)
        for item in data:
            i = item.get("id")
            if isinstance(i, int) and 0 <= i < len(res):
                res[i] = item.get("result")
        return res
    except Exception:
        return [None] * len(calls)

def _ps_hex_uint(h):
    try:
        if not h or h == "0x":
            return None
        v = int(h[:66], 16)
        return v if v else None
    except Exception:
        return None

def _ps_hex_addr(h):
    try:
        if not h or len(h) < 66:
            return ""
        a = "0x" + h[2:66][-40:]
        return "" if int(a, 16) == 0 else a
    except Exception:
        return ""

def _ps_hex_string(h):
    """Decode an ABI-encoded string (or bytes32 fallback)."""
    try:
        if not h or h == "0x":
            return ""
        body = h[2:]
        if len(body) >= 128:
            off = int(body[0:64], 16) * 2
            ln  = int(body[off:off + 64], 16) * 2
            raw = bytes.fromhex(body[off + 64: off + 64 + ln])
            return raw.decode("utf-8", "ignore").strip()
        return bytes.fromhex(body).decode("utf-8", "ignore").strip("\x00").strip()
    except Exception:
        return ""

def _ps_onchain_details(info: dict) -> dict:
    """Read the presale contract directly. Returns {} for non-EVM chains."""
    addr  = (info.get("address") or "").strip()
    chain = (info.get("chain") or "").upper()
    if not addr.startswith("0x"):
        return {}
    rpcs = _PS_RPCS.get(chain) or []
    if not rpcs:
        # unknown chain → try the big ones until one answers
        rpcs = _PS_RPCS["BSC"] + _PS_RPCS["ETH"] + _PS_RPCS["BASE"]
    # Native-coin amounts are stored in wei → always /1e18.
    # `rate` / `listing_rate` are token counts and must NEVER be divided,
    # warna PinkSale details me galat ya khaali values aati thin.
    _WEI_FIELDS  = ("soft_cap", "hard_cap", "min_buy", "max_buy", "raised")
    _NEED = {f for _, f in _PS_SELECTORS} | {"token"}

    out = {}
    # Ek RPC ke jawab par ruk jana hi asli bug tha: pehla RPC agar aadhe fields
    # None deta tha to baaki hamesha N/A reh jate the. Ab har RPC (aur zaroorat
    # pare to doosri chains) se missing fields bhare jate hain.
    for rpc in rpcs:
        if _NEED <= set(out.keys()):
            break
        res = _ps_rpc_batch(rpc, [(addr, sel) for sel, _ in _PS_SELECTORS] +
                                 [(addr, s) for s in _PS_SEL_TOKEN])
        if all(x is None for x in res):
            continue
        for (sel, field), val in zip(_PS_SELECTORS, res):
            n = _ps_hex_uint(val)
            if n is None or out.get(field):
                continue
            if field == "whitelist":
                out[field] = True
            elif field in ("start_time", "end_time"):
                if 1_000_000_000 < n < 4_000_000_000:
                    out[field] = n
            elif field in ("liquidity", "lock_time"):
                if field == "liquidity" and n > 100:
                    continue          # percent hi ho sakta hai, kuch aur nahi
                out[field] = n
            elif field in _WEI_FIELDS:
                out[field] = n / 1e18
            else:
                out[field] = n
        tok = out.get("token") or ""
        if not tok:
            for val in res[len(_PS_SELECTORS):]:
                tok = _ps_hex_addr(val)
                if tok:
                    break
        if tok and not out.get("symbol"):
            out["token"] = tok
            nm = _ps_rpc_batch(rpc, [(tok, _PS_SEL_NAME), (tok, _PS_SEL_SYMBOL),
                                     (tok, _PS_SEL_DEC)])
            name, sym = _ps_hex_string(nm[0]), _ps_hex_string(nm[1])
            dec = _ps_hex_uint(nm[2])
            if name:
                out["name"] = name
            if sym:
                out["symbol"] = sym
            if dec and dec <= 36:
                out["decimals"] = dec
        elif tok:
            out["token"] = tok
    if not out:
        logger.warning(f"PinkSale on-chain read empty for {addr} ({chain or 'unknown chain'})")
    return out

# ── CheesePad official API ──────────────────────────────────────────────────
# CheesePad ka public API (wahi jo unki website use karti hai). Pehle bot sirf
# guessy endpoints try karta tha, isi liye har token pe "N/A" / "No Hard Cap"
# aa raha tha. Ab soft cap, hard cap, min/max buy, dates, rate sab yahan se
# seedha aate hain.
_CP_API_BASE = "https://api.cheesepad.ai"
_CP_CHAIN_IDS = {
    "BSC": 56, "BNB": 56, "ETH": 1, "ETHEREUM": 1, "BASE": 8453,
    "ARBITRUM": 42161, "POLYGON": 137, "AVAX": 43114, "CORE": 1116,
    "opBNB": 204, "OPBNB": 204,
}
_CP_ID_TO_CHAIN = {56: "BSC", 1: "ETH", 8453: "BASE", 42161: "ARBITRUM",
                   137: "POLYGON", 43114: "AVAX", 1116: "CORE", 204: "opBNB"}


def _cp_fmt_num(n):
    try:
        n = float(n)
    except Exception:
        return ""
    if n <= 0:
        return ""
    if n < 1:
        return f"{n:,.6f}".rstrip("0").rstrip(".")
    if n < 1000:
        return f"{n:,.4f}".rstrip("0").rstrip(".")
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _cp_amount(raw, formatted, decimals, cur):
    """Return '85 BNB' style text from either a formatted number or a wei string."""
    n = None
    if isinstance(formatted, (int, float)) and not isinstance(formatted, bool):
        n = float(formatted)
    elif raw not in (None, "", 0, "0"):
        try:
            n = float(int(str(raw))) / (10 ** int(decimals if decimals is not None else 18))
        except Exception:
            n = None
    txt = _cp_fmt_num(n) if n is not None else ""
    if not txt:
        return ""
    return f"{txt} {cur}".strip()


def _cp_map_pool(j: dict) -> dict:
    """Map a CheesePad full_info payload to the bot's detail fields."""
    if not isinstance(j, dict) or not j:
        return {}
    pool = j.get("pool") if isinstance(j.get("pool"), dict) else j
    curr = j.get("currency") if isinstance(j.get("currency"), dict) else {}
    tok  = j.get("token") if isinstance(j.get("token"), dict) else {}
    cur_sym = (curr.get("symbol") or "").strip()
    cur_dec = curr.get("decimals", 18)
    tok_dec = tok.get("decimals", 18)
    out = {}

    def put(field, raw_key, fmt_key, dec=cur_dec, cur=None):
        v = _cp_amount(pool.get(raw_key), pool.get(fmt_key), dec,
                       cur_sym if cur is None else cur)
        if v:
            out[field] = v

    put("soft_cap", "softCap", "formattedSoftCap")
    put("hard_cap", "hardCap", "formattedHardCap")
    put("min_buy",  "minContribution", "formattedMinContribution")
    put("max_buy",  "maxContribution", "formattedMaxContribution")
    put("raised",   "totalRaised", "formattedTotalRaised")

    rate = _cp_amount(pool.get("rate"), pool.get("formattedRate"), tok_dec, "")
    if rate:
        out["rate"] = f"{rate} {tok.get('symbol','')}".strip()
    lrate = _cp_amount(pool.get("listingRate"), pool.get("formattedListingRate"), tok_dec, "")
    if lrate:
        out["listing_rate"] = f"{lrate} {tok.get('symbol','')}".strip()

    for f, k in (("start_time", "startTime"), ("end_time", "endTime")):
        v = pool.get(k)
        try:
            if v and 1_000_000_000 < float(v) < 4_000_000_000:
                out[f] = int(float(v))
        except Exception:
            pass

    lp = pool.get("listingPercent")
    if isinstance(lp, (int, float)) and lp:
        out["liquidity"] = f"{int(lp)}%"
    ld = pool.get("lockDuration")
    try:
        ld = int(str(ld or 0))
        if ld > 0:
            out["lock_time"] = (f"{ld // 86400} days" if ld >= 86400
                                else f"{max(1, ld // 3600)} hours")
    except Exception:
        pass

    if tok.get("name"):
        out["name"] = tok["name"]
    if tok.get("symbol"):
        out["symbol"] = tok["symbol"]
    if tok.get("address"):
        out["token"] = tok["address"]
    if cur_sym:
        out["currency"] = cur_sym

    # ── Token supply (shown in the bot's details view) ──
    _sup = _cp_amount(tok.get("totalSupply") or tok.get("supply"),
                      tok.get("formattedTotalSupply"), tok_dec,
                      tok.get("symbol", "") or "")
    if _sup:
        out["supply"] = _sup
    _tt = _cp_amount(pool.get("totalToken") or pool.get("hardCapToken"),
                     pool.get("formattedTotalToken"), tok_dec,
                     tok.get("symbol", "") or "")
    if _tt:
        out["total_token"] = _tt

    tiers = pool.get("whitelistTierCount")
    if isinstance(tiers, (int, float)) and tiers and tiers > 0:
        out["whitelist"] = True
        out["sale_type"] = "Whitelist"
    elif pool.get("requireContributorValidation"):
        out["whitelist"] = True
        out["sale_type"] = "Whitelist"
    else:
        out["sale_type"] = out.get("sale_type") or "Public"

    st = pool.get("state")
    if isinstance(st, (int, float)):
        out["status"] = {0: "Upcoming", 1: "Live", 2: "Ended",
                         3: "Cancelled", 4: "Completed"}.get(int(st), "")
    cid = j.get("chainId")
    if cid in _CP_ID_TO_CHAIN:
        out["_chain"] = _CP_ID_TO_CHAIN[cid]
    return {k: v for k, v in out.items() if v not in (None, "")}


def _cp_api_details(info: dict) -> dict:
    """Fetch real CheesePad sale details (launchpad pool or microsale)."""
    addr = (info.get("address") or "").strip()
    if not addr.startswith("0x"):
        return {}
    chain = (info.get("chain") or "").upper()
    ids = []
    cid = _CP_CHAIN_IDS.get(chain)
    if cid:
        ids.append(cid)
    for c in (56, 1, 8453, 42161, 137, 43114, 204, 1116):
        if c not in ids:
            ids.append(c)
    url = (info.get("url") or "").lower()
    paths = ["/microsale/full_info", "/launchpad/full_info"] if "microsale" in url \
            else ["/launchpad/full_info", "/microsale/full_info"]
    for path in paths:
        for c in ids:
            try:
                key = "fundManager" if "microsale" in path else "poolAddress"
                r = requests.get(_CP_API_BASE + path,
                                 params={"chainId": c, key: addr},
                                 headers=_LP_UA, timeout=10)
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception:
                continue
            mapped = _cp_map_pool(data if isinstance(data, dict) else {})
            if mapped:
                logger.info(f"🧀 CheesePad API hit {path} chainId={c} {addr}")
                return mapped
    logger.warning(f"CheesePad API: no data for {addr}")
    return {}


def fetch_launchpad_details_sync(info: dict, post_text: str = "") -> dict:
    """Best-effort sale details.
    Order: on-chain contract → platform API → page HTML → KOL post text."""
    out = {}
    addr  = info.get("address", "")
    chain = info.get("chain", "")
    try:
        out.update({k: v for k, v in (_ps_onchain_details(info) or {}).items() if v not in (None, "")})
    except Exception as e:
        logger.warning(f"on-chain sale read failed: {e}")

    # CheesePad: official API is authoritative — it overrides the on-chain guess
    if info.get("platform") == "CheesePad":
        try:
            cp = _cp_api_details(info)
        except Exception as e:
            cp = {}
            logger.warning(f"CheesePad API read failed: {e}")
        if cp:
            if cp.get("_chain") and not info.get("chain"):
                info["chain"] = cp["_chain"]
            cp.pop("_chain", None)
            out.update(cp)
            if not out.get("sale_type") and info.get("sale_type_hint"):
                out["sale_type"] = info["sale_type_hint"]
            _lp_parse_socials(post_text or "", out)
            try:
                r = requests.get(info.get("url", ""), headers=_LP_UA, timeout=8)
                if r.status_code == 200 and r.text:
                    _lp_parse_socials(r.text, out)
            except Exception:
                pass
            return out

    for url_tpl in _LP_API_CANDIDATES.get(info.get("platform", ""), []):
        try:
            u = url_tpl.format(addr=addr, chain=chain.lower())
            r = requests.get(u, headers=_LP_UA, timeout=8)
            if r.status_code == 200 and "json" in (r.headers.get("content-type", "") or "").lower():
                _lp_walk_json(r.json(), out)
                if out.get("hard_cap") or out.get("soft_cap"):
                    break
        except Exception:
            continue
    # PinkSale's client-rendered HTML includes unrelated tokenomics numbers
    # (and sometimes data from recommendation cards). Do not treat that page
    # text as the sale contract's caps or limits.
    _ps_blind = (info.get("platform") == "PinkSale"
                 and not (out.get("hard_cap") or out.get("soft_cap")))
    if (info.get("platform") != "PinkSale" or _ps_blind) and not (
        out.get("hard_cap") and out.get("min_buy") and out.get("telegram")
    ):
        try:
            r = requests.get(info.get("url", ""), headers=_LP_UA, timeout=10)
            if r.status_code == 200 and r.text:
                html = r.text
                for m in re.finditer(r'<script[^>]*(?:__NEXT_DATA__|application/json)[^>]*>(.*?)</script>',
                                     html, re.DOTALL | re.IGNORECASE):
                    try:
                        _lp_walk_json(json.loads(m.group(1)), out)
                    except Exception:
                        pass
                _lp_parse_labeled(html, out)
                _lp_parse_socials(html, out)
        except Exception:
            pass
    # PinkSale pages often contain unrelated tokenomics/marketing numbers in
    # rendered HTML. Never use those as sale details. For GemPad/CheesePad the
    # post is still useful as a social-link fallback, but PinkSale amounts must
    # come only from the official API/on-chain sale contract.
    if info.get("platform") != "PinkSale":
        _lp_parse_labeled(post_text or "", out, skip=_LP_AMOUNT_FIELDS)
        _lp_parse_socials(post_text or "", out)
    else:
        _lp_parse_socials(post_text or "", out)
    if not out.get("sale_type") and info.get("sale_type_hint"):
        out["sale_type"] = info["sale_type_hint"]
    _lp_sanity_amounts(out)
    return out

def build_launchpad_post(channel: str, msg_id, info: dict, details: dict) -> str:
    """HTML post for a launchpad (presale/fairlaunch) call."""
    plat  = info.get("platform", "Launchpad")
    chain = info.get("chain", "")
    addr  = info.get("address", "")
    url   = info.get("url", "")
    name  = _lp_fmt_val(details.get("name")) or _lp_fmt_val(details.get("symbol")) or "New Project"
    sym   = _lp_fmt_val(details.get("symbol"))
    cur   = _lp_fmt_val(details.get("currency"))

    def line(label, key, suffix=""):
        v = _lp_fmt_val(details.get(key))
        if not v:
            return ""
        if suffix and suffix.lower() not in v.lower():
            v = f"{v} {suffix}"
        return f"🔸 <b>{label}:</b> {v}\n"

    head = f"🚀 <b>{plat.upper()} LAUNCH DETECTED</b> 🚀\n\n"
    head += f"🪙 <b>Project:</b> {name}" + (f" (${sym})" if sym else "") + "\n"
    head += f"⛓ <b>Chain:</b> {chain}\n"
    head += f"👤 <b>Caller:</b> @{html.escape(_display_handle(channel))}\n\n"

    body  = line("Sale Type", "sale_type")
    body += line("Soft Cap", "soft_cap", cur)
    body += line("Hard Cap", "hard_cap", cur)
    body += line("Min Buy", "min_buy", cur)
    body += line("Max Buy", "max_buy", cur)
    body += line("Presale Rate", "rate")
    body += line("Listing Rate", "listing_rate")
    body += line("Liquidity", "liquidity")
    body += line("Lockup", "lock_time")
    body += line("Starts", "start_time")
    body += line("Ends", "end_time")
    if not body:
        body = "🔸 <i>Sale details launchpad page pe live hain — link niche.</i>\n"

    tail  = f"\n📄 <b>Sale Address:</b>\n<code>{addr}</code>\n"
    tok = _lp_fmt_val(details.get("token"))
    if tok and tok.lower() != addr.lower():
        tail += f"🧾 <b>Token:</b> <code>{tok}</code>\n"
    tail += f"\n🔗 <a href=\"{url}\">Open on {plat}</a>"
    if channel and msg_id:
        tail += f" | <a href=\"https://t.me/{channel}/{msg_id}\">Caller Post</a>"
    tail += "\n\n⚠️ <i>DYOR — presales are high risk.</i>"
    return head + body + tail

_LAUNCHPAD_SEEN = set()

async def send_launchpad_alert(bot, channel: str, msg_id, info: dict, post_text: str = ""):
    """Fetch sale details and post them to the main channel."""
    try:
        key = f"{info.get('platform','')}|{info.get('address','').lower()}"
        if key in _LAUNCHPAD_SEEN:
            return False
        _LAUNCHPAD_SEEN.add(key)
        details = await asyncio.to_thread(fetch_launchpad_details_sync, info, post_text)
        text = build_launchpad_post(channel, msg_id, info, details)

        media = (load_config().get("launchpad_media", {}) or {}).get(
            info.get("platform", "").lower()) or (load_config().get("launchpad_media", {}) or {}).get("global")
        if isinstance(media, dict) and media.get("file_id"):
            try:
                if media.get("type") == "photo":
                    await bot.send_photo(TARGET_CHANNEL, photo=media["file_id"],
                                         caption=text[:1024], parse_mode="HTML")
                else:
                    await bot.send_video(TARGET_CHANNEL, video=media["file_id"],
                                         caption=text[:1024], parse_mode="HTML")
                logger.info(f"🚀 Launchpad post sent ({info.get('platform')}) @{channel}")
                return True
            except Exception as e:
                logger.warning(f"Launchpad media send failed, text fallback: {e}")
        await bot.send_message(TARGET_CHANNEL, text, parse_mode="HTML",
                               disable_web_page_preview=False)
        logger.info(f"🚀 Launchpad post sent ({info.get('platform')}) @{channel}")
        return True
    except Exception as e:
        logger.error(f"send_launchpad_alert failed: {e}")
        return False



# ═══════════════════════════════════════════════════════════════════════════
# PINKSALE PREMIUM ALERTS  (owner panel: /ownerhelpPS)
#   • premium-emoji template (exact style set by owner)
#   • rotating media (up to 10)
#   • referral link injection on the Pinksale button/link
#   • "Details" deep-link → full project breakdown inside the bot
#   • copy of the channel post forwarded to the caller's DM
#   • post-presale live watch → token joins normal X tracking once it launches
# ═══════════════════════════════════════════════════════════════════════════

PS_EMOJI = {
    # ── owner-approved premium emoji pack (PinkSale) ──
    "ca":       5769424182827820166,   # 🔶
    "time":     5769498567366418287,   # ⌚
    "buy":      5769249592407236853,   # 🚀
    "cap":      5769277870471913210,   # 🎯
    "info":     5769119699711304008,   # 📢
    "details":  5769174559328575136,   # Details
    "balloon":  5769339752360715284,   # 🎈
    "lock":     5769363598019140888,   # 🔒
    "fire":     5769583457395024807,   # 🔥
    "globe":    5769653117469598004,   # 🌍
    "coin":     5769287899220548660,   # 🪙
    "hand":     5769324320543220241,   # 🤝
    "crystal":  5769446284729523754,   # 🔮
    "flag":     5769378772138597984,   # 🏁
    # chain emojis
    "rh":       5769590088824527822,
    "base":     5769581116637847406,
    "ton":      5769487202882953652,
    "eth":      5769144967003907167,
    "bsc":      5769209219714654791,
    "sol":      5769345211264147269,
    "channel":  5769533489745502351,
    "pinksale": 5769357250057477935,
    # Details button emoji used on the main channel post
    "details_btn": 5769182930219836102,
}

PS_MAX_MEDIA = 10

def _ps_chain_emoji(chain):
    c = (chain or "").upper()
    return {
        "BSC": PS_EMOJI["bsc"], "BNB": PS_EMOJI["bsc"],
        "ETH": PS_EMOJI["eth"], "ETHEREUM": PS_EMOJI["eth"], "EVM": PS_EMOJI["eth"],
        "BASE": PS_EMOJI["base"], "SOL": PS_EMOJI["sol"], "SOLANA": PS_EMOJI["sol"],
        "TON": PS_EMOJI["ton"], "RH": PS_EMOJI["rh"],
    }.get(c, PS_EMOJI["pinksale"])

def _ps_native(chain):
    return {
        "BSC": "BNB", "BNB": "BNB", "ETH": "ETH", "ETHEREUM": "ETH", "EVM": "ETH",
        "BASE": "ETH", "SOL": "SOL", "SOLANA": "SOL", "TON": "TON",
        "POLYGON": "MATIC", "AVAX": "AVAX", "CORE": "CORE",
    }.get((chain or "").upper(), "")

def ps_ref_link(url: str) -> str:
    """Append the owner's PinkSale referral to a sale link (set via /setpsref)."""
    ref = (cfg_get("pinksale_ref", "") or cfg_get("affiliate_wallet", "")
           or "0x5Ca1913ecC0Df6C65334aE4E4b77c86731089577").strip()
    if not url:
        return url
    if not ref:
        return url
    if "ref=" in url.lower():
        return url
    if ref.lower().startswith("http"):
        # owner pasted his full ref link — take only its ref value
        m = re.search(r'[?&]ref=([^&\s]+)', ref, re.IGNORECASE)
        ref = m.group(1) if m else ""
        if not ref:
            return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}ref={ref}"

def _ps_num(v):
    """First number inside a value like '20 BNB' / '31.5' / 20000000000000000000 (wei)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        if f > 1e15:          # wei
            f = f / 1e18
        return f
    m = re.search(r'-?\d+(?:[.,]\d+)?', str(v).replace(",", ""))
    if not m:
        return None
    try:
        f = float(m.group(0))
    except Exception:
        return None
    if f > 1e15:
        f = f / 1e18
    return f

def _ps_decimals(chain):
    """Base-unit decimals of the chain's native coin (SOL/TON = 9, EVM = 18)."""
    return 9 if (chain or "").upper() in ("SOL", "SOLANA", "TON") else 18

def _ps_num_chain(v, chain):
    """Number in human units, scaled by the chain's decimals when the launchpad
    hands us raw base units (e.g. 3318000000000 lamports → 3,318 SOL)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
    else:
        m = re.search(r'-?\d+(?:\.\d+)?', str(v).replace(",", "").replace(" ", ""))
        if not m:
            return None
        try:
            n = float(m.group(0))
        except Exception:
            return None
    if abs(n) >= 1e7:                      # far too big to be a real native-coin cap
        d = _ps_decimals(chain)
        scaled = n / (10 ** d)
        if scaled < 1e-6 and d == 18:      # value was actually 9-decimals
            scaled = n / 1e9
        elif scaled >= 1e7 and d == 9:     # value was actually 18-decimals
            scaled = n / 1e18
        return scaled
    return n

def _ps_amount(v, chain):
    """Format an amount + native currency exactly as the launchpad shows it,
    e.g. '20 BNB' / '3,318 SOL'."""
    n = _ps_num_chain(v, chain)
    if n is None:
        return ""
    cur = _ps_native(chain)
    txt = f"{n:,.4f}".rstrip("0").rstrip(".") if n < 1000 else f"{n:,.2f}".rstrip("0").rstrip(".")
    if txt in ("", "-"):
        txt = "0"
    raw = str(v).upper()
    # "85 SPCXB" / "0.25 USD1" → keep the launchpad's own sale currency
    m_cur = re.match(r'^\s*[\d.,]+\s*([A-Z][A-Z0-9$._-]{0,11})\s*$', raw)
    if m_cur:
        return f"{txt} {m_cur.group(1)}".strip()
    for c in ("BNB", "ETH", "SOL", "TON", "MATIC", "AVAX", "USDT", "USDC"):
        if c in raw:
            cur = c
            break
    return f"{txt} {cur}".strip()

_PS_JUNK_RE = re.compile(
    r'(?:[<>{}\[\]]|null|undefined|vertical-align|max-width|min-width|height\s*:|width\s*:|'
    r'font-|margin|padding|"\s*:|:\s*"|=\s*"|;\s*[a-z-]+\s*:|\bpx\b|@media|0x[a-fA-F0-9]{20,})',
    re.IGNORECASE)

def _ps_clean(v, limit=48):
    """Drop scraped garbage (leftover JSON / CSS fragments) so the template stays clean."""
    s = _lp_fmt_val(v) if not isinstance(v, str) else v
    s = str(s or "").strip().strip('",;:')
    if not s:
        return ""
    if len(s) > limit:
        return ""
    if _PS_JUNK_RE.search(s):
        return ""
    if not re.search(r'[0-9A-Za-z]', s):
        return ""
    return html.escape(s)

def _ps_supply(v, symbol=""):
    """Human-readable token supply, e.g. '1,000,000,000 GUARD'."""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.match(r'^\s*([\d.,]+)\s*([A-Za-z0-9$._-]{0,12})\s*$', s)
    if not m:
        return _ps_clean(s, limit=40)
    num, sym = m.group(1), (m.group(2) or symbol or "").strip()
    try:
        n = float(num.replace(",", ""))
    except Exception:
        return _ps_clean(s, limit=40)
    if n <= 0:
        return ""
    if n >= 1e18:          # raw wei-style value → assume 18 decimals
        n = n / 1e18
    txt = f"{n:,.0f}" if n >= 1 else f"{n:,.4f}".rstrip("0").rstrip(".")
    return html.escape(f"{txt} {sym}".strip())


def _ps_zero(chain):
    return f"0 {_ps_native(chain)}".strip()

def _ps_kind(details, info=None):
    """'Presale' or 'Fairlaunch' for the Starts line."""
    raw = " ".join(str(x) for x in ((info or {}).get("url", ""),
                                    (info or {}).get("sale_type_hint", ""),
                                    details.get("sale_type", ""),
                                    details.get("status", ""))).lower()
    if "fair" in raw:
        return "Fairlaunch"
    return "Presale"

def _ps_socials(details):
    """[(label, url), ...] for Telegram | Twitter | Website (only what we found)."""
    out = []
    for label, key in (("Telegram", "telegram"), ("Twitter", "twitter"), ("Website", "website")):
        u = str(details.get(key) or "").strip()
        if not u:
            continue
        if u.startswith("@"):
            u = f"https://t.me/{u[1:]}"
        if not u.lower().startswith("http"):
            u = "https://" + u
        if _PS_JUNK_RE.search(u) or len(u) > 120 or " " in u:
            continue
        out.append((label, html.escape(u, quote=True)))
    return out

def _ps_time(v):
    """Human date. FIX: plain second-epochs were being read as milliseconds,
    which is why sale dates showed up as 'Jan 1970'."""
    ep = _ps_epoch(v)
    if ep:
        return datetime.utcfromtimestamp(ep).strftime("%d %b %Y %H:%M UTC")
    return _lp_fmt_val(v) or ""

def _ps_sale_type(details, info):
    """PinkSale sale type is either 'Public' or 'Whitelist Only' (WL Only)."""
    raw = " ".join(str(details.get(k, "")) for k in
                   ("sale_type", "whitelist", "status", "name")).lower()
    if any(w in raw for w in ("whitelist", "wl only", "wl-only", "private")):
        return "WL Only"
    if "public" in raw:
        return "Public Sale"
    wl = details.get("whitelist")
    if wl in (True, "true", "True", 1, "1"):
        return "WL Only"
    return "Public Sale"

def _ps_is_live(details):
    """True while the presale / fair-launch is actually running."""
    now = time.time()
    st = _ps_epoch(details.get("start_time"))
    en = _ps_epoch(details.get("end_time"))
    status = str(details.get("status", "")).lower()
    if any(w in status for w in ("ended", "finished", "closed", "filled", "cancel")):
        return False
    if st and now < st:
        return False
    if en and now > en:
        return False
    if st or en:
        return True
    # no dates known → fall back to "raised" presence
    return bool(details.get("raised"))

def _ps_epoch(v):
    n = None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
        if n > 1e12:
            n = n / 1000.0
    else:
        try:
            txt = str(v or "").strip()
            if txt.isdigit():
                n = float(txt)
                if n > 1e12:
                    n = n / 1000.0
            elif txt:
                n = datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
        except Exception:
            n = None
    if n and 1_000_000_000 < n < 4_000_000_000:
        return n
    return None

# extra fields PinkSale exposes that the generic launchpad map does not cover
_LP_KEYMAP.update({
    "raised":     ("raised",     ("totalRaised", "total_raised", "raisedAmount", "amountRaised",
                                  "totalContributed", "raised", "totalBnb", "totalSold")),
    "whitelist":  ("whitelist",  ("whitelist", "isWhitelist", "whitelistEnabled", "whitelistOnly",
                                  "isWhitelistEnabled")),
    "status":     ("status",     ("status", "poolStatus", "state", "saleStatus")),
    "totalToken": ("total_token", ("totalToken", "totalTokens", "tokensForPresale", "hardCapToken")),
})

def load_ps_projects():
    return load_json(PS_PROJECTS_FILE, {})

def save_ps_projects(d):
    save_json(PS_PROJECTS_FILE, d)

def _ps_store(info, details, channel, msg_id):
    """Persist a project so the Details deep-link can render it later."""
    projects = load_ps_projects()
    pid = str(abs(hash(f"{info.get('platform')}|{info.get('address','').lower()}")) % (10 ** 10))
    projects[pid] = {
        "platform": info.get("platform", "PinkSale"),
        "url":      info.get("url", ""),
        "address":  info.get("address", ""),
        "chain":    info.get("chain", ""),
        "channel":  channel,
        "msg_id":   str(msg_id or ""),
        "details":  {k: _lp_fmt_val(v) for k, v in (details or {}).items()},
        "saved_at": datetime.utcnow().isoformat(),
    }
    # keep last 300
    if len(projects) > 300:
        for k in sorted(projects, key=lambda k: projects[k].get("saved_at", ""))[:len(projects) - 300]:
            projects.pop(k, None)
    save_ps_projects(projects)
    return pid

class _SafeFmtDict(dict):
    """str.format_map helper — a missing {placeholder} in an owner-written
    template renders as blank instead of crashing the whole post."""
    def __missing__(self, key):
        return ""

def _render_template(tpl: str, vals: dict):
    """Fill an owner-supplied template with computed values. Returns None
    (caller should fall back to the built-in default) if the template is
    broken (e.g. malformed HTML/braces) rather than ever crash a post."""
    try:
        out = tpl.format_map(_SafeFmtDict(vals))
        # Owner ki apni premium emoji syntax [[emoji:ID]] ko standard
        # <tg-emoji> HTML me badal do (bot API + userbot dono samajhte hain).
        try:
            out = owner_emoji_markers_to_html(out)
        except Exception:
            pass
        return out
    except Exception as e:
        logger.warning(f"custom template render failed, using default: {e}")
        return None

# Channel-post templates use this many 🔮 marker emojis, matched in order
# against the emoji IDs (see build_*_post below) — an owner-edited channel
# template MUST keep exactly this many 🔮 characters for premium emojis to
# line up correctly. Details templates have no such limit — they can use
# literal <tg-emoji emoji-id="...">fallback</tg-emoji> HTML tags anywhere,
# same as the built-in defaults do.
CP_CHANNEL_TEMPLATE_MARKERS = 9
PS_CHANNEL_TEMPLATE_MARKERS = 9


def build_pinksale_post(channel, msg_id, info, details, pid):
    """Owner-approved PinkSale template. Returns (html_text, ordered_emoji_ids).
    🔮 order: chain, chain, info, cap, buy, time, pinksale, details, channel"""
    chain = (info.get("chain") or "").upper()
    name  = (_lp_fmt_val(details.get("name")) or _lp_fmt_val(details.get("symbol"))
             or "New Project")
    soft  = _ps_amount(details.get("soft_cap"), chain)
    hard  = _ps_amount(details.get("hard_cap"), chain)
    mnb   = _ps_amount(details.get("min_buy"), chain)
    mxb   = _ps_amount(details.get("max_buy"), chain)
    start = _ps_time(details.get("start_time"))
    end   = _ps_time(details.get("end_time"))

    ps_link  = ps_ref_link(info.get("url", ""))
    det_link = f"{BOT_LINK}?start=ps_{pid}"
    ch_link  = f"https://t.me/{channel}/{msg_id}" if channel and msg_id else TG_CHANNEL_LINK

    live   = _ps_is_live(details)
    kind   = _ps_kind(details, info)
    raised = _ps_amount(details.get("raised"), chain) if live else ""

    custom_tpl = cfg_get("ps_channel_template", "")
    if custom_tpl:
        rendered = _render_template(custom_tpl, {
            "name": html.escape(name), "caller": html.escape(channel), "chain": chain,
            "sale_type": _ps_sale_type(details, info),
            "soft_cap": soft or "No Soft Cap", "hard_cap": hard or "No Hard Cap",
            "min_buy": mnb or "No Min Buy", "max_buy": mxb or "No Max Buy",
            "raised": raised or "", "starts": (kind + " is live") if live else (start or "N/A"),
            "ends": end or "N/A", "pinksale_link": ps_link, "details_link": det_link,
            "channel_link": ch_link,
        })
        if rendered is not None:
            ce = _ps_chain_emoji(chain)
            emoji_ids = [ce, ce, PS_EMOJI["info"], PS_EMOJI["cap"], PS_EMOJI["buy"],
                         PS_EMOJI["time"], PS_EMOJI["pinksale"], PS_EMOJI["details_btn"],
                         PS_EMOJI["channel"]]
            return rendered, emoji_ids

    t  = "<b>🔮 PINKSALE LAUNCH DETECTED 🔮</b>\n\n"
    t += "🔮 <b>Project Info:</b>\n"
    t += f'PROJECT:   <a href="{ps_link}">{html.escape(name)}</a>\n'
    t += f"CALLER:     @{html.escape(_display_handle(channel))}\n"
    t += f"Sale Typ:    {_ps_sale_type(details, info)}\n\n"
    t += "🔮 <b>CAP:</b>\n\n"
    t += f"Soft Cap: {soft or 'No Soft Cap'}\n"
    t += f"HardCap: {hard or 'No Hard Cap'}\n\n"
    t += "🔮 <b>BUY LIMITS:</b>\n\n"
    t += f"Min Buy:  {mnb or 'No Min Buy'}\n"
    t += f"Max buy: {mxb or 'No Max Buy'}\n"
    # Raised sirf tab jab presale / fairlaunch LIVE ho
    if live and raised:
        t += f"Raised:    {raised}\n"
    t += "\n"
    t += "🔮 <b>Time:</b>\n\n"
    t += f"Starts: {(kind + ' is live') if live else (start or 'N/A')}\n"
    t += f"Ends: {end or 'N/A'}\n\n"
    t += (f"<blockquote>@{html.escape(_display_handle(channel))} has posted a PinkSale project. "
          f"Click Details to view the project information.</blockquote>\n\n")
    t += (f'🔮<a href="{ps_link}">Pinksale</a>  '
          f'🔮<a href="{det_link}">Details</a>  '
          f'🔮<a href="{ch_link}">Channel</a>')

    ce = _ps_chain_emoji(chain)
    emoji_ids = [ce, ce, PS_EMOJI["info"], PS_EMOJI["cap"], PS_EMOJI["buy"],
                 PS_EMOJI["time"], PS_EMOJI["pinksale"], PS_EMOJI["details_btn"],
                 PS_EMOJI["channel"]]
    return t, emoji_ids

def _ps_pe(eid, fallback):
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def build_ps_details_text(pid, rec, premium=True):
    """Full breakdown shown when someone taps Details (premium emoji version)."""
    d       = rec.get("details", {}) or {}
    chain   = (rec.get("chain") or "").upper()
    channel = rec.get("channel", "")
    msg_id  = rec.get("msg_id", "")
    name    = d.get("name") or d.get("symbol") or "New Project"
    info    = {"chain": chain, "url": rec.get("url", "")}
    live    = _ps_is_live(d)

    def em(key, fb):
        return _ps_pe(PS_EMOJI[key], fb) if premium else fb

    def row(key, fb, label, val):
        return f"{em(key, fb)} <b>{label}:</b> {val}\n" if val else ""

    _cem = _ps_pe(_ps_chain_emoji(chain), "🔮") if premium else "🔮"

    custom_tpl = cfg_get("ps_details_template", "")
    if custom_tpl:
        kind_now = _ps_kind(d, info)
        rendered = _render_template(custom_tpl, {
            "name": html.escape(str(name)), "caller": html.escape(channel), "chain": chain,
            "sale_type": _ps_sale_type(d, info), "token": d.get("symbol") or "",
            "supply": _ps_supply(d.get("supply"), d.get("symbol") or ""),
            "currency": _ps_clean(d.get("currency"), limit=16) or "",
            "soft_cap": _ps_amount(d.get("soft_cap"), chain) or "No Soft Cap",
            "hard_cap": _ps_amount(d.get("hard_cap"), chain) or "No Hard Cap",
            "raised": (_ps_amount(d.get("raised"), chain) or _ps_zero(chain)) if live else "",
            "min_buy": _ps_amount(d.get("min_buy"), chain) or "No Min Buy",
            "max_buy": _ps_amount(d.get("max_buy"), chain) or "No Max Buy",
            "starts": (f"{kind_now} is live") if live else (_ps_time(d.get("start_time")) or "N/A"),
            "ends": _ps_time(d.get("end_time")) or "N/A",
            "presale_rate": _ps_clean(d.get("rate")) or "",
            "listing_rate": _ps_clean(d.get("listing_rate")) or "",
            "tokens_for_sale": _ps_supply(d.get("total_token"), d.get("symbol") or "") or "",
            "liquidity": _ps_clean(d.get("liquidity")) or "",
            "lp_lock": _ps_clean(d.get("lock_time")) or "",
            "token_ca": html.escape(d.get("token") or ""),
            "sale_address": html.escape(rec.get("address", "") or ""),
            "buy_link": ps_ref_link(rec.get("url", "")),
            "caller_post_link": f"https://t.me/{channel}/{msg_id}" if channel and msg_id else "",
            "chain_emoji": _cem,
        })
        if rendered is not None:
            return rendered

    t  = f"{_cem} <b>PINKSALE PROJECT DETAILS</b> {_cem}\n\n"
    t += (f"{em('info','📢')} <b>@{html.escape(_display_handle(channel))}</b> has posted "
          f"<b>{html.escape(str(name))}</b> on PinkSale.\n\n")
    t += f"{em('info','📢')} <b>PROJECT INFO</b>\n\n"
    t += row("ca", "🔶", "Sale Type", _ps_sale_type(d, info))
    t += row("ca", "🔶", "Chain", chain)
    t += row("ca", "🔶", "Token", d.get("symbol"))
    t += row("ca", "🔶", "Supply", _ps_supply(d.get("supply"), d.get("symbol") or ""))
    t += row("ca", "🔶", "Sale Currency", _ps_clean(d.get("currency"), limit=16))
    t += "\n"
    t += f"{em('cap','🎯')} <b>CAP</b>\n\n"
    t += row("ca", "🔶", "Soft Cap", _ps_amount(d.get("soft_cap"), chain) or "No Soft Cap")
    t += row("ca", "🔶", "Hard Cap", _ps_amount(d.get("hard_cap"), chain) or "No Hard Cap")
    # Raised sirf LIVE presale / fairlaunch par
    if live:
        t += row("cap", "🎯", "Raised", _ps_amount(d.get("raised"), chain) or _ps_zero(chain))
    t += "\n"
    t += f"{em('buy','🚀')} <b>BUY LIMITS</b>\n\n"
    t += row("ca", "🔶", "Min Buy", _ps_amount(d.get("min_buy"), chain) or "No Min Buy")
    t += row("ca", "🔶", "Max Buy", _ps_amount(d.get("max_buy"), chain) or "No Max Buy")
    t += "\n"
    t += f"{em('time','⌚')} <b>TIMELINE</b>\n\n"
    _kind = _ps_kind(d, info)
    t += row("ca", "🔶", "Starts",
             (f"{_kind} is live") if live else (_ps_time(d.get("start_time")) or "N/A"))
    t += row("time", "⌚", "Ends", _ps_time(d.get("end_time")) or "N/A")
    t += "\n"
    extra  = row("ca", "🔶", "Presale Rate", _ps_clean(d.get("rate")))
    extra += row("ca", "🔶", "Listing Rate", _ps_clean(d.get("listing_rate")))
    extra += row("ca", "🔶", "Tokens For Sale", _ps_supply(d.get("total_token"), d.get("symbol") or ""))
    extra += row("ca", "🔶", "Liquidity", _ps_clean(d.get("liquidity")))
    extra += row("ca", "🔶", "LP Lock", _ps_clean(d.get("lock_time")))
    if extra:
        t += extra + "\n"
    tok = d.get("token") or ""
    if tok and tok.lower() != (rec.get("address", "") or "").lower():
        t += f"{em('ca','🔶')} <b>Token CA:</b>\n<code>{html.escape(tok)}</code>\n\n"
    t += f"{em('ca','🔶')} <b>Sale Address:</b>\n<code>{html.escape(rec.get('address',''))}</code>\n\n"
    t += f'{em("pinksale","🩷")} <a href="{ps_ref_link(rec.get("url",""))}">Buy on PinkSale</a>'
    if channel and msg_id:
        t += f'  |  {em("channel","📣")} <a href="https://t.me/{channel}/{msg_id}">Caller Post</a>'
    return t


def _ps_next_media():
    """Rotate through the owner's PinkSale media (max 10)."""
    med = cfg_get("ps_media", []) or []
    if not med:
        return None
    idx = int(cfg_get("ps_media_index", 0) or 0)
    item = med[idx % len(med)]
    cfg_set("ps_media_index", (idx + 1) % len(med))
    return item

async def _ps_forward_to_caller(bot, channel, sent_msg_id):
    """Send a copy of the channel post into the caller's DM (if the KOL is linked)."""
    try:
        owners = load_kol_owners()
        uid = owners.get((channel or "").lower())
        if not uid or not sent_msg_id:
            return False
        await bot.forward_message(chat_id=int(uid), from_chat_id=TARGET_CHANNEL,
                                  message_id=int(sent_msg_id))
        logger.info(f"📬 PinkSale post forwarded to caller DM @{channel}")
        return True
    except Exception as e:
        logger.warning(f"PinkSale DM forward @{channel} failed: {e}")
        return False

def _ps_watch_add(pid, info, details, channel):
    """Queue the project so the bot starts X-tracking once the token goes live."""
    try:
        watch = cfg_get("ps_watch", []) or []
        token = _lp_fmt_val(details.get("token")) or ""
        end_ts = _ps_epoch(details.get("end_time")) or (time.time() + 3 * 86400)
        if any(w.get("pid") == pid for w in watch):
            return
        watch.append({
            "pid": pid, "channel": channel, "chain": info.get("chain", ""),
            "address": info.get("address", ""), "token": token,
            "url": info.get("url", ""), "end_ts": end_ts,
            "added": datetime.utcnow().isoformat(),
        })
        cfg_set("ps_watch", watch[-100:])
    except Exception as e:
        logger.warning(f"ps_watch add failed: {e}")

_PINKSALE_SEEN = set()

async def send_pinksale_alert(bot, channel, msg_id, info, post_text="", force=False,
                              preview_to=None):
    """Build + post the PinkSale alert with premium emojis, media and DM forward.
    force=True → bypasses the duplicate guard (used by /pscall for skipped calls)."""
    key = f"PS|{(info.get('address') or '').lower()}"
    try:
        if not force and not preview_to and key in _PINKSALE_SEEN:
            logger.info(f"PinkSale duplicate skipped: {key}")
            return False
        _PINKSALE_SEEN.add(key)


        details = await asyncio.to_thread(fetch_launchpad_details_sync, info, post_text)
        pid     = _ps_store(info, details, channel, msg_id)
        text, emoji_ids = build_pinksale_post(channel, msg_id, info, details, pid)

        if preview_to:
            try:
                await bot.send_message(preview_to, text, parse_mode="HTML",
                                       disable_web_page_preview=True)
            except Exception as e:
                await bot.send_message(preview_to, f"Preview failed: {e}")
            return True

        media = _ps_next_media()
        sent  = None

        # 1) userbot + media (premium emojis, no edit pencil)
        if media and media.get("file_id") and userbot_client:
            try:
                sent = await _userbot_send_media_with_emoji(
                    bot, TARGET_CHANNEL, media["file_id"],
                    media.get("type", "video"), text, emoji_ids)
            except Exception as e:
                logger.warning(f"PinkSale userbot media send failed: {e}")
        # 2) userbot text-only
        if not sent and userbot_client:
            sent = await _userbot_send_with_premium_emoji(
                TARGET_CHANNEL, text, emoji_ids, link_preview=False)
        sent_id = getattr(sent, "id", None)

        # 3) bot API fallback (no premium emojis)
        if not sent:
            plain = text.replace("🔮", "🩷")
            if media and media.get("file_id"):
                try:
                    if media.get("type") == "photo":
                        m = await bot.send_photo(TARGET_CHANNEL, photo=media["file_id"],
                                                 caption=plain[:1024], parse_mode="HTML")
                    else:
                        m = await bot.send_video(TARGET_CHANNEL, video=media["file_id"],
                                                 caption=plain[:1024], parse_mode="HTML")
                    sent_id = m.message_id
                except Exception as e:
                    logger.warning(f"PinkSale media fallback failed: {e}")
            if not sent_id:
                m = await bot.send_message(TARGET_CHANNEL, plain, parse_mode="HTML",
                                           disable_web_page_preview=True)
                sent_id = m.message_id

        logger.info(f"🩷 PinkSale alert posted @{channel} pid={pid} msg={sent_id}")
        await _ps_forward_to_caller(bot, channel, sent_id)
        _ps_watch_add(pid, info, details, channel)
        return True
    except Exception as e:
        # Un-mark it: a failed post must not block the next attempt for the
        # same project (this is why some PinkSale calls never appeared).
        _PINKSALE_SEEN.discard(key)
        logger.error(f"send_pinksale_alert failed for {key}: {e}", exc_info=True)
        return False


async def ps_watch_job(context: ContextTypes.DEFAULT_TYPE):
    """Once a presale/fair-launch ends and the token trades, register it as a normal
    call so the existing 2X / 5X / 10X milestone engine takes over."""
    try:
        watch = cfg_get("ps_watch", []) or []
        if not watch:
            return
        now = time.time()
        keep = []
        for w in watch:
            try:
                # not finished yet → keep waiting
                if now < float(w.get("end_ts") or 0):
                    keep.append(w); continue
                # give up after 21 days
                if now - float(w.get("end_ts") or now) > 21 * 86400:
                    continue
                ca = (w.get("token") or "").strip() or (w.get("address") or "").strip()
                if not ca:
                    keep.append(w); continue
                call_key = f"{w.get('channel','').lower()}_{ca}"
                if call_key in tracked_calls:
                    continue
                dex = await asyncio.to_thread(_fetch_dex_sync, ca, 1, 0)
                if not dex or not (dex.get("mcap") or dex.get("price")):
                    keep.append(w); continue
                entry_mc = dex.get("mcap", 0) or 0
                tracked_calls[call_key] = {
                    "channel":      (w.get("channel") or "").lower(),
                    "msg_id":       "0",
                    "ca":           ca,
                    "chain":        dex.get("chain") or w.get("chain") or "EVM",
                    "entry_mc":     entry_mc,
                    "entry_price":  dex.get("price", 0),
                    "entry_fmt":    fmt_mc(entry_mc) if entry_mc else "N/A",
                    "entry_locked": True,
                    "post_mc_hint": entry_mc,
                    "symbol":       dex.get("symbol", ""),
                    "tracked_since": datetime.utcnow().isoformat(),
                    "dex_pending":  False,
                    "source":       "pinksale",
                }
                _save_tracked()
                logger.info(f"🩷 PinkSale project now LIVE — tracking started "
                            f"@{w.get('channel')} {ca[:12]}… entry={fmt_mc(entry_mc)}")
                try:
                    await notify_owners(context.bot,
                        f"🩷 <b>PinkSale project live</b>\n\nCaller: @{html.escape(w.get('channel',''))}\n"
                        f"Entry MC: {fmt_mc(entry_mc)}\nX-tracking started.")
                except Exception:
                    pass
            except Exception as e_w:
                logger.warning(f"ps_watch item failed: {e_w}")
                keep.append(w)
        cfg_set("ps_watch", keep)
    except Exception as e:
        logger.error(f"ps_watch_job crash: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# CHEESEPAD PREMIUM ALERTS
#   • owner-approved template with premium emojis
#   • affiliate link injection (owner wallet) on every pool link
#   • "Details" deep-link → full project breakdown inside the bot
# ═══════════════════════════════════════════════════════════════════════════

AFFILIATE_WALLET = "0x5Ca1913ecC0Df6C65334aE4E4b77c86731089577"

CP_MAX_MEDIA = 10   # owner can store up to 10 CheesePad videos (rotating)
CPD_MAX_MEDIA = 10  # media for the CheesePad DETAILS message (inside the bot)
PSD_MAX_MEDIA = 10  # media for the PinkSale  DETAILS message (inside the bot)


def _details_next_media(key, index_key):
    """Rotate through owner-set media for the in-bot details messages."""
    med = cfg_get(key, []) or []
    if not med:
        return None
    idx = int(cfg_get(index_key, 0) or 0) % len(med)
    cfg_set(index_key, (idx + 1) % len(med))
    return med[idx]


async def send_details_media(bot, chat_id, kind):
    """Send the owner-set media above a details message. kind = 'cp' | 'ps'."""
    try:
        item = _details_next_media(f"{kind}d_media", f"{kind}d_media_index")
        if not item or not item.get("file_id"):
            return False
        fid, ftype = item["file_id"], item.get("type", "video")
        if ftype == "photo":
            await bot.send_photo(chat_id, fid)
        elif ftype == "gif":
            await bot.send_animation(chat_id, fid)
        else:
            await bot.send_video(chat_id, fid, supports_streaming=True)
        return True
    except Exception as e:
        logger.warning(f"details media send failed ({kind}): {e}")
        return False


DETAILS_CAPTION_LIMIT = 1024


def _split_html_for_caption(text, limit=DETAILS_CAPTION_LIMIT):
    """Split text into (caption_part, rest) on a line boundary so HTML stays valid."""
    if len(text) <= limit:
        return text, ""
    head = text[:limit]
    cut = head.rfind("\n")
    if cut < int(limit * 0.4):
        cut = head.rfind(" ")
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip(), text[cut:].lstrip("\n")


async def send_details_message(bot, chat_id, kind, builder, pid, rec):
    """Send the in-bot details text INSIDE the owner-set media caption.
    If the text is longer than the caption limit, the media carries the first
    part and the remainder is sent as a reply to that same media message so
    they stay visually connected."""
    def _txt(premium=True):
        return builder(pid, rec, premium=premium)

    item = _details_next_media(f"{kind}d_media", f"{kind}d_media_index")
    fid = (item or {}).get("file_id")
    ftype = (item or {}).get("type", "video")

    if fid:
        for premium in (True, False):
            try:
                full = _txt(premium)
            except Exception:
                continue
            cap, rest = _split_html_for_caption(full)
            try:
                if ftype == "photo":
                    msg = await bot.send_photo(chat_id, fid, caption=cap, parse_mode="HTML")
                elif ftype == "gif":
                    msg = await bot.send_animation(chat_id, fid, caption=cap, parse_mode="HTML")
                else:
                    msg = await bot.send_video(chat_id, fid, caption=cap, parse_mode="HTML",
                                               supports_streaming=True)
                if rest:
                    try:
                        await bot.send_message(chat_id, rest, parse_mode="HTML",
                                               disable_web_page_preview=True,
                                               reply_to_message_id=getattr(msg, "message_id", None))
                    except Exception:
                        await bot.send_message(chat_id, rest, parse_mode="HTML",
                                               disable_web_page_preview=True)
                return True
            except Exception as e:
                logger.warning(f"details caption send failed ({kind}, premium={premium}): {e}")
        # all caption attempts failed → media first, text after
        try:
            if ftype == "photo":
                await bot.send_photo(chat_id, fid)
            elif ftype == "gif":
                await bot.send_animation(chat_id, fid)
            else:
                await bot.send_video(chat_id, fid, supports_streaming=True)
        except Exception as e:
            logger.warning(f"details media send failed ({kind}): {e}")

    for premium in (True, False):
        try:
            await bot.send_message(chat_id, _txt(premium), parse_mode="HTML",
                                   disable_web_page_preview=True)
            return True
        except Exception as e:
            logger.warning(f"details text send failed ({kind}, premium={premium}): {e}")
    return False


def _cp_next_media():
    """Return the next CheesePad video/photo in rotation (None if none set)."""
    med = cfg_get("cp_media", []) or []
    if not med:
        return None
    idx = int(cfg_get("cp_media_index", 0) or 0) % len(med)
    cfg_set("cp_media_index", (idx + 1) % len(med))
    return med[idx]


CP_EMOJI = {
    "rocket":  5773941882832822049,
    "search":  5774150171566808453,
    "zap":     5773754635143618815,
    "fire":    5773911440104629143,   # TIMELINE
    "warn":    5773859767353088857,
    "chart":   5771643010177574828,
    "eye":     5774033919687007833,   # PROJECT INFO
    "target":  5774109463866777097,   # CAP
    "dot":     5771849310341701611,
    "ca":      5773971281883965193,
    "crystal": 5771363334792159192,   # BUY LIMITS
    "scan":    5771410794180781175,
    "eth":     5773746856957845866,
    "rh":      5771761937821999182,
    "sol":     5773941762573738612,
    "ton":     5774014970291297527,
    "base":    5771449332922326995,
    "bnb":     5771476142108188715,
    # footer buttons
    "details":   5771657862174483166,
    "cheesepad": 5773647114932329762,
    "kol":       5771633196177301953,
}

# Premium emoji that must always prefix the /setpromolink text
PROMO_EMOJI_ID = 5773906204539493747


def _cp_chain_emoji(chain):
    c = (chain or "").upper()
    return {
        "BSC": CP_EMOJI["bnb"], "BNB": CP_EMOJI["bnb"],
        "ETH": CP_EMOJI["eth"], "ETHEREUM": CP_EMOJI["eth"], "EVM": CP_EMOJI["eth"],
        "BASE": CP_EMOJI["base"], "SOL": CP_EMOJI["sol"], "SOLANA": CP_EMOJI["sol"],
        "TON": CP_EMOJI["ton"], "RH": CP_EMOJI["rh"],
    }.get(c, CP_EMOJI["cheesepad"])


def affiliate_link(url: str) -> str:
    """Attach the owner's affiliate/referral wallet to ANY launchpad link."""
    if not url:
        return url
    ref = (cfg_get("affiliate_wallet", "") or AFFILIATE_WALLET).strip()
    if not ref or re.search(r'[?&](ref|refer|referral)=', url, re.IGNORECASE):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}ref={ref}"


def cp_pool_link(info: dict) -> str:
    """CheesePad pool link (with affiliate ref)."""
    url = info.get("url", "") or ""
    if not url and info.get("address"):
        url = (f"https://www.cheesepad.ai/sale/"
               f"{(info.get('chain') or 'bsc').lower()}/{info['address']}")

    return affiliate_link(url)


def _cp_sale_type(details, info):
    """English sale type: 'Whitelist' or 'Public'."""
    raw = " ".join(str(details.get(k, "")) for k in
                   ("sale_type", "whitelist", "status", "name")).lower()
    if any(w in raw for w in ("whitelist", "wl only", "wl-only", "private")):
        return "Whitelist"
    wl = details.get("whitelist")
    if wl in (True, "true", "True", 1, "1"):
        return "Whitelist"
    return "Public"


def _cp_is_fairlaunch(details, info) -> bool:
    raw = " ".join(str(x) for x in (info.get("url", ""), info.get("sale_type_hint", ""),
                                    details.get("sale_type", ""), details.get("status", ""))).lower()
    return ("fair" in raw) or (not _ps_num(details.get("hard_cap")))


def build_cheesepad_post(channel, msg_id, info, details, pid):
    """Owner-approved CheesePad template. Returns (html_text, ordered_emoji_ids).
    🔮 order: chain, chain, info, cap, buy, time, details, cheesepad, kol"""
    chain = (info.get("chain") or "").upper()
    name  = (_lp_fmt_val(details.get("name")) or _lp_fmt_val(details.get("symbol"))
             or "New Project")
    soft  = _ps_amount(details.get("soft_cap"), chain)
    hard  = _ps_amount(details.get("hard_cap"), chain)
    mnb   = _ps_amount(details.get("min_buy"), chain)
    mxb   = _ps_amount(details.get("max_buy"), chain)
    raised = _ps_amount(details.get("raised"), chain)
    live   = _ps_is_live(details)
    start  = _ps_time(details.get("start_time"))
    end    = _ps_time(details.get("end_time"))

    pool_link = cp_pool_link(info)
    det_link  = f"{BOT_LINK}?start=cp_{pid}"
    ch_link   = f"https://t.me/{channel}/{msg_id}" if channel and msg_id else TG_CHANNEL_LINK
    kind      = "Fairlaunch" if _cp_is_fairlaunch(details, info) else "Presale"

    custom_tpl = cfg_get("cp_channel_template", "")
    if custom_tpl:
        rendered = _render_template(custom_tpl, {
            "name": html.escape(name), "caller": html.escape(channel), "chain": chain,
            "sale_type": _cp_sale_type(details, info),
            "soft_cap": soft or "N/A", "hard_cap": hard or "No Hard Cap",
            "min_buy": mnb or "No Min Buy", "max_buy": mxb or "No Max Buy",
            "raised": raised if (live and raised) else "",
            "starts": (kind + " is already live") if live else (start or "N/A"),
            "ends": end or "N/A", "cheesepad_link": pool_link, "details_link": det_link,
            "kol_link": ch_link,
        })
        if rendered is not None:
            ce = _cp_chain_emoji(chain)
            emoji_ids = [ce, ce, CP_EMOJI["eye"], CP_EMOJI["target"], CP_EMOJI["crystal"],
                         CP_EMOJI["fire"], CP_EMOJI["details"], CP_EMOJI["cheesepad"],
                         CP_EMOJI["kol"]]
            return rendered, emoji_ids

    t  = "<b>🔮 CHEESEPAD TOKEN DETECTED 🔮</b>\n\n"
    t += "🔮 <b>PROJECT INFO:</b>\n"
    t += f'Project:  <a href="{pool_link}">{html.escape(name)}</a>\n'
    t += f"Caller:    @{html.escape(_display_handle(channel))}\n"
    t += f"Sale typ: {_cp_sale_type(details, info)}\n\n"
    t += "🔮 <b>CAP:</b>\n\n"
    t += f"Soft Cap: {soft or 'N/A'}\n"
    t += f"Hard Cap: {hard or 'No Hard Cap'}\n"
    # Raised sirf tab jab presale / fairlaunch LIVE ho
    if live and raised:
        t += f"Raised: {raised}\n"
    t += "\n"
    t += "🔮 <b>BUY LIMITS:</b>\n\n"
    t += f"Min Buy:  {mnb or 'No Min Buy'}\n"
    t += f"Max Buy: {mxb or 'No Max Buy'}\n\n"
    t += "🔮 <b>TIMELINE:</b>\n\n"
    t += f"Starts: {(kind + ' is already live') if live else (start or 'N/A')}\n"
    t += f"Ends:  {end or 'N/A'}\n\n"
    t += (f"<blockquote>@{html.escape(_display_handle(channel))} has posted a CheesePad project. "
          f"Click Details to view the project information.</blockquote>\n\n")
    t += (f'🔮<a href="{det_link}">Details</a>  '
          f'🔮<a href="{pool_link}">Cheesepad</a>  '
          f'🔮<a href="{ch_link}">KOL</a>')

    ce = _cp_chain_emoji(chain)
    emoji_ids = [ce, ce, CP_EMOJI["eye"], CP_EMOJI["target"], CP_EMOJI["crystal"],
                 CP_EMOJI["fire"], CP_EMOJI["details"], CP_EMOJI["cheesepad"],
                 CP_EMOJI["kol"]]
    return t, emoji_ids


def _cp_pe(eid, fallback):
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def build_cp_details_text(pid, rec, premium=True):
    """Full CheesePad breakdown shown when someone taps Details (inside the bot)."""
    d       = rec.get("details", {}) or {}
    chain   = (rec.get("chain") or "").upper()
    channel = rec.get("channel", "")
    msg_id  = rec.get("msg_id", "")
    name    = d.get("name") or d.get("symbol") or "New Project"
    info    = {"chain": chain, "url": rec.get("url", ""), "address": rec.get("address", ""),
               "sale_type_hint": ""}
    live    = _ps_is_live(d)
    kind    = "Fairlaunch" if _cp_is_fairlaunch(d, info) else "Presale"
    pool    = cp_pool_link(info)

    # Sirf CheesePad ki apni emojis — koi PinkSale emoji yahan nahi aati.
    _ALIAS = {"dot": "details", "chart": "cheesepad", "zap": "details",
              "scan": "details", "ca": "details", "warn": "cheesepad"}

    def em(key, fb):
        key = _ALIAS.get(key, key)
        return _cp_pe(CP_EMOJI[key], fb) if premium else fb

    def row(key, fb, label, val):
        return f"{em(key, fb)} <b>{label}:</b> {val}\n" if val else ""

    _cem = _cp_pe(_cp_chain_emoji(chain), "🔮") if premium else "🔮"

    custom_tpl = cfg_get("cp_details_template", "")
    if custom_tpl:
        _tok_early = d.get("token") or ""
        rendered = _render_template(custom_tpl, {
            "name": html.escape(str(name)), "caller": html.escape(channel), "chain": chain,
            "sale_type": _cp_sale_type(d, info), "token": d.get("symbol") or "",
            "supply": _ps_supply(d.get("supply"), d.get("symbol") or ""),
            "currency": _ps_clean(d.get("currency"), limit=16) or "",
            "soft_cap": _ps_amount(d.get("soft_cap"), chain) or "N/A",
            "hard_cap": _ps_amount(d.get("hard_cap"), chain) or "No Hard Cap",
            "raised": (_ps_amount(d.get("raised"), chain) or _ps_zero(chain)) if live else "",
            "min_buy": _ps_amount(d.get("min_buy"), chain) or "No Min Buy",
            "max_buy": _ps_amount(d.get("max_buy"), chain) or "No Max Buy",
            "starts": (kind + " is live") if live else (_ps_time(d.get("start_time")) or "N/A"),
            "ends": _ps_time(d.get("end_time")) or "N/A",
            "presale_rate": _ps_clean(d.get("rate")) or "",
            "listing_rate": _ps_clean(d.get("listing_rate")) or "",
            "tokens_for_sale": _ps_supply(d.get("total_token"), d.get("symbol") or "") or "",
            "liquidity": _ps_clean(d.get("liquidity")) or "",
            "lp_lock": _ps_clean(d.get("lock_time")) or "",
            "token_ca": html.escape(_tok_early) if (_tok_early and _tok_early.lower() != (rec.get("address","") or "").lower()) else "",
            "sale_address": html.escape(rec.get("address", "") or ""),
            "buy_link": pool, "caller_post_link": f"https://t.me/{channel}/{msg_id}" if channel and msg_id else "",
            "chain_emoji": _cem,
        })
        if rendered is not None:
            return rendered

    t  = f"{_cem} <b>CHEESEPAD PROJECT DETAILS</b> {_cem}\n\n"
    t += (f"{em('cheesepad','🧀')} <b>@{html.escape(_display_handle(channel))}</b> has posted "
          f"<b>{html.escape(str(name))}</b> on Cheesepad.\n\n")
    t += f'{em("eye","👁")} <b>Project:</b> <a href="{pool}">{html.escape(str(name))}</a>\n'
    t += f"{em('dot','🔸')} <b>Caller:</b> @{html.escape(_display_handle(channel))}\n"
    t += f"{em('dot','🔸')} <b>Chain:</b> {chain}\n"
    t += f"{em('dot','🔸')} <b>Sale Type:</b> {_cp_sale_type(d, info)}\n"
    if d.get("symbol"):
        t += f"{em('dot','🔸')} <b>Token:</b> {html.escape(str(d.get('symbol')))}\n"
    _sup = _ps_supply(d.get("supply"), d.get("symbol") or "")
    if _sup:
        t += f"{em('dot','🔸')} <b>Supply:</b> {_sup}\n"
    _cur = _ps_clean(d.get("currency"), limit=16)
    if _cur:
        t += f"{em('dot','🔸')} <b>Sale Currency:</b> {_cur}\n"
    t += "\n"
    t += f"{em('target','🎯')} <b>CAP</b>\n\n"
    t += f"{em('dot','🔸')} <b>Soft Cap:</b> {_ps_amount(d.get('soft_cap'), chain) or 'N/A'}\n"
    t += f"{em('dot','🔸')} <b>Hard Cap:</b> {_ps_amount(d.get('hard_cap'), chain) or 'No Hard Cap'}\n"
    # Raised sirf LIVE presale / fairlaunch par
    if live:
        t += (f"{em('chart','📈')} <b>Raised:</b> "
              f"{_ps_amount(d.get('raised'), chain) or _ps_zero(chain)}\n")
    t += "\n"
    t += f"{em('crystal','🔮')} <b>BUY LIMITS</b>\n\n"
    t += f"{em('dot','🔸')} <b>Min Buy:</b> {_ps_amount(d.get('min_buy'), chain) or 'No Min Buy'}\n"
    t += f"{em('dot','🔸')} <b>Max Buy:</b> {_ps_amount(d.get('max_buy'), chain) or 'No Max Buy'}\n\n"
    t += f"{em('fire','🔥')} <b>TIMELINE</b>\n\n"
    t += (f"{em('dot','🔸')} <b>Starts:</b> "
          f"{(kind + ' is live') if live else (_ps_time(d.get('start_time')) or 'N/A')}\n")
    t += f"{em('dot','🔸')} <b>Ends:</b> {_ps_time(d.get('end_time')) or 'N/A'}\n\n"
    extra = ""
    extra += row("zap", "⚡", "Presale Rate", _ps_clean(d.get("rate")))
    extra += row("zap", "⚡", "Listing Rate", _ps_clean(d.get("listing_rate")))
    extra += row("zap", "⚡", "Tokens For Sale", _ps_supply(d.get("total_token"), d.get("symbol") or ""))
    extra += row("zap", "⚡", "Liquidity", _ps_clean(d.get("liquidity")))
    extra += row("zap", "⚡", "LP Lock", _ps_clean(d.get("lock_time")))
    if extra:
        t += extra + "\n"
    tok = d.get("token") or ""
    if tok and tok.lower() != (rec.get("address", "") or "").lower():
        t += f"{em('ca','🧾')} <b>Token CA:</b>\n<code>{html.escape(tok)}</code>\n\n"
    t += f"{em('scan','🔎')} <b>Sale Address:</b>\n<code>{html.escape(rec.get('address',''))}</code>\n\n"
    t += f'{em("cheesepad","🧀")} <a href="{pool}">Buy on CheesePad</a>'
    if channel and msg_id:
        t += f'  |  {em("kol","📣")} <a href="https://t.me/{channel}/{msg_id}">Caller Post</a>'
    return t


_CHEESEPAD_SEEN = set()


async def send_cheesepad_alert(bot, channel, msg_id, info, post_text="", force=False,
                               preview_to=None):
    """Build + post the CheesePad alert with premium emojis and media."""
    key = f"CP|{(info.get('address') or info.get('url') or '').lower()}"
    try:
        if not force and not preview_to and key in _CHEESEPAD_SEEN:
            logger.info(f"CheesePad duplicate skipped: {key}")
            return False
        _CHEESEPAD_SEEN.add(key)

        details = await asyncio.to_thread(fetch_launchpad_details_sync, info, post_text)
        pid     = _ps_store(info, details, channel, msg_id)
        text, emoji_ids = build_cheesepad_post(channel, msg_id, info, details, pid)

        if preview_to:
            try:
                await bot.send_message(preview_to, text, parse_mode="HTML",
                                       disable_web_page_preview=True)
            except Exception as e:
                await bot.send_message(preview_to, f"Preview failed: {e}")
            return True

        # CheesePad posts use ONLY CheesePad media. Never fall back to the
        # PinkSale rotation — that made CheesePad tokens show PinkSale videos.
        media = _cp_next_media()
        sent  = None
        # forced_pack → 🔮KOL must use the CheesePad KOL emoji, never the
        # rotating alert pack's KOL emoji.
        cp_pack = {"name": "cheesepad", "kol": CP_EMOJI["kol"],
                   "bot": CP_EMOJI["details"], "mae": 0}
        if media and media.get("file_id") and userbot_client:
            try:
                sent = await _userbot_send_media_with_emoji(
                    bot, TARGET_CHANNEL, media["file_id"],
                    media.get("type", "video"), text, emoji_ids,
                    forced_pack=cp_pack)
            except Exception as e:
                logger.warning(f"CheesePad userbot media send failed: {e}")
        if not sent and userbot_client:
            sent = await _userbot_send_with_premium_emoji(
                TARGET_CHANNEL, text, emoji_ids, link_preview=False,
                forced_pack=cp_pack)
        sent_id = getattr(sent, "id", None)

        if not sent:
            plain = text.replace("🔮", "🧀")
            m = await bot.send_message(TARGET_CHANNEL, plain, parse_mode="HTML",
                                       disable_web_page_preview=True)
            sent_id = m.message_id

        logger.info(f"🧀 CheesePad alert posted @{channel} pid={pid} msg={sent_id}")
        await _ps_forward_to_caller(bot, channel, sent_id)
        _ps_watch_add(pid, info, details, channel)
        return True
    except Exception as e:
        _CHEESEPAD_SEEN.discard(key)
        logger.error(f"send_cheesepad_alert failed for {key}: {e}", exc_info=True)
        return False



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

# X-alert (buttons wali post) ka naya paragraph template — sirf ek line.
# Champion / Leaderboard KOL badge isi paragraph ke aakhir me inline lagta hai.
DEFAULT_X_TEMPLATE = (
    "${symbol} {chain} play called at {entry} MC. Current MC stands at {current}."
)

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
        has_mae    = "mae_link" in x_tmpl
        if has_x_line or not has_mae:
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
        "@{channel} delivered {x}X. ${symbol} was called at a {entry} MC. Current MC stands at {current}. "
        "Massive move delivered. Eyes on the next milestone.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (500, 999,
        "<b>🔮 Apex KOL @{channel}</b>\n\n"
        "@{channel} printed {x}X. ${symbol} continues to exceed expectations, climbing from {entry} MC to {current}. "
        "Another milestone secured. Based KOL of Wizard Scan.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (1000, 1999,
        "<b>🔮 ELITE KOL @{channel}</b>\n\n"
        "@{channel} nailed {x}X. ${symbol} was called at a {entry} MC and has now climbed to {current}. "
        "A truly elite performance with exceptional returns.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (2000, 2999,
        "<b>🔮 RARE KOL @{channel}</b>\n\n"
        "@{channel} crushed {x}X. ${symbol} was called at a {entry} MC and has now reached {current}. "
        "An extraordinary run that stands among the rarest performances.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (3000, 4999,
        "<b>🔮 EPIC KOL @{channel}</b>\n\n"
        "@{channel} smashed {x}X. From {entry} MC to {current}, ${symbol} delivered a historic performance. "
        "One of the biggest moves ever tracked.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{mae_link}">MAE</a>\n'
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (5000, 9999,
        "<b>🔮 LEGENDARY KOL @{channel}</b>\n\n"
        "@{channel} hit {x}X. The results speak for themselves. ${symbol} climbed from {entry} MC to {current}, "
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
        pack = _get_alert_pack()
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

    crystal    = MC_ENTRY_EMOJI_ID  # locked — same across all packs, no longer pack["crystal"]
    teer       = TEER_EMOJI_ID
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
    TIERED_TEMPLATES so the owner's own per-range text always wins."""
    config = config or load_config()
    for r in config.get("range_templates", []):
        try:
            if int(r["low"]) <= x_val <= int(r["high"]):
                return r["template"]
        except (KeyError, ValueError, TypeError):
            continue
    return None

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

def build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol, badge=None):
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    # Priority: 1) specific milestone template  2) owner-defined range template
    #           3) global custom (owner set via /settemplate)
    #           4) hardcoded tiered range fallback (incl. champion at 10000X)  5) default
    # NOTE: global custom (alert_template) intentionally ranks ABOVE tiered so that
    # /settemplate always overrides the built-in 100x-9999x tiered texts.
    _ms_tmpl = config.get("milestone_templates", {}).get(str(x_val))
    if not _is_real_template(_ms_tmpl):
        if _ms_tmpl:
            logger.warning(f"Ignoring broken {x_val}X template ({_ms_tmpl!r}) — using range/tier template")
        _ms_tmpl = None
    template = (_ms_tmpl
                or _get_range_template(x_val, config)
                or config.get("alert_template")
                or _get_tiered_template(x_val)
                or DEFAULT_TEMPLATE)
    mae_link = f"{MAESTRO_REFLINK_BASE}{ca}{MAESTRO_REF_SUFFIX}" if ca else f"{MAESTRO_REFLINK_BASE}r-wizard_scan"
    kwargs = dict(channel=channel, x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=entry_fmt, current=current_fmt, ca=ca,
                  kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK,
                  mae_link=mae_link)
    try:
        text = safe_format(template, **kwargs)
    except Exception:
        text = safe_format(DEFAULT_TEMPLATE, **kwargs)

    # Append Leaderboard / Champions badge — ab post ke END par alag line nahi,
    # balki usi paragraph ke saath inline:
    #   "... Current MC stands at $342.4K. Champion KOL - Rank # 🔮"
    # "Champion KOL" / "Leaderboard KOL" = us list wali post ka link,
    # 🔮 = us KOL ki rank wali numbering premium emoji.
    if badge is None:
        badge = _get_kol_badge(channel)
    if badge:
        if badge["type"] == "leaderboard":
            text += f' <a href="https://t.me/WizardScan/136">Leaderboard KOL</a> - Rank # 🔮'
        else:
            text += f' <a href="https://t.me/WizardScan/137">Champion KOL</a> - Rank # 🔮'

    # Append owner-configured extra post links
    extra_links = config.get("extra_post_links", [])
    for lnk in extra_links:
        lnk_text = html.escape(lnk.get("text", ""))
        lnk_url  = lnk.get("url", "")
        if lnk_text and lnk_url:
            text += f'\n🔮<a href="{lnk_url}">{lnk_text}</a>'

    # ── 12-hour promo link — ALWAYS the very last line ───────────────────────
    # Champion KOL / Leaderboard KOL badge aur extra links ke BAAD, ek khali
    # line chhod kar, taake post saaf aur piyari lage.
    if 2 <= x_val <= 50:
        promo = config.get("promo_link")
        if promo and promo.get("url") and promo.get("text") and promo.get("set_at"):
            try:
                if datetime.utcnow() - datetime.fromisoformat(promo["set_at"]) < timedelta(hours=12):
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
    kwargs = dict(channel=channel, x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=entry_fmt, current=current_fmt, ca=ca,
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
    """X alert ke 6 fixed buttons:
       Caller (tracked post link) · Twitter (KOL ka X ya WizardScan)
       Trade  (Maestro reflink)   · Dex     (DexScreener chart)
       Details (filhal khali)     · Dev     (@Wizard_Scan)
       Owner ke /addbutton wale milestone buttons neeche add ho jate hain."""
    caller  = _x_alert_caller_link(channel, msg_id)
    twitter = _x_alert_twitter_link(channel)
    trade   = _x_alert_trade_link(ca)
    dex     = _x_alert_dex_link(ca, chain)

    row1 = []
    if caller:  row1.append(InlineKeyboardButton("👤 Caller",  url=caller))
    if twitter: row1.append(InlineKeyboardButton("𝕏 Twitter", url=twitter))
    row2 = []
    if trade:   row2.append(InlineKeyboardButton("⚡ Trade", url=trade))
    if dex:     row2.append(InlineKeyboardButton("📊 Dex",   url=dex))
    row3 = [InlineKeyboardButton("📄 Details", callback_data="xbtn:details"),
            InlineKeyboardButton("👨‍💻 Dev",   url=WIZARD_DEV_LINK)]

    rows = [r for r in (row1, row2, row3) if r]
    if x_val is not None:
        extra = load_config().get("milestone_buttons", {}).get(str(x_val), [])
        for b in extra:
            if b.get("text") and b.get("url"):
                rows.append([InlineKeyboardButton(b["text"], url=b["url"])])
    return InlineKeyboardMarkup(rows) if rows else None

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
    if 'Leaderboard KOL - Rank #' in plain_text:
        _badge_type = 'leaderboard'
    elif 'Champion KOL - Rank #' in plain_text:
        _badge_type = 'champion'
    badge_count = 1 if _badge_type else 0

    if badge_count and len(emoji_ids) >= badge_count:
        non_badge_ids = emoji_ids[:-badge_count]
        badge_ids     = emoji_ids[-badge_count:]
    else:
        non_badge_ids = emoji_ids
        badge_ids     = []

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

            if _this_marker in explicit:
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

            elif badge_ids and plain_text[:i].endswith('Rank # '):
                # "Champion KOL - Rank # 🔮" / "Leaderboard KOL - Rank # 🔮"
                # → us KOL ki rank numbering premium emoji
                eid = badge_ids[0]


            else:
                # ── MC separator lock ─────────────────────────────────────────
                # The 🔮 between entry MC and current MC must ALWAYS be TEER_EMOJI_ID.
                # Detect by context: preceded by MC value (ends K/M/B/digit) and
                # followed (after spaces) by $ or digit.
                before_stripped = plain_text[:i].rstrip()
                after_stripped  = rest.lstrip()
                if (before_stripped
                        and before_stripped[-1] in 'KMBkmbkm0123456789'
                        and after_stripped
                        and after_stripped[0] in '$0123456789'):
                    eid = TEER_EMOJI_ID
                    # NOTE: pos_index NOT advanced — teer slot is not counted against
                    # the positional list, keeping badge emoji alignment correct.
                elif non_badge_ids:
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

async def _userbot_send_with_premium_emoji(chat, text, emoji_id=None, link_preview=False, forced_pack=None):
    """Send via userbot. Returns sent Message object on success, None on failure."""
    if not userbot_client:
        return None
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
                    link_preview=link_preview
                )
                return msg
            except Exception as e:
                logger.warning(f"Custom emoji send failed, fallback: {e}")
        msg = await userbot_client.send_message(chat, text, parse_mode="html", link_preview=link_preview)
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
                bot_app, chat, file_id, file_type, text, emoji_id,
                keyboard=keyboard, forced_pack=forced_pack, _retry=False)
        logger.error(f"Userbot media send failed: {e}")
        return None

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
    template  = config.get("dropped_call_template") or DEFAULT_DROPPED_TEMPLATE
    kwargs    = dict(
        channel=html.escape(channel), symbol=(symbol or "TOKEN").upper(),
        chain=chain, entry=entry_fmt, ca=ca,
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
                            await asyncio.sleep(1.0)
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
            await asyncio.sleep(0.4)
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
# Ek slow send poori queue ko block na kare: kai workers parallel chalte hain,
# priority queue ki wajah se X alerts phir bhi dropped-call posts se aage hain.
RT_WORKER_COUNT = max(1, int(os.environ.get("RT_WORKER_COUNT", "3") or 3))
RT_MAX_SEND_RETRIES = max(0, int(os.environ.get("RT_MAX_SEND_RETRIES", "2") or 2))

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
RT_DROP_WORKER_COUNT = max(1, int(os.environ.get("RT_DROP_WORKER_COUNT", "2") or 2))

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
        except Exception as e:
            logger.error(f"silent milestone record failed: {e}")
        finally:
            _RT_INFLIGHT.discard(tag)
        return
    await _rt_put(RT_PRIO_X, "milestone", payload)

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


# Bot @WizardScan ka admin nahi hai to send_photo/send_video har dafa 403 deta
# hai. Ye flag wo nakaam raasta ek dafa ke baad band kar deta hai.
_BOT_CHANNEL_FORBIDDEN = {"v": False}

async def send_alert(bot, channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol,
                     force=False, record_only=False):
    config   = load_config()
    media    = config.get("milestone_media",{}).get(str(x_val)) or config.get("milestone_media",{}).get("global")
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
        if _call_key:
            try:
                await award_points_for_milestone(channel, _call_key, x_val)
            except Exception as _e_pt:
                logger.warning(f"points award failed (silent): {_e_pt}")
        # Ranking refresh: recorded-only milestones bhi leaderboard/champions me
        # foran count hone chahiye (owner requirement).
        if x_val >= 2:
            try:
                asyncio.create_task(_update_leaderboard_with_premium_emojis(bot))
                asyncio.create_task(_update_champions_with_premium_emojis(bot))
            except Exception:
                pass
        logger.info(f"⏸️ {x_val}X @{channel} recorded only — no channel post")
        return "silent"

    # Compute badge once, pass to build_alert + emoji list
    badge    = _get_kol_badge(channel)
    text     = build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol, badge=badge)
    keyboard = build_x_alert_keyboard(channel, msg_id, ca, chain, x_val=x_val)
    # Pack-based premium emojis (rotate every 10 posts, 5 packs total)
    pack     = _get_alert_pack()
    emoji_id = list(_get_alert_emoji_ids(x_val, chain, pack))
    # Append badge premium emoji ID — ab sirf 1 (rank numbering emoji),
    # kyunki naya badge format "Champion KOL - Rank # 🔮" hai (koi star 🔮 nahi).
    if badge:
        if badge["type"] == "leaderboard":
            rank_emoji_id = LEADERBOARD_PREMIUM_EMOJIS.get(badge["rank"], LEADERBOARD_PREMIUM_EMOJIS[1])
            emoji_id += [rank_emoji_id]
        else:
            rank_emoji_id = CHAMPIONS_PREMIUM_EMOJIS.get(badge["rank"], CHAMPIONS_PREMIUM_EMOJIS[1])
            emoji_id += [rank_emoji_id]
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

    # `force=True` is the real-time path.  Keep media in realtime alerts, but
    # use the Telegram Bot API/file_id first.  This reuses Telegram's stored
    # media instantly and avoids the slow userbot download -> re-upload path.
    # Normal alerts still prefer the userbot so premium emoji entities remain.
    if media and media.get("file_id"):
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
            for attempt in (1, 2):
                try:
                    sent_msg = await asyncio.wait_for(
                        _userbot_send_media_with_emoji(
                            bot, TARGET_CHANNEL, fid, ftype, text, emoji_id,
                            keyboard=keyboard),
                        timeout=USERBOT_SEND_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    sent_msg = None
                    media_fail_reason = f"userbot media timed out after {USERBOT_SEND_TIMEOUT_SECONDS}s (attempt {attempt})"
                    if attempt == 1:
                        logger.warning(f"{media_fail_reason}, retrying once")
                        continue
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
                            TARGET_CHANNEL, text, emoji_id=emoji_id),
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
    if wizard_post_id:
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
        for kol_ch, linked_ch in linked.items():
            if str(kol_ch).lstrip("@").lower() == channel_key:
                destinations.append((linked_ch, f"({x_val}X linked)"))

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


# ─── True ATH / peak tracking (GeckoTerminal OHLCV) ───────────────────────────
# Real-time peak: monitoring_job stores the highest ratio it ever sees.
# Backfill: GeckoTerminal candle highs recover peaks that happened while the
# bot was restarting/offline — this is how the displayed X becomes a REAL ATH
# multiplier (like Kolscope / SpyDefi) instead of the current live price.
_GT_NET = {"SOL": "solana", "ETH": "eth", "BNB": "bsc", "BASE": "base",
           "TON": "ton", "RH": "robinhood", "ARB": "arbitrum"}
_ath_pool_cache: dict = {}

def _gt_get(url, timeout=12):
    try:
        r = _dex_session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"gecko get failed {url}: {e}")
    return None

def _gt_best_pool(ca, chain):
    """(network, pool_address) of the deepest pool for this token, or None."""
    net = _GT_NET.get((chain or "SOL").upper())
    if not net or not ca:
        return None
    key = f"{net}:{ca}"
    if key in _ath_pool_cache:
        return _ath_pool_cache[key]
    d = _gt_get(f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{ca}/pools?page=1")
    best, best_liq = None, -1.0
    for p in ((d or {}).get("data") or []):
        a = p.get("attributes", {}) or {}
        try:
            liq = float(a.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if liq > best_liq and a.get("address"):
            best_liq, best = liq, a["address"]
    if best:
        _ath_pool_cache[key] = (net, best)
        return (net, best)
    return None

def fetch_entry_and_peak_sync(ca, chain, since_dt):
    """(entry_price_at_call_time, highest_price_since) from real candles.

    entry_price = OPEN of the candle that contains the call timestamp.
    Both are 0.0 when unknown."""
    pool = _gt_best_pool(ca, chain)
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
        if ts + span < since_ts:      # candle fully before the call
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

def _verified_live_ratio(call, dex):
    """Return a market-cap-first live ratio, or 0 when the sample is unsafe.

    Price ratios are not interchangeable across pool migrations, decimal/supply
    changes or bad pair responses. Market cap is therefore authoritative. A
    price ratio may only be used when MC is unavailable, and conflicting price
    data can never inflate a valid MC ratio.
    """
    try:
        entry_mc = float(call.get("entry_mc", 0) or 0)
        entry_price = float(call.get("entry_price", 0) or 0)
        cur_mc = float((dex or {}).get("mcap", 0) or 0)
        cur_price = float((dex or {}).get("price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    mc_ratio = cur_mc / entry_mc if entry_mc > 0 and cur_mc > 0 else 0.0
    price_ratio = cur_price / entry_price if entry_price > 0 and cur_price > 0 else 0.0
    if mc_ratio > 0:
        if price_ratio > 0:
            divergence = max(mc_ratio, price_ratio) / max(min(mc_ratio, price_ratio), 1e-12)
            if divergence > 3:
                logger.warning(
                    f"Ignoring conflicting price ratio {price_ratio:.2f}x; "
                    f"DexScreener MC ratio is {mc_ratio:.2f}x for {call.get('ca','')[:12]}…")
        return mc_ratio
    # Last-resort price tracking is deliberately conservative. Once a real MC
    # appears, all future ticks automatically switch to MC-first tracking.
    if 0 < price_ratio <= 1000:
        return price_ratio
    return 0.0

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
    return bool(
        call.get("ca") and call.get("msg_id") and call.get("symbol")
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
        if call.get("channel", "").lower() != channel.lower() or call.get("post_deleted"):
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
    cutoff = datetime.utcnow() - timedelta(days=90)
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
        if not top:
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
        if not _complete_history_call(call, highest_x, current_x, current_mc):
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
            if not top:
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
    for call in calls[:30]:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = str(call.get("chain") or "").upper()
        chain_emoji = _history_chain_emoji(chain)
        arrow_emoji = _history_arrow_emoji()
        ca     = call["ca"]
        ef     = html.escape(call["entry_fmt"])
        hf     = html.escape(call["highest_mc_fmt"])
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        # Build post link (t.me/{channel}/{msg_id}) or chart fallback
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">{HISTORY_WAND_EMOJI} View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        # KOL TG override link (from addmissedcall)
        kol_tg = call.get("kol_tg_link", "")
        if kol_tg:
            post_link = f'<a href="{kol_tg}">{HISTORY_WAND_EMOJI} View Post</a>'
        # X string — real decimal (e.g. 2.7x, 15.3x)
        x_label = f" {fmt_x(cx)}"
        # X/Twitter handle for this channel
        x_handle = _get_channel_x_handle(channel)
        x_link = f'  <a href="https://x.com/{html.escape(x_handle)}">𝕏</a>' if x_handle else ""
        lines.append(
            f'\n{chain_emoji} <b>${sym} {HISTORY_X_EMOJI}{x_label}</b>{x_link}\n'
            f'     {post_link}  |  {ef} to {hf}'
        )
        shown += 1
    if len(calls) > 30:
        lines.append(f"\n\n<i>Showing 30 of {len(calls)} calls.</i>")
    # Show channel points at the end
    ch_pts = get_channel_points(channel)
    lines.append(f"\n\n<i>This channel has {ch_pts}/{POINTS_FOR_CHAMPION} points</i>")
    return "\n".join(lines)


def _build_html_caption_for_video(channel, calls, limit=1024):
    """Build valid HTML caption matching format_history style, fitting within 1024-char limit."""
    ch_safe = html.escape(_display_handle(channel))
    title_emoji = _history_emoji(HISTORY_TITLE_EMOJI, "🔮")
    header  = f"<b>{title_emoji} Calls History Of @{ch_safe}</b>\n"
    body    = ""
    for call in calls[:30]:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = str(call.get("chain") or "").upper()
        chain_emoji = _history_chain_emoji(chain)
        ca     = call["ca"]
        ef     = html.escape(call["entry_fmt"])
        hf     = html.escape(call["highest_mc_fmt"])
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">{HISTORY_WAND_EMOJI} View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        x_label = f" {fmt_x(cx)}"
        entry = (
            f'\n\n{chain_emoji} <b>${sym} {HISTORY_X_EMOJI}{x_label}</b>\n'
            f'     {post_link}  |  {ef} to {hf}'
        )
        if len(header) + len(body) + len(entry) > limit:
            break
        body += entry
    return header + body

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
                    ms = list(sent_milestones.get(ck, set()))
                    if ms and max(ms) == bx:
                        bck = ck; break
                if bck is None:
                    for ck, call in tracked_calls.items():
                        if call.get("channel","").lower() != ch.lower(): continue
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

def build_leaderboard_text():
    """Build top 10 leaderboard with 🔮 placeholders for premium emojis.
    Order: star(header), then per row: num+arrow, then star(footer)."""
    top10 = _calc_leaderboard_scores()

    # User-set custom template support
    tmpl = cfg_get("leaderboard_template","")
    if tmpl:
        kwargs = {}
        for i in range(10):
            idx = i + 1
            if i < len(top10):
                ch, x, wpost = top10[i]
                kwargs[f"rank{idx}_link"]    = f"@{html.escape(ch)}"
                kwargs[f"rank{idx}_channel"] = f"@{html.escape(ch)}"
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
        lines.append(f"🔮 <b>@{html.escape(ch)}</b> 🔮 {x_part}{x_link}")

    if not lines:
        return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
                "<i>No data yet. Tracking in progress...</i>\n\n"
                "🔮 <b>New List in 3 Days</b>")

    # Pad to 10 slots with dashes for empty ranks
    for i in range(len(lines), 10):
        lines.append(f"🔮 <b>—</b> 🔮 —")

    return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
            + "\n".join(lines)
            + "\n\n🔮 <b>New List in 3 Days</b>")

def build_champions_text():
    """Build champion kols list for post 137.
    Only channels with >= POINTS_FOR_CHAMPION (100) points qualify.
    Sorted by points descending. Uses 🔮 placeholders for premium emojis."""
    channels = _ranking_channels()
    pts_data = load_channel_points()

    # Exclude milestones that existed before the last champion reset
    cfg_now      = load_config()
    excluded_keys = set(cfg_now.get("champion_excluded_call_keys", []))  # legacy blanket list
    champ_snap    = cfg_now.get("champion_milestone_snapshot", {})       # snapshot-based (preferred)

    champions = []
    for ch in channels:
        pts = pts_data.get(ch.lower(), {}).get("points", 0)
        if pts >= POINTS_FOR_CHAMPION:
            best_x, best_call_key = champion_total_x(ch, champ_snap, excluded_keys)
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
        lines.append(f"🔮 <b>@{html.escape(ch)}</b> 🔮 {x_part}{x_link}")

    # Show at least 5 slots; grow up to a maximum of 10 as more champions qualify
    _slots = max(5, min(len(lines), 10))
    for i in range(len(lines), _slots):
        lines.append(f"🔮 <b>—</b> 🔮 —")

    footer = ("\n\n🔮 KOLs need 100 points to appear here. These are the verified Champion KOLs "
              "of Wizard Scan, ranked purely on real, live-tracked call performance. Making this "
              "list is tough: only the most consistent, high-conviction based callers ever reach it.")

    return "🔮 <b>CHAMPION KOLS</b>\n\n" + "\n".join(lines) + footer

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
        if post_date:
            try:
                parsed_date = datetime.fromisoformat(str(post_date).replace("Z", "+00:00"))
                post_ts = parsed_date.timestamp()
                lookback_minutes = float(os.environ.get("CALL_CATCHUP_MINUTES", "30") or 30)
                if channel in set(load_special_channels()):
                    lookback_minutes = float(os.environ.get("SPECIAL_BACKFILL_MINUTES", "180") or 180)
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
        # Now that this is a real token call, claim the message so the
        # realtime handler + polling safety-net cannot double-process it.
        seen_message_ids[channel].add(msg_id_str)
        _save_seen()
        call_key = f"{channel}_{ca}"
        if call_key in tracked_calls:
            return False

        # MC written in the KOL post — treated only as a HINT. It is never
        # trusted blindly: _reconcile_entry_mc() cross-checks it against the
        # live chart MC and discards it when the two clearly disagree.
        post_mc = extract_mc_from_text(text)

        # ── Track immediately — NO dex wait ──────────────────────────────────
        # entry data filled in by milestone_job once token appears on Dexscreener
        tracked_calls[call_key] = {
            "channel":      channel,
            "msg_id":       msg_id_str,
            "ca":           ca,
            "chain":        chain_guess,   # "SOL" or "EVM"
            "entry_mc":     post_mc if post_mc > 0 else 0,
            "entry_price":  0,
            "entry_fmt":    fmt_mc(post_mc) if post_mc > 0 else "N/A",
            "entry_locked": False,         # stays open until live data confirms
            "post_mc_hint": post_mc,       # KOL-stated MC (unverified)
            "symbol":       "",
            "tracked_since": datetime.utcnow().isoformat(),
            "dex_pending":  True,          # milestone_job will fill this in
        }

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

            for phase_count, phase_sleep in retry_schedule:
                if _is_full(dex):
                    break  # complete data already found in a previous phase
                for _attempt in range(phase_count):
                    try:
                        # A brand-new call must never read an older cached quote.
                        # Bypass the short Dex cache on every retry so the first
                        # usable MC/price is captured as soon as the token indexes.
                        _invalidate_dex_cache(ca)
                        fetched = await asyncio.wait_for(
                            asyncio.to_thread(_fetch_dex_sync, ca, 1, 0),  # min_liquidity=0
                            timeout=6)
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
                    # Entry MC = KOL's stated MC when it is plausible vs the
                    # live chart, otherwise the live chart MC. This kills the
                    # "called at 15K, tracked at 25K" drift without trusting a
                    # wrong number written in the post.
                    if not _c_upd.get("entry_locked"):
                        _live_mc = dex.get("mcap", 0) or 0
                        _hint_mc = _c_upd.get("post_mc_hint", 0) or 0
                        _age = _t.time() - _call_detected_at
                        if _hint_mc > 0 and _age > ENTRY_MC_FRESH_SECONDS:
                            # quote arrived too late to be "the entry"
                            _entry_mc, _src = (_hint_mc, "post")
                        else:
                            _entry_mc, _src = _reconcile_entry_mc(_hint_mc, _live_mc)
                        _entry_price = dex.get("price", 0)
                        if _src == "post" and _live_mc > 0 and _entry_price and _entry_mc > 0:
                            _entry_price = _entry_price * (_entry_mc / _live_mc)
                        _c_upd.update({
                            "entry_mc":    _entry_mc,
                            "entry_price": _entry_price,
                            "entry_fmt":   fmt_mc(_entry_mc) if _entry_mc > 0
                                           else dex.get("mcap_fmt", "N/A"),
                            "entry_src":   _src,
                            "entry_locked": True,
                        })

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
        resolved = []
        for ch in channels:
            try:
                try:
                    from telethon.tl.functions.channels import JoinChannelRequest
                    await asyncio.wait_for(userbot_client(JoinChannelRequest(ch)), timeout=15)
                except Exception:
                    pass  # already joined or public access — get_entity still works
                ent = await asyncio.wait_for(userbot_client.get_entity(ch), timeout=10)
                resolved.append(ent)
            except Exception as e:
                logger.warning(f"Realtime: could not resolve/join @{ch}: {e}")

        if not resolved:
            logger.warning("Realtime monitoring: could not resolve any channels")
            return

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

        @userbot_client.on(events.NewMessage(chats=resolved))
        async def _realtime_handler(event):
            global _bot_ref
            try:
                if _bot_ref is None:
                    return
                chat = await event.get_chat()
                username = (getattr(chat, 'username', None) or "").lower()
                channels_now = [c.lower() for c in load_channels()]
                if username not in channels_now:
                    return
                text   = event.text or event.caption or ""
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

        @userbot_client.on(events.MessageDeleted(chats=resolved))
        async def _deleted_post_handler(event):
            """Penalize a KOL once and stop a deleted call from producing alerts."""
            try:
                deleted_ids = {str(mid) for mid in (event.deleted_ids or [])}
                if not deleted_ids:
                    return
                chat = await event.get_chat()
                username = (getattr(chat, "username", None) or "").lower()
                changed = False
                for call_key, call in tracked_calls.items():
                    if call.get("channel", "").lower() != username:
                        continue
                    if str(call.get("msg_id", "")) not in deleted_ids or call.get("post_deleted"):
                        continue
                    call["post_deleted"] = True
                    call["frozen"] = True
                    call["deleted_at"] = datetime.utcnow().isoformat()
                    await deduct_points_for_deleted_post(username, call_key)
                    logger.info(f"🗑 Deleted KOL post: -10 points @{username} ({call_key})")
                    changed = True
                if changed:
                    _save_tracked()
            except Exception as e:
                logger.warning(f"Deleted-post handler failed: {e}")

        _RT_DELETED_HANDLER = _deleted_post_handler
        logger.info(f"✅ Realtime monitoring active for {len(resolved)} channels")
    except (RuntimeError, TypeError) as e:
        # Suppress Telethon v1.44.0 internal errors (Event loop is closed etc.)
        logger.warning(f"setup_realtime_monitoring Telethon error (suppressed): {e}")
    except Exception as e:
        logger.error(f"setup_realtime_monitoring failed: {e}")


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
        regular = [ch for ch in channels if ch not in set(special)]
        slice_size = max(1, int(os.environ.get("SCAN_BATCH_SIZE", "60") or 60))
        regular_batch = []
        if regular:
            i = _SCAN_CURSOR["i"] % len(regular)
            regular_batch = [regular[(i + k) % len(regular)]
                             for k in range(min(slice_size, len(regular)))]
            _SCAN_CURSOR["i"] = (i + len(regular_batch)) % len(regular)
        batch = special + regular_batch

        sem = asyncio.Semaphore(max(1, int(os.environ.get("SCAN_CONCURRENCY", "24") or 24)))
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
                except Exception as e:
                    logger.error(f"scan_job @{channel}: {e}")

        await asyncio.gather(*[_scan_one(ch) for ch in batch])
        if found:
            logger.info(f"🔎 scan_job: {found} new call(s) from {len(batch)} channel(s)")

    except Exception as e:
        logger.error(f"scan_job crash: {e}")


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
RUGGED_CALL_MAX_AGE_HOURS = float(os.environ.get("RUGGED_CALL_MAX_AGE_HOURS", "1") or 1)

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
            }
        save_call_archive(archive)
    except Exception as e:
        logger.warning(f"call archive save failed (pruned calls still deleted): {e}")

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
                    # Announce the highest newly-reached X (missed by live polling).
                    # Old calls (>3 days) are recorded silently to avoid spam.
                    fresh = (datetime.utcnow() - since) < timedelta(days=3)
                    if fresh:
                        top_ms  = new_ms[-1]
                        ms_fmt  = fmt_mc(entry_mc * top_ms) if entry_mc > 0 else "N/A"
                        try:
                            posted = await send_alert(
                                context.bot, call["channel"], call["msg_id"],
                                top_ms, call.get("chain", "SOL"),
                                call.get("entry_fmt", "N/A"), ms_fmt,
                                call["ca"], call.get("symbol", ""))
                            if posted:
                                for ms in new_ms:
                                    sent_milestones[call_key].add(ms)
                                _save_milestones()
                                logger.info(f"🚀 (backfill) {call.get('symbol','?')} "
                                            f"@{call['channel']} {top_ms}X!")
                            else:
                                logger.warning(f"Backfill alert not posted; will retry: {top_ms}X {call_key}")
                        except Exception as e_al:
                            logger.error(f"backfill alert failed: {e_al}")
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

        urgent = []
        regular = []
        seen_urgent = set()
        for kv in items:
            k, c = kv
            age = _age_seconds(c)
            pending_stuck_ok = age <= STUCK_URGENT_CUTOFF_SECONDS
            is_urgent = bool(
                (c.get("alert_pending") and pending_stuck_ok)
                or (c.get("dex_pending") and pending_stuck_ok)
                or age <= 300
            )
            if is_urgent and k not in seen_urgent:
                seen_urgent.add(k)
                urgent.append(kv)
            else:
                regular.append(kv)

        urgent.sort(key=lambda kv: _age_seconds(kv[1]))
        max_checks = max(20, int(os.environ.get(
            "MONITOR_PRIORITY_MAX_CHECKS_PER_TICK" if prio_mode else "MONITOR_MAX_CHECKS_PER_TICK",
            "120" if prio_mode else "90") or (120 if prio_mode else 90)))
        remaining = max(0, max_checks - len(urgent))
        if regular and remaining:
            cursor_name = "priority" if prio_mode else "bulk"
            cursor = (_PRIO_CHECK_CURSOR if prio_mode else _MON_CHECK_CURSOR) % len(regular)
            rotated = [regular[(cursor + j) % len(regular)] for j in range(min(remaining, len(regular)))]
            if prio_mode:
                _PRIO_CHECK_CURSOR = (cursor + len(rotated)) % len(regular)
            else:
                _MON_CHECK_CURSOR = (cursor + len(rotated)) % len(regular)
            items = urgent[:max_checks] + rotated
            logger.debug(f"monitoring {cursor_name}: urgent={len(urgent)} rotated={len(rotated)} total={len(items)}")
        else:
            items = urgent[:max_checks] if urgent else regular[:max_checks]

        # ── Watchdog: pending alerts must never get stuck forever ────────────
        # If a call has been waiting > 5 min and we at least know the symbol,
        # post it now (MC filled as soon as DexScreener indexes the token).
        for _k, _c in items:
            if not _c.get("alert_pending"):
                continue
            try:
                _since = datetime.fromisoformat(_c.get("tracked_since"))
            except Exception:
                continue
            if (datetime.utcnow() - _since).total_seconds() < 300:
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
            _cas = [v.get("ca") for _, v in items if v.get("ca") and not v.get("frozen")]
            # ca → chain, so if DexScreener 429s mid-batch we can hand the rest
            # to Birdeye without guessing which chain each address is on.
            _chain_map = {v.get("ca"): v.get("chain", "SOL")
                          for _, v in items if v.get("ca") and not v.get("frozen")}
            _chunks = [_cas[i:i + 30] for i in range(0, len(_cas), 30)]
            if _chunks:
                _max_batches = max(1, int(os.environ.get("MONITOR_BULK_MAX_BATCHES", "3") or 3))
                _start = _DEX_BULK_CURSOR % len(_chunks)
                _selected = [_chunks[(_start + j) % len(_chunks)] for j in range(min(_max_batches, len(_chunks)))]
                _DEX_BULK_CURSOR = (_start + len(_selected)) % len(_chunks)
                await asyncio.gather(*[
                    asyncio.to_thread(_bulk_refresh_dex_sync, ch, 0, _chain_map) for ch in _selected
                ], return_exceptions=True)
        except Exception as e_bulk:
            logger.warning(f"bulk dex refresh failed: {e_bulk}")

        # HOT-CALL FAST LANE:
        # For the newest calls, do one direct fresh quote in addition to the
        # bulk pass. This protects against a Dex bulk snapshot being briefly
        # stale exactly when a new call rips through 2X/3X.
        # Bounded to 4 calls/tick to avoid API flooding.
        try:
            _hot = []
            for _hk, _hc in items:
                if _hc.get("frozen") or not _hc.get("ca"):
                    continue
                try:
                    _age = (datetime.utcnow() - datetime.fromisoformat(
                        str(_hc.get("tracked_since", "")))).total_seconds()
                except Exception:
                    _age = 999999
                if _age <= 180:
                    _hot.append((_hk, _hc))
                if len(_hot) >= 4:
                    break
            if _hot:
                await asyncio.gather(*[
                    asyncio.to_thread(_bulk_refresh_dex_sync, [_hc.get("ca")])
                    for _, _hc in _hot
                ], return_exceptions=True)
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
        _fallback_budget = {"used": 0, "max": max(0, int(os.environ.get(
            "MONITOR_PRIORITY_MAX_FALLBACKS_PER_TICK" if prio_mode else "MONITOR_MAX_FALLBACKS_PER_TICK",
            "16" if prio_mode else "10") or (16 if prio_mode else 10)))}
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
                    _fallback_budget["used"] += 1
                    async with _monitor_miss_sem:
                        try:
                            dex = await asyncio.wait_for(
                                fetch_dexscreener(call["ca"], allow_alt=False, retries=1, min_liquidity=0),
                                timeout=_fallback_timeout)
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Market-data primary timeout: "
                                f"{call['ca'][:12]}...")
                            dex = None
                if dex is None: return []

                # DexScreener can legally return HTTP 200 with a temporarily
                # stale pair snapshot.  That used to leave a fast token stuck
                # at 1.0X forever because the fallback path only ran when the
                # primary provider returned no pair at all.  Probe the
                # independent providers for sub-2X calls and prefer their
                # quote only when it materially advances the ratio.
                try:
                    dex_ratio = _verified_live_ratio(call, dex)
                except Exception:
                    dex_ratio = 0.0
                if (EXTRA_SOURCES_ENABLED and dex_ratio < 1.95):
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
                            if alt_ratio > max(dex_ratio * 1.15, dex_ratio + 0.10):
                                logger.info(
                                    f"📈 fresher alternate quote selected "
                                    f"{call.get('symbol','?')} "
                                    f"{dex_ratio:.2f}X → {alt_ratio:.2f}X "
                                    f"({alt_quote.get('_source','alt')})")
                                dex = alt_quote

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
                        _entry_mc, _src = _reconcile_entry_mc(
                            call.get("post_mc_hint", 0), cur_mc)
                        _entry_price = cur_price
                        if _src == "post" and cur_mc > 0 and cur_price and _entry_mc > 0:
                            _entry_price = cur_price * (_entry_mc / cur_mc)
                        call["entry_price"] = _entry_price
                        call["entry_mc"]    = _entry_mc
                        call["entry_fmt"]   = (fmt_mc(_entry_mc) if _entry_mc > 0
                                               else dex.get("mcap_fmt", "N/A"))
                        call["entry_src"]   = _src
                        call["entry_locked"] = True

                    call["symbol"]      = dex.get("symbol", "")
                    call["chain"]       = dex.get("chain", call.get("chain", "SOL"))
                    call["dex_pending"] = False
                    logger.info(f"📊 entry filled: {call['symbol']} @{call['channel']} {call['entry_fmt']}")
                    # Deferred dropped-call alert: we skipped it earlier because
                    # data was N/A. Now that we have real data, post it.
                    if call.get("alert_pending") and call.get("symbol") and call.get("entry_fmt") not in ("", "N/A"):
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
                # HARD stop: token really is dead / data is garbage.
                _HARD_RUG = ("liquidity_pulled", "already_rugged", "absurd_mc",
                             "bad_data", "no_mc")
                _record_only = False
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
                call["last_ratio"] = round(ratio, 4)

                # ── Real-time ATH: remember the highest ratio ever seen ──────
                _update_peak(call, ratio, cur_mc)
                peak_ratio = call_peak_ratio(call)

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
                top_ms = pending[-1]

                triggered = []
                # Stagger: lowest new X (e.g. 2X) fires INSTANTLY, each further
                # X of the same call follows 5 seconds later — so a token that
                # rips 10x in one second still gets 2X now, 3X in 5s, 4X in 10s...
                _slot = 0
                for ms in pending:
                    # FIX (wrong MC bug): when several milestones fire in the same
                    # tick (e.g. a call jumps straight to 4X), each post MUST show
                    # the market cap of ITS OWN level — 2X post = entry×2,
                    # 3X post = entry×3 — instead of repeating the live MC.
                    if ms == top_ms and cur_mc > 0 and cur_mc < 1_000_000_000_000 and ratio >= ms:
                        expected_mc = entry_mc * ms if entry_mc > 0 else 0
                        # live MC wildly off vs. expectation → trust the maths
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
                                               delay=_slot * MS_STAGGER_SECONDS)
                    _slot += 1
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
            if _fallback_budget["used"] >= _fallback_budget["max"]:
                break

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

# ─── Leaderboard auto-update job ─────────────────────────────────────────────
_LAST_LB_TEXT    = None   # last text actually pushed to the leaderboard post
_LAST_CHAMP_TEXT = None   # last text actually pushed to the champions post
async def _update_leaderboard_with_premium_emojis(bot):
    """Edit post 136 with leaderboard data + all premium emojis via userbot."""
    global userbot_client, _LAST_LB_TEXT
    text    = build_leaderboard_text()
    # Nothing changed since the last edit → skip the Telegram call entirely.
    # (Job now runs every 5 s; this keeps us far away from edit rate limits.)
    if text == _LAST_LB_TEXT:
        return False
    top10   = _calc_leaderboard_scores()
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
            logger.error(f"Leaderboard premium emoji edit failed: {e}")
            # Do NOT fall back to bot API — it would strip premium emojis from the post.
            # Better to keep the old premium-emoji post than overwrite with plain text.
            return False

    # Userbot unavailable — skip update to preserve existing premium emojis on the post.
    logger.warning("Leaderboard update skipped: userbot unavailable (preserving premium emojis).")
    return False

async def leaderboard_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 136 (leaderboard) every 1-2 minutes. Auto-resets scores every 3 days."""
    config = load_config()
    now    = datetime.utcnow()

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
    """Edit post 137 with champions data + all premium emojis via userbot."""
    global userbot_client, _LAST_CHAMP_TEXT
    text     = build_champions_text()
    if text == _LAST_CHAMP_TEXT:
        return False
    # Build emoji_ids to match EXACTLY the 🔮 placeholders in build_champions_text().
    # build_champions_text() always pads to 10 rows (real champions + dash rows),
    # so the text always has: 1 header 🔮 + 10 rows × 2 🔮 + 1 footer 🔮 = 22 total 🔮.
    # emoji_ids must have exactly 22 entries — otherwise the userbot edit fails
    # and falls back to plain bot API (no premium emojis).
    pts_data = load_channel_points()
    channels = load_channels()
    excluded_keys = set(load_config().get("champion_excluded_call_keys", []))
    champ_count = sum(
        1 for ch in channels
        if pts_data.get(ch.lower(), {}).get("points", 0) >= POINTS_FOR_CHAMPION
    )
    n = min(champ_count, 10)  # real champion rows (0–10)
    rows = max(5, n)          # minimum 5 slots, max 10

    emoji_ids = [CHAMPIONS_PREMIUM_EMOJIS["star"]]  # header star (row 0 🔮)
    for i in range(1, rows + 1):   # 5–10 rows
        if i <= n:
            # Real champion row — use the rank-specific emoji
            emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS.get(i, CHAMPIONS_PREMIUM_EMOJIS[1]))
        else:
            # Dash / empty row — use rank-1 emoji as neutral placeholder
            emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS[1])
        emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["arrow"])  # second 🔮 in every row
    emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["star"])  # footer star (last 🔮)

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
            logger.info("✅ Champions post 137 updated with premium emojis")
            return True
        except Exception as e:
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
        logger.info("✅ Champions post 137 updated via bot API (userbot not configured)")
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            chat_id=TARGET_CHANNEL, message_id=POST_CHAMPIONS,
            text=text, parse_mode="HTML", disable_web_page_preview=True
        )
        logger.info("✅ Champions post 137 updated via bot API text fallback")
        return True
    except Exception as e2:
        logger.error(f"Champions bot API fallback also failed: {e2}")
        return False

async def champions_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 137 (champions) every 1-2 minutes. Resets all points every 7 days."""
    config   = load_config()
    now      = datetime.utcnow()

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

async def trending_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 135 (trending) every 2 minutes.
    If a trending cache exists, only refresh MC prices (tokens stay fixed).
    Token list auto-resets every 24 hours OR after a manual /resetXXXtrend command."""
    try:
        now = datetime.utcnow()
        # Auto-reset check: if last full fetch was more than 24h ago, force re-fetch
        last_upd = cfg_get("last_trending_update", "")
        auto_reset = False
        if last_upd:
            try:
                age_h = (now - datetime.fromisoformat(last_upd)).total_seconds() / 3600
                if age_h >= TRENDING_AUTO_RESET_HOURS:
                    auto_reset = True
                    logger.info(f"Trending auto-reset triggered (age={age_h:.1f}h)")
            except Exception:
                pass

        cache = load_trending_cache()
        if cache and any(len(v) > 0 for v in cache.values()) and not auto_reset:
            updated = await _update_prices_for_chain_tokens(cache)
            _inject_pinned_tokens(updated)
            save_trending_cache(updated)
            ok = await _update_trending_with_premium_emojis(context.bot, chain_tokens=updated)
        else:
            if auto_reset:
                save_trending_cache({})  # clear stale cache
            chain_tokens = await fetch_trending()
            if any(len(v) > 0 for v in chain_tokens.values()):
                save_trending_cache(chain_tokens)
                ok = await _update_trending_with_premium_emojis(context.bot, chain_tokens=chain_tokens)
            else:
                # Never persist/edit an empty transient API response. Keeping the
                # timestamp unchanged forces another full fetch on the next cycle.
                logger.warning("Trending full fetch returned empty; retrying next cycle")
                ok = False
        if ok:
            cfg_set("last_trending_update", now.isoformat())
    except Exception as e:
        logger.error(f"Trending post 135 update failed: {e}")

async def trending2_job(context: ContextTypes.DEFAULT_TYPE):
    """Update posts 3560 + 3562 (new trending) every 2 minutes.
    If a trending2 cache exists, only refresh MC prices (tokens stay fixed).
    Token list auto-resets every 24 hours."""
    try:
        now = datetime.utcnow()
        # Auto-reset check
        last_upd2 = cfg_get("last_trending2_update", "")
        auto_reset2 = False
        if last_upd2:
            try:
                age_h = (now - datetime.fromisoformat(last_upd2)).total_seconds() / 3600
                if age_h >= TRENDING_AUTO_RESET_HOURS:
                    auto_reset2 = True
                    logger.info(f"Trending2 auto-reset triggered (age={age_h:.1f}h)")
            except Exception:
                pass

        cache2 = load_trending2_cache()
        if cache2 and any(len(v) > 0 for v in cache2.values()) and not auto_reset2:
            updated2 = await _update_prices_for_chain_tokens(cache2)
            _inject_pinned_tokens(updated2)
            save_trending2_cache(updated2)
            res = await _update_trending2_posts(context.bot, chain_tokens2=updated2)
        else:
            if auto_reset2:
                save_trending2_cache({})
            chain_tokens2 = await fetch_trending2()
            if any(len(v) > 0 for v in chain_tokens2.values()):
                save_trending2_cache(chain_tokens2)
                res = await _update_trending2_posts(context.bot, chain_tokens2=chain_tokens2)
            else:
                logger.warning("Trending2 full fetch returned empty; retrying next cycle")
                res = {}
        if any(res.values()):
            cfg_set("last_trending2_update", now.isoformat())
    except Exception as e:
        logger.error(f"Trending posts 3560/3562 update failed: {e}")

# Owner ne "MOMENTUM ACTIVE" posts filhal band karwa di hain. Job schedule bhi
# off hai aur ye flag double safety hai — dobara chalu karne ke liye True kar do.
MOMENTUM_POSTS_ENABLED = False   # PERMANENTLY REMOVED — do not re-enable

async def momentum_check_job(context: ContextTypes.DEFAULT_TYPE):
    """DISABLED PERMANENTLY — MOMENTUM ACTIVE posts removed."""
    return
    try:
        channels      = load_channels()
        now           = datetime.utcnow()
        week_ago      = now - timedelta(days=7)
        momentum_sent = load_momentum_sent()

        for ch in channels:
            # Collect calls from this channel tracked within the last 7 days
            weekly_calls = []
            for call_key, call in tracked_calls.items():
                if call.get("channel","").lower() != ch.lower(): continue
                ts_str = call.get("tracked_since","")
                try:
                    if datetime.fromisoformat(ts_str) >= week_ago:
                        weekly_calls.append((call_key, call))
                except Exception: pass

            if len(weekly_calls) < 5:
                continue

            # Group calls by which X milestones they hit
            from collections import defaultdict as _dd
            x_calls = _dd(list)
            for call_key, call in weekly_calls:
                # Include milestones held back for missing media — they are still
                # part of the channel's real momentum record.
                _ms = set(sent_milestones.get(call_key, set()))
                try:
                    _ms |= {int(x) for x in pending_media_alerts.get(call_key, set())}
                except Exception:
                    pass
                for x in _ms:
                    x_calls[x].append((call_key, call))

            for x_val, calls in x_calls.items():
                if len(calls) < 5: continue
                if x_val < 10: continue  # Only post MOMENTUM ACTIVE for 10x+ calls
                # Skip if already posted this combo within 7 days
                ch_sent = momentum_sent.get(ch, {})
                last_sent = ch_sent.get(str(x_val), "")
                if last_sent:
                    try:
                        if now - datetime.fromisoformat(last_sent) < timedelta(days=7): continue
                    except Exception: pass

                # Build MOMENTUM ACTIVE post
                bot_username = (await context.bot.get_me()).username
                xray_url     = f"https://t.me/{bot_username}?start=xray_{ch}_{x_val}"

                text = (
                    f"<b>🔮 MOMENTUM ACTIVE 🔮</b>\n\n"
                    f"<b>@{ch}</b> has delivered <b>{len(calls)}</b> calls above <b>{x_val}X</b> in the last 7 days.\n\n"
                    f"Consistent edge. Consistent results. Track the pattern."
                )
                momentum_emoji_ids = [MOMENTUM_ACTIVE_EMOJI_ID, MOMENTUM_ACTIVE_EMOJI_ID]
                # Append leaderboard/champions badge to momentum post
                m_badge = _get_kol_badge(ch)
                if m_badge:
                    if m_badge["type"] == "leaderboard":
                        m_rank_id = LEADERBOARD_PREMIUM_EMOJIS.get(m_badge["rank"], LEADERBOARD_PREMIUM_EMOJIS[1])
                        text += f'\n\n<a href="https://t.me/WizardScan/136">Leaderboard KOL</a> - Rank # 🔮'
                        momentum_emoji_ids = momentum_emoji_ids + [m_rank_id]
                    else:
                        m_rank_id = CHAMPIONS_PREMIUM_EMOJIS.get(m_badge["rank"], CHAMPIONS_PREMIUM_EMOJIS[1])
                        text += f'\n\n<a href="https://t.me/WizardScan/137">Champion KOL</a> - Rank # 🔮'
                        momentum_emoji_ids = momentum_emoji_ids + [m_rank_id]

                # KOL Signal buttons — 2 per row
                signal_buttons = []
                row = []
                snapshot_rows = []
                for _mi, (ck, _c_snap) in enumerate(calls):
                    # Try exact x_val post first, then any available post for this call
                    post_id = milestone_posts.get(ck, {}).get(str(x_val))
                    if not post_id:
                        # Fallback: find closest available milestone post for this call
                        _ck_posts = milestone_posts.get(ck, {})
                        if _ck_posts:
                            _valid_keys = [v for v in _ck_posts.keys() if v.lstrip('-').isdigit()]
                            if _valid_keys:
                                _sorted_x = sorted(_valid_keys, key=lambda v: abs(int(v) - x_val))
                                post_id = _ck_posts.get(_sorted_x[0])
                    btn_url = (f"https://t.me/WizardScan/{post_id}" if post_id
                               else f"https://t.me/WizardScan")
                    # Freeze this exact row for the X-Ray Report button
                    try:
                        _e_mc = float(_c_snap.get("entry_mc", 0) or 0)
                    except (TypeError, ValueError):
                        _e_mc = 0.0
                    snapshot_rows.append({
                        "symbol":  _c_snap.get("symbol", "TOKEN") or "TOKEN",
                        "chain":   _c_snap.get("chain", ""),
                        "entry":   _c_snap.get("entry_fmt", "?"),
                        "ms_mc":   fmt_mc(_e_mc * x_val) if _e_mc > 0 else "?",
                        "ca":      _c_snap.get("ca", ""),
                        "post_id": post_id,
                    })
                    if _mi < 20:   # Telegram keyboard stays readable: max 10 rows
                        row.append(InlineKeyboardButton("🔮 KOL Signal", url=btn_url))
                        if len(row) == 2:
                            signal_buttons.append(row); row = []
                if row:
                    signal_buttons.append(row)
                if snapshot_rows:
                    save_momentum_snapshot(ch, x_val, snapshot_rows)
                # X-Ray button last row
                signal_buttons.append([InlineKeyboardButton("🔮 X-Ray Report", url=xray_url)])
                kb = InlineKeyboardMarkup(signal_buttons)

                # Rotating momentum video — config-stored file_ids first, else VID_MOMENTUM_LIST
                _mcfg = load_config()
                momentum_idx = _mcfg.get("momentum_video_index", 0)
                _mom_vids = _mcfg.get("momentum_videos", [])
                if _mom_vids:
                    _mv = _mom_vids[momentum_idx % len(_mom_vids)]
                    cfg_set("momentum_video_index", (momentum_idx + 1) % len(_mom_vids))
                    vid_file_id = _mv.get("file_id"); vid_ftype = _mv.get("type", "video")
                    vid_path = None
                else:
                    vid_file_id = None; vid_ftype = None
                    vid_path = VID_MOMENTUM_LIST[momentum_idx % len(VID_MOMENTUM_LIST)]
                    cfg_set("momentum_video_index", (momentum_idx + 1) % len(VID_MOMENTUM_LIST))

                try:
                    posted_momentum = False
                    momentum_msg_id = None
                    if vid_file_id:
                        if userbot_client and momentum_emoji_ids:
                            try:
                                # Send emoji via userbot (no buttons — will add via bot edit below)
                                sent = await _userbot_send_media_with_emoji(
                                    context.bot, TARGET_CHANNEL, vid_file_id, vid_ftype,
                                    text, momentum_emoji_ids, None)
                                if sent:
                                    posted_momentum = True
                                    try: momentum_msg_id = sent.id
                                    except Exception: pass
                                    logger.info(f"✅ MOMENTUM ACTIVE (userbot+emoji+fileid) @{ch} {x_val}X")
                            except Exception as e_fid:
                                logger.warning(f"Momentum userbot file_id send failed: {e_fid}")
                        if not posted_momentum:
                            _sent_b = await context.bot.send_video(chat_id=TARGET_CHANNEL, video=vid_file_id,
                                caption=text, parse_mode="HTML", reply_markup=kb)
                            posted_momentum = True
                            try: momentum_msg_id = _sent_b.message_id
                            except Exception: pass
                    elif userbot_client and vid_path and os.path.exists(vid_path):
                        try:
                            from telethon.extensions.html import parse as tl_html_parse
                            plain_text, base_ents = tl_html_parse(text)
                            all_ents = _build_premium_entities(plain_text, base_ents, momentum_emoji_ids)
                            with open(vid_path, "rb") as vf_data:
                                vid_bytes = vf_data.read()
                            import tempfile as _tf, os as _os
                            tmp_m = _tf.NamedTemporaryFile(delete=False, suffix='.mp4')
                            tmp_m.write(vid_bytes); tmp_m.close()
                            # Send emoji via userbot (no buttons — will add via bot edit below)
                            _sent_ub = await userbot_client.send_file(
                                TARGET_CHANNEL, tmp_m.name,
                                caption=plain_text, formatting_entities=all_ents,
                                supports_streaming=True
                            )
                            try: _os.unlink(tmp_m.name)
                            except Exception: pass
                            try: momentum_msg_id = _sent_ub.id
                            except Exception: pass
                            posted_momentum = True
                            logger.info(f"✅ MOMENTUM ACTIVE (userbot+emoji) posted for @{ch} {x_val}X")
                        except Exception as e_m:
                            logger.warning(f"Momentum userbot send failed: {e_m}")
                    if not posted_momentum:
                        if vid_path and os.path.exists(vid_path):
                            with open(vid_path, "rb") as vf:
                                _sent_fb = await context.bot.send_video(
                                    chat_id=TARGET_CHANNEL, video=vf,
                                    caption=text, parse_mode="HTML",
                                    reply_markup=kb
                                )
                            try: momentum_msg_id = _sent_fb.message_id
                            except Exception: pass
                        else:
                            _sent_tb = await context.bot.send_message(
                                chat_id=TARGET_CHANNEL, text=text,
                                parse_mode="HTML", reply_markup=kb,
                                disable_web_page_preview=True
                            )
                            try: momentum_msg_id = _sent_tb.message_id
                            except Exception: pass
                    # Add buttons via bot edit (works reliably; userbot can't add inline buttons)
                    if momentum_msg_id:
                        try:
                            await context.bot.edit_message_reply_markup(
                                chat_id=TARGET_CHANNEL,
                                message_id=momentum_msg_id,
                                reply_markup=kb
                            )
                            logger.info(f"✅ Buttons added to momentum post {momentum_msg_id}")
                        except Exception as e_kb:
                            logger.warning(f"Momentum button edit failed: {e_kb}")
                    if ch not in momentum_sent: momentum_sent[ch] = {}
                    momentum_sent[ch][str(x_val)] = now.isoformat()
                    save_momentum_sent(momentum_sent)
                    logger.info(f"✅ MOMENTUM ACTIVE posted for @{ch} {x_val}X")
                except Exception as e:
                    logger.error(f"MOMENTUM post failed {ch} {x_val}x: {e}")
    except Exception as e:
        logger.error(f"momentum_check_job crash: {e}")

# ─── Owner only ───────────────────────────────────────────────────────────────
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

async def notify_owners(bot, text, reply_markup=None):
    """Send an owner notification to EVERY owner + admin.

    Previously only OWNER_ID was messaged and a single failure (blocked bot,
    HTML parse error, wrong id) silently swallowed the whole notification —
    which is why new KOL requests never arrived. Now: all owners + admins,
    HTML first with a plain-text retry, and every failure is logged.
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
        return False
    delivered = False
    for tid in targets:
        try:
            await bot.send_message(tid, text, parse_mode="HTML",
                                   reply_markup=reply_markup,
                                   disable_web_page_preview=True)
            delivered = True
        except Exception as e_html:
            try:
                plain = re.sub(r"<[^>]+>", "", text)
                await bot.send_message(tid, plain, reply_markup=reply_markup,
                                       disable_web_page_preview=True)
                delivered = True
            except Exception as e_plain:
                logger.error(f"notify_owners failed for {tid}: {e_html} / {e_plain}")
    return delivered


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def _show_xray_report(update: Update, channel: str, x_val: int):
    """DISABLED PERMANENTLY — X-Ray Report feature removed."""
    try:
        if getattr(update, "message", None):
            await update.message.reply_text("This feature is no longer available.")
    except Exception:
        pass
    return

    # ── Demo / test mode: return fake data for TestKOL so owner can verify UI ──
    if channel.lower() in ("testkol", "test_kol", "demokol"):
        DEMO_RESULTS = [
            {"symbol":"WIZTEST","chain":"SOL","entry":"$45K","ms_mc":"$450K","ca":"DemoSo1ABC123xyz456789","post_id":None},
            {"symbol":"MOONCAT","chain":"ETH","entry":"$80K","ms_mc":"$800K","ca":"0xDemoEth123abc456def789","post_id":None},
            {"symbol":"APEKING","chain":"BNB","entry":"$120K","ms_mc":"$1.2M","ca":"DemoBNB0xabc123","post_id":None},
        ]
        lines = [f"<b>{PE_CRYSTAL} X-Ray Report of @{html.escape(channel)}</b> <i>(Demo)</i>\n"]
        for r in DEMO_RESULTS:
            sym_safe   = html.escape(str(r.get("symbol","TOKEN")))
            chain_safe = html.escape(str(r.get("chain","")))
            entry_safe = html.escape(str(r.get("entry","?")))
            ms_mc_safe = html.escape(str(r.get("ms_mc","?")))
            lines.append(
                f"<b>{PE_CRYSTAL} ${sym_safe} ({chain_safe}) {x_val}X</b>\n"
                f"     {PE_WAND} View Post  |  {entry_safe} {PE_ARROW} {ms_mc_safe}"
            )
        XRAY_FOOTER = (
            f"\n\n\n{PE_CRYSTAL} Early calls. Real results. The track record keeps growing with every successful pick. "
            "Stay early, stay informed. DYOR • NFA"
        )
        caption = "\n\n".join(lines) + XRAY_FOOTER
        CAPTION_LIMIT = 1024
        xray_vids = load_config().get("xray_videos", [])
        if xray_vids:
            ctr = load_config().get("xray_video_counter", 0)
            vid = xray_vids[ctr % len(xray_vids)]
            vid_caption = caption if len(caption) <= CAPTION_LIMIT else caption[:CAPTION_LIMIT-3]+"..."
            try:
                await update.message.reply_video(video=vid["file_id"], caption=vid_caption, parse_mode="HTML")
                return
            except Exception: pass
        if len(caption) <= CAPTION_LIMIT and os.path.exists(VID_XRAY):
            try:
                with open(VID_XRAY, "rb") as vf:
                    await update.message.reply_video(video=vf, caption=caption, parse_mode="HTML")
                return
            except Exception: pass
        await update.message.reply_text(caption, parse_mode="HTML", disable_web_page_preview=True)
        return
    # ── End demo mode ──────────────────────────────────────────────────────────

    # ── Snapshot first: the exact rows frozen when the MOMENTUM ACTIVE post was
    #    made. Guarantees the X-Ray Report always matches that post (8 calls in
    #    the post → 8 records here), even after a /restorenow or data cleanup.
    results  = []
    seen_cas = set()

    def _xr_add(rows):
        for r in rows or []:
            r = dict(r)
            _ca = (r.get("ca") or "").lower()
            if _ca and _ca in seen_cas:
                continue
            if _ca:
                seen_cas.add(_ca)
            results.append(r)

    # 1) frozen snapshot from the MOMENTUM ACTIVE post (exact same calls)
    _xr_add(get_momentum_snapshot(channel, x_val))
    # 2) permanent archive of every milestone alert ever posted for this channel
    _xr_add(get_xray_archive_rows(channel, x_val))

    for call_key, call in tracked_calls.items():
        if call.get("channel","").lower() != channel.lower(): continue
        # PRIMARY: check sent_milestones (normal path)
        # SECONDARY: check milestone_posts — covers cases where sent_milestones was
        # lost after a /restorenow from an older backup, but WizardScan post IDs survived.
        in_milestones   = x_val in sent_milestones.get(call_key, set())
        post_id         = milestone_posts.get(call_key, {}).get(str(x_val))
        has_post        = bool(post_id)
        if not in_milestones and not has_post:
            continue
        ca = call.get("ca","")
        if ca and ca in seen_cas:
            continue
        if ca:
            seen_cas.add(ca)
        try:
            entry_mc = float(call.get("entry_mc", 0) or 0)
        except (TypeError, ValueError):
            entry_mc = 0.0
        # Compute milestone MC; if entry_mc unknown show "?" not entry price
        ms_mc_fmt = fmt_mc(entry_mc * x_val) if entry_mc > 0 else "?"
        results.append({
            "symbol":  call.get("symbol","TOKEN"),
            "chain":   call.get("chain",""),
            "entry":   call.get("entry_fmt","?"),
            "ms_mc":   ms_mc_fmt,
            "ca":      ca,
            "post_id": post_id,
        })

    # Tertiary fallback: if results still empty, include calls that have ANY recorded
    # milestone >= x_val OR have a milestone_posts entry >= x_val.
    # Covers partial data loss where sent_milestones was lost but WizardScan post IDs survived.
    if not results:
        for call_key, call in tracked_calls.items():
            if call.get("channel","").lower() != channel.lower(): continue
            ca = call.get("ca","")
            if ca and ca in seen_cas: continue
            # Check sent_milestones
            ms_set = sent_milestones.get(call_key, set())
            has_via_milestones = ms_set and max(ms_set) >= x_val
            # ALSO check milestone_posts — works even if sent_milestones is empty after restore
            _ck_posts = milestone_posts.get(call_key, {})
            _valid_posts = {int(k): v for k, v in _ck_posts.items()
                            if v and str(k).lstrip('-').isdigit()}
            has_via_posts = bool(_valid_posts) and max(_valid_posts.keys()) >= x_val
            if not has_via_milestones and not has_via_posts:
                continue
            # Call has a milestone at or above x_val — it definitely passed x_val
            if ca: seen_cas.add(ca)
            try:
                entry_mc = float(call.get("entry_mc", 0) or 0)
            except (TypeError, ValueError):
                entry_mc = 0.0
            ms_mc_fmt = fmt_mc(entry_mc * x_val) if entry_mc > 0 else "?"
            # Find nearest post_id from milestone_posts for this call
            post_id = _valid_posts.get(x_val)
            if not post_id and _valid_posts:
                _nearest = sorted(_valid_posts.items(), key=lambda kv: abs(kv[0] - x_val))
                post_id = _nearest[0][1]
            results.append({
                "symbol":  call.get("symbol","TOKEN"),
                "chain":   call.get("chain",""),
                "entry":   call.get("entry_fmt","?"),
                "ms_mc":   ms_mc_fmt,
                "ca":      ca,
                "post_id": post_id,
            })

    # Quaternary fallback: use last_ratio from any call of this channel that reached x_val
    # This covers cases where sent_milestones AND milestone_posts are both empty (e.g. after
    # a full data wipe) but the call's last_ratio shows it actually hit that level.
    if not results:
        for call_key, call in tracked_calls.items():
            if call.get("channel","").lower() != channel.lower(): continue
            ca = call.get("ca","")
            if ca and ca in seen_cas: continue
            last_r = call_peak_ratio(call)
            if last_r < x_val:
                continue
            if ca: seen_cas.add(ca)
            try:
                entry_mc = float(call.get("entry_mc", 0) or 0)
            except (TypeError, ValueError):
                entry_mc = 0.0
            ms_mc_fmt = fmt_mc(entry_mc * x_val) if entry_mc > 0 else "?"
            results.append({
                "symbol":  call.get("symbol","TOKEN") or "TOKEN",
                "chain":   call.get("chain",""),
                "entry":   call.get("entry_fmt","?"),
                "ms_mc":   ms_mc_fmt,
                "ca":      ca,
                "post_id": None,
            })

    if not results:
        await update.message.reply_text(
            f"<b>{PE_CRYSTAL} X-Ray Report of @{html.escape(channel)}</b>\n\n"
            f"No {x_val}X calls found for @{html.escape(channel)}.\n\n"
            f"<i>Milestone data may still be building. New {x_val}X calls will appear here automatically.</i>",
            parse_mode="HTML"
        )
        return

    lines = [f"<b>{PE_CRYSTAL} X-Ray Report of @{html.escape(channel)}</b>\n"]
    for r in results:
        view_link  = (f'<a href="https://t.me/WizardScan/{r["post_id"]}">View Post</a>'
                      if r["post_id"] else "View Post")
        ms_mc      = r.get("ms_mc") or r["entry"]
        sym_safe   = html.escape(str(r.get("symbol") or "TOKEN").upper())
        chain_safe = html.escape(str(r.get("chain") or ""))
        entry_safe = html.escape(str(r.get("entry") or "?"))
        ms_mc_safe = html.escape(str(ms_mc or "?"))
        lines.append(
            f"<b>{PE_CRYSTAL} ${sym_safe} ({chain_safe}) {x_val}X</b>\n"
            f"     {PE_WAND} {view_link}  |  {entry_safe} {PE_ARROW} {ms_mc_safe}"
        )
    # Footer added at the end of every X-Ray Report
    XRAY_FOOTER = (
        f"\n\n\n{PE_CRYSTAL} Early calls. Real results. The track record keeps growing with every successful pick. "
        "Stay early, stay informed. DYOR • NFA"
    )
    caption = "\n\n".join(lines) + XRAY_FOOTER
    # Telegram video caption limit = 1024 chars; text message limit = 4096
    CAPTION_LIMIT = 1024
    sent_with_media = False

    # 1) Always try owner-uploaded X-Ray rotating videos first (even if caption is long).
    #    Truncate caption to fit the video limit rather than skipping the video entirely.
    xray_vids = load_config().get("xray_videos", [])
    if xray_vids:
        ctr      = load_config().get("xray_video_counter", 0)
        vid      = xray_vids[ctr % len(xray_vids)]
        next_ctr = (ctr + 1) % len(xray_vids)
        cfg_set("xray_video_counter", next_ctr)
        vid_caption = caption if len(caption) <= CAPTION_LIMIT else caption[:CAPTION_LIMIT - 3] + "..."
        try:
            await update.message.reply_video(
                video=vid["file_id"], caption=vid_caption, parse_mode="HTML"
            )
            sent_with_media = True
        except Exception as e:
            logger.warning(f"xray_video send failed: {e}")

    # 2) Fall back to built-in file if caption fits and no custom video succeeded
    if not sent_with_media and len(caption) <= CAPTION_LIMIT and os.path.exists(VID_XRAY):
        try:
            with open(VID_XRAY, "rb") as vf:
                await update.message.reply_video(video=vf, caption=caption, parse_mode="HTML")
            sent_with_media = True
        except Exception as e:
            logger.warning(f"VID_XRAY send failed: {e}")

    if not sent_with_media:
        # No video available or all video attempts failed — send as text (4096 limit)
        await update.message.reply_text(
            caption, parse_mode="HTML", disable_web_page_preview=True
        )

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
        if False and arg.startswith("buybot_"):
            raw_cid = arg[len("buybot_"):]
            try:
                title = ""
                try:
                    chat_obj = await context.bot.get_chat(int(raw_cid))
                    title = chat_obj.title or ""
                except Exception:
                    title = ""
                await start_buybot_wizard(update, context, int(raw_cid), title)
            except Exception as e_bb:
                logger.error(f"buybot deep link: {e_bb}")
                await update.message.reply_text(
                    "⚠️ Couldn't start the Buy Bot setup. Send /buybot to try again.")
            return
        if arg.startswith("xray_"):
            # Safe parser: channel names may contain underscores (e.g. xray_some_kol_100)
            # Scan from the right to find the first all-digit segment → that is the x_val
            raw = arg[5:]  # strip "xray_"
            all_parts = raw.split("_")
            ch_name = ""
            x_str   = ""
            for split_at in range(len(all_parts) - 1, 0, -1):
                candidate_x  = all_parts[split_at]
                candidate_ch = "_".join(all_parts[:split_at])
                if candidate_x.isdigit() and candidate_ch:
                    ch_name, x_str = candidate_ch, candidate_x
                    break
            if ch_name and x_str:
                try:
                    x_val = int(float(x_str))  # handles both "10" and "10.0"
                    await _show_xray_report(update, ch_name, x_val)
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    logger.error(f"X-Ray deep link error: {e!r} | arg={arg!r}\n{tb}")
                    uid = update.effective_user.id if update.effective_user else 0
                    if uid == OWNER_ID:
                        # Owner: show actual error for debugging
                        await update.message.reply_text(
                            f"⚠️ <b>X-Ray error (owner debug):</b>\n"
                            f"<code>{html.escape(str(e))}</code>\n\n"
                            f"<code>{html.escape(tb[-800:])}</code>",
                            parse_mode="HTML"
                        )
                    else:
                        await update.message.reply_text(
                            f"⚠️ Could not load X-Ray report for @{html.escape(ch_name)}.\n\n"
                            f"Please try again in a moment.",
                            parse_mode="HTML"
                        )
            else:
                await update.message.reply_text(
                    "⚠️ Invalid X-Ray link. Please try again.",
                    parse_mode="HTML"
                )
            return  # Always return after xray_ deep link — never show welcome

    welcome = cfg_get("start_text", DEFAULT_START_TEXT)
    kb      = InlineKeyboardMarkup([[InlineKeyboardButton("🔮 Command 🔮", callback_data="command_menu")]])
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
    "start", "command", "submit", "subscribe", "linkme", "linkinfo", "history",
]

PUBLIC_TEXT_CMDS = ["submit", "subscribe", "linkme", "linkinfo", "history"]

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
        await refresh_channel_calls_live(channel)
        calls = get_call_history(channel)
        hist  = format_history(channel, calls)
        kb    = history_keyboard(channel)
        try:
            await update.message.reply_text(hist, parse_mode="HTML", reply_markup=kb,
                                            disable_web_page_preview=True)
        except Exception as e:
            # FIX: previously an unhandled send error here (e.g. message too long,
            # bad HTML) meant the owner saw nothing at all — looked like "no record".
            logger.error(f"/history @{channel} send failed ({len(calls)} calls): {e}")
            await update.message.reply_text(
                f"⚠️ Could not display the record for @{channel} (internal error). "
                f"This has been logged.")
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
        text = get_public_text("submit", btexts.get("kol_request", DEFAULT_KOL_REQUEST))
        kb   = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔮 Fast Track 🔮", callback_data="fast_track")],
            [InlineKeyboardButton("💬 Chat Us",       callback_data="chat_us")],
        ])
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
    ok_notify = await notify_owners(context.bot,
        f"{PE_CRYSTAL} <b>New KOL Tracking Request</b>\n\n"
        f"👤 From: @{html.escape(username)} (ID: <code>{user.id}</code>)\n"
        f"📡 Channel: <b>@{html.escape(channel)}</b>\n"
        f"🆔 Request: <code>{req_id}</code>\n"
        f"🕐 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"📋 Full list: /pendingkols",
        reply_markup=owner_kb)
    if not ok_notify:
        logger.error(f"KOL request {req_id} (@{channel}) could not be delivered to any owner")

    await update.message.reply_text(
        f"📨 <b>Request Submitted!</b>\n\n"
        f"Your request to track <b>@{channel}</b> has been sent to our team.\n\n"
        f"⏳ Review may take 1 day to 1 month depending on the queue.\n\n"
        f"🪄 Want faster approval? Use <b>Fast Track</b> — priority review.\n\nUse /command → Fast Track for details.",
        parse_mode="HTML")

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
        text = get_public_text("subscribe", DEFAULT_SUBSCRIBE_INFO)
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

# ─── Lookup ───────────────────────────────────────────────────────────────────
async def handle_lookup(update: Update, text: str):
    msg = update.message
    # Twitter/X lookup removed

    tg_match = TG_MENTION_RE.match(text.strip())
    if tg_match:
        channel  = tg_match.group(1)
        channels = [c.lower() for c in load_channels()]
        if channel.lower() in channels:
            await refresh_channel_calls_live(channel)
            calls = get_call_history(channel)
            hist  = format_history(channel, calls)
            kb    = history_keyboard(channel)
            try:
                await msg.reply_text(hist, parse_mode="HTML", reply_markup=kb,
                                     disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"lookup @{channel} send failed ({len(calls)} calls): {e}")
                await msg.reply_text(
                    f"⚠️ Could not display the record for @{channel} (internal error). "
                    f"This has been logged.")
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

    elif data == "kol_request":
        text = btexts.get("kol_request", DEFAULT_KOL_REQUEST)
        kb   = InlineKeyboardMarkup([[InlineKeyboardButton("🔮 Fast Track 🔮", callback_data="fast_track")]])
        bm   = cfg_get("button_media", {}).get("kol_request")
        sent = False
        if bm and bm.get("file_id"):
            try:
                fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                         caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(query.message, IMG_KOLREQUEST, text, reply_markup=kb)
            if not msg: await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "promo_hub":
        cap = btexts.get("promo_hub", DEFAULT_PROMO_HUB)
        bm  = cfg_get("button_media", {}).get("promo_hub")
        sent = False
        if bm and bm.get("file_id"):
            try:
                fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                         caption=cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(query.message, IMG_PROMO, cap, reply_markup=CONTACT_BUTTONS)
            if not msg: await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

    elif data == "tracked_kols":
        channels = load_channels()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔮 MAIN CHANNEL 🔮", url="https://t.me/WizardScan")]])
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

    elif data == "leaderboard":
        text = btexts.get("leaderboard", DEFAULT_LEADERBOARD)
        kb   = InlineKeyboardMarkup([
            [InlineKeyboardButton("View Leaderboard", url="https://t.me/WizardScan/136")],
            [InlineKeyboardButton("View Champions",   url="https://t.me/WizardScan/137")],
        ])
        bm   = cfg_get("button_media", {}).get("leaderboard")
        sent = False
        if bm and bm.get("file_id"):
            try:
                fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                         caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception: pass
        if not sent:
            msg  = await send_photo_safe(query.message, IMG_LEADERBOARD, text, reply_markup=kb)
            if not msg: await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "alert_rules":
        text = btexts.get("alert_rules", DEFAULT_ALERT_RULES)
        msg  = await send_photo_safe(query.message, IMG_ALERT, text)
        if not msg: await query.message.reply_text(text, parse_mode="HTML")

    elif data == "dm_alerts":
        # Same info/media as the /subscribe command (owner edits via
        # /settext subscribe and /setcommandmedia subscribe — single source
        # of truth, no separate config needed for this button).
        text = get_public_text("subscribe", DEFAULT_SUBSCRIBE_INFO)
        sent = await send_cmd_media(query.message, "subscribe", text)
        if not sent:
            await query.message.reply_text(text, parse_mode="HTML")

    elif data == "fast_track":
        cap = btexts.get("fast_track", DEFAULT_FAST_TRACK)
        bm  = cfg_get("button_media", {}).get("fast_track")
        sent = False
        if bm and bm.get("file_id"):
            try:
                fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                         caption=cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(query.message, IMG_FASTTRACK, cap, reply_markup=CONTACT_BUTTONS)
            if not msg: await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

    elif data == "chat_us":
        cap = btexts.get("chat_us", DEFAULT_CHAT_US)
        bm  = cfg_get("button_media", {}).get("chat_us")
        sent = False
        if bm and bm.get("file_id"):
            try:
                fn = query.message.reply_photo if bm.get("type") == "photo" else query.message.reply_video
                await fn(**{("photo" if bm.get("type")=="photo" else "video"): bm["file_id"]},
                         caption=cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_video_safe(query.message, VID_CHAT_US, cap, reply_markup=CONTACT_BUTTONS)
            if not msg: await query.message.reply_text(cap, parse_mode="HTML", reply_markup=CONTACT_BUTTONS)

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
        # Remove from pending so it doesn't show in /pendingkols anymore
        _pending_acc = load_pending()
        _to_del_acc = [k for k, v in _pending_acc.items()
                       if v.get("channel","").lower() == channel.lower() and str(v.get("user_id","")) == uid_str]
        for k in _to_del_acc: del _pending_acc[k]
        if _to_del_acc: save_pending(_pending_acc)
        try:
            await context.bot.send_message(uid,
                f"🎉 <b>Congratulations!</b>\n\n"
                f"Your KOL channel <b>@{channel}</b> has been successfully listed by Wizard Scan.\n\n"
                f"{PE_CRYSTAL} Our system will now monitor all calls from your channel 24/7.\n"
                f"{PE_CRYSTAL} Whenever a call hits a milestone, an alert will be posted in @WizardScan.\n\n"
                f"Welcome to the Wizard Scan family!", parse_mode="HTML")
        except Exception as e: logger.warning(f"DM to {uid} failed: {e}")
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        await query.message.reply_text(f"✅ <b>@{channel}</b> confirmed and added to tracking!", parse_mode="HTML")

    # ── KOL request reject — show confirm first (prevent accidental rejects) ──
    elif data.startswith("krej|"):
        _, uid_str, channel = data.split("|", 2)
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
            [InlineKeyboardButton("🔮 Fast Track 🔮",      callback_data="fast_track")],
            [InlineKeyboardButton("🔮  Chat With Us  🔮", callback_data="chat_us")],
        ])
        try:
            await context.bot.send_message(uid,
                f"📋 <b>Channel Review Update</b>\n\n"
                f"Your channel <b>@{channel}</b> has been rejected by our team.\n\n"
                f"Please try again. For priority review, contact our team for Fast Track.\n\n"
                f"Thank you.",
                parse_mode="HTML", reply_markup=fast_kb)
        except Exception as e: logger.warning(f"DM to {uid} failed: {e}")
        # Remove from pending so user can re-submit
        _pending = load_pending()
        _to_del = [k for k, v in _pending.items()
                   if v.get("channel","").lower() == channel.lower() and str(v.get("user_id","")) == uid_str]
        for k in _to_del: del _pending[k]
        if _to_del: save_pending(_pending)
        try: await query.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        await query.message.reply_text(f"❌ <b>@{channel}</b> request rejected.", parse_mode="HTML")

    elif data == "krej_cancel":
        try: await query.message.delete()
        except Exception: pass

    # ── History filter ────────────────────────────────────────────────────────
    elif data.startswith("h|"):
        parts = data.split("|")
        if len(parts) == 3:
            _, channel, filt = parts
            chain_map = {"bnb":"BNB","eth":"ETH","sol":"SOL","base":"BASE","rh":"RH","ton":"TON"}
            is_top = (filt == "top")
            await refresh_channel_calls_live(channel)
            if is_top:         calls = get_call_history(channel, top=True)
            elif filt in chain_map: calls = get_call_history(channel, chain_filter=chain_map[filt])
            else:              calls = get_call_history(channel)
            text = format_history(channel, calls, is_top=is_top)
            kb   = history_keyboard(channel)
            try:
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
        "6️⃣ <b>Trending se block</b> — koi token trending me nahi chahiye\n\n"
        "ℹ️ Har cheez buttons se hoti hai — <b>/cancel</b> se kabhi bhi rok sakte ho.",
        [("1️⃣ Skipped call add", "oh:fix:missed"),
         ("2️⃣ Late track MC fix", "oh:fix:latemc"),
         ("3️⃣ X alert nahi aayi", "oh:fix:fixx"),
         ("4️⃣ Call freeze", "oh:fix:freeze"),
         ("5️⃣ Call check", "oh:fix:check"),
         ("6️⃣ Trending block", "oh:fix:blocktrend")]),

    "posts": ("📣 Channel Post Media",
        "📣 <b>CHANNEL POSTS — MEDIA</b>\n"
        "(jo posts channel ke andar jaati hain)\n\n"
        "🚀 <b>X Alert Media (2X, 5X, 10X…)</b>\n"
        "/setmedia 2 — us X ka media set (media reply karo)\n"
        "/clearmedia 2 · /listmedia\n"
        "ℹ️ Jis X ka media set nahi, wo channel me post nahi hoti — "
        "lekin trending / champions / record me phir bhi count hoti hai.\n\n"
        "⚡ <b>Momentum Active Videos</b>\n"
        "/addmomentumvideo · /listmomentumvideos\n"
        "/removemomentumvideo N · /clearmomentumvideos\n\n"
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
        "🩻 <b>X-Ray Report Videos (1–10)</b>\n"
        "/addxrayvideo · /listxrayvideos\n"
        "/removexrayvideo N · /clearxrayvideos",
        [("➕ PinkSale Media", "oh:do:ps"), ("➕ CheesePad Media", "oh:do:cp"),
         ("📄 CP Details Media", "oh:do:cpd"), ("📄 PS Details Media", "oh:do:psd"),
         ("➕ Momentum Video", "oh:do:mom"), ("➕ Dropped Video", "oh:do:drop"),
         ("➕ X-Ray Video", "oh:do:xray")]),

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
         "/setcommandmedia subscribe · history · linkme · submit\n"
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
        "/refreshchampions — Champion KOLs (137)\n"
        "/refreshtrending2 — Trending (3560 + 3562)\n\n"
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
         ("🔄 Trending", "oh:tip:refreshtrending2"),
         ("📊 Check points", "oh:tip:checkpoints")]),

    "trend": ("📊 Trending & Buy Bots",
        "📊 <b>TRENDING MANAGEMENT</b>\n\n"
        "🔄 <b>Chain reset</b> (us chain ke tokens clear)\n"
        "/resetsoltrend · /resetethtrend · /resetbsctrend\n"
        "/resetbasetrend · /resettontrend · /resetrhtrend\n\n"
        "📌 <b>Pin token (24h, live MC)</b>\n"
        "/pintrending CHAIN CA [TG_LINK]\n"
        "Example: <code>/pintrending BSC 0xABC... https://t.me/MyToken</code>\n"
        "/unpintrending CHAIN · /listpinned\n"
        "Chains: SOL, ETH, BSC, BASE, TON, RH\n\n"
        "🚫 <b>Block</b>\n"
        "/blocktrending CA · /unblocktrending CA\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📚 Poori list: /ownerhelpT",
        [("🔄 Trending refresh", "oh:tip:refreshtrending2"),
         ("📌 Pinned list", "oh:tip:listpinned"),
         ("🤖 Buy bots", "oh:tip:listbuybots")]),

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
        "/removekolowner @ch · /listkolowners",
        [("📨 Pending KOLs", "oh:tip:pendingkols"),
         ("📡 My channels", "oh:tip:mychannels")]),

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

    "pages": ("📚 Baqi Help Pages",
        "📚 <b>HELP PAGES — TARTEEB SE</b>\n\n"
        "1️⃣ <b>/ownerhelp</b> — yehi buttons wala main panel (sab kuch yahan hai)\n"
        "2️⃣ <b>/ownerhelp2</b> — points reset, dropped-call posts, button media, "
        "X-Ray videos, extra alert links\n"
        "3️⃣ <b>/ownerhelpPS</b> — PinkSale · GemPad · CheesePad panel\n"
        "4️⃣ <b>/ownerhelpT</b> — Trending management + Buy Bots\n"
        "5️⃣ <b>/ownerhelpfull</b> — poori purani text list (2 parts)\n\n"
        "🆘 Koi masla ho to seedha <b>/fix</b> bhejo — buttons wala fix center khul jayega.",
        [("2️⃣ /ownerhelp2", "oh:tip:ownerhelp2"),
         ("3️⃣ /ownerhelpPS", "oh:tip:ownerhelpPS"),
         ("4️⃣ /ownerhelpT", "oh:tip:ownerhelpT"),
         ("5️⃣ /ownerhelpfull", "oh:tip:ownerhelpfull")]),
}

# Panel order — sabse pehle problem fix, phir baqi sab
_OH_ORDER = ["fix", "posts", "bot", "tpl", "public", "ranks", "trend", "ps", "kols",
             "links", "users", "pages"]
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
    "mom":  (ST_ADD_MOMENTUM_VID,  "⚡ Momentum video ab bhejo:"),
    "drop": (ST_ADD_DROPPED_VID,   "📉 Dropped-Call video ab bhejo:"),
    "xray": (ST_ADD_XRAY_VID,      "🩻 X-Ray video ab bhejo:"),
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
}

@owner_only
async def cmd_ownerhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(OH_HOME_TEXT, parse_mode="HTML", reply_markup=_oh_main_kb())


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
        if key == "blocktrend":
            await query.message.reply_text(
                "🚫 <b>Trending Block</b>\n\nToken ka CA is tarah bhejo:\n"
                "<code>/blocktrending CONTRACT_ADDRESS</code>\n\n"
                "Wapas allow karna ho: <code>/unblocktrending CA</code>",
                parse_mode="HTML")
            return
        flow = OH_FIX_FLOWS.get(key)
        if not flow: return
        wizard_state.pop(uid, None)
        await query.message.reply_text(flow[1] + "\n\n(/cancel se kabhi bhi rok sakte ho)")
        await _wiz_goto(query.message, uid, flow[0], "channel", {})
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
        "   Handle set na ho to button me x.com/WizardScan chala jata hai.\n\n"

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

        "🎬 <b>Momentum Videos</b>\n"
        "/addmomentumvideo | /listmomentumvideos\n"
        "/removemomentumvideo N | /clearmomentumvideos\n\n"

        "🔄 <b>Lists Force Refresh</b>\n"
        "/refreshleaderboard — Leaderboard (136)\n"
        "/refreshchampions — Champions (137)\n"
        "/refreshtrending2 — Trending (3560+3562)\n\n"

        "📌 Aage ke commands: /ownerhelp2\n"
        "🩷 PinkSale panel: /ownerhelpPS",

        parse_mode="HTML"
    )
    # Part 2 — sent immediately after
    await update.message.reply_text(
        f"{PE_CRYSTAL} <b>OWNER COMMANDS (2/2)</b>\n\n"

        "🚫 <b>Trending Blacklist</b>\n"
        "/blocktrending CA | /unblocktrending CA | /listblockedtrending\n\n"

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
        "📚 Baaki pages: /ownerhelp · /ownerhelp2 · /ownerhelpT\n"
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
    """Full pipeline diagnostic — shows exactly where tracking breaks."""
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

    # 2. Fetch posts from first channel
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
            "<i>Ab /listmomentumvideos, /listxrayvideos etc. se check kar sakte ho.</i>",
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

        "🌐 <b>Public Command Media (/submit /subscribe /linkme)</b>\n"
        "/setcommandmedia COMMAND — public command reply mein photo/video set karo (reply se)\n"
        "  Commands: <code>submit</code>, <code>subscribe</code>, <code>linkme</code>\n"
        "  Example: <code>/setcommandmedia subscribe</code> (phir video reply karo)\n"
        "/clearcommandmedia COMMAND — command ka media hata do\n\n"

        "🎬 <b>X-Ray Report Rotating Videos (1–10)</b>\n"
        "/addxrayvideo — video bhejo, X-Ray report mein rotate hogi\n"
        "/listxrayvideos — stored videos ki list\n"
        "/removexrayvideo N — video N hata do\n"
        "/clearxrayvideos — sab hata do (built-in par wapas)\n\n"

        "📜 <b>History Media</b>\n"
        "/sethistorymedia — /history reply mein photo/video set karo (reply karo)\n"
        "/clearhistorymedia — history media hata do\n\n"

        "🔗 <b>Extra Alert Links (custom links in every alert post)</b>\n"
        "/addpostlink EMOJI_ID TEXT URL — alert mein extra link add karo\n"
        "  Example: <code>/addpostlink 5368324170671202310 ALPHA https://t.me/mychannel</code>\n"
        "/removepostlink N — link N hata do\n"
        "/listpostlinks — current extra links dekho\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📚 <b>Help pages (tarteeb se):</b>\n"
        "1️⃣ /ownerhelp — main buttons panel\n"
        "2️⃣ /ownerhelp2 — yeh page\n"
        "3️⃣ /ownerhelpPS — PinkSale / GemPad / CheesePad\n"
        "4️⃣ /ownerhelpT — Trending + Buy Bots\n"
        "5️⃣ /ownerhelpfull — poori purani list\n\n"
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
async def cmd_addxrayvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start flow to add an X-Ray report video (up to 10 rotating)."""
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_XRAY_VID}
    vids = load_config().get("xray_videos", [])
    await update.message.reply_text(
        f"🎬 <b>Add X-Ray Video</b>\n\n"
        f"Stored X-Ray videos: <b>{len(vids)}/10</b>\n\n"
        f"Jo video bhejein ge woh X-Ray reports mein rotate hogi.\n"
        f"(/cancel se cancel karo)",
        parse_mode="HTML"
    )

@owner_only
async def cmd_listxrayvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all stored X-Ray report videos."""
    vids = load_config().get("xray_videos", [])
    if not vids:
        await update.message.reply_text(
            "📭 Koi custom X-Ray video nahi hai.\n\n"
            "Bot built-in VID_XRAY file use kar raha hai.\n"
            "Use /addxrayvideo to upload your own."
        ); return
    lines = [f"🎬 <b>X-Ray Videos ({len(vids)} total)</b>\n"]
    for i, v in enumerate(vids, 1):
        lines.append(f"<b>{i}.</b> {v.get('type','video')} — <code>{v.get('file_id','?')[:30]}...</code>")
    ctr = load_config().get("xray_video_counter", 0)
    lines.append(f"\n⏩ Next video: #{(ctr % len(vids)) + 1}")
    lines.append("Use /removexrayvideo N to remove.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_removexrayvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an X-Ray video by position."""
    if not context.args:
        await update.message.reply_text("Usage: /removexrayvideo <number>\nUse /listxrayvideos to see numbers."); return
    try: n = int(context.args[0])
    except ValueError: await update.message.reply_text("Please send a valid number."); return
    vids = load_config().get("xray_videos", [])
    if n < 1 or n > len(vids):
        await update.message.reply_text(f"❌ Invalid number. There are {len(vids)} videos."); return
    vids.pop(n - 1)
    cfg_set("xray_videos", vids)
    await update.message.reply_text(f"✅ X-Ray Video #{n} removed. {len(vids)} videos remaining.")

@owner_only
async def cmd_clearxrayvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all stored X-Ray videos (revert to built-in)."""
    cfg_set("xray_videos", [])
    cfg_set("xray_video_counter", 0)
    await update.message.reply_text("✅ Sab X-Ray videos hata diye.\n\nBot ab built-in VID_XRAY file use karega.")

@owner_only
async def cmd_testxray(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: directly test X-Ray Report for any channel + x_val.
    Usage: /testxray @channel 20
    Shows exactly what the X-Ray button would show."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: <code>/testxray @channel X</code>\n"
            "Example: <code>/testxray @klevercalls 20</code>\n\n"
            "Ye command X-Ray Report simulate karta hai bina button click ke.",
            parse_mode="HTML"
        ); return
    channel = context.args[0].lstrip("@")
    try:
        x_val = int(float(context.args[1]))
    except ValueError:
        await update.message.reply_text("❌ X value number hona chahiye. Example: /testxray @klevercalls 20")
        return

    # Debug info first
    ch_calls = [(ck, c) for ck, c in tracked_calls.items() if c.get("channel","").lower() == channel.lower()]
    if not ch_calls:
        await update.message.reply_text(
            f"⚠️ <b>@{channel}</b> ka koi call tracked nahi.\n\n"
            f"Total tracked calls: {len(tracked_calls)}\n"
            f"<i>Channel name check karo — exactly jaisa add kiya tha waisa likhna hoga.</i>",
            parse_mode="HTML"
        ); return

    ms_info = []
    for ck, c in ch_calls:
        ms_list = sorted(sent_milestones.get(ck, set()))
        posts   = milestone_posts.get(ck, {})
        ms_info.append(f"• <code>{ck}</code>\n  Milestones: {ms_list}\n  Posts: {dict(list(posts.items())[:3])}")

    debug_text = (
        f"🔍 <b>Debug: @{channel}</b>\n\n"
        f"Tracked calls: {len(ch_calls)}\n\n"
        + "\n\n".join(ms_info[:5])
        + f"\n\n⏳ Ab {x_val}X X-Ray report generate ho rahi hai..."
    )
    await update.message.reply_text(debug_text[:3000], parse_mode="HTML")

    # Now run the actual X-Ray report
    await _show_xray_report(update, channel, x_val)

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
    """Set a custom text+link for 2X-50X alert posts for 12 hours."""
    uid = update.effective_user.id
    owner_edit_state[uid] = {"state": ST_SETPROMOLINK}
    await update.message.reply_text(
        "🔗 <b>Set Promo Link (12 hours)</b>\n\n"
        "Send your promo text and link in two lines:\n\n"
        "<code>Your custom text here\nhttps://yourlink.com</code>\n\n"
        "Ye link har 2X–50X post ke <b>sabse neeche</b> (Champion / Leaderboard "
        "KOL line ke baad, ek khali line chhod kar) 12 ghante tak show hoga.\n\n"
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
async def cmd_addmomentumvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start flow to add a Momentum Active video."""
    owner_edit_state[update.effective_user.id] = {"state": ST_ADD_MOMENTUM_VID}
    vids = load_config().get("momentum_videos", [])
    await update.message.reply_text(
        f"🎬 <b>Add Momentum Video</b>\n\n"
        f"Current stored videos: <b>{len(vids)}</b>\n\n"
        f"Send the video you want to add to the rotation.\n"
        f"Send /cancel to abort.",
        parse_mode="HTML")

@owner_only
async def cmd_listmomentumvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all stored Momentum Active videos."""
    vids = load_config().get("momentum_videos", [])
    if not vids:
        await update.message.reply_text(
            "📭 No custom momentum videos stored.\n\n"
            "Bot is using the 5 built-in rotating videos.\n"
            "Use /addmomentumvideo to upload your own."); return
    lines = [f"🎬 <b>Momentum Videos ({len(vids)} total)</b>\n"]
    for i, v in enumerate(vids, 1):
        fid = v.get("file_id", "?")
        lines.append(f"<b>{i}.</b> {v.get('type','video')} — <code>{fid[:30]}...</code>")
    lines.append("\nUse /removemomentumvideo N to remove by number.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@owner_only
async def cmd_removemomentumvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a Momentum Active video by position."""
    if not context.args:
        await update.message.reply_text("Usage: /removemomentumvideo <number>\nUse /listmomentumvideos to see numbers."); return
    try: n = int(context.args[0])
    except ValueError: await update.message.reply_text("Please send a valid number."); return
    vids = load_config().get("momentum_videos", [])
    if n < 1 or n > len(vids):
        await update.message.reply_text(f"❌ Invalid number. There are {len(vids)} videos."); return
    vids.pop(n - 1)
    cfg_set("momentum_videos", vids)
    await update.message.reply_text(f"✅ Video #{n} removed. {len(vids)} videos remaining.")

@owner_only
async def cmd_clearmomentumvideos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all stored Momentum Active videos (revert to built-in)."""
    cfg_set("momentum_videos", [])
    await update.message.reply_text(
        "✅ All custom momentum videos cleared.\n\n"
        "Bot will now use the 5 built-in rotating videos.")

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
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan"),
         InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan"),
         InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 X-Ray Report", url=xray_url)],
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
async def cmd_testmomentum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post N rotating Momentum Active videos to TARGET_CHANNEL. Usage: /testmomentum [count]"""
    args = context.args
    count = 20
    if args:
        try: count = max(1, min(int(args[0]), 30))
        except ValueError: pass

    bot_username = (await context.bot.get_me()).username
    await update.message.reply_text(f"⏳ {count} Momentum Active posts channel pe bhej raha hoon...")

    for i in range(1, count + 1):
        xray_url = f"https://t.me/{bot_username}?start=xray_TestKOL_10"
        text = (
            f"<b>🔮 MOMENTUM ACTIVE 🔮</b>\n\n"
            f"<b>@TestKOL</b> has delivered <b>5</b> calls above <b>10X</b> in the last 7 days.\n\n"
            f"Consistent edge. Consistent results. Track the pattern."
        )
        test_momentum_emoji_ids = [MOMENTUM_ACTIVE_EMOJI_ID, MOMENTUM_ACTIVE_EMOJI_ID]
        # 5 KOL Signal buttons (2 per row) + X-Ray button
        signal_rows = []
        row = []
        for j in range(1, 6):
            row.append(InlineKeyboardButton(
                "🔮 KOL Signal", url=f"https://t.me/WizardScan"
            ))
            if len(row) == 2:
                signal_rows.append(row); row = []
        if row:
            signal_rows.append(row)
        signal_rows.append([InlineKeyboardButton("🔮 X-Ray Report", url=xray_url)])
        kb = InlineKeyboardMarkup(signal_rows)

        _tcfg = load_config()
        momentum_idx = _tcfg.get("momentum_video_index", 0)
        _tmom_vids = _tcfg.get("momentum_videos", [])
        if _tmom_vids:
            _tmv = _tmom_vids[momentum_idx % len(_tmom_vids)]
            cfg_set("momentum_video_index", (momentum_idx + 1) % len(_tmom_vids))
            vid_file_id_t = _tmv.get("file_id"); vid_ftype_t = _tmv.get("type", "video")
            vid_path = None
        else:
            vid_file_id_t = None; vid_ftype_t = None
            vid_path = VID_MOMENTUM_LIST[momentum_idx % len(VID_MOMENTUM_LIST)]
            cfg_set("momentum_video_index", (momentum_idx + 1) % len(VID_MOMENTUM_LIST))

        try:
            posted_test = False
            if vid_file_id_t:
                if userbot_client and test_momentum_emoji_ids:
                    try:
                        sent_t = await _userbot_send_media_with_emoji(
                            context.bot, TARGET_CHANNEL, vid_file_id_t, vid_ftype_t,
                            text, test_momentum_emoji_ids, kb)
                        if sent_t: posted_test = True
                    except Exception as e_fid_t:
                        logger.warning(f"testmomentum userbot file_id failed: {e_fid_t}")
                if not posted_test:
                    await context.bot.send_video(chat_id=TARGET_CHANNEL, video=vid_file_id_t,
                        caption=text, parse_mode="HTML", reply_markup=kb)
                    posted_test = True
            elif userbot_client and vid_path and os.path.exists(vid_path):
                try:
                    from telethon.extensions.html import parse as tl_html_parse
                    from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonUrl
                    plain_test, base_test = tl_html_parse(text)
                    all_ents_t = _build_premium_entities(plain_test, base_test, test_momentum_emoji_ids)
                    tl_rows_t = []
                    for row_btns_t in kb.inline_keyboard:
                        tl_row_btns_t = [KeyboardButtonUrl(text=b.text, url=b.url) for b in row_btns_t if b.url]
                        if tl_row_btns_t:
                            tl_rows_t.append(KeyboardButtonRow(buttons=tl_row_btns_t))
                    tl_kb_t = ReplyInlineMarkup(rows=tl_rows_t) if tl_rows_t else None
                    with open(vid_path, "rb") as vf_t:
                        vid_bytes_t = vf_t.read()
                    import tempfile as _tft, os as _ost
                    tmp_t = _tft.NamedTemporaryFile(delete=False, suffix='.mp4')
                    tmp_t.write(vid_bytes_t); tmp_t.close()
                    await userbot_client.send_file(
                        TARGET_CHANNEL, tmp_t.name,
                        caption=plain_test, formatting_entities=all_ents_t,
                        buttons=tl_kb_t, supports_streaming=True
                    )
                    try: _ost.unlink(tmp_t.name)
                    except Exception: pass
                    posted_test = True
                except Exception as e_t:
                    logger.warning(f"testmomentum userbot send failed: {e_t}")
            if not posted_test:
                if vid_path and os.path.exists(vid_path):
                    with open(vid_path, "rb") as vf:
                        await context.bot.send_video(
                            chat_id=TARGET_CHANNEL, video=vf,
                            caption=text, parse_mode="HTML", reply_markup=kb
                        )
                else:
                    await context.bot.send_message(
                        chat_id=TARGET_CHANNEL, text=text,
                        parse_mode="HTML", reply_markup=kb,
                        disable_web_page_preview=True
                    )
            logger.info(f"testmomentum post {i}/{count} — vid_idx={momentum_idx}")
        except Exception as e:
            await update.message.reply_text(f"❌ Post {i} fail: {e}")
            return
        await asyncio.sleep(1)

    await update.message.reply_text(f"✅ {count} posts channel pe bhej diye! Sab 5 videos rotate hoi hain.")

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


@owner_only
async def cmd_freezecall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Freeze a tracked call — stop all future milestone alerts for it.
    Usage: /freezecall CA
    Optionally: /freezecall @channel CA  (freeze only that channel's call)"""
    if not context.args:
        await update.message.reply_text(
            "⛔ <b>Call Freeze</b>\n\n"
            "Usage:\n"
            "<code>/freezecall CA</code>\n"
            "<code>/freezecall @channel CA</code>\n\n"
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

    if not frozen_keys:
        await update.message.reply_text(
            f"❌ CA <code>{ca}</code> abhi tracked calls mein nahi mila.\n"
            "Pehle /addmissedcall se add karo, ya CA check karo.",
            parse_mode="HTML"); return

    _save_tracked()
    lines = "\n".join(f"  • {k}" for k in frozen_keys)
    await update.message.reply_text(
        f"⛔ <b>Call Frozen!</b>\n\n{lines}\n\n"
        f"Ab koi milestone alert nahi jayega is call ka.\n"
        f"Unfreeze karne ke liye: <code>/unfreezecall {ca}</code>",
        parse_mode="HTML")


@owner_only
async def cmd_unfreezecall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unfreeze a previously frozen call — resume milestone alerts.
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

    if not unfrozen_keys:
        await update.message.reply_text(
            f"❌ <code>{ca}</code> ya toh frozen nahi tha, ya tracked nahi mila.",
            parse_mode="HTML"); return

    _save_tracked()
    lines = "\n".join(f"  • {k}" for k in unfrozen_keys)
    await update.message.reply_text(
        f"✅ <b>Call Unfrozen!</b>\n\n{lines}\n\n"
        f"Ab is call ke milestone alerts phir se jayenge.",
        parse_mode="HTML")


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
    if len(context.args) < 7:
        await start_missedcall_wizard(update, context); return
    channel = context.args[0].lstrip("@").strip()
    ca = context.args[1].strip()
    _x_raw = context.args[2].strip().lower().rstrip("x")
    auto_x = _x_raw in ("auto", "a", "0", "")
    x_achieved = 0
    if not auto_x:
        try:
            x_achieved = int(float(_x_raw))
        except ValueError:
            await update.message.reply_text(
                "⚠️ x_achieved number ya <code>auto</code> hona chahiye. Example: 100 ya auto",
                parse_mode="HTML"); return
        if x_achieved < 2:
            await update.message.reply_text(
                "⚠️ x_achieved kam se kam 2 hona chahiye (ya <code>auto</code> likhein).",
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
            entry_mc = cur_mc_val / max(x_achieved, 1)
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
                    else (dex.get("price", 0) / max(x_achieved, 1) if dex.get("price") else 0)
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
        cur_mc_val = entry_mc * max(x_achieved, 1)
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

    auto_note = ""
    try:
        true_ratio, peak_mc_val = await compute_true_x(call_ref, dex)
    except Exception as _e_tx:
        logger.error(f"addmissedcall compute_true_x: {_e_tx}")
        true_ratio, peak_mc_val = 0.0, 0.0

    if true_ratio > 0:
        call_ref["last_ratio"] = round(true_ratio, 4)
        _update_peak(call_ref, true_ratio, peak_mc_val)
        _save_tracked()
        hit = milestones_for_ratio(true_ratio)
        if hit:
            auto_x_val = max(hit)
            if auto_x_val != x_achieved:
                auto_note = (f"\n\n🤖 <b>Auto-X:</b> entry {fmt_mc(entry_mc)} → peak "
                             f"{fmt_mc(peak_mc_val) if peak_mc_val > 0 else cur_fmt} = "
                             f"<b>{fmt_x(true_ratio)}</b> (milestone {auto_x_val}X)"
                             + (f"\n<i>Aap ne {x_achieved}X likha tha — bot ne asli X use kiya.</i>"
                                if x_achieved else ""))
            x_achieved = auto_x_val
            if peak_mc_val > 0:
                cur_mc_val, cur_fmt = peak_mc_val, fmt_mc(peak_mc_val)
        elif auto_x:
            await msg.edit_text(
                f"⚠️ Entry {fmt_mc(entry_mc)} se abhi tak koi X milestone hit nahi hua "
                f"(current ratio: {fmt_x(true_ratio)}).\n\n"
                f"Call track ho gayi hai — X aane par bot khud alert karega.",
                parse_mode="HTML")
            return
    elif auto_x:
        await msg.edit_text(
            "⚠️ Live data nahi mila, is liye auto-X calculate nahi ho saka.\n"
            "X manually dein: <code>/addmissedcall @ch CA 4 453.2K</code>",
            parse_mode="HTML")
        return

    # Mark all milestones up to x_achieved as hit
    milestones_to_mark = [m for m in get_milestones() if m <= x_achieved]
    newly_marked = []
    for ms in milestones_to_mark:
        if ms not in sent_milestones[call_key]:
            sent_milestones[call_key].add(ms)
            newly_marked.append(ms)
            await award_points_for_milestone(channel, call_key, ms)
    _save_milestones()

    # Post alert for the x_achieved milestone to main channel
    call_data = tracked_calls[call_key]
    # Also send the initial "Dropped a Call" post to WizardScan channel
    asyncio.create_task(send_dropped_alert(
        context.bot, channel, call_data.get("msg_id", 0), ca,
        call_data.get("chain", chain_val), call_data.get("entry_fmt", "N/A"),
        call_data.get("symbol", "UNKNOWN")
    ))
    # Media rule: agar us X level ki media set nahi hai to channel post nahi
    # hoga — milestone sirf record hoga aur media add hone par auto-post ho jayega.
    asyncio.create_task(send_alert(
        context.bot, channel, call_data.get("msg_id", 0), x_achieved,
        call_data.get("chain", chain_val), call_data.get("entry_fmt", "N/A"),
        cur_fmt, ca, call_data.get("symbol", "UNKNOWN")
    ))


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
        f"Entry MC: {entry_fmt} → Current: {cur_fmt}\n"
        f"X Achieved: <b>{x_achieved}X</b>{auto_note}\n"
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
            await update.message.reply_text(
                f"⚠️ Yeh call tracked nahi hai: @{channel} / {ca[:16]}...\n\n"
                "Pehle /addmissedcall se add karo.", parse_mode="HTML"); return

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
        for ms in missing:
            sent_milestones[call_key].add(ms)
            try:
                await award_points_for_milestone(call.get("channel", channel), call_key, ms)
            except Exception as e_p:
                logger.error(f"recheckx points: {e_p}")
        top_ms = missing[-1]
        entry_mc = float(call.get("entry_mc", 0) or 0)
        ms_mc = peak_mc if peak_mc > 0 else (entry_mc * top_ms if entry_mc > 0 else 0)
        try:
            await send_alert(
                context.bot, call.get("channel", channel), call.get("msg_id", 0), top_ms,
                call.get("chain", "SOL"), call.get("entry_fmt", "N/A"),
                fmt_mc(ms_mc) if ms_mc > 0 else "N/A", ca, call.get("symbol", "UNKNOWN"))
            posted_total += 1
            lines.append(f"• {call.get('symbol','?')} — {fmt_x(ratio)} → 📣 {top_ms}X posted")
        except Exception as e_a:
            logger.error(f"recheckx alert: {e_a}")
            lines.append(f"• {call.get('symbol','?')} — alert fail: {type(e_a).__name__}")
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
    if len(context.args) < 3:
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
    if new_mc <= 0:
        await update.message.reply_text("⚠️ Valid entry MC dain. Example: 100K, 1M, 500000"); return

    call_key = f"{channel}_{ca}"
    if call_key not in tracked_calls:
        found = next((k for k in tracked_calls if k.lower() == call_key.lower()), None)
        if not found:
            await update.message.reply_text(
                f"⚠️ Yeh call tracked nahi hai: @{channel} / {ca[:16]}…\n\n"
                "Pehle <code>/addmissedcall</code> se add karein.", parse_mode="HTML"); return
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

    # Recompute the ratio from the corrected entry and reset the peak so that
    # tracking continues cleanly from here.
    ratio = 0.0
    if cur_mc > 0:
        ratio = cur_mc / new_mc
    elif call.get("last_ratio"):
        ratio = float(call["last_ratio"])
    call.pop("peak_ratio", None); call.pop("peak_mc", None); call.pop("peak_mc_fmt", None)
    if ratio > 0:
        call["last_ratio"] = round(ratio, 4)
        _update_peak(call, ratio, cur_mc)

    # Milestones already passed at the corrected entry are recorded SILENTLY
    # (no spam), future ones alert normally in real time.
    silent = []
    if ratio > 0:
        for ms in get_milestones():
            if ms <= MAX_MILESTONE and ratio >= ms and ms not in sent_milestones[call_key]:
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

    await update.message.reply_text(
        f"✅ <b>Late Call Fixed!</b>\n\n"
        f"📡 Channel: @{call.get('channel', channel)}\n"
        f"🪙 Symbol: {call.get('symbol','UNKNOWN')}\n"
        f"🔗 CA: <code>{call.get('ca', ca)}</code>\n\n"
        f"Old Entry: <b>{old_fmt}</b>\n"
        f"New Entry: <b>{call['entry_fmt']}</b>\n"
        f"Live MC: <b>{fmt_mc(cur_mc) if cur_mc > 0 else 'N/A'}</b>\n"
        f"Ab ka X: <b>{fmt_x(ratio) if ratio > 0 else 'N/A'}</b>\n"
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
      • Post 137 (Champions)   — KOL name ke saath 𝕏 icon
    Viewers can click to go directly to the KOL's X profile."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "📋 <b>X Handle Set Karne Ka Tarika:</b>\n\n"
            "<code>/addx @channel xhandle</code>\n\n"
            "Example:\n<code>/addx @SomeKOL WizardScan</code>\n\n"
            "Handle @ ke bina likhain (e.g. WizardScan, not @WizardScan)\n\n"
            "━━━━━━━━━━━━━━━\n"
            "💡 <b>Yeh kya karta hai?</b>\n\n"
            "Jab yeh set hota hai toh post 136 (Leaderboard) aur post 137 (Champions) mein "
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
        f"• Post 137 (Champions) mein bhi <b>𝕏</b> link aayega\n"
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
    now = datetime.utcnow()
    cfg_set("leaderboard_reset_since", now.isoformat())  # ← real score reset
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
    await update.message.reply_text("⏳ Updating champions post (137) with premium emojis...")
    ok = await _update_champions_with_premium_emojis(context.bot)
    if ok:
        cfg_set("last_champions_update", datetime.utcnow().isoformat())
        await update.message.reply_text("✅ Champions (post 137) updated with premium emojis!")
    else:
        await update.message.reply_text("⚠️ Could not edit post 137. Check bot admin permissions in @WizardScan.")

@owner_only
async def cmd_refreshchampions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full champions reset + refresh post 137.
    - Saare channel points zero ho jaate hain
    - Purane tracked call keys exclude ho jaate hain (old X records nahi dikhenge)
    - Post 137 seedha fresh (empty) list se update hoti hai
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

    msg = await update.message.reply_text("⏳ Champions reset ho rahi hai — points zero, purane records exclude, post 137 update ho raha hai...")
    try:
        ok = await _update_champions_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_champions_update", now.isoformat())
            await msg.edit_text(
                "✅ <b>Champions (post 137) reset + refresh!</b>\n\n"
                "• Sare points zero ho gaye\n"
                "• Purane records (1000x etc.) ab nahi dikhenge\n"
                "• Post 137 fresh ho gayi — ab se sirf naye calls count honge 🏆",
                parse_mode="HTML")
        else:
            await msg.edit_text(
                "⚠️ Points reset hue lekin post 137 edit nahi hua.\n"
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
async def cmd_refreshtrending2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh new trending posts 3560 + 3562 with DexScreener data."""
    save_trending2_cache({})  # clear cache so full fresh list is fetched
    msg = await update.message.reply_text("⏳ DexScreener/GeckoTerminal se naya data fetch ho raha hai (SOL/ETH/BSC/RH/BASE/TON)...")
    try:
        chain_tokens2 = await fetch_trending2()
        save_trending2_cache(chain_tokens2)  # store for next price-only cycle
        total = sum(len(v) for v in chain_tokens2.values())
        breakdown = " | ".join(f"{c}:{len(chain_tokens2.get(c,[]))}" for c in ["SOL","ETH","BSC","RH","BASE","TON"])
        results = await _update_trending2_posts(context.bot, chain_tokens2=chain_tokens2)
        ok1 = results.get(POST_TRENDING_1, False)
        ok2 = results.get(POST_TRENDING_2, False)
        cfg_set("last_trending2_update", datetime.utcnow().isoformat())
        await msg.edit_text(
            f"{'✅' if ok1 else '❌'} Post {POST_TRENDING_1} (SOL/ETH/BSC): {'Updated' if ok1 else 'Failed'}\n"
            f"{'✅' if ok2 else '❌'} Post {POST_TRENDING_2} (RH/BASE/TON): {'Updated' if ok2 else 'Failed'}\n\n"
            f"📊 {breakdown}\nTotal: {total} tokens."
        )
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: {e}")

@owner_only
async def cmd_ownerhelpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner help for trending management commands."""
    await update.message.reply_text(
        "📊 <b>Trending Management Commands</b>\n\n"
        "🔄 <b>Chain Reset (clears that chain's tokens, shows new data in seconds)</b>\n"
        "/resetsoltrend — SOL trending reset\n"
        "/resetethtrend — ETH trending reset\n"
        "/resetbsctrend — BSC trending reset\n"
        "/resetbasetrend — BASE trending reset\n"
        "/resettontrend — TON trending reset\n"
        "/resetrhtrend — Robinhood trending reset\n\n"
        "ℹ️ <b>Note:</b> Sirf selected chain ka data reset hota hai. Baki chains unchanged.\n\n"
        "📌 <b>Pin Token to Trending (24 hours, live MC)</b>\n"
        "/pintrending CHAIN CA [TG_LINK] — Pin a token to #1 position\n"
        "Example: /pintrending BSC 0xABCDef... https://t.me/MyToken\n\n"
        "📌 <b>Remove Pin</b>\n"
        "/unpintrending CHAIN — Remove pinned token from a chain\n"
        "/listpinned — See all currently pinned tokens\n\n"
        "Chains: SOL, ETH, BSC, BASE, TON, RH\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🆘 <b>Calls ka koi masla?</b>\n"
        "<b>/fix</b> — ek hi panel: skipped call · late track MC · X alert missing · "
        "freeze · call check · trending block. Bot ek ek sawal poochega.\n\n"
        "📚 <b>Help pages:</b> /ownerhelp · /ownerhelp2 · /ownerhelpPS · /ownerhelpT\n",
        parse_mode="HTML"
    )


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
async def cmd_resetsoltrend(update, context):
    await _do_chain_trend_reset(update, context, "SOL")

@owner_only
async def cmd_resetethtrend(update, context):
    await _do_chain_trend_reset(update, context, "ETH")

@owner_only
async def cmd_resetbsctrend(update, context):
    await _do_chain_trend_reset(update, context, "BSC")

@owner_only
async def cmd_resetbasetrend(update, context):
    await _do_chain_trend_reset(update, context, "BASE")

@owner_only
async def cmd_resettontrend(update, context):
    await _do_chain_trend_reset(update, context, "TON")

@owner_only
async def cmd_resetrhtrend(update, context):
    await _do_chain_trend_reset(update, context, "RH")


@owner_only
async def cmd_pintrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pin a token CA to position #1 in a chain's trending for 24 hours with live MC updates.
    Usage: /pintrending CHAIN CA [TG_LINK]
    Example: /pintrending BSC 0xABCD... https://t.me/TokenGroup
    """
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: <code>/pintrending CHAIN CA [TG_LINK]</code>\n\n"
            "Examples:\n"
            "<code>/pintrending BSC 0xABCDef1234...40hex</code>\n"
            "<code>/pintrending SOL AbCDefg...SolanaCA https://t.me/TokenGroup</code>\n\n"
            "Chains: SOL, ETH, BSC, BASE, TON, RH\n"
            "Pin lasts 24 hours. MC auto-updates every 2 minutes.",
            parse_mode="HTML"
        ); return

    chain_key = context.args[0].strip().upper()
    ca        = context.args[1].strip()
    tg_link   = context.args[2].strip() if len(context.args) > 2 else ""
    valid_chains = {"SOL", "ETH", "BSC", "BASE", "TON", "RH", "BNB"}
    if chain_key not in valid_chains:
        await update.message.reply_text(
            f"❌ Unknown chain: <code>{chain_key}</code>\nValid: SOL, ETH, BSC, BASE, TON, RH",
            parse_mode="HTML"); return
    # Normalise BSC/BNB
    if chain_key == "BNB": chain_key = "BSC"

    msg = await update.message.reply_text(f"⏳ {chain_key} ke liye CA <code>{ca}</code> pin ho raha hai...", parse_mode="HTML")
    # Fetch initial data
    sym    = "PIN"
    mc_fmt = "N/A"
    dex_path = {"SOL":"solana","ETH":"ethereum","BSC":"bsc","BASE":"base","TON":"ton","RH":"robinhood"}.get(chain_key,"ethereum")
    dex_url  = f"https://dexscreener.com/{dex_path}/{ca}"
    try:
        r = await asyncio.to_thread(
            _dex_session.get, f"https://api.dexscreener.com/latest/dex/tokens/{ca}", timeout=12
        )
        if r.status_code == 200:
            pairs = r.json().get("pairs") or []
            if pairs:
                best   = max(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0))
                mc_raw = float(best.get("marketCap") or best.get("fdv") or 0)
                sym    = best.get("baseToken",{}).get("symbol", "PIN")
                mc_fmt = fmt_mc(mc_raw) if mc_raw > 0 else "N/A"
                dex_url = f"https://dexscreener.com/{best.get('chainId','ethereum').lower()}/{ca}"
    except Exception as e:
        logger.warning(f"pintrending initial fetch failed: {e}")

    pins = load_pinned_trending()
    pins[chain_key] = {
        "ca":        ca,
        "tg_link":   tg_link,
        "symbol":    sym,
        "mc_fmt":    mc_fmt,
        "dex_url":   dex_url,
        "pinned_at": datetime.utcnow().isoformat(),
    }
    save_pinned_trending(pins)

    # Immediately refresh the trending posts so pin shows up
    try:
        if chain_key in ("SOL","ETH","BSC","BASE"):
            await _update_trending_with_premium_emojis(context.bot)
        await _update_trending2_posts(context.bot)
    except Exception: pass

    await msg.edit_text(
        f"📌 <b>Pinned!</b>\n\n"
        f"Chain: <b>{chain_key}</b>\n"
        f"CA: <code>{ca}</code>\n"
        f"Symbol: ${sym}\n"
        f"MC: {mc_fmt}\n"
        f"TG: {tg_link or '—'}\n\n"
        f"✅ Token ab #1 position par hai (24 hours).\n"
        f"MC har 2 minute mein auto-update hogi.",
        parse_mode="HTML"
    )


@owner_only
async def cmd_unpintrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove pinned token from a chain.  Usage: /unpintrending CHAIN"""
    if not context.args:
        await update.message.reply_text("Usage: <code>/unpintrending CHAIN</code>\nExample: /unpintrending BSC", parse_mode="HTML"); return
    chain_key = context.args[0].strip().upper()
    if chain_key == "BNB": chain_key = "BSC"
    pins = load_pinned_trending()
    if chain_key not in pins:
        await update.message.reply_text(f"ℹ️ {chain_key} mein koi pinned token nahi hai."); return
    del pins[chain_key]
    save_pinned_trending(pins)
    await update.message.reply_text(f"✅ {chain_key} ka pinned token hata diya gaya.")


@owner_only
async def cmd_listpinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all currently pinned trending tokens."""
    pins = load_pinned_trending()
    now  = datetime.utcnow()
    if not pins:
        await update.message.reply_text("📌 Koi pinned token nahi hai."); return
    lines = ["📌 <b>Pinned Trending Tokens</b>\n"]
    for chain_key, pin in pins.items():
        try:
            pinned_at = datetime.fromisoformat(pin.get("pinned_at",""))
            expires   = pinned_at + timedelta(hours=24)
            remaining = max(timedelta(0), expires - now)
            rem_str   = f"{int(remaining.total_seconds()//3600)}h {int((remaining.total_seconds()%3600)//60)}m"
        except Exception:
            rem_str = "?"
        lines.append(
            f"<b>{chain_key}:</b> ${pin.get('symbol','?')} — {pin.get('mc_fmt','N/A')}\n"
            f"   CA: <code>{pin.get('ca','?')}</code>\n"
            f"   ⏱ Expires in: {rem_str}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


@owner_only
async def cmd_blocktrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block a token CA from ever appearing in trending."""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/blocktrending TOKEN_CA</code>\n\nExample: <code>/blocktrending So1ABC123xyz</code>",
            parse_mode="HTML"); return
    ca = context.args[0].strip().lower()
    bl = load_trending_blacklist()
    bl.add(ca)
    save_trending_blacklist(bl)
    await update.message.reply_text(
        f"🚫 <b>Blocked from Trending</b>\n\n<code>{ca}</code>\n\nTotal blocked: {len(bl)}",
        parse_mode="HTML")

@owner_only
async def cmd_unblocktrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a CA from the trending blacklist."""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/unblocktrending TOKEN_CA</code>", parse_mode="HTML"); return
    ca = context.args[0].strip().lower()
    bl = load_trending_blacklist()
    if ca in bl:
        bl.discard(ca)
        save_trending_blacklist(bl)
        await update.message.reply_text(f"✅ <b>Unblocked:</b> <code>{ca}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ <code>{ca}</code> blacklist mein nahi tha.", parse_mode="HTML")

@owner_only
async def cmd_listblockedtrending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all blocked trending CAs."""
    bl = load_trending_blacklist()
    if not bl:
        await update.message.reply_text("✅ Koi CA block nahi hai. Trending clean hai."); return
    lines = "\n".join(f"• <code>{ca}</code>" for ca in sorted(bl))
    await update.message.reply_text(
        f"🚫 <b>Blocked Trending CAs ({len(bl)})</b>\n\n{lines}",
        parse_mode="HTML")

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
async def cmd_resetleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually reset leaderboard (and trending KOLs list) and immediately update post 136."""
    now = datetime.utcnow()
    c = load_config()
    c["last_leaderboard_update"] = ""
    c["leaderboard_reset_date"] = now.isoformat()
    c["leaderboard_reset_since"] = now.isoformat()
    # Also reset trending KOLs so old high-X records don't bleed back in
    c["trending_kols_reset_since"] = now.isoformat()
    # Snapshot-based reset: record the MAX milestone per call_key at reset time.
    # _calc_leaderboard_scores will only count milestones ABOVE the snapshot value —
    # so new records on existing calls appear correctly after reset.
    lb_snap = {}
    for ck, _ in tracked_calls.items():
        ms = list(sent_milestones.get(ck, set()))
        lb_snap[ck] = max(ms) if ms else 0
    c["lb_milestone_snapshot"] = lb_snap
    c["lb_excluded_call_keys"] = []  # clear legacy blanket list
    save_config(c)
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
                "linkme": DEFAULT_LINKME_INFO, "linkinfo": DEFAULT_LINKME_INFO,
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
    """Manually force-update posts 135 (trending), 136 (leaderboard), 137 (champions), 3560+3562 (new trending)."""
    msg = await update.message.reply_text("⏳ Posts 135/136/137/3560/3562 update ho rahe hain...")
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
        results.append(f"🔮 Post 137 (Champions): {'✅ Updated' if ok else '❌ Failed'}")
    except Exception as e:
        results.append(f"🔮 Post 137 (Champions): ❌ {e}")
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
    [InlineKeyboardButton("🔮 Main Channel 🔮", url=TG_CHANNEL_LINK)],
    [InlineKeyboardButton("🔮 Done Wizrad 🔮", callback_data="force_join_done")],
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

# Owner requirement: force-join requirement OFF — koi bhi user bina channel
# join kiye saare commands use kar sakta hai.
FORCE_JOIN_ENABLED = False

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
                    await context.bot.send_photo(tid, photo=msg.photo[-1].file_id, caption=cap, caption_entities=cap_ents)
                elif msg.video:
                    await context.bot.send_video(tid, video=msg.video.file_id, caption=cap, caption_entities=cap_ents)
                elif msg.text:
                    await context.bot.send_message(tid, txt, entities=txt_ents)
                else:
                    await context.bot.forward_message(tid, from_chat_id=msg.chat_id, message_id=msg.message_id)
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
            await msg.reply_text(
                f"📢 <b>Broadcast Done</b>\n"
                f"✅ Sent: {ok} | 🚫 Blocked/Deleted: {blocked} | ❌ Failed: {fail}",
                parse_mode="HTML"
            ); return

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
            status_msg = await msg.reply_text(f"⏳ Sending to {len(targets)} users...")
            async def _do_media_send(tid, plain=False):
                cap = re.sub(r"<[^>]+>", "", msg.caption or "") if plain else (msg.caption or "")
                cap_ents = None if plain else (msg.caption_entities or None)
                if msg.photo:
                    await context.bot.send_photo(tid, photo=msg.photo[-1].file_id, caption=cap, caption_entities=cap_ents)
                elif msg.video:
                    await context.bot.send_video(tid, video=msg.video.file_id, caption=cap, caption_entities=cap_ents)
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
            summary = (
                f"📸 <b>Media Broadcast Done!</b>\n"
                f"✅ Sent: {ok} | 🚫 Blocked/Deleted: {blocked} | ❌ Failed: {fail}"
            )
            try:
                await status_msg.edit_text(summary, parse_mode="HTML")
            except Exception:
                await msg.reply_text(summary, parse_mode="HTML")
            return

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

        elif state == ST_ADD_MOMENTUM_VID:
            owner_edit_state[uid] = {"state": None}
            if msg.video or msg.animation:
                media_obj = msg.video or msg.animation
                ftype = "gif" if msg.animation else "video"
                vids = load_config().get("momentum_videos", [])
                vids.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set("momentum_videos", vids)
                await msg.reply_text(
                    f"✅ <b>Momentum Video #{len(vids)} Added!</b>\n\n"
                    f"Total stored videos: <b>{len(vids)}</b>\n"
                    f"These will now rotate in MOMENTUM ACTIVE posts.\n\n"
                    f"Use /listmomentumvideos to see all · /addmomentumvideo to add more",
                    parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Please send a video file. No changes made."); return

        elif state == ST_ADD_XRAY_VID:
            owner_edit_state[uid] = {"state": None}
            if msg.video or msg.animation:
                media_obj = msg.video or msg.animation
                ftype = "gif" if msg.animation else "video"
                vids = load_config().get("xray_videos", [])
                if len(vids) >= 10:
                    await msg.reply_text(
                        "⚠️ Maximum 10 X-Ray videos allowed.\n"
                        "Use /removexrayvideo N to remove one first."
                    ); return
                vids.append({"file_id": media_obj.file_id, "type": ftype})
                cfg_set("xray_videos", vids)
                await msg.reply_text(
                    f"✅ <b>X-Ray Video #{len(vids)} Added!</b>\n\n"
                    f"Total stored: <b>{len(vids)}/10</b>\n"
                    f"X-Ray reports mein rotate hongi.\n\n"
                    f"Use /listxrayvideos to see all · /addxrayvideo to add more",
                    parse_mode="HTML"); return
            else:
                await msg.reply_text("⚠️ Please send a video file. No changes made."); return

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
MISSEDCALL_STEPS = ["channel", "ca", "x", "entry_mc", "symbol", "link", "chain", "confirm"]
FIXX_STEPS       = ["channel", "ca", "x", "confirm"]
BUYBOT_STEPS     = ["ca", "chain", "symbol", "min_buy", "emoji", "emoji_step",
                    "media_low", "media_mid", "media_high", "link", "confirm"]
BBEDIT_STEPS     = ["value"]

LATEMC_STEPS     = ["channel", "ca", "entry_mc", "confirm"]
FREEZE_STEPS     = ["channel", "ca", "confirm"]
XCHECK_STEPS     = ["channel", "ca", "confirm"]

FLOW_STEPS = {"missedcall": MISSEDCALL_STEPS, "fixx": FIXX_STEPS,
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
        if step == "x":
            return ("📡 <b>Step 3/7</b>\n\nKitna <b>X</b> gaya? Number bhejo (e.g. <code>3</code>) "
                    "ya <b>Auto</b> dabao — bot khud asli ATH X nikaal lega.",
                    _wiz_kb([("🤖 Auto X", "auto")], per_row=1))
        if step == "entry_mc":
            return ("📡 <b>Step 4/7</b>\n\nAb <b>Entry MC</b> bhejo — jis MC par KOL ne call ki thi "
                    "(jab post ki thi us waqt ka MC) — e.g. <code>5K</code>\n\n"
                    "Bot khud calculate kar lega: Entry MC × X = Achieved MC.\n\n"
                    "Ya button se auto chuno.",
                    _wiz_kb([("⚡ Auto (DexScreener)", "auto")], per_row=1))
        if step == "symbol":
            return ("📡 <b>Step 5/7</b>\n\nToken ka <b>naam / symbol</b> bhejo (e.g. <code>PEPE</code>).",
                    _wiz_kb([("⚡ Auto", "auto")], per_row=1))
        if step == "link":
            return ("📡 <b>Step 6/7</b>\n\nKOL ki original call post ka <b>Telegram link</b> bhejo.\n"
                    "Example: <code>https://t.me/SomeKOL/123</code>",
                    _wiz_kb([("⏭ Skip", "skip")], per_row=1))
        if step == "chain":
            return ("📡 <b>Step 7/7</b>\n\n<b>Chain</b> chuno (RH tokens ke liye hamesha RH chuno).",
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
                f"X: <b>{d.get('x','auto')}</b>\n"
                f"Entry MC: <b>{d.get('entry_mc','auto')}</b>\n"
                f"{_achieved_line}"
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
            return ("🔧 <b>Step 3/3</b>\n\n<b>Asli entry MC</b> bhejo — jis MC par KOL ne call di thi.\n"
                    "Example: <code>100K</code> · <code>1.2M</code> · <code>500000</code>", _wiz_kb([]))
        if step == "confirm":
            d = data
            return ("✅ <b>Confirm Late Call Fix</b>\n\n"
                    f"Channel: <b>@{html.escape(d.get('channel',''))}</b>\n"
                    f"CA: <code>{html.escape(d.get('ca',''))}</code>\n"
                    f"Naya Entry MC: <b>{html.escape(str(d.get('entry_mc','')))}</b>\n\n"
                    "Bot entry MC set karega, purani 'Dropped a Call' post edit karega "
                    "aur usi MC se aage track karega.",
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
async def cmd_freeze_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "freeze", "channel", {})


@owner_only
async def cmd_checkcall_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await _wiz_goto(update.message, uid, "xcheck", "channel", {})


async def cmd_buybot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the Buy Bot setup. Works in a group (adds that group) or in DM."""
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in ("group", "supergroup"):
        me = await context.bot.get_me()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "🤖 Set up Buy Bot", url=f"https://t.me/{me.username}?start=buybot_{chat.id}")]])
        await update.message.reply_text(
            "🤖 <b>Buy Bot</b>\n\nTap below and I'll set up buy alerts for this group — "
            "I'll ask you everything step by step.",
            parse_mode="HTML", reply_markup=kb)
        return
    # DM without a group selected
    bots = load_buybots()
    mine = [(cid, c) for cid, c in bots.items() if c.get("owner_id") == user.id]
    me   = await context.bot.get_me()
    kb_rows = [[InlineKeyboardButton("➕ Add me to your group",
                url=f"https://t.me/{me.username}?startgroup=buybot&admin=post_messages")]]
    text = ("🤖 <b>Buy Bot</b>\n\nGet a buy alert in your group every time someone buys your token.\n\n"
            "<b>How to start:</b>\n1. Add me to your group as admin\n"
            "2. I'll send a setup button in the group\n3. Answer a few quick questions\n")
    if mine:
        text += "\n<b>Your buy bots:</b>\n" + "\n".join(
            f"• {html.escape(str(c.get('group_title','group')))} — "
            f"{'🟢 active' if c.get('active') else '⚪️ paused'} "
            f"({html.escape(str(c.get('symbol','?')))})" for _, c in mine)
        kb_rows.append([InlineKeyboardButton("⚙️ Manage", callback_data="bb:manage")])
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(kb_rows))


async def start_buybot_wizard(update, context, chat_id, group_title=""):
    return  # Buy Bot feature permanently removed
    uid  = update.effective_user.id
    bots = load_buybots()
    prev = bots.get(str(chat_id), {})
    await _wiz_goto(update.message, uid, "buybot", "ca",
                    {"chat_id": int(chat_id),
                     "group_title": group_title or prev.get("group_title", "your group")})


async def cmd_stopbuybot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    bots = load_buybots()
    uid  = update.effective_user.id
    targets = ([str(chat.id)] if chat.type in ("group", "supergroup")
               else [cid for cid, c in bots.items() if c.get("owner_id") == uid])
    stopped = []
    for cid in targets:
        if cid in bots:
            bots[cid]["active"] = False
            stopped.append(bots[cid].get("group_title", cid))
    save_buybots(bots)
    await update.message.reply_text(
        ("⏸ Buy bot paused for: " + ", ".join(map(str, stopped))) if stopped
        else "No active buy bot found here.")


async def cmd_buybothelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Buy Bot Help</b>\n\n"
        "/buybot — set up or manage buy alerts\n"
        "/buybotstatus — see this group's buy bot\n"
        "/stopbuybot — pause buy alerts\n\n"
        "Add me to your group as admin, then tap the setup button.",
        parse_mode="HTML")


async def cmd_buybotstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    c = load_buybots().get(str(chat.id))
    if not c:
        await update.message.reply_text("No buy bot configured here. Send /buybot to set one up."); return
    await update.message.reply_text(
        f"🤖 <b>Buy Bot</b>\n\nToken: <b>{html.escape(str(c.get('symbol','?')))}</b>\n"
        f"Chain: <b>{c.get('chain')}</b>\nMin buy: <b>${c.get('min_buy')}</b>\n"
        f"Status: <b>{'🟢 Active' if c.get('active') else '⚪️ Paused'}</b>\n"
        f"Alerts sent: <b>{c.get('alerts_sent', 0)}</b>",
        parse_mode="HTML")


@owner_only
async def cmd_listbuybots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bots = load_buybots()
    if not bots:
        await update.message.reply_text("Koi buy bot active nahi hai."); return
    lines = []
    for cid, c in bots.items():
        lines.append(f"• <b>{html.escape(str(c.get('group_title', cid)))}</b> (<code>{cid}</code>)\n"
                     f"   {html.escape(str(c.get('symbol','?')))} / {c.get('chain')} — "
                     f"{'🟢' if c.get('active') else '⚪️'} — by @{c.get('owner_username') or c.get('owner_id')}")
    await update.message.reply_text("🤖 <b>Buy Bots</b>\n\n" + "\n".join(lines[:50]), parse_mode="HTML")


@owner_only
async def cmd_removebuybot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: <code>/removebuybot chat_id</code>", parse_mode="HTML"); return
    bots = load_buybots(); cid = context.args[0].strip()
    if cid in bots:
        bots.pop(cid); save_buybots(bots)
        await update.message.reply_text(f"🗑 Buy bot removed: {cid}")
    else:
        await update.message.reply_text("Woh chat id registered nahi hai.")


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
        elif step == "entry_mc":
            if parse_mc_string(text) <= 0:
                await msg.reply_text("⚠️ Valid MC bhejo, e.g. 453.2K / 1.2M / 500000."); return True
            _wiz_set(uid, "entry_mc", text)
        elif step == "x":
            try:
                xv = int(float(text.lower().rstrip("x")))
            except ValueError:
                await msg.reply_text("⚠️ Number bhejo (e.g. 12) ya Auto dabao."); return True
            if xv < 2:
                await msg.reply_text("⚠️ X kam se kam 2 hona chahiye."); return True
            _wiz_set(uid, "x", str(xv))
        elif step == "symbol":
            _wiz_set(uid, "symbol", text.upper()[:20])
        elif step == "link":
            _wiz_set(uid, "link", text)
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
        elif flow == "buybot":
            await _run_buybot_activate(update, context, query.message, data)
        return

    # value buttons
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


async def _run_missedcall(update, context, target_msg, d):
    args = [d.get("channel", ""), d.get("ca", ""), (d.get("x") or "auto"),
            d.get("entry_mc") or "auto",
            d.get("symbol") or "-",
            d.get("link") or "-",
            d.get("chain") or "auto"]
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
    args = [d.get("channel", ""), d.get("ca", ""), str(d.get("entry_mc", ""))]
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

async def buybot_job(context: ContextTypes.DEFAULT_TYPE):
    """DISABLED PERMANENTLY — Buy Bot feature removed."""
    return
    bots = load_buybots()
    if not bots: return
    changed = False
    for chat_id, cfgb in list(bots.items()):
        if not cfgb.get("active"): continue
        try:
            gnet = cfgb.get("gecko") or BUYBOT_CHAIN_MAP.get(cfgb.get("chain", "SOL"), {}).get("gecko", "solana")
            pool = cfgb.get("pair")
            info = None
            if not pool:
                info = await asyncio.to_thread(_resolve_token_pool, cfgb.get("ca", ""), cfgb.get("chain", ""))
                if not info:
                    cfgb["last_error"] = "pool not found"; changed = True; continue
                cfgb.update({"pair": info["pair"], "gecko": info["gecko"]}); changed = True
                pool, gnet = info["pair"], info["gecko"]
            min_usd = float(cfgb.get("min_buy") or 10)
            trades  = await asyncio.to_thread(_fetch_pool_buys, gnet, pool, min_usd)
            cfgb["last_poll"]   = datetime.utcnow().isoformat()
            cfgb["last_trades"] = len(trades)
            changed = True
            if not trades:
                cfgb["last_error"] = "no buys returned by data source"
                continue
            cfgb["last_error"] = ""
            seen_tx = list(cfgb.get("seen_tx") or [])
            last_ts = cfgb.get("last_ts") or ""
            if not last_ts:
                cfgb["last_ts"] = trades[-1]["ts"]
                cfgb["seen_tx"] = [t["tx"] for t in trades][-60:]
                continue
            fresh = [t for t in trades if t["usd"] >= min_usd
                     and t["tx"] not in seen_tx and t["ts"] > last_ts]
            if not fresh:
                continue
            if info is None:
                info = await asyncio.to_thread(_resolve_token_pool, cfgb.get("ca", ""), cfgb.get("chain", ""))
            mc    = (info or {}).get("mc", 0)
            price = (info or {}).get("price", 0)
            for t in fresh[-5:]:
                ok = await _post_buy_alert(context.bot, int(chat_id), cfgb, t, mc, price)
                if ok:
                    cfgb["alerts_sent"] = int(cfgb.get("alerts_sent", 0)) + 1
                await asyncio.sleep(1)
            cfgb["last_ts"] = fresh[-1]["ts"]
            cfgb["seen_tx"] = ([t["tx"] for t in fresh] + seen_tx)[:80]
        except Exception as e:
            logger.warning(f"buybot job {chat_id}: {e}")
            cfgb["last_error"] = str(e)[:120]; changed = True
    if changed:
        save_buybots(bots)


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


async def cmd_buybotsettings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in ("group", "supergroup"):
        me = await context.bot.get_me()
        await update.message.reply_text(
            "⚙️ Buy Bot settings open in a private chat.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "⚙️ Open settings", url=f"https://t.me/{me.username}?start=buybot_{chat.id}")]]))
        return
    bots = load_buybots()
    mine = [(cid, c) for cid, c in bots.items()
            if c.get("owner_id") == user.id or user.id in OWNER_IDS]
    if not mine:
        await update.message.reply_text("You have no buy bot yet. Send /buybot to create one."); return
    if len(mine) == 1:
        await bb_show_panel(update.message, mine[0][0]); return
    rows = [[InlineKeyboardButton(f"{c.get('symbol','?')} — {c.get('group_title','group')}",
             callback_data=f"bb:{cid}:panel")] for cid, c in mine[:20]]
    await update.message.reply_text("Pick a buy bot to manage:",
                                    reply_markup=InlineKeyboardMarkup(rows))


async def buybot_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        await cmd_buybotsettings(update, context); return
    chat_id, key = parts[1], parts[2]
    bots = load_buybots(); cfgb = bots.get(str(chat_id))
    uid  = query.from_user.id
    if not cfgb:
        await query.message.reply_text("That buy bot no longer exists."); return
    if cfgb.get("owner_id") != uid and uid not in OWNER_IDS:
        await query.message.reply_text("Only the person who set this buy bot up can change it."); return

    if key == "panel":
        await bb_show_panel(query.message, chat_id, edit=True); return
    if key == "media":
        try:
            await query.message.edit_text(
                "🖼 <b>Alert Visuals</b>\n\nEvery buy size can use its own photo, GIF or video.\n"
                "Pick a size to set or replace its visual.",
                parse_mode="HTML", reply_markup=_bb_media_panel(cfgb))
        except Exception:
            await query.message.reply_text(
                "🖼 <b>Alert Visuals</b>", parse_mode="HTML", reply_markup=_bb_media_panel(cfgb))
        return
    if key.startswith("t_"):
        field = key[2:]
        cfgb[field] = not bool(cfgb.get(field, True))
        bots[str(chat_id)] = cfgb; save_buybots(bots)
        await bb_show_panel(query.message, chat_id, edit=True); return
    if key == "rerun":
        await start_buybot_wizard_from_cb(query, context, int(chat_id), cfgb.get("group_title", "")); return
    if key == "test":
        demo = {"tx": "", "usd": float(cfgb.get("min_buy") or 10) * 3, "amt": 1234567,
                "ts": "", "from": "", "price": 0}
        ok = await _post_buy_alert(context.bot, int(chat_id), cfgb, demo, 0, 0)
        await query.message.reply_text(
            "✅ Test alert sent to your group." if ok else
            "⚠️ I couldn't post in that group — make sure I'm still a member with send permission.")
        return
    wizard_state[uid] = {"flow": "bbedit", "step": "value",
                         "data": {"chat_id": int(chat_id), "key": key}}
    await _wiz_send(query.message, "bbedit", "value", wizard_state[uid]["data"])


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


async def start_buybot_wizard_from_cb(query, context, chat_id, group_title=""):
    uid = query.from_user.id
    wizard_state[uid] = {"flow": "buybot", "step": "ca",
                         "data": {"chat_id": int(chat_id),
                                  "group_title": group_title or "your group"}}
    await _wiz_send(query.message, "buybot", "ca", wizard_state[uid]["data"])


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
    app.add_handler(CommandHandler("linkinfo",  cmd_linkinfo))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("linkme",    cmd_linkme))
    app.add_handler(CommandHandler("submit",    cmd_submit))

    # Callbacks — specific patterns FIRST, then generic catch-all
    app.add_handler(CallbackQueryHandler(cb_setemoji, pattern=r"^setemoji:"))
    app.add_handler(CallbackQueryHandler(cb_ownerhelp, pattern=r"^oh:"))
    app.add_handler(CallbackQueryHandler(cb_x_alert_details, pattern=r"^xbtn:"))
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
    app.add_handler(CommandHandler("refreshtrending2",     cmd_refreshtrending2))
    app.add_handler(CommandHandler("ownerhelpt",           cmd_ownerhelpt))
    app.add_handler(CommandHandler("resetsoltrend",        cmd_resetsoltrend))
    app.add_handler(CommandHandler("resetethtrend",        cmd_resetethtrend))
    app.add_handler(CommandHandler("resetbsctrend",        cmd_resetbsctrend))
    app.add_handler(CommandHandler("resetbasetrend",       cmd_resetbasetrend))
    app.add_handler(CommandHandler("resettontrend",        cmd_resettontrend))
    app.add_handler(CommandHandler("resetrhtrend",         cmd_resetrhtrend))
    app.add_handler(CommandHandler("pintrending",          cmd_pintrending))
    app.add_handler(CommandHandler("unpintrending",        cmd_unpintrending))
    app.add_handler(CommandHandler("listpinned",           cmd_listpinned))
    # app.add_handler(CommandHandler("blocktrending",        cmd_blocktrending))    # disabled
    # app.add_handler(CommandHandler("unblocktrending",      cmd_unblocktrending))  # disabled
    # app.add_handler(CommandHandler("listblockedtrending",  cmd_listblockedtrending))  # disabled
    app.add_handler(CommandHandler("givepoints",        cmd_givepoints))
    app.add_handler(CommandHandler("checkpoints",       cmd_checkpoints))
    app.add_handler(CommandHandler("zerocolpoints",     cmd_zerocolpoints))
    app.add_handler(CommandHandler("resetallpoints",    cmd_resetallpoints))
    app.add_handler(CommandHandler("freezecall",        cmd_freezecall))
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
    app.add_handler(CommandHandler("mystats",        cmd_mystats))

    # Owner — templates
    app.add_handler(CommandHandler("settemplate",    cmd_settemplate))
    app.add_handler(CommandHandler("edittemplate",   cmd_edittemplate))
    app.add_handler(CommandHandler("setrangetemplate",   cmd_setrangetemplate))
    app.add_handler(CommandHandler("listrangetemplates", cmd_listrangetemplates))
    app.add_handler(CommandHandler("delrangetemplate",   cmd_delrangetemplate))
    app.add_handler(CommandHandler("editmilestone",  cmd_editmilestone))
    app.add_handler(CommandHandler("clearmilestone", cmd_clearmilestone))
    app.add_handler(CommandHandler("listmilestones", cmd_listmilestones))
    app.add_handler(CommandHandler("setmilestones",  cmd_setmilestones))

    # Owner — media
    app.add_handler(CommandHandler("setmedia",       cmd_setmedia))
    app.add_handler(CommandHandler("clearmedia",     cmd_clearmedia))
    app.add_handler(CommandHandler("listmedia",      cmd_listmedia))
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
    app.add_handler(CommandHandler("resetleaderboard",   cmd_resetleaderboard))
    app.add_handler(CommandHandler("setleaderboardtemplate", cmd_setleaderboardtemplate))
    app.add_handler(CommandHandler("clearleaderboardtemplate", cmd_clearleaderboardtemplate))
    # app.add_handler(CommandHandler("trendingKols",       cmd_trendingkols))  # disabled
    # app.add_handler(CommandHandler("trendingkols",       cmd_trendingkols))  # disabled
    app.add_handler(CommandHandler("setcommandvideo",    cmd_setcommandvideo))
    app.add_handler(CommandHandler("setpromo",           cmd_setpromo))
    app.add_handler(CommandHandler("stoppromo",          cmd_stoppromo))
    app.add_handler(CommandHandler("setpromolink",        cmd_setpromolink))
    app.add_handler(CommandHandler("clearpromolink",      cmd_clearpromolink))
    app.add_handler(CommandHandler("pendingkols",         cmd_pendingkols))
    app.add_handler(CommandHandler("previewtemplate",     cmd_previewtemplate))

    # Owner — new commands (Page 2)
    app.add_handler(CommandHandler("joinkols",             cmd_joinkols))
    app.add_handler(CommandHandler("setdroppedtemplate",  cmd_setdroppedtemplate))
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

    # ── Button wizards + Buy Bots ────────────────────────────────────────────
    app.add_handler(CommandHandler("addmissedcallw",  start_missedcall_wizard))
    app.add_handler(CommandHandler("missedcallw",     start_missedcall_wizard))
    app.add_handler(CommandHandler("fixx",            cmd_fixx))
    # 🆘 Problem Fix Center — ek hi solid entry point (buttons + step-by-step)
    app.add_handler(CommandHandler("fix",             cmd_fixpanel))
    app.add_handler(CommandHandler("problem",         cmd_fixpanel))
    app.add_handler(CommandHandler("latemc",          cmd_latemc_wizard))
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

    app.job_queue.run_repeating(leaderboard_job,     interval=max(5, int(os.environ.get("LEADERBOARD_INTERVAL_SECONDS", "20") or 20)), first=45)    # slowed from 5s: frequent edits were flooding the shared userbot connection
    app.job_queue.run_repeating(champions_job,       interval=max(5, int(os.environ.get("CHAMPIONS_INTERVAL_SECONDS", "20") or 20)), first=50)    # slowed from 5s: frequent edits were flooding the shared userbot connection
    app.job_queue.run_repeating(trending_job,         interval=120,       first=60)    # every 2 min
    # trending2_job (posts 3560+3562, Dexscreener trend lists) DISABLED — owner
    # request to cut bot load. Re-enable this line if the feature is needed again.
    # app.job_queue.run_repeating(trending2_job,        interval=120,       first=90)
    app.job_queue.run_repeating(backup_job,          interval=1800,      first=60)   # har 30 min Telegram backup
    app.job_queue.run_repeating(ps_watch_job,         interval=180,       first=120)  # PinkSale: presale end → live X tracking
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
        await _step("realtime_monitoring", setup_realtime_monitoring(app_ref.bot), 90)

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
                        posts = await asyncio.wait_for(fetch_channel_posts(ch), timeout=30)
                        special_set = set(load_special_channels())
                        mins = (float(os.environ.get("SPECIAL_BACKFILL_MINUTES", "180") or 180)
                                if ch.lower() in special_set else
                                float(os.environ.get("CALL_CATCHUP_MINUTES", "30") or 30))
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

if __name__ == "__main__":
    main()
