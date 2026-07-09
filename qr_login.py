"""
QR Code Login — No OTP needed!
Telegram app se scan karo aur session generate ho jaayega.
"""
import asyncio, os, base64, json, requests

API_ID   = int(os.environ.get("USERBOT_API_ID", "0"))
API_HASH = os.environ.get("USERBOT_API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int(os.environ.get("OWNER_ID", "0"))

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.auth import ExportLoginTokenRequest, AcceptLoginTokenRequest
    from telethon.tl.types import auth as tl_auth
    import qrcode, io

    print("Connecting to Telegram...")
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    attempt = 0
    while True:
        attempt += 1
        print(f"Attempt {attempt}: Requesting QR token...")
        try:
            result = await client(ExportLoginTokenRequest(
                api_id=API_ID, api_hash=API_HASH, except_ids=[]
            ))
        except Exception as e:
            print(f"ERROR getting token: {e}")
            await client.disconnect()
            return

        if isinstance(result, tl_auth.LoginTokenMigrateTo):
            print(f"Migrating to DC {result.dc_id}...")
            await client._switch_dc(result.dc_id)
            await client(AcceptLoginTokenRequest(token=result.token))
            continue

        if isinstance(result, tl_auth.LoginTokenSuccess):
            break

        # Build QR URL
        token_b64 = base64.urlsafe_b64encode(result.token).decode()
        qr_url = f"tg://login?token={token_b64}"

        # Compute expiry (handle both datetime and int)
        import time
        from datetime import datetime, timezone
        exp = result.expires
        if isinstance(exp, datetime):
            exp_ts = exp.timestamp()
        else:
            exp_ts = float(exp)
        secs_left = max(0, int(exp_ts - time.time()))

        # Generate QR image
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        # Send QR image to owner via Bot API
        caption = (
            "🔑 <b>QR Code se Login karo!</b>\n\n"
            "📱 <b>Steps:</b>\n"
            "1. Telegram app kholo\n"
            "2. Settings → Devices (ya Linked Devices)\n"
            "3. <b>Link Desktop Device</b> tap karo\n"
            "4. Camera se <b>yeh QR scan karo</b>\n"
            "5. <b>Confirm</b> karo\n\n"
            f"⏳ QR expires in {secs_left} seconds\n"
            "<i>Scan karo — session auto-generate ho jaayega!</i>"
        )
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": OWNER_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("qr.png", buf, "image/png")},
            timeout=10
        )
        print(f"QR sent to owner: {resp.status_code}")
        if resp.status_code != 200:
            print(resp.text)

        # Wait for scan
        print(f"Waiting for QR scan... (expires in ~{secs_left}s)")
        import time
        while time.time() < exp_ts:
            await asyncio.sleep(3)
            try:
                check = await client(ExportLoginTokenRequest(
                    api_id=API_ID, api_hash=API_HASH, except_ids=[]
                ))
                if isinstance(check, tl_auth.LoginTokenSuccess):
                    result = check
                    break
                if isinstance(check, tl_auth.LoginTokenMigrateTo):
                    await client._switch_dc(check.dc_id)
                    try:
                        check2 = await client(AcceptLoginTokenRequest(token=check.token))
                        if isinstance(check2, tl_auth.Authorization):
                            result = tl_auth.LoginTokenSuccess(authorization=check2)
                            break
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            print("QR expired! Generating new one...")
            continue
        break

    # Login success
    me = await client.get_me()
    session_str = client.session.save()
    await client.disconnect()

    print(f"\n{'='*55}")
    print(f"LOGIN SUCCESSFUL! @{me.username or me.first_name}")
    print(f"{'='*55}")
    print(f"\nSESSION STRING:\n{session_str}\n")

    # Save to file
    with open("session_output.txt", "w") as f:
        f.write(session_str)
    print("Saved to bot/session_output.txt")

    # Notify owner
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": OWNER_ID,
            "text": (
                f"✅ <b>Login successful!</b> @{me.username or me.first_name}\n\n"
                "📋 Session string generate ho gayi!\n\n"
                "Ab <b>Replit → Secrets → SESSION_STRING</b> mein yeh paste karo "
                "aur bot restart karo."
            ),
            "parse_mode": "HTML"
        }
    )

    print("\nNEXT STEPS:")
    print("1. Upar session string copy karo (ya bot/session_output.txt dekhna)")
    print("2. Replit → Secrets → SESSION_STRING update karo")
    print("3. Bot restart karo")

asyncio.run(main())
