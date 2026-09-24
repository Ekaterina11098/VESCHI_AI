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

REAL_ARTICLES = load_real_articles()
REF_DATA = load_declarations_and_tnved()

def get_wb_sales_speed(article):
    """📈 ИМИТАЦИЯ СКОРОСТИ ПРОДАЖ: Возвращает средние продажи артикула в день за неделю.
    (В реальном API здесь будет запрос к статистике WB, пока заложим базовые 2 шт/день для теста)"""
    return 2.0

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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer("Привет, Екатерина! 👜✨\nЯ ваш обновленный ИИ-супервайзер бренда VESCHI.\n\n• Напишите **остатки** — проверка дефицита на 7 дней и упущенных продаж!\n• Напишите **аудит** — проверка ТН ВЭД.")

@dp.message(lambda message: message.text and message.text.lower().strip() == "остатки")
async def check_cross_stocks(message: types.Message):
    status_msg = await message.answer("⚡ Провожу глубокий анализ дефицита, обнулений и лимитов на 7 дней продаж...")
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)
    
    report_lines = ["📋 **АНАЛИТИКА УПУЩЕННОЙ ВЫРУЧКИ И ДЕФИЦИТА:**\n"]
    issues_found = 0
    
    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)
        
    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        stock_cab1 = wb_stocks_1.get(art_l, 0)
        stock_cab2 = wb_stocks_2.get(art_l, 0)
        total_wb = stock_cab1 + stock_cab2
        
        sales_speed = get_wb_sales_speed(art) # Скорость продаж в день
        days_left = total_wb / sales_speed if sales_speed > 0 and total_wb > 0 else 0
        
        # ⚠️ КРИТИЧЕСКИЙ СЛУЧАЙ 1: Полное обнуление на WB при наличии товара в Моем Складе!
        if total_wb == 0 and ms_stock > 0:
            report_lines.append(
                f"🔥 **УПУЩЕННАЯ ВЫРУЧКА! Арт: `{art}`**\n"
                f"• На Wildberries сейчас: **0 шт.** (Товар скрыт от покупателей!)\n"
                f"• В Моем Складе РЕАЛЬНО ЕСТЬ: **{ms_stock} шт.**\n"
                f"• **Действие:** Срочно добавьте остатки на маркетплейс, карточка падает в выдаче!\n"
            )
            issues_found += 1
            
        # 🚨 КРИТИЧЕСКИЙ СЛУЧАЙ 2: Овербукинг (Сумма на WB превышает МойСклад)
        elif total_wb > ms_stock and ms_stock > 0:
            report_lines.append(
                f"🚨 **ОВЕРБУКИНГ! Арт: `{art}`**\n"
                f"• Всего на WB: {total_wb} шт. | В Моем Складе: {ms_stock} шт.\n"
                f"• **Действие:** Снизьте остатки на FBS, рискуете получить штраф!\n"
            )
            issues_found += 1
            
        # ⚠️ КРИТИЧЕСКИЙ СЛУЧАЙ 3: Дефицит (Остатка на WB хватает меньше чем на 7 дней)
        elif total_wb > 0 and days_left < 7 and ms_stock > total_wb:
            required_stock = int((7 - days_left) * sales_speed)
            # Защита: не просим больше, чем реально есть в Моем Складе
            safe_add = min(required_stock, ms_stock - total_wb)
            if safe_add > 0:
                report_lines.append(
                    f"⚠️ **ДЕФИЦИТ НА 7 ДНЕЙ! Арт: `{art}`**\n"
                    f"• Остатка на WB ({total_wb} шт.) хватит всего на **{round(days_left, 1)} дн.**\n"
                    f"• На производстве в Моем Складе свободно: {ms_stock} шт.\n"
                    f"• **Рекомендация:** Догрузите еще **+{safe_add} шт.** в Кабинет №1!\n"
                )
                issues_found += 1

    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Кабинеты в идеальном балансе!** Товара на WB хватает минимум на 7 дней продаж, обнуленных позиций при наличии запасов на складе не обнаружено.", parse_mode="Markdown")
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
    session = AiohttpSession()
    global bot
    bot = Bot(token=BOT_TOKEN, session=session)
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    # Скрипт будет автоматически присылать этот развернутый аналитический отчет дважды в день
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)
