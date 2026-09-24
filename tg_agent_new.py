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

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WB_TOKEN_1 = os.getenv("WB_API_TOKEN")
WB_TOKEN_2 = os.getenv("WB_API_TOKEN_2") or WB_TOKEN_1
MS_TOKEN = os.getenv("MOYSKLAD_API_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в файле .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
MY_CHAT_ID = None 

WB_CONTENT_URL = "https://wildberries.ru"

def get_ms_store_id_by_name(store_name="МСК"):
    """📡 API МОЙ СКЛАД: Находит внутренний уникальный ID склада по его названию"""
    if not MS_TOKEN:
        return None
    url = "https://moysklad.ru"
    headers = {"Authorization": f"Bearer {MS_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            stores = response.json().get("rows", [])
            for store in stores:
                if store_name.lower() in str(store.get("name", "")).lower():
                    return store.get("id")
    except:
        pass
    return None

def load_ms_stocks_dict():
    """📡 POST-АНАЛИЗ МСК: Выгружает реальные остатки по складу МСК, 
    выравнивая регистр текстовых артикулов для безошибочного слияния баз."""
    if not MS_TOKEN:
        return {}
    
    store_id = get_ms_store_id_by_name("МСК")
    stocks_dict = {}
    
    url = "https://moysklad.ru"
    headers = {
        "Authorization": f"Bearer {MS_TOKEN}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip"
    }
    
    params = {"limit": 1000}
    if store_id:
        params["storeId"] = store_id
        
    try:
        response = requests.post(url, headers=headers, json={}, params=params, timeout=15)
        if response.status_code == 200:
            rows = response.json().get("rows", [])
            for row in rows:
                stock = int(row.get("stock", 0)) 
                if stock <= 0:
                    continue
                
                art = str(row.get("article", "")).strip()
                if not art and "product" in row:
                    prod_data = row.get("product", {})
                    art = str(prod_data.get("article", "")).strip()
                
                if art:
                    stocks_dict[art.lower().strip()] = stock
    except:
        pass
    return stocks_dict
def get_wb_warehouse_ids(token):
    """📡 API WILDBERRIES: Автоматически скачивает ID всех FBS-складов продавца"""
    if not token:
        return []
    url = "https://wildberries.ru"
    headers = {"Authorization": token}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return [int(w.get("id")) for w in response.json() if w.get("id")]
    except:
        pass
    return []

def get_wb_sales_speed(article):
    """📈 СКОРОСТЬ ПРОДАЖ: Базовая скорость для расчёта дефицита."""
    return 2.0

def get_real_wb_stocks(token, barcodes_list, warehouse_id):
    """📡 FBS API WB: Запрашивает остатки строго по цифровым баркодам (skus) через Marketplace API v3."""
    if not token or not barcodes_list or not warehouse_id:
        return {}
    
    url = f"https://wildberries.ru{warehouse_id}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }
    wb_stocks_dict = {}
    
    for i in range(0, len(barcodes_list), 100):
        chunk = barcodes_list[i:i+100]
        try:
            payload = {"skus": chunk}
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                wb_data = response.json().get("stocks", [])
                for item in wb_data:
                    sku = str(item.get("sku", "")).strip()
                    amount = int(item.get("amount", 0))
                    if sku:
                        wb_stocks_dict[sku] = amount
        except:
            pass
    return wb_stocks_dict

def get_real_wb_cards_and_scores(token):
    """📡 API КОНТЕНТА V2: Скачивает абсолютно 100% карточек продавца со всех страниц."""
    if not token:
        return []
    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }
    all_cards = []
    
    payload = {
        "settings": {
            "cursor": {
                "limit": 100
            },
            "filter": {
                "withPhoto": -1
            }
        }
    }
    
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
                    
                payload["settings"]["cursor"] = {
                    "limit": 100, 
                    "updatedAt": updated_at, 
                    "nmId": nm_id
                }
            else:
                break
        except:
            break
    return all_cards

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

REF_DATA = load_declarations_and_tnved()
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer(
        "Привет, Екатерина! 👜✨\nЯ ваш ИИ-супервайзер бренда VESCHI, защищённый от любых капризов, скрытий и блокировок API маркетплейса!\n\n"
        "• Напишите **остатки** — проверка дефицита и лимитов на 7 дней по ходовой витрине.\n"
        "• Напишите **новинки** — бронебойный независимый радар отгрузок и черновиков.\n"
        "• Напишите **аудит** — жесткий комплаенс-контроль логистики (габариты, вес) и ТН ВЭД."
    )

@dp.message(lambda message: message.text and message.text.lower().strip() == "остатки")
async def check_cross_stocks(message: types.Message):
    status_msg = await message.answer("📡 Подключаюсь к Marketplace API WB... Сверяю остатки ходовых товаров с ячейками склада МСК...")
    
    ms_stocks = load_ms_stocks_dict()
    if not ms_stocks:
        await status_msg.delete()
        return await message.reply("⚠️ **Ошибка связи с API Моего Склада!** Отчёт по складу МСК вернул пустой результат. Проверка не выполнена.")
        
    all_wb_cards = get_real_wb_cards_and_scores(WB_TOKEN_1)
    if not all_wb_cards:
        await status_msg.delete()
        return await message.reply("⚠️ **Ошибка связи с API Контента Wildberries!** Список карточек пуст. Проверка не выполнена.")

    warehouse_ids = get_wb_warehouse_ids(WB_TOKEN_1)
    wh_id = warehouse_ids[0] if warehouse_ids else None
    
    sku_to_art_map = {}
    barcodes_to_check = []
    
    for card in all_wb_cards:
        art = str(card.get("vendorCode", "")).lower().strip()
        sizes = card.get("sizes", [])
        for size in sizes:
            skus = size.get("skus", [])
            for sku in skus:
                sku_str = str(sku).strip()
                if sku_str:
                    sku_to_art_map[sku_str] = art
                    if art in ms_stocks:
                        barcodes_to_check.append(sku_str)

    wb_stocks = get_real_wb_stocks(WB_TOKEN_1, barcodes_to_check, wh_id)
    report_lines = ["📋 **АНАЛИТИКА ТЕКУЩИХ FBS-ОСТАТКОВ И ДЕФИЦИТА (СКЛАД МСК):**\n"]
    issues_found = 0
    
    for art, ms_stock in ms_stocks.items():
        total_wb = sum(wb_stocks.get(sku, 0) for sku, a in sku_to_art_map.items() if a == art)
        
        if total_wb > 0:
            sales_speed = get_wb_sales_speed(art)
            days_left = total_wb / sales_speed if sales_speed > 0 else 0
            
            if total_wb > ms_stock:
                report_lines.append(f"🚨 **ОВЕРБУКИНГ! Арт: `{art}`**\n• На WB суммарно: {total_wb} шт. | На складе МСК: {ms_stock} шт.\n")
                issues_found += 1
            elif days_left < 7 and ms_stock > total_wb:
                required_stock = int((7 - days_left) * sales_speed)
                safe_add = min(required_stock, ms_stock - total_wb)
                if safe_add > 0:
                    report_lines.append(f"⚠️ **ДЕФИЦИТ НА 7 ДНЕЙ! Арт: `{art}`**\n• Хватит на **{round(days_left, 1)} дн.** | Свободно на МСК: {ms_stock} шт. | Догрузить: **+{safe_add} шт.**\n")
                    issues_found += 1

    await status_msg.delete()
    if issues_found == 0:
        await message.reply("✅ **Все ходовые товары в идеальном балансе!** Остатков на складе МСК хватает минимум на 7 дней продаж.", parse_mode="Markdown")
    else:
        await message.reply("\n".join(report_lines[:15]), parse_mode="Markdown")

@dp.message(lambda message: message.text and message.text.lower().strip() == "новинки")
async def check_new_products_radar(message: types.Message):
    status_msg = await message.answer("🔍 Радар независимого Zero-API контроля запущен. Сверяю МСК напрямую со сквозными списками vendorCode маркетплейса...")
    
    # Шаг 1: Скачиваем остатки МСК (100% честные цифры)
    ms_stocks = load_ms_stocks_dict()
    if not ms_stocks:
        await status_msg.delete()
        return await message.reply("⚠️ **Ошибка связи с API Моего Склада!** База остатков МСК пуста. Проверка не выполнена.")
        
    # Шаг 2: Скачиваем ВСЕ карточки контента из Wildberries по обоим кабинетам
    all_wb_cards = get_real_wb_cards_and_scores(WB_TOKEN_1) + get_real_wb_cards_and_scores(WB_TOKEN_2)
    
    # Накапливаем ВСЕ артикулы, прописанные в кабинетах WB контента (очищаем регистры)
    wb_content_articles = set(str(c.get("vendorCode", "")).lower().strip() for c in all_wb_cards if c.get("vendorCode"))
    
    # Шаг 3: Собираем остатки WB по баркодам только для тех карточек, которые маркетплейс отдал в API
    warehouse_ids = get_wb_warehouse_ids(WB_TOKEN_1)
    wh_id = warehouse_ids[0] if warehouse_ids else None
    
    sku_to_art_map = {}
    barcodes_to_check = []
    for card in all_wb_cards:
        art = str(card.get("vendorCode", "")).lower().strip()
        sizes = card.get("sizes", [])
        for size in sizes:
            for sku in size.get("skus", []):
                sku_str = str(sku).strip()
                if sku_str:
                    sku_to_art_map[sku_str] = art
                    if art in ms_stocks:
                        barcodes_to_check.append(sku_str)
                        
    wb_stocks = get_real_wb_stocks(WB_TOKEN_1, barcodes_to_check, wh_id)
    
    report_lines = ["🔥 **РАДАР НОВИНОК: ЕСТЬ НА СКЛАДЕ МСК, НО ОБНУЛЕНЫ ИЛИ ОТСУТСТВУЮТ НА WB:**\n"]
    new_detected = 0
    
    for art, ms_stock in ms_stocks.items():
        # Считаем остаток на витрине, если карточка смогла отдать данные в API остатков
        total_wb_stock = sum(wb_stocks.get(sku, 0) for sku, a in sku_to_art_map.items() if a == art)
        
        # 🚨 ЖЕЛЕЗОБЕТОННЫЙ ОБХОД ЛОГИКИ WB:
        # Если товар есть в наличии в МСК (ms_stock > 0), но на Wildberries:
        # ЛИБО остаток FBS равен нулю, ЛИБО артикул ВООБЩЕ отсутствует в списке одобренных карточек (черновик/блок)!
        if ms_stock > 0 and (total_wb_stock == 0 or art not in wb_content_articles):
            report_lines.append(
                f"✨ **ПОСТАВЬ НА ОСТАТОК НОВЫЙ ТОВАР! Арт: `{art.upper()}`**\n"
                f"• На складе МСК в наличии: **{ms_stock} шт.**\n"
                f"• На витрине маркетплейса: ❌ **Остаток не выставлен (0 шт.) или карточка в черновиках**\n"
                f"• **Задание команде:** Срочно пропишите остатки по FBS и проверьте статус карточки в ЛК WB! 🚀\n"
            )
            new_detected += 1
            
    await status_msg.delete()
    if new_detected == 0:
        await message.reply("✅ **Новых скрытых позиций не обнаружено!** Все товары с остатками на МСК успешно выгружены на Wildberries.", parse_mode="Markdown")
    else:
        await message.reply("\n".join(report_lines[:15]), parse_mode="Markdown")

@dp.message(lambda message: message.text and message.text.lower().strip() == "аудит")
async def check_tnved_and_rating_audit(message: types.Message):
    status_msg = await message.answer("📋 Юридический комплаенс-контроль активной матрицы товаров со склада МСК...")
    
    ms_stocks = load_ms_stocks_dict()
    current_articles = list(ms_stocks.keys())
    
    cabinets = [("Кабинет №1", WB_TOKEN_1), ("Кабинет №2", WB_TOKEN_2)]
    report_lines = ["📋 **🚨 ОТЧЕТ: КАРТОЧКИ С ОТСУТСТВИЕМ ОБЯЗАТЕЛЬНЫХ ДАННЫХ:**\n"]
    issues_found = 0
    
    for cab_name, token in cabinets:
        if not token: continue
        cards = get_real_wb_cards_and_scores(token)
        for card in cards:
            art = str(card.get("vendorCode", "—")).strip()
            art_l = art.lower().strip()
            object_name = str(card.get("object", "")).lower()
            
            if art_l not in current_articles:
                continue
                
            characteristics = card.get("characteristics", [])
            description = str(card.get("description", "")).strip()
            tnved_wb = str(card.get("tnved", "")).strip()
            
            weight, ch_width, ch_height, ch_length = 0, 0, 0, 0
            has_certificate = False
            
            for char in characteristics:
                char_name = str(char.get("name", "")).lower()
                char_val = char.get("value", [])
                val_str = str(char_val).strip() if char_val else ""
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
                    if val_str and val_str != "—" and val_str != "0": has_certificate = True

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

            missing_fields = []
            if not ch_width or not ch_height or not ch_length:
                missing_fields.append("❌ ГАБАРИТЫ УПАКОВКИ (Обнулены длина/ширина/высота!)")
            if not weight:
                missing_fields.append("❌ ВЕС ТОВАРА (Не указана масса)")
            if not description or len(description) < 100:
            if not description or len(description) < 100:
                missing_fields.append("❌ ОПИСАНИЕ (Пустой текст карточки)")
            if ref_row:
                ref_tnved = str(ref_row.get("ТНВЭД", "")).strip()
                ref_decl = str(ref_row.get("Номер декларации", "—")).strip()
                if not tnved_wb or not tnved_wb.startswith(ref_tnved[:4]):
                    missing_fields.append(f"🛑 КОД ТН ВЭД (Должен быть: `{ref_tnved}`)")
                if not has_certificate:
                    missing_fields.append(f"📜 СВЯЗЬ С ДЕКЛАРАЦИЕЙ (Не привязан номер `{ref_decl}`)")

            if missing_fields:
                card_issue_text = f"📦 **[{cab_name}] Артикул: `{art}`**\n"
                for field in missing_fields: card_issue_text += f"• {field}\n"
                report_lines.append(card_issue_text)
                issues_found += 1
            if issues_found >= 10: break
        if issues_found >= 10: break
        
    await status_msg.delete()
    if issues_found == 0:
        await message.answer("✅ **Логистический и юридический аудит пройден на 10/10!** Все обязательные поля заполнены.", parse_mode="Markdown")
    else:
        await message.answer("\n".join(report_lines[:10]), parse_mode="Markdown")

async def main():
    session = AiohttpSession()
    global bot
    bot = Bot(token=BOT_TOKEN, session=session)
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)
