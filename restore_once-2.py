"""One-shot restore: bot_backup.json -> DATA_DIR files.
Runs only ONCE (marker file), backs up existing files first. Stdlib only.
Usage:  python restore_once.py   (then python bot.py)
"""
import json, os, shutil, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "bot_backup.json")
DATA_DIR = os.environ.get("DATA_DIR", ".")
MARKER = os.path.join(DATA_DIR, ".restored_from_backup")

# backup key -> file name (taken from core.py _get_data_files / _dp paths)
FILES = {
    "channels": "channels.json", "config": "bot_config.json",
    "tracked": "tracked_calls.json", "milestones": "sent_milestones.json",
    "seen": "seen_messages.json", "milestone_posts": "milestone_posts.json",
    "channel_points": "channel_points.json", "kol_owners": "kol_owners.json",
    "trending_cache": "trending_cache.json", "trending2_cache": "trending2_cache.json",
    "trending_bl": "trending_blacklist.json", "global_ca_bans": "global_ca_bans.json",
    "subscriptions": "subscriptions.json", "channel_subs": "channel_subs.json",
    "admins": "admins.json", "x_accounts": "x_accounts.json",
    "special_channels": "special_channels.json", "linked_channels": "linked_channels.json",
    "pending": "pending_requests.json", "momentum_sent": "momentum_sent.json",
    "momentum_reports": "momentum_reports.json", "xray_archive": "xray_archive.json",
    "users": "users.json", "call_archive": "call_archive.json",
    "milestone_times": "milestone_times.json", "ps_projects": "ps_projects.json",
    "xalert_details": "xalert_details.json", "priority_channels": "priority_channels.json",
    "removed_channels": "removed_channels.json", "pending_media": "pending_media_alerts.json",
    "broadcasts": "broadcasts.json",
}
# file names for these two are defined outside the files I was given -> saved aside, not guessed
UNMAPPED = ("buy_bots", "hot_streak_state")

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(MARKER) and "--force" not in sys.argv:
        print("Already restored once (marker found). Skipping."); return
    # --- disk report: show what is filling the volume ---
    try:
        tot, used, free = shutil.disk_usage(DATA_DIR)
        print(f"DISK: total={tot/1e6:.0f}MB used={used/1e6:.0f}MB free={free/1e6:.0f}MB")
        big = []
        for r, _, fs in os.walk(DATA_DIR):
            for fn in fs:
                fp = os.path.join(r, fn)
                try: big.append((os.path.getsize(fp), fp))
                except OSError: pass
        for sz, fp in sorted(big, reverse=True)[:15]:
            print(f"  {sz/1e6:8.1f}MB  {fp}")
        if free < 60e6:
            # volume full: delete throw-away generated videos (*.mp4 / *.mp4.tmp) only
            freed = 0
            for sz, fp in big:
                if fp.lower().endswith((".mp4", ".mp4.tmp", ".tmp")):
                    try:
                        os.remove(fp); freed += sz; print("  deleted cache:", fp)
                    except OSError:
                        pass
            free = shutil.disk_usage(DATA_DIR)[2]
            print(f"freed {freed/1e6:.0f}MB, free now {free/1e6:.0f}MB")
        if free < 60e6:
            print("ERROR: volume almost full (<60MB free). Resize the volume first, then redeploy. NOT restoring.")
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print("disk report failed:", e)
    with open(SRC, encoding="utf-8") as f:
        backup = json.load(f)
    print("Backup timestamp:", backup.get("_timestamp"))

    safe = os.path.join(DATA_DIR, "pre_restore_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(safe, exist_ok=True)
    for name in set(FILES.values()):
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(safe, name))

    n = 0
    for key, name in FILES.items():
        val = backup.get(key)
        if val is None:
            continue
        with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
            json.dump(val, f, indent=2, ensure_ascii=False)
        n += 1
        print(f"  restored {key:18s} -> {name}")
    for key in UNMAPPED:
        if backup.get(key) is not None:
            with open(os.path.join(DATA_DIR, f"_unmapped_{key}.json"), "w", encoding="utf-8") as f:
                json.dump(backup[key], f, indent=2, ensure_ascii=False)
    with open(MARKER, "w") as f:
        f.write(datetime.utcnow().isoformat())
    print(f"DONE: {n} files restored to {os.path.abspath(DATA_DIR)} (old files saved in {safe})")

if __name__ == "__main__":
    main()
