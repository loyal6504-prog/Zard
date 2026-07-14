import os
import re
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
    ContextTypes, MessageHandler, filters,
)
from telegram.error import TelegramError

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
OWNER_ID_2 = int(os.environ.get("OWNER_ID_2", "0"))
OWNER_IDS  = [oid for oid in [OWNER_ID, OWNER_ID_2] if oid != 0]
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
SUBSCRIPTIONS_FILE    = _dp("subscriptions.json")
LINKED_CHANNELS_FILE  = _dp("linked_channels.json")
PENDING_REQUESTS_FILE = _dp("pending_requests.json")
TRACKED_FILE          = _dp("tracked_calls.json")
MILESTONES_FILE       = _dp("sent_milestones.json")
SEEN_FILE             = _dp("seen_messages.json")
ADMINS_FILE           = _dp("admins.json")
MILESTONE_POSTS_FILE  = _dp("milestone_posts.json")
CHANNEL_SUBS_FILE     = _dp("channel_subs.json")
MOMENTUM_SENT_FILE    = _dp("momentum_sent.json")
CHANNEL_POINTS_FILE   = _dp("channel_points.json")
TRENDING_BLACKLIST_FILE = _dp("trending_blacklist.json")
KOL_OWNERS_FILE         = _dp("kol_owners.json")   # channel.lower() -> telegram user_id of owner

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
                6000, 7000, 8000, 9000, 10000]
MAX_MILESTONE = 10000  # No alerts beyond this

SUPPORTED_CHAINS = {"ethereum": "ETH", "bsc": "BNB", "base": "BASE", "solana": "SOL"}
CHAIN_TO_DEXPATH = {"SOL": "solana", "ETH": "ethereum", "BNB": "bsc", "BASE": "base"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

ETH_CA_PATTERN = re.compile(r'0x[a-fA-F0-9]{40}')
SOL_CA_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
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
    "star":  5823304208253723019,
    "arrow": 5823644661721341672,
    1:  5823249627809323607,
    2:  5823495591996432785,
    3:  5823660454316090458,
    4:  5823196112516817383,
    5:  5823474885959098519,
    6:  5823206733970940623,
    7:  5823190073792799582,
    8:  5823699920770572135,
    9:  5823170879583952565,
    10: 5823639825588166142,
}

# Premium emoji IDs for champions post 137
CHAMPIONS_PREMIUM_EMOJIS = {
    "star":  5915598335275703773,
    "arrow": 5823568391692099527,
    1:  5823670826662108929,
    2:  5823288900990278598,
    3:  5823289412091387748,
    4:  5823639666674377774,
    5:  5823199677339673551,
    6:  5823237610490835810,
    7:  5823618874737696599,
    8:  5823591889458176055,
    9:  5823593311092349714,
    10: 5823365544681676049,
}

# Premium emoji IDs for trending post 135
TRENDING_PREMIUM_EMOJIS = {
    "SOL":   5818711831652343968,
    "BASE":  5818705539525255782,
    "ETH":   5821222313051301501,
    "BNB":   5820961269234016758,
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
TEER_EMOJI_ID = 5909106337588976440
MOMENTUM_ACTIVE_EMOJI_ID = 5920079442159344914

EMOJI_PACKS = [
    {   # 0: Red
        "name": "red",
        "crystal": 5909248376452424773,
        "kol":     5909159208636391630,
        "x":       5908741969743454611,
        "bot":     5908820284177129986,
        "chain": {
            "SOL":  5920357369493069134,
            "ETH":  5920045855515091835,
            "BNB":  5920443749875326865,
            "BSC":  5920443749875326865,
            "BASE": 5920444029048200535,
        },
    },
    {   # 1: Blue
        "name": "blue",
        "crystal": 5909056627637493639,
        "kol":     5904225055717467267,
        "x":       5906643096535310217,
        "bot":     5906892114444164583,
        "chain": {
            "SOL":  5920151760818676194,
            "ETH":  5920015992607481576,
            "BNB":  5920511086372595188,
            "BSC":  5920511086372595188,
            "BASE": 5920360015192933467,
        },
    },
    {   # 2: White
        "name": "white",
        "crystal": 5911492432440073438,
        "kol":     5909266939301076138,
        "x":       5906532432407960996,
        "bot":     5908863744951197586,
        "chain": {
            "SOL":  5908783523552042618,
            "ETH":  5908947999324645137,
            "BNB":  5909194796735406168,
            "BSC":  5909194796735406168,
            "BASE": 5908768143274155036,
        },
    },
    {   # 3: Purple
        "name": "purple",
        "crystal": 5909214897182352449,
        "kol":     5906881183752396796,
        "x":       5909219853574610744,
        "bot":     5909039048336351478,
        "chain": {
            "SOL":  5911071315191668044,
            "ETH":  5908865196650143288,
            "BNB":  5908955077430746579,
            "BSC":  5908955077430746579,
            "BASE": 5909036321032118088,
        },
    },
    {   # 4: Green
        "name": "green",
        "crystal": 5823205836322777681,
        "kol":     5872792324676788914,
        "x":       5873041836506882365,
        "bot":     5875447065437282522,
        "chain": {
            "SOL":  5875048857544432514,
            "ETH":  5875284157327744482,
            "BNB":  5872901489860550331,
            "BSC":  5872901489860550331,
            "BASE": 5875142917328215269,
        },
    },
]

ST_X_TEMPLATE      = "edit_x_template"
ST_ADD_DROPPED_VID = "add_dropped_vid"
ST_DROPPED_TMPL    = "edit_dropped_tmpl"

# ─── Dropped Call feature ─────────────────────────────────────────────────────
DROPPED_CALL_EMOJI = 5960999605633031173

DEFAULT_DROPPED_TEMPLATE = (
    "🔮 <b>@{channel} Dropped a Call</b> 🔮\n\n"
    "@{channel} has called the {chain} token ${symbol} at a {entry} MC. "
    "We've started tracking it and will continue to send performance alerts "
    "as the token progresses. Stay tuned!\n\n"
    "Ca: <code>{ca}</code>\n\n"
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="https://x.com/WizardScan"> X</a>\n'
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
def load_channels():         return load_json(CHANNELS_FILE, [])
def save_channels(c):        save_json(CHANNELS_FILE, c)
def load_config():           return load_json(BOT_CONFIG_FILE, {})
def save_config(c):          save_json(BOT_CONFIG_FILE, c)
def load_x_accounts():       return load_json(X_ACCOUNTS_FILE, [])
def save_x_accounts(x):      save_json(X_ACCOUNTS_FILE, x)
def load_subscriptions():    return load_json(SUBSCRIPTIONS_FILE, [])
def save_subscriptions(s):   save_json(SUBSCRIPTIONS_FILE, s)
def load_channel_subs():     return load_json(CHANNEL_SUBS_FILE, {})
def save_channel_subs(s):    save_json(CHANNEL_SUBS_FILE, s)
def load_momentum_sent():    return load_json(MOMENTUM_SENT_FILE, {})
def save_momentum_sent(s):   save_json(MOMENTUM_SENT_FILE, s)
def load_channel_points():       return load_json(CHANNEL_POINTS_FILE, {})
def save_channel_points(p):      save_json(CHANNEL_POINTS_FILE, p)
def load_trending_blacklist():   return set(load_json(TRENDING_BLACKLIST_FILE, []))
def save_trending_blacklist(s):  save_json(TRENDING_BLACKLIST_FILE, sorted(s))
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

def _save_seen():
    try:
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
owner_edit_state = {}
_userbot_login   = {}
userbot_client   = None
_login_client    = None

# ─── Points System (Champion KOL List) ────────────────────────────────────────
# Points awarded per milestone tier (for champion kols list only)
POINT_TIERS = [
    (250, 100),  # 250X–499X → 100 pts
    (100, 75),   # 100X–249X → 75 pts
    (50,  50),   # 50X–99X   → 50 pts
    (25,  35),   # 25X–49X   → 35 pts
    (10,  20),   # 10X–24X   → 20 pts
    (5,   10),   # 5X–9X     → 10 pts
    (2,    5),   # 2X–4X     →  5 pts
]
POINTS_FOR_CHAMPION = 100  # Points needed to appear in champion kols list
POINTS_DEDUCT_FAILED = 10  # Deducted if call never hits 2X
CALL_FAIL_DAYS = 30        # Days after which a call is considered "failed" if no 2X

def get_point_tier_reward(x_val):
    """Return (tier_threshold, points) for the given x_val, or (0, 0)."""
    for threshold, pts in POINT_TIERS:
        if x_val >= threshold:
            return threshold, pts
    return 0, 0

def get_channel_points(channel):
    """Return total points for a channel."""
    pts = load_channel_points()
    return pts.get(channel.lower(), {}).get("points", 0)

async def award_points_for_milestone(channel, call_key, x_val):
    """Award incremental points when a milestone is hit. Only awards once per tier per call.
    Uses asyncio lock to prevent concurrent read-modify-write corruption."""
    tier_threshold, tier_pts = get_point_tier_reward(x_val)
    if tier_pts <= 0:
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
        # Points increment = current tier - highest previously awarded tier for this call
        prev_pts = sum(tp for tt, tp in POINT_TIERS if tt in call_awarded)
        new_pts = tier_pts - prev_pts
        if new_pts <= 0:
            return 0
        entry["points"] = entry.get("points", 0) + new_pts
        call_awarded.append(tier_threshold)
        awarded[call_key] = call_awarded
        entry["awarded_tiers"] = awarded
        pts_data[key] = entry
        save_channel_points(pts_data)
    return new_pts

async def deduct_points_for_failed_call(channel, call_key):
    """Deduct 10 points if a call never hit 2X. Only deducts once per call."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        entry = pts_data[key]
        deducted = entry.get("deducted_calls", [])
        if call_key in deducted:
            return  # Already deducted
        entry["points"] = entry.get("points", 0) - POINTS_DEDUCT_FAILED
        deducted.append(call_key)
        entry["deducted_calls"] = deducted
        pts_data[key] = entry
        save_channel_points(pts_data)

async def give_manual_points(channel, amount):
    """Manually give points to a channel (owner command)."""
    async with _points_lock:
        pts_data = load_channel_points()
        key = channel.lower()
        if key not in pts_data:
            pts_data[key] = {"points": 0, "awarded_tiers": {}, "deducted_calls": []}
        pts_data[key]["points"] = pts_data[key].get("points", 0) + amount
        total = pts_data[key]["points"]
        save_channel_points(pts_data)
    return total

# ─── Utilities ────────────────────────────────────────────────────────────────
def safe_format(template, **kwargs):
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

def fmt_x(x: float) -> str:
    """Format X multiplier as readable decimal string (e.g. 1.25x, 2.7x, 123x)."""
    if x <= 0: return "1x"
    if x >= 100:   return f"{x:.0f}x"
    elif x >= 10:  return f"{x:.1f}x"
    elif x >= 2:   return f"{x:.1f}x"
    elif x > 1.01: return f"{x:.2f}x"
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
    "<b>🔮 Fee: $180 ONLY</b>\n\n"
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
    "▪ Call hits 2X–4X → +5 pts\n"
    "▪ Call hits 5X–9X → +10 pts\n"
    "▪ Call hits 10X–24X → +20 pts\n"
    "▪ Call hits 25X–49X → +35 pts\n"
    "▪ Call hits 50X–99X → +50 pts\n"
    "▪ Call hits 100X–249X → +75 pts\n"
    "▪ Call hits 250X+ → +100 pts\n\n"
    "▪ A call that fails to reach 2X within 30 days → −10 pts"
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
    "If your KOL channel is tracked by Wizard Scan, you can link your own Telegram channel to automatically receive all milestone alerts.\n\n"
    "<b>🔮 How it works:</b>\n\n"
    "🪄 Wizard Scan tracks your KOL channel for winning calls\n"
    "🪄 When a call hits a milestone (2X, 10X, 100X...), Wizard Scan posts an alert\n"
    "🪄 With /linkme, that same alert is ALSO sent to your linked channel automatically\n"
    "🪄 Your community gets instant updates without leaving your channel\n\n"
    "<b>🔮 Setup (2 steps):</b>\n\n"
    "Step 1: Add @WIZARD_SCAN_BOT as admin to your channel (with post permission)\n"
    "Step 2: Send this command:\n"
    "<code>/linkme @tracked_kol_channel @your_channel</code>\n\n"
    "Example:\n"
    "<code>/linkme @SomeCryptoKOL @MyAlertsChannel</code>\n\n"
    "<i>Only channels already tracked by Wizard Scan are eligible. Use /submit to request tracking first if needed.</i>"
)

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
        [InlineKeyboardButton(lbl("fast_track",   "🔮 Fast Track 🔮"),        callback_data="fast_track")],
        [InlineKeyboardButton(lbl("chat_us",      "🔮  Chat With Us  🔮"),    callback_data="chat_us")],
    ])

def history_keyboard(channel):
    ch = channel[:28]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔮ALL",  callback_data=f"h|{ch}|all"),
            InlineKeyboardButton("🔮BNB",  callback_data=f"h|{ch}|bnb"),
            InlineKeyboardButton("🔮ETH",  callback_data=f"h|{ch}|eth"),
        ],
        [
            InlineKeyboardButton("🔮SOL",  callback_data=f"h|{ch}|sol"),
            InlineKeyboardButton("🔮BASE", callback_data=f"h|{ch}|base"),
            InlineKeyboardButton("🔮TOP",  callback_data=f"h|{ch}|top"),
        ],
    ])

# ─── Userbot ──────────────────────────────────────────────────────────────────
def load_userbot_session():
    try:
        with open(USERBOT_SESSION_FILE) as f: return f.read().strip()
    except Exception: return ""

def save_userbot_session(s):
    with open(USERBOT_SESSION_FILE, "w") as f: f.write(s)

async def init_userbot():
    global userbot_client
    session_str = load_userbot_session() or os.environ.get("SESSION_STRING", "").strip()
    if not session_str or not OWNER_API_ID or not OWNER_API_HASH:
        logger.warning("⚠️ Userbot session not found.")
        return
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        userbot_client = TelegramClient(StringSession(session_str), OWNER_API_ID, OWNER_API_HASH)
        await userbot_client.connect()
        if not await userbot_client.is_user_authorized():
            userbot_client = None; return
        me = await userbot_client.get_me()
        logger.info(f"✅ Userbot: @{me.username}")
    except Exception as e:
        logger.error(f"Userbot init failed: {e}"); userbot_client = None

# ─── DexScreener ─────────────────────────────────────────────────────────────
def _fetch_dex_sync(ca, retries=3):
    for attempt in range(retries):
        try:
            resp  = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}", timeout=12, headers=HEADERS)
            if resp.status_code != 200: time.sleep(2); continue
            pairs = resp.json().get("pairs") or []
            sup   = [p for p in pairs if p.get("chainId","").lower() in SUPPORTED_CHAINS
                     and (p.get("liquidity",{}).get("usd") or 0) > 500]
            if not sup: return {}
            best  = sorted(sup, key=lambda p: p.get("liquidity",{}).get("usd",0) or 0, reverse=True)[0]
            chain = SUPPORTED_CHAINS[best.get("chainId","").lower()]
            # Use live priceUsd for accurate ratio calculation
            price = float(best.get("priceUsd") or 0)
            # Use marketCap (live circulating supply MC) — do NOT fall back to fdv (static)
            mc    = float(best.get("marketCap") or 0)
            # If marketCap missing, estimate from price * fdv_supply ratio if fdv available
            if mc <= 0:
                fdv = float(best.get("fdv") or 0)
                mc  = fdv  # last resort only
            if price <= 0 and mc <= 0: return {}
            return {"chain": chain, "mcap": mc, "mcap_fmt": fmt_mc(mc) if mc > 0 else "N/A",
                    "price": price,
                    "symbol": best.get("baseToken",{}).get("symbol",""),
                    "pair_addr": best.get("pairAddress",""),
                    "tg_link": (best.get("info",{}).get("socials") or [{}])[0].get("url","") if best.get("info",{}).get("socials") else ""}
        except Exception as e:
            logger.warning(f"DexScreener attempt {attempt+1}: {e}"); time.sleep(2)
    return {}

async def fetch_dexscreener(ca):
    return await asyncio.to_thread(_fetch_dex_sync, ca)

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

def _fetch_gecko_chain(gecko_network: str, dex_chain_key: str, blacklist: set) -> list:
    """Fetch top-5 trending tokens for one chain via GeckoTerminal.
    Returns list of {symbol, mc_fmt, tg_url, dex_url, has_tg} dicts.
    """
    GECKO_HEADERS = {"Accept": "application/json"}
    DEXPATH = {"ETH": "ethereum", "BNB": "bsc", "BASE": "base", "SOL": "solana"}
    results = []
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/{gecko_network}/trending_pools"
            f"?page=1&include=base_token",
            headers=GECKO_HEADERS, timeout=15
        )
        if r.status_code != 200:
            logger.warning(f"GeckoTerminal {gecko_network}: HTTP {r.status_code}")
            return results
        data = r.json()
    except Exception as e:
        logger.warning(f"GeckoTerminal {gecko_network} fetch failed: {e}")
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

    return chain_tokens

async def fetch_trending():
    return await asyncio.to_thread(_fetch_trending_sync)

def _calc_trending_kols():
    """Top 10 tracked channels sorted by highest X milestone (descending).
    Returns list of dicts: {channel, best_x, wizard_post_id}"""
    channels = load_channels()
    scores = []
    for ch in channels:
        best_x = 0
        best_call_key = None
        for call_key, call in tracked_calls.items():
            if call.get("channel", "").lower() != ch.lower(): continue
            ms = list(sent_milestones.get(call_key, set()))
            if ms:
                mx = max(ms)
                if mx > best_x:
                    best_x = mx
                    best_call_key = call_key
        # Get WizardScan post ID for this channel's highest X milestone
        wizard_post_id = None
        if best_call_key and best_x > 0:
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
            posts.append({"id": msg_id, "text": text})
        return posts
    except Exception as e:
        logger.error(f"Fetch {channel}: {e}"); return []

async def fetch_channel_posts(channel):
    return await asyncio.to_thread(_fetch_posts_sync, channel)

def extract_ca(text):
    eth = ETH_CA_PATTERN.findall(text)
    if eth: return ("EVM", eth[0].lower())
    for s in SOL_CA_PATTERN.findall(text):
        if not s.startswith("0x") and 32 <= len(s) <= 44: return ("SOL", s)
    return None

def is_call_message(text):
    if not text or len(text) < 10: return False
    tl  = text.lower()
    kw  = ["buy","long","entry","target","tp ","gem"," call","launch","listed","mcap",
           "market cap","ca:","contract","bullish","ape","snipe","early","presale",
           "stealth","kol","dexscreener","dextools","birdeye","pump.fun","bullx","photon"]
    return any(k in tl for k in kw) or bool(ETH_CA_PATTERN.search(text)) or \
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
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="https://x.com/WizardScan"> X</a>\n'
    '🔮<a href="{bot_link}">BOT</a>'
)

DEFAULT_X_TEMPLATE = DEFAULT_TEMPLATE  # X features removed

# ─── Tiered templates (100X–9999X) ────────────────────────────────────────────
TIERED_TEMPLATES = [
    (100, 499,
        "<b>🔮 SOLID KOL @{channel}</b>\n\n"
        "@{channel} delivered {x}X. ${symbol} was called at a {entry} MC. Current MC stands at {current}. "
        "Massive move delivered. Eyes on the next milestone.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (500, 999,
        "<b>🔮 Apex KOL @{channel}</b>\n\n"
        "@{channel} printed {x}X. ${symbol} continues to exceed expectations, climbing from {entry} MC to {current}. "
        "Another milestone secured. Based KOL of Wizard Scan.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (1000, 1999,
        "<b>🔮 ELITE KOL @{channel}</b>\n\n"
        "@{channel} nailed {x}X. ${symbol} was called at a {entry} MC and has now climbed to {current}. "
        "A truly elite performance with exceptional returns.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (2000, 2999,
        "<b>🔮 RARE KOL @{channel}</b>\n\n"
        "@{channel} crushed {x}X. ${symbol} was called at a {entry} MC and has now reached {current}. "
        "An extraordinary run that stands among the rarest performances.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (3000, 4999,
        "<b>🔮 EPIC KOL @{channel}</b>\n\n"
        "@{channel} smashed {x}X. From {entry} MC to {current}, ${symbol} delivered a historic performance. "
        "One of the biggest moves ever tracked.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
        '🔮<a href="{bot_link}">BOT</a>'
    ),
    (5000, 9999,
        "<b>🔮 LEGENDARY KOL @{channel}</b>\n\n"
        "@{channel} hit {x}X. The results speak for themselves. ${symbol} climbed from {entry} MC to {current}, "
        "once again delivering exceptional returns. Another historic call added to the record.\n\n"
        "<b>🔮 {entry}  🔮  {current}</b>\n\n"
        "CA: <code>{ca}</code>\n\n"
        '🔮<a href="{kol_link}">KOL</a>\n'
        '🔮<a href="https://x.com/WizardScan"> X</a>\n'
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
    '🔮<a href="{kol_link}">KOL</a>\n'
    '🔮<a href="https://x.com/WizardScan"> X</a>\n'
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
    """
    if pack is None:
        pack = _get_alert_pack()
    chain_key  = (chain or "").upper()
    chain_emoji = pack["chain"].get(chain_key) or pack["chain"].get("SOL")
    crystal    = pack["crystal"]
    teer       = TEER_EMOJI_ID
    # NOTE: kol and bot_em are intentionally excluded — _build_premium_entities
    # special-cases 🔮KOL / 🔮BOT and injects pack.kol / pack.bot directly.
    # Including them here would shift badge emoji IDs into the wrong positions.
    if x_val >= 1000:
        base = [chain_emoji, crystal, crystal, teer]
    else:
        base = [chain_emoji, crystal, teer]
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

def build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol, badge=None):
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    # Priority: 1) specific milestone template  2) owner-defined range template
    #           3) hardcoded tiered range (incl. champion at 10000X)  4) global custom  5) default
    template = (config.get("milestone_templates",{}).get(str(x_val))
                or _get_range_template(x_val, config)
                or _get_tiered_template(x_val)
                or config.get("alert_template")
                or DEFAULT_TEMPLATE)
    kwargs = dict(channel=channel, x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=entry_fmt, current=current_fmt, ca=ca,
                  kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK)
    try:
        text = safe_format(template, **kwargs)
    except Exception:
        text = safe_format(DEFAULT_TEMPLATE, **kwargs)

    # Append 12-hour promo link for 2x-50x alerts
    if 2 <= x_val <= 50:
        promo = config.get("promo_link")
        if promo and promo.get("url") and promo.get("text") and promo.get("set_at"):
            try:
                if datetime.utcnow() - datetime.fromisoformat(promo["set_at"]) < timedelta(hours=12):
                    text += f'\n\n<a href="{promo["url"]}">{html.escape(promo["text"])}</a>'
            except Exception:
                pass

    # Append Leaderboard / Champions badge
    if badge is None:
        badge = _get_kol_badge(channel)
    if badge:
        if badge["type"] == "leaderboard":
            text += f'\n\n🔮 <b><a href="https://t.me/WizardScan/136">LEADERBOARD KOL</a></b> 🔮'
        else:
            text += f'\n\n🔮 <b><a href="https://t.me/WizardScan/137">CHAMPION KOL</a></b> 🔮'

    # Append owner-configured extra post links
    extra_links = config.get("extra_post_links", [])
    for lnk in extra_links:
        lnk_text = html.escape(lnk.get("text", ""))
        lnk_url  = lnk.get("url", "")
        if lnk_text and lnk_url:
            text += f'\n🔮<a href="{lnk_url}">{lnk_text}</a>'

    return text

def build_x_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol):
    """Build alert text for the X/Twitter channel (@WizardscanX)."""
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    template = config.get("x_alert_template") or DEFAULT_X_TEMPLATE
    kwargs = dict(channel=channel, x=x_val, symbol=(symbol or "TOKEN").upper(), chain=chain,
                  entry=entry_fmt, current=current_fmt, ca=ca,
                  kol_link=kol_link, tg_link=TG_CHANNEL_LINK, bot_link=BOT_LINK)
    try:
        return safe_format(template, **kwargs)
    except Exception:
        return safe_format(DEFAULT_X_TEMPLATE, **kwargs)

def build_milestone_keyboard(x_val):
    buttons = load_config().get("milestone_buttons",{}).get(str(x_val),[])
    if not buttons: return None
    rows = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in buttons if b.get("text") and b.get("url")]
    return InlineKeyboardMarkup(rows) if rows else None

def _build_premium_entities(plain_text, base_entities, emoji_ids):
    """Replace each 🔮 in plain_text with the next emoji_id from the list.
    emoji_ids may be a single str/int or a list. Falls back to last id when list runs out.

    Special-case: a 🔮 immediately followed by "KOL" or "BOT" (no space — matches the
    '🔮KOL'/'🔮BOT' link placeholders used by alert templates) always gets the current
    pack's dedicated kol/bot emoji, regardless of where it falls in the emoji_ids list.
    This keeps KOL/BOT premium emoji correct even in custom/tiered templates that don't
    include the same number/order of 🔮 placeholders as the default template."""
    from telethon.tl.types import MessageEntityCustomEmoji
    if not isinstance(emoji_ids, list):
        emoji_ids = [emoji_ids]
    emoji_ids = [e for e in emoji_ids if e]
    pack = _get_alert_pack()
    kol_id = pack.get("kol")
    bot_id = pack.get("bot")
    x_id   = pack.get("x") or 0
    if not emoji_ids and not kol_id and not bot_id and not x_id:
        return base_entities or []
    custom_entities = []
    pos_index = 0  # which emoji in the list to use next (for non-KOL/BOT placeholders)
    utf16_off = 0
    i = 0
    PLACEHOLDER = '🔮'
    PH_LEN = len(PLACEHOLDER)
    emoji_u16 = len(PLACEHOLDER.encode('utf-16-le')) // 2
    while i < len(plain_text):
        if plain_text[i:i+PH_LEN] == PLACEHOLDER:
            if plain_text[i+PH_LEN:i+PH_LEN+3] == 'KOL' and kol_id:
                eid = kol_id
            elif plain_text[i+PH_LEN:i+PH_LEN+3] == 'BOT' and bot_id:
                eid = bot_id
            elif plain_text[i+PH_LEN:i+PH_LEN+2] == ' X' and x_id:
                eid = x_id
            elif emoji_ids:
                eid = emoji_ids[min(pos_index, len(emoji_ids)-1)]
                pos_index += 1
            else:
                eid = None
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

async def _userbot_send_with_premium_emoji(chat, text, emoji_id=None, link_preview=False):
    """Send via userbot. Returns sent Message object on success, None on failure."""
    if not userbot_client:
        return None
    try:
        if emoji_id:
            try:
                from telethon.extensions.html import parse as tl_html_parse
                plain_text, base_entities = tl_html_parse(text)
                all_entities = _build_premium_entities(plain_text, base_entities, emoji_id)
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

async def _userbot_edit_with_premium_emoji(chat, msg_id, text, emoji_ids_for_ranking=None):
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
                await userbot_client.edit_message(chat, msg_id, plain_text,
                                                   formatting_entities=all_entities,
                                                   link_preview=False)
                return True
            except Exception as e:
                logger.warning(f"Premium emoji edit failed, fallback: {e}")
        await userbot_client.edit_message(chat, msg_id, text, parse_mode="html", link_preview=False)
        return True
    except Exception as e:
        logger.error(f"userbot edit {msg_id}: {e}")
        return False

async def _userbot_edit_caption_with_premium_emoji(chat, msg_id, text, emoji_id):
    """Edit a media message's caption via userbot. emoji_id = single id or list (one per 🔮)."""
    if not userbot_client or not emoji_id:
        logger.warning(f"Skipping emoji edit: userbot={userbot_client is not None}, emoji_id={emoji_id}")
        return False
    try:
        from telethon.extensions.html import parse as tl_html_parse
        plain_text, base_entities = tl_html_parse(text)
        all_entities = _build_premium_entities(plain_text, base_entities, emoji_id)
        logger.info(f"Userbot editing msg {msg_id} with emoji_id={emoji_id}, entities={len(all_entities)}")
        await userbot_client.edit_message(
            chat, msg_id, plain_text,
            formatting_entities=all_entities,
            link_preview=False
        )
        logger.info(f"✅ Userbot emoji edit SUCCESS for msg {msg_id}")
        return True
    except Exception as e:
        logger.warning(f"Caption premium emoji edit FAILED: {e}")
        return False

async def _userbot_send_media_with_emoji(bot_app, chat, file_id, file_type, text, emoji_id, keyboard=None):
    """Download file via bot API, then send via userbot with premium emoji — NO edit = NO pencil mark."""
    import tempfile, os
    tmp_path = None
    try:
        tg_file = await bot_app.get_file(file_id)
        suffix = '.mp4' if file_type == 'video' else '.jpg'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            tmp_path = f.name
        await tg_file.download_to_drive(tmp_path)

        from telethon.extensions.html import parse as tl_html_parse
        plain_text, base_ents = tl_html_parse(text)
        all_entities = _build_premium_entities(plain_text, base_ents, emoji_id)

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
            chat, tmp_path,
            caption=plain_text,
            formatting_entities=all_entities,
            buttons=tl_buttons,
            supports_streaming=True
        )
        logger.info(f"✅ Userbot media send SUCCESS (no pencil) file_type={file_type}")
        return msg
    except Exception as e:
        logger.error(f"Userbot media send failed: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass

def _get_chain_emoji(chain, config=None):
    """Return chain-specific premium emoji ID, or None."""
    if config is None:
        config = load_config()
    chain_emoji_ids = config.get("chain_emoji_ids", {})
    # Normalize chain key: SOL→sol, ETH→eth, BNB→bsc (user uses 'bsc'), BASE→base
    key_map = {"SOL": "sol", "ETH": "eth", "BNB": "bsc", "BSC": "bsc", "BASE": "base"}
    key = key_map.get(chain.upper(), chain.lower())
    return chain_emoji_ids.get(key)


def build_dropped_alert(channel, msg_id, ca, chain, entry_fmt, symbol):
    """Build the 'Dropped a Call' post text."""
    kol_link = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/{channel}"
    config   = load_config()
    template = config.get("dropped_call_template") or DEFAULT_DROPPED_TEMPLATE
    kwargs   = dict(
        channel=html.escape(channel), symbol=(symbol or "TOKEN").upper(),
        chain=chain, entry=entry_fmt, ca=ca,
        kol_link=kol_link, bot_link=BOT_LINK
    )
    try:
        return safe_format(template, **kwargs)
    except Exception:
        return safe_format(DEFAULT_DROPPED_TEMPLATE, **kwargs)


async def send_dropped_alert(bot, channel, msg_id, ca, chain, entry_fmt, symbol):
    """Post a 'Dropped a Call' alert to the main channel when a new call is first tracked."""
    try:
        text = build_dropped_alert(channel, msg_id, ca, chain, entry_fmt, symbol)
        # Premium emoji IDs: two header emojis (DROPPED_CALL_EMOJI) for the 🔮 before/after title.
        # KOL / X / BOT emojis are injected by _build_premium_entities special-casing.
        emoji_ids = [DROPPED_CALL_EMOJI, DROPPED_CALL_EMOJI]

        # Rotating video (up to 20 — round robin)
        config    = load_config()
        vids      = config.get("dropped_videos", [])
        media     = None
        if vids:
            idx   = config.get("dropped_video_index", 0) % len(vids)
            media = vids[idx]
            cfg_set("dropped_video_index", (idx + 1) % len(vids))

        posted = False
        if userbot_client:
            if media and media.get("file_id"):
                fid, ftype = media["file_id"], media.get("type", "video")
                sent_msg = await _userbot_send_media_with_emoji(
                    bot, TARGET_CHANNEL, fid, ftype, text, emoji_ids, None)
                if sent_msg:
                    posted = True
            if not posted:
                sent_msg = await _userbot_send_with_premium_emoji(
                    TARGET_CHANNEL, text, emoji_id=emoji_ids)
                if sent_msg:
                    posted = True

        if not posted:
            if media and media.get("file_id"):
                fid, ftype = media["file_id"], media.get("type", "video")
                try:
                    if ftype == "photo":
                        await bot.send_photo(TARGET_CHANNEL, photo=fid, caption=text, parse_mode="HTML")
                    else:
                        await bot.send_video(TARGET_CHANNEL, video=fid, caption=text, parse_mode="HTML")
                    posted = True
                except Exception as e:
                    logger.error(f"Dropped alert media send failed: {e}")
            if not posted:
                try:
                    await bot.send_message(TARGET_CHANNEL, text,
                                           parse_mode="HTML", disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"Dropped alert text send failed: {e}")
    except Exception as e:
        logger.error(f"send_dropped_alert crash: {e}")


async def send_alert(bot, channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol):
    config   = load_config()
    # Only send alert if a video/photo is set for this specific X value (or global)
    media    = config.get("milestone_media",{}).get(str(x_val)) or config.get("milestone_media",{}).get("global")
    if not media:
        logger.info(f"⏭ Skip alert {x_val}x @{channel} — no media set for this X value")
        return
    # Compute badge once, pass to build_alert + emoji list
    badge    = _get_kol_badge(channel)
    text     = build_alert(channel, msg_id, x_val, chain, entry_fmt, current_fmt, ca, symbol, badge=badge)
    keyboard = build_milestone_keyboard(x_val)
    # Pack-based premium emojis (rotate every 10 posts, 5 packs total)
    pack     = _get_alert_pack()
    emoji_id = list(_get_alert_emoji_ids(x_val, chain, pack))
    # Append badge premium emoji IDs
    if badge:
        if badge["type"] == "leaderboard":
            rank_emoji_id = LEADERBOARD_PREMIUM_EMOJIS.get(badge["rank"], LEADERBOARD_PREMIUM_EMOJIS[1])
            emoji_id += [LEADERBOARD_PREMIUM_EMOJIS["star"], rank_emoji_id]
        else:
            rank_emoji_id = CHAMPIONS_PREMIUM_EMOJIS.get(badge["rank"], CHAMPIONS_PREMIUM_EMOJIS[1])
            emoji_id += [CHAMPIONS_PREMIUM_EMOJIS["star"], rank_emoji_id]
    logger.info(f"Alert emoji_id for {x_val}x chain={chain} pack={pack['name']}: {emoji_id} | userbot={userbot_client is not None}")

    # Main channel — prefer userbot (premium account) so emojis work
    posted = False
    wizard_post_id = None   # WizardScan channel message ID (for /trendingKols link)

    call_key = f"{channel}_{ca}" if ca else None

    if userbot_client:
        if media and media.get("file_id"):
            fid, ftype = media["file_id"], media.get("type", "photo")
            if emoji_id:
                sent_msg = await _userbot_send_media_with_emoji(
                    bot, TARGET_CHANNEL, fid, ftype, text, emoji_id, keyboard)
                if sent_msg:
                    posted = True
                    try: wizard_post_id = sent_msg.id
                    except Exception: pass
            if not posted:
                try:
                    if ftype == "photo":
                        sent_msg = await bot.send_photo(TARGET_CHANNEL, photo=fid, caption=text, parse_mode="HTML", reply_markup=keyboard)
                    else:
                        sent_msg = await bot.send_video(TARGET_CHANNEL, video=fid, caption=text, parse_mode="HTML", reply_markup=keyboard)
                    posted = True
                    try: wizard_post_id = sent_msg.message_id
                    except Exception: pass
                except Exception as e:
                    logger.error(f"Media alert via bot failed: {e}")
        if not posted:
            sent_msg = await _userbot_send_with_premium_emoji(TARGET_CHANNEL, text, emoji_id=emoji_id)
            if sent_msg:
                posted = True
                try: wizard_post_id = sent_msg.id
                except Exception: pass
    if not posted:
        if media and media.get("file_id"):
            fid, ftype = media["file_id"], media.get("type","photo")
            try:
                if ftype == "photo":
                    sent_msg = await bot.send_photo(TARGET_CHANNEL, photo=fid, caption=text, parse_mode="HTML", reply_markup=keyboard)
                else:
                    sent_msg = await bot.send_video(TARGET_CHANNEL, video=fid, caption=text, parse_mode="HTML", reply_markup=keyboard)
                posted = True
                try: wizard_post_id = sent_msg.message_id
                except Exception: pass
            except Exception as e:
                logger.error(f"Media alert failed: {e}")
        if not posted:
            try:
                sent_msg = await bot.send_message(
                    chat_id=TARGET_CHANNEL, text=text,
                    parse_mode="HTML", disable_web_page_preview=True,
                    reply_markup=keyboard
                )
                try: wizard_post_id = sent_msg.message_id
                except Exception: pass
            except Exception as e:
                logger.error(f"Text alert to channel failed: {e}")

    # Save WizardScan post ID for this milestone
    if wizard_post_id and call_key:
        if call_key not in milestone_posts:
            milestone_posts[call_key] = {}
        milestone_posts[call_key][str(x_val)] = wizard_post_id
        _save_milestone_posts()

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

    # Forward WizardScan post to linked channel + DM KOL owner (preserves premium emojis)
    if wizard_post_id:
        for kol_ch, linked_ch in load_linked_channels().items():
            if kol_ch.lower() == channel.lower():
                try:
                    await bot.forward_message(
                        chat_id=linked_ch,
                        from_chat_id=TARGET_CHANNEL,
                        message_id=wizard_post_id)
                except Exception as e:
                    logger.warning(f"Forward to linked ch {linked_ch} failed: {e}")
                    try: await _text_alert(bot, linked_ch, text, keyboard)
                    except Exception: pass
        # DM KOL owner
        kol_owners = load_kol_owners()
        owner_id = kol_owners.get(channel.lower())
        if owner_id:
            try:
                await bot.forward_message(
                    chat_id=owner_id,
                    from_chat_id=TARGET_CHANNEL,
                    message_id=wizard_post_id)
            except Exception as e:
                logger.warning(f"Forward DM to KOL owner {owner_id} failed: {e}")
    else:
        # Fallback if wizard_post_id missing: text alert to linked channels only
        for kol_ch, linked_ch in load_linked_channels().items():
            if kol_ch.lower() == channel.lower():
                try: await _text_alert(bot, linked_ch, text, keyboard)
                except Exception as e: logger.warning(f"Text alert to linked ch failed ({linked_ch}): {e}")

    # DM channel-specific subscribers — forward WizardScan post (no premium emoji issues)
    ch_subs = load_channel_subs().get(channel.lower(), [])
    for uid in ch_subs:
        try:
            if wizard_post_id:
                try:
                    await bot.forward_message(
                        chat_id=uid,
                        from_chat_id=TARGET_CHANNEL,
                        message_id=wizard_post_id)
                    continue
                except Exception:
                    pass
            # Fallback if forward fails
            await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"DM subscriber {uid} failed: {e}")

    # Legacy global subscribers — forward WizardScan post (no premium emoji issues)
    for uid in load_subscriptions():
        try:
            if wizard_post_id:
                try:
                    await bot.forward_message(
                        chat_id=uid,
                        from_chat_id=TARGET_CHANNEL,
                        message_id=wizard_post_id)
                    continue
                except Exception:
                    pass
            await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception: pass

async def _text_alert(bot, chat_id, text, keyboard):
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=keyboard)
    except Exception as e: logger.error(f"Text alert ({chat_id}): {e}")

# ─── History helpers ──────────────────────────────────────────────────────────
def get_call_history(channel, chain_filter=None, top=False):
    calls = []
    cutoff = datetime.utcnow() - timedelta(days=90)
    for call_key, call in tracked_calls.items():
        if call.get("channel","").lower() != channel.lower(): continue
        if chain_filter and call.get("chain","").upper() != chain_filter.upper(): continue
        if not top:
            ts = call.get("tracked_since","")
            if ts:
                try:
                    if datetime.fromisoformat(ts) < cutoff: continue
                except Exception: pass
        milestones = list(sent_milestones.get(call_key, set()))
        highest_x  = max(milestones) if milestones else 0
        entry_mc   = call.get("entry_mc", 0)
        # Use live ratio for decimal display; floor at highest milestone so crashes don't hide peaks
        last_ratio  = call.get("last_ratio", 0.0)
        current_x   = round(max(last_ratio, float(highest_x) if highest_x > 0 else 1.0), 2)
        if current_x < 1.0: current_x = 1.0
        current_mc  = entry_mc * current_x if entry_mc > 0 else entry_mc
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
    if top:
        calls.sort(key=lambda x: x["highest_x"], reverse=True)
        return calls[:50]
    calls.sort(key=lambda x: x.get("tracked_since",""), reverse=True)
    return calls

def format_history(channel, calls, is_top=False):
    ch_safe = html.escape(channel)
    if not calls:
        return (
            f"<b>🔮 Calls History Of @{ch_safe}</b>\n\n"
            "<i>No calls found for this filter.</i>"
        )
    label = "Top 30 Calls" if is_top else "Calls History"
    lines = [f"<b>🔮 {label} Of @{ch_safe}</b>\n"]
    shown = 0
    for call in calls[:30]:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = html.escape(call["chain"])
        ca     = call["ca"]
        ef     = html.escape(call["entry_fmt"])
        hf     = html.escape(call["highest_mc_fmt"])
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        # Build post link (t.me/{channel}/{msg_id})
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">🪄 View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        # X string — real decimal (e.g. 2.7x, 15.3x)
        x_label = f" {fmt_x(cx)}"
        lines.append(
            f'\n🔮 <b>${sym} ({chain}){x_label}</b>\n'
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
    ch_safe = html.escape(channel)
    header  = f"<b>🔮 Calls History Of @{ch_safe}</b>\n"
    body    = ""
    for call in calls[:30]:
        sym    = html.escape((call["symbol"] or "TOKEN").upper())
        chain  = html.escape(call["chain"])
        ca     = call["ca"]
        ef     = html.escape(call["entry_fmt"])
        hf     = html.escape(call["highest_mc_fmt"])
        cx     = call.get("current_x", call["highest_x"])
        msg_id = call.get("msg_id", 0)
        if msg_id:
            post_link = f'<a href="https://t.me/{channel}/{msg_id}">🪄 View Post</a>'
        else:
            path = CHAIN_TO_DEXPATH.get(call["chain"], "ethereum")
            post_link = f'<a href="https://dexscreener.com/{path}/{ca}">📊 Chart</a>'
        x_label = f" {fmt_x(cx)}"
        entry = (
            f'\n\n🔮 <b>${sym} ({chain}){x_label}</b>\n'
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
    channels = load_channels()

    # Build champion set to exclude
    pts_data = load_channel_points()
    champ_candidates = sorted(
        [(ch, pts_data.get(ch.lower(), {}).get("points", 0)) for ch in channels
         if pts_data.get(ch.lower(), {}).get("points", 0) >= POINTS_FOR_CHAMPION],
        key=lambda x: x[1], reverse=True
    )
    champion_set = {ch.lower() for ch, _ in champ_candidates[:10]}

    # Leaderboard reset filter — only count calls tracked AFTER last manual reset
    cfg_now = load_config()
    lb_reset_since = cfg_now.get("leaderboard_reset_since", "")
    lb_reset_dt = None
    if lb_reset_since:
        try: lb_reset_dt = datetime.fromisoformat(lb_reset_since)
        except Exception: lb_reset_dt = None

    all_scores = {}
    for ch in channels:
        if ch.lower() in champion_set:
            continue   # already a Champion — skip from Leaderboard
        best_x = 1
        best_call_key = None
        for call_key, call in tracked_calls.items():
            if call.get("channel","").lower() != ch.lower(): continue
            # Skip calls tracked before the last manual reset
            if lb_reset_dt:
                try:
                    ts = datetime.fromisoformat(call.get("tracked_since", ""))
                    if ts < lb_reset_dt: continue
                except Exception: pass
            ms = list(sent_milestones.get(call_key, set()))
            if ms:
                mx = max(ms)
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
        # Use live ratio for decimal display; floor at highest milestone so crashes don't hide peaks
        live_ratio = tracked_calls.get(best_call_key, {}).get("last_ratio", 0.0) if best_call_key else 0.0
        peak_x_float = round(max(float(best_x), live_ratio), 2)
        # Only include channels that have reached at least 2x
        if peak_x_float < 2.0:
            continue
        all_scores[ch] = (peak_x_float, wizard_post_id)

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
        pts_data = load_channel_points()
        channels = load_channels()
        champ_list = []
        for ch in channels:
            pts = pts_data.get(ch.lower(), {}).get("points", 0)
            if pts >= POINTS_FOR_CHAMPION:
                champ_list.append((ch, pts))
        champ_list.sort(key=lambda x: x[1], reverse=True)
        for i, (ch, pts) in enumerate(champ_list[:10]):
            if ch.lower() == channel.lower():
                bx = 1; bck = None
                for ck, call in tracked_calls.items():
                    if call.get("channel","").lower() != ch.lower(): continue
                    ms = list(sent_milestones.get(ck, set()))
                    if ms:
                        mx = max(ms)
                        if mx > bx: bx = mx; bck = ck
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
                        mx = max(ms)
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
                kwargs[f"rank{idx}_link"]    = f'<a href="https://t.me/{ch}">@{html.escape(ch)}</a>'
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
        ch_link = f'<a href="https://t.me/{ch}">@{html.escape(ch)}</a>'
        x_str   = fmt_x(x)
        if wpost:
            x_part = f'<a href="https://t.me/WizardScan/{wpost}">{x_str}</a>'
        else:
            x_part = x_str
        lines.append(f"🔮 <b>{ch_link}</b> 🔮 {x_part}")

    if not lines:
        return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
                "<i>No data yet. Tracking in progress...</i>")

    # Pad to 10 slots with dashes for empty ranks
    for i in range(len(lines), 10):
        lines.append(f"🔮 <b>—</b> 🔮 —")

    return ("🔮 <b>LEADERBOARD KOLS:</b>\n\n"
            + "\n".join(lines))

def build_champions_text():
    """Build champion kols list for post 137.
    Only channels with >= POINTS_FOR_CHAMPION (100) points qualify.
    Sorted by points descending. Uses 🔮 placeholders for premium emojis."""
    channels = load_channels()
    pts_data = load_channel_points()
    champions = []
    for ch in channels:
        pts = pts_data.get(ch.lower(), {}).get("points", 0)
        if pts >= POINTS_FOR_CHAMPION:
            # Find best wizard post ID for this channel
            best_x = 1
            best_call_key = None
            for call_key, call in tracked_calls.items():
                if call.get("channel","").lower() != ch.lower(): continue
                ms = list(sent_milestones.get(call_key, set()))
                if ms:
                    mx = max(ms)
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
            champions.append((ch, pts, best_x, wizard_post_id))
    # Sort by points descending
    champions.sort(key=lambda x: x[1], reverse=True)

    lines = []
    for i, (ch, pts, best_x, wpost) in enumerate(champions[:10]):
        ch_link = f'<a href="https://t.me/{ch}">@{html.escape(ch)}</a>'
        # Use live ratio if higher than highest milestone
        live_r = 0.0
        for ck, call in tracked_calls.items():
            if call.get("channel","").lower() != ch.lower(): continue
            ms = list(sent_milestones.get(ck, set()))
            if ms and max(ms) == best_x:
                live_r = call.get("last_ratio", 0.0)
                break
        display_x = fmt_x(round(max(float(best_x), live_r), 2))
        if wpost:
            x_part = f'<a href="https://t.me/WizardScan/{wpost}">{display_x}</a>'
        else:
            x_part = display_x
        lines.append(f"🔮 <b>{ch_link}</b> 🔮 {x_part}")

    # Pad to 10 slots with dashes for empty ranks
    for i in range(len(lines), 10):
        lines.append(f"🔮 <b>—</b> 🔮 —")

    footer = ("\n\n🔮 KOLs need 100 points to appear here.")

    if not lines:
        return ("🔮 <b>CHAMPION KOLS</b>\n\n"
                "<i>No channels have reached 100 points yet.</i>"
                + footer)

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

# ─── Monitoring job ───────────────────────────────────────────────────────────
async def monitoring_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        bot      = context.bot
        channels = load_channels()

        # Scan channels for new calls
        for channel in channels:
            try:
                posts = await fetch_channel_posts(channel)
                for post in posts:
                    msg_id = post["id"]; text = post["text"]
                    if msg_id in seen_message_ids[channel]: continue
                    seen_message_ids[channel].add(msg_id)
                    if not is_call_message(text): continue
                    result = extract_ca(text)
                    if not result: continue
                    _, ca = result
                    call_key = f"{channel}_{ca}"
                    if call_key in tracked_calls: continue
                    dex = await fetch_dexscreener(ca)
                    if not dex or not dex.get("mcap"): continue
                    tracked_calls[call_key] = {
                        "channel": channel, "msg_id": msg_id, "ca": ca,
                        "chain": dex["chain"], "entry_mc": dex["mcap"],
                        "entry_price": dex.get("price", 0),
                        "entry_fmt": dex["mcap_fmt"], "symbol": dex.get("symbol",""),
                        "tracked_since": datetime.utcnow().isoformat(),
                    }
                    logger.info(f"📌 {dex.get('symbol','?')} @{channel} {dex['chain']} {dex['mcap_fmt']}")
                    _save_tracked(); _save_seen()
                    # Post "Dropped a Call" alert immediately
                    asyncio.create_task(send_dropped_alert(
                        bot, channel, msg_id, ca,
                        dex["chain"], dex["mcap_fmt"], dex.get("symbol","")))
            except Exception as e:
                logger.error(f"Scan error @{channel}: {e}")
            await asyncio.sleep(1)

        # Check milestones
        items = list(tracked_calls.items())
        async def check_one(call_key, call):
            try:
                if call.get("frozen"): return []   # owner froze this call — skip alerts
                try:
                    dex = await asyncio.wait_for(fetch_dexscreener(call["ca"]), timeout=15)
                except asyncio.TimeoutError:
                    logger.warning(f"DexScreener timeout: {call['ca'][:12]}...")
                    return []
                if not dex: return []
                cur_price = dex.get("price", 0)
                cur_mc    = dex.get("mcap", 0)
                entry_price = call.get("entry_price", 0)
                entry_mc    = call.get("entry_mc", 0)
                # Prefer price-based ratio (more accurate — not affected by supply changes)
                if entry_price > 0 and cur_price > 0:
                    ratio = cur_price / entry_price
                elif entry_mc > 0 and cur_mc > 0:
                    ratio = cur_mc / entry_mc
                else:
                    return []
                # Save live ratio so history shows real decimal X (e.g. 2.7x, 15.3x)
                call["last_ratio"] = round(ratio, 4)

                # Calculate per-milestone market cap: entry_mc × milestone
                # This ensures 200x alert shows entry×200, not same live MC for all milestones
                triggered = []
                for ms in get_milestones():
                    if ms > MAX_MILESTONE: continue  # no alerts beyond 10000X
                    if ratio >= ms and ms not in sent_milestones[call_key]:
                        # Use calculated MC for consistency with X value shown in post
                        if entry_mc > 0:
                            ms_mc_fmt = fmt_mc(entry_mc * ms)
                        else:
                            ms_mc_fmt = dex.get("mcap_fmt", "N/A")
                        triggered.append((call_key, call, ms, ms_mc_fmt))
                return triggered
            except Exception: return []

        all_hits = []
        for i in range(0, len(items), 10):
            results = await asyncio.gather(*[check_one(k, v) for k, v in items[i:i+10]])
            for hits in results: all_hits.extend(hits)
            if i + 10 < len(items): await asyncio.sleep(0.5)

        # Persist updated last_ratio values for decimal X history display
        _save_tracked()

        for call_key, call, ms, cur_fmt in all_hits:
            sent_milestones[call_key].add(ms); _save_milestones()
            logger.info(f"🚀 {call.get('symbol','?')} @{call['channel']} {ms}X!")
            await send_alert(bot, call["channel"], call["msg_id"], ms,
                             call["chain"], call["entry_fmt"], cur_fmt, call["ca"], call["symbol"])
            await asyncio.sleep(0.5)

        # Check for failed calls: tracked > CALL_FAIL_DAYS days without hitting 2X → deduct points
        cutoff_fail = datetime.utcnow() - timedelta(days=CALL_FAIL_DAYS)
        for call_key, call in list(tracked_calls.items()):
            milestones_hit = sent_milestones.get(call_key, set())
            if 2 in milestones_hit:
                continue  # Already hit 2X, no deduction
            ts_str = call.get("tracked_since", "")
            if not ts_str:
                continue
            try:
                tracked_dt = datetime.fromisoformat(ts_str)
                if tracked_dt < cutoff_fail:
                    await deduct_points_for_failed_call(call["channel"], call_key)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"monitoring_job crash: {e}")

# ─── Leaderboard auto-update job ─────────────────────────────────────────────
async def _update_leaderboard_with_premium_emojis(bot):
    """Edit post 136 with leaderboard data + all premium emojis via userbot."""
    global userbot_client
    text    = build_leaderboard_text()
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
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids)
            await userbot_client.edit_message(
                TARGET_CHANNEL, POST_LEADERBOARD, plain_text,
                formatting_entities=all_entities,
                link_preview=False
            )
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
            if now - last_dt < timedelta(minutes=2): return
    except Exception: pass
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_leaderboard_update", now.isoformat())
    except Exception as e:
        logger.error(f"Leaderboard update failed: {e}")

async def _update_champions_with_premium_emojis(bot):
    """Edit post 137 with champions data + all premium emojis via userbot."""
    global userbot_client
    text     = build_champions_text()
    # Count 🔮 in text to build emoji_ids list
    # Order: star(header), then per row: num+arrow, then star(footer)
    # Champions are now points-based (>=100 points) — match build_champions_text() logic
    pts_data = load_channel_points()
    channels = load_channels()
    champ_count = sum(
        1 for ch in channels
        if pts_data.get(ch.lower(), {}).get("points", 0) >= POINTS_FOR_CHAMPION
    )
    n = min(champ_count, 10)

    emoji_ids = [CHAMPIONS_PREMIUM_EMOJIS["star"]]  # header star
    for i in range(1, n + 1):
        emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS.get(i, CHAMPIONS_PREMIUM_EMOJIS[1]))
        emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["arrow"])
    emoji_ids.append(CHAMPIONS_PREMIUM_EMOJIS["star"])  # footer star

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
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids)
            await userbot_client.edit_message(
                TARGET_CHANNEL, POST_CHAMPIONS, plain_text,
                formatting_entities=all_entities, link_preview=False
            )
            logger.info("✅ Champions post 137 updated with premium emojis")
            return True
        except Exception as e:
            logger.error(f"Champions premium emoji edit failed: {e}")
            # Do NOT fall back to bot API — it would strip premium emojis from the post.
            return False

    # Userbot unavailable — skip update to preserve existing premium emojis on the post.
    logger.warning("Champions update skipped: userbot unavailable (preserving premium emojis).")
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
            if now - last_dt < timedelta(minutes=2): return
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
    text, emoji_ids     = build_trending_text_and_emojis(chain_tokens)

    if userbot_client:
        try:
            from telethon.extensions.html import parse as tl_html_parse
            plain_text, base_entities = tl_html_parse(text)
            all_entities = _build_premium_entities(plain_text, base_entities, emoji_ids)
            await userbot_client.edit_message(
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

async def trending_job(context: ContextTypes.DEFAULT_TYPE):
    """Update post 135 (trending) every 1-2 minutes."""
    config   = load_config()
    last_upd = config.get("last_trending_update","")
    try:
        if last_upd:
            last_dt = datetime.fromisoformat(last_upd)
            if datetime.utcnow() - last_dt < timedelta(minutes=2): return
    except Exception: pass
    try:
        ok = await _update_trending_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_trending_update", datetime.utcnow().isoformat())
    except Exception as e:
        logger.error(f"Trending update failed: {e}")

async def momentum_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Daily check: if any KOL channel delivered 2+ calls to same X in last 7 days, post MOMENTUM ACTIVE."""
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
                for x in sent_milestones.get(call_key, set()):
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
                        text += f'\n\n🔮 <b><a href="https://t.me/WizardScan/136">LEADERBOARD KOL</a></b> 🔮'
                        momentum_emoji_ids = momentum_emoji_ids + [LEADERBOARD_PREMIUM_EMOJIS["star"], m_rank_id]
                    else:
                        m_rank_id = CHAMPIONS_PREMIUM_EMOJIS.get(m_badge["rank"], CHAMPIONS_PREMIUM_EMOJIS[1])
                        text += f'\n\n🔮 <b><a href="https://t.me/WizardScan/137">CHAMPION KOL</a></b> 🔮'
                        momentum_emoji_ids = momentum_emoji_ids + [CHAMPIONS_PREMIUM_EMOJIS["star"], m_rank_id]

                # KOL Signal buttons — 2 per row
                signal_buttons = []
                row = []
                for ck, _ in calls[:10]:
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
                    row.append(InlineKeyboardButton("🔮 KOL Signal", url=btn_url))
                    if len(row) == 2:
                        signal_buttons.append(row); row = []
                if row:
                    signal_buttons.append(row)
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

# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def _show_xray_report(update: Update, channel: str, x_val: int):
    """Send X-Ray Report for a channel's x_val milestone — called from deep link."""
    week_ago = datetime.utcnow() - timedelta(days=7)
    results  = []
    for call_key, call in tracked_calls.items():
        if call.get("channel","").lower() != channel.lower(): continue
        if x_val not in sent_milestones.get(call_key, set()): continue
        ts_str = call.get("tracked_since","")
        try:
            if datetime.fromisoformat(ts_str) < week_ago: continue
        except Exception: pass
        post_id   = milestone_posts.get(call_key, {}).get(str(x_val))
        entry_mc  = call.get("entry_mc", 0)
        ms_mc_fmt = fmt_mc(entry_mc * x_val) if entry_mc > 0 else call.get("entry_fmt","?")
        results.append({
            "symbol":  call.get("symbol","TOKEN"),
            "chain":   call.get("chain",""),
            "entry":   call.get("entry_fmt","?"),
            "ms_mc":   ms_mc_fmt,
            "ca":      call.get("ca",""),
            "post_id": post_id,
        })

    if not results:
        await update.message.reply_text(
            f"<b>🔮 X-Ray Report of @{channel}</b>\n\n"
            f"No {x_val}X calls found in the last 7 days.",
            parse_mode="HTML"
        )
        return

    lines = [f"<b>🔮 X-Ray Report of @{channel}</b>\n"]
    for r in results:
        view_link  = (f'<a href="https://t.me/WizardScan/{r["post_id"]}">View Post</a>'
                      if r["post_id"] else "View Post")
        ms_mc = r.get("ms_mc") or r["entry"]
        lines.append(
            f"<b>🔮 ${r['symbol']} ({r['chain']}) {x_val}X</b>\n"
            f"     🪄 {view_link}  |  {r['entry']} ➤ {ms_mc}"
        )
    # Footer added at the end of every X-Ray Report
    XRAY_FOOTER = (
        "\n\n\n🔮 Early calls. Real results. The track record keeps growing with every successful pick. "
        "Stay early, stay informed. DYOR • NFA"
    )
    caption = "\n\n".join(lines) + XRAY_FOOTER
    # Telegram video caption limit = 1024 chars; text message limit = 4096
    CAPTION_LIMIT = 1024
    sent_with_media = False
    if len(caption) <= CAPTION_LIMIT:
        # 1) Try owner-uploaded X-Ray rotating videos first
        xray_vids = load_config().get("xray_videos", [])
        if xray_vids:
            ctr  = load_config().get("xray_video_counter", 0)
            vid  = xray_vids[ctr % len(xray_vids)]
            next_ctr = (ctr + 1) % len(xray_vids)
            cfg_set("xray_video_counter", next_ctr)
            try:
                await update.message.reply_video(
                    video=vid["file_id"], caption=caption, parse_mode="HTML"
                )
                sent_with_media = True
            except Exception as e:
                logger.warning(f"xray_video send failed: {e}")
        # 2) Fall back to built-in file if it exists
        if not sent_with_media and os.path.exists(VID_XRAY):
            try:
                with open(VID_XRAY, "rb") as vf:
                    await update.message.reply_video(video=vf, caption=caption, parse_mode="HTML")
                sent_with_media = True
            except Exception as e:
                logger.warning(f"VID_XRAY send failed: {e}")
    if not sent_with_media:
        # Caption too long or no video — send as text message (4096 limit, much safer)
        await update.message.reply_text(
            caption, parse_mode="HTML", disable_web_page_preview=True
        )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; add_user(user.id, user.username, user.first_name)

    # Handle deep links (e.g. /start xray_channel_100)
    if context.args:
        arg = context.args[0]
        if arg.startswith("xray_"):
            parts = arg[5:].rsplit("_", 1)  # xray_{channel}_{x}
            if len(parts) == 2:
                try:
                    ch_name = parts[0]
                    x_val   = int(parts[1])
                    await _show_xray_report(update, ch_name, x_val)
                    return
                except Exception: pass

    welcome = cfg_get("start_text", DEFAULT_START_TEXT)
    kb      = InlineKeyboardMarkup([[InlineKeyboardButton("🔮 Command 🔮", callback_data="command_menu")]])
    media   = cfg_get("start_media")
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

async def _send_command_menu(message, context):
    caption  = cfg_get("command_text", DEFAULT_COMMAND_TEXT)
    keyboard = build_command_keyboard()
    media    = cfg_get("command_media")
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
    await _send_command_menu(update.message, context)

async def cmd_xcommand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

async def cmd_xlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dedicated /history @channel command."""
    if not context.args:
        msg = await send_photo_safe(update.message, IMG_HISTORY, DEFAULT_HISTORY_INFO)
        if not msg:
            await update.message.reply_text(DEFAULT_HISTORY_INFO, parse_mode="HTML")
        return
    channel  = context.args[0].lstrip("@").strip()
    channels = [c.lower() for c in load_channels()]
    if channel.lower() in channels:
        calls = get_call_history(channel)
        hist  = format_history(channel, calls)
        kb    = history_keyboard(channel)
        cap   = _build_html_caption_for_video(channel, calls)
        hist_media = cfg_get("history_media")
        sent_media = False
        # 1) Try owner-configured history media (file_id — fast, no file I/O)
        if hist_media and hist_media.get("file_id") and len(cap) <= 1024:
            fid   = hist_media["file_id"]
            ftype = hist_media.get("type", "video")
            try:
                if ftype == "photo":
                    await update.message.reply_photo(photo=fid, caption=cap, parse_mode="HTML", reply_markup=kb)
                else:
                    await update.message.reply_video(video=fid, caption=cap, parse_mode="HTML", reply_markup=kb)
                sent_media = True
            except Exception as e:
                logger.warning(f"history_media send failed: {e}")
        # 2) Fall back to built-in VID_HISTORY file
        if not sent_media and os.path.exists(VID_HISTORY) and len(cap) <= 1024:
            try:
                with open(VID_HISTORY, "rb") as vf:
                    await update.message.reply_video(video=vf, caption=cap, parse_mode="HTML", reply_markup=kb)
                sent_media = True
            except Exception:
                pass
        if not sent_media:
            await update.message.reply_text(hist, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    else:
        await update.message.reply_text(
            f"<b>🔮 @{channel}</b>\n\n"
            f"❌ This channel is not currently tracked by Wizard Scan.\n\n"
            f"To request tracking, use: <code>/submit @{channel}</code>\n\n"
            f"For priority review, contact our team below.",
            parse_mode="HTML", reply_markup=CHAT_US_BUTTON)

async def cmd_linkinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show /linkme usage explanation."""
    msg = await send_photo_safe(update.message, IMG_LINKME, DEFAULT_LINKME_INFO)
    if not msg:
        await update.message.reply_text(DEFAULT_LINKME_INFO, parse_mode="HTML")

async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; add_user(user.id, user.username, user.first_name)
    if not context.args:
        text = btexts.get("kol_request", DEFAULT_KOL_REQUEST)
        kb   = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Fast Track", callback_data="fast_track"),
                                      InlineKeyboardButton("💬 Chat Us",   callback_data="chat_us")]])
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
    try:
        await context.bot.send_message(OWNER_ID,
            f"🔮 <b>New KOL Tracking Request</b>\n\n"
            f"👤 From: @{username} (ID: <code>{user.id}</code>)\n"
            f"📡 Channel: <b>@{channel}</b>\n"
            f"🕐 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            parse_mode="HTML", reply_markup=owner_kb)
    except Exception as e: logger.error(f"Owner notify: {e}")

    await update.message.reply_text(
        f"📨 <b>Request Submitted!</b>\n\n"
        f"Your request to track <b>@{channel}</b> has been sent to our team.\n\n"
        f"⏳ Review may take 1 day to 1 month depending on the queue.\n\n"
        f"🪄 Want faster approval? Use <b>Fast Track</b> — priority review.\n\nUse /command → Fast Track for details.",
        parse_mode="HTML")

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; add_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not context.args:
        text = (
            "If you want alerts from a specific KOL channel to appear here, send:\n\n"
            "<code>/subscribe @channelname</code>\n\n"
            "Example: <code>/subscribe @SomeCryptoKOL</code>\n\n"
            "Send the same command again to unsubscribe."
        )
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
    channel = context.args[0].lstrip("@").lower()
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
        ch_list.append(uid)
        subs[channel] = ch_list
        save_channel_subs(subs)
        await update.message.reply_text(
            f"🔔 <b>Subscribed to @{channel}!</b>\n\n"
            f"Every time this KOL hits a milestone, you'll get the alert here in DM.\n\n"
            f"Send <code>/subscribe @{channel}</code> again to disable.",
            parse_mode="HTML"
        )

async def cmd_linkme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        cm   = cfg_get("command_media", {}).get("linkme")
        sent = False
        if cm and cm.get("file_id"):
            try:
                fn = update.message.reply_photo if cm.get("type") == "photo" else update.message.reply_video
                await fn(**{("photo" if cm.get("type")=="photo" else "video"): cm["file_id"]},
                         caption=DEFAULT_LINKME_INFO, parse_mode="HTML")
                sent = True
            except Exception: pass
        if not sent:
            msg = await send_photo_safe(update.message, IMG_LINKME, DEFAULT_LINKME_INFO)
            if not msg:
                await update.message.reply_text(DEFAULT_LINKME_INFO, parse_mode="HTML")
        return
    kol_ch  = context.args[0].lstrip("@").lower()
    my_ch   = context.args[1].lstrip("@")
    channels = [c.lower() for c in load_channels()]
    if kol_ch not in channels:
        await update.message.reply_text(
            f"⚠️ <b>@{kol_ch}</b> is not currently tracked by Wizard Scan.\n\n"
            f"Only owners of tracked KOL channels can link a destination channel.\n\n"
            f"If you'd like your channel tracked, use: /submit @yourchannel",
            parse_mode="HTML"); return
    linked = load_linked_channels()
    linked[kol_ch] = f"@{my_ch}"; save_linked_channels(linked)
    # Store KOL owner for forward DMs on milestones
    kol_owners = load_kol_owners()
    kol_owners[kol_ch.lower()] = update.effective_user.id
    save_kol_owners(kol_owners)
    await update.message.reply_text(
        f"✅ <b>Channel Linked!</b>\n\n📡 KOL Channel: @{kol_ch}\n📬 Alerts → @{my_ch}\n\n"
        f"⚠️ Make sure @WIZARD_SCAN_BOT is admin in @{my_ch} with post permission.",
        parse_mode="HTML")
    try:
        await context.bot.send_message(OWNER_ID,
            f"🔗 <b>Channel Link</b>\n\nKOL: @{kol_ch} → @{my_ch}\nBy: {update.effective_user.id}",
            parse_mode="HTML")
    except Exception: pass

# ─── Lookup ───────────────────────────────────────────────────────────────────
async def handle_lookup(update: Update, text: str):
    msg = update.message
    # Twitter/X lookup removed

    tg_match = TG_MENTION_RE.match(text.strip())
    if tg_match:
        channel  = tg_match.group(1)
        channels = [c.lower() for c in load_channels()]
        if channel.lower() in channels:
            calls = get_call_history(channel)
            hist  = format_history(channel, calls)
            kb    = history_keyboard(channel)
            if os.path.exists(VID_HISTORY):
                try:
                    cap = _build_html_caption_for_video(channel, calls)
                    with open(VID_HISTORY, "rb") as vf:
                        await msg.reply_video(video=vf, caption=cap, parse_mode="HTML", reply_markup=kb)
                    return True
                except Exception:
                    pass
            await msg.reply_text(hist, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        else:
            await msg.reply_text(
                f"<b>🔮 @{channel}</b>\n\n❌ This channel is not tracked by Wizard Scan.\n\n"
                f"To request tracking: <code>/submit @{channel}</code>\n\nFor priority review, contact our team.",
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
            cap_txt = "<b>🔮 TRACKED KOLs (0)</b>\n\nNo channels tracked yet.\n\n<i>Type /history @channelname to see their call history</i>"
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
                f"{i+1}. <a href='https://t.me/{c}'>@{html.escape(c)}</a>"
                for i, c in enumerate(channels)
            ]
            header1 = f"<b>🔮 TRACKED KOLs ({len(channels)})</b>\n\nHall of Tracked KOLs:\n\n"
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
        try:
            await context.bot.send_message(uid,
                f"🎉 <b>Congratulations!</b>\n\n"
                f"Your KOL channel <b>@{channel}</b> has been successfully listed by Wizard Scan.\n\n"
                f"🔮 Our system will now monitor all calls from your channel 24/7.\n"
                f"🔮 Whenever a call hits a milestone, an alert will be posted in @WizardScan.\n\n"
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
            chain_map = {"bnb":"BNB","eth":"ETH","sol":"SOL","base":"BASE"}
            is_top = (filt == "top")
            if is_top:         calls = get_call_history(channel, top=True)
            elif filt in chain_map: calls = get_call_history(channel, chain_filter=chain_map[filt])
            else:              calls = get_call_history(channel)
            text = format_history(channel, calls, is_top=is_top)
            kb   = history_keyboard(channel)
            try: await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            except Exception: await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

# ═══════════════════════════════════════════════════════════════════════════════
# OWNER COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@owner_only
async def cmd_ownerhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔮 <b>OWNER COMMANDS</b>\n\n"

        "✏️ <b>Alert Templates</b>\n"
        "/settemplate — ⭐ template + premium emojis ek saath set karo\n"
        "/edittemplate — sirf text template edit karo\n"
        "/showtemplate — templates preview karo\n"
        "/editmilestone 2 — sirf 2X ke liye\n"
        "/clearmilestone 2 — remove milestone template\n"
        "/listmilestones — milestone status\n"
        "/setmilestones 2,5,10,50 — change list\n\n"

        "🔍 <b>Template Preview</b>\n"
        "/previewtemplate 100 — dekho 100X alert kaisa dikhega\n"
        "/previewmomentum — MOMENTUM ACTIVE post ka preview\n\n"

        "🖼️ <b>Milestone Media</b>\n"
        "/setmedia 2 — add photo/video for 2X\n"
        "/clearmedia 2 — remove media\n"
        "/listmedia — view all set media\n\n"

        "🎬 <b>Command Video</b>\n"
        "/setcommandvideo — reply karo video ko, set ho jaegi /command menu ki video\n\n"

        "📣 <b>Promo Posts (har 25 alerts ke baad)</b>\n"
        "/setpromo &lt;text&gt; — template set karo aur promo enable karo\n"
        "    (video add karne ke liye, video ko reply karo /setpromo se)\n"
        "/stoppromo — promo band karo\n\n"

        "🌟 <b>Premium Emojis</b>\n"
        "/getemoji — premium emoji bhejo ya reply karo, ID nikal lo aur set karo\n\n"

        "👤 <b>Admins (Owner only)</b>\n"
        "/addadmin USER_ID — add admin\n"
        "/removeadmin USER_ID — remove admin\n"
        "/listadmins — show admins\n\n"

        "📡 <b>Channels</b>\n"
        "/mychannels — tracked channels\n"
        "/addchannel username — add channel (purani posts skip ho jaengi)\n"
        "/removechannel username — remove channel\n\n"

        "🖼️ <b>Public Commands Media</b>\n"
        "/setstartmedia — /start ki photo/video set karo (reply karo)\n"
        "/clearstartmedia — /start ki media hata do\n"
        "/setcommandmedia — /command ki photo/video set karo (reply karo)\n"
        "/clearcommandmedia — /command ki media hata do\n\n"

        "🔗 <b>Channel Linking</b>\n"
        "/linkme @kol @mychannel — link for alerts\n\n"

        "🤖 <b>Userbot</b>\n"
        "/userbotlogin — login\n"
        "/userbotcheck — status\n"
        "/userbotlogout — disconnect\n\n"

        "👥 <b>Users & Stats</b>\n"
        "/myusers — user count\n"
        "/mystats — bot statistics + usernames list\n"
        "/broadcast — interactive multi-step broadcast (show users → select → send)\n\n"

        "🔗 <b>Promo Link (12-hour)</b>\n"
        "/setpromolink — add custom text+link to 2X–50X alerts for 12 hours\n"
        "/clearpromolink — remove promo link immediately\n\n"

        "📋 <b>Pending KOL Requests</b>\n"
        "/pendingkols — view all pending requests with Accept/Reject buttons\n\n"

        "🎬 <b>Momentum Active Videos</b>\n"
        "/addmomentumvideo — upload a video to add to rotation\n"
        "/listmomentumvideos — see all stored videos\n"
        "/removemomentumvideo N — remove video by number\n"
        "/clearmomentumvideos — clear all (revert to built-in 5 videos)\n\n"


        "🔧 <b>Other</b>\n"
        "/testalert — test post to @WizardScan\n"
        "/ownerhelp — this list\n"
        "/premiumguide — premium emoji ID kaise nikalna hai\n\n"

        "🔄 <b>Lists Force Refresh</b>\n"
        "/refreshtrending — Trending list (135) fresh data se update karo\n"
        "/refreshleaderboard — Leaderboard (136) force update karo\n"
        "/refreshchampions — Champions (137) points reset + force update\n\n"

        "🚫 <b>Trending Anti-Cheat (Blacklist)</b>\n"
        "/blocktrending CA — Token address ko trending se permanently block karo\n"
        "/unblocktrending CA — Block hatao\n"
        "/listblockedtrending — Blocked tokens ki list dekho\n\n"

        "🏆 <b>Points System (Champion KOL)</b>\n"
        "/givepoints @channel 50 — Channel ko manually points do (ya kato: -20)\n"
        "/checkpoints @channel — Kisi channel ke points dekho\n"
        "/checkpoints — Sab channels ke points top 20\n"
        "/zerocolpoints @channel — Kisi bhi KOL ke sab points zero karo\n\n"

        "⛔ <b>Call Freeze (Anti-Cheat)</b>\n"
        "/freezecall CA — Suspicious call ke sab future alerts band karo foran\n"
        "/freezecall @channel CA — Sirf us channel ki call freeze karo\n"
        "/unfreezecall CA — Freeze hatao, alerts phir se chalu ho jayenge\n\n"

        "📡 <b>Missed Call Tracking</b>\n"
        "/addmissedcall @channel CA x — Bot se miss hoi call manually add karo\n"
        "/addmissedcall @channel CA x entry_mc — DexScreener fail ho toh MC manually dein (e.g. 5K)\n"
        "Example: /addmissedcall @SomeKOL So1ABC123 100 5K\n\n"

        "📌 Naye commands ke liye: /ownerhelp2",
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
        "• <code>{kol_link}</code> → link to KOL post\n"
        "• <code>{bot_link}</code> → bot link\n\n"
        f"<b>Current template:</b>\n<pre>{html.escape(cur) if cur else '(default)'}</pre>\n\n"
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

@owner_only
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


async def cmd_ownerhelp2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔮 <b>OWNER COMMANDS — PAGE 2</b>\n\n"

        "📣 <b>Dropped-Call Posts (jab KOL pehli baar call kare)</b>\n"
        "/setdroppedtemplate — custom template set karo\n"
        "/showdroppedtemplate — current template dekho\n"
        "/cleardroppedtemplate — default par wapas\n"
        "/adddroppedvideo — video add karo rotation mein (max 20)\n"
        "/listdroppedvideos — stored videos dekho\n"
        "/removedroppedvideo N — video N hata do\n"
        "/cleardroppedvideos — sab videos hata do (text-only)\n"
        "/testdropped — test post bhejo channel mein\n\n"

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

        "ℹ️ Pehle waale commands ke liye: /ownerhelp",
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
    Supported: submit, subscribe, linkme"""
    VALID_CMDS = ["submit", "subscribe", "linkme"]
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
    VALID_CMDS = ["submit", "subscribe", "linkme"]
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
    cfg_set("command_media", {"file_id": fid, "type": ftype})
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
        "This will be appended to all 2X–50X alert posts for 12 hours.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_clearpromolink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the active promo link immediately."""
    cfg_set("promo_link", None)
    await update.message.reply_text("✅ Promo link removed from alert posts.")

@owner_only
async def cmd_pendingkols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all pending KOL requests with Accept/Reject buttons."""
    pending = load_pending()
    if not pending:
        await update.message.reply_text("✅ No pending KOL requests at the moment."); return
    await update.message.reply_text(f"📋 <b>Pending KOL Requests ({len(pending)} total)</b>", parse_mode="HTML")
    for req_id, req in list(pending.items()):
        uid   = req.get("user_id", "?")
        uname = req.get("username", f"User#{uid}")
        ch    = req.get("channel", "?")
        ts    = req.get("ts", "")[:16]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"kreq|{uid}|{ch[:28]}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"krej|{uid}|{ch[:28]}"),
        ]])
        await update.message.reply_text(
            f"🔮 Channel: <b>@{ch}</b>\n👤 From: @{uname} (ID: <code>{uid}</code>)\n🕐 {ts} UTC",
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
    """Preview a MOMENTUM ACTIVE post."""
    bot_username = (await context.bot.get_me()).username
    xray_url     = f"https://t.me/{bot_username}?start=xray_DemoKOL_10"
    text = (
        "<b>🔮 MOMENTUM ACTIVE 🔮</b>\n\n"
        "<b>@DemoKOL</b> has delivered <b>5</b> calls above <b>10X</b> in the last 7 days.\n\n"
        "Consistent edge. Consistent results. Track the pattern."
    )
    _momentum_preview_emoji_ids = [MOMENTUM_ACTIVE_EMOJI_ID, MOMENTUM_ACTIVE_EMOJI_ID]
    buttons = [
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan"),
         InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan"),
         InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 KOL Signal", url="https://t.me/WizardScan")],
        [InlineKeyboardButton("🔮 X-Ray Report", url=xray_url)],
    ]
    kb = InlineKeyboardMarkup(buttons)
    # Use current rotating video — config file_ids first, else VID_MOMENTUM_LIST
    _pcfg = load_config()
    momentum_idx = _pcfg.get("momentum_video_index", 0)
    _pmom_vids = _pcfg.get("momentum_videos", [])
    if _pmom_vids:
        _pmv = _pmom_vids[momentum_idx % len(_pmom_vids)]
        cfg_set("momentum_video_index", (momentum_idx + 1) % len(_pmom_vids))
        vid_file_id_p = _pmv.get("file_id"); vid_ftype_p = _pmv.get("type", "video")
        vid_path = None
    else:
        vid_file_id_p = None; vid_ftype_p = None
        vid_path = VID_MOMENTUM_LIST[momentum_idx % len(VID_MOMENTUM_LIST)]
        cfg_set("momentum_video_index", (momentum_idx + 1) % len(VID_MOMENTUM_LIST))
    _preview_sent_id = None
    if vid_file_id_p:
        if userbot_client and _momentum_preview_emoji_ids:
            try:
                # Send emoji via userbot (no buttons in userbot — will add via bot below)
                _pv_sent = await _userbot_send_media_with_emoji(
                    context.bot, update.message.chat_id, vid_file_id_p, vid_ftype_p,
                    text, _momentum_preview_emoji_ids, None)
                try: _preview_sent_id = _pv_sent.id if _pv_sent else None
                except Exception: pass
            except Exception as ep:
                logger.warning(f"Preview momentum userbot file_id failed: {ep}")
        if not _preview_sent_id:
            _pv_b = await update.message.reply_video(video=vid_file_id_p, caption=text, parse_mode="HTML", reply_markup=kb)
            try: _preview_sent_id = _pv_b.message_id
            except Exception: pass
    elif userbot_client and vid_path and os.path.exists(vid_path):
        try:
            from telethon.extensions.html import parse as tl_html_parse
            plain_text, base_ents = tl_html_parse(text)
            all_ents = _build_premium_entities(plain_text, base_ents, _momentum_preview_emoji_ids)
            import tempfile as _tfp
            with open(vid_path, "rb") as vfp:
                vid_bytes_p = vfp.read()
            tmp_p = _tfp.NamedTemporaryFile(delete=False, suffix='.mp4')
            tmp_p.write(vid_bytes_p); tmp_p.close()
            # Send emoji via userbot (no buttons — will add via bot below)
            _pv_ub = await userbot_client.send_file(
                update.message.chat_id, tmp_p.name,
                caption=plain_text, formatting_entities=all_ents,
                supports_streaming=True
            )
            try: _preview_sent_id = _pv_ub.id
            except Exception: pass
            try: os.unlink(tmp_p.name)
            except Exception: pass
        except Exception as ep:
            logger.warning(f"Preview momentum userbot send failed: {ep}")
            if os.path.exists(vid_path):
                with open(vid_path, "rb") as vf:
                    _pv_fb = await update.message.reply_video(video=vf, caption=text, parse_mode="HTML", reply_markup=kb)
                    try: _preview_sent_id = _pv_fb.message_id
                    except Exception: pass
            else:
                _pv_tb = await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
                try: _preview_sent_id = _pv_tb.message_id
                except Exception: pass
    elif vid_path and os.path.exists(vid_path):
        with open(vid_path, "rb") as vf:
            _pv_vf = await update.message.reply_video(video=vf, caption=text, parse_mode="HTML", reply_markup=kb)
        try: _preview_sent_id = _pv_vf.message_id
        except Exception: pass
    else:
        _pv_txt = await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        try: _preview_sent_id = _pv_txt.message_id
        except Exception: pass

    # Add buttons via bot (reliable in both DMs and channels; userbot can't add inline buttons to DMs)
    if _preview_sent_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=update.message.chat_id,
                message_id=_preview_sent_id,
                reply_markup=kb
            )
        except Exception as e_btn:
            logger.warning(f"Preview momentum button edit failed: {e_btn}")

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
        await query.answer("⛔ Sirf admin/owner", show_alert=True); return
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
        "➤ <a href='https://t.me/WizardScan'>KOL</a>\n"
        "➤ <a href='https://t.me/WizardScan'>TG</a>"
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

@owner_only
async def cmd_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /addchannel username"); return
    ch = context.args[0].lstrip("@"); channels = load_channels()
    if ch in channels: await update.message.reply_text(f"@{ch} already tracked."); return
    channels.append(ch); save_channels(channels)
    await update.message.reply_text(f"✅ @{ch} added to tracking. Scanning existing posts to skip old calls...")
    # Pre-populate seen_message_ids so bot only tracks NEW posts from now on
    try:
        posts = await fetch_channel_posts(ch)
        for post in posts:
            seen_message_ids[ch].add(post["id"])
        _save_seen()
        await update.message.reply_text(f"✅ {len(posts)} existing posts marked as seen. Only new posts will be tracked.")
    except Exception as e:
        logger.warning(f"addchannel pre-scan failed for @{ch}: {e}")

@owner_only
async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /removechannel username"); return
    ch = context.args[0].lstrip("@"); channels = load_channels()
    if ch not in channels: await update.message.reply_text(f"@{ch} not found."); return
    channels.remove(ch); save_channels(channels)

    # Auto-blacklist this channel's tracked CAs from trending
    bl = load_trending_blacklist()
    blocked_cas = []
    for call_key, call in tracked_calls.items():
        if call.get("channel", "").lower() == ch.lower():
            ca = call.get("ca", "")
            if ca and ca.lower() not in bl:
                bl.add(ca.lower())
                blocked_cas.append(ca)
    if blocked_cas:
        save_trending_blacklist(bl)
        await update.message.reply_text(
            f"✅ @{ch} removed.\n\n"
            f"⛔ {len(blocked_cas)} token(s) trending se bhi block kar diye gaye:\n"
            + "\n".join(f"  • <code>{ca}</code>" for ca in blocked_cas[:5])
            + ("\n  ..." if len(blocked_cas) > 5 else ""),
            parse_mode="HTML")
    else:
        await update.message.reply_text(f"✅ @{ch} removed.")

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
    Usage: /addmissedcall @channel CA x_achieved [entry_mc]
    Example: /addmissedcall @SomeKOL So1ABC...xyz 100
             /addmissedcall @SomeKOL So1ABC...xyz 100 5K
    If DexScreener fails, you can pass entry_mc manually (e.g. 5K, $5K, 1M, 50000)."""
    if len(context.args) < 3:
        await update.message.reply_text(
            "📡 <b>Missed Call Add Karne Ka Tarika:</b>\n\n"
            "<code>/addmissedcall @channelname CONTRACT_ADDRESS x_achieved</code>\n"
            "<code>/addmissedcall @channelname CONTRACT_ADDRESS x_achieved entry_mc</code>\n\n"
            "Example:\n"
            "<code>/addmissedcall @SomeKOL So1ABC123xyz 100</code>\n"
            "<code>/addmissedcall @SomeKOL So1ABC123xyz 100 5K</code>\n\n"
            "Bot DexScreener se data fetch karega aur call ko manually track karega.\n"
            "x_achieved = call kitna x gayi (e.g. 100 matlab 100x)\n"
            "entry_mc (optional) = agar DexScreener data na mile toh manually dein (e.g. 5K, 1.5M)",
            parse_mode="HTML"); return
    channel = context.args[0].lstrip("@").strip()
    ca = context.args[1].strip()
    try:
        x_achieved = int(context.args[2])
    except ValueError:
        await update.message.reply_text("⚠️ x_achieved number hona chahiye. Example: 100"); return
    if x_achieved < 2:
        await update.message.reply_text("⚠️ x_achieved kam se kam 2 hona chahiye."); return

    # Optional 4th argument: manual entry_mc override (for pump.fun / dead tokens)
    manual_entry_mc = 0.0
    if len(context.args) >= 4:
        manual_entry_mc = parse_mc_string(context.args[3])

    channels = load_channels()
    if channel.lower() not in [c.lower() for c in channels]:
        await update.message.reply_text(
            f"⚠️ @{channel} tracked channels mein nahi hai.\n"
            f"Pehle /addchannel {channel} karo."); return

    msg = await update.message.reply_text(f"⏳ DexScreener se {ca[:12]}... ka data fetch ho raha hai...")
    dex = await fetch_dexscreener(ca)
    call_key = f"{channel}_{ca}"

    if dex and dex.get("mcap"):
        entry_mc = dex["mcap"] / x_achieved  # Back-calculate entry MC
        entry_fmt = fmt_mc(entry_mc)
        cur_mc_val = dex["mcap"]
        cur_fmt = fmt_mc(cur_mc_val)
        chain_val = dex["chain"]
        symbol_val = dex.get("symbol", "UNKNOWN")
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": entry_mc,
                "entry_price": dex.get("price", 0) / x_achieved if dex.get("price") else 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
            }
            _save_tracked()
    elif manual_entry_mc > 0:
        # Manual entry MC provided — calculate values without DexScreener
        entry_mc  = manual_entry_mc
        entry_fmt = fmt_mc(entry_mc)
        cur_mc_val = entry_mc * x_achieved
        cur_fmt   = fmt_mc(cur_mc_val)
        chain_val = "SOL"; symbol_val = "UNKNOWN"
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": entry_mc, "entry_price": 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
            }
            _save_tracked()
    else:
        # No DexScreener data and no manual MC — warn user
        entry_mc  = 0; entry_fmt = "N/A"; cur_fmt = "N/A"
        chain_val = "SOL"; symbol_val = "UNKNOWN"
        if call_key not in tracked_calls:
            tracked_calls[call_key] = {
                "channel": channel, "msg_id": 0, "ca": ca,
                "chain": chain_val, "entry_mc": 0, "entry_price": 0,
                "entry_fmt": entry_fmt, "symbol": symbol_val,
                "tracked_since": datetime.utcnow().isoformat(),
            }
            _save_tracked()

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
    asyncio.create_task(send_alert(
        context.bot, channel, call_data.get("msg_id", 0), x_achieved,
        call_data.get("chain", "SOL"), call_data.get("entry_fmt", "N/A"),
        cur_fmt, ca, call_data.get("symbol", "UNKNOWN")
    ))

    new_pts = get_channel_points(channel)
    dex_note = ""
    if not (dex and dex.get("mcap")) and manual_entry_mc <= 0:
        dex_note = "\n\n⚠️ DexScreener data nahi mila. Entry MC N/A hai.\nAgar entry MC pata ho toh yeh use karein:\n<code>/addmissedcall @{} {} {} 5K</code>".format(channel, ca, x_achieved)
    elif not (dex and dex.get("mcap")) and manual_entry_mc > 0:
        dex_note = f"\n\n📌 Manual entry MC use kiya: {entry_fmt}"
    await msg.edit_text(
        f"✅ <b>Missed Call Added!</b>\n\n"
        f"Channel: @{channel}\n"
        f"CA: <code>{ca}</code>\n"
        f"Chain: {chain_val}\n"
        f"Symbol: {symbol_val}\n"
        f"Entry MC: {entry_fmt} → Current: {cur_fmt}\n"
        f"X Achieved: <b>{x_achieved}X</b>\n"
        f"Milestones Marked: {len(newly_marked)}\n\n"
        f"📊 @{channel} ke ab <b>{new_pts}/{POINTS_FOR_CHAMPION} points</b> hain.{dex_note}",
        parse_mode="HTML")

@owner_only
async def cmd_addx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

@owner_only
async def cmd_removex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ X feature hataya gaya hai.")

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
    """Force refresh champions post 137 — clears points, resets timer, updates immediately."""
    # Clear all points so the list fully resets
    save_channel_points({})
    cfg_set("last_champions_update", "")
    cfg_set("last_champions_reset", datetime.utcnow().isoformat())
    msg = await update.message.reply_text("⏳ Champions points clear ho gaye. Post 137 refresh ho raha hai...")
    try:
        ok = await _update_champions_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_champions_update", datetime.utcnow().isoformat())
            await msg.edit_text("✅ Champions (post 137) refresh ho gayi! Sab points reset — fresh start.")
        else:
            await msg.edit_text("⚠️ Points clear hue lekin post 137 edit nahi hua. Bot/userbot check karo.")
    except Exception as e:
        await msg.edit_text(f"⚠️ Error: {e}")

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
    msg = await update.message.reply_text("⏳ DexScreener/GeckoTerminal se bilkul naya data fetch ho raha hai...")
    try:
        # Fetch once and pass directly — avoids double-fetch inside _update_trending_with_premium_emojis
        chain_tokens = await fetch_trending()
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
        post_id  = row["wizard_post_id"]
        num      = numbers[i] if i < len(numbers) else f"{i+1}."
        x_str    = f"{best_x}X" if best_x > 0 else "—"
        ch_link  = f'<a href="https://t.me/{html.escape(ch)}">@{html.escape(ch)}</a>'
        line = f"{num} {ch_link} ➡️ {x_str}"
        if post_id:
            alert_link = f'<a href="https://t.me/WizardScan/{post_id}">🔗 WizardScan Alert</a>'
            line += f"\n       {alert_link}"
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
            with_uname.append(f"@{uname}" + (f" — {html.escape(fname)}" if fname else ""))
        else:
            no_uname.append(f"ID:{k}" + (f" — {html.escape(fname)}" if fname else ""))

    MAX_SHOW = 100
    lines = with_uname[:MAX_SHOW]
    remaining_slots = MAX_SHOW - len(lines)
    if remaining_slots > 0:
        lines += no_uname[:remaining_slots]
    users_preview = "\n".join(f"• {l}" for l in lines) if lines else "No users yet."
    hidden = len(d) - len(lines)
    if hidden > 0:
        users_preview += f"\n<i>...+{hidden} more</i>"

    header = (
        f"📊 <b>Bot Stats</b>\n\n"
        f"👥 Total users: <b>{len(d)}</b>\n"
        f"✅ With @username: <b>{len(with_uname)}</b>\n"
        f"🔔 DM subscribers: <b>{len(subs)}</b>\n"
        f"📡 Tracked channels: <b>{len(channels)}</b>\n"
        f"🎯 Active calls: <b>{len(tracked_calls)}</b>\n"
        f"🔔 Milestones fired: <b>{sum(len(v) for v in sent_milestones.values())}</b>\n"
        f"🤖 Userbot: <b>{ub_status}</b>\n\n"
        f"📅 LB: <b>{last_lb}</b> | Champs: <b>{last_ch}</b> | Trending: <b>{last_tr}</b>\n\n"
        f"<b>Users (@ first, then ID-only):</b>\n{users_preview}"
    )
    # Send in chunks if > 4096 chars
    if len(header) <= 4096:
        await update.message.reply_text(header, parse_mode="HTML")
    else:
        await update.message.reply_text(header[:4090] + "\n...", parse_mode="HTML")

@owner_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_users_dict()
    if not d:
        await update.message.reply_text("No users found."); return
    uid_state = update.effective_user.id
    owner_edit_state[uid_state] = {"state": ST_BROADCAST_PICK, "all_users": d}
    # Show only usernames — skip users without a username
    with_username = [(k, v) for k, v in d.items() if v.get("username")]
    no_username   = len(d) - len(with_username)
    lines = [f"@{v['username']}" for k, v in with_username]
    users_list = "\n".join(lines[:100])
    if len(with_username) > 100:
        users_list += f"\n...+{len(with_username)-100} more"
    no_uname_note = f"\n⚠️ {no_username} user(s) have no username (will still receive if 'all' is used)." if no_username else ""
    await update.message.reply_text(
        f"📢 <b>Broadcast — {len(d)} Users</b>\n\n"
        f"<pre>{users_list}</pre>{no_uname_note}\n\n"
        f"Reply with usernames (comma-separated):\n"
        f"Example: <code>@user1, @user2</code>\n\n"
        f"Or send <code>all</code> to broadcast to everyone.",
        parse_mode="HTML"
    )

@owner_only
async def cmd_mediabroadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a photo or video with caption to ALL bot users at once."""
    d = load_users_dict()
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
    """Manually reset leaderboard and immediately update post 136."""
    now = datetime.utcnow()
    c = load_config()
    c["last_leaderboard_update"] = ""
    c["leaderboard_reset_date"] = now.isoformat()
    c["leaderboard_reset_since"] = now.isoformat()
    save_config(c)
    msg = await update.message.reply_text(
        "⏳ Leaderboard reset ho raha hai, post 136 abhi update ho raha hai...",
        parse_mode="HTML"
    )
    try:
        ok = await _update_leaderboard_with_premium_emojis(context.bot)
        if ok:
            cfg_set("last_leaderboard_update", now.isoformat())
            await msg.edit_text(
                "✅ <b>Leaderboard Reset!</b>\n\nPost 136 foran update ho gayi — naya record ab live hai. 🏆",
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
    """Manually force-update posts 135 (trending), 136 (leaderboard), 137 (champions)."""
    msg = await update.message.reply_text("⏳ Posts 135/136/137 update ho rahe hain...")
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
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    msg = update.message
    if not msg or not uid: return

    # Custom commands
    if msg.text and msg.text.startswith("/"):
        cmd_name = msg.text.split()[0].lstrip("/").split("@")[0].lower()
        custom   = cfg_get("custom_commands",{})
        if cmd_name in custom:
            await msg.reply_text(custom[cmd_name], parse_mode="HTML"); return

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
                c = load_config(); mt = c.get("milestone_templates",{}); mt[ms] = text_val; c["milestone_templates"] = mt; save_config(c)
            if msg.photo or msg.video:
                fid = msg.photo[-1].file_id if msg.photo else msg.video.file_id
                ftype = "photo" if msg.photo else "video"
                c = load_config(); media = c.get("milestone_media",{}); media[ms] = {"type":ftype,"file_id":fid}; c["milestone_media"] = media; save_config(c)
            if not text_val and not (msg.photo or msg.video): await msg.reply_text("⚠️ Send template text or media."); return
            owner_edit_state[uid] = {"state": None}
            await msg.reply_text(f"✅ {ms}X template saved!"); return

        elif state == ST_SET_MEDIA:
            ms = si["milestone"]
            if msg.photo:   fid, ftype = msg.photo[-1].file_id, "photo"
            elif msg.video: fid, ftype = msg.video.file_id, "video"
            else:           await msg.reply_text("⚠️ Send photo or video."); return
            c = load_config(); media = c.get("milestone_media",{}); media[ms] = {"type":ftype,"file_id":fid}
            if msg.caption:
                mt = c.get("milestone_templates",{}); mt[ms] = msg.caption; c["milestone_templates"] = mt
            c["milestone_media"] = media; save_config(c)
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
                c["command_media"] = {"type":ftype,"file_id":fid}; saved.append(ftype)
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
                f"Now send your broadcast message (text, photo, or video with caption).",
                parse_mode="HTML"
            ); return

        elif state == ST_BROADCAST_MSG:
            targets = si.get("targets", [])
            owner_edit_state[uid] = {"state": None}
            ok = fail = blocked = 0
            for i, tid in enumerate(targets):
                try:
                    if msg.photo:
                        await context.bot.send_photo(tid, photo=msg.photo[-1].file_id,
                            caption=msg.caption or "", parse_mode="HTML")
                    elif msg.video:
                        await context.bot.send_video(tid, video=msg.video.file_id,
                            caption=msg.caption or "", parse_mode="HTML")
                    elif msg.text:
                        await context.bot.send_message(tid, msg.text, parse_mode="HTML")
                    else:
                        await context.bot.forward_message(tid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                    ok += 1
                except Exception as e:
                    err_str = str(e).lower()
                    if any(x in err_str for x in ("deactivated", "deleted", "blocked", "bot was kicked", "user not found", "chat not found", "forbidden")):
                        blocked += 1
                    else:
                        fail += 1
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
            targets = [int(k) for k in d.keys()]
            ok = fail = blocked = 0
            status_msg = await msg.reply_text(f"⏳ Sending to {len(targets)} users...")
            for i, tid in enumerate(targets):
                try:
                    if msg.photo:
                        await context.bot.send_photo(tid, photo=msg.photo[-1].file_id,
                            caption=msg.caption or "", parse_mode="HTML")
                    elif msg.video:
                        await context.bot.send_video(tid, video=msg.video.file_id,
                            caption=msg.caption or "", parse_mode="HTML")
                    ok += 1
                except Exception as e:
                    err_str = str(e).lower()
                    if any(x in err_str for x in ("deactivated", "deleted", "blocked", "bot was kicked", "user not found", "chat not found", "forbidden")):
                        blocked += 1
                    else:
                        fail += 1
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
            cfg_set("promo_link", {"text": promo_text, "url": promo_url, "set_at": datetime.utcnow().isoformat()})
            await msg.reply_text(
                f"✅ <b>Promo Link Set for 12 Hours</b>\n\n"
                f"Text: {promo_text}\nURL: {promo_url}\n\n"
                f"Will appear on all 2X–50X alert posts until expiry.",
                parse_mode="HTML"
            ); return

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

        elif state == ST_DROPPED_TMPL:
            owner_edit_state[uid] = {"state": None}
            text_val = msg.text or msg.caption
            if not text_val:
                await msg.reply_text("⚠️ Template text bhejo."); return
            cfg_set("dropped_call_template", text_val)
            await msg.reply_text(
                "✅ <b>Dropped-Call Template Saved!</b>\n\n"
                "Ab jab bhi koi KOL pehli baar call kare ga, isi template se post hogi.\n"
                "/showdroppedtemplate se dekh sakte ho.",
                parse_mode="HTML"); return

    # Channel / Twitter lookup
    if msg.text and not msg.text.startswith("/"):
        handled = await handle_lookup(update, msg.text)
        if handled: return

# ─── Post init ────────────────────────────────────────────────────────────────
async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start",     "Welcome & Bot Info"),
        BotCommand("command",   "Command Center"),
        BotCommand("history",   "Call history of a KOL channel"),
        BotCommand("linkme",    "Link your channel for alerts"),
        BotCommand("subscribe", "Toggle DM alerts"),
        BotCommand("submit",    "Request KOL tracking"),
    ])
    asyncio.create_task(init_userbot())
    logger.info("✅ Bot commands menu set")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN: logger.error("❌ BOT_TOKEN not set!"); return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Public commands
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("command",   cmd_command))
    app.add_handler(CommandHandler("history",   cmd_history))
    app.add_handler(CommandHandler("linkinfo",  cmd_linkinfo))
    app.add_handler(CommandHandler("submit",    cmd_submit))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("linkme",    cmd_linkme))

    # Callbacks — specific patterns FIRST, then generic catch-all
    app.add_handler(CallbackQueryHandler(cb_setemoji, pattern=r"^setemoji:"))
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
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))


    # Owner — post updates
    app.add_handler(CommandHandler("updateleaderboard", cmd_updateleaderboard))
    app.add_handler(CommandHandler("updatechampions",   cmd_updatechampions))
    app.add_handler(CommandHandler("trending",          cmd_trending))
    app.add_handler(CommandHandler("setrankingemojis",  cmd_setrankingemojis))
    app.add_handler(CommandHandler("refreshtrending",      cmd_refreshtrending))
    app.add_handler(CommandHandler("refreshleaderboard",   cmd_refreshleaderboard))
    app.add_handler(CommandHandler("refreshchampions",     cmd_refreshchampions))
    app.add_handler(CommandHandler("blocktrending",        cmd_blocktrending))
    app.add_handler(CommandHandler("unblocktrending",      cmd_unblocktrending))
    app.add_handler(CommandHandler("listblockedtrending",  cmd_listblockedtrending))
    app.add_handler(CommandHandler("givepoints",        cmd_givepoints))
    app.add_handler(CommandHandler("checkpoints",       cmd_checkpoints))
    app.add_handler(CommandHandler("zerocolpoints",     cmd_zerocolpoints))
    app.add_handler(CommandHandler("freezecall",        cmd_freezecall))
    app.add_handler(CommandHandler("unfreezecall",      cmd_unfreezecall))
    app.add_handler(CommandHandler("addmissedcall",     cmd_addmissedcall))

    # Admin + Owner — emoji management
    app.add_handler(CommandHandler("setalertemoji",     cmd_setalertemoji))
    app.add_handler(CommandHandler("listalertemojis",   cmd_listalertemojis))
    app.add_handler(CommandHandler("clearalertemoji",   cmd_clearalertemoji))

    # Owner — admin management
    app.add_handler(CommandHandler("addadmin",          cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin",       cmd_removeadmin))
    app.add_handler(CommandHandler("listadmins",        cmd_listadmins))

    # Owner — users
    app.add_handler(CommandHandler("myusers",        cmd_myusers))
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
    app.add_handler(CommandHandler("trendingKols",       cmd_trendingkols))
    app.add_handler(CommandHandler("trendingkols",       cmd_trendingkols))
    app.add_handler(CommandHandler("setcommandvideo",    cmd_setcommandvideo))
    app.add_handler(CommandHandler("setpromo",           cmd_setpromo))
    app.add_handler(CommandHandler("stoppromo",          cmd_stoppromo))
    app.add_handler(CommandHandler("setpromolink",        cmd_setpromolink))
    app.add_handler(CommandHandler("clearpromolink",      cmd_clearpromolink))
    app.add_handler(CommandHandler("pendingkols",         cmd_pendingkols))
    app.add_handler(CommandHandler("addmomentumvideo",    cmd_addmomentumvideo))
    app.add_handler(CommandHandler("listmomentumvideos",  cmd_listmomentumvideos))
    app.add_handler(CommandHandler("removemomentumvideo", cmd_removemomentumvideo))
    app.add_handler(CommandHandler("clearmomentumvideos", cmd_clearmomentumvideos))
    app.add_handler(CommandHandler("previewtemplate",     cmd_previewtemplate))
    app.add_handler(CommandHandler("previewmomentum",    cmd_previewmomentum))
    app.add_handler(CommandHandler("testmomentum",       cmd_testmomentum))

    # Owner — new commands (Page 2)
    app.add_handler(CommandHandler("setdroppedtemplate",  cmd_setdroppedtemplate))
    app.add_handler(CommandHandler("showdroppedtemplate", cmd_showdroppedtemplate))
    app.add_handler(CommandHandler("cleardroppedtemplate",cmd_cleardroppedtemplate))
    app.add_handler(CommandHandler("adddroppedvideo",     cmd_adddroppedvideo))
    app.add_handler(CommandHandler("listdroppedvideos",   cmd_listdroppedvideos))
    app.add_handler(CommandHandler("removedroppedvideo",  cmd_removedroppedvideo))
    app.add_handler(CommandHandler("cleardroppedvideos",  cmd_cleardroppedvideos))
    app.add_handler(CommandHandler("testdropped",         cmd_testdropped))
    app.add_handler(CommandHandler("ownerhelp2",          cmd_ownerhelp2))
    app.add_handler(CommandHandler("setbuttonmedia",     cmd_setbuttonmedia))
    app.add_handler(CommandHandler("clearbuttonmedia",   cmd_clearbuttonmedia))
    app.add_handler(CommandHandler("addxrayvideo",       cmd_addxrayvideo))
    app.add_handler(CommandHandler("listxrayvideos",     cmd_listxrayvideos))
    app.add_handler(CommandHandler("removexrayvideo",    cmd_removexrayvideo))
    app.add_handler(CommandHandler("clearxrayvideos",    cmd_clearxrayvideos))
    app.add_handler(CommandHandler("sethistorymedia",    cmd_sethistorymedia))
    app.add_handler(CommandHandler("clearhistorymedia",  cmd_clearhistorymedia))
    app.add_handler(CommandHandler("addpostlink",        cmd_addpostlink))
    app.add_handler(CommandHandler("removepostlink",     cmd_removepostlink))
    app.add_handler(CommandHandler("listpostlinks",      cmd_listpostlinks))
    app.add_handler(CommandHandler("setcommandmedia",    cmd_setcommandmedia))
    app.add_handler(CommandHandler("clearcommandmedia",  cmd_clearcommandmedia))

    # General messages
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, handle_message))

    # Jobs
    app.job_queue.run_repeating(monitoring_job,      interval=60,        first=30)
    app.job_queue.run_repeating(leaderboard_job,     interval=90,        first=120)   # every 90 sec
    app.job_queue.run_repeating(champions_job,       interval=90,        first=150)   # every 90 sec
    app.job_queue.run_repeating(trending_job,        interval=90,        first=60)    # every 90 sec
    app.job_queue.run_repeating(momentum_check_job,  interval=86400,     first=300)  # daily

    # Startup: notify owner if userbot not connected
    async def _startup_notify(app_ref):
        await init_userbot()

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

        # Pre-populate seen_message_ids for channels that have NO seen IDs yet.
        # This prevents tracking old posts when the bot starts fresh on a new server.
        try:
            channels_to_scan = [ch for ch in load_channels() if not seen_message_ids.get(ch)]
            if channels_to_scan:
                logger.info(f"Startup pre-scan: marking existing posts as seen for {len(channels_to_scan)} channel(s)...")
                for ch in channels_to_scan:
                    try:
                        posts = await fetch_channel_posts(ch)
                        for post in posts:
                            seen_message_ids[ch].add(post["id"])
                        logger.info(f"Pre-scan: marked {len(posts)} posts as seen for @{ch}")
                    except Exception as e_scan:
                        logger.warning(f"Pre-scan failed for @{ch}: {e_scan}")
                _save_seen()
        except Exception as e_prescan:
            logger.warning(f"Startup pre-scan error: {e_prescan}")

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

    app.post_init = _startup_notify

    logger.info(f"✅ WIZARD SCAN Bot starting — Owner: {OWNER_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
