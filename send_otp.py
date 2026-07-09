import asyncio, os, json, sys

API_ID   = int(os.environ.get("USERBOT_API_ID", "0"))
API_HASH = os.environ.get("USERBOT_API_HASH", "")
PHONE    = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("USERBOT_PHONE", "")

async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    result = await client.send_code_request(PHONE)
    with open("/tmp/otp_state.json", "w") as f:
        json.dump({"phone": PHONE, "hash": result.phone_code_hash}, f)
    await client.disconnect()
    print("OTP_SENT_OK")

asyncio.run(main())
