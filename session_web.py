"""
Web-based Telethon session generator — accessible at /session/
"""
import os, asyncio
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(os.environ.get("USERBOT_API_ID", "0"))
API_HASH = os.environ.get("USERBOT_API_HASH", "")
PORT     = int(os.environ.get("SESSION_PORT", "3002"))
BASE     = os.environ.get("SESSION_BASE", "/session")

_client = None
_phone_hash = None
_phone = None

CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f0f1a; color: #fff; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; padding: 20px; }
.card { background: #1a1a2e; border: 1px solid #2d2d4e; border-radius: 16px;
        padding: 36px; width: 100%; max-width: 480px; }
h2 { font-size: 22px; margin-bottom: 8px; color: #a78bfa; }
p.sub { color: #888; font-size: 14px; margin-bottom: 28px; line-height: 1.5; }
label { display: block; font-size: 13px; color: #aaa; margin-bottom: 6px; }
input { width: 100%; padding: 13px 16px; font-size: 16px; background: #0f0f1a;
        border: 1.5px solid #2d2d4e; border-radius: 10px; color: #fff;
        margin-bottom: 20px; outline: none; }
input:focus { border-color: #7c3aed; }
button { width: 100%; padding: 14px; font-size: 15px; font-weight: 600;
         background: linear-gradient(135deg, #7c3aed, #4f46e5);
         color: white; border: none; border-radius: 10px; cursor: pointer;
         transition: opacity .2s; }
button:hover { opacity: .88; }
.err { background: #3b1111; border: 1px solid #7f1d1d; color: #fca5a5;
       padding: 12px 16px; border-radius: 10px; font-size: 14px; margin-bottom: 20px; }
.info { background: #1e1b4b; border: 1px solid #3730a3; color: #a5b4fc;
        padding: 14px 16px; border-radius: 10px; font-size: 14px;
        margin-bottom: 20px; line-height: 1.6; }
.session-box { background: #0a0a14; border: 1.5px solid #7c3aed; border-radius: 10px;
               padding: 16px; font-family: monospace; font-size: 11px;
               word-break: break-all; color: #a78bfa; margin: 16px 0;
               max-height: 180px; overflow-y: auto; }
.copy-btn { background: #059669; }
.copy-btn:hover { opacity: .88; }
.back { display: block; text-align: center; margin-top: 16px;
        color: #6366f1; font-size: 14px; text-decoration: none; }
.step { display: inline-block; background: #7c3aed; color: white;
        width: 24px; height: 24px; border-radius: 50%; text-align: center;
        line-height: 24px; font-size: 12px; font-weight: bold; margin-right: 8px; }
.steps li { list-style: none; padding: 6px 0; font-size: 14px; color: #ccc; }
</style>
"""

def page(title, body):
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>{CSS}</head>
<body><div class="card">{body}</div></body></html>"""

STEP1 = lambda err="": page("Userbot Login", f"""
<h2>🔐 Userbot Login</h2>
<p class="sub">Premium emojis ke liye apna Telegram account connect karo. Sirf aapka SESSION_STRING generate hoga.</p>
{err}
<form method="POST" action="{BASE}/send_otp">
  <label>Phone Number (country code ke saath)</label>
  <input type="tel" name="phone" placeholder="+923001234567" required autofocus>
  <button type="submit">📨 OTP Bhejo</button>
</form>
""")

STEP2 = lambda err="": page("OTP Daalo", f"""
<h2>📱 OTP Daalo</h2>
<div class="info">
  ✅ Code bheja gaya!<br><br>
  <b>Telegram app kholo</b> → contacts mein sabse upar <b>"Telegram"</b> wali official chat dhundo → wahan 5-digit code hoga.<br><br>
  ⚠️ SMS nahi aata — app ke andar aata hai!
</div>
{err}
<form method="POST" action="{BASE}/verify_otp">
  <label>5-Digit Code</label>
  <input type="text" name="otp" placeholder="1 2 3 4 5" maxlength="10"
         style="letter-spacing:6px;text-align:center;font-size:22px" autofocus required>
  <button type="submit">✅ Verify Karo</button>
</form>
<a class="back" href="{BASE}/">↩ Naya OTP Bhejo</a>
""")

STEP_2FA = lambda err="": page("2FA Password", f"""
<h2>🔑 2FA Password</h2>
<p class="sub">Aapke account par 2-Step Verification hai. Cloud password daalo.</p>
{err}
<form method="POST" action="{BASE}/verify_2fa">
  <label>Cloud Password</label>
  <input type="password" name="password" placeholder="••••••••" autofocus required>
  <button type="submit">✅ Login Karo</button>
</form>
""")

def SUCCESS(username, session):
    return page("✅ Login Ho Gaya!", f"""
<h2>🎉 Login Ho Gaya! @{username}</h2>
<p class="sub">Ab SESSION_STRING copy karo aur Replit Secrets mein save karo.</p>
<label>SESSION_STRING (poori copy karo):</label>
<div class="session-box" id="s">{session}</div>
<button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('s').innerText);this.innerText='✅ Copy Ho Gaya!'">
  📋 Copy SESSION_STRING
</button>
<div style="margin-top:24px;background:#0f2211;border:1px solid #14532d;border-radius:10px;padding:16px">
  <p style="color:#86efac;font-weight:600;margin-bottom:10px">Ab ye karo:</p>
  <ul class="steps">
    <li><span class="step">1</span>Upar copy button dabao</li>
    <li><span class="step">2</span>Replit → 🔒 Secrets → <b>SESSION_STRING</b> naam se paste karo</li>
    <li><span class="step">3</span>Bot restart hoga → Premium emojis ON! 🚀</li>
  </ul>
</div>
""")

def ERR(msg):
    return f'<div class="err">❌ {msg}</div>'

async def index(request):
    return web.Response(text=STEP1(), content_type="text/html")

async def send_otp(request):
    global _client, _phone_hash, _phone
    data = await request.post()
    phone = data.get("phone","").strip()
    if not phone:
        return web.Response(text=STEP1(ERR("Phone number daalo!")), content_type="text/html")
    if not API_ID or not API_HASH:
        return web.Response(text=STEP1(ERR("USERBOT_API_ID ya USERBOT_API_HASH set nahi — Secrets check karo.")), content_type="text/html")
    try:
        if _client:
            try: await _client.disconnect()
            except: pass
        _client = TelegramClient(StringSession(), API_ID, API_HASH)
        await _client.connect()
        result = await _client.send_code_request(phone)
        _phone_hash = result.phone_code_hash
        _phone = phone
        return web.Response(text=STEP2(), content_type="text/html")
    except Exception as e:
        err = str(e)
        if "FLOOD_WAIT" in err:
            secs = ''.join(filter(str.isdigit, err)) or "kuch"
            return web.Response(text=STEP1(ERR(f"Telegram ne {secs} seconds ke liye block kiya — baad mein try karo.")), content_type="text/html")
        return web.Response(text=STEP1(ERR(str(e))), content_type="text/html")

async def verify_otp(request):
    global _client, _phone_hash, _phone
    data = await request.post()
    otp = data.get("otp","").strip().replace(" ","")
    if not _client or not _phone:
        return web.Response(text=STEP1(ERR("Session expire ho gaya — phone dobara daalo.")), content_type="text/html")
    try:
        await _client.sign_in(phone=_phone, code=otp, phone_code_hash=_phone_hash)
        me = await _client.get_me()
        sess = StringSession.save(_client.session)
        with open("userbot_session.txt","w") as f: f.write(sess)
        return web.Response(text=SUCCESS(me.username or me.first_name, sess), content_type="text/html")
    except Exception as e:
        err = str(e)
        if "SessionPasswordNeeded" in err or "two-step" in err.lower() or "PASSWORD" in err.upper():
            return web.Response(text=STEP_2FA(), content_type="text/html")
        if "PHONE_CODE_INVALID" in err:
            return web.Response(text=STEP2(ERR("Wrong code — dobara try karo.")), content_type="text/html")
        return web.Response(text=STEP2(ERR(str(e))), content_type="text/html")

async def verify_2fa(request):
    data = await request.post()
    pw = data.get("password","").strip()
    try:
        await _client.sign_in(password=pw)
        me = await _client.get_me()
        sess = StringSession.save(_client.session)
        with open("userbot_session.txt","w") as f: f.write(sess)
        return web.Response(text=SUCCESS(me.username or me.first_name, sess), content_type="text/html")
    except Exception as e:
        return web.Response(text=STEP_2FA(ERR(str(e))), content_type="text/html")

app = web.Application()
app.router.add_get(BASE+"/",       index)
app.router.add_get(BASE,           index)
app.router.add_post(BASE+"/send_otp",   send_otp)
app.router.add_post(BASE+"/verify_otp", verify_otp)
app.router.add_post(BASE+"/verify_2fa", verify_2fa)

if __name__ == "__main__":
    print(f"Session Generator: http://0.0.0.0:{PORT}{BASE}/")
    web.run_app(app, host="0.0.0.0", port=PORT)
