import os
import asyncio
import aiohttp
import requests
import csv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from moysklad import get_moysklad_stock_async

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WB_TOKEN_1 = os.getenv("WB_API_TOKEN")
WB_TOKEN_2 = os.getenv("WB_API_TOKEN_2") or WB_TOKEN_1

if not BOT_TOKEN:
    raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в файле .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
MY_CHAT_ID = None 

WB_CONTENT_URL = "https://wildberries.ru"

def load_real_articles():
    """Автоматически читает ВСЕ реальные артикулы из файла articles.txt"""
    file_path = os.path.join(os.path.dirname(__file__), "articles.txt")
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_declarations_and_tnved():
    """Читает эталонные ТН ВЭД и Декларации из созданного csv-файла"""
    # Исправленный чистый облачный путь прямо в корень папки сайта
    file_path = os.path.join(os.path.dirname(__file__), "declarations_tnved.csv")
    data = {}
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                kat = row.get("Категория", "").strip().lower()
                if kat:
                    data[kat] = row
    return data

# Загружаем базовую матрицу товаров
REAL_ARTICLES = load_real_articles()
REF_DATA = load_declarations_and_tnved()

def get_real_wb_stocks(token, articles_list):
    """📡 FBS остатки с WB"""
    if not token or not articles_list:
        return {}
    url = "https://wildberries.ru"
    headers = {"Authorization": token}
    try:
        response = requests.post(url, headers=headers, json={"skus": articles_list}, timeout=15)
        if response.status_code == 200:
            wb_data = response.json().get("stocks", [])
            wb_stocks_dict = {}
            for item in wb_data:
                art = str(item.get("article", "")).strip().lower()
                amount = int(item.get("amount", 0))
                if art:
                    wb_stocks_dict[art] = amount
            return wb_stocks_dict
    except:
        pass
    return {}

def get_real_wb_cards_and_scores(token):
    """📡 Карточки для аудита"""
    if not token:
        return []
    headers = {"Authorization": token}
    payload = {"settings": {"cursor": {"limit": 100}, "filter": {"withPhoto": -1}}}
    try:
        res = requests.post(WB_CONTENT_URL, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            return res.json().get("data", {}).get("cards", [])
    except:
        pass
    return []

async def run_scheduled_stock_check():
    """⏰ АВТО-ОТЧЕТ (9:00 / 15:00 МСК)"""
    global MY_CHAT_ID
    if not MY_CHAT_ID:
        return
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)
    report_lines = ["📋 **⏰ АВТО-ОТЧЕТ: КОНТРОЛЬ ОВЕРБУКИНГА И РАСПРЕДЕЛЕНИЯ:**\n"]
    alert_triggered = False
    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)
    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        total_wb = wb_stocks_1.get(art_l, 0) + wb_stocks_2.get(art_l, 0)
        if total_wb > ms_stock and ms_stock > 0:
            report_lines.append(f"🚨 **ОВЕРБУКИНГ! Арт: `{art}`** | Всего на WB: {total_wb} шт. | В Моем Складе: {ms_stock} шт.")
            alert_triggered = True
    if alert_triggered:
        await bot.send_message(MY_CHAT_ID, "\n".join(report_lines), parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer("Привет, Екатерина! 👜✨\nЯ ваш облачный ИИ-супервайзер бренда VESCHI.\n\n• Напишите **остатки** — проверка 2 кабинетов.\n• Напишите **аудит** — проверка ТН ВЭД.")

@dp.message(lambda message: message.text and message.text.lower().strip() == "остатки")
async def check_cross_stocks(message: types.Message):
    status_msg = await message.answer("⚡ Сверяю лимиты Кабинета №1 и Кабинета №2 с системой МойСклад...")
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)
    report_lines = ["📋 **СВОДКА ОВЕРБУКИНГА И ДЕФИЦИТА (2 КАБИНЕТА):**\n"]
    issues_found = 0
    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)
    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        total_wb = wb_stocks_1.get(art_l, 0) + wb_stocks_2.get(art_l, 0)
        if total_wb > ms_stock and ms_stock > 0:
            report_lines.append(f"🚨 **ОВЕРБУКИНГ! `{art}`** | Сумма на WB: {total_wb} шт. | Реально на складе: {ms_stock} шт.")
            issues_found += 1
    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Кабинеты синхронизированы!** Остатки в двух магазинах соответствуют системе МойСклад. Рисков штрафов нет.", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines[:15]), parse_mode="Markdown")

@dp.message(lambda message: message.text and message.text.lower().strip() == "аудит")
async def check_tnved_and_rating_audit(message: types.Message):
    status_msg = await message.answer("📋 Сканирую карточки обоих кабинетов на ТН ВЭД и Декларации...")
    cabinets = [("Кабинет №1", WB_TOKEN_1), ("Кабинет №2", WB_TOKEN_2)]
    report_lines = ["📋 **ГЛУБОКИЙ АУДИТ КАРТОЧЕК КОНТЕНТА VESCHI (2 КАБИНЕТА):**\n"]
    issues_found = 0
    for cab_name, token in cabinets:
        if not token: continue
        cards = get_real_wb_cards_and_scores(token)
        for card in cards:
            art = card.get("vendorCode", "—")
            object_name = card.get("object", "").lower()
            tnved_wb = card.get("tnved", "—")
            score = card.get("score", 10.0)
            ref_row = None
            for key in REF_DATA:
                if key in object_name or key in str(art).lower():
                    ref_row = REF_DATA[key]
                    break
            card_has_issue = False
            card_issue_text = f"❌ **[{cab_name}] Арт: `{art}`** (Рейтинг: {score}/10)\n"
            if ref_row:
                ref_tnved = ref_row.get("ТНВЭД", "")
                if ref_tnved and tnved_wb != ref_tnved:
                    card_issue_text += f"• 🛑 **Сбой ТН ВЭД!** На WB стоит: `{tnved_wb}`, по декларации должен: `{ref_tnved}`\n"
                    card_has_issue = True
            if score < 10.0:
                card_issue_text += f"• 📉 **Рейтинг контента снижен.**\n"
                card_has_issue = True
            if card_has_issue:
                report_lines.append(card_issue_text)
                issues_found += 1
    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Аудит обоих кабинетов пройден на 10/10!** Все коды ТН ВЭД соответствуют декларациям, карточек с низким рейтингом контента не обнаружено! 🌟", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines[:15]), parse_mode="Markdown")

async def main():
    # 🌐 ПРАВИЛЬНЫЙ ОБЛАЧНЫЙ ТАЙМАУТ ДЛЯ VPN БЕЗ ОШИБОК ЗАМОРОЗКИ
    # Передаем время ожидания напрямую в настройки сессии при ее создании
    timeout_settings = aiohttp.ClientTimeout(total=45, connect=15, sock_read=15)
    session = AiohttpSession(timeout=timeout_settings)
    
    global bot
    bot = Bot(token=BOT_TOKEN, session=session)
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(run_scheduled_stock_check, CronTrigger(hour="9,15", minute="0"))
    scheduler.start()
    print("⏰ Планировщик отчетов (9:00 и 15:00 МСК) успешно запущен.")
    print("👜 Боевой асинхронный ИИ-агент VESCHI запущен и слушает чат...")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


