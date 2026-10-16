"""
champion_video.py — HD (1920x820) video generator for CHAMPION KOL X alerts.

Timeline (~7s):
  0-3s    GOLD intro: wizard logo (ALWAYS golden) + powerful burst effects
  3-3.6s  shockwave transition into the tier colour
  3.6-7s  details screen (KOL, rank, count-up multiplier, token, stats)
          coloured by multiplier tier:
            2-10x red | 11-20x blue | 21-30x orange | 31-50x green
            51-70x purple | 71-99x green+purple | 100x+ black & white

Needs: Pillow, numpy and ffmpeg (system ffmpeg OR `pip install imageio-ffmpeg`).
Public API:
    build_champion_video(...)        -> mp4 path (blocking)
    build_champion_video_async(...)  -> mp4 path or None (timeout-safe)
    tier_for(x), warmup()
"""
import asyncio
import hashlib
import logging
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("champion_video")

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
W, H, FPS = 1920, 820, 30
OUT_DIR = os.path.join(os.environ.get("DATA_DIR", HERE), "champion_videos")
def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return paths[-1]


# Works with BOTH layouts: files in repo root, or inside assets/
LOGO_PATH = _first_existing(os.environ.get("CHAMPION_LOGO"),
                            os.path.join(ASSETS, "wizard_logo.png"),
                            os.path.join(HERE, "wizard_logo.png"))
GOLD = (255, 190, 60)

# ───────────────────────── palettes ─────────────────────────
PALETTES = {
    "red":         dict(a=(255, 64, 64),  b=(255, 140, 110), bg=(34, 6, 8)),
    "blue":        dict(a=(60, 140, 255), b=(130, 195, 255), bg=(5, 14, 36)),
    "orange":      dict(a=(255, 140, 30), b=(255, 195, 90),  bg=(36, 16, 3)),
    "green":       dict(a=(40, 220, 110), b=(130, 255, 175), bg=(4, 28, 14)),
    "purple":      dict(a=(165, 95, 255), b=(210, 165, 255), bg=(20, 6, 38)),
    "greenpurple": dict(a=(40, 220, 110), b=(170, 100, 255), bg=(8, 12, 30)),
    "bw":          dict(a=(255, 255, 255), b=(170, 170, 170), bg=(0, 0, 0)),
}


VIDEO_PATH = _first_existing(os.environ.get("CHAMPION_LOGO_VIDEO"),
                             os.path.join(ASSETS, "wizard_logo.mp4"),
                             os.path.join(HERE, "wizard_logo.mp4"))
_VID_OFF = (14, -6)   # shifts the 720px video frame so the ring centre = image centre


def tier_for(x):
    x = float(x)
    if x >= 100: return "bw"
    if x >= 71:  return "greenpurple"
    if x >= 51:  return "purple"
    if x >= 31:  return "green"
    if x >= 21:  return "orange"
    if x >= 11:  return "blue"
    return "red"


def _smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _ease_out(u):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


# ───────────────────────── fonts / helpers ─────────────────────────
def _font_path():
    for p in (os.environ.get("CHAMPION_FONT"),
              os.path.join(ASSETS, "fonts", "Poppins-Bold.ttf"),
              os.path.join(HERE, "Poppins-Bold.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if p and os.path.exists(p):
            return p
    return None


@lru_cache(maxsize=64)
def _font(size):
    p = _font_path()
    return ImageFont.truetype(p, size) if p else ImageFont.load_default()


def _fit_font(text, size, max_w):
    while size > 18:
        f = _font(size)
        if f.getlength(text) <= max_w:
            return f
        size -= 2
    return _font(18)


def _ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg  # pip install imageio-ffmpeg (bundles a binary)
    return imageio_ffmpeg.get_ffmpeg_exe()


class _Enc:
    """ffmpeg encoder process. Same interface the render code already uses
    (.stdin.write / .stdin.close / .wait) but if ffmpeg dies we raise an error that
    contains the exit code + ffmpeg's own stderr, instead of a bare BrokenPipeError.
    (rc < 0 means killed by a signal: -9 OOM/kill, -4 illegal CPU instruction, -11 segfault.)"""

    def __init__(self, cmd):
        self._cmd0 = cmd[0]
        self._err = tempfile.TemporaryFile()
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=self._err)
        self.stdin = self

    def _why(self):
        try:
            rc = self.p.wait(timeout=5)
        except Exception:
            rc = self.p.poll()
        try:
            self._err.seek(0)
            tail = self._err.read()[-600:].decode("utf-8", "replace").strip()
        except Exception:
            tail = ""
        return f"ffmpeg died rc={rc} bin={self._cmd0} stderr={tail!r}"

    def write(self, data):
        try:
            return self.p.stdin.write(data)
        except (BrokenPipeError, OSError):
            raise RuntimeError(self._why()) from None

    def close(self):
        try:
            self.p.stdin.close()
        except (BrokenPipeError, OSError):
            raise RuntimeError(self._why()) from None

    def wait(self):
        rc = self.p.wait()
        if rc != 0:
            raise RuntimeError(self._why())
        try:
            self._err.close()
        except Exception:
            pass
        return rc


def _encoder(path):
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-g", str(FPS), "-movflags", "+faststart", path]
    return _Enc(cmd)


def _fmt_mc(v):
    if isinstance(v, str):
        return v if v.startswith("$") else "$" + v
    v = float(v or 0)
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _fmt_x(v, final):
    v = float(v)
    return f"{v:.1f}x" if (final < 10) else f"{int(round(v))}x"


def _circle_crop(img, d):
    img = img.convert("RGB").resize((d, d), Image.LANCZOS)
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d - 1, d - 1), fill=255)
    out = img.convert("RGBA")
    out.putalpha(m)
    return out


# ───────────────────────── GOLD intro (logo always golden) ─────────────────────────
class _Gold:
    D = 688  # logo image size (ring diameter on screen = 640px)

    def __init__(self):
        logo = Image.open(LOGO_PATH).convert("RGBA")
        self.logo_src = logo
        self.logo = logo.resize((self.D, self.D), Image.LANCZOS)
        self.cx, self.cy = W // 2, H // 2
        pos = (self.cx - self.D // 2, self.cy - self.D // 2)
        sil = Image.new("L", (W, H), 0)
        sil.paste(self.logo.split()[3], pos)
        self.glow1 = np.asarray(sil.filter(ImageFilter.GaussianBlur(70)), np.float32) / 255.0
        self.glow2 = np.asarray(sil.filter(ImageFilter.GaussianBlur(22)), np.float32) / 255.0
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.hypot((xx - W / 2) / (W * 0.55), (yy - H / 2) / (H * 0.9))
        bg = np.zeros((H, W, 3), np.float32)
        bg += np.array([16, 11, 4], np.float32) * np.exp(-r ** 2 * 2.2)[..., None]
        bg += np.array([3, 3, 3], np.float32)
        self.bg = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
        rng = random.Random(7)
        self.parts = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1.5, 4.2),
                       rng.uniform(6, 30), rng.uniform(0, 6.28)) for _ in range(110)]
        qh, qw = H // 4, W // 4
        ys, xs = np.mgrid[0:qh, 0:qw].astype(np.float32)
        self.ox, self.oy = self.cx / 4.0, (self.cy + 59) / 4.0  # orb centre
        dx, dy = xs - self.ox, ys - self.oy
        self.theta = np.arctan2(dy, dx)
        self.rq = np.hypot(dx, dy)
        self.pos = pos
        self.has_video = os.path.exists(VIDEO_PATH)
        self.alpha720 = self.logo_src.split()[3].resize((720, 720), Image.LANCZOS)

    @staticmethod
    def burst(t):
        if t < 1.5: return 0.0
        if t < 2.9: return _smooth((t - 1.5) / 1.4)
        if t < 3.7: return 1 - _smooth((t - 2.9) / 0.8)
        return 0.0

    def _up(self, q):
        im = Image.fromarray(np.clip(q * 255, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        return np.asarray(im, np.float32) / 255.0

    def _logo_frame(self, frame, scale):
        if frame is None:
            lg = self.logo
        else:
            canvas = Image.new("RGBA", (720, 720), (0, 0, 0, 255))
            canvas.paste(Image.fromarray(frame), _VID_OFF)
            canvas.putalpha(self.alpha720)
            lg = canvas
        dd = int(self.D * scale)
        return lg.resize((dd, dd), Image.LANCZOS), dd

    def render(self, t, frame=None, b=None):
        img = self.bg.copy()
        d = ImageDraw.Draw(img)
        for (x, y, r, sp, ph) in self.parts:
            yy = (y - sp * t) % H
            c = int(70 + 150 * (0.5 + 0.5 * math.sin(t * 2.2 + ph)))
            d.ellipse((x - r, yy - r, x + r, yy + r), fill=(c, int(c * .78), int(c * .3)))
        arr = np.asarray(img, np.float32)
        if b is None:
            b = self.burst(t)
        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.5)
        arr = arr + self.glow1[..., None] * np.array([255, 170, 40], np.float32) * (0.5 + 0.25 * pulse + 0.9 * b) * 0.55
        arr = arr + self.glow2[..., None] * np.array([255, 205, 95], np.float32) * (0.22 + 0.45 * b)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        s = 1 + 0.012 * math.sin(t * 2 * math.pi / 1.5) + (0.04 * b if frame is None else 0.0)
        lg, dd = self._logo_frame(frame, s)
        img.paste(lg, (self.cx - dd // 2, self.cy - dd // 2), lg)
        arr = np.asarray(img, np.float32)
        if frame is None and b > 0.01:   # our own rays only for the static-logo fallback
            rot = t * 0.9
            ray = np.clip(np.cos(self.theta * 14 + rot), 0, 1) ** 5
            ray2 = np.clip(np.cos(self.theta * 23 - rot * 1.7), 0, 1) ** 8
            fall = np.exp(-self.rq / 90.0) + 0.15 * np.exp(-self.rq / 220.0)
            inten = self._up(np.clip((ray * 0.8 + ray2 * 0.5) * fall * b, 0, 1))
            arr = arr + inten[..., None] * np.array([255, 215, 120], np.float32)
            fb = math.exp(-((t - 2.9) / 0.28) ** 2) + 0.35 * b
            fl = self._up(np.exp(-(self.rq / (16 + 34 * b)) ** 2) * min(1.0, fb))
            arr = arr + fl[..., None] * np.array([255, 235, 170], np.float32)
        return np.clip(arr, 0, 255).astype(np.uint8)

    def ring(self, R, width, strength, color):
        q = np.exp(-(((self.rq * 4) - R) / width) ** 2) * strength
        return self._up(np.clip(q, 0, 1))[..., None] * np.array(color, np.float32)


_GOLD = None
_GOLD_LOCK = None


def _gold():
    global _GOLD
    if _GOLD is None:
        _GOLD = _Gold()
    return _GOLD


def _iter_video_frames():
    """Yields the logo video as 30fps RGB uint8 arrays (720x720), one at a time."""
    cmd = [_ffmpeg(), "-v", "error", "-i", VIDEO_PATH, "-vf", "fps=30,scale=720:720",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = 720 * 720 * 3
    try:
        while True:
            buf = pr.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(720, 720, 3)
    finally:
        pr.stdout.close()
        pr.wait()


def _video_energy(fr):
    """0..1 'how bright is the orb' -> drives the golden glow behind the logo."""
    e = float(fr[370:490, 290:410].mean())
    return min(1.0, max(0.0, (e - 150.0) / 45.0))


def _video_last_frame():
    last = None
    for fr in _iter_video_frames():
        last = fr.copy()
    return last


def _intro_cache_path():
    """Intro is encoded by the SAME ffmpeg that encodes the details segment, so the
    two can be joined with a lossless stream copy. Cached on the volume (DATA_DIR)."""
    try:
        ver = subprocess.run([_ffmpeg(), "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    except Exception:
        ver = "unknown"
    try:
        vs = f"{os.path.getsize(VIDEO_PATH)}|{os.path.getsize(LOGO_PATH)}" if os.path.exists(VIDEO_PATH) else "static"
    except Exception:
        vs = "static"
    tag = hashlib.md5(f"{ver}|{W}x{H}|{FPS}|v2|{vs}".encode()).hexdigest()[:8]
    return os.path.join(OUT_DIR, f"intro_gold_{tag}.mp4")


def ensure_intro():
    """3s golden intro, rendered once (~15s on a small CPU) and cached."""
    os.makedirs(OUT_DIR, exist_ok=True)
    p = _intro_cache_path()
    if os.path.exists(p):
        return p
    g = _gold()
    tmp = p + ".tmp.mp4"
    enc = _encoder(tmp)
    total = int(3.0 * FPS)
    if g.has_video:
        for i, fr in enumerate(_iter_video_frames()):
            if i >= total:
                break
            enc.stdin.write(g.render(i / FPS, frame=fr, b=_video_energy(fr)).tobytes())
    else:
        for i in range(total):
            enc.stdin.write(g.render(i / FPS).tobytes())
    enc.stdin.close(); enc.wait()
    os.replace(tmp, p)
    return p


# ───────────────────────── details screen ─────────────────────────
def _chart_points(seed, n=420):
    rng = random.Random(seed)
    x0, x1 = 60, 1770
    y0, y1 = 610, 290
    k = 9
    ctrl = []
    for i in range(k + 1):
        u = i / k
        base = y0 + (y1 - y0) * (u ** 1.35)
        ctrl.append(base + (rng.uniform(-45, 45) if 0 < i < k else 0))
    xs = np.linspace(x0, x1, n)
    cx = np.linspace(x0, x1, k + 1)
    ys = np.interp(xs, cx, ctrl)
    ker = np.ones(25) / 25.0
    pad = np.pad(ys, 25, mode="edge")
    ys = np.convolve(pad, ker, mode="same")[25:-25]
    return xs, ys


def _grad_color(p, u):
    a, b = np.array(p["a"], np.float32), np.array(p["b"], np.float32)
    return tuple(int(v) for v in (a * (1 - u) + b * u))


def _build_static(p, kol_name, avatar_path, rank, symbol, chain, entry, now, points, calls, win_rate, seed):
    a, b = p["a"], p["b"]
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    base = np.zeros((H, W, 3), np.float32)
    base += np.array(p["bg"], np.float32) * (1.2 - 0.65 * yy)[..., None]
    tint = np.exp(-(((xx - .5) / .5) ** 2 + ((yy - .45) / .7) ** 2))
    base += np.array(a, np.float32) * (0.10 * tint)[..., None]
    stripe = np.zeros((1, W), np.float32)
    for x in range(20, W, 62):
        stripe[0, x:x + 3] = 1.0
    mod = (0.07 + 0.11 * np.exp(-(((xx - .5) / .38) ** 2)))
    base += (stripe * mod)[..., None] * np.array(a, np.float32) * 1.4
    rng = random.Random(seed)
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(img)
    for _ in range(70):
        x, y, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1.2, 3.2)
        c = rng.randint(60, 150)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*[int(v * c / 255) for v in b], 255))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    o = ImageDraw.Draw(ov)
    # header (KOL) box
    o.rounded_rectangle((40, 38, 800, 200), 44, fill=(0, 0, 0, 150), outline=(*a, 255), width=4)
    o.rounded_rectangle((1490, 38, 1880, 214), 40, fill=(0, 0, 0, 150), outline=(*a, 255), width=4)
    o.rounded_rectangle((40, 650, 1880, 792), 40, fill=(0, 0, 0, 165), outline=(*a, 255), width=4)
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)
    # avatar
    ax, ay, ad = 70, 66, 106
    if avatar_path and os.path.exists(avatar_path):
        try:
            av = _circle_crop(Image.open(avatar_path), ad)
            img.paste(av, (ax, ay), av)
        except Exception:
            avatar_path = None
    if not (avatar_path and os.path.exists(avatar_path)):
        d.ellipse((ax, ay, ax + ad, ay + ad), fill=(*[int(v * .35) for v in a], 255))
        d.text((ax + ad // 2, ay + ad // 2), (kol_name or "?").lstrip("@")[:1].upper(),
               font=_font(60), fill=(255, 255, 255, 255), anchor="mm")
    d.ellipse((ax - 3, ay - 3, ax + ad + 3, ay + ad + 3), outline=(*a, 255), width=5)
    name = (kol_name or "KOL").lstrip("@")
    d.text((205, 88), name, font=_fit_font(name, 64, 570), fill=(255, 255, 255, 255), anchor="lm")
    d.text((205, 154), "CHAMPION KOL  ·  X ALERT", font=_font(30), fill=(*b, 255), anchor="lm")
    # brand
    try:
        lg = Image.open(LOGO_PATH).convert("RGBA").resize((92, 92), Image.LANCZOS)
        img.paste(lg, (956, 74), lg)
    except Exception:
        pass
    bf = _font(54)
    d.text((1062, 120), "Wizard", font=bf, fill=(255, 255, 255, 255), anchor="lm")
    d.text((1062 + bf.getlength("Wizard "), 120), "Scan", font=bf, fill=(*a, 255), anchor="lm")
    # rank
    d.text((1685, 78), "RANK ON WIZARDSCANLIVE", font=_font(25), fill=(235, 235, 235, 255), anchor="mm")
    d.text((1685, 150), f"#{rank}", font=_font(96), fill=(255, 255, 255, 255), anchor="mm")
    # token line
    d.text((960, 585), f"${(symbol or 'TOKEN').upper()}  ·  {(chain or 'SOL').upper()}",
           font=_font(58), fill=(*b, 255), anchor="mm")
    # stats
    cols = [("ENTRY MC", _fmt_mc(entry)), ("NOW", _fmt_mc(now)), ("POINTS", str(points)),
            ("CALLS", str(calls)), ("WIN RATE", str(win_rate))]
    cw = (1880 - 40) / 5
    for i, (lab, val) in enumerate(cols):
        cx = int(40 + cw * (i + 0.5))
        d.text((cx, 685), lab, font=_font(27), fill=(190, 190, 200, 255), anchor="mm")
        d.text((cx, 745), val, font=_font(58), fill=(255, 255, 255, 255), anchor="mm")
    # soft glow behind the big multiplier
    arr = np.asarray(img.convert("RGB"), np.float32)
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    gl = np.exp(-(((gx - 960) / 520) ** 2 + ((gy - 400) / 190) ** 2))
    arr += gl[..., None] * np.array(a, np.float32) * 0.22
    return np.clip(arr, 0, 255).astype(np.uint8)


def _build_chart(p, seed):
    xs, ys = _chart_points(seed)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line)
    n = len(xs)
    for i in range(n - 1):
        col = _grad_color(p, i / n)
        ld.line((xs[i], ys[i], xs[i + 1], ys[i + 1]), fill=(*col, 255), width=13)
        ld.ellipse((xs[i] - 6.5, ys[i] - 6.5, xs[i] + 6.5, ys[i] + 6.5), fill=(*col, 255))
    glow = line.filter(ImageFilter.GaussianBlur(16))
    # area fill
    cy = np.interp(np.arange(W), xs, ys, left=np.nan, right=np.nan)
    gy = np.arange(H, dtype=np.float32)[:, None]
    dist = gy - cy[None, :]
    alpha = np.clip(1 - dist / 420.0, 0, 1) * (dist >= 0) * 0.30
    alpha[:, np.isnan(cy)] = 0
    col_row = np.array([_grad_color(p, min(1, max(0, (x - xs[0]) / (xs[-1] - xs[0])))) for x in range(W)], np.float32)
    area = np.zeros((H, W, 4), np.float32)
    area[..., :3] = col_row[None, :, :]
    area[..., 3] = alpha * 255
    layer = Image.alpha_composite(layer, Image.fromarray(area.astype(np.uint8), "RGBA"))
    layer = Image.alpha_composite(layer, glow)
    layer = Image.alpha_composite(layer, glow)
    layer = Image.alpha_composite(layer, line)
    return np.asarray(layer, np.float32), xs, ys


def _halo(color):
    s = 150
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((s // 2 - 34, s // 2 - 34, s // 2 + 34, s // 2 + 34), fill=(*color, 255))
    im = im.filter(ImageFilter.GaussianBlur(14))
    d = ImageDraw.Draw(im)
    d.ellipse((s // 2 - 13, s // 2 - 13, s // 2 + 13, s // 2 + 13), fill=(255, 255, 255, 255))
    return im


_TRANS_N = int(0.45 * FPS)
_TRANS_FRAMES = None


def _trans_frames(gold):
    """Golden frames for the 3.0-3.45s transition. Same for every alert -> cached."""
    global _TRANS_FRAMES
    if _TRANS_FRAMES is None:
        if gold.has_video:
            last = _video_last_frame()
            _TRANS_FRAMES = [gold.render(3.0 + i / FPS, frame=last, b=0.0) for i in range(_TRANS_N)]
        else:
            _TRANS_FRAMES = [gold.render(3.0 + i / FPS) for i in range(_TRANS_N)]
    return _TRANS_FRAMES


def _render_details(path, p, base, layer, xs, ys, x_final, gold):
    enc = _encoder(path)
    out = enc.stdin
    a = p["a"]
    halo = _halo(a)
    total = int(4.0 * FPS)
    trans = _TRANS_N
    rs, re = trans, int(2.4 * FPS)
    canvas = base.copy()                      # uint8, grows as the chart is revealed
    tf = _trans_frames(gold)
    ring_col = np.array(a, np.float32)
    prev = int(xs[0])
    last = None
    for i in range(total):
        if i < trans:
            u = _smooth(i / (trans - 1))
            t = i / FPS
            fr = tf[i].astype(np.float32) * (1 - u) + canvas.astype(np.float32) * u
            fr += gold.ring(1500 * _ease_out(t / 0.45), 55, (1 - t / 0.45) * 1.1, ring_col)
            out.write(np.clip(fr, 0, 255).astype(np.uint8).tobytes())
            continue
        if last is not None:
            out.write(last)
            continue
        u = _ease_out((i - rs) / (re - rs))
        rx = int(xs[0] + (xs[-1] - xs[0]) * u)
        if rx > prev:
            sl = layer[:, prev:rx + 1]
            al = sl[..., 3:4] / 255.0
            cur = canvas[:, prev:rx + 1].astype(np.float32)
            canvas[:, prev:rx + 1] = np.clip(cur * (1 - al) + sl[..., :3] * al, 0, 255).astype(np.uint8)
            prev = rx
        img = Image.fromarray(canvas)
        ty = float(np.interp(min(rx, xs[-1]), xs, ys))
        img.paste(halo, (int(rx - 75), int(ty - 75)), halo)
        d = ImageDraw.Draw(img)
        val = 1 + (x_final - 1) * u
        txt = _fmt_x(val, x_final) if i < re else _fmt_x(x_final, x_final)
        f = _font(250)
        d.text((960, 420), txt, font=f, fill=tuple(int(c * .35) for c in a), anchor="mm")
        d.text((960, 412), txt, font=f, fill=(255, 255, 255), anchor="mm")
        buf = img.tobytes()
        out.write(buf)
        if i >= re:
            last = buf
    enc.stdin.close()
    enc.wait()


# ───────────────────────── public API ─────────────────────────
def build_champion_video(*, kol_name, x_val, symbol, chain="SOL", entry_mc=0, now_mc=0,
                         rank=1, points=0, calls=0, win_rate="0%", avatar_path=None,
                         ca="", out_path=None):
    """Blocking. Returns path to a 1920x820 H.264 mp4."""
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    key = hashlib.md5(f"{kol_name}|{x_val}|{symbol}|{ca}|{entry_mc}|{now_mc}|{rank}|{points}|{calls}|{win_rate}".encode()).hexdigest()[:14]
    final = out_path or os.path.join(OUT_DIR, f"champ_{key}.mp4")
    if os.path.exists(final):
        return final
    x_val = float(x_val)
    p = PALETTES[tier_for(x_val)]
    seed = int(key, 16) % 10_000_000
    intro = ensure_intro()
    gold = _gold()
    base = _build_static(p, kol_name, avatar_path, rank, symbol, chain, entry_mc, now_mc,
                         points, calls, win_rate, seed)
    layer, xs, ys = _build_chart(p, seed)
    tmpd = tempfile.mkdtemp(prefix="champ_")
    try:
        seg = os.path.join(tmpd, "b.mp4")
        _render_details(seg, p, base, layer, xs, ys, x_val, gold)
        lst = os.path.join(tmpd, "l.txt")
        with open(lst, "w") as fh:
            fh.write(f"file '{os.path.abspath(intro)}'\nfile '{seg}'\n")
        tmp_out = final + ".part.mp4"
        r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                            "-i", lst, "-c", "copy", "-movflags", "+faststart", tmp_out],
                           capture_output=True)
        if r.returncode != 0:  # fall back to re-encode
            subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                            "-i", lst, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp_out], check=True)
        os.replace(tmp_out, final)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    log.info(f"champion video ready in {time.time()-t0:.1f}s -> {final}")
    _cleanup_old()
    return final


def _cleanup_old(max_age=3 * 3600):
    try:
        now = time.time()
        for f in os.listdir(OUT_DIR):
            fp = os.path.join(OUT_DIR, f)
            if f.startswith("champ_") and now - os.path.getmtime(fp) > max_age:
                os.remove(fp)
    except Exception:
        pass


_SEM = None


async def build_champion_video_async(timeout=25, **kw):
    """Non-blocking wrapper. Returns mp4 path, or None on any failure/timeout
    so the caller can fall back to the normal alert."""
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(2)
    try:
        async with _SEM:
            return await asyncio.wait_for(asyncio.to_thread(build_champion_video, **kw), timeout)
    except Exception as e:
        log.warning(f"champion video failed: {e!r}")
        return None


async def champion_alert_video(*, channel, x_val, chain, entry_fmt, cur_fmt, ca, symbol,
                               stats=None, avatar_path=None, timeout=None):
    """What send_alert() calls for a CHAMPION KOL X alert.
    entry_fmt / cur_fmt: already formatted MC strings (e.g. "$220K").
    stats: dict(rank=, points=, calls=, win_rate=) from the leaderboard data.
    Returns mp4 path, or None -> caller posts the normal alert instead."""
    stats = stats or {}
    timeout = timeout or float(os.environ.get("CHAMPION_VIDEO_TIMEOUT", "12"))
    return await build_champion_video_async(
        timeout=timeout, kol_name=channel, x_val=x_val, symbol=symbol, chain=chain,
        entry_mc=entry_fmt, now_mc=cur_fmt, rank=stats.get("rank", "-"),
        points=stats.get("points", "-"), calls=stats.get("calls", "-"),
        win_rate=stats.get("win_rate", "-"), avatar_path=avatar_path, ca=ca)


def warmup():
    """Call once at bot start (in a thread) so the first alert is fast."""
    try:
        t0 = time.time()
        ip = ensure_intro()
        _trans_frames(_gold())
        log.info(f"champion video warmup OK in {time.time()-t0:.1f}s ffmpeg={_ffmpeg()} intro={ip}")
    except Exception as e:
        log.warning(f"champion video warmup failed: {e!r}")


if __name__ == "__main__":
    import sys
    xs_ = [float(v) for v in sys.argv[1:]] or [5, 15, 25, 45, 60, 85, 100]
    for xv in xs_:
        t = time.time()
        out = build_champion_video(kol_name="Pump Watch", x_val=xv, symbol="GRAIL", chain="SOL",
                                   entry_mc=220_000, now_mc=220_000 * xv, rank=1, points=102,
                                   calls=21, win_rate="33%", ca=f"demo{xv}")
        print(xv, tier_for(xv), out, f"{time.time()-t:.1f}s")
