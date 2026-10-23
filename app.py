import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
import instaloader

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data.json"
ACCOUNTS_FILE = APP_DIR / "accounts.json"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "1800"))  # 30 minutes

app = Flask(__name__, static_folder="static", template_folder="templates")
lock = threading.Lock()

DEFAULT_ACCOUNTS = [
    "churtkeh.ir",
    "zackdfilms",
    "hamid.ghoraishi",
    "laboostube",
    "barcairanofficial",
    "5scoreofficial",
    "picopry",
    "maarstv",
]

def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback

def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def accounts():
    return read_json(ACCOUNTS_FILE, DEFAULT_ACCOUNTS)

def load_data():
    return read_json(DATA_FILE, {"profiles": {}, "last_check": None, "last_error": None})

def save_data(data):
    write_json(DATA_FILE, data)

def make_loader():
    # Public-profile checks only; Instagram may rate-limit or block unauthenticated access.
    loader = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )
    return loader

def check_profiles():
    with lock:
        data = load_data()
        profiles = data.setdefault("profiles", {})
        loader = make_loader()
        errors = []
        for username in accounts():
            old = profiles.get(username, {})
            try:
                profile = instaloader.Profile.from_username(loader.context, username)
                latest = None
                for post in profile.get_posts():
                    latest = {
                        "shortcode": post.shortcode,
                        "date_utc": post.date_utc.replace(tzinfo=timezone.utc).isoformat(),
                        "typename": getattr(post, "typename", ""),
                        "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    }
                    break

                old_latest = old.get("latest")
                initialized = old.get("initialized", False)
                is_new = bool(initialized and latest and old_latest and
                              latest.get("shortcode") != old_latest.get("shortcode"))
                # A fresh install establishes a baseline and does not mark every profile as new.
                profiles[username] = {
                    "username": username,
                    "url": f"https://www.instagram.com/{username}/",
                    "latest": latest,
                    "has_new": bool(old.get("has_new", False) or is_new),
                    "initialized": True,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "status": "ok",
                    "error": None,
                }
            except Exception as exc:
                errors.append(f"{username}: {type(exc).__name__}")
                profiles[username] = {
                    **old,
                    "username": username,
                    "url": f"https://www.instagram.com/{username}/",
                    "status": "error",
                    "error": "Instagram did not allow this check; try again later.",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            # A short pause reduces request bursts, but cannot prevent rate limits.
            time.sleep(2)

        data["last_check"] = datetime.now(timezone.utc).isoformat()
        data["last_error"] = "; ".join(errors) if errors else None
        save_data(data)
        return data

def background_loop():
    # Initial check after startup, then repeat at the configured interval.
    time.sleep(3)
    while True:
        try:
            check_profiles()
        except Exception:
            pass
        time.sleep(max(CHECK_INTERVAL, 900))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    data = load_data()
    result = []
    for username in accounts():
        entry = data.get("profiles", {}).get(username, {
            "username": username,
            "url": f"https://www.instagram.com/{username}/",
            "latest": None,
            "has_new": False,
            "initialized": False,
            "status": "waiting",
            "error": None,
            "checked_at": None,
        })
        result.append(entry)
    return jsonify({
        "accounts": result,
        "last_check": data.get("last_check"),
        "last_error": data.get("last_error"),
        "check_interval_seconds": CHECK_INTERVAL,
    })

@app.post("/api/check")
def api_check():
    # Manual check button. Can take a while; use sparingly to avoid rate limits.
    data = check_profiles()
    return jsonify({"ok": True, "last_check": data.get("last_check")})

@app.post("/api/clear/<username>")
def api_clear(username):
    if username not in accounts():
        return jsonify({"ok": False, "error": "Unknown account"}), 404
    with lock:
        data = load_data()
        if username in data.get("profiles", {}):
            data["profiles"][username]["has_new"] = False
            save_data(data)
    return jsonify({"ok": True})

@app.get("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")

if __name__ == "__main__":
    thread = threading.Thread(target=background_loop, daemon=True)
    thread.start()
    # Set HOST=0.0.0.0 only if you intentionally want LAN access.
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)
