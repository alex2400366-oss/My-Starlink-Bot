import os
import json
import re
import traceback
from flask import Flask, request, abort
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext, ConversationHandler, Dispatcher
)
from datetime import datetime, timedelta

# --- 1. الإعدادات الأساسية والنصوص ---
DB_FILE = "database.json"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "2400366A")
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_URL = os.environ.get('RENDER_URL')
CRON_SECRET_KEY = os.environ.get('CRON_SECRET_KEY')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID')

# حالات المحادثة
(AWAITING_ID, AWAITING_SUPPORT_MESSAGE, PASSWORD, MAIN_MENU, ADD_ID, ADD_DATE, ADD_STATUS, DELETE_MENU, EDIT_MENU, EDIT_DATE, EDIT_STATUS) = range(11)

# النصوص المركزية (لتسهيل التعديل والترجمة)
TEXTS = {
    "ru_welcome": """
👋 Добро пожаловать в бот проверки подписки Starlink!  
Бот был разработан 

☠️Фараоном ☠️

📡 *Что делает этот бот?* Он помогает проверить статус и дату продления подписки на ваш роутер.

🛠 *Как использовать бот:* Нажмите «🔍 Поиск» и введите идентификатор роутера.  
Пример: `KIT-12345`
""",
    "ru_search_prompt": "Пожалуйста, отправьте ID вашего роутера.",
    "ru_support_prompt": "Напишите ваш запрос, и мы рассмотрим его в ближайшее время. Пожалуйста, укажите ID роутера.",
    "ru_support_success": "✅ Ваше сообщение отправлено администратору. Спасибо!",
    "ru_support_fail_admin": "Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже.",
    "ru_support_fail_system": "К сожалению, система поддержки временно недоступна.",
    "ru_favorites_empty": "Ваш список избранного пуст.",
    "ru_favorites_title": "⭐ *Ваши роутеры в избранном:*\n\n",
    "ru_favorite_added": "✅ Роутер *{router_id}* успешно добавлен в избранное!",
    "ru_favorite_exists": "ℹ️ Роутер *{router_id}* уже в вашем списке избранного.",
    "ru_search_success": "🛰️ *Данные для роутера: {router_id}*\n\n*Статус:* {status}\n*Дата продления:* {renewal_date}",
    "ru_search_fail": "❌ Пожалуйста, проверьте правильность ID роутера. Попробуйте еще раз:",
    "ru_reminder": "🔔 Напоминание: подписка для роутера *{key}* истекает *{date}*. Пожалуйста, продлите ее.",
    "ru_cancel": "Действие отменено.",
    
    "ar_admin_password_prompt": "🔐 Эта область защищена. Пожалуйста, введите пароль:",
    "ar_admin_password_correct": "✅ Пароль верный. اختر الإجراء المطلوب:",
    "ar_admin_password_wrong": "❌ كلمة السر خاطئة.",
    "ar_admin_exit": "تم الخروج من قائمة الإدارة.",
    # ... etc for admin messages
}

# --- 2. دوال مساعدة (قاعدة البيانات والويب سيرفر) ---
def load_db():
    try:
        with open(DB_FILE, "r", encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_db(data):
    try:
        with open(DB_FILE, "w", encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[CRITICAL ERROR] Failed to save database: {e}")

def escape_markdown(text: str) -> str:
    if not text: return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

app = Flask(__name__)
bot_instance = None
@app.route('/')
def home(): return "Bot is running!"
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook_handler():
    if request.is_json:
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    return 'ok', 200

def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# --- 3. الوظائف الأساسية (الإشعارات، البداية، المفضلة) ---
def check_subscriptions_once(bot: Bot) -> None:
    print(f"CRON JOB TRIGGERED: Running check...")
    db = load_db(); today = datetime.now().date()
    for key, info in db.items():
        date_str = info.get('renewal_date')
        if not date_str: continue
        try:
            renewal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if renewal_date - today == timedelta(days=2):
                for user_id in info.get('favorited_by', []):
                    message = TEXTS["ru_reminder"].format(key=escape_markdown(key), date=escape_markdown(date_str))
                    try: bot.send_message(chat_id=user_id, text=message, parse_mode='MarkdownV2')
                    except Exception as e: print(f"Error sending notification to {user_id}: {e}")
        except ValueError: print(f"Invalid date for {key}: {date_str}")
    print("Check finished.")

@app.route('/run-checks/<secret_key>')
def run_checks_endpoint(secret_key):
    if not CRON_SECRET_KEY or secret_key != CRON_SECRET_KEY: abort(401)
    if bot_instance:
        Thread(target=check_subscriptions_once, args=(bot_instance,)).start()
        return "Check process started."
    return "Bot instance not ready.", 503

def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("🔍 Найти роутер", callback_data='start_search')],
        [InlineKeyboardButton("💬 Техническая поддержка", callback_data='start_support')]
    ]
    update.message.reply_text(TEXTS["ru_welcome"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def favorites(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id; db = load_db()
    fav_list = [f"- `{escape_markdown(rid)}` \\(истекает: {escape_markdown(info.get('renewal_date', 'N/A'))}\\)" for rid, info in db.items() if user_id in info.get('favorited_by', [])]
    message = TEXTS["ru_favorites_title"] + "\n".join(fav_list) if fav_list else TEXTS["ru_favorites_empty"]
    update.message.reply_text(message, parse_mode='MarkdownV2')

def favorite_button_handler(update: Update, context: CallbackContext) -> None:
    try:
        query = update.callback_query; query.answer(); router_id = query.data.split('_')[1]; user_id = query.from_user.id
        db = load_db(); info = db.get(router_id, {})
        if 'favorited_by' not in info: info['favorited_by'] = []
        if user_id not in info['favorited_by']:
            info['favorited_by'].append(user_id); db[router_id] = info; save_db(db)
            query.edit_message_text(text=TEXTS["ru_favorite_added"].format(router_id=escape_markdown(router_id)), parse_mode='MarkdownV2')
        else:
            query.edit_message_text(text=TEXTS["ru_favorite_exists"].format(router_id=escape_markdown(router_id)), parse_mode='MarkdownV2')
    except Exception as e:
        print(f"[ERROR] in favorite_button_handler: {e}")

# --- 4. نظام المحادثات الموحد (ConversationHandler) ---
def start_search(update: Update, context: CallbackContext) -> int:
    update.callback_query.edit_message_text(TEXTS["ru_search_prompt"]); return AWAITING_ID

def handle_search_input(update: Update, context: CallbackContext) -> int:
    db = load_db(); router_id = update.message.text.strip().upper(); info = db.get(router_id)
    if info:
        text = TEXTS["ru_search_success"].format(router_id=escape_markdown(router_id), status=escape_markdown(info.get('status', 'N/A')), renewal_date=escape_markdown(info.get('renewal_date', 'N/A')))
        keyboard = [[InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f'fav_{router_id}')]]
        update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2'); return ConversationHandler.END
    else:
        update.message.reply_text(TEXTS["ru_search_fail"]); return AWAITING_ID

def start_support(update: Update, context: CallbackContext) -> int:
    update.callback_query.edit_message_text(TEXTS["ru_support_prompt"]); return AWAITING_SUPPORT_MESSAGE

def handle_support_message(update: Update, context: CallbackContext) -> int:
    if ADMIN_CHAT_ID:
        try:
            user_info = update.message.from_user; user_message = update.message.text
            admin_text = (f"✉️ رسالة دعم جديدة\n"
                          f"--------------------------\n"
                          f"من: @{user_info.username} (ID: {user_info.id})\n"
                          f"--------------------------\n"
                          f"الرسالة:\n{user_message}")
            context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            update.message.reply_text(TEXTS["ru_support_success"])
        except Exception as e:
            print(f"ERROR: Could not send support message to admin. Reason: {e}"); update.message.reply_text(TEXTS["ru_support_fail_admin"])
    else: update.message.reply_text(TEXTS["ru_support_fail_system"])
    return ConversationHandler.END

def manage_start(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(TEXTS["ar_admin_password_prompt"]); return PASSWORD

def check_password(update: Update, context: CallbackContext) -> int:
    if update.message.text == ADMIN_PASSWORD: display_main_menu(update, TEXTS["ar_admin_password_correct"]); return MAIN_MENU
    else: update.message.reply_text(TEXTS["ar_admin_password_wrong"]); return ConversationHandler.END

def display_main_menu(update: Update, text: str) -> None:
    keyboard = [[InlineKeyboardButton("➕ إضافة راوتر", callback_data='add')], [InlineKeyboardButton("🗑️ حذف راوتر", callback_data='delete')], [InlineKeyboardButton("✏️ تعديل راوتر", callback_data='edit')], [InlineKeyboardButton("📋 عرض كل الروترات", callback_data='list')], [InlineKeyboardButton("❌ خروج", callback_data='exit')]]
    try:
        if update.callback_query: update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else: update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: print(f"Error in display_main_menu: {e}")

def main_menu_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query; action = query.data; db = load_db()
    if action == 'add': query.edit_message_text("➕ أرسل *معرف الراوتر*", parse_mode='Markdown'); return ADD_ID
    elif action in ['delete', 'edit']:
        text, state = ("🗑️ اختر الراوتر للحذف:", DELETE_MENU) if action == 'delete' else ("✏️ اختر الراوتر للتعديل:", EDIT_MENU)
        keyboard = [[InlineKeyboardButton(f"`{rid}`", callback_data=rid)] for rid in db.keys()]; keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='back')])
        if not db: query.edit_message_text("لا توجد روترات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='back')]])); return MAIN_MENU
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'); return state
    elif action == 'list':
        text = "*قائمة الروترات:*\n" + "\n".join([f"- `{rid}` ({info.get('status', 'N/A')})" for rid, info in db.items()]) if db else "لا توجد روترات."
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='back')]]), parse_mode='Markdown'); return MAIN_MENU
    elif action == 'back': display_main_menu(update, "اختر الإجراء:"); return MAIN_MENU
    elif action == 'exit': query.edit_message_text(TEXTS["ar_admin_exit"]); return ConversationHandler.END

def add_get_id(update: Update, context: CallbackContext) -> int:
    context.user_data['new_router_id'] = update.message.text.strip().upper(); update.message.reply_text("أرسل *تاريخ التجديد* (YYYY-MM-DD)", parse_mode='Markdown'); return ADD_DATE
def add_get_date(update: Update, context: CallbackContext) -> int:
    context.user_data['new_router_date'] = update.message.text.strip(); update.message.reply_text("أرسل *حالة الراوتر*", parse_mode='Markdown'); return ADD_STATUS
def add_get_status(update: Update, context: CallbackContext) -> int:
    db = load_db(); db[context.user_data['new_router_id']] = {'status': update.message.text.strip(), 'renewal_date': context.user_data['new_router_date']}; save_db(db)
    update.message.reply_text(f"✅ تم إضافة `{context.user_data['new_router_id']}`."); context.user_data.clear(); return ConversationHandler.END

def delete_confirm(update: Update, context: CallbackContext) -> int:
    router_id = update.callback_query.data; db = load_db()
    if router_id in db: del db[router_id]; save_db(db); display_main_menu(update, f"✅ تم حذف `{router_id}`.")
    return MAIN_MENU

def edit_select_router(update: Update, context: CallbackContext) -> int:
    context.user_data['edit_router_id'] = update.callback_query.data; update.callback_query.edit_message_text("أرسل *التاريخ الجديد*", parse_mode='Markdown'); return EDIT_DATE
def edit_get_date(update: Update, context: CallbackContext) -> int:
    context.user_data['edit_new_date'] = update.message.text.strip(); update.message.reply_text("أرسل *الحالة الجديدة*", parse_mode='Markdown'); return EDIT_STATUS
def edit_get_status(update: Update, context: CallbackContext) -> int:
    router_id = context.user_data['edit_router_id']; new_date = context.user_data['edit_new_date']; new_status = update.message.text.strip(); db = load_db()
    db[router_id]['renewal_date'] = new_date; db[router_id]['status'] = new_status; save_db(db)
    update.message.reply_text(f"✅ تم تعديل `{router_id}`."); context.user_data.clear(); return ConversationHandler.END

def cancel_conversation(update: Update, context: CallbackContext) -> int:
    if update.message: update.message.reply_text(TEXTS["ru_cancel"])
    context.user_data.clear(); return ConversationHandler.END

def error_handler(update: object, context: CallbackContext) -> None:
    print(f"--- ERROR ---")
    print(f"Update: {update}")
    print(f"Error: {context.error}")
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    print(f"--- END ERROR ---")

# --- 5. الوظيفة الرئيسية ---
bot = Bot(TOKEN)
dispatcher = Dispatcher(bot, None, workers=4, use_context=True)

if __name__ == '__main__':
    # إعداد الـ Handlers
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
    dispatcher.add_error_handler(error_handler) # إضافة معالج الأخطاء الشامل

    # تشغيل كل شيء
    bot_instance = bot
    keep_alive()
    bot.set_webhook(url=f'{RENDER_URL}/{TOKEN}')
    print("Bot is starting up... Armored Edition.")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
