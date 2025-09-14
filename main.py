import os
import json
import re
from flask import Flask, request, abort
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext, ConversationHandler, Dispatcher
)
from datetime import datetime, timedelta

# --- إعدادات أساسية ---
DB_FILE = "database.json"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "2400366A")
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_URL = os.environ.get('RENDER_URL')
CRON_SECRET_KEY = os.environ.get('CRON_SECRET_KEY')

# حالات المحادثة
(AWAITING_ID, AWAITING_SUPPORT_MESSAGE, PASSWORD, MAIN_MENU, ADD_ID, ADD_DATE, ADD_STATUS, DELETE_MENU, EDIT_MENU, EDIT_DATE, EDIT_STATUS) = range(11)

# --- دوال التعامل مع قاعدة البيانات ---
def load_db():
    try:
        with open(DB_FILE, "r", encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}
def save_db(data):
    with open(DB_FILE, "w", encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

def escape_markdown(text: str) -> str:
    if not text: return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- جزء الويب سيرفر (أساس البوت الآن) ---
app = Flask(__name__)

@app.route('/')
def home(): return "Bot is running with Webhook!"

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook_handler():
    if request.is_json:
        update_json = request.get_json(force=True)
        update = Update.de_json(update_json, bot)
        dispatcher.process_update(update)
    return 'ok', 200

def check_subscriptions_once(bot: Bot) -> None:
    print(f"CRON JOB TRIGGERED: Running check...")
    db = load_db(); today = datetime.now().date()
    for key, info in db.items():
        date_str = info.get('renewal_date')
        if date_str:
            try:
                renewal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                if renewal_date - today == timedelta(days=2):
                    for user_id in info.get('favorited_by', []):
                        message = f"🔔 Напоминание: подписка для роутера *{escape_markdown(key)}* истекает *{escape_markdown(date_str)}*\\. Пожалуйста, продлите ее\\."
                        try: bot.send_message(chat_id=user_id, text=message, parse_mode='MarkdownV2')
                        except Exception as e: print(f"Error sending notification to {user_id}: {e}")
            except ValueError: print(f"Invalid date for {key}: {date_str}")
    print("Check finished.")

@app.route('/run-checks/<secret_key>')
def run_checks_endpoint(secret_key):
    if not CRON_SECRET_KEY or secret_key != CRON_SECRET_KEY: abort(401)
    if bot:
        Thread(target=check_subscriptions_once, args=(bot,)).start()
        return "Check process started."
    return "Bot instance not ready.", 503

# --- وظائف البوت ---
def start(update: Update, context: CallbackContext) -> None:
    # ... (الكود هنا زي ما هو بدون تغيير)
    welcome_text = "..."
    keyboard = [[InlineKeyboardButton("🔍 Найти роутер", callback_data='start_search')], [InlineKeyboardButton("💬 Техническая поддержка", callback_data='start_support')]]
    update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def favorites(update: Update, context: CallbackContext) -> None:
    # ... (الكود هنا زي ما هو بدون تغيير)
    
def favorite_button_handler(update: Update, context: CallbackContext) -> None:
    # ... (الكود هنا زي ما هو بدون تغيير)

# --- نظام المحادثات الموحد ---
def start_search(update: Update, context: CallbackContext) -> int:
    update.callback_query.edit_message_text("Пожалуйста, отправьте ID вашего роутера."); return AWAITING_ID

# ... (كل دوال المحادثات والإدارة من النسخة السابقة بالضبط)
def handle_search_input(update: Update, context: CallbackContext) -> int:
#...
def start_support(update: Update, context: CallbackContext) -> int:
#...
def handle_support_message(update: Update, context: CallbackContext) -> int:
#...
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
    if update.message: update.message.reply_text("Действие отменено.")
    context.user_data.clear(); return ConversationHandler.END

# --- إعداد البوت والـ Webhook ---
bot = Bot(TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

# إضافة كل الـ Handlers للـ dispatcher
conv_handler = ConversationHandler(
    entry_points=[CommandHandler('manage_routers', manage_start), CallbackQueryHandler(start_search, pattern='^start_search$'), CallbackQueryHandler(start_support, pattern='^start_support$')],
    states={
        AWAITING_ID: [MessageHandler(Filters.text & ~Filters.command, handle_search_input)], AWAITING_SUPPORT_MESSAGE: [MessageHandler(Filters.text & ~Filters.command, handle_support_message)],
        PASSWORD: [MessageHandler(Filters.text & ~Filters.command, check_password)], MAIN_MENU: [CallbackQueryHandler(main_menu_handler)],
        ADD_ID: [MessageHandler(Filters.text & ~Filters.command, add_get_id)], ADD_DATE: [MessageHandler(Filters.text & ~Filters.command, add_get_date)], ADD_STATUS: [MessageHandler(Filters.text & ~Filters.command, add_get_status)],
        DELETE_MENU: [CallbackQueryHandler(delete_confirm)], EDIT_MENU: [CallbackQueryHandler(edit_select_router)],
        EDIT_DATE: [MessageHandler(Filters.text & ~Filters.command, edit_get_date)], EDIT_STATUS: [MessageHandler(Filters.text & ~Filters.command, edit_get_status)],
    },
    fallbacks=[CommandHandler('cancel', cancel_conversation)], per_message=False, allow_reentry=True
)
dispatcher.add_handler(conv_handler)
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("favorites", favorites))
dispatcher.add_handler(CallbackQueryHandler(favorite_button_handler, pattern='^fav_.*$'))

# --- الوظيفة الرئيسية ---
if __name__ == '__main__':
    print("Setting webhook...")
    bot.set_webhook(url=f'{RENDER_URL}/{TOKEN}')
    print("Webhook is set. Starting Flask server...")
    # تشغيل الويب سيرفر
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
