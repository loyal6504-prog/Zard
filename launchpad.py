# ===== launchpad.py — PinkSale / CheesePad / launchpad alerts =====
# Loaded by bot.py into ONE shared namespace (no imports needed between pieces).

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
    "rh": "RH", "robinhood": "RH", "robinhood-chain": "RH",
    "arc": "ARC", "arc-network": "ARC",
}

# PinkSale posts are ONLY made for these chains (owner 2026-09-30).
# Anything else (Polygon, Arbitrum, Avax, ...) is silently skipped.
PS_SUPPORTED_CHAINS = {"BSC", "SOL", "ETH", "BASE", "TON", "RH", "ARC"}
_PS_CHAIN_DISPLAY   = {"BSC": "BNB", "BNB": "BNB", "ETH": "ETH", "ETHEREUM": "ETH",
                       "SOL": "SOL", "SOLANA": "SOL", "BASE": "BASE", "TON": "TON",
                       "RH": "RH", "ARC": "ARC"}

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

        if not chain and platform == "PinkSale":
            # /launchpad/<chain>/<addr> with a chain we do not know (linea, sui, ...)
            # used to fall through to the BSC default below → wrong chain, wrong post.
            _segs = [x for x in path.split("?")[0].split("/") if x]
            for _i, _sg in enumerate(_segs):
                if _PS_ADDR_RE.fullmatch(_sg):
                    if _i > 0:
                        _prev = _segs[_i - 1].lower()
                        if _prev not in ("launchpad", "fairlaunch", "fair-launch", "presale",
                                         "sale", "en", "zh", "cn"):
                            chain = _prev.upper()
                    break
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
                                     (tok, _PS_SEL_DEC), (tok, "0x18160ddd")])
            name, sym = _ps_hex_string(nm[0]), _ps_hex_string(nm[1])
            dec = _ps_hex_uint(nm[2])
            if name:
                out["name"] = name
            if sym:
                out["symbol"] = sym
            if dec and dec <= 36:
                out["decimals"] = dec
                _ts = _ps_hex_uint(nm[3]) if len(nm) > 3 else None
                if _ts:
                    _sup = _ts // (10 ** dec)
                    if _sup > 0:
                        out["supply"] = _sup
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


# ── PinkSale extras: badges (SAFU/Audit/KYC/Doxx/Business) + token socials ──
# Only the TOKEN's own links are wanted, never PinkSale's — PinkSale's site
# footer carries all of its own socials, so nothing is regex-scraped from page
# HTML; links are taken from JSON keys only and PinkSale-owned ones dropped.
_PS_OWN_MARKS = ("pinksale", "pinkecosystem", "pinklock", "pinkmoon", "pinkanti", "pinkpad",
                 "babypinkpink",            # PinkSale's "Support" link in the top menu
                 "t.me/share", "/sharer", "/intent/", "twitter.com/share")
_PS_SOCIAL_KEYS = {
    "website":   ("website", "websiteurl", "websitelink", "site", "web"),
    "twitter":   ("twitter", "twitterurl", "twitterlink", "x", "xurl"),
    "telegram":  ("telegram", "telegramurl", "telegramlink", "telegramchaturl", "tglink", "tg"),
    "youtube":   ("youtube", "youtubeurl", "youtubelink"),
    "instagram": ("instagram", "instagramurl", "instagramlink"),
    "discord":   ("discord", "discordurl", "discordlink"),
    "reddit":    ("reddit", "reddithurl", "redditurl", "redditlink"),
}
_PS_SOCIAL_DOMAINS = {
    "twitter": ("x.com", "twitter.com"), "telegram": ("t.me", "telegram.me"),
    "youtube": ("youtube.com", "youtu.be"), "instagram": ("instagram.com",),
    "discord": ("discord.gg", "discord.com", "discordapp.com"), "reddit": ("reddit.com",),
}
_PS_SOCIAL_BASE = {"twitter": "https://x.com/", "telegram": "https://t.me/",
                   "instagram": "https://instagram.com/"}
_PS_BADGE_KEYS = {
    "safu":     ("safu", "issafu", "safubadge"),
    "audit":    ("audit", "isaudit", "audited", "isaudited", "auditbadge", "auditlink", "auditurl"),
    "kyc":      ("kyc", "iskyc", "kycbadge", "kyclink", "kycurl", "kycverified"),
    "doxx":     ("doxx", "isdoxx", "doxxed", "isdoxxed", "dox", "isdox", "doxxbadge"),
    "business": ("business", "isbusiness", "businessbadge"),
}

def _ps_norm_social(kind, v):
    """Clean one social link; '' if it is junk or belongs to PinkSale itself."""
    if not isinstance(v, str):
        return ""
    u = v.strip().strip('",;')
    if not u or len(u) > 200 or re.search(r'\s', u):
        return ""
    if u.startswith("@"):
        u = u[1:]
    if not re.match(r'^https?://', u, re.I):
        if re.fullmatch(r'[A-Za-z0-9_]{3,40}', u) and kind in _PS_SOCIAL_BASE:
            u = _PS_SOCIAL_BASE[kind] + u
        elif "." in u:
            u = "https://" + u
        else:
            return ""
    low = u.lower()
    if any(m in low for m in _PS_OWN_MARKS):
        return ""
    doms = _PS_SOCIAL_DOMAINS.get(kind)
    if doms:
        m = re.match(r'https?://(?:www\.)?([^/?#]+)', low)
        if not m or not any(m.group(1) == d or m.group(1).endswith("." + d) for d in doms):
            return ""
    return u

def _ps_truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "no", "0", "none", "null", "n/a", "undefined")
    if isinstance(v, (dict, list)):
        return bool(v)
    return False

def _ps_find_pool_dict(obj, addr, depth=0):
    """First dict that has the sale address as one of its values."""
    if depth > 8 or not addr:
        return None
    a = addr.lower()
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, str) and v.lower() == a:
                return obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                r = _ps_find_pool_dict(v, addr, depth + 1)
                if r is not None:
                    return r
    elif isinstance(obj, list):
        for it in obj[:50]:
            r = _ps_find_pool_dict(it, addr, depth + 1)
            if r is not None:
                return r
    return None

def _ps_walk_extras(obj, out, depth=0):
    if depth > 5:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = re.sub(r'[^a-z]', '', str(k).lower())
            for b, names in _PS_BADGE_KEYS.items():
                if kl in names:
                    out["badges_seen"] = "Yes"
                    if _ps_truthy(v):
                        out["badge_" + b] = "Yes"
            if kl in ("badges", "badge"):
                out["badges_seen"] = "Yes"
                items = v if isinstance(v, list) else ([v] if isinstance(v, str) else [])
                for it in items:
                    txt = it if isinstance(it, str) else (
                        (it.get("name") or it.get("type") or it.get("title") or "")
                        if isinstance(it, dict) else "")
                    low = str(txt).lower()
                    for b in _PS_BADGE_KEYS:
                        if b in low:
                            out["badge_" + b] = "Yes"
            for kind, names in _PS_SOCIAL_KEYS.items():
                if kl in names and isinstance(v, str) and not out.get(kind):
                    u = _ps_norm_social(kind, v)
                    if u:
                        out[kind] = u
            if isinstance(v, (dict, list)):
                _ps_walk_extras(v, out, depth + 1)
    elif isinstance(obj, list):
        for it in obj[:30]:
            _ps_walk_extras(it, out, depth + 1)

def _ps_extras_from_json(data, addr, out, strict=True):
    """Pull badge flags + token socials out of a PinkSale JSON blob.
    strict=True (page data, may hold many pools) → only the dict that carries
    this sale's address is read; strict=False (pool-specific API) → whole blob."""
    try:
        pool = _ps_find_pool_dict(data, addr)
        if pool is None:
            if strict:
                return
            pool = data
        _ps_walk_extras(pool, out)
    except Exception as e:
        logger.warning(f"PinkSale extras parse failed: {e}")

def _ps_extras_from_page(info, out):
    try:
        r = requests.get(info.get("url", ""), headers=_LP_UA, timeout=8)
        if r.status_code != 200 or not r.text:
            return
        for m in re.finditer(r'<script[^>]*(?:__NEXT_DATA__|application/json)[^>]*>(.*?)</script>',
                             r.text, re.DOTALL | re.IGNORECASE):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            _ps_extras_from_json(data, info.get("address", ""), out, strict=True)
    except Exception as e:
        logger.warning(f"PinkSale page extras failed: {e}")

def _ps_title_socials(info):
    """Read the token's REAL links = the icons (website / telegram / instagram ...)
    printed right under the project title on the PinkSale page, i.e. the links
    between the title heading and the 'About' section. The old code took the
    first t.me link of the whole page, which is PinkSale's own 'Support' link
    in the top menu (t.me/babypinkpink). If the block cannot be located safely
    we return nothing — no link is better than a wrong link."""
    out = {}
    try:
        r = requests.get(info.get("url", ""), headers=_LP_UA, timeout=10)
        if r.status_code != 200 or not r.text:
            return out
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.find(string=lambda t: t and t.strip().lower() == "about")
        if node is None:
            return out
        links, hit_heading = [], False
        for el in node.previous_elements:
            nm = getattr(el, "name", None)
            if nm in ("h1", "h2", "h3"):
                hit_heading = True
                break
            if nm == "a" and el.get("href"):
                links.append(el["href"].strip())
            if len(links) > 10:
                break
        if not hit_heading:
            return out
        for href in reversed(links):
            low = href.lower()
            if not low.startswith("http"):
                continue
            if "t.me/" in low or "telegram.me/" in low:
                kind = "telegram"
            elif "x.com/" in low or "twitter.com/" in low:
                kind = "twitter"
            elif "instagram.com" in low:
                kind = "instagram"
            elif "youtube.com" in low or "youtu.be" in low:
                kind = "youtube"
            elif "discord.gg" in low or "discord.com" in low:
                kind = "discord"
            elif "reddit.com" in low:
                kind = "reddit"
            else:
                kind = "website"
            u = _ps_norm_social(kind, href)
            if u and not out.get(kind):
                out[kind] = u
    except Exception as e:
        logger.warning(f"PinkSale title socials failed: {e}")
    return out

_PS_BADGE_IMG_RE = re.compile(r'(safu|audit|kyc|doxx)', re.I)

def _ps_badges_from_html(info, out):
    """PinkSale draws badges as small icons next to the project title (client
    rendered, so JSON keys are often missing). Look for <img>/<svg> whose
    src / alt / title / class names a badge, in the stretch of page between the
    title heading and the 'About' block (+ a little before the heading).
    Nothing is invented: if no badge icon is found, nothing is set."""
    try:
        r = requests.get(info.get("url", ""), headers=_LP_UA, timeout=10)
        if r.status_code != 200 or not r.text:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        about = soup.find(string=lambda t: t and t.strip().lower() == "about")
        if about is None:
            return
        head, seen_elems = None, []
        for el in about.previous_elements:
            nm = getattr(el, "name", None)
            if nm in ("h1", "h2", "h3"):
                head = el
                break
            if nm:
                seen_elems.append(el)
            if len(seen_elems) > 400:
                return
        if head is None:
            return
        for el in head.previous_elements:          # icons that sit just above the title
            nm = getattr(el, "name", None)
            if nm:
                seen_elems.append(el)
            if len(seen_elems) > 450:
                break
        for el in seen_elems:
            if el.name not in ("img", "svg", "image", "use", "span", "div"):
                continue
            blob = " ".join(str(el.get(k) or "") for k in ("src", "alt", "title", "aria-label", "data-src"))
            if el.name in ("span", "div"):
                blob = " ".join(el.get("class") or []) if el.get("class") else ""
                if el.find(True):                   # only leaf-like nodes
                    continue
            if "pinksale.finance/kyc" in blob.lower():
                continue
            for m in _PS_BADGE_IMG_RE.finditer(blob):
                b = m.group(1).lower()
                # an "unverified"/"no" style icon must not count as a badge
                if re.search(r'(no[-_ ]?|un|not[-_ ]?)' + b, blob.lower()):
                    continue
                out["badge_" + b] = "Yes"
                out["badges_seen"] = "Yes"
    except Exception as e:
        logger.warning(f"PinkSale badge scrape failed: {e}")

def _ps_short_num(n):
    """1000000000 → 1B, 2500000 → 2.5M, 10000000000000 → 10T (never a long digit string)."""
    n = float(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.2f}".rstrip("0").rstrip(".") + suf
    return (f"{n:,.4f}" if n < 1 else f"{n:,.2f}").rstrip("0").rstrip(".")

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
                _jd = r.json()
                _lp_walk_json(_jd, out)
                if info.get("platform") == "PinkSale":
                    _ps_extras_from_json(_jd, addr, out, strict=False)
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
        # PinkSale: the caller's post links are the CALLER's own socials, not
        # the token's — so they are no longer used. Badges + the token's real
        # socials come from PinkSale's own data only.
        # Anything set so far came from whole-page / whole-JSON scraping, which
        # also sees PinkSale's own menu/footer links → throw it away and use
        # only the icons under the project title.
        for _k in ("website", "twitter", "telegram", "youtube", "instagram", "discord", "reddit"):
            out.pop(_k, None)
        out.update(_ps_title_socials(info))
        _ps_badges_from_html(info, out)
        if not out.get("badges_seen") or not (out.get("twitter") or out.get("telegram")
                                              or out.get("website")):
            _ps_extras_from_page(info, out)
        for _k in ("telegram", "twitter", "website"):
            if out.get(_k) and not _ps_norm_social(_k, str(out[_k])):
                out.pop(_k, None)
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

# ── New PinkSale channel-post template (owner 2026-09-30) ──────────────────
# 9-emoji banner, in order (no spaces between them → forms the PinkSale banner)
PS_BANNER_EMOJI_IDS = [
    5902276935861411290, 5899992932382941003, 5902011257774416524,
    5899767300571013940, 5902258012235504858, 5900038798338697101,
    5902069836833365994, 5902433216836411625, 5899812170094355297,
]
PS_LABEL_EMOJI_ID   = 5902270497705436073   # replaces ❤️ (label rows)
PS_VALUE_EMOJI_ID   = 5902122690700914117   # replaces 💋 (value rows / time rows)
PS_HDR_TOKEN_ID     = 5899829384323276638   # TOKEN INFO header
PS_HDR_SALE_ID      = 5900110588717048148   # SALE Details header
PS_HDR_TIME_ID      = 5900254143703949266   # TIME Details header
PS_LINK_DETAILS_ID  = 5769182930219836102   # Details link
PS_LINK_PINKSALE_ID = 5769182930219836102   # Pinksale link
PS_LINK_CALLER_ID   = 5769533489745502351   # Caller link

# ── In-bot Details message (tap on "Details") — owner 2026-09-30 ───────────
PS_BADGE_LABEL_ID   = 5769424182827820166   # before "Badges:"
PS_BADGE_EMOJI      = {"audit": 5276030241217723326, "doxx": 5271468122660940510,
                       "kyc":   5269346146823712259, "safu": 5271891082450317227}
PS_BADGE_FALLBACK   = {"audit": "📋", "doxx": "🪪", "kyc": "🆔", "safu": "🛡️"}
PS_BADGE_ORDER      = ("safu", "audit", "kyc", "doxx", "business")
PS_BADGE_TITLES     = {"safu": "SAFU", "audit": "AUDIT", "kyc": "KYC",
                       "doxx": "DOXX", "business": "Business"}
PS_BADGE_ROW_PRE    = 5899871354743697500   # before each badge row
PS_BADGE_ROW_POST   = 5895537926015557276   # after each badge row
PS_CROSS_ID         = 5897632955227972480   # "no badges" cross
PS_HDR_SALELIQ_ID   = 5900067338396376568   # Sale & Liquidity header
PS_SL_ROW_ID        = 5902279268028653728   # PS Rate / Liq Rate / Liq % / Lock Time / Supply
PS_PARTNER_ID       = 5769324320543220241   # partners header
PS_SOCIAL_HDR_ID    = 5769653117469598004   # {Token}'s Socials header

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
    """PinkSale sale link, unmodified — owner asked to remove the affiliate
    ref= injection entirely, so PinkSale posts now use the plain sale link."""
    return url

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

# ── X-alert "Details" deep-link (WIZRAD INTELLIGENCE panel) ────────────────
# Telegram start-parameters are capped at 64 chars and channel/CA together
# routinely blow past that, so we store a short opaque id -> call_key
# mapping instead of encoding the call_key straight into the link (same
# approach the ps_/cp_ deep-links above already use).
_xalert_details_cache = None

def _xalert_details_map():
    global _xalert_details_cache
    if _xalert_details_cache is None:
        _xalert_details_cache = load_json(XALERT_DETAILS_FILE, {})
    return _xalert_details_cache

def _xalert_details_id(call_key: str) -> str:
    return hashlib.sha256(call_key.encode("utf-8")).hexdigest()[:12]

def _register_xalert_details(channel: str, ca: str, x_val=None) -> str:
    """Ensure this call's Details deep-link will resolve, and return its id."""
    call_key = f"{channel}_{ca}"
    sid = _xalert_details_id(call_key)
    m = _xalert_details_map()
    entry = {"call_key": call_key, "x_val": x_val}
    if m.get(sid) != entry:
        m[sid] = entry
        save_json(XALERT_DETAILS_FILE, m)
    return sid

_PS_SOCIAL_FIELDS = ("website", "twitter", "telegram", "youtube", "instagram", "discord", "reddit")
_PS_SHARERS_PENDING = {}   # "platform|addr" -> [{"channel","msg_id"}] (sharer seen before the record exists)

def _ps_key(info):
    return f"{info.get('platform')}|{(info.get('address') or '').lower()}"

def _ps_find_pid(projects, info):
    a = (info.get("address") or "").lower()
    if not a:
        return None
    for k, r in projects.items():
        if (r.get("address") or "").lower() == a and r.get("platform") == info.get("platform"):
            return k
    return None

def _ps_add_sharer(info, channel, msg_id):
    """Another caller posted a project we already alerted → remember them so the
    Details message can list them under 'partners who also shared this token'."""
    try:
        if not channel or not msg_id:
            return
        entry = {"channel": channel, "msg_id": str(msg_id)}
        projects = load_ps_projects()
        pid = _ps_find_pid(projects, info)
        if pid:
            rec = projects[pid]
            if (rec.get("channel") or "").lower() == channel.lower():
                return
            sh = rec.setdefault("sharers", [])
            if any((x.get("channel") or "").lower() == channel.lower() for x in sh):
                return
            sh.append(entry)
            del sh[:-20]
            save_ps_projects(projects)
        else:
            lst = _PS_SHARERS_PENDING.setdefault(_ps_key(info), [])
            if not any((x.get("channel") or "").lower() == channel.lower() for x in lst):
                lst.append(entry)
    except Exception as e:
        logger.warning(f"ps sharer add failed: {e}")

def _ps_store(info, details, channel, msg_id):
    """Persist a project so the Details deep-link can render it later."""
    projects = load_ps_projects()
    old_pid = _ps_find_pid(projects, info)
    old = projects.get(old_pid) if old_pid else None
    pid = old_pid or str(abs(hash(f"{info.get('platform')}|{info.get('address','').lower()}")) % (10 ** 10))
    # first caller stays the primary caller; later ones become "sharers"
    p_channel = (old or {}).get("channel") or channel
    p_msg     = (old or {}).get("msg_id") or str(msg_id or "")
    sharers = list((old or {}).get("sharers") or [])
    extra = list(_PS_SHARERS_PENDING.pop(_ps_key(info), []))
    if old and channel and channel.lower() != (p_channel or "").lower():
        extra.append({"channel": channel, "msg_id": str(msg_id or "")})
    for e in extra:
        c = (e.get("channel") or "")
        if not c or c.lower() == (p_channel or "").lower():
            continue
        if any((x.get("channel") or "").lower() == c.lower() for x in sharers):
            continue
        sharers.append(e)
    fmt = {}
    for k, v in (details or {}).items():
        # social links must not be cut at 70 chars like other display values
        fmt[k] = str(v)[:200] if k in _PS_SOCIAL_FIELDS else _lp_fmt_val(v)
    projects[pid] = {
        "platform": info.get("platform", "PinkSale"),
        "url":      info.get("url", ""),
        "address":  info.get("address", ""),
        "chain":    info.get("chain", ""),
        "channel":  p_channel,
        "msg_id":   p_msg,
        "sharers":  sharers[-20:],
        "details":  fmt,
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


_PS_W_NARROW = set("il.,:;!'|jI")
_PS_W_SMALL  = set("tfr()")
def _ps_tw(txt, bold=False):
    """Rough text width in 1/1000 em (Roboto/SF-like). Used only to pad the
    shorter of label/value so the premium emojis line up in both rows."""
    w = 0
    for ch in str(txt):
        if ch in _PS_W_NARROW:      w += 270
        elif ch in _PS_W_SMALL:     w += 350
        elif ch in "mwMW@":         w += 880
        elif ch == " ":             w += 250
        elif ch.isdigit() or ch == "$": w += 570
        elif ch == "%":             w += 730
        elif ch.isupper():          w += 650
        else:                       w += 560
    return w if bold else int(w * 0.96)

def _ps_pad(units):
    """Invisible NON-breaking spaces worth ~`units`/1000 em (line never wraps inside)."""
    out = ""
    for ch, wd in (("\u2007", 560), ("\u00a0", 250), ("\u202f", 180)):
        n = units // wd
        out += ch * n
        units -= n * wd
    return out

def _ps_grid(LBL, VAL, cols, limit=19000):
    """Premium-emoji grid. Labels are BOLD. Every column gets one common width
    (widest of label/value) and the shorter text is padded with invisible
    non-breaking spaces, so each emoji sits exactly above/below its partner even
    with a proportional font. Wide grids are split into rows (4 cols -> 2 + 2)."""
    cols = [(str(a), str(b)[:18]) for a, b in cols]
    n = len(cols)
    EMO = 1400                              # emoji + gap
    SEP = "\u00a0\u00a0\u00a0"              # 3 NBSP between columns
    for k in ([n, 2, 1] if n > 2 else list(range(n, 0, -1))):
        groups = [cols[i:i + k] for i in range(0, n, k)]
        widths = [max(max(_ps_tw(g[j][0], True), _ps_tw(g[j][1])) for g in groups if j < len(g))
                  for j in range(k)]
        if k == 1 or sum(w + EMO for w in widths) + 750 * (k - 1) <= limit:
            break
    rows = []
    for g in groups:
        lab = SEP.join(f"{LBL}\u00a0<b>{html.escape(a)}</b>" + _ps_pad(widths[j] - _ps_tw(a, True))
                       for j, (a, _) in enumerate(g))
        val = SEP.join(f"{VAL}\u00a0{html.escape(b)}" + _ps_pad(widths[j] - _ps_tw(b))
                       for j, (_, b) in enumerate(g))
        rows.append(lab + "\n" + val)
    return "\n".join(rows)


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

    # ── DEFAULT TEMPLATE (owner 2026-09-30) ──────────────────────────────────
    # Old "PINKSALE LAUNCH DETECTED / Project Info / CAP / BUY LIMITS / Time"
    # layout removed. Every premium emoji is embedded by explicit ID via
    # <tg-emoji>, so nothing depends on 🔮 marker order any more.
    def _e(eid, fb):
        return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

    LBL = _e(PS_LABEL_EMOJI_ID, "❤️")
    VAL = _e(PS_VALUE_EMOJI_ID, "💋")
    banner = "".join(_e(i, "🩷") for i in PS_BANNER_EMOJI_IDS)

    sym = str(_lp_fmt_val(details.get("symbol")) or "").strip().lstrip("$")
    token_plain = f"${sym}" if sym else str(name)
    chain_plain = _PS_CHAIN_DISPLAY.get(chain, chain) if chain else "N/A"
    handle    = html.escape(_display_handle(channel))

    t  = banner + "\n\n"
    t += (f"<b>@{handle} has posted a PinkSale project. "
          f"Click Details to view the project information.</b>\n\n")

    t += f"{_e(PS_HDR_TOKEN_ID, '🩷')} <b>TOKEN INFO</b>\n"
    t += ("<blockquote>" + _ps_grid(LBL, VAL, [
        ("Sale Type", _ps_sale_type(details, info)),
        ("Chain", chain_plain),
        ("TOKEN", token_plain),
    ]) + "</blockquote>\n\n")

    t += f"{_e(PS_HDR_SALE_ID, '🩷')} <b>SALE Details</b>\n"
    t += ("<blockquote>" + _ps_grid(LBL, VAL, [
        ("Min Buy", mnb or "N/A"),
        ("Max Buy", mxb or "N/A"),
        ("Softcap", soft or "N/A"),
        ("Hardcap", hard or "N/A"),
    ]) + "</blockquote>\n\n")

    t += f"{_e(PS_HDR_TIME_ID, '🩷')} <b>TIME Details</b>\n"
    t += (f"<blockquote>{VAL} <b>Starts:</b> {(kind + ' is already live') if live else (start or 'N/A')}\n"
          f"{VAL} <b>Ends:</b> {end or 'N/A'}</blockquote>\n\n")

    t += (f'{_e(PS_LINK_DETAILS_ID, "🩷")}<a href="{det_link}">Details</a>  '
          f'{_e(PS_LINK_PINKSALE_ID, "🩷")}<a href="{ps_link}">Pinksale</a>  '
          f'{_e(PS_LINK_CALLER_ID, "🩷")}<a href="{ch_link}">Caller</a>')

    # All emojis above carry explicit IDs (they win over positional ones), but
    # the send helpers bail out early on an EMPTY id list, so pass a non-empty
    # placeholder list.
    emoji_ids = [PS_EMOJI["pinksale"]]
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

    # ── DEFAULT DETAILS TEMPLATE (owner 2026-09-30) ──────────────────────────
    def pe(eid, fb):
        return _ps_pe(eid, fb) if premium else fb

    LBL = pe(PS_LABEL_EMOJI_ID, "❤️")
    VAL = pe(PS_VALUE_EMOJI_ID, "💋")
    sym    = str(d.get("symbol") or "").strip().lstrip("$")
    tname  = str(d.get("name") or "").strip() or sym or "Token"
    handle = html.escape(_display_handle(channel))
    chain_part = f"{html.escape(chain)} " if chain else ""
    tok_part   = f" ${html.escape(sym)}" if sym else ""

    t  = f"{_cem} <b>PINKSALE PROJECT DETAILS</b> {_cem}\n\n"
    t += (f"@{handle}, our partner, posted about a PinkSale {chain_part}token{tok_part}. "
          f"We’ve detected it. Here are the key details.\n\n")

    # SALE Details — same look as the channel post (emoji style + quote)
    mnb  = _ps_amount(d.get("min_buy"), chain) or "N/A"
    mxb  = _ps_amount(d.get("max_buy"), chain) or "N/A"
    soft = _ps_amount(d.get("soft_cap"), chain) or "N/A"
    hard = _ps_amount(d.get("hard_cap"), chain) or "N/A"
    t += f"{pe(PS_HDR_SALE_ID, '🩷')} <b>SALE Details</b>\n"
    t += ("<blockquote>" + _ps_grid(LBL, VAL, [
        ("Min Buy", mnb), ("Max Buy", mxb), ("Softcap", soft), ("Hardcap", hard),
    ]) + "</blockquote>\n\n")

    # Badges — only what the token really has
    have = [b for b in PS_BADGE_ORDER if str(d.get("badge_" + b) or "").lower() in ("yes", "true", "1")]
    known = bool(have) or str(d.get("badges_seen") or "").lower() in ("yes", "true", "1")
    if have:
        icons = " ".join(pe(PS_BADGE_EMOJI[b], PS_BADGE_FALLBACK[b]) for b in have if b in PS_BADGE_EMOJI)
        t += f"{pe(PS_BADGE_LABEL_ID, '🔶')} <b>Badges:</b> {icons}".rstrip() + "\n"
        pre, post = pe(PS_BADGE_ROW_PRE, "🩷"), pe(PS_BADGE_ROW_POST, "✅")
        for b in have:
            if b == "business":
                t += f"{pre} Business {post}\n"
            else:
                t += f"{pre} {PS_BADGE_TITLES[b]}: Yes {post}\n"
        t += "\n"
    elif known:
        t += (f"{pe(PS_BADGE_LABEL_ID, '🔶')} <b>Badges:</b>\n"
              f"This token has no badges. {pe(PS_CROSS_ID, '❌')}\n\n")

    # Sale & Liquidity — rows without data are simply left out
    rows = []
    v = _ps_clean(d.get("rate"))
    if v:
        rows.append(("PS Rate", v))
    v = _ps_clean(d.get("listing_rate"))
    if v:
        rows.append(("Liq Rate", v))
    v = _ps_clean(d.get("liquidity"), limit=16)
    if v:
        rows.append(("Liq %", v + "%" if re.fullmatch(r'\d+(?:\.\d+)?', v) else v))
    v = _ps_clean(d.get("lock_time"), limit=24)
    if v:
        if re.fullmatch(r'\d+', v):
            n = int(v)
            v = f"{n // 86400} days" if n >= 86400 else f"{n} days"
        rows.append(("Lock Time", v))
    m_sup = re.match(r'^\s*([\d.,]+)\s*([A-Za-z0-9$._-]{0,12})\s*$', str(d.get("supply") or ""))
    if m_sup:
        try:
            n_sup = float(m_sup.group(1).replace(",", ""))
        except ValueError:
            n_sup = 0
        if n_sup > 0:
            rows.append(("Supply", html.escape(f"{_ps_short_num(n_sup)} {(m_sup.group(2) or sym).strip()}".strip())))
    if rows:
        t += f"{pe(PS_HDR_SALELIQ_ID, '🩷')} <b>Sale &amp; Liquidity</b>\n"
        for label, val in rows:
            t += f"{pe(PS_SL_ROW_ID, '🩷')} {label}: {val}\n"
        t += "\n"

    # Partners who also shared this token (max 5)
    sh = []
    for x in (rec.get("sharers") or []):
        c = str(x.get("channel") or "").strip()
        if c and x.get("msg_id") and c.lower() != str(channel).lower():
            sh.append((c, x["msg_id"]))
    if sh:
        t += f"{pe(PS_PARTNER_ID, '🤝')} <b>These are our partners who also shared this token:</b>\n\n"
        for c, mid in sh[:5]:
            t += (f'@{html.escape(_display_handle(c))} — '
                  f'<a href="https://t.me/{html.escape(c)}/{html.escape(str(mid))}">Post Link</a>\n')
        t += "\n"

    # Token's socials — only links that really exist, never PinkSale's own
    def _soc(kind):
        u = _ps_norm_social(kind, str(d.get(kind) or ""))
        if not u:
            return ""
        low = u.lower()
        ch = str(channel or "").lower()
        if ch and re.search(r'(?:t\.me|telegram\.me|x\.com|twitter\.com)/@?' + re.escape(ch) + r'(?:[/?#]|$)', low):
            return ""          # that is the caller's own link, not the token's
        if "wizardscan" in low:
            return ""
        return html.escape(u, quote=True)

    labels = {"website": "Website", "twitter": "X", "telegram": "Telegram", "youtube": "YouTube",
              "instagram": "Instagram", "discord": "Discord", "reddit": "Reddit"}
    line1 = [f'<a href="{u}">{labels[k]}</a>' for k in ("website", "twitter", "telegram", "youtube") if (u := _soc(k))]
    line2 = [f'<a href="{u}">{labels[k]}</a>' for k in ("instagram", "discord", "reddit") if (u := _soc(k))]
    if line1 or line2:
        t += f"{pe(PS_SOCIAL_HDR_ID, '🌐')} <b>{html.escape(tname)}'s Socials</b>\n\n"
        if line1:
            t += " - ".join(line1) + "\n"
        if line2:
            t += " - ".join(line2) + "\n"
    return t.rstrip()

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
    _ch_norm = _PS_CHAIN_DISPLAY.get((info.get("chain") or "").upper(), "")
    if (info.get("chain") or "").upper() not in PS_SUPPORTED_CHAINS and _ch_norm not in PS_SUPPORTED_CHAINS:
        logger.info(f"PinkSale skipped — unsupported chain '{info.get('chain')}' ({info.get('address')})")
        return False
    try:
        if not force and not preview_to and key in _PINKSALE_SEEN:
            _ps_add_sharer(info, channel, msg_id)
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
                # forced_pack={} — without this, _build_premium_entities'
                # generic "🔮 Details" text-match (meant for the X-alert
                # Caller/Twitter/Trade/DEX/Details/DEV link row) was hijacking
                # PinkSale's "🔮<a>Details</a>" link too, replacing the
                # correct PS_EMOJI["details_btn"] with the X-alert pack's own
                # (different) details emoji. Empty pack disables all of those
                # X-alert-only special cases so PinkSale's own positional
                # emoji_ids (which already had the right id) are used as-is.
                sent = await _userbot_send_media_with_emoji(
                    bot, TARGET_CHANNEL, media["file_id"],
                    media.get("type", "video"), text, emoji_ids, forced_pack={})
            except Exception as e:
                logger.warning(f"PinkSale userbot media send failed: {e}")
        # 2) userbot text-only
        if not sent and userbot_client:
            sent = await _userbot_send_with_premium_emoji(
                TARGET_CHANNEL, text, emoji_ids, link_preview=False, forced_pack={})
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


def _html_visible_len(t):
    """Length Telegram counts against the caption limit (tags don't count)."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", t))
    return len(plain.encode("utf-16-le")) // 2

def _split_html_for_caption(text, limit=DETAILS_CAPTION_LIMIT):
    """Split text into (caption_part, rest). Length is measured on the VISIBLE
    text (premium-emoji / link tags don't count) and the cut is made on a blank
    line, so a <blockquote> is never cut in half."""
    if _html_visible_len(text) <= limit:
        return text, ""
    best = -1
    pos = text.find("\n\n")
    while pos != -1:
        if _html_visible_len(text[:pos]) <= limit:
            best = pos
        else:
            break
        pos = text.find("\n\n", pos + 2)
    if best > 0:
        return text[:best].rstrip(), text[best:].lstrip("\n")
    head = text[:limit]
    cut = head.rfind("\n")
    if cut < int(limit * 0.4):
        cut = head.rfind(" ")
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip(), text[cut:].lstrip("\n")


def _details_keyboard(kind, rec):
    """PinkSale Details message gets one button → the sale/fairlaunch link."""
    if kind != "ps":
        return None
    url = str((rec or {}).get("url") or "").strip()
    if not url.lower().startswith("http"):
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("Pinksale", url=ps_ref_link(url))]])


async def send_details_message(bot, chat_id, kind, builder, pid, rec):
    """Send the in-bot details text INSIDE the owner-set media caption.
    If the text is longer than the caption limit, the media carries the first
    part and the remainder is sent as a reply to that same media message so
    they stay visually connected. PinkSale gets a 'Pinksale' link button on the
    last message."""
    def _txt(premium=True):
        return builder(pid, rec, premium=premium)

    kb = _details_keyboard(kind, rec)
    item = _details_next_media(f"{kind}d_media", f"{kind}d_media_index")
    if not (item or {}).get("file_id"):
        # No separate Details media set (/addpsdmedia) → reuse the media of the
        # channel post (/addpsmedia / /addcpmedia) instead of sending text only.
        item = _ps_next_media() if kind == "ps" else _cp_next_media()
        logger.info(f"details media: no {kind}d_media set, fallback used={bool(item)}")
    fid = (item or {}).get("file_id")
    ftype = (item or {}).get("type", "video")

    if fid:
        for premium in (True, False):
            try:
                full = _txt(premium)
            except Exception:
                continue
            cap, rest = _split_html_for_caption(full)
            cap_kb = None if rest else kb
            try:
                if ftype == "photo":
                    msg = await bot.send_photo(chat_id, fid, caption=cap, parse_mode="HTML",
                                               reply_markup=cap_kb)
                elif ftype == "gif":
                    msg = await bot.send_animation(chat_id, fid, caption=cap, parse_mode="HTML",
                                                   reply_markup=cap_kb)
                else:
                    msg = await bot.send_video(chat_id, fid, caption=cap, parse_mode="HTML",
                                               supports_streaming=True, reply_markup=cap_kb)
                if rest:
                    try:
                        await bot.send_message(chat_id, rest, parse_mode="HTML",
                                               disable_web_page_preview=True,
                                               reply_to_message_id=getattr(msg, "message_id", None),
                                               reply_markup=kb)
                    except Exception:
                        await bot.send_message(chat_id, rest, parse_mode="HTML",
                                               disable_web_page_preview=True, reply_markup=kb)
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
                                   disable_web_page_preview=True, reply_markup=kb)
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

# ── CheesePad channel-post template emojis (owner 2026-10-02) ──
CP_BANNER_EMOJI_IDS = [5899930174320812054, 5902334767596051332, 5902167869461897083,
                       5902123923356525238, 5902133179011052382, 5902387673003204413,
                       5902505565560511339, 5902118606187012346, 5900214320767180333]
CP_LABEL_EMOJI_ID       = 5904530561036197958   # label rows (bold)
CP_VALUE_EMOJI_ID       = 5904447835671108691   # value rows + Starts/Ends
CP_HDR_TOKEN_ID         = 5904381010274951555   # TOKEN INFO
CP_HDR_SALE_ID          = 5774150171566808453   # SALE Details
CP_HDR_TIME_ID          = 5904657030643195190   # TIME Details
CP_LINK_DETAILS_ID      = 5904615201956700959   # Details
CP_LINK_CHEESEPAD_ID    = 5904530092884763234   # Cheesepad
CP_LINK_KOL_ID          = 5904633923719142949   # KOL

# ── CheesePad in-bot Details message emojis (owner 2026-10-02) ──
CP_DET_ROW_ID      = 5904447835671108691   # replaces every 🚀 row emoji
CP_DET_CHAIN_IDS   = {"BSC": 5904371350893501739, "BNB": 5904371350893501739,
                      "RH": 5904684316570427171, "BASE": 5907018485266915085,
                      "SOL": 5904318209263148254, "SOLANA": 5904318209263148254,
                      "ETH": 5904251379572023763, "ETHEREUM": 5904251379572023763}
CP_DET_HDR_PROJECT = 5904381010274951555
CP_DET_HDR_SALE    = 5774150171566808453
CP_DET_HDR_TOKENO  = 5904696991018917965
CP_DET_TOKEN_CA    = 5774033919687007833
CP_DET_SALE_ADDR   = 5773971281883965193
CP_DET_BUY         = 5904530092884763234
CP_DET_CALLER      = 5904633923719142949

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
    """Owner 2026-10-02: ref/affiliate injection removed. The link is returned
    exactly as the caller posted it (PinkSale AND CheesePad)."""
    return url


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

    # ── DEFAULT TEMPLATE (owner 2026-10-02) ──────────────────────────────────
    # Same layout as PinkSale, CheesePad emojis. Every premium emoji is embedded
    # by explicit ID via <tg-emoji>, so nothing depends on 🔮 marker order.
    def _e(eid, fb):
        return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

    LBL    = _e(CP_LABEL_EMOJI_ID, "🧀")
    VAL    = _e(CP_VALUE_EMOJI_ID, "🧀")
    banner = "".join(_e(i, "🧀") for i in CP_BANNER_EMOJI_IDS)

    sym = str(_lp_fmt_val(details.get("symbol")) or "").strip().lstrip("$")
    token_plain = f"${sym}" if sym else str(name)
    chain_plain = _PS_CHAIN_DISPLAY.get(chain, chain) if chain else "N/A"
    sale_plain  = "Whitelist sale" if _cp_sale_type(details, info) == "Whitelist" else "Public sale"
    handle      = html.escape(_display_handle(channel))

    t  = banner + "\n\n"
    t += (f"<b>@{handle} has posted a CheesePad project. "
          f"Click Details to view the project information.</b>\n\n")

    t += f"{_e(CP_HDR_TOKEN_ID, '🧀')} <b>TOKEN INFO</b>\n"
    t += ("<blockquote>" + _ps_grid(LBL, VAL, [
        ("Sale Type", sale_plain),
        ("Chain", chain_plain),
        ("TOKEN", token_plain),
    ]) + "</blockquote>\n\n")

    t += f"{_e(CP_HDR_SALE_ID, '🧀')} <b>SALE Details</b>\n"
    t += ("<blockquote>" + _ps_grid(LBL, VAL, [
        ("Min Buy", mnb or "N/A"),
        ("Max Buy", mxb or "N/A"),
        ("Softcap", soft or "N/A"),
        ("Hardcap", hard or "N/A"),
    ]) + "</blockquote>\n\n")

    t += f"{_e(CP_HDR_TIME_ID, '🧀')} <b>TIME Details</b>\n"
    t += (f"<blockquote>{VAL} <b>Starts:</b> {(kind + ' is already live') if live else (start or 'N/A')}\n"
          f"{VAL} <b>Ends:</b> {end or 'N/A'}</blockquote>\n\n")

    t += (f'{_e(CP_LINK_DETAILS_ID, "🧀")}<a href="{det_link}">Details</a>  '
          f'{_e(CP_LINK_CHEESEPAD_ID, "🧀")}<a href="{pool_link}">Cheesepad</a>  '
          f'{_e(CP_LINK_KOL_ID, "🧀")}<a href="{ch_link}">KOL</a>')

    # Explicit IDs win; the send helpers only need a NON-empty id list.
    return t, [CP_EMOJI["cheesepad"]]


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

    # ── DEFAULT DETAILS TEMPLATE (owner 2026-10-02) ──────────────────────────
    def E(eid, fb):
        return _cp_pe(eid, fb) if premium else fb

    R   = E(CP_DET_ROW_ID, "🧀")                       # row emoji (was 🚀)
    CHE = E(CP_DET_CHAIN_IDS.get(chain, CP_DET_CHAIN_IDS["BSC"]), "🧀")
    sym = str(d.get("symbol") or "").strip().lstrip("$")
    handle = html.escape(_display_handle(channel))

    def row2(label, val):
        return f"{R}<b>{label}:</b> {val}\n" if val else ""

    t  = f"{CHE} <b>CHEESEPAD Project Details</b> {CHE}\n\n"
    t += (f"<b>@{handle} has posted about the Cheesepad token "
          f"{('$' + html.escape(sym)) if sym else html.escape(str(name))}. "
          f"Below, we’re sharing some information about the token with you.</b>\n\n")

    t += f"{E(CP_DET_HDR_PROJECT, '🧀')} <b>PROJECT DETAILS</b>\n\n"
    t += f'{R}<b>Project:</b> <a href="{pool}">{html.escape(str(name))}</a>\n'
    t += row2("Caller", f"@{handle}")
    t += row2("Chain", html.escape(chain) if chain else "")
    t += row2("Sale Type", _cp_sale_type(d, info))
    t += row2("Token", html.escape(str(d.get("symbol") or name)))
    t += row2("Sale Currency", html.escape(_ps_clean(d.get("currency"), limit=16) or ""))
    t += "\n"

    t += f"{E(CP_DET_HDR_SALE, '🧀')} <b>SALE DETAILS</b>\n\n"
    t += row2("Soft Cap", _ps_amount(d.get("soft_cap"), chain) or "N/A")
    t += row2("Hard Cap", _ps_amount(d.get("hard_cap"), chain) or "No Hard Cap")
    _raised = _ps_amount(d.get("raised"), chain)
    if live or _raised:
        t += row2("Raised", _raised or _ps_zero(chain))
    t += row2("Min Buy", _ps_amount(d.get("min_buy"), chain) or "No Min Buy")
    t += row2("Max Buy", _ps_amount(d.get("max_buy"), chain) or "No Max Buy")
    t += "\n"

    tk = ""
    tk += row2("Presale Rate", html.escape(_ps_clean(d.get("rate")) or ""))
    tk += row2("Listing Rate", html.escape(_ps_clean(d.get("listing_rate")) or ""))
    tk += row2("Liquidity", html.escape(_ps_clean(d.get("liquidity")) or ""))
    tk += row2("LP Lock", html.escape(_ps_clean(d.get("lock_time")) or ""))
    if tk:
        t += f"{E(CP_DET_HDR_TOKENO, '🧀')} <b>TOKENOMICS</b>\n\n" + tk + "\n"

    addr_blocks = []
    tok = d.get("token") or ""
    if tok and tok.lower() != (rec.get("address", "") or "").lower():
        addr_blocks.append(f"{E(CP_DET_TOKEN_CA, '🧀')} <b>TOKEN CA:</b>\n\n<code>{html.escape(tok)}</code>")
    if rec.get("address"):
        addr_blocks.append(f"{E(CP_DET_SALE_ADDR, '🧀')} <b>Sale Address:</b>\n\n<code>{html.escape(rec.get('address', ''))}</code>")
    if addr_blocks:
        t += "<blockquote>" + "\n\n".join(addr_blocks) + "</blockquote>\n\n"

    t += f'{E(CP_DET_BUY, "🧀")}<a href="{pool}">Buy on CheesePad</a>'
    if channel and msg_id:
        t += f'  |  {E(CP_DET_CALLER, "🧀")}<a href="https://t.me/{channel}/{msg_id}">Caller Post</a>'
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
                    forced_pack={})
            except Exception as e:
                logger.warning(f"CheesePad userbot media send failed: {e}")
        if not sent and userbot_client:
            sent = await _userbot_send_with_premium_emoji(
                TARGET_CHANNEL, text, emoji_ids, link_preview=False,
                forced_pack={})
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
