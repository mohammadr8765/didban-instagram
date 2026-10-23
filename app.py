import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data.json"
ACCOUNTS_FILE = APP_DIR / "accounts.json"

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR = os.getenv("APIFY_ACTOR", "apify~instagram-scraper")

# Automatic check interval (default: once a day, minimum: 1 hour).
CHECK_INTERVAL = max(3600, int(os.getenv("CHECK_INTERVAL_SECONDS", "86400")))

# Only posts newer than this are requested from Apify (keeps cost low).
# Keep it larger than the check interval (e.g. "2 days" for daily checks).
LOOKBACK = os.getenv("LOOKBACK", "2 days")
MAX_POSTS_PER_PROFILE = int(os.getenv("MAX_POSTS_PER_PROFILE", "5"))

# Profile pictures are downloaded once to our own server (Instagram links
# expire) and only fetched again if the file is missing.
AVATAR_DIR = APP_DIR / "static" / "avatars"

# Thumbnails of each profile's latest post (downloaded directly from the
# Instagram CDN, so this costs no Apify credit).
POST_DIR = APP_DIR / "static" / "posts"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")

check_lock = threading.Lock()
state_lock = threading.Lock()
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


def now_utc():
    return datetime.now(timezone.utc).isoformat()


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
        {
            "profiles": {},
            "last_check": None,
            "last_error": None,
            "last_success_count": 0,
            "last_error_count": 0,
        },
    )


def save_data(data):
    write_json(DATA_FILE, data)


def profile_url(username):
    return f"https://www.instagram.com/{username}/"


def fetch_from_apify(usernames, details=False):
    """Run the Apify Instagram scraper once for all profiles and return items."""
    if details:
        payload = {
            "directUrls": [profile_url(u) for u in usernames],
            "resultsType": "details",
            "resultsLimit": 1,
        }
    else:
        payload = {
            "directUrls": [profile_url(u) for u in usernames],
            "resultsType": "posts",
            "resultsLimit": MAX_POSTS_PER_PROFILE,
            "onlyPostsNewerThan": LOOKBACK,
            "addParentData": False,
        }
    url = (
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}"
        f"/run-sync-get-dataset-items?timeout=280"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {APIFY_TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def avatar_file(username):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", username)
    return AVATAR_DIR / f"{safe}.jpg"


def avatar_url(username):
    path = avatar_file(username)
    if path.exists() and path.stat().st_size > 0:
        return f"/static/avatars/{path.name}?v={int(path.stat().st_mtime)}"
    return None


def refresh_avatars(usernames):
    """Download profile pictures that are missing."""
    needed = []
    for username in usernames:
        path = avatar_file(username)
        if not path.exists() or path.stat().st_size == 0:
            needed.append(username)
    if not needed:
        return

    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        items = fetch_from_apify(needed, details=True)
    except Exception:
        logger.exception("Avatar fetch from Apify failed")
        return

    wanted = {u.lower(): u for u in needed}
    saved = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        owner = (item.get("username") or "").lower()
        pic = item.get("profilePicUrlHD") or item.get("profilePicUrl")
        username = wanted.get(owner)
        if not username or not pic:
            continue
        try:
            request = urllib.request.Request(
                pic, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read()
            if content:
                avatar_file(username).write_bytes(content)
                saved += 1
        except Exception:
            logger.exception("Avatar download failed: %s", username)
    logger.info("Avatars saved: %s of %s", saved, len(needed))


def save_thumb(post):
    """Download a post thumbnail to our server. Returns the file name or None."""
    pic = post.get("displayUrl")
    if not pic:
        return None
    try:
        POST_DIR.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", post["shortCode"]) + ".jpg"
        request = urllib.request.Request(pic, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            content = response.read()
        if content:
            (POST_DIR / name).write_bytes(content)
            return name
    except Exception:
        logger.exception("Thumbnail download failed: %s", post.get("shortCode"))
    return None


def remove_thumb(name):
    if not name:
        return
    try:
        (POST_DIR / name).unlink()
    except OSError:
        pass


def check_profiles():
    if not check_lock.acquire(blocking=False):
        logger.warning("A profile check is already running")
        return None
    try:
        data = load_data()
        profiles = data.setdefault("profiles", {})
        usernames = accounts()

        def finish(errors, successes, rate_limited=False):
            data["last_check"] = now_utc()
            data["last_error"] = "; ".join(errors) if errors else None
            data["last_success_count"] = successes
            data["last_error_count"] = len(errors)
            data["rate_limited"] = rate_limited
            save_data(data)
            return data

        if not APIFY_TOKEN:
            msg = "APIFY_TOKEN is not set in Render environment variables"
            logger.error(msg)
            return finish([msg], 0)

        try:
            items = fetch_from_apify(usernames)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")[:300]
            logger.error("Apify HTTP %s: %s", exc.code, body)
            if exc.code in (402, 403):
                msg = "Apify credit is finished or access denied"
            else:
                msg = f"Apify error {exc.code}"
            for username in usernames:
                old = profiles.get(username, {})
                profiles[username] = {
                    **old,
                    "username": username,
                    "url": profile_url(username),
                    "status": "error",
                    "error": "بررسی ناموفق بود؛ جزئیات را در Render Logs ببینید.",
                    "checked_at": now_utc(),
                }
            return finish([msg], 0)
        except Exception as exc:
            logger.exception("Apify request failed")
            for username in usernames:
                old = profiles.get(username, {})
                profiles[username] = {
                    **old,
                    "username": username,
                    "url": profile_url(username),
                    "status": "error",
                    "error": "بررسی ناموفق بود؛ جزئیات را در Render Logs ببینید.",
                    "checked_at": now_utc(),
                }
            return finish([f"{type(exc).__name__}: {str(exc)[:200]}"], 0)

        if items:
            logger.info("Apify returned %s items; first keys: %s",
                        len(items), sorted(items[0].keys())[:20])

        # Group posts by owner username.
        by_user = {}
        error_items = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("error") and not item.get("shortCode"):
                error_items += 1
                logger.warning("Apify item error: %s", str(item)[:200])
                continue
            owner = (item.get("ownerUsername") or "").lower()
            if owner and item.get("shortCode"):
                by_user.setdefault(owner, []).append(item)

        for username in usernames:
            old = profiles.get(username, {})
            posts = by_user.get(username.lower(), [])
            posts.sort(key=lambda p: p.get("timestamp") or "", reverse=True)

            seen = list(old.get("seen", []))
            initialized = old.get("initialized", False)
            new_posts = [p for p in posts if p["shortCode"] not in seen]

            latest = old.get("latest")
            if posts:
                top = posts[0]
                old_latest = latest or {}
                thumb = None
                if old_latest.get("shortcode") == top["shortCode"]:
                    thumb = old_latest.get("thumb")
                if not thumb or not (POST_DIR / thumb).exists():
                    thumb = save_thumb(top)
                old_thumb = old_latest.get("thumb")
                if old_thumb and old_thumb != thumb:
                    remove_thumb(old_thumb)
                latest = {
                    "shortcode": top["shortCode"],
                    "date_utc": top.get("timestamp"),
                    "typename": top.get("type", ""),
                    "caption": (top.get("caption") or "")[:140],
                    "thumb": thumb,
                    "url": (
                        top.get("url")
                        or f"https://www.instagram.com/p/{top['shortCode']}/"
                    ),
                }

            for p in posts:
                if p["shortCode"] not in seen:
                    seen.append(p["shortCode"])

            profiles[username] = {
                "username": username,
                "url": profile_url(username),
                "latest": latest,
                "has_new": bool(
                    old.get("has_new", False) or (initialized and new_posts)
                ),
                "initialized": True,
                "seen": seen[-100:],
                "checked_at": now_utc(),
                "status": "ok",
                "error": None,
            }

        errors = []
        if error_items:
            errors.append(f"{error_items} profile(s) returned an error from Apify")
        successes = len(usernames)
        logger.info("Check finished: %s profiles, %s errors", successes, len(errors))
        result = finish(errors, successes)
        try:
            refresh_avatars(usernames)
        except Exception:
            logger.exception("Avatar refresh crashed")
        return result
    finally:
        check_lock.release()


def background_loop():
    # Wait briefly after startup, then check whenever the last check is old.
    time.sleep(15)
    while True:
        try:
            last = load_data().get("last_check")
            due = True
            if last:
                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(last)
                ).total_seconds()
                due = age >= CHECK_INTERVAL
            if due:
                logger.info("Starting scheduled profile check")
                check_profiles()
        except Exception:
            logger.exception("Scheduled profile check crashed")
        time.sleep(600)


def start_background_thread():
    global background_started
    with state_lock:
        if background_started:
            return
        background_started = True
    thread = threading.Thread(
        target=background_loop,
        name="profile-checker",
        daemon=True,
    )
    thread.start()
    logger.info("Background checker started; interval=%s seconds", CHECK_INTERVAL)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/explore")
def explore():
    return render_template("explore.html")


@app.route("/api/status")
def api_status():
    data = load_data()
    result = []
    for username in accounts():
        entry = data.get("profiles", {}).get(
            username,
            {
                "username": username,
                "url": profile_url(username),
                "latest": None,
                "has_new": False,
                "initialized": False,
                "status": "waiting",
                "error": None,
                "checked_at": None,
            },
        )
        item = {k: v for k, v in entry.items() if k != "seen"}
        item["avatar"] = avatar_url(username)
        latest = item.get("latest")
        if latest:
            latest = dict(latest)
            thumb = latest.pop("thumb", None)
            if thumb and (POST_DIR / thumb).exists():
                latest["thumb_url"] = f"/static/posts/{thumb}"
            else:
                latest["thumb_url"] = None
            item["latest"] = latest
        result.append(item)
    return jsonify({
        "accounts": result,
        "last_check": data.get("last_check"),
        "last_error": data.get("last_error"),
        "last_success_count": data.get("last_success_count", 0),
        "last_error_count": data.get("last_error_count", 0),
        "rate_limited": data.get("rate_limited", False),
        "check_interval_seconds": CHECK_INTERVAL,
    })


@app.post("/api/check")
def api_check():
    data = check_profiles()
    if data is None:
        return jsonify({
            "ok": False,
            "message": "بررسی دیگری در حال اجراست؛ کمی صبر کنید.",
        }), 409

    successes = data.get("last_success_count", 0)
    errors = data.get("last_error_count", 0)
    if successes == 0 and errors > 0:
        return jsonify({
            "ok": False,
            "message": "بررسی ناموفق بود؛ جزئیات را در Render Logs ببینید.",
            "last_check": data.get("last_check"),
            "successes": successes,
            "errors": errors,
            "rate_limited": data.get("rate_limited", False),
        }), 502

    return jsonify({
        "ok": True,
        "last_check": data.get("last_check"),
        "successes": successes,
        "errors": errors,
        "rate_limited": data.get("rate_limited", False),
    })


@app.post("/api/clear/<username>")
def api_clear(username):
    if username not in accounts():
        return jsonify({"ok": False, "error": "Unknown account"}), 404
    with state_lock:
        data = load_data()
        if username in data.get("profiles", {}):
            data["profiles"][username]["has_new"] = False
            save_data(data)
    return jsonify({"ok": True})


@app.get("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


# Also starts when the app is launched by Gunicorn.
start_background_thread()

if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
