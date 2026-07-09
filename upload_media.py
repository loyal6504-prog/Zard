"""
One-time helper script: uploads command video to Telegram, gets file_id,
saves it to bot_config.json so /command uses it automatically.
Run: python upload_media.py
"""
import os, json, asyncio
from telegram import Bot

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID  = int(os.environ["OWNER_ID"])
VIDEO_PATH = "attached_assets/VID_20260613_095756_826_1783113812883.mp4"
CONFIG_FILE = "bot_config.json"

async def main():
    bot = Bot(token=BOT_TOKEN)
    print(f"Uploading video: {VIDEO_PATH}")
    with open(VIDEO_PATH, "rb") as vf:
        msg = await bot.send_video(
            chat_id=OWNER_ID,
            video=vf,
            caption="✅ /command video set ho gayi! (upload test)"
        )
    file_id = msg.video.file_id
    print(f"Got file_id: {file_id}")

    # Save to bot_config.json
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    cfg["command_media"] = {"file_id": file_id, "type": "video"}

    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print("✅ Saved to bot_config.json as command_media")

asyncio.run(main())
