#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для Автостоянки «Каменногорская-6»
Версия: 4.1 (полная + исправленная)
• Верификация с 2 вопросами (место + статус)
• Жалобы с фильтром мата
• Связь с соседом
• Справочник + поиск по Правилам
• Напоминания из календаря
• Подписка /subscribe
"""

import os
import logging
from datetime import datetime, timedelta
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes,
    ChatJoinRequestHandler, filters
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ==================== НАСТРОЙКИ ====================

BOT_TOKEN = os.getenv('BOT_TOKEN', '7794791486:AAEhXXzsK0UZeiEj6L1nn-XGYtBF8WKb9bo')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '-1001234567890'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '-1009876543210'))  # ← ДОЛЖЕН НАЧИНАТЬСЯ С -100
GOOGLE_CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID', 'kamenogorskaya6@gmail.com')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyAM-hDcTF_7if-h3XoXlWPtWuBceqiWD5c')

PLACE_MIN = 1
PLACE_MAX = 37
PREDSEDAT_NIK = "@vitali_k81"
BUHGAL_CONTACT = "📞 +375 29 XXX-XX-XX"  # ← ТЕКСТОВЫЙ КОНТАКТ БУХГАЛТЕРА

TIMEZONE = "Europe/Minsk"

# Фильтр мата
PROFANITY_WORDS = [
    'бля', 'блядь', 'ебать', 'ёбать', 'пизда', 'хуй', 'хер', 'сука',
    'гандон', 'говно', 'нахуй', 'пидор', 'педик', 'еблан', 'лох', 'мудак'
]

# ==================== ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ЧИСЛОВЫЕ КОНСТАНТЫ СОСТОЯНИЙ (КРИТИЧЕСКИ ВАЖНО!) ====================
# ← БЕЗ КАВЫЧЕК = ЧИСЛА, а не строки!

AWAITING_PLACE, AWAITING_STATUS, COMPLAINT_PLACE_FROM, COMPLAINT_PLACE_TO, \
COMPLAINT_TEXT, CONTACT_PLACE, SEARCH_RULES = range(7)

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================

class SheetsDB:
    """Работа с таблицей 'telegramm'"""
    
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
    
    def save_member(self, user_id, username, first_name, place, is_member, status='активен'):
        """Сохранить запись в лист «Члены»"""
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
    
    def get_member_by_place(self, place):
        """Получить данные о владельце места"""
        try:
            ws = self.sheet.worksheet('Члены')
            # Ищем в столбце "Место" (столбец D = индекс 4)
            cell = ws.find(str(place), in_column=4)
            if cell:
                row = ws.row_values(cell.row)
                return {
                    'user_id': row[0] if len(row) > 0 else None,
                    'username': row[1] if len(row) > 1 else "нет",
                    'first_name': row[2] if len(row) > 2 else "Пользователь",
                    'place': row[3] if len(row) > 3 else str(place)
                }
        except:
            pass
        return None
    
    def save_complaint(self, place_from, place_to, text):
        """Сохранить жалобу в лист «Заявки»"""
        ws = self.sheet.worksheet('Заявки')
        ws.append_row([
            datetime.now().strftime('%d.%m.%Y'),
            datetime.now().strftime('%H:%M'),
            place_from,
            place_to,
            text,
            'новая'
        ])
    
    def subscribe_user(self, user_id, username, first_name):
        """Подписать на ЛС-напоминания"""
        ws = self.sheet.worksheet('Подписки')
        try:
            cell = ws.find(str(user_id), in_column=1)
            if cell:
                ws.update_cell(cell.row, 4, 'да')
                return
        except:
            pass
        ws.append_row([
            str(user_id),
            username or "нет",
            first_name or "Пользователь",
            'да',
            datetime.now().strftime('%d.%m.%Y')
        ])
    
    def unsubscribe_user(self, user_id):
        """Отписать от ЛС-напоминаний"""
        try:
            ws = self.sheet.worksheet('Подписки')
            cell = ws.find(str(user_id), in_column=1)
            if cell:
                ws.update_cell(cell.row, 4, 'нет')
        except:
            pass
    
    def get_subscribers(self):
        """Получить список подписанных"""
        try:
            ws = self.sheet.worksheet('Подписки')
            records = ws.get_all_records()
            return [
                int(r['Telegram ID']) 
                for r in records 
                if r.get('Подписан на ЛС') == 'да'
            ]
        except:
            return []
    
    def get_rules(self):
        """Получить правила из листа «Правила»"""
        try:
            ws = self.sheet.worksheet('Правила')
            records = ws.get_all_records()
            rules = {}
            for r in records:
                keywords = r.get('Ключевые слова', '').lower().split(',')
                text = r.get('Текст', '')
                for kw in keywords:
                    kw = kw.strip()
                    if kw and text:
                        rules[kw] = text
            return rules
        except:
            return {}
    
    def search_rules(self, query):
        """Поиск правила по ключевому слову"""
        rules = self.get_rules()
        query_lower = query.lower()
        for kw, text in rules.items():
            if query_lower in kw or kw in query_lower:
                return text
        return None

# ==================== ФИЛЬТР МАТА ====================

def contains_profanity(text):
    """Проверка на мат"""
    text_lower = text.lower()
    for word in PROFANITY_WORDS:
        if word in text_lower:
            return True
    return False

# ==================== ВЕРИФИКАЦИЯ ЧЕРЕЗ ЗАЯВКИ ====================

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение заявки на вступление в канал"""
    if not update.chat_join_request:
        return ConversationHandler.END
    
    user = update.chat_join_request.from_user
    chat = update.chat_join_request.chat
    
    # Сохраняем данные заявки
    context.user_data['join_req'] = {
        'user_id': user.id,
        'username': f"@{user.username}" if user.username else "нет",
        'first_name': user.first_name or "Пользователь",
        'chat_id': chat.id
    }
    
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"👋 Добро пожаловать на стоянку «Каменногорская-6», {user.first_name}!\n\n"
                 f"Укажите номер вашего машино-места ({PLACE_MIN}–{PLACE_MAX}):"
        )
        return AWAITING_PLACE  # ← ЧИСЛО БЕЗ КАВЫЧЕК (КРИТИЧЕСКИ ВАЖНО!)
    except Exception as e:
        logger.error(f"Не удалось написать пользователю {user.id}: {e}")
        try:
            await context.bot.decline_chat_join_request(
                chat_id=chat.id,
                user_id=user.id
            )
        except:
            pass
        return ConversationHandler.END

async def process_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка номера места"""
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("❌ Только цифры. Попробуйте ещё раз:")
        return AWAITING_PLACE  # ← ЧИСЛО БЕЗ КАВЫЧЕК
    
    place = int(text)
    if place < PLACE_MIN or place > PLACE_MAX:
        await update.message.reply_text(
            f"❌ Нет такого места. Диапазон: {PLACE_MIN}–{PLACE_MAX}."
        )
        req = context.user_data.get('join_req')
        if req:
            try:
                await context.bot.decline_chat_join_request(
                    chat_id=req['chat_id'],
                    user_id=req['user_id']
                )
            except:
                pass
        return ConversationHandler.END
    
    context.user_data['place'] = place
    
    await update.message.reply_text(
        "Вы член кооператива?\n"
        "Ответьте «да» или «нет»:"
    )
    return AWAITING_STATUS  # ← ЧИСЛО БЕЗ КАВЫЧЕК

async def process_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка статуса члена + сохранение в таблицу + одобрение"""
    text = update.message.text.strip().lower()
    is_member = "да" if text in ['да', 'д', 'yes', 'y'] else "нет"
    
    req = context.user_data.get('join_req')
    place = context.user_data.get('place')
    
    if not req or not place:
        await update.message.reply_text("⚠️ Произошла ошибка. Обратитесь к председателю.")
        return ConversationHandler.END
    
    # Сохраняем в таблицу
    db = SheetsDB()
    db.save_member(
        user_id=req['user_id'],
        username=req['username'],
        first_name=req['first_name'],
        place=place,
        is_member=is_member
    )
    
    # Автоматически одобряем заявку
    try:
        await context.bot.approve_chat_join_request(
            chat_id=req['chat_id'],
            user_id=req['user_id']
        )
    except Exception as e:
        logger.error(f"Ошибка одобрения заявки: {e}")
        await update.message.reply_text("⚠️ Не удалось одобрить заявку. Обратитесь к председателю.")
        return ConversationHandler.END
    
    # Приветствие
    await update.message.reply_text(
        f"✅ Верификация пройдена, {req['first_name']}!\n"
        f"• Место: №{place}\n"
        f"• Статус: {'член кооператива' if is_member == 'да' else 'гость'}\n\n"
        f"Добро пожаловать в канал! 🎉\n"
        f"Используйте /help для просмотра функций бота."
    )
    
    return ConversationHandler.END

# ==================== ГЛАВНОЕ МЕНЮ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("📜 Справочник", callback_data='docs')],
        [InlineKeyboardButton("🚨 Сообщить о нарушении", callback_data='report')],
        [InlineKeyboardButton("👥 Сосед по месту", callback_data='contact')],
        [InlineKeyboardButton("📞 Контакты", callback_data='contacts')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👋 Я — помощник стоянки «Каменногорская-6».\n\nВыберите действие:"
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок меню"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'docs':
        await query.edit_message_text(
            "📜 <b>Справочник</b>\n\n"
            "• Нажмите «Поиск по Правилам» — найдите ответ по ключевому слову (мойка, снег, штраф)\n"
            "• Готовые ответы на частые вопросы доступны у председателя",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Поиск по Правилам", callback_data='search_rules')],
                [InlineKeyboardButton("⬅️ Назад", callback_data='back_main')]
            ])
        )
    
    elif query.data == 'search_rules':
        await query.edit_message_text(
            "🔍 Введите ключевое слово для поиска:\n"
            "(например: мойка, снег, штраф, парковка)"
        )
        return SEARCH_RULES
    
    elif query.data == 'report':
        await query.edit_message_text(
            f"🚨 <b>Сообщить о нарушении</b>\n\n"
            f"Шаг 1 из 3: Укажите номер <b>ВАШЕГО</b> места ({PLACE_MIN}–{PLACE_MAX}):",
            parse_mode='HTML'
        )
        return COMPLAINT_PLACE_FROM
    
    elif query.data == 'contact':
        await query.edit_message_text(
            "👥 Введите номер машино-места соседа:\n"
            "(Бот покажет @username, если указан)"
        )
        return CONTACT_PLACE
    
    elif query.data == 'contacts':
        await query.edit_message_text(
            f"📞 <b>Контакты Правления</b>\n\n"
            f"👤 Председатель: {PREDSEDAT_NIK}\n"
            f"💰 Бухгалтер: {BUHGAL_CONTACT}\n\n"  # ← ИСПРАВЛЕНО: текстовый контакт
            f"⏰ Приём: пн-пт 17:00–19:00 (у ворот стоянки)",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data='back_main')]
            ])
        )
    
    elif query.data == 'back_main':
        await start(update, context)
    
    return ConversationHandler.END

# ==================== ПОИСК ПО ПРАВИЛАМ ====================

async def search_rules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск по правилам из таблицы"""
    query = update.message.text.strip()
    db = SheetsDB()
    result = db.search_rules(query)
    
    if result:
        await update.message.reply_text(
            f"📜 Найдено по «{query}»:\n\n{result}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data='back_main')]
            ])
        )
    else:
        await update.message.reply_text(
            f"❌ По «{query}» ничего не найдено в Правилах.\n"
            f"Обратитесь к председателю: {PREDSEDAT_NIK}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад в меню", callback_data='back_main')]
            ])
        )
    
    return ConversationHandler.END

# ==================== ЖАЛОБЫ ====================

async def complaint_place_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = update.message.text.strip()
    if not place.isdigit() or not (PLACE_MIN <= int(place) <= PLACE_MAX):
        await update.message.reply_text(f"❌ Неверный номер. Укажите {PLACE_MIN}–{PLACE_MAX}:")
        return COMPLAINT_PLACE_FROM
    context.user_data['place_from'] = place
    await update.message.reply_text("Шаг 2 из 3: Номер места нарушителя:")
    return COMPLAINT_PLACE_TO

async def complaint_place_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = update.message.text.strip()
    if not place.isdigit() or not (PLACE_MIN <= int(place) <= PLACE_MAX):
        await update.message.reply_text(f"❌ Неверный номер. Укажите {PLACE_MIN}–{PLACE_MAX}:")
        return COMPLAINT_PLACE_TO
    context.user_data['place_to'] = place
    await update.message.reply_text("Шаг 3 из 3: Опишите ситуацию подробно:")
    return COMPLAINT_TEXT

async def complaint_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Проверка на мат
    if contains_profanity(text):
        await update.message.reply_text(
            "❌ Обнаружено неприемлемое слово.\n"
            "Пожалуйста, переформулируйте без оскорблений."
        )
        return COMPLAINT_TEXT
    
    # Сохраняем жалобу
    db = SheetsDB()
    db.save_complaint(
        context.user_data['place_from'],
        context.user_data['place_to'],
        text
    )
    
    # Уведомление в чат Правления
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"⚠️ <b>Новая жалоба!</b>\n\n"
                f"От: место {context.user_data['place_from']}\n"
                f"Нарушитель: место {context.user_data['place_to']}\n"
                f"Описание: {text}"
            ),
            parse_mode='HTML'
        )
    except:
        pass
    
    await update.message.reply_text(
        "✅ Жалоба отправлена Правлению.\n"
        "Спасибо за содействие в поддержании порядка!"
    )
    await start(update, context)
    return ConversationHandler.END

# ==================== СВЯЗЬ С СОСЕДОМ ====================

async def contact_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = update.message.text.strip()
    if not place.isdigit() or not (PLACE_MIN <= int(place) <= PLACE_MAX):
        await update.message.reply_text(f"❌ Неверный номер. Укажите {PLACE_MIN}–{PLACE_MAX}:")
        return CONTACT_PLACE
    
    db = SheetsDB()
    neighbor = db.get_member_by_place(int(place))
    
    if not neighbor:
        await update.message.reply_text(
            f"ℹ️ Владелец места №{place} не верифицирован в системе.\n"
            f"Обратитесь к председателю: {PREDSEDAT_NIK}"
        )
        await start(update, context)
        return ConversationHandler.END
    
    if neighbor['username'] and neighbor['username'] != "нет":
        await update.message.reply_text(
            f"✅ Контакт для места №{place}:\n{neighbor['username']}\n\n"
            f"⚠️ Просим соблюдать этикет общения."
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Владелец места №{place} не указал @username.\n"
            f"Обратитесь к председателю: {PREDSEDAT_NIK}"
        )
    
    await start(update, context)
    return ConversationHandler.END

# ==================== ПОДПИСКА НА НАПОМИНАНИЯ ====================

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else "нет"
    first_name = update.effective_user.first_name or "Пользователь"
    
    db = SheetsDB()
    db.subscribe_user(user_id, username, first_name)
    
    await update.message.reply_text(
        "✅ Вы подписаны на личные напоминания!\n"
        "Теперь будете получать уведомления о событиях прямо здесь."
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    db = SheetsDB()
    db.unsubscribe_user(user_id)
    
    await update.message.reply_text(
        "❌ Вы отписались от личных напоминаний.\n"
        "Уведомления будут приходить только в канал."
    )

# ==================== СПРАВКА ====================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ <b>Справка по боту</b>\n\n"
        "✅ <b>Верификация</b>\n"
        "Перейдите по ссылке-приглашению канала → подайте заявку → укажите номер места и статус.\n\n"
        "✅ <b>Справочник</b>\n"
        "/start → 📜 Справочник → 🔍 Поиск → введите слово (мойка, снег).\n\n"
        "✅ <b>Жалобы</b>\n"
        "/start → 🚨 Сообщить о нарушении → 3 шага → отправка.\n"
        "Фильтр мата автоматически блокирует оскорбления.\n\n"
        "✅ <b>Сосед</b>\n"
        "/start → 👥 Сосед по месту → введите номер → получите @username.\n\n"
        "✅ <b>Напоминания</b>\n"
        "Автоматически в канале. Для ЛС: /subscribe.\n\n"
        f"👤 Председатель: {PREDSEDAT_NIK}",
        parse_mode='HTML'
    )

# ==================== НАПОМИНАНИЯ ИЗ КАЛЕНДАРЯ ====================

def get_event_type(summary):
    """Определение типа события по тегу в названии"""
    summary_lower = summary.lower()
    if '[собрание]' in summary_lower:
        return 'собрание'
    elif '[оплата]' in summary_lower:
        return 'оплата'
    else:
        return 'другое'

async def send_reminders(application, for_date, reminder_type):
    """Отправка напоминаний"""
    try:
        start = for_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = for_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        url = f"https://www.googleapis.com/calendar/v3/calendars/{GOOGLE_CALENDAR_ID}/events"
        params = {
            'key': GOOGLE_API_KEY,
            'timeMin': start.isoformat() + 'Z',
            'timeMax': end.isoformat() + 'Z',
            'orderBy': 'startTime',
            'singleEvents': True,
            'maxResults': 20
        }
        
        response = requests.get(url, params=params, timeout=10)
        events = response.json().get('items', [])
        
        relevant_events = []
        for event in events:
            summary = event.get('summary', '')
            event_type = get_event_type(summary)
            
            if reminder_type == 'today':
                relevant_events.append(event)
            elif reminder_type == 'tomorrow_evening' and event_type != 'собрание':
                relevant_events.append(event)
            elif reminder_type == 'meeting_7d' and event_type == 'собрание':
                relevant_events.append(event)
        
        if not relevant_events:
            return
        
        message = "🔔 <b>Напоминание</b>\n\n"
        for event in relevant_events:
            summary = event.get('summary', 'Событие')
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            summary_clean = summary.replace('[собрание]', '').replace('[оплата]', '').strip()
            message += f"• {summary_clean}"
            if 'T' in start_time:
                time_str = start_time.split('T')[1][:5]
                message += f" в {time_str}"
            message += "\n"
        
        await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"Ошибка напоминаний: {e}")

# ==================== ЗАПУСК БОТА ====================

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден! Проверьте переменные окружения.")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    
    # Верификация (ИСПРАВЛЕНО: числовые состояния!)
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
    
    # Главное меню
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('subscribe', subscribe))
    application.add_handler(CommandHandler('unsubscribe', unsubscribe))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Диалоги
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^search_rules$')],
        states={SEARCH_RULES: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_rules_handler)]},
        fallbacks=[CommandHandler('start', start)]
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^report$')],
        states={
            COMPLAINT_PLACE_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_place_from)],
            COMPLAINT_PLACE_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_place_to)],
            COMPLAINT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_text)]
        },
        fallbacks=[CommandHandler('start', start)]
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^contact$')],
        states={CONTACT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_place)]},
        fallbacks=[CommandHandler('start', start)]
    ))
    
    # Планировщик напоминаний
    scheduler.add_job(
        send_reminders,
        CronTrigger(hour=10, minute=0, timezone=TIMEZONE),
        args=[application, datetime.now(), 'today'],
        id='reminders_today'
    )
    
    scheduler.start()
    logger.info("✅ Бот запущен. Полная версия с исправленной логикой диалогов.")
    application.run_polling()

if __name__ == '__main__':
    main()
