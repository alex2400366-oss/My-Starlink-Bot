import os
import json
import re
from flask import Flask, request, abort
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext, ConversationHandler
)
import time
from datetime import datetime, timedelta

# --- إعدادات أساسية ---
DB_FILE = "database.json"
ADMIN_PASSWORD = "2400366A"
# حالات المحادثة الموحدة
(CHOOSING, AWAITING_ID, # للبحث
 AWAITING_SUPPORT_MESSAGE, # للدعم
 PASSWORD, MAIN_MENU, # للقائمة الرئيسية
 ADD_ID, ADD_DATE, ADD_STATUS, # للإضافة
 DELETE_MENU, EDIT_MENU, EDIT_DATE, EDIT_STATUS # للحذف والتعديل
 ) = range(12)

# --- دوال التعامل مع قاعدة البيانات ---
def load_db():
    try:
        with open(DB_FILE, "r", encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}
def save_db(data):
    with open(DB_FILE, "w", encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

# --- دالة جديدة للحماية من أخطاء التنسيق ---
def escape_markdown(text: str) -> str:
    """تهريب كل العلامات الخاصة في MarkdownV2."""
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- جزء الويب سيرفر ---
app = Flask('')
bot_instance = None

@app.route('/')
def home(): return "Bot is running and ready for cron job!"

@app.route('/run-checks/<secret_key>')
def run_checks_endpoint(secret_key):
    CRON_KEY = os.environ.get('CRON_SECRET_KEY')
    if not CRON_KEY or secret_key != CRON_KEY:
        abort(401)
    
    if bot_instance:
        Thread(target=check_subscriptions_once, args=(bot_instance,)).start()
        return "Subscription check process started."
    else:
        return "Bot instance not ready.", 503

def run_flask(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run_flask, daemon=True).start()

# --- دالة فحص الاشتراكات ---
def check_subscriptions_once(bot: Bot) -> None:
    print(f"CRON JOB TRIGGERED AT {datetime.now()}: Running subscription check...")
    db = load_db()
    today = datetime.now().date()
    for key, router_info in db.items():
        renewal_date_str = router_info.get('renewal_date')
        if renewal_date_str:
            try:
                renewal_date = datetime.strptime(renewal_date_str, '%Y-%m-%d').date()
                if renewal_date - today == timedelta(days=2):
                    for user_id in router_info.get('favorited_by', []):
                        escaped_key = escape_markdown(key)
                        escaped_date = escape_markdown(renewal_date_str)
                        message = f"🔔 Напоминание: подписка для роутера *{escaped_key}* истекает *{escaped_date}*. Пожалуйста, продлите ее."
                        try:
                            bot.send_message(chat_id=user_id, text=message, parse_mode='MarkdownV2')
                        except Exception as e:
                            print(f"[ERROR] Could not send notification to user {user_id}. Reason: {e}")
            except ValueError:
                print(f"[WARNING] Invalid date format for router '{key}': '{renewal_date_str}'. Skipping.")
    print("Subscription check finished.")

# --- وظائف البوت العادية (باللغة الروسية) ---
def start(update: Update, context: CallbackContext) -> None:
    welcome_text = """
👋 Добро пожаловать в бот проверки подписки Starlink!  
Бот был разработан 

☠️Фараоном ☠️

📡 *Что делает этот бот?* Он помогает проверить статус и дату продления подписки на ваш роутер.

🛠 *Как использовать бот:* Нажмите «🔍 Поиск» и введите идентификатор роутера.  
Пример: `KIT-12345`
"""
    keyboard = [
        [InlineKeyboardButton("🔍 Найти роутер", callback_data='start_search')],
        [InlineKeyboardButton("💬 Техническая поддержка", callback_data='start_support')]
    ]
    update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def favorites(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    db = load_db()
    fav_list = [f"- `{escape_markdown(rid)}` (истекает: {escape_markdown(info.get('renewal_date', 'N/A'))})" for rid, info in db.items() if user_id in info.get('favorited_by', [])]
    message = "⭐ *Ваши роутеры в избранном:*\n\n" + "\n".join(fav_list) if fav_list else "Ваш список избранного пуст."
    update.message.reply_text(message, parse_mode='MarkdownV2')

def favorite_button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query; query.answer()
    router_id = query.data.split('_')[1]; user_id = query.from_user.id
    db = load_db(); info = db.get(router_id, {})
    if 'favorited_by' not in info: info['favorited_by'] = []
    if user_id not in info['favorited_by']:
        info['favorited_by'].append(user_id); db[router_id] = info; save_db(db)
        query.edit_message_text(text=f"✅ Роутер *{escape_markdown(router_id)}* успешно добавлен в избранное!", parse_mode='MarkdownV2')
    else: query.edit_message_text(text=f"ℹ️ Роутер *{escape_markdown(router_id)}* уже в вашем списке избранного.", parse_mode='MarkdownV2')

# --- نظام المحادثات الموحد (ConversationHandler) ---
def start_search(update: Update, context: CallbackContext) -> int:
    update.callback_query.edit_message_text("Пожалуйста, отправьте ID вашего роутера."); return AWAITING_ID

def handle_search_input(update: Update, context: CallbackContext) -> int:
    db = load_db(); router_id = update.message.text.strip().upper(); info = db.get(router_id)
    if info:
        text = f"🛰️ *Данные для роутера: {escape_markdown(router_id)}*\n\n*Статус:* {escape_markdown(info.get('status', 'N/A'))}\n*Дата продления:* {escape_markdown(info.get('renewal_date', 'N/A'))}"
        keyboard = [[InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f'fav_{router_id}')]]
        update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2')
        return ConversationHandler.END
    else:
        update.message.reply_text("❌ Пожалуйста, проверьте правильность ID роутера. Попробуйте еще раз:"); return AWAITING_ID

def start_support(update: Update, context: CallbackContext) -> int:
    text = "Напишите ваш запрос, и мы рассмотрим его в ближайшее время. Пожалуйста, укажите ID роутера."
    update.callback_query.edit_message_text(text); return AWAITING_SUPPORT_MESSAGE

def handle_support_message(update: Update, context: CallbackContext) -> int:
    ADMIN_ID = os.environ.get('ADMIN_CHAT_ID')
    if ADMIN_ID:
        user_info = update.message.from_user
        escaped_username = escape_markdown(user_info.username if user_info.username else "нет")
        forward_text = f"✉️ Новое сообщение в техподдержку от пользователя: @{escaped_username} (ID: `{user_info.id}`)"
        try:
            context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode='MarkdownV2')
            context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            update.message.reply_text("✅ Ваше сообщение отправлено администратору. Спасибо!")
        except Exception as e:
            print(f"ERROR: Could not forward message to admin. Reason: {e}"); update.message.reply_text("حدث خطأ أثناء إرسال رسالتك.")
    else: update.message.reply_text("К сожалению, система поддержки временно недоступна.")
    return ConversationHandler.END

# ... (باقي دوال الإدارة كلها زي ما هي بدون أي تغيير) ...
def manage_start(update: Update, context: CallbackContext) -> int:
#...
def check_password(update: Update, context: CallbackContext) -> int:
#...
def display_main_menu(update: Update, text: str) -> None:
#...
def main_menu_handler(update: Update, context: CallbackContext) -> int:
#...
def add_get_id(update: Update, context: CallbackContext) -> int:
#...
def add_get_date(update: Update, context: CallbackContext) -> int:
#...
def add_get_status(update: Update, context: CallbackContext) -> int:
#...
def delete_confirm(update: Update, context: CallbackContext) -> int:
#...
def edit_select_router(update: Update, context: CallbackContext) -> int:
#...
def edit_get_date(update: Update, context: CallbackContext) -> int:
#...
def edit_get_status(update: Update, context: CallbackContext) -> int:
#...
def cancel_conversation(update: Update, context: CallbackContext) -> int:
#...

# --- الوظيفة الرئيسية ---
def main() -> None:
    # ... (الكود هنا زي ما هو بدون تغيير) ...
