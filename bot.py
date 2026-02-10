#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для Автостоянки «Каменногорская-6»
Версия: 2.1 (безопасная + поддержка без @username)
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

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '-1001234567890'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '-3504696045'))
GOOGLE_CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID', 'kamenogorskaya6@gmail.com')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')

PLACE_MIN = 1
PLACE_MAX = 37
PREDSEDAT_NIK = "@vitali_k81"
BUHGAL_CONTACT = "📞 +375 44 541-67-09"  

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

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================

class SheetsDB:
    """Простая работа с Google Таблицей"""
    
    def __init__(self):
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            'credentials.json', scope
        )
        self.client = gspread.authorize(creds)
        self.sheet = self.client.open('Каменногорская-6 — Заявки и контакты')
    
    def save_member(self, user_id, username, first_name, place, is_member):
        """Сохранить верифицированного члена"""
        ws = self.sheet.worksheet('Члены')
        ws.append_row([
            str(user_id),
            username or "нет",
            first_name or "Пользователь",
            str(place),
            is_member,
            datetime.now().strftime('%d.%m.%Y %H:%M'),
            'активен'
        ])
    
    def get_member_by_place(self, place):
        """Получить данные о владельце места"""
        try:
            ws = self.sheet.worksheet('Члены')
            cell = ws.find(str(place), in_column=4)  # Столбец "Место"
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
        """Сохранить жалобу"""
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
    text_lower = text.lower()
    for word in PROFANITY_WORDS:
        if word in text_lower:
            return word
    return ""

# ==================== ВЕРИФИКАЦИЯ ЧЕРЕЗ ЗАЯВКИ ====================

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    
    context.user_data['join_req'] = {
        'user_id': user.id,
        'username': f"@{user.username}" if user.username else None,
        'first_name': user.first_name or "Пользователь",
        'chat_id': update.chat_join_request.chat.id
    }
    
    await context.bot.send_message(
        chat_id=user.id,
        text=f"👋 Добро пожаловать на стоянку «Каменногорская-6», {user.first_name}!\n\n"
             f"Укажите номер вашего машино-места ({PLACE_MIN}–{PLACE_MAX}):"
    )
    return 'AWAITING_PLACE'

async def process_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("❌ Только цифры. Попробуйте ещё раз:")
        return 'AWAITING_PLACE'
    
    place = int(text)
    if place < PLACE_MIN or place > PLACE_MAX:
        await update.message.reply_text(
            f"❌ Нет такого места. Диапазон: {PLACE_MIN}–{PLACE_MAX}.\n"
            f"Обратитесь к председателю {PREDSEDAT_NIK}"
        )
        req = context.user_data.get('join_req')
        if req:
            await context.bot.decline_chat_join_request(
                chat_id=req['chat_id'],
                user_id=req['user_id']
            )
        return ConversationHandler.END
    
    context.user_data['place'] = place
    
    await update.message.reply_text(
        "Вы член кооператива?\n"
        "Ответьте «да» или «нет»:"
    )
    return 'AWAITING_STATUS'

async def process_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    is_member = "да" if text in ['да', 'д', 'yes', 'y'] else "нет"
    
    req = context.user_data.get('join_req')
    if not req:
        return ConversationHandler.END
    
    db = SheetsDB()
    db.save_member(
        user_id=req['user_id'],
        username=req['username'],
        first_name=req['first_name'],
        place=context.user_data['place'],
        is_member=is_member
    )
    
    await context.bot.approve_chat_join_request(
        chat_id=req['chat_id'],
        user_id=req['user_id']
    )
    
    await update.message.reply_text(
        f"✅ Верификация пройдена, {req['first_name']}!\n"
        f"• Место: №{context.user_data['place']}\n"
        f"• Статус: {'член кооператива' if is_member == 'да' else 'гость'}\n\n"
        f"Добро пожаловать! 🎉\n"
        f"Команда /help — справка по функциям бота."
    )
    
    return ConversationHandler.END

# ==================== ГЛАВНОЕ МЕНЮ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        return 'SEARCH_RULES'
    
    elif query.data == 'report':
        await query.edit_message_text(
            f"🚨 <b>Сообщить о нарушении</b>\n\n"
            f"Шаг 1 из 3: Укажите номер <b>ВАШЕГО</b> места ({PLACE_MIN}–{PLACE_MAX}):",
            parse_mode='HTML'
        )
        return 'COMPLAINT_PLACE_FROM'
    
    elif query.data == 'contact':
        await query.edit_message_text(
            "👥 Введите номер машино-места соседа:\n"
            "(Бот поможет связаться, даже если нет @username)"
        )
        return 'CONTACT_PLACE'
    
    elif query.data == 'contacts':
        await query.edit_message_text(
            f"📞 <b>Контакты Правления</b>\n\n"
            f"👤 Председатель: {PREDSEDAT_NIK}\n"
            f"💰 Бухгалтер: {BUHGAL_CONTACT}\n\n"  # ← ИСПРАВЛЕНО: BUHGAL_CONTACT вместо BUHGAL_NIK
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
        return 'COMPLAINT_PLACE_FROM'
    context.user_data['place_from'] = place
    await update.message.reply_text("Шаг 2 из 3: Номер места нарушителя:")
    return 'COMPLAINT_PLACE_TO'

async def complaint_place_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = update.message.text.strip()
    if not place.isdigit() or not (PLACE_MIN <= int(place) <= PLACE_MAX):
        await update.message.reply_text(f"❌ Неверный номер. Укажите {PLACE_MIN}–{PLACE_MAX}:")
        return 'COMPLAINT_PLACE_TO'
    context.user_data['place_to'] = place
    await update.message.reply_text("Шаг 3 из 3: Опишите ситуацию подробно:")
    return 'COMPLAINT_TEXT'

async def complaint_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if contains_profanity(text):
        await update.message.reply_text(
            "❌ Обнаружено неприемлемое слово.\n"
            "Пожалуйста, переформулируйте без оскорблений."
        )
        return 'COMPLAINT_TEXT'
    
    db = SheetsDB()
    db.save_complaint(
        context.user_data['place_from'],
        context.user_data['place_to'],
        text
    )
    
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
        return 'CONTACT_PLACE'
    
    db = SheetsDB()
    neighbor = db.get_member_by_place(int(place))
    
    if not neighbor:
        await update.message.reply_text(
            f"ℹ️ Владелец места №{place} не верифицирован в системе.\n"
            f"Обратитесь к председателю: {PREDSEDAT_NIK}"
        )
        await start(update, context)
        return ConversationHandler.END
    
    context.user_data['target_user_id'] = neighbor['user_id']
    context.user_data['target_place'] = place
    
    if neighbor['username'] and neighbor['username'] != "нет":
        await update.message.reply_text(
            f"✅ Найден владелец места №{place}:\n"
            f"{neighbor['username']} ({neighbor['first_name']})\n\n"
            f"⚠️ Вы можете написать ему напрямую.\n"
            f"Или отправьте сообщение через бота (анонимно):"
        )
    else:
        await update.message.reply_text(
            f"✅ Найден владелец места №{place}:\n"
            f"{neighbor['first_name']}\n\n"
            f"ℹ️ У пользователя нет @username в Telegram.\n"
            f"Отправьте сообщение через бота (анонимно):"
        )
    
    await update.message.reply_text(
        "✏️ Введите ваше сообщение:\n"
        "(Бот перешлёт его владельцу места)"
    )
    return 'CONTACT_MESSAGE'

async def contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text.strip()
    
    if contains_profanity(message_text):
        await update.message.reply_text("❌ Неприемлемые слова. Переформулируйте без оскорблений.")
        return 'CONTACT_MESSAGE'
    
    try:
        sender_place = context.user_data.get('place', 'неизвестно')
        
        await context.bot.send_message(
            chat_id=int(context.user_data['target_user_id']),
            text=f"📬 <b>Сообщение от соседа</b>\n\n"
                 f"От владельца места №{sender_place}:\n\n"
                 f"{message_text}",
            parse_mode='HTML'
        )
        
        await update.message.reply_text(
            f"✅ Сообщение отправлено владельцу места №{context.user_data['target_place']}!"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await update.message.reply_text(
            "❌ Не удалось доставить сообщение.\n"
            "Возможно, владелец заблокировал бота."
        )
    
    await start(update, context)
    return ConversationHandler.END

# ==================== ПОДПИСКА НА НАПОМИНАНИЯ ====================

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else None
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
        "Перейдите по ссылке-приглашению канала → подайте заявку → укажите номер места.\n"
        "⚠️ Работает даже без @username!\n\n"
        "✅ <b>Справочник</b>\n"
        "/start → 📜 Справочник → 🔍 Поиск → введите слово (мойка, снег).\n\n"
        "✅ <b>Жалобы</b>\n"
        "/start → 🚨 Сообщить о нарушении → 3 шага → отправка.\n\n"
        "✅ <b>Сосед</b>\n"
        "/start → 👥 Сосед по месту → введите номер → отправьте сообщение.\n"
        "Работает даже без @username!\n\n"
        f"👤 Председатель: {PREDSEDAT_NIK}",
        parse_mode='HTML'
    )

# ==================== НАПОМИНАНИЯ ИЗ КАЛЕНДАРЯ ====================

def get_event_type(summary):
    summary_lower = summary.lower()
    if '[собрание]' in summary_lower:
        return 'собрание'
    elif '[оплата]' in summary_lower:
        return 'оплата'
    else:
        return 'другое'

async def send_reminders(application, for_date, reminder_type):
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
        
        if reminder_type in ['today', 'meeting_7d']:
            db = SheetsDB()
            subscribers = db.get_subscribers()
            for user_id in subscribers:
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode='HTML'
                    )
                except:
                    pass
    
    except Exception as e:
        logger.error(f"Ошибка напоминаний: {e}")

# ==================== ЗАПУСК БОТА ====================

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден! Проверьте переменные окружения.")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    
    # Верификация
    application.add_handler(ChatJoinRequestHandler(handle_join_request))
    application.add_handler(ConversationHandler(
        entry_points=[ChatJoinRequestHandler(handle_join_request)],
        states={
            'AWAITING_PLACE': [MessageHandler(filters.TEXT & ~filters.COMMAND, process_place)],
            'AWAITING_STATUS': [MessageHandler(filters.TEXT & ~filters.COMMAND, process_status)]
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
        states={'SEARCH_RULES': [MessageHandler(filters.TEXT & ~filters.COMMAND, search_rules_handler)]},
        fallbacks=[CommandHandler('start', start)]
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^report$')],
        states={
            'COMPLAINT_PLACE_FROM': [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_place_from)],
            'COMPLAINT_PLACE_TO': [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_place_to)],
            'COMPLAINT_TEXT': [MessageHandler(filters.TEXT & ~filters.COMMAND, complaint_text)]
        },
        fallbacks=[CommandHandler('start', start)]
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^contact$')],
        states={
            'CONTACT_PLACE': [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_place)],
            'CONTACT_MESSAGE': [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_message)]
        },
        fallbacks=[CommandHandler('start', start)]
    ))
    
    # Планировщик
    scheduler.add_job(
        send_reminders,
        CronTrigger(hour=10, minute=0, timezone=TIMEZONE),
        args=[application, datetime.now(), 'today'],
        id='reminders_today'
    )
    
    scheduler.add_job(
        send_reminders,
        CronTrigger(hour=19, minute=0, timezone=TIMEZONE),
        args=[application, datetime.now() + timedelta(days=1), 'tomorrow_evening'],
        id='reminders_tomorrow_evening'
    )
    
    scheduler.add_job(
        send_reminders,
        CronTrigger(hour=10, minute=0, timezone=TIMEZONE),
        args=[application, datetime.now() + timedelta(days=7), 'meeting_7d'],
        id='reminders_meeting_7d'
    )
    
    scheduler.start()
    logger.info("✅ Бот запущен. Поддержка пользователей без @username.")
    application.run_polling()

if __name__ == '__main__':
    main()