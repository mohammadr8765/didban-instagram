
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template
import instaloader

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data.json"
ACCOUNTS_FILE = APP_DIR / "accounts.json"
CHECK_INTERVAL = max(
    900, int(os.getenv("CHECK_INTERVAL_SECONDS", "1800"))
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
lock = threading.Lock()
background_started = False

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
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def accounts():
    result = read_json(ACCOUNTS_FILE, DEFAULT_ACCOUNTS)
    if not isinstance(result, list):
        logger.error("accounts.json must contain a JSON list")
        return DEFAULT_ACCOUNTS
    return result


def load_data():
    return read_json(
        DATA_FILE,
        {"profiles": {}, "last_check": None, "last_error": None},
    )


def save_data(data):
    write_json(DATA_FILE, data)


def make_loader():
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )


def check_profiles():
    with lock:
        data = load_data()
        profiles = data.setdefault("profiles", {})
        errors = []
        successes = 0
        loader = make_loader()

        for username in accounts():
            old = profiles.get(username, {})

            try:
                profile = instaloader.Profile.from_username(
                    loader.context, username
                )

                latest = None
                for post in profile.get_posts():
                    latest = {
                        "shortcode": post.shortcode,
                        "date_utc": post.date_utc.replace(
                            tzinfo=timezone.utc
                        ).isoformat(),
                        "typename": getattr(post, "typename", ""),
                        "url": (
                            f"https://www.instagram.com/p/"
                            f"{post.shortcode}/"
                        ),
                    }
                    break

                old_latest = old.get("latest")
                initialized = old.get("initialized", False)
                is_new = bool(
                    initialized
                    and latest
                    and old_latest
                    and latest["shortcode"] != old_latest["shortcode"]
                )

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
                successes += 1
                logger.info("Profile check succeeded: %s", username)

            except Exception as exc:
                detail = f"{type(exc).__name__}: {str(exc)[:250]}"
                errors.append(f"{username}: {detail}")

                profiles[username] = {
                    **old,
                    "username": username,
                    "url": f"https://www.instagram.com/{username}/",
                    "status": "error",
                    "error": (
                        "بررسی ناموفق بود؛ جزئیات در گزارش‌های Render ثبت شده."
                    ),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }

                logger.exception("Profile check failed: %s", username)

            time.sleep(2)

        data["last_check"] = datetime.now(timezone.utc).isoformat()
        data["last_error"] = "; ".join(errors) if errors else None
        data["last_success_count"] = successes
        data["last_error_count"] = len(errors)
        save_data(data)

        logger.info(
            "Check finished: %s successful, %s failed",
            successes,
            len(errors),
        )
        return data


def background_loop():
    time.sleep(10)

    while True:
        try:
            logger.info("Starting scheduled profile check")
            check_profiles()
        except Exception:
            logger.exception("Scheduled check crashed")

        time.sleep(CHECK_INTERVAL)


def start_background_thread():
    global background_started

    if background_started:
        return

    background_started = True
    thread = threading.Thread(
        target=background_loop,
        name="profile-checker",
        daemon=True,
    )
    thread.start()
    logger.info(
        "Background checker started; interval=%s seconds",
        CHECK_INTERVAL,
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    data = load_data()
    result = []

    for username in accounts():
        entry = data.get("profiles", {}).get(
            username,
            {
                "username": username,
                "url": f"https://www.instagram.com/{username}/",
                "latest": None,
                "has_new": False,
                "initialized": False,
                "status": "waiting",
                "error": None,
                "checked_at": None,
            },
        )
        result.append(entry)

    return jsonify({
        "accounts": result,
        "last_check": data.get("last_check"),
        "last_error": data.get("last_error"),
        "last_success_count": data.get("last_success_count", 0),
        "last_error_count": data.get("last_error_count", 0),
        "check_interval_seconds": CHECK_INTERVAL,
    })


@app.post("/api/check")
def api_check():
    try:
        data = check_profiles()
        successes = data.get("last_success_count", 0)
        errors = data.get("last_error_count", 0)

        if successes == 0 and errors > 0:
            return jsonify({
                "ok": False,
                "message": "بررسی همه حساب‌ها ناموفق بود.",
                "last_check": data.get("last_check"),
                "successes": successes,
                "errors": errors,
            }), 502

        return jsonify({
            "ok": True,
            "last_check": data.get("last_check"),
            "successes": successes,
            "errors": errors,
        })

    except Exception:
        logger.exception("Manual check crashed")
        return jsonify({
            "ok": False,
            "message": "خطای داخلی هنگام بررسی حساب‌ها",
        }), 500


@app.route("/api/clear/<username>", methods=["POST"])
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


# Runs under Gunicorn as well as when started directly.
start_background_thread()


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
    
