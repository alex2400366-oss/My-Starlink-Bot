import os
import json
import re
from flask import Flask
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

# --- دوال التعامل مع قاعدة البيانات (محصّنة) ---
def load_db():
    try:
        with open(DB_FILE, "r", encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}
def save_db(data):
    try:
        with open(DB_FILE, "w", encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[CRITICAL ERROR] Failed to save database: {e}")

# --- جزء الويب سيرفر ---
app = Flask('')
bot_instance = None
@app.route('/')
def home(): return "Bot is running!"
def run_flask(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run_flask, daemon=True).start()

@app.route('/run-checks/<secret_key>')
def run_checks_endpoint(secret_key):
    CRON_KEY = os.environ.get('CRON_SECRET_KEY')
    if not CRON_KEY or secret_key != CRON_KEY: return "Unauthorized", 401
    if bot_instance:
        Thread(target=check_subscriptions_once, args=(bot_instance,)).start()
        return "Subscription check process started."
    return "Bot instance not ready.", 503

# --- دالة فحص الاشتراكات (النسخة المدرعة) ---
def check_subscriptions_once(bot: Bot) -> None:
    print(f"CRON JOB TRIGGERED: Running subscription check...")
    db = load_db()
    today = datetime.now().date()
    for key, router_info in db.items():
        renewal_date_str = router_info.get('renewal_date')
        if not renewal_date_str: continue
        try:
            renewal_date = datetime.strptime(renewal_date_str, '%Y-%m-%d').date()
            if renewal_date - today == timedelta(days=2):
                for user_id in router_info.get('favorited_by', []):
                    message = f"🔔 Напоминание: подписка для роутера *{key}* истекает *{renewal_date_str}*. Пожалуйста, продлите ее."
                    try:
                        bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
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
    fav_list = [f"- `{rid}` (истекает: {info.get('renewal_date', 'N/A')})" for rid, info in db.items() if user_id in info.get('favorited_by', [])]
    message = "⭐ *Ваши роутеры в избранном:*\n\n" + "\n".join(fav_list) if fav_list else "Ваш список избранного пуст."
    update.message.reply_text(message, parse_mode='Markdown')

def favorite_button_handler(update: Update, context: CallbackContext) -> None:
    try:
        query = update.callback_query; query.answer()
        router_id = query.data.split('_')[1]; user_id = query.from_user.id
        db = load_db(); info = db.get(router_id, {})
        print(f"DEBUG: Handling favorite for user {user_id} and router {router_id}") # لطباعة معلومات للمساعدة
        if 'favorited_by' not in info: info['favorited_by'] = []
        if user_id not in info['favorited_by']:
            info['favorited_by'].append(user_id); db[router_id] = info; save_db(db)
            query.edit_message_text(text=f"✅ Роутер *{router_id}* успешно добавлен в избранное!", parse_mode='Markdown')
        else:
            query.edit_message_text(text=f"ℹ️ Роутер *{router_id}* уже в вашем списке избранного.", parse_mode='Markdown')
    except Exception as e:
        print(f"[ERROR] in favorite_button_handler: {e}")

# --- نظام المحادثات الموحد (ConversationHandler) ---
def start_search(update: Update, context: CallbackContext) -> int:
    try: update.callback_query.edit_message_text("Пожалуйста, отправьте ID вашего роутера."); return AWAITING_ID
    except Exception as e: print(f"Error in start_search: {e}"); return ConversationHandler.END

def handle_search_input(update: Update, context: CallbackContext) -> int:
    db = load_db(); router_id = update.message.text.strip().upper(); info = db.get(router_id)
    if info:
        text = f"🛰️ *Данные для роутера: {router_id}*\n\n*Статус:* {info.get('status', 'N/A')}\n*Дата продления:* {info.get('renewal_date', 'N/A')}"
        keyboard = [[InlineKeyboardButton("⭐ Добавить в избранное", callback_data=f'fav_{router_id}')]]
        update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
    else:
        update.message.reply_text("❌ Пожалуйста, проверьте правильность ID роутера. Попробуйте еще раз:"); return AWAITING_ID

def start_support(update: Update, context: CallbackContext) -> int:
    try:
        text = "Напишите ваш запрос, и мы рассмотрим его в ближайшее время. Пожалуйста, укажите ID роутера."
        update.callback_query.edit_message_text(text); return AWAITING_SUPPORT_MESSAGE
    except Exception as e: print(f"Error in start_support: {e}"); return ConversationHandler.END

# تم إعادة بناء هذه الدالة بالكامل
def handle_support_message(update: Update, context: CallbackContext) -> int:
    ADMIN_ID = os.environ.get('ADMIN_CHAT_ID')
    user_info = update.message.from_user
    user_message = update.message.text

    if ADMIN_ID:
        try:
            # رسالة بسيطة ونظيفة لا يمكن أن تفشل
            admin_text = (
                f"✉️ رسالة دعم جديدة\n"
                f"--------------------------\n"
                f"من: @{user_info.username} (ID: {user_info.id})\n"
                f"--------------------------\n"
                f"الرسالة:\n{user_message}"
            )
            context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
            update.message.reply_text("✅ Ваше сообщение отправлено администратору. Спасибо!")
        except Exception as e:
            print(f"ERROR: Could not send support message to admin. Reason: {e}")
            update.message.reply_text("Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже.")
    else:
        update.message.reply_text("К сожалению, система поддержки временно недоступна.")
    return ConversationHandler.END

# --- باقي دوال الإدارة ---
def manage_start(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("🔐 Эта область защищена. Пожалуйста, введите пароль:"); return PASSWORD
def check_password(update: Update, context: CallbackContext) -> int:
    if update.message.text == ADMIN_PASSWORD: display_main_menu(update, "✅ Пароль верный. Выберите действие:"); return MAIN_MENU
    else: update.message.reply_text("❌ Неверный пароль."); return ConversationHandler.END
def display_main_menu(update: Update, text: str) -> None:
    keyboard = [[InlineKeyboardButton("➕ Добавить роутер", callback_data='add')], [InlineKeyboardButton("🗑️ Удалить роутер", callback_data='delete')], [InlineKeyboardButton("✏️ Изменить роутер", callback_data='edit')], [InlineKeyboardButton("📋 Показать все роутеры", callback_data='list')], [InlineKeyboardButton("❌ Выход", callback_data='exit')]]
    try:
        if update.callback_query: update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else: update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: print(f"Error in display_main_menu: {e}")
def main_menu_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query; action = query.data; db = load_db()
    if action == 'add': query.edit_message_text("➕ *Добавить новый роутер*\n\nОтправьте *ID роутера* (пример: `KIT-55555`)", parse_mode='Markdown'); return ADD_ID
    elif action in ['delete', 'edit']:
        text, state = ("🗑️ Выберите роутер для удаления:", DELETE_MENU) if action == 'delete' else ("✏️ Выберите роутер для изменения:", EDIT_MENU)
        if not db: query.edit_message_text(f"Нет роутеров. [🔙 Назад]", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back')]])); return MAIN_MENU
        keyboard = [[InlineKeyboardButton(f"`{rid}`", callback_data=rid)] for rid in db.keys()]; keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back')])
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'); return state
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

# --- الوظيفة الرئيسية ---
def main() -> None:
    global bot_instance
    keep_alive()
    TOKEN = os.environ['TELEGRAM_TOKEN']
    updater = Updater(TOKEN, use_context=True)
    bot_instance = updater.bot
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
    
    # تأكد من تمرير `updater.bot` بشكل صحيح
    job_thread = Thread(target=check_subscriptions_once, args=(updater.bot,))
    job_thread.daemon = True
    job_thread.start()
    
    print("Bot is starting up... Armored Edition.")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
