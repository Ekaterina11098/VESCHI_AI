"""Telegram checks for MoySklad Moscow stock and two independent WB accounts.

Read only: this module never changes WB stock or product cards.
"""
import asyncio
import math
import os
import re
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
VERSION = "2026-09-24-r9"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Both naming schemes are supported. Prefer the names shown in the user's
# current Streamlit secrets so a stale alias cannot silently select a token.
WB_TOKEN_1 = os.getenv("WB_TOKEN_1") or os.getenv("WB_API_TOKEN")
WB_TOKEN_2 = os.getenv("WB_TOKEN_2") or os.getenv("WB_API_TOKEN_2")
WB_TOKEN_1_SOURCE = "WB_TOKEN_1" if os.getenv("WB_TOKEN_1") else "WB_API_TOKEN"
WB_TOKEN_2_SOURCE = "WB_TOKEN_2" if os.getenv("WB_TOKEN_2") else "WB_API_TOKEN_2"
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
    unresolved = []
    # The stock-by-store report identifies goods through assortment/meta. Its
    # top-level rows do not necessarily have an article field.
    assortment_index = None
    for row in rows:
        entries = row.get("stockByStore")
        if not isinstance(entries, list):
            raise CheckError("Отчёт МойСклад не содержит stockByStore")
        amount = sum(float(e.get("stock") or 0) for e in entries
                     if urlparse(e.get("meta", {}).get("href") or "").path == urlparse(store_href).path)
        if amount <= 0:
            continue
        assortment = row.get("assortment") or {}
        art = norm(row.get("article") or assortment.get("article"))
        if not art:
            href = (assortment.get("meta") or row.get("meta") or {}).get("href")
            if assortment_index is None:
                goods = ms_rows(f"{MS_API}/entity/assortment")
                assortment_index = {urlparse(g.get("meta", {}).get("href") or "").path: g for g in goods}
            product = assortment_index.get(urlparse(href).path) if href else None
            # A few entity types may be absent from the assortment listing.
            if product is None and href and href.startswith(("https://api.moysklad.ru/", "https://online.moysklad.ru/")):
                product = api("GET", href, MS_TOKEN, wb=False)
            art = norm((product or {}).get("article"))
            if not art:
                unresolved.append(str(row.get("name") or assortment.get("name") or
                                      (product or {}).get("name") or href or "без названия"))
        if art:
            stocks[art] += amount
    return dict(stocks), unresolved


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


def cabinet_diagnostics(token):
    cards = get_real_wb_cards_and_scores(token)
    skus = sorted({str(s).strip() for card in cards for size in card.get("sizes") or []
                   for s in size.get("skus") or [] if s})
    warehouses = get_wb_warehouse_ids(token)
    per_warehouse = []
    for warehouse_id in warehouses:
        amounts = get_real_wb_stocks(token, skus, warehouse_id)
        per_warehouse.append((warehouse_id, len(amounts), sum(value > 0 for value in amounts.values()),
                              sum(amounts.values())))
    return len(cards), len(skus), per_warehouse


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
        a, b = wb1.get(art, 0), wb2.get(art, 0)
        missing = []
        for label, known, stock in (("К1", known1, a), ("К2", known2, b)):
            if art in known and stock <= 0:
                missing.append(f"{label}: остаток 0")
        if missing:
            issues.append(f"{art}: МСК {qty:g}; WB " + "; ".join(missing))
    return issues


def card_issues(cards, cabinet, required_by_subject):
    issues = []
    for card in cards:
        art = norm(card.get("vendorCode")) or str(card.get("nmID") or "без артикула")
        missing = []
        for field, label in (("vendorCode", "артикул продавца"), ("title", "название"),
                             ("description", "описание"), ("photos", "фото"), ("sizes", "размеры/баркоды")):
            if field in card and not card[field]:
                missing.append(label)
        if card.get("sizes") and not any(s.get("skus") for s in card["sizes"]):
            missing.append("баркод")
        if "dimensions" in card:
            dimensions = card.get("dimensions") or {}
            for key, label in (("length", "длина"), ("width", "ширина"),
                               ("height", "высота"), ("weightBrutto", "вес упаковки")):
                if key in dimensions and float(dimensions[key] or 0) <= 0:
                    missing.append(label)
        subject = card.get("subjectID")
        if not subject:
            missing.append("ID предмета")
        else:
            characteristics = card.get("characteristics") or []
            filled = {c.get("id") for c in characteristics if c.get("value")}
            definitions = required_by_subject[subject]
            for required in (c for c in definitions if c.get("required") and not is_tnved_name(c.get("name"))):
                if required["id"] not in filled:
                    missing.append(str(required.get("name") or required["id"]))
            tnved_ids = {c.get("id") for c in definitions if is_tnved_name(c.get("name"))}
            tnved_values = [card.get("tnved"), card.get("tnvedCode")]
            tnved_chars = [c for c in characteristics
                           if c.get("id") in tnved_ids or is_tnved_name(c.get("name"))]
            tnved_values.extend(c.get("value") for c in tnved_chars)
            # An absent property in the API response is not proof that the
            # seller has not filled the field in the account interface.
            if ("tnved" in card or "tnvedCode" in card or tnved_chars) and not any(tnved_values):
                missing.append("ТН ВЭД")
        if missing:
            issues.append(f"{cabinet} {art}: " + ", ".join(missing))
    return issues


def is_tnved_name(name):
    return "тнвэд" in re.sub(r"[^а-яёa-z0-9]", "", norm(name)) or "tnved" in norm(name)


def get_required_characteristics(token, cards):
    result = {}
    for subject in {c.get("subjectID") for c in cards if c.get("subjectID")}:
        data = api("GET", f"{WB_CONTENT}/content/v2/object/charcs/{subject}", token)
        if data.get("error") or not isinstance(data.get("data"), list):
            raise CheckError(f"Не удалось загрузить обязательные поля предмета {subject}")
        result[subject] = data["data"]
    return result


def snapshot(with_sales=False):
    required_tokens("MS_TOKEN", "WB_TOKEN_1", "WB_TOKEN_2")
    ms, unresolved = load_ms_stocks_dict()
    if not ms:
        raise CheckError(f"Нет товаров МСК с артикулом для сверки; без артикула: {len(unresolved)}")
    cards1, wb1 = collect_cabinet(WB_TOKEN_1)
    cards2, wb2 = collect_cabinet(WB_TOKEN_2)
    speeds = (sales_speed(WB_TOKEN_1), sales_speed(WB_TOKEN_2)) if with_sales else ({}, {})
    return ms, cards1, cards2, wb1, wb2, *speeds, unresolved


async def send_issues(message, title, issues, max_items=30):
    if not issues:
        await message.answer(f"✅ VESCHI AI {VERSION}: {title} — отклонений не найдено")
        return
    shown = issues if max_items is None else issues[:max_items]
    lines = [f"⚠️ VESCHI AI {VERSION}: {title} — найдено {len(issues)}:"] + ["• " + item for item in shown]
    if max_items is not None and len(issues) > max_items:
        lines.append(f"Показаны первые {max_items} из {len(issues)}.")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await message.answer(chunk)


async def run_check(message, kind):
    status = await message.answer(f"VESCHI AI {VERSION}: проверяю данные МойСклад и двух кабинетов WB…")
    try:
        if kind == "аудит":
            required_tokens("WB_TOKEN_1", "WB_TOKEN_2")
            issues = []
            tnved_unverified = 0
            fields_unverified = defaultdict(int)
            for name, token in (("К1", WB_TOKEN_1), ("К2", WB_TOKEN_2)):
                cards = await asyncio.to_thread(get_real_wb_cards_and_scores, token)
                if not cards:
                    raise CheckError(f"Кабинет {name} вернул 0 карточек; аудит не завершён")
                required = await asyncio.to_thread(get_required_characteristics, token, cards)
                issues.extend(card_issues(cards, name, required))
                for card in cards:
                    for key, label in (("title", "название"), ("description", "описание"),
                                       ("photos", "фото"), ("dimensions", "габариты/вес")):
                        if key not in card:
                            fields_unverified[label] += 1
                    if isinstance(card.get("dimensions"), dict):
                        for key, label in (("length", "длина"), ("width", "ширина"),
                                           ("height", "высота"), ("weightBrutto", "вес упаковки")):
                            if key not in card["dimensions"]:
                                fields_unverified[label] += 1
                    ids = {c.get("id") for c in required.get(card.get("subjectID"), [])
                           if is_tnved_name(c.get("name"))}
                    exposed = any(c.get("id") in ids or is_tnved_name(c.get("name"))
                                  for c in card.get("characteristics") or [])
                    if "tnved" not in card and "tnvedCode" not in card and not exposed:
                        tnved_unverified += 1
        else:
            ms, c1, c2, w1, w2, s1, s2, unresolved = await asyncio.to_thread(snapshot, kind == "остатки")
            if kind == "остатки":
                issues = stock_issues(ms, w1, w2, s1, s2)
            else:
                issues = new_issues(ms, c1, c2, w1, w2)
        await status.delete()
        if kind == "аудит" and not issues and (fields_unverified or tnved_unverified):
            await message.answer(f"⚠️ VESCHI AI {VERSION}: аудит — проверенные поля без отклонений; "
                                 "некоторые поля WB не передал в ответе API.")
        elif kind != "аудит" and unresolved and not issues:
            await message.answer(f"⚠️ VESCHI AI {VERSION}: {kind.capitalize()} — "
                                 f"среди {len(ms)} сопоставленных товаров отклонений нет; "
                                 f"ещё {len(unresolved)} товаров не проверены из-за отсутствующего артикула.")
        else:
            await send_issues(message, kind.capitalize(), issues,
                              max_items=None if kind == "аудит" else 30)
        if kind != "аудит" and unresolved:
            await message.answer("⚠️ Отдельно проверьте артикулы в МойСклад: " +
                                 ", ".join(unresolved[:10]) +
                                 (f" (ещё {len(unresolved) - 10})" if len(unresolved) > 10 else ""))
        if kind == "аудит" and tnved_unverified:
            await message.answer(f"ℹ️ ТН ВЭД нельзя подтвердить по ответу API у {tnved_unverified} карточек: "
                                 "поле не передано. Это не означает, что код отсутствует в личном кабинете WB.")
        if kind == "аудит" and fields_unverified:
            summary = ", ".join(f"{label} — {count}" for label, count in fields_unverified.items())
            await message.answer("ℹ️ Не проверены поля, которые WB не передал в ответе: " + summary)
    except (CheckError, ValueError, TypeError) as exc:
        await status.edit_text(f"❌ VESCHI AI {VERSION}: проверка «{kind}» не выполнена: {exc}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    await message.answer(f"VESCHI AI {VERSION}: отправьте «остатки», «новинки» или «аудит». "
                         "Команда /version показывает версию и названия используемых секретов.")


@dp.message(Command("version"))
async def cmd_version(message: types.Message):
    await message.answer(f"VESCHI AI {VERSION}\n"
                         f"Кабинет 1: {WB_TOKEN_1_SOURCE} {'задан' if WB_TOKEN_1 else 'не задан'}\n"
                         f"Кабинет 2: {WB_TOKEN_2_SOURCE} {'задан' if WB_TOKEN_2 else 'не задан'}\n"
                         f"МойСклад: MOYSKLAD_API_TOKEN {'задан' if MS_TOKEN else 'не задан'}")


@dp.message(Command("diagnostics"))
async def cmd_diagnostics(message: types.Message):
    try:
        required_tokens("WB_TOKEN_1", "WB_TOKEN_2")
        lines = [f"VESCHI AI {VERSION}: проверка данных FBS (без значений токенов)"]
        for name, token in (("К1", WB_TOKEN_1), ("К2", WB_TOKEN_2)):
            cards, skus, warehouses = await asyncio.to_thread(cabinet_diagnostics, token)
            lines.append(f"{name}: карточек {cards}, баркодов {skus}, складов {len(warehouses)}")
            lines.extend(f"  Склад {ident}: баркодов в ответе {returned}, с остатком {positive}, "
                         f"штук {total:g}" for ident, returned, positive, total in warehouses)
        await message.answer("\n".join(lines))
    except CheckError as exc:
        await message.answer(f"❌ Диагностика не выполнена: {exc}")


@dp.message(lambda message: message.text and message.text.strip().casefold() in {"остатки", "новинки", "аудит"})
async def check_command(message: types.Message):
    await run_check(message, message.text.strip().casefold())


async def run_scheduled_stock_check():
    if not MY_CHAT_ID:
        return
    try:
        ms, _, _, w1, w2, s1, s2, unresolved = await asyncio.to_thread(snapshot, True)
        issues = stock_issues(ms, w1, w2, s1, s2)
        if issues:
            await send_issues(_BotMessage(MY_CHAT_ID), "Автопроверка остатков", issues)
        if unresolved:
            await _BotMessage(MY_CHAT_ID).answer(f"⚠️ Автопроверка не охватила {len(unresolved)} товаров МСК без артикула")
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
