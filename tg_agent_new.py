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
    file_path = "articles.txt"
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]
def load_declarations_and_tnved():
    """Читает эталонные ТН ВЭД и Декларации из созданного csv-файла"""
    file_path = "../data/declarations_tnved.csv"
    data = {}
    if not os.path.exists(file_path):
        file_path = "data/declarations_tnved.csv"
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
    """📡 ЗАПРОС К WILDBERRIES: Скачивает реальные остатки FBS по конкретному токену."""
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
    """📡 ЗАПРОС К WB: Скачивает карточки контента для аудита."""
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
    """⏰ АВТО-ПИЛОТ (9:00 / 15:00 МСК): Анализ дефицита и защита от овербукинга для 2-х кабинетов."""
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
        stock_cab1 = wb_stocks_1.get(art_l, 0)
        stock_cab2 = wb_stocks_2.get(art_l, 0)
        total_wb_stock = stock_cab1 + stock_cab2

        if total_wb_stock > ms_stock and ms_stock > 0:
            report_lines.append(
                f"🚨 **КРИТИЧЕСКИЙ ОВЕРБУКИНГ! Арт: `{art}`**\n"
                f"• Выставлено в Кабинете №1: {stock_cab1} шт.\n"
                f"• Выставлено в Кабинете №2: {stock_cab2} шт.\n"
                f"• 🔥 **Всего на WB: {total_wb_stock} шт. | В Моем Складе РЕАЛЬНО: {ms_stock} шт.**\n"
                f"• **Действие:** Срочно уменьшите остатки на WB во избежание штрафов!\n"
            )
            alert_triggered = True

    if alert_triggered:
        await bot.send_message(MY_CHAT_ID, "\n".join(report_lines), parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer(
        f"Привет, Екатерина! 👜✨\nЯ ваш ИИ-супервайзер бренда VESCHI.\n\n"
        f"• Напишите слово **остатки** — проверка 2-х кабинетов и защита от овербукинга.\n"
        f"• Напишите слово **аудит** — глубокая проверка ТН ВЭД, Деклараций и поиск карточек с рейтингом < 10 баллов!\n"
    )
@dp.message(lambda message: message.text and message.text.lower().strip() == "остатки")
async def check_cross_stocks(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    status_msg = await message.answer("⚡ Проверяю Кабинет №1 + Кабинет №2 и сверяю лимиты с системой МойСклад...")
    
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)

    report_lines = ["📋 **СВОДКА ОВЕРБУКИНГА И ДЕФИЦИТА (2 КАБИНЕТА):**\n"]
    issues_found = 0

    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)

    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        stock_cab1 = wb_stocks_1.get(art_l, 0)
        stock_cab2 = wb_stocks_2.get(art_l, 0)
        total_wb_stock = stock_cab1 + stock_cab2

        if total_wb_stock > ms_stock and ms_stock > 0:
            report_lines.append(
                f"🚨 **ОВЕРБУКИНГ! Арт: `{art}`**\n"
                f"• В Кабинете №1: {stock_cab1} шт. | В Кабинете №2: {stock_cab2} шт.\n"
                f"• Сумма на WB: {total_wb_stock} шт. | В Моем Складе РЕАЛЬНО: {ms_stock} шт.\n"
                f"• **Рекомендация:** Немедленно снизьте остатки на FBS!\n"
            )
            issues_found += 1
        elif total_wb_stock < 5 and ms_stock > 10:
            report_lines.append(
                f"⚠️ **ДЕФИЦИТ НА WB! Арт: `{art}`**\n"
                f"• На WB суммарно: {total_wb_stock} шт. | В Моем Складе лежит: {ms_stock} шт.\n"
                f"• **Умное распределение:** Выгрузите остаток в Кабинет №1 (так как там выше продажи)!\n"
            )
            issues_found += 1

        if issues_found >= 15:
            report_lines.append("... лог приостановлен, исправьте первые 15 критических позиций.")
            break

    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Кабинеты синхронизированы!** Суммарные остатки в Кабинете №1 и Кабинете №2 не превышают запасы в Моем Складе. Рисков штрафов и дефицита нет.", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines), parse_mode="Markdown")

@dp.message(lambda message: message.text and message.text.lower().strip() == "аудит")
async def check_tnved_and_rating_audit(message: types.Message):
    status_msg = await message.answer("📋 Запущен глубокий аудит карточек контента WB (Рейтинг, ТН ВЭД, Декларации)...")
    
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
            wb_errors = card.get("errors", []) or card.get("invalidParams", [])
            
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
                    card_issue_text += f"• 🛑 **Сбой ТН ВЭД!** На WB стоит: `{tnved_wb}`, а по декларации должен быть: `{ref_tnved}`\n"
                    card_has_issue = True
                    
            if score < 10.0:
                if wb_errors:
                    reasons = ", ".join([str(err) for err in wb_errors])
                    card_issue_text += f"• 📉 **Причина снижения от WB:** {reasons}\n"
                else:
                    card_issue_text += f"• 📉 **Рейтинг снижен.** Проверьте наличие видео, длину описания или заполненность поля 'Коллекция'.\n"
                card_has_issue = True
                
            if card_has_issue:
                report_lines.append(card_issue_text)
                issues_found += 1
                
            if issues_found >= 15:
                break
        if issues_found >= 15:
            report_lines.append("... аудит приостановлен, исправьте первые критические карточки.")
            break
            
    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Аудит обоих кабинетов пройден на 10/10!** Все коды ТН ВЭД соответствуют декларациям, карточек с низким рейтингом контента не обнаружено! 🌟", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines), parse_mode="Markdown")

async def main():
    # 🌐 ИДЕАЛЬНЫЙ ОБЛАЧНЫЙ ЗАПУСК ДЛЯ СЕРВЕРА
    global bot
    bot = Bot(token=BOT_TOKEN)
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(run_scheduled_stock_check, CronTrigger(hour="9,15", minute="0"))
    scheduler.start()
    print("⏰ Планировщик отчетов (9:00 и 15:00 МСК) успешно запущен.")
    print("👜 Боевой асинхронный ИИ-агент VESCHI запущен и слушает чат...")
    
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем чистый опрос без тяжелых циклов while True
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

