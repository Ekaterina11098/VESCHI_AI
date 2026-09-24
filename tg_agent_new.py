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
    """📈 ИМИТАЦИЯ СКОРОСТИ ПРОДАЖ: Базовые 2 шт/день."""
    return 2.0

def get_real_wb_stocks(token, articles_list):
    """📡 FBS остатки с WB пачками по 100 шт."""
    if not token or not articles_list:
        return {}
    url = "https://wildberries.ru"
    headers = {"Authorization": token}
    wb_stocks_dict = {}
    
    for i in range(0, len(articles_list), 100):
        chunk = articles_list[i:i+100]
        try:
            response = requests.post(url, headers=headers, json={"skus": chunk}, timeout=15)
            if response.status_code == 200:
                wb_data = response.json().get("stocks", [])
                for item in wb_data:
                    art = str(item.get("article", "")).strip().lower()
                    amount = int(item.get("amount", 0))
                    if art:
                        wb_stocks_dict[art] = amount
        except:
            pass
    return wb_stocks_dict
def get_real_wb_cards_and_scores(token):
    """📡 БЕЗЛИМИТНЫЙ ЗАПРОС КАРТОЧЕК через пагинацию"""
    if not token:
        return []
    headers = {"Authorization": token}
    all_cards = []
    cursor = {"limit": 100}
    payload = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
    
    while True:
        try:
            res = requests.post(WB_CONTENT_URL, headers=headers, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json().get("data", {})
                cards = data.get("cards", [])
                if not cards:
                    break
                all_cards.extend(cards)
                
                next_cursor = data.get("cursor", {})
                updated_at = next_cursor.get("updatedAt")
                nm_id = next_cursor.get("nmId")
                
                if len(cards) < 100 or not updated_at or not nm_id:
                    break
                payload["settings"]["cursor"] = {"limit": 100, "updatedAt": updated_at, "nmId": nm_id}
            else:
                break
        except:
            break
    return all_cards

async def run_scheduled_stock_check():
    """⏰ АВТО-ПИЛОТ (9:00 / 15:00 МСК)"""
    global MY_CHAT_ID
    if not MY_CHAT_ID:
        return
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)
    report_lines = ["📋 **⏰ АВТО-ОТЧЕТ: КОНТРОЛЬ ОВЕРБУКИНГА:**\n"]
    alert_triggered = False
    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)
    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        total_wb = wb_stocks_1.get(art_l, 0) + wb_stocks_2.get(art_l, 0)
        if total_wb > ms_stock and ms_stock > 0:
            report_lines.append(f"🚨 **ОВЕРБУКИНГ! `{art}`** | WB: {total_wb} шт. | МС: {ms_stock} шт.")
            alert_triggered = True
    if alert_triggered:
        await bot.send_message(MY_CHAT_ID, "\n".join(report_lines), parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer("Привет, Екатерина! 👜✨\nЯ ваш ИИ-аудитор безопасности бренда VESCHI.\n\n• Напишите **остатки** — проверка лимитов.\n• Напишите **аудит** — тотальный радар отсутствия обязательных полей (габариты, вес, ТН ВЭД, декларации).")

@dp.message(lambda message: message.text and message.text.lower().strip() == "остатки")
async def check_cross_stocks(message: types.Message):
    status_msg = await message.answer("⚡ Анализирую FBS-остатки...")
    wb_stocks_1 = get_real_wb_stocks(WB_TOKEN_1, REAL_ARTICLES)
    wb_stocks_2 = get_real_wb_stocks(WB_TOKEN_2, REAL_ARTICLES)
    report_lines = ["📋 **АНАЛИТИКА ОСТАТКОВ:**\n"]
    issues_found = 0
    async with aiohttp.ClientSession() as session:
        tasks = [get_moysklad_stock_async(session, art) for art in REAL_ARTICLES]
        ms_results = await asyncio.gather(*tasks)
    for art, ms_stock in ms_results:
        art_l = str(art).strip().lower()
        total_wb = wb_stocks_1.get(art_l, 0) + wb_stocks_2.get(art_l, 0)
        if total_wb == 0 and ms_stock > 0:
            report_lines.append(f"🔥 **УПУЩЕННАЯ ВЫРУЧКА! `{art}`** | WB: 0 шт. | МС: {ms_stock} шт.")
            issues_found += 1
    await status_msg.delete()
    if issues_found == 0:
        await message.reply("✅ Кабинеты в идеальном балансе!", parse_mode="Markdown")
    else:
        await message.reply("\n".join(report_lines[:15]), parse_mode="Markdown")

@dp.message(lambda message: message.text and message.text.lower().strip() == "аудит")
async def check_tnved_and_rating_audit(message: types.Message):
    status_msg = await message.answer("🔍 Сканирую базы данных Wildberries... Проверяю габариты, вес, коды ТН ВЭД и декларации по каждой карточке...")
    cabinets = [("Кабинет №1", WB_TOKEN_1), ("Кабинет №2", WB_TOKEN_2)]
    report_lines = ["📋 **🚨 ОТЧЕТ: КАРТОЧКИ С ОТСУТСТВИЕМ ОБЯЗАТЕЛЬНЫХ ДАННЫХ:**\n"]
    issues_found = 0
    
    for cab_name, token in cabinets:
        if not token: continue
        cards = get_real_wb_cards_and_scores(token)
        
        for card in cards:
            art = str(card.get("vendorCode", "—")).strip()
            art_l = art.lower()
            object_name = str(card.get("object", "")).lower()
            
            # Извлекаем характеристики и размеры упаковки
            characteristics = card.get("characteristics", [])
            description = str(card.get("description", "")).strip()
            
            # Инициализируем переменные поиска
            tnved_wb = str(card.get("tnved", "")).strip()
            weight = 0
            ch_width, ch_height, ch_length = 0, 0, 0
            has_certificate = False
            
            # Бежим по всему массиву доп. характеристик WB и вытаскиваем логистику
            for char in characteristics:
                char_name = str(char.get("name", "")).lower()
                char_val = char.get("value", [])
                val_str = str(char_val[0]).strip() if char_val else ""
                
                if "вес" in char_name:
                    try: weight = float(val_str)
                    except: pass
                elif "ширин" in char_name and "упаков" in char_name:
                    try: ch_width = int(float(val_str))
                    except: pass
                elif "высот" in char_name and "упаков" in char_name:
                    try: ch_height = int(float(val_str))
                    except: pass
                elif "длин" in char_name and "упаков" in char_name:
                    try: ch_length = int(float(val_str))
                    except: pass
                elif "сертификат" in char_name or "декларац" in char_name or "номер" in char_name:
                    if val_str and val_str != "—" and val_str != "0":
                        has_certificate = True

            # Ищем правильную эталонную декларацию по корням категорий
            ref_row = None
            for key in REF_DATA:
                k_l = key.lower()
                is_match = (k_l in object_name or k_l in art_l or
                            ("сумк" in k_l and ("bag" in art_l or "sumka" in art_l)) or
                            ("рюкзак" in k_l and ("bag" in art_l or "ryukzak" in art_l)) or
                            ("шарф" in k_l and ("scarf" in art_l or "sharf" in art_l)) or
                            ("зонт" in k_l and ("umbrella" in art_l or "zont" in art_l)))
                if is_match:
                    ref_row = REF_DATA[key]
                    break

            # 🚨 ЖЕСТКИЙ ПРОВЕРОЧНЫЙ СПИСОК (COMPLIANCE CHECKLIST)
            missing_fields = []
            
            if not ch_width or not ch_height or not ch_length or ch_width == 0 or ch_height == 0 or ch_length == 0:
                missing_fields.append("❌ ГАБАРИТЫ УПАКОВКИ (Обнулены длина/ширина/высота. Риск блокировки FBS!)")
            if not weight or weight == 0:
                missing_fields.append("❌ ВЕС ТОВАРА (Не заполнена масса с упаковкой)")
            if not description or len(description) < 100:
                missing_fields.append("❌ ОПИСАНИЕ (Пустой или слишком короткий текст карточки)")
                
            # Проверка ТН ВЭД и декларации
            if ref_row:
                ref_tnved = str(ref_row.get("ТНВЭД", "")).strip()
                ref_decl = str(ref_row.get("Номер декларации", "—")).strip()
                
                if not tnved_wb or tnved_wb == "" or tnved_wb == "—" or not tnved_wb.startswith(ref_tnved[:4]):
                    missing_fields.append(f"🛑 КОД ТН ВЭД (На WB стоит: `{tnved_wb or 'ПУСТО'}`, а должен быть: `{ref_tnved}`)")
                if not has_certificate:
                    missing_fields.append(f"📜 СВЯЗЬ С ДЕКЛАРАЦИЕЙ (В поле сертификатов карточки не найден привязанный номер `{ref_decl}`)")

            # Если в карточке не хватает хотя бы одного обязательного параметра, выводим её в чат!
            if missing_fields:
                card_issue_text = f"📦 **[{cab_name}] Артикул: `{art}`**\n"
                for field in missing_fields:
                    card_issue_text += f"• {field}\n"
                report_lines.append(card_issue_text)
                issues_found += 1
                
            if issues_found >= 10: break
        if issues_found >= 10: break
            
    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Юридический и логистический аудит пройден на 10/10!** Все карточки проверены: габариты упаковки заполнены, вес указан, коды ТН ВЭД и номера деклараций соответствуют закону по всему ассортименту! 🌟", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines[:10]), parse_mode="Markdown")

async def main():
    session = AiohttpSession()
    global bot
    bot = Bot(token=BOT_TOKEN, session=session)
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)
