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

# --- دالة تهريب الحروف الخاصة (تم تحديثها) ---
def escape_markdown(text: str) -> str:
    if not text: return ""
    # تم إضافة النقطة للقائمة
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
    welcome_text = "👋 Добро пожаловать! Чтобы проверить статус вашей подписки, нажмите кнопку поиска и отправьте ID роутера."
    keyboard = [[InlineKeyboardButton("🔍 Найти роутер", callback_data='start_search')]]
    update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

def favorites(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id; db = load_db()
    fav_list = [f"- `{escape_markdown(rid)}` \\(истекает: {escape_markdown(info.get('renewal_date', 'N/A'))}\\)" for rid, info in db.items() if user_id in info.get('favorited_by', [])]
    message = "⭐ *Ваши роутеры в избранном:*\n\n" + "\n".join(fav_list) if fav_list else "Ваш список избранного пуст."
    update.message.reply_text(message, parse_mode='MarkdownV2')

def favorite_button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query; query.answer(); router_id = query.data.split('_')[1]; user_id = query.from_user.id
    db = load_db(); info = db.get(router_id, {})
    if 'favorited_by' not in info: info['favorited_by'] = []
    if user_id not in info['favorited_by']:
        info['favorited_by'].append(user_id); db[router_id] = info; save_db(db)
        query.edit_message_text(text=f"✅ Роутер *{escape_markdown(router_id)}* успешно добавлен в избранное!", parse_mode='MarkdownV2')
    else: query.edit_message_text(text=f"ℹ️ Роутер *{escape_markdown(router_id)}* уже в вашем списке избранного.", parse_mode='MarkdownV2')

# --- نظام المحادثات الموحد ---
def start_search(update: Update, context: CallbackContext) -> int:
    update.callback_query.edit_message_text("Пожалуйста, отправьте ID вашего роутера."); return AWAITING_ID
def handle_search_input(update: Update, context: CallbackContext) -> int:
    db = load_db(); router_id = update.message.text.strip().upper(); info = db.get(router_id)
    if info:
        text = f"🛰️ *Данные для роутера: {escape_markdown(router_id)}*\n\n*Статус:* {escape_markdown(info.get('status', 'N/A'))}\n*Дата продления:* {escape_markdown(info.get('renewal_date', 'N/A'))}"
        keyboard = [[InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f'fav_{router_id}')]]; update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2'); return ConversationHandler.END
    else:
        update.message.reply_text("❌ Пожалуйста, проверьте правильность ID роутера. Попробуйте еще раз:"); return AWAITING_ID
def start_support(update: Update, context: CallbackContext) -> int:
    text = "Напишите ваш запрос, и мы рассмотрим его в ближайшее время. Пожалуйста, укажите ID роутера."
    update.callback_query.edit_message_text(text); return AWAITING_SUPPORT_MESSAGE
def handle_support_message(update: Update, context: CallbackContext) -> int:
    ADMIN_ID = os.environ.get('ADMIN_CHAT_ID')
    if ADMIN_ID:
        user_info = update.message.from_user; escaped_username = escape_markdown(user_info.username if user_info.username else "нет")
        forward_text = f"✉️ Новое сообщение в техподдержку от пользователя: @{escaped_username} \\(ID: `{user_info.id}`\\)"
        try:
            context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode='MarkdownV2')
            context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            update.message.reply_text("✅ Ваше сообщение отправлено администратору. Спасибо!")
        except Exception as e:
            print(f"ERROR: Could not forward message to admin. Reason: {e}"); update.message.reply_text("Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже.")
    else: update.message.reply_text("К сожалению, система поддержки временно недоступна.")
    return ConversationHandler.END
def manage_start(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("🔐 Эта область защищена. Пожалуйста, введите пароль:"); return PASSWORD
def check_password(update: Update, context: CallbackContext) -> int:
    if update.message.text == ADMIN_PASSWORD: display_main_menu(update, "✅ Пароль верный. Выберите действие:"); return MAIN_MENU
    else: update.message.reply_text("❌ Неверный пароль."); return ConversationHandler.END
def display_main_menu(update: Update, text: str) -> None:
    keyboard = [[InlineKeyboardButton("➕ Добавить роутер", callback_data='add')], [InlineKeyboardButton("🗑️ Удалить роутер", callback_data='delete')], [InlineKeyboardButton("✏️ Изменить роутер", callback_data='edit')], [InlineKeyboardButton("📋 Показать все роутеры", callback_data='list')], [InlineKeyboardButton("❌ Выход", callback_data='exit')]]
    if update.callback_query: update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
def main_menu_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query; action = query.data; db = load_db()
    if action == 'add': query.edit_message_text("➕ *Добавить новый роутер*\n\nОтправьте *ID роутера* (пример: `KIT-55555`)", parse_mode='Markdown'); return ADD_ID
    elif action in ['delete', 'edit']:
        text, state = ("🗑️ Выберите роутер для удаления:", DELETE_MENU) if action == 'delete' else ("✏️ Выберите роутер для изменения:", EDIT_MENU)
        if not db: query.edit_message_text(f"Нет роутеров. [🔙 Назад]", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back')]])); return MAIN_MENU
        keyboard = [[InlineKeyboardButton(f"`{rid}`", callback_data=rid)] for rid in db.keys()]; keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back')]); query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'); return state
    elif action == 'list':
        text = "*Список всех роутеров:*\n\n" + "\n".join([f"- `{rid}` | Статус: {info.get('status', 'N/A')}" for rid, info in db.items()]) if db else "База данных пуста."
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back')]]), parse_mode='Markdown'); return MAIN_MENU
    elif action == 'back': display_main_menu(update, "Выберите действие:"); return MAIN_MENU
    elif action == 'exit': query.edit_message_text("Вы вышли из меню администратора."); return ConversationHandler.END
def add_get_id(update: Update, context: CallbackContext) -> int:
    context.user_data['new_router_id'] = update.message.text.strip().upper(); update.message.reply_text("Хорошо, теперь отправьте *дату продления* (пример: `2025-12-31`)", parse_mode='Markdown'); return ADD_DATE
def add_get_date(update: Update, context: CallbackContext) -> int:
    context.user_data['new_router_date'] = update.message.text.strip(); update.message.reply_text("Отлично, и, наконец, отправьте *статус роутера* (пример: `активен`)", parse_mode='Markdown'); return ADD_STATUS
def add_get_status(update: Update, context: CallbackContext) -> int:
    db = load_db(); db[context.user_data['new_router_id']] = {'status': update.message.text.strip(), 'renewal_date': context.user_data['new_router_date']}; save_db(db)
    update.message.reply_text(f"✅ Роутер `{context.user_data['new_router_id']}` успешно добавлен.", parse_mode='Markdown'); context.user_data.clear(); return ConversationHandler.END
def delete_confirm(update: Update, context: CallbackContext) -> int:
    router_id = update.callback_query.data; db = load_db()
    if router_id in db: del db[router_id]; save_db(db); display_main_menu(update, f"✅ Роутер `{router_id}` успешно удален.")
    return MAIN_MENU
def edit_select_router(update: Update, context: CallbackContext) -> int:
    context.user_data['edit_router_id'] = update.callback_query.data; update.callback_query.edit_message_text("Хорошо, теперь отправьте *новую дату продления* (пример: `2026-01-15`)", parse_mode='Markdown'); return EDIT_DATE
def edit_get_date(update: Update, context: CallbackContext) -> int:
    context.user_data['edit_new_date'] = update.message.text.strip(); update.message.reply_text("Отлично, теперь отправьте *новый статус* роутера (пример: `неактивен`)", parse_mode='Markdown'); return EDIT_STATUS
def edit_get_status(update: Update, context: CallbackContext) -> int:
    router_id = context.user_data['edit_router_id']; new_date = context.user_data['edit_new_date']; new_status = update.message.text.strip(); db = load_db()
    db[router_id]['renewal_date'] = new_date; db[router_id]['status'] = new_status; save_db(db)
    update.message.reply_text(f"✅ Роутер `{router_id}` успешно изменен.", parse_mode='Markdown'); context.user_data.clear(); return ConversationHandler.END
def cancel_conversation(update: Update, context: CallbackContext) -> int:
    if update.message: update.message.reply_text("Действие отменено.")
    context.user_data.clear(); return ConversationHandler.END

# --- إعداد البوت والـ Webhook ---
bot = Bot(TOKEN)
# مهم: Updater هنا مش بنستخدمه للـ polling، بس عشان يجهز الـ dispatcher
updater = Updater(TOKEN, use_context=True)
dispatcher = updater.dispatcher

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
    bot.set_webhook(url=f'{RENDER_URL}/{TOKEN}')
    print("Webhook is set. Starting Flask server...")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
