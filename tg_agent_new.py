"""Telegram checks for MoySklad Moscow stock and two independent WB accounts.

Read only: this module never changes WB stock or product cards.
"""
import asyncio
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WB_TOKEN_1 = os.getenv("WB_TOKEN_1")
WB_TOKEN_2 = os.getenv("WB_TOKEN_2")
MS_TOKEN = os.getenv("MOYSKLAD_API_TOKEN")
MS_STORE_NAME = os.getenv("MOYSKLAD_STORE_NAME", "МСК")
SALES_DAYS = int(os.getenv("SALES_DAYS", "28"))
MS_API = "https://api.moysklad.ru/api/remap/1.2"
WB_CONTENT = "https://content-api.wildberries.ru"
WB_MARKET = "https://marketplace-api.wildberries.ru"
WB_STATS = "https://statistics-api.wildberries.ru"

dp = Dispatcher()
MY_CHAT_ID = None


class CheckError(RuntimeError):
    """Incomplete input or API response: never interpret as an empty inventory."""


def norm(value):
    return str(value or "").strip().casefold()


def required_tokens(*names):
    missing = [name for name in names if not globals()[name]]
    if missing:
        raise CheckError("Нет настроек: " + ", ".join(missing))
    if WB_TOKEN_1 and WB_TOKEN_2 and WB_TOKEN_1 == WB_TOKEN_2:
        raise CheckError("Два кабинета настроены на один токен WB")


def api(method, url, token, *, wb=True, **kwargs):
    headers = {"Authorization": token if wb else f"Bearer {token}"}
    try:
        res = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        res.raise_for_status()
        return res.json()
    except (requests.RequestException, ValueError) as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        raise CheckError(f"Ошибка API {url.split('/')[2]}: HTTP {code or 'сеть/формат ответа'}") from exc


def ms_rows(url, **params):
    """Read all pages; avoid silently dropping products beyond the first 1000."""
    rows, offset = [], 0
    while True:
        data = api("GET", url, MS_TOKEN, wb=False, params={**params, "limit": 1000, "offset": offset})
        page = data.get("rows")
        if not isinstance(page, list):
            raise CheckError("МойСклад вернул ответ без rows")
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += len(page)
    return rows


def load_ms_stocks_dict():
    required_tokens("MS_TOKEN")
    stores = ms_rows(f"{MS_API}/entity/store")
    matches = [s for s in stores if norm(s.get("name")) == norm(MS_STORE_NAME)]
    if len(matches) != 1:
        raise CheckError(f"Нужен один склад МойСклад с точным именем «{MS_STORE_NAME}», найдено: {len(matches)}")
    store_href = matches[0].get("meta", {}).get("href")
    if not store_href:
        raise CheckError("Нет ссылки на склад МСК в ответе МойСклад")
    rows = ms_rows(f"{MS_API}/report/stock/bystore", filter=f"store={store_href}")
    stocks = defaultdict(float)
    missing_article = 0
    for row in rows:
        entries = row.get("stockByStore")
        if not isinstance(entries, list):
            raise CheckError("Отчёт МойСклад не содержит stockByStore")
        amount = sum(float(e.get("stock") or 0) for e in entries
                     if e.get("meta", {}).get("href") == store_href)
        if amount <= 0:
            continue
        art = norm(row.get("article"))
        if not art:
            missing_article += 1
        else:
            stocks[art] += amount
    if missing_article:
        raise CheckError(f"У {missing_article} товаров на МСК есть остаток, но нет артикула для сверки")
    return dict(stocks)


def get_real_wb_cards_and_scores(token):
    if not token:
        raise CheckError("Нет токена WB для получения карточек")
    cards, cursor, seen = [], {"limit": 100}, set()
    while True:
        body = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
        data = api("POST", f"{WB_CONTENT}/content/v2/get/cards/list", token, json=body)
        if data.get("error"):
            raise CheckError("WB Content сообщил об ошибке выгрузки карточек")
        if not isinstance(data.get("cards"), list) or not isinstance(data.get("cursor"), dict):
            # Some API versions wrap the response in data.
            data = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(data.get("cards"), list) or not isinstance(data.get("cursor"), dict):
            raise CheckError("WB Content вернул неполный список карточек")
        page = data["cards"]
        cards.extend(page)
        if len(page) < 100:
            break
        marker = (data["cursor"].get("updatedAt"), data["cursor"].get("nmID"))
        if not all(marker) or marker in seen:
            raise CheckError("Не удалось получить все страницы карточек WB")
        seen.add(marker)
        cursor = {"limit": 100, "updatedAt": marker[0], "nmID": marker[1]}
    return cards


def get_wb_warehouse_ids(token):
    data = api("GET", f"{WB_MARKET}/api/v3/warehouses", token)
    if not isinstance(data, list) or not data:
        raise CheckError("WB не вернул ни одного FBS-склада")
    ids = [item.get("id") for item in data]
    if not all(ids):
        raise CheckError("У FBS-склада нет ID")
    return ids


def get_real_wb_stocks(token, skus, warehouse_id):
    result = {}
    for start in range(0, len(skus), 100):
        data = api("POST", f"{WB_MARKET}/api/v3/stocks/{warehouse_id}", token,
                   json={"skus": skus[start:start + 100]})
        if not isinstance(data.get("stocks"), list):
            raise CheckError("WB Stocks вернул ответ без stocks")
        for item in data["stocks"]:
            sku = str(item.get("sku") or "").strip()
            if sku:
                result[sku] = float(item.get("amount") or 0)
    return result


def collect_cabinet(token, include_stock=True):
    cards = get_real_wb_cards_and_scores(token)
    article_skus = defaultdict(set)
    for card in cards:
        article = norm(card.get("vendorCode"))
        if article:
            for size in card.get("sizes") or []:
                article_skus[article].update(str(s).strip() for s in (size.get("skus") or []) if s)
    totals = defaultdict(float)
    if include_stock:
        skus = sorted({sku for items in article_skus.values() for sku in items})
        warehouse_ids = get_wb_warehouse_ids(token)
        for warehouse_id in warehouse_ids:
            amounts = get_real_wb_stocks(token, skus, warehouse_id)
            for article, article_codes in article_skus.items():
                totals[article] += sum(amounts.get(sku, 0) for sku in article_codes)
    return cards, dict(totals)


def sales_speed(token, days=SALES_DAYS):
    """Average daily actual sales over a fixed calendar interval, returns excluded."""
    if days < 1 or days > 89:
        raise CheckError("SALES_DAYS должен быть от 1 до 89")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    data = api("GET", f"{WB_STATS}/api/v1/supplier/sales", token,
               params={"dateFrom": since.strftime("%Y-%m-%dT%H:%M:%S"), "flag": 0})
    if not isinstance(data, list):
        raise CheckError("WB Statistics вернул ответ не в виде списка продаж")
    if len(data) >= 80000:
        raise CheckError("Отчёт продаж WB достиг лимита строк; нужен постраничный сбор")
    totals = defaultdict(float)
    for sale in data:
        if str(sale.get("saleID") or "").startswith("S"):
            article = norm(sale.get("supplierArticle"))
            if article:
                totals[article] += 1
    return {art: count / days for art, count in totals.items()}


def stock_issues(ms, wb1, wb2, speed1, speed2):
    issues = []
    for art, ms_qty in ms.items():
        a, b = wb1.get(art, 0), wb2.get(art, 0)
        total = a + b
        speed = speed1.get(art, 0) + speed2.get(art, 0)
        target = math.ceil(7 * speed)
        if total > ms_qty:
            issues.append(f"{art}: превышение МСК — WB {total:g} (К1 {a:g}, К2 {b:g}), МСК {ms_qty:g}")
        if speed > 0 and total < target:
            add = min(max(0, target - total), max(0, ms_qty - total))
            issues.append(f"{art}: запас {total/speed:.1f} дн.; нужно {target:g}, можно добавить {add:g} из МСК")
    return issues


def new_issues(ms, cards1, cards2, wb1, wb2):
    known1 = {norm(c.get("vendorCode")) for c in cards1}
    known2 = {norm(c.get("vendorCode")) for c in cards2}
    issues = []
    for art, qty in ms.items():
        if qty <= 0:
            continue
        missing = [name for name, known, wb in (("К1", known1, wb1), ("К2", known2, wb2))
                   if art not in known or wb.get(art, 0) <= 0]
        if missing:
            issues.append(f"{art}: МСК {qty:g}, WB К1 {wb1.get(art, 0):g}, К2 {wb2.get(art, 0):g}; проверить {', '.join(missing)}")
    return issues


def card_issues(cards, cabinet, required_by_subject):
    issues = []
    for card in cards:
        art = norm(card.get("vendorCode")) or str(card.get("nmID") or "без артикула")
        missing = []
        for field, label in (("vendorCode", "артикул продавца"), ("title", "название"),
                             ("description", "описание"), ("photos", "фото"), ("sizes", "размеры/баркоды")):
            if not card.get(field):
                missing.append(label)
        if card.get("sizes") and not any(s.get("skus") for s in card["sizes"]):
            missing.append("баркод")
        dimensions = card.get("dimensions") or {}
        for key, label in (("length", "длина"), ("width", "ширина"),
                           ("height", "высота"), ("weightBrutto", "вес упаковки")):
            if float(dimensions.get(key) or 0) <= 0:
                missing.append(label)
        if not card.get("tnved"):
            missing.append("ТН ВЭД")
        subject = card.get("subjectID")
        if not subject:
            missing.append("ID предмета")
        else:
            filled = {c.get("id") for c in (card.get("characteristics") or []) if c.get("value")}
            for required in required_by_subject[subject]:
                if required["id"] not in filled:
                    missing.append(str(required.get("name") or required["id"]))
        if missing:
            issues.append(f"{cabinet} {art}: " + ", ".join(missing))
    return issues


def get_required_characteristics(token, cards):
    result = {}
    for subject in {c.get("subjectID") for c in cards if c.get("subjectID")}:
        data = api("GET", f"{WB_CONTENT}/content/v2/object/charcs/{subject}", token)
        if data.get("error") or not isinstance(data.get("data"), list):
            raise CheckError(f"Не удалось загрузить обязательные поля предмета {subject}")
        result[subject] = [c for c in data["data"] if c.get("required")]
    return result


def snapshot(with_sales=False):
    required_tokens("MS_TOKEN", "WB_TOKEN_1", "WB_TOKEN_2")
    ms = load_ms_stocks_dict()
    if not ms:
        raise CheckError("На складе МСК нет товаров с положительным остатком; проверка не выполнена")
    cards1, wb1 = collect_cabinet(WB_TOKEN_1)
    cards2, wb2 = collect_cabinet(WB_TOKEN_2)
    speeds = (sales_speed(WB_TOKEN_1), sales_speed(WB_TOKEN_2)) if with_sales else ({}, {})
    return ms, cards1, cards2, wb1, wb2, *speeds


async def send_issues(message, title, issues):
    if not issues:
        await message.answer("✅ " + title + ": отклонений не найдено")
        return
    lines = [f"⚠️ {title} — найдено {len(issues)}:"] + ["• " + item for item in issues]
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await message.answer(chunk)


async def run_check(message, kind):
    status = await message.answer("Проверяю данные МойСклад и двух кабинетов WB…")
    try:
        if kind == "аудит":
            required_tokens("WB_TOKEN_1", "WB_TOKEN_2")
            issues = []
            for name, token in (("К1", WB_TOKEN_1), ("К2", WB_TOKEN_2)):
                cards = await asyncio.to_thread(get_real_wb_cards_and_scores, token)
                if not cards:
                    raise CheckError(f"Кабинет {name} вернул 0 карточек; аудит не завершён")
                required = await asyncio.to_thread(get_required_characteristics, token, cards)
                issues.extend(card_issues(cards, name, required))
        else:
            ms, c1, c2, w1, w2, s1, s2 = await asyncio.to_thread(snapshot, kind == "остатки")
            if kind == "остатки":
                issues = stock_issues(ms, w1, w2, s1, s2)
            else:
                issues = new_issues(ms, c1, c2, w1, w2)
        await status.delete()
        await send_issues(message, kind.capitalize(), issues)
    except (CheckError, ValueError, TypeError) as exc:
        await status.edit_text(f"❌ Проверка «{kind}» не выполнена: {exc}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer("VESCHI AI: отправьте «остатки», «новинки» или «аудит».")


@dp.message(lambda message: message.text and message.text.strip().casefold() in {"остатки", "новинки", "аудит"})
async def check_command(message: types.Message):
    await run_check(message, message.text.strip().casefold())


async def run_scheduled_stock_check():
    if not MY_CHAT_ID:
        return
    try:
        ms, _, _, w1, w2, s1, s2 = await asyncio.to_thread(snapshot, True)
        issues = stock_issues(ms, w1, w2, s1, s2)
        if issues:
            await send_issues(_BotMessage(MY_CHAT_ID), "Автопроверка остатков", issues)
    except CheckError as exc:
        await _BotMessage(MY_CHAT_ID).answer(f"❌ Автопроверка не выполнена: {exc}")


class _BotMessage:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    async def answer(self, text):
        await bot.send_message(self.chat_id, text)


async def main():
    global bot
    required_tokens("BOT_TOKEN")
    bot = Bot(token=BOT_TOKEN)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(run_scheduled_stock_check, "cron", hour="9,15", minute=0)
    scheduler.start()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, handle_signals=False)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
