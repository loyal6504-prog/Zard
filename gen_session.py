"""
Run this in Replit Shell:
    cd bot && python gen_session.py
"""
import asyncio, os, sys

API_ID   = int(os.environ.get("USERBOT_API_ID", "0"))
API_HASH = os.environ.get("USERBOT_API_HASH", "")
PHONE    = os.environ.get("USERBOT_PHONE", "")

if not API_ID or not API_HASH:
    print("USERBOT_API_ID or USERBOT_API_HASH env var missing.")
    sys.exit(1)

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    phone = PHONE or input("Phone number daalo (e.g. +923001234567): ").strip()
    print(f"\nPhone: {phone}")
    print("Connecting...\n")

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    try:
        result = await client.send_code_request(phone)
    except Exception as e:
        print(f"Error: {e}")
        await client.disconnect()
        return

    print("=" * 55)
    print("CODE BHEJA GAYA!")
    print()
    print("CODE KAHAN MILEGA:")
    print("  Telegram app kholo phone par")
    print('  "Telegram" wali official chat dhundo')
    print("  (Contacts mein sabse upar hoti hai)")
    print("  Wahan 5-digit login code hoga")
    print()
    print("  NOTE: SMS nahi aata — Telegram app ke")
    print("  andar hi code aata hai official chat se")
    print("=" * 55)

    code = input("\nCode yahan type karo: ").strip().replace(" ", "")

    try:
        await client.sign_in(phone=phone, code=code,
                             phone_code_hash=result.phone_code_hash)
    except Exception as e:
        err = str(e)
        if "SessionPasswordNeeded" in err or "PASSWORD" in err.upper() or "2FA" in err:
            password = input("2FA password daalo: ").strip()
            try:
                await client.sign_in(password=password)
            except Exception as e2:
                print(f"2FA failed: {e2}")
                await client.disconnect()
                return
        else:
            print(f"Sign in failed: {e}")
            await client.disconnect()
            return

    session_str = client.session.save()
    me = await client.get_me()
    await client.disconnect()

    # Save to file — easier to copy from file editor than terminal
    out_file = os.path.join(os.path.dirname(__file__), "session_output.txt")
    with open(out_file, "w") as f:
        f.write(session_str)

    print("\n" + "=" * 55)
    print(f"LOGIN SUCCESSFUL! @{me.username or me.first_name}")
    print("=" * 55)
    print()
    print("✅ SESSION STRING FILE MEIN SAVE HO GAYI:")
    print(f"   tgbot/session_output.txt")
    print()
    print("Ab Replit file browser mein yeh file kholo,")
    print("string copy karo aur SESSION_STRING secret")
    print("mein update karo.")
    print("=" * 55)

asyncio.run(main())
