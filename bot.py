#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для Автостоянки «Каменногорская-6»
МИНИМАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ (только верификация + меню)
"""

import os
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes,
    ChatJoinRequestHandler, filters
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==================== НАСТРОЙКИ ====================

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '-1009876543210'))

PLACE_MIN = 1
PLACE_MAX = 37
PREDSEDAT_NIK = "@vitali_k81"
BUHGAL_CONTACT = "📞 +375 29 XXX-XX-XX"

# ==================== ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================

class SheetsDB:
    def __init__(self):
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            'credentials.json', scope
        )
        self.client = gspread.authorize(creds)
        self.sheet = self.client.open('telegramm')  # ← ТОЧНОЕ НАЗВАНИЕ ВАШЕЙ ТАБЛИЦЫ
    
    def save_member(self, user_id, username, first_name, place, is_member, status):
        ws = self.sheet.worksheet('Члены')
        ws.append_row([
            str(user_id),
            username or "нет",
            first_name or "Пользователь",
            str(place),
            is_member,
            datetime.now().strftime('%d.%m.%Y %H:%M'),
            status
        ])
    
    def check_conflict(self, place, is_member):
        try:
            ws = self.sheet.worksheet('Члены')
            all_records = ws.get_all_records()
            active_members = [
                r for r in all_records 
                if str(r.get('Место', '')) == str(place) 
                and r.get('Член') == 'да' 
                and r.get('Статус') == 'активен'
            ]
            if is_member == 'да' and len(active_members) >= 1:
                return 'конфликт_член'
            return 'активен'
        except:
            return 'активен'

# ==================== ВЕРИФИКАЦИЯ (ИСПРАВЛЕНО) ====================

# ЧИСЛОВЫЕ КОНСТАНТЫ (без кавычек!)
AWAITING_PLACE, AWAITING_STATUS = range(2)

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    chat = update.chat_join_request.chat
    
    context.user_data['join_req'] = {
        'user_id': user.id,
        'username': f"@{user.username}" if user.username else "нет",
        'first_name': user.first_name or "Пользователь",
        'chat_id': chat.id
    }
    
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"👋 Добро пожаловать! Укажите номер места ({PLACE_MIN}–{PLACE_MAX}):"
        )
        return AWAITING_PLACE  # ← ЧИСЛО БЕЗ КАВЫЧЕК
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return ConversationHandler.END

async def process_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("❌ Только цифры. Попробуйте ещё раз:")
        return AWAITING_PLACE  # ← ЧИСЛО БЕЗ КАВЫЧЕК
    
    place = int(text)
    if place < PLACE_MIN or place > PLACE_MAX:
        await update.message.reply_text(f"❌ Нет такого места. Диапазон: {PLACE_MIN}–{PLACE_MAX}.")
        return ConversationHandler.END
    
    context.user_data['place'] = place
    
    await update.message.reply_text(
        "Вы член кооператива?\n"
        "Ответьте «да» или «нет»:"
    )
    return AWAITING_STATUS  # ← ЧИСЛО БЕЗ КАВЫЧЕК

async def process_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    is_member = "да" if text in ['да', 'д', 'yes', 'y'] else "нет"
    
    req = context.user_data.get('join_req')
    place = context.user_data.get('place')
    
    if not req or not place:
        await update.message.reply_text("⚠️ Ошибка. Обратитесь к председателю.")
        return ConversationHandler.END
    
    db = SheetsDB()
    status = db.check_conflict(place, is_member)
    
    db.save_member(
        user_id=req['user_id'],
        username=req['username'],
        first_name=req['first_name'],
        place=place,
        is_member=is_member,
        status=status
    )
    
    try:
        await context.bot.approve_chat_join_request(
            chat_id=req['chat_id'],
            user_id=req['user_id']
        )
    except Exception as e:
        logger.error(f"Ошибка одобрения: {e}")
        await update.message.reply_text("⚠️ Не удалось одобрить заявку.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✅ Верификация пройдена!\n"
        f"• Место: №{place}\n"
        f"• Статус: {'член' if is_member == 'да' else 'гость'}\n"
        f"Добро пожаловать в канал! 🎉"
    )
    return ConversationHandler.END

# ==================== МЕНЮ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📞 Контакты", callback_data='contacts')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Я — помощник стоянки «Каменногорская-6».\n\n"
        "Для связи с Правлением используйте кнопку ниже.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'contacts':
        await query.edit_message_text(
            f"📞 <b>Контакты Правления</b>\n\n"
            f"👤 Председатель: {PREDSEDAT_NIK}\n"
            f"💰 Бухгалтер: {BUHGAL_CONTACT}",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data='back_main')]
            ])
        )
    elif query.data == 'back_main':
        await start(update, context)

# ==================== ЗАПУСК ====================

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Верификация (ИСПРАВЛЕНО: числовые состояния)
    application.add_handler(ChatJoinRequestHandler(handle_join_request))
    application.add_handler(ConversationHandler(
        entry_points=[ChatJoinRequestHandler(handle_join_request)],
        states={
            AWAITING_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_place)],
            AWAITING_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_status)]
        },
        fallbacks=[CommandHandler('start', start)],
        per_chat=False
    ))
    
    # Меню
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("✅ Бот запущен (минимальная версия)")
    application.run_polling()

if __name__ == '__main__':
    main()
