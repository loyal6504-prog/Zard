# ═══════════════════════════════════════════════════════════════════════════
# CHEESEPAD — HARDCAP FILLED VIDEO  (owner 2026-10-20)
#   Base video (si_hardcap_filled_v3.mp4) ko har post ke liye patch karta hai:
#     • $SI              → project ka symbol
#     • N BNB / Raised / X BNB  → project ka hardcap (count-up animation same rehti hai)
#     • N participants   → real participants (nahi mile to wo text hide)
#     • right wala NEELA logo → caller channel ka profile logo
#   Baqi sab (wizard logo, HARDCAP FILLED, bar, glow) base video se hi aata hai.
#
#   Railway load: ~90 frames numpy/PIL + 1 ffmpeg encode (ultra-light, 1 time me 1 render,
#   nice 10, 2 threads). File post hote hi delete (cp_hc_cleanup) + purani temp dirs sweep.
#
#   Is block ko launchpad.py me cp_hardcap_patch.py ke UPAR/NEECHE paste karo
#   (bot.py sab ko ek namespace me load karta hai, is liye imports yahin hain).
# ═══════════════════════════════════════════════════════════════════════════
import os, re, io, time, shutil, tempfile, threading, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    _HCV_HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:                                   # exec()-loaded namespace
    _HCV_HERE = os.getcwd()

# repo me ye 2 cheezein rakho:  assets/si_hardcap_filled_v3.mp4  aur  assets/fonts/Poppins-*.ttf
HCV_BASE_VIDEO = os.environ.get("CP_HC_BASE_VIDEO") or os.path.join(_HCV_HERE, "assets", "si_hardcap_filled_v3.mp4")
HCV_FONT_DIRS  = [os.environ.get("CP_HC_FONT_DIR") or "",
                  os.path.join(_HCV_HERE, "assets", "fonts"),
                  os.path.join(_HCV_HERE, "fonts"),
                  _HCV_HERE,
                  "/usr/share/fonts/truetype/google-fonts"]

HCV_W, HCV_H = 1080, 462
HCV_SYM_COLOR = (253, 212, 9)
HCV_TAN_COLOR = (192, 176, 120)
HCV_WHITE     = (255, 255, 255)

_HCV_LOCK = threading.Lock()          # ek waqt me sirf ek render → Railway pe spike nahi


# ── fonts ────────────────────────────────────────────────────────────────────
_HCV_FONT_CACHE = {}

def _hcv_font(weight, size):
    key = (weight, int(size))
    if key in _HCV_FONT_CACHE:
        return _HCV_FONT_CACHE[key]
    fn = f"Poppins-{weight}.ttf"
    font = None
    for d in HCV_FONT_DIRS:
        p = os.path.join(d, fn) if d else ""
        if p and os.path.isfile(p):
            font = ImageFont.truetype(p, int(size)); break
    if font is None:
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
            if os.path.isfile(p):
                font = ImageFont.truetype(p, int(size)); break
    if font is None:
        font = ImageFont.load_default()
    _HCV_FONT_CACHE[key] = font
    return font


# ── text helpers ─────────────────────────────────────────────────────────────
def _hcv_text_layer(w, h, text, font, xy, anchor):
    """L-mode mask (float 0..1) jisme text draw hua ho."""
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text(xy, text, font=font, fill=255, anchor=anchor)
    return np.asarray(im, dtype=np.float32) / 255.0


def _hcv_blend(frame, x0, y0, mask, color, alpha):
    """frame[y0:y0+h, x0:x0+w] me color ko mask*alpha ke saath blend."""
    h, w = mask.shape
    reg = frame[y0:y0 + h, x0:x0 + w].astype(np.float32)
    m = (mask * alpha)[..., None]
    col = np.array(color, dtype=np.float32)[None, None, :]
    frame[y0:y0 + h, x0:x0 + w] = (reg * (1 - m) + col * m).astype(np.uint8)


def _hcv_cover(frame, x0, y0, x1, y1):
    """Purana text mita do: har row ko rect ke right-edge ke pixel se bhar do."""
    x1 = min(x1, HCV_W - 2)
    col = frame[y0:y1, x1 + 1:x1 + 2, :]
    frame[y0:y1, x0:x1, :] = col


def _hcv_fmt_amount(hard_num, p, decimals):
    v = hard_num * p
    if p >= 0.999:
        v = hard_num
    if decimals <= 0:
        return f"{int(v):,}" if hard_num >= 1000 else f"{int(v)}"
    return f"{v:.{decimals}f}".rstrip("0").rstrip(".") if p >= 0.999 else f"{v:.{decimals}f}"


def _hcv_parse_cap(hard_txt, cur_fallback="BNB"):
    """'40 BNB' / '1,250.5 ETH' → (40.0, 'BNB', decimals)."""
    s = str(hard_txt or "").strip()
    m = re.match(r"^\s*([\d,]*\.?\d+)\s*([A-Za-z0-9\.\$]*)", s)
    if not m:
        return None, cur_fallback, 0
    num_s = m.group(1).replace(",", "")
    try:
        num = float(num_s)
    except Exception:
        return None, cur_fallback, 0
    dec = len(num_s.split(".")[1]) if "." in num_s else 0
    return num, (m.group(2) or cur_fallback).upper(), min(dec, 3)


def _hcv_clean_symbol(sym, fallback="TOKEN"):
    s = re.sub(r"[^A-Za-z0-9\.\-_]", "", str(sym or "").lstrip("$"))[:14]
    return s or fallback


# ── logo ─────────────────────────────────────────────────────────────────────
def _hcv_prep_logo(logo_bytes, diameter, ss=4):
    """Caller logo → gol (circle) RGBA, diameter x diameter. None agar decode fail."""
    try:
        im = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    big = diameter * ss
    im = im.resize((big, big), Image.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", (big, big), 0), mask))
    return im.resize((diameter, diameter), Image.LANCZOS)


def _hcv_paste_logo(frame, logo_cache, logo_bytes, cx, cy, r, alpha):
    d = int(round(2 * r))
    if d < 8:
        return
    lg = logo_cache.get(d)
    if lg is None:
        lg = _hcv_prep_logo(logo_bytes, d)
        if lg is None:
            return
        logo_cache[d] = lg
    x0, y0 = int(round(cx - d / 2)), int(round(cy - d / 2))
    if x0 < 0 or y0 < 0 or x0 + d > HCV_W or y0 + d > HCV_H:
        return
    arr = np.asarray(lg, dtype=np.float32)
    a = (arr[..., 3:4] / 255.0) * alpha
    reg = frame[y0:y0 + d, x0:x0 + d].astype(np.float32)
    frame[y0:y0 + d, x0:x0 + d] = (reg * (1 - a) + arr[..., :3] * a).astype(np.uint8)


# ── per-frame patch ──────────────────────────────────────────────────────────
_BLUE_REF = {"n": None}


def _hcv_patch_frame(f, st):
    """f: (H,W,3) uint8 (in-place). st: dict(sym, hard_num, cur, dec, parts, logo, cache)."""
    R = f[:, :, 0].astype(np.int16)
    G = f[:, :, 1].astype(np.int16)
    B = f[:, :, 2].astype(np.int16)

    # 1) right logo (neela disc) ----------------------------------------------
    if st["logo"]:
        sub = (B[:210, 840:1060] > 150) & (R[:210, 840:1060] < 90) & (G[:210, 840:1060] < 120)
        n = int(sub.sum())
        if n > 300:
            ys, xs = np.where(sub)
            x0, x1, y0, y1 = xs.min() + 840, xs.max() + 840, ys.min(), ys.max()
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            r = max(x1 - x0, y1 - y0) / 2.0 + 1.0
            a = min(1.0, n / 2000.0)          # poora disc (~2300 px) = full opaque, fade-in me halka
            _hcv_paste_logo(f, st["cache"], st["logo"], cx, cy, r, a)

    # 2) $SYMBOL --------------------------------------------------------------
    rx0, ry0, rx1, ry1 = 270, 62, 860, 152
    TX0 = 281 - rx0                      # left-logo ka glow (x<281) text nahi hai
    reg = R[ry0:ry1, rx0:rx1]
    mk = reg > 60
    mk[:, :TX0] = False
    if mk.sum() > 40:
        ys, xs = np.where(mk)
        dx, dy = int(xs.min() + rx0 - 280), int(ys.min() + ry0 - 80)
        a = min(1.0, float(reg[:, TX0:].max()) / 250.0)
        # sirf purane text ke pixel (thoda phailake) kaale karo → glow par box nahi banta
        dm = mk.copy()
        for _ in range(3):
            t = dm.copy()
            t[1:, :] |= dm[:-1, :]; t[:-1, :] |= dm[1:, :]
            t[:, 1:] |= dm[:, :-1]; t[:, :-1] |= dm[:, 1:]
            dm = t
        sreg = f[ry0:ry1, rx0:rx1]
        sreg[dm] = (6, 6, 4)
        txt = "$" + st["sym"]
        size = 64
        font = _hcv_font("Bold", size)
        while font.getlength(txt) > 540 and size > 28:
            size -= 2
            font = _hcv_font("Bold", size)
        mask = _hcv_text_layer(rx1 - rx0, ry1 - ry0, txt, font,
                               (280 + dx - rx0, 131 + dy - ry0), "ls")
        _hcv_blend(f, rx0, ry0, mask, HCV_SYM_COLOR, a)

    # 3) card texts -----------------------------------------------------------
    col = f[240:462, 300, :].astype(np.int16)
    hit = np.where((col[:, 0] > 170) & (col[:, 1] > 130) & (col[:, 2] < 90))[0]
    if len(hit):
        ty = int(hit[0]) + 240
        # bar fill p (0..1)
        brow = f[ty + 76, 148:938, 0].astype(np.int16)
        dark = np.where(brow < 90)[0]
        p = 1.0 if len(dark) == 0 else float(dark[0]) / 790.0
        p = max(0.0, min(1.0, p))
        if p > 0.985:
            p = 1.0

        # card text visibility (fade-in)
        amt_reg = f[ty + 14:ty + 52, 312:620, :]
        ca = min(1.0, float(amt_reg.min(axis=2).max()) / 230.0)

        # amount "N BNB"
        _hcv_cover(f, 312, ty + 14, 620, ty + 52)
        amt = f"{_hcv_fmt_amount(st['hard_num'], p, st['dec'])} {st['cur']}"
        # baseline = ty + 46  → mask coords: (ty+46) - (ty+14) = 32
        m = _hcv_text_layer(308, 38, amt, _hcv_font("Bold", 36), (319 - 312, 32), "ls")
        _hcv_blend(f, 312, ty + 14, m, HCV_WHITE, ca)

        # "Raised / X BNB"
        cap_txt = f"{_hcv_fmt_amount(st['hard_num'], 1.0, st['dec'])} {st['cur']}"
        ra = f[ty + 92:ty + 132, 136:470, :]
        rca = min(1.0, float(ra.max()) / 190.0)
        _hcv_cover(f, 136, ty + 92, 470, ty + 132)
        m = _hcv_text_layer(334, 40, f"Raised / {cap_txt}", _hcv_font("Medium", 25), (139 - 136, 123 - 92), "ls")
        _hcv_blend(f, 136, ty + 92, m, HCV_TAN_COLOR, rca)

        # participants (right aligned @ x=940)
        _hcv_cover(f, 690, ty + 92, 946, ty + 132)
        if st["parts"] is not None:
            n_now = st["parts"] if p >= 1.0 else int(round(st["parts"] * p))
            m = _hcv_text_layer(256, 40, f"{n_now} participants", _hcv_font("Medium", 25),
                                (939 - 690, 123 - 92), "rs")
            _hcv_blend(f, 690, ty + 92, m, HCV_TAN_COLOR, rca)


# ── render ───────────────────────────────────────────────────────────────────
def _hcv_sweep(max_age=900):
    try:
        now = time.time()
        base = tempfile.gettempdir()
        for n in os.listdir(base):
            if n.startswith("cphc_"):
                p = os.path.join(base, n)
                if now - os.path.getmtime(p) > max_age:
                    shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def cp_hc_render_sync(symbol, hard_cap_txt, participants, logo_bytes, currency=None):
    """Blocking. Returns mp4 path (temp) ya None. Call karne ke baad cp_hc_cleanup(path) zaroor."""
    if not os.path.isfile(HCV_BASE_VIDEO):
        return None
    hard_num, cur, dec = _hcv_parse_cap(hard_cap_txt, currency or "BNB")
    if not hard_num or hard_num <= 0:
        return None
    if currency:
        cur = str(currency).upper()
    _hcv_sweep()
    out_dir = tempfile.mkdtemp(prefix="cphc_")
    out = os.path.join(out_dir, "hardcap.mp4")
    st = {"sym": _hcv_clean_symbol(symbol), "hard_num": hard_num, "cur": cur, "dec": dec,
          "parts": (int(participants) if participants is not None else None),
          "logo": logo_bytes, "cache": {}, "blue_ref": None}
    fsz = HCV_W * HCV_H * 3
    dec_p = enc_p = None
    try:
        with _HCV_LOCK:
            nice = lambda: os.nice(10)
            dec_p = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-threads", "2", "-i", HCV_BASE_VIDEO,
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, preexec_fn=nice)
            enc_p = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", f"{HCV_W}x{HCV_H}", "-r", "30", "-i", "-",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out],
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, preexec_fn=nice)
            nfr = 0
            while True:
                buf = dec_p.stdout.read(fsz)
                if len(buf) < fsz:
                    break
                fr = np.frombuffer(buf, dtype=np.uint8).reshape(HCV_H, HCV_W, 3).copy()
                _hcv_patch_frame(fr, st)
                enc_p.stdin.write(fr.tobytes())
                nfr += 1
            enc_p.stdin.close()
            enc_p.wait(timeout=60)
            dec_p.wait(timeout=10)
        if nfr < 30 or not os.path.isfile(out) or os.path.getsize(out) < 5000:
            shutil.rmtree(out_dir, ignore_errors=True)
            return None
        return out
    except Exception:
        for p_ in (dec_p, enc_p):
            try:
                if p_ and p_.poll() is None:
                    p_.kill()
            except Exception:
                pass
        shutil.rmtree(out_dir, ignore_errors=True)
        return None


def cp_hc_cleanup(path):
    """Post hone ke foran baad: video + uska temp folder delete."""
    try:
        if path and os.path.basename(os.path.dirname(path)).startswith("cphc_"):
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        elif path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


# ── caller ka logo ───────────────────────────────────────────────────────────
async def _cp_hc_fetch_logo(bot, channel):
    """Caller channel ki profile photo (bytes) ya None. 1) Bot API  2) userbot."""
    ch = (channel or "").lstrip("@")
    if not ch:
        return None
    try:
        chat = await bot.get_chat(f"@{ch}")
        ph = getattr(chat, "photo", None)
        if ph:
            tf = await bot.get_file(ph.big_file_id)
            bio = io.BytesIO()
            await tf.download_to_memory(bio)
            data = bio.getvalue()
            if data:
                return data
    except Exception as e:
        logger.info(f"cp_hc logo (bot api) @{ch}: {e}")
    try:
        if userbot_client:
            data = await userbot_client.download_profile_photo(ch, file=bytes)
            if data:
                return data
    except Exception as e:
        logger.info(f"cp_hc logo (userbot) @{ch}: {e}")
    return None


async def cp_hc_make_video(bot, w, snap):
    """Watch-entry + snapshot se video banao. None = base video/ffmpeg nahi → purana media use hoga."""
    try:
        d = snap["d"]
        sym = str(d.get("symbol") or d.get("name") or "").strip()
        logo = await _cp_hc_fetch_logo(bot, w.get("channel"))
        return await asyncio.to_thread(
            cp_hc_render_sync, sym, d.get("hard_cap"), snap.get("participants"),
            logo, d.get("currency"))
    except Exception as e:
        logger.warning(f"cp_hc video build failed: {e}")
        return None
