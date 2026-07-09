import asyncio, os, json, sys

API_ID   = int(os.environ.get("USERBOT_API_ID", "0"))
API_HASH = os.environ.get("USERBOT_API_HASH", "")
CODE     = sys.argv[1].strip().replace(" ", "") if len(sys.argv) > 1 else ""
PASSWORD = sys.argv[2].strip() if len(sys.argv) > 2 else ""

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    with open("/tmp/otp_state.json") as f:
        state = json.load(f)
    phone = state["phone"]
    phone_hash = state["hash"]
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=CODE, phone_code_hash=phone_hash)
    except Exception as e:
        if "SessionPasswordNeeded" in str(e) or "2FA" in str(e):
            if not PASSWORD:
                print("2FA_REQUIRED")
                await client.disconnect()
                return
            await client.sign_in(password=PASSWORD)
        else:
            print(f"ERROR: {e}")
            await client.disconnect()
            return
    session_str = client.session.save()
    me = await client.get_me()
    print(f"SUCCESS: @{me.username or me.first_name}")
    print(f"SESSION: {session_str}")
    await client.disconnect()

asyncio.run(main())
