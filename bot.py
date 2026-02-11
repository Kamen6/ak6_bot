#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для ПК «Каменногорская-6» - БЕЗОПАСНАЯ ВЕРСИЯ
Фиксы критических уязвимостей + полное соответствие Уставу
"""

import os
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes,
    ChatJoinRequestHandler, filters
)

import config
from google_integration import GoogleSheetsDB, GoogleCalendarService

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БАЗ ====================
db = GoogleSheetsDB(config.SPREADSHEET_ID, config.SHEET_NAMES)
calendar = GoogleCalendarService(config.GOOGLE_CALENDAR_ID, config.GOOGLE_API_KEY)

# ==================== БЕЗОПАСНЫЙ ФИЛЬТР МАТА ====================
def contains_profanity_safe(text: str) -> bool:
    """Безопасная проверка на мат с регулярными выражениями"""
    text_lower = text.lower()
    text_clean = re.sub(r'[^\w\s]', '', text_lower)  # Убираем спецсимволы
    
    for pattern in config.PROFANITY_PATTERNS:
        if re.search(pattern, text_clean):
            return True
            
    # Дополнительная проверка по словарю
    simple_words = ['лох', 'долбоеб', 'дебил', 'тупой']
    for word in simple_words:
        if word in text_clean:
            return True
            
    return False

# ==================== ВЕРИФИКАЦИЯ (ИСПРАВЛЕННАЯ) ====================
AWAITING_PLACE, AWAITING_STATUS = range(2)

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на вступление в канал - БЕЗОПАСНО"""
    if not update.chat_join_request:
        return ConversationHandler.END
    
    user = update.chat_join_request.from_user
    chat = update.chat_join_request.chat
    
    # Проверяем, не подавал ли уже заявку
    existing_request = db.get_pending_request(user.id)
    if existing_request:
        await context.bot.decline_chat_join_request(chat.id, user.id)
        return ConversationHandler.END
    
    # Сохраняем во временные данные
    context.user_data['join_request'] = {
        'user_id': user.id,
        'username': f"@{user.username}" if user.username else None,
        'first_name': user.first_name or "Аноним",
        'chat_id': chat.id,
        'request_time': datetime.now().isoformat()
    }
    
    # Отправляем приветствие
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"👋 Здравствуйте, {user.first_name}!\n\n"
                 f"Для доступа к каналу ПК «Каменногорская-6» "
                 f"пройдите верификацию.\n\n"
                 f"📌 *Шаг 1 из 2*\n"
                 f"Укажите номер вашего машино-места "
                 f"({config.PLACE_MIN}-{config.PLACE_MAX}):",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение: {e}")
        await context.bot.decline_chat_join_request(chat.id, user.id)
        return ConversationHandler.END
    
    return AWAITING_PLACE

async def process_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка номера места"""
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("❌ Введите только цифры:")
        return AWAITING_PLACE
    
    place = int(text)
    if not (config.PLACE_MIN <= place <= config.PLACE_MAX):
        await update.message.reply_text(
            f"❌ Нет такого места. Диапазон: {config.PLACE_MIN}-{config.PLACE_MAX}.\n"
            f"Повторите ввод:"
        )
        return AWAITING_PLACE
    
    # Проверяем конфликты
    conflict_status = db.check_membership_conflict(place)
    
    context.user_data['place'] = place
    context.user_data['conflict_info'] = conflict_status
    
    # Спрашиваем статус
    await update.message.reply_text(
        "📌 *Шаг 2 из 2*\n\n"
        "Вы являетесь членом кооператива?\n"
        "Ответьте \"да\" или \"нет\":",
        parse_mode='Markdown'
    )
    
    return AWAITING_STATUS

async def process_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка статуса и сохранение"""
    text = update.message.text.strip().lower()
    is_member = "да" if text in ['да', 'д', 'yes', 'y', '+'] else "нет"
    
    req = context.user_data.get('join_request')
    place = context.user_data.get('place')
    conflict_info = context.user_data.get('conflict_info')
    
    if not req or not place:
        await update.message.reply_text("⚠️ Ошибка сессии. Попробуйте снова.")
        return ConversationHandler.END
    
    # Определяем финальный статус
    if is_member == 'да':
        if conflict_info.get('has_active_member', False):
            final_status = 'конфликт_член'
        else:
            final_status = 'активен'
    else:
        if conflict_info.get('has_active_guest', False):
            final_status = 'конфликт_гость'
        else:
            final_status = 'активен'
    
    # Сохраняем в базу
    db.save_member(
        user_id=req['user_id'],
        username=req['username'],
        first_name=req['first_name'],
        place=place,
        is_member=is_member,
        status=final_status
    )
    
    # Одобряем заявку (всегда, как в вашем коде)
    try:
        await context.bot.approve_chat_join_request(
            chat_id=req['chat_id'],
            user_id=req['user_id']
        )
    except Exception as e:
        logger.error(f"Ошибка одобрения: {e}")
    
    # Отправляем результат
    if final_status == 'активен':
        await update.message.reply_text(
            f"✅ *Верификация успешна!*\n\n"
            f"• Место: №{place}\n"
            f"• Статус: {'Член кооператива' if is_member == 'да' else 'Гость'}\n\n"
            f"Добро пожаловать в канал!\n"
            f"Используйте /help для просмотра функций.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"⚠️ *Внимание: обнаружен конфликт*\n\n"
            f"• Место: №{place}\n"
            f"• Статус: {'Член кооператива' if is_member == 'да' else 'Гость'}\n"
            f"• Конфликт: {final_status}\n\n"
            f"✅ Доступ к каналу предоставлен.\n"
            f"❌ Данные переданы правлению для проверки.\n\n"
            f"Используйте /help для просмотра функций.",
            parse_mode='Markdown'
        )
        
        # Уведомляем правление о конфликте
        try:
            await context.bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=f"⚠️ *КОНФЛИКТ ПРИ ВЕРИФИКАЦИИ*\n\n"
                     f"Пользователь: {req['first_name']}\n"
                     f"Место: {place}\n"
                     f"Статус: {'Член' if is_member == 'да' else 'Гость'}\n"
                     f"Тип конфликта: {final_status}\n"
                     f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode='Markdown'
            )
        except:
            pass
    
    # Очищаем данные
    context.user_data.clear()
    return ConversationHandler.END

# ==================== ГЛАВНОЕ МЕНЮ (БЕЗОПАСНОЕ) ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Безопасное главное меню"""
    keyboard = [
        [InlineKeyboardButton("📜 Документы и правила", callback_data='docs')],
        [InlineKeyboardButton("🚨 Сообщить о нарушении", callback_data='report')],
        [InlineKeyboardButton("👥 Связь через правление", callback_data='contact_admin')],
        [InlineKeyboardButton("📅 Ближайшие события", callback_data='calendar')],
        [InlineKeyboardButton("📞 Контакты", callback_data='contacts')],
        [InlineKeyboardButton("ℹ️ Справка", callback_data='help_callback')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "👋 *Добро пожаловать!*\n\n"
        "Я — официальный бот ПК «Каменногорская-6».\n"
        "Выберите нужный раздел:"
    )
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    return ConversationHandler.END

# ==================== ДОКУМЕНТЫ И ПРАВИЛА (с вашей ссылкой) ====================
async def show_documents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ документов со ссылкой"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "📚 *Документы и правила ПК «Каменногорская-6»*\n\n"
        "Полный перечень документов доступен по ссылке:\n"
        f"[📄 Сайт с документами кооператива]({config.DOCUMENTS_LINK})\n\n"
        "📌 *Основные разделы:*\n"
        "• Устав кооператива\n"
        "• Правила внутреннего распорядка\n"
        "• Протоколы общих собраний\n"
        "• Финансовая отчетность\n\n"
        "🔍 *Быстрый поиск по правилам:*"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск по ключевому слову", callback_data='search_rules')],
        [InlineKeyboardButton("❓ Частые вопросы", callback_data='faq')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_main')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# ==================== БЕЗОПАСНАЯ СВЯЗЬ С СОСЕДОМ ====================
async def contact_via_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Связь с соседом только через правление - БЕЗОПАСНО"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "👥 *Связь с соседом через правление*\n\n"
        "Введите номер места соседа:",
        parse_mode='Markdown'
    )
    return 'GET_NEIGHBOR_PLACE'

async def get_neighbor_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение номера места"""
    text = update.message.text.strip()
    
    if not text.isdigit() or not (config.PLACE_MIN <= int(text) <= config.PLACE_MAX):
        await update.message.reply_text(
            f"❌ Неверный номер. Введите {config.PLACE_MIN}-{config.PLACE_MAX}:"
        )
        return 'GET_NEIGHBOR_PLACE'
    
    context.user_data['neighbor_place'] = text
    
    await update.message.reply_text(
        "✏️ *Введите ваше сообщение для соседа:*\n\n"
        "Сообщение будет передано через правление.",
        parse_mode='Markdown'
    )
    return 'GET_NEIGHBOR_MESSAGE'

async def get_neighbor_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение и отправка сообщения через правление - БЕЗОПАСНО"""
    message = update.message.text.strip()
    
    # Проверка на мат
    if contains_profanity_safe(message):
        await update.message.reply_text(
            "❌ Сообщение содержит неприемлемые слова.\n"
            "Переформулируйте и отправьте снова:"
        )
        return 'GET_NEIGHBOR_MESSAGE'
    
    neighbor_place = context.user_data.get('neighbor_place', 'неизвестно')
    user = update.effective_user
    
    # Получаем информацию о пользователе
    user_info = db.get_user_info(user.id)
    user_place = user_info.get('place', 'не указано') if user_info else 'не указано'
    
    # Сохраняем обращение в таблицу
    request_id = db.save_neighbor_request(
        from_user_id=user.id,
        from_place=user_place,
        to_place=neighbor_place,
        message=message,
        status='новое'
    )
    
    # Отправляем в чат правления
    try:
        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=(
                f"📬 *НОВОЕ ОБРАЩЕНИЕ* #{request_id}\n\n"
                f"👤 От: {user.first_name}\n"
                f"📍 Место отправителя: {user_place}\n"
                f"📍 Место получателя: {neighbor_place}\n\n"
                f"💬 Сообщение:\n{message}\n\n"
                f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки в чат правления: {e}")
    
    await update.message.reply_text(
        "✅ *Сообщение отправлено в правление!*\n\n"
        f"Номер обращения: #{request_id}\n"
        "Правление свяжется с соседом в рабочее время.\n\n"
        "Спасибо за понимание!",
        parse_mode='Markdown'
    )
    
    await start(update, context)
    return ConversationHandler.END

# ==================== НАПОМИНАНИЯ ИЗ КАЛЕНДАРЯ (в канал) ====================
async def send_calendar_reminders(application):
    """Отправка напоминаний только в канал"""
    try:
        today = datetime.now()
        
        # Получаем события на сегодня
        events = calendar.get_events_for_date(today)
        
        if not events:
            return
        
        # Формируем сообщение для канала
        message = "🔔 *Напоминание о событиях*\n\n"
        
        for event in events:
            summary = event.get('summary', 'Событие')
            start_time = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
            
            # Форматируем время
            if 'T' in start_time:
                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                time_str = dt.strftime('%d.%m.%Y в %H:%M')
            else:
                dt = datetime.fromisoformat(start_time)
                time_str = dt.strftime('%d.%m.%Y')
            
            message += f"• *{summary}*\n  📅 {time_str}\n\n"
        
        # Отправляем ТОЛЬКО в канал
        await application.bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=message,
            parse_mode='Markdown'
        )
        
        logger.info(f"Отправлено напоминание в канал о {len(events)} событиях")
        
    except Exception as e:
        logger.error(f"Ошибка отправки напоминаний: {e}")

# ==================== НАСТРОЙКА ПЛАНИРОВЩИКА ====================
def setup_scheduler(application):
    """Настройка ежедневных напоминаний в 10:00"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    
    # Напоминания каждый день в 10:00
    scheduler.add_job(
        send_calendar_reminders,
        CronTrigger(hour=10, minute=0, timezone=config.TIMEZONE),
        args=[application],
        id='daily_reminders'
    )
    
    scheduler.start()
    logger.info("Планировщик напоминаний запущен")

# ==================== ЗАПУСК БОТА ====================
def main():
    """Основная функция запуска"""
    application = Application.builder().token(config.BOT_TOKEN).build()
    
    # 1. Верификация через заявки
    verification_handler = ConversationHandler(
        entry_points=[ChatJoinRequestHandler(handle_join_request)],
        states={
            AWAITING_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_place)],
            AWAITING_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_status)],
        },
        fallbacks=[CommandHandler('start', start)],
        per_chat=False,
        per_user=True
    )
    application.add_handler(verification_handler)
    
    # 2. Главное меню
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', start))
    
    # 3. Обработчики кнопок
    application.add_handler(CallbackQueryHandler(show_documents, pattern='^docs$'))
    application.add_handler(CallbackQueryHandler(start, pattern='^back_main$'))
    
    # 4. Связь с соседом через правление
    contact_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(contact_via_admin, pattern='^contact_admin$')],
        states={
            'GET_NEIGHBOR_PLACE': [MessageHandler(filters.TEXT & ~filters.COMMAND, get_neighbor_place)],
            'GET_NEIGHBOR_MESSAGE': [MessageHandler(filters.TEXT & ~filters.COMMAND, get_neighbor_message)],
        },
        fallbacks=[CommandHandler('start', start)]
    )
    application.add_handler(contact_handler)
    
    # 5. Запуск планировщика напоминаний
    setup_scheduler(application)
    
    # Запуск бота
    logger.info("🚀 Бот запущен в безопасном режиме")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
