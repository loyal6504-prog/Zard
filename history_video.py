"""
history_video.py — 2.5s, 1536x592 "CALLS HISTORY" card video for a channel record lookup.
Theme: dark teal background, gold + cyan accents, sky-blue glow around channel logo.

Public API:
    build_history_video(...)        -> mp4 path (blocking)
    build_history_video_async(...)  -> mp4 path or None (timeout-safe, never raises)
"""
import asyncio, logging, math, os, subprocess, tempfile, threading, time
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# reuse font / ffmpeg / circle-crop helpers from the champion video module
from champion_video import _font, _ffmpeg, _circle_crop, HERE

log = logging.getLogger("history_video")
W, H, FPS, DUR = 1536, 592, 30, 2.5
NFRAMES = int(round(FPS * DUR))
OUT_DIR = os.path.join(os.environ.get("DATA_DIR", HERE), "history_videos")

GOLD = (255, 200, 80)
CYAN = (70, 220, 235)
SKY = (90, 200, 255)
WHITE = (240, 248, 250)
MUTED = (150, 190, 195)
BG_IN, BG_OUT = (14, 62, 70), (3, 16, 22)


def _ease(u):
    u = max(0.0, min(1.0, u))
    return 1 - (1 - u) ** 3


def _seg(t, a, b):
    return _ease((t - a) / (b - a))


def _background():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W * 0.42) / (W * 0.75)) ** 2 + ((yy - H * 0.45) / (H * 0.95)) ** 2)
    d = np.clip(d, 0, 1)[..., None]
    a, b = np.array(BG_IN, np.float32), np.array(BG_OUT, np.float32)
    img = Image.fromarray((a * (1 - d) + b * d).astype(np.uint8), "RGB").convert("RGBA")
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle((6, 6, W - 7, H - 7), 28, outline=GOLD + (70,), width=2)
    return img


def _glow(size, color, radius, alpha):
    g = Image.new("RGBA", size, color + (0,))
    ImageDraw.Draw(g).ellipse((size[0] * .2, size[1] * .2, size[0] * .8, size[1] * .8),
                              fill=color + (int(alpha),))
    return g.filter(ImageFilter.GaussianBlur(radius))


def _avatar(path, name, d):
    if path and os.path.exists(path):
        try:
            return _circle_crop(Image.open(path), d)
        except Exception:
            pass
    im = Image.new("RGBA", (d, d), (20, 90, 100, 255))
    ch = (name or "?").lstrip("@")[:1].upper() or "?"
    dr = ImageDraw.Draw(im)
    f = _font(int(d * 0.55))
    dr.text((d / 2, d / 2), ch, font=f, fill=GOLD, anchor="mm")
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d - 1, d - 1), fill=255)
    im.putalpha(m)
    return im


def _text(layer, xy, s, size, fill, a=1.0, anchor="la"):
    if a <= 0:
        return
    ImageDraw.Draw(layer).text(xy, s, font=_font(size), fill=tuple(fill) + (int(255 * a),), anchor=anchor)


def _fmt_x(x):
    try:
        x = float(x)
    except Exception:
        return str(x)
    return f"{x:.1f}x" if x < 10 else f"{int(round(x))}x"


def build_history_video(*, channel, points=0, max_points=100, total_calls=0, alpha_calls=0,
                        best_x=0, best_sym="", consistency=0, days=0,
                        avatar_path=None, out_path=None, title="CALLS HISTORY", footer=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = out_path or os.path.join(OUT_DIR, f"hist_{int(time.time()*1000)}.mp4")
    handle = "@" + str(channel).lstrip("@")
    bg = _background()
    AV, CX, CY = 190, 215, 296          # avatar diameter / centre
    av = _avatar(avatar_path, channel, AV)
    glow_big = _glow((560, 560), SKY, 60, 200)
    glow_small = _glow((420, 420), CYAN, 30, 160)
    best_txt = _fmt_x(best_x)
    sym = ("$" + str(best_sym).lstrip("$").upper()) if best_sym else ""
    days_txt = footer or (f"Bot showed {days} day{'s' if days != 1 else ''} of record" if days
                else "Bot showed the full available record")

    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=tempfile.TemporaryFile())
    try:
        for i in range(NFRAMES):
            t = i / FPS
            f = bg.copy()
            # ---- logo with sky-blue glow (pulsing)
            pulse = 0.85 + 0.15 * math.sin(t * 2 * math.pi * 1.2)
            la = _seg(t, 0.0, 0.5)
            g1 = glow_big.copy(); g1.putalpha(g1.getchannel("A").point(lambda v: int(v * pulse * la)))
            f.alpha_composite(g1, (CX - 280, CY - 280))
            g2 = glow_small.copy(); g2.putalpha(g2.getchannel("A").point(lambda v: int(v * la)))
            f.alpha_composite(g2, (CX - 210, CY - 210))
            sc = 0.8 + 0.2 * _seg(t, 0.0, 0.55)
            d = int(AV * sc)
            a_img = av.resize((d, d), Image.LANCZOS)
            a_img.putalpha(a_img.getchannel("A").point(lambda v: int(v * la)))
            f.alpha_composite(a_img, (CX - d // 2, CY - d // 2))
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ring)
            R = d // 2 + 8
            rd.ellipse((CX - R, CY - R, CX + R, CY + R), outline=GOLD + (int(255 * la),), width=6)
            R2 = R + 14
            rd.ellipse((CX - R2, CY - R2, CX + R2, CY + R2), outline=SKY + (int(120 * la * pulse),), width=2)
            f.alpha_composite(ring)

            # ---- text layer
            L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            x0 = 410
            s = _seg(t, 0.15, 0.6); _text(L, (x0 + (1 - s) * 40, 62), handle, 46, WHITE, s)
            s = _seg(t, 0.25, 0.7); _text(L, (x0 + (1 - s) * 40, 120), title, 26, GOLD, s)
            ImageDraw.Draw(L).line((x0, 168, x0 + 560, 168), fill=GOLD + (int(90 * s),), width=2)

            cols = [("TOTAL POINTS", points, f"/{max_points}") if points is not None
                    else ("PERIOD", days, "D"), ("TOTAL CALLS", total_calls, ""),
                    ("ALPHA CALLS", alpha_calls, "")]
            for k, (lab, val, suf) in enumerate(cols):
                s = _seg(t, 0.4 + 0.12 * k, 0.95 + 0.12 * k)
                cx = x0 + k * 215
                _text(L, (cx, 205 + (1 - s) * 20), lab, 18, MUTED, s)
                _text(L, (cx, 232 + (1 - s) * 20), f"{int(round(val * s))}{suf}", 46, GOLD if k == 0 else WHITE, s)

            s = _seg(t, 0.8, 1.4)
            _text(L, (x0, 332 + (1 - s) * 20), "BEST CALL", 18, MUTED, s)
            bx = float(best_x or 0) * s
            _text(L, (x0, 358 + (1 - s) * 20), _fmt_x(bx) if bx else "-", 62, CYAN, s)
            if sym:
                _text(L, (x0 + _font(62).getlength(best_txt) + 24, 392 + (1 - s) * 20), sym, 30, CYAN, s)

            # ---- consistency ring
            RX, RY, RR = 1290, 276, 105
            s = _seg(t, 0.7, 1.7)
            _text(L, (RX, RY - 10), f"{int(round(consistency * s))}%", 52, WHITE, _seg(t, 0.7, 1.1), "mm")
            _text(L, (RX, RY + 38), "CONSISTENCY", 16, MUTED, _seg(t, 0.8, 1.2), "mm")
            rd = ImageDraw.Draw(L)
            rd.ellipse((RX - RR, RY - RR, RX + RR, RY + RR), outline=(255, 255, 255, 28), width=12)
            sweep = 360 * (consistency / 100.0) * s
            if sweep > 1:
                rd.arc((RX - RR, RY - RR, RX + RR, RY + RR), -90, -90 + sweep, fill=CYAN + (255,), width=12)

            # ---- bottom line (replaces the long project box)
            s = _seg(t, 1.3, 1.9)
            ImageDraw.Draw(L).line((80, 500, W - 80, 500), fill=CYAN + (int(70 * s),), width=2)
            _text(L, (W // 2, 545 - (1 - s) * 12), days_txt, 26, CYAN, s, "mm")

            f.alpha_composite(L)
            p.stdin.write(np.asarray(f.convert("RGB")).tobytes())
        p.stdin.close()
        rc = p.wait(timeout=60)
        if rc != 0:
            raise RuntimeError(f"ffmpeg rc={rc}")
    except Exception:
        try: p.kill()
        except Exception: pass
        raise
    return out_path


def _cleanup_old(max_age=3 * 3600):
    try:
        now = time.time()
        for fn in os.listdir(OUT_DIR):
            fp = os.path.join(OUT_DIR, fn)
            if fn.startswith("hist_") and now - os.path.getmtime(fp) > max_age:
                os.remove(fp)
    except Exception:
        pass


_LOCK = threading.Lock()


def _locked(kw):
    if not _LOCK.acquire(timeout=8):
        return None
    try:
        _cleanup_old()
        return build_history_video(**kw)
    finally:
        _LOCK.release()


async def build_history_video_async(timeout=20, **kw):
    """Returns mp4 path or None on any failure/timeout (caller falls back to text only)."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_locked, kw), timeout)
    except Exception as e:
        log.warning(f"history video failed: {e!r}")
        return None
