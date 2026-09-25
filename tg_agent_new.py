"""Telegram checks for MoySklad Moscow stock and two independent WB accounts.

Read only: this module never changes WB stock or product cards.
"""
import asyncio
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
VERSION = "2026-09-25-r20"
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
FEEDBACK_STATE_PATH = Path(__file__).with_name("telegram_feedback_state.json")
FEEDBACK_STATE = {"seen": [], "chat_id": None, "initialized": False}


def load_feedback_state():
    try:
        data = json.loads(FEEDBACK_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), list):
            FEEDBACK_STATE.update({"seen": data["seen"], "chat_id": data.get("chat_id"),
                                   "initialized": bool(data.get("initialized"))})
    except (OSError, ValueError, TypeError):
        pass


def save_feedback_state():
    try:
        temp = FEEDBACK_STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(FEEDBACK_STATE, ensure_ascii=False), encoding="utf-8")
        temp.replace(FEEDBACK_STATE_PATH)
    except OSError:
        pass  # Keep the in-memory state when the deployment directory is read-only.


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


def is_exact_ms_store_entry(entry, store):
    """Only a stock row belonging to this exact store can enter the MCK total."""
    expected_id = str(store.get("id") or urlparse(store.get("meta", {}).get("href") or "").path.rsplit("/", 1)[-1])
    actual_id = urlparse(entry.get("meta", {}).get("href") or "").path.rsplit("/", 1)[-1]
    if not expected_id or actual_id != expected_id:
        return False
    return not entry.get("name") or norm(entry["name"]) == norm(store.get("name"))


def load_ms_stocks_dict():
    required_tokens("MS_TOKEN")
    stores = ms_rows(f"{MS_API}/entity/store")
    matches = [s for s in stores if norm(s.get("name")) == norm(MS_STORE_NAME)]
    if len(matches) != 1:
        raise CheckError(f"Нужен один склад МойСклад с точным именем «{MS_STORE_NAME}», найдено: {len(matches)}")
    selected_store = matches[0]
    store_href = selected_store.get("meta", {}).get("href")
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
                     if is_exact_ms_store_entry(e, selected_store))
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


def ms_store_diagnostics():
    required_tokens("MS_TOKEN")
    stores = ms_rows(f"{MS_API}/entity/store")
    rows = ms_rows(f"{MS_API}/report/stock/bystore")
    summary = []
    for store in stores:
        count, quantity = 0, 0.0
        for row in rows:
            amount = sum(float(e.get("stock") or 0) for e in row.get("stockByStore") or []
                         if is_exact_ms_store_entry(e, store))
            if amount > 0:
                count += 1
                quantity += amount
        summary.append((str(store.get("name") or "без названия"), count, quantity))
    return summary


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
        time.sleep(0.65)  # WB Content allows approximately one request every 600 ms.
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
            if ("tnved" in card or "tnvedCode" in card or tnved_chars) and not any(
                    has_tnved_value(value) for value in tnved_values):
                missing.append("ТН ВЭД")
        if missing:
            issues.append(f"{cabinet} {art}: " + ", ".join(missing))
    return issues


def is_tnved_name(name):
    return "тнвэд" in re.sub(r"[^а-яёa-z0-9]", "", norm(name)) or "tnved" in norm(name)


def has_tnved_value(value):
    if isinstance(value, (list, tuple)):
        return any(has_tnved_value(item) for item in value)
    return value is not None and bool(str(value).strip()) and str(value).strip() != "0"


def document_issues(card, today=None):
    """Return confirmed business-rule issues and whether WB exposed document data."""
    documents = card.get("documents")
    if not isinstance(documents, dict) or not ({"items", "excludeDocuments"} & documents.keys()):
        return [], False
    if today is None:
        today = datetime.now(timezone(timedelta(hours=3))).date()
    subject = norm(card.get("subjectName"))
    vendor = norm(card.get("vendorCode"))
    if not subject and not re.search(r"(?:^|[-_])zont(?:[-_]|$)", vendor):
        return [], False  # Cannot distinguish a special-category umbrella without its subject.
    umbrella = "зонт" in subject or "umbrella" in subject or bool(re.search(r"(?:^|[-_])zont(?:[-_]|$)", vendor))
    excluded = documents.get("excludeDocuments")
    items = documents.get("items")
    issues = []
    if umbrella:
        if excluded is False:
            issues.append("отметить «документы не требуются»")
        elif excluded is None:
            return [], False
        return issues, True
    if excluded is True:
        issues.append("снять отметку «документы не требуются»")
    if not isinstance(items, list):
        return issues, False
    declarations = [item for item in items if isinstance(item, dict) and str(item.get("type")) == "2"]
    if not declarations:
        issues.append("нет декларации соответствия")
        return issues, True
    def problems(item):
        missing = []
        if not str(item.get("number") or "").strip():
            missing.append("номер ДС")
        for key, label in (("startDate", "дата начала ДС"), ("endDate", "дата окончания ДС")):
            value = item.get(key)
            if not value:
                missing.append(label)
                continue
            try:
                date = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
            except ValueError:
                missing.append(label)
                continue
            if key == "endDate" and date < today:
                missing.append("срок ДС истёк")
            if key == "startDate" and date > today:
                missing.append("ДС ещё не действует")
        return missing
    # One valid declaration suffices; report the best candidate otherwise.
    best = min((problems(item) for item in declarations), key=len)
    issues.extend(best)
    return issues, True


def compliance_issues(cards, cabinet, today=None):
    """Only confirmed TN VED/document issues, plus counts the API cannot verify."""
    issues = []
    tnved_unknown = documents_unknown = 0
    for card in cards:
        article = str(card.get("vendorCode") or card.get("nmID") or "без артикула")
        missing = []
        tnved_values = [card.get(key) for key in ("tnved", "tnvedCode") if key in card]
        tnved_values.extend(char.get("value") for char in card.get("characteristics") or []
                            if is_tnved_name(char.get("name")))
        if not tnved_values:
            tnved_unknown += 1
        elif not any(has_tnved_value(value) for value in tnved_values):
            missing.append("ТН ВЭД")
        doc_errors, exposed = document_issues(card, today=today)
        missing.extend(doc_errors)
        if not exposed:
            documents_unknown += 1
        if missing:
            issues.append(f"{cabinet} {article}: " + ", ".join(missing))
    return issues, tnved_unknown, documents_unknown


def compliance_manual_checks(cards, cabinet, today=None):
    """List articles whose fields cannot be checked, without calling them defects."""
    manual = []
    for card in cards:
        article = str(card.get("vendorCode") or card.get("nmID") or "без артикула")
        values = [card.get(key) for key in ("tnved", "tnvedCode") if key in card]
        values.extend(char.get("value") for char in card.get("characteristics") or []
                      if is_tnved_name(char.get("name")))
        fields = []
        if not values:
            fields.append("ТН ВЭД не передан")
        _, docs_exposed = document_issues(card, today=today)
        if not docs_exposed:
            fields.append("документы/категория не переданы")
        if fields:
            manual.append(f"{cabinet} {article}: " + ", ".join(fields) + " — проверить вручную")
    return manual


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
    status = await message.answer(f"VESCHI AI {VERSION}: проверяю два кабинета WB…" if kind == "аудит"
                                  else f"VESCHI AI {VERSION}: проверяю МойСклад и два кабинета WB…")
    try:
        if kind == "аудит":
            required_tokens("WB_TOKEN_1", "WB_TOKEN_2")
            issues = []
            manual = []
            for name, token in (("К1", WB_TOKEN_1), ("К2", WB_TOKEN_2)):
                cards = await asyncio.to_thread(get_real_wb_cards_and_scores, token)
                if not cards:
                    raise CheckError(f"Кабинет {name} вернул 0 карточек; аудит не завершён")
                found, _, _ = compliance_issues(cards, name)
                issues.extend(found)
                manual.extend(compliance_manual_checks(cards, name))
        else:
            ms, c1, c2, w1, w2, s1, s2, unresolved = await asyncio.to_thread(snapshot, kind == "остатки")
            if kind == "остатки":
                issues = stock_issues(ms, w1, w2, s1, s2)
            else:
                issues = new_issues(ms, c1, c2, w1, w2)
        await status.delete()
        if kind != "аудит" and unresolved and not issues:
            await message.answer(f"⚠️ VESCHI AI {VERSION}: {kind.capitalize()} — "
                                 f"среди {len(ms)} сопоставленных товаров отклонений нет; "
                                 f"ещё {len(unresolved)} товаров не проверены из-за отсутствующего артикула.")
        elif kind == "аудит" and manual and not issues:
            await message.answer(f"ℹ️ VESCHI AI {VERSION}: по доступным полям исправлений не найдено; "
                                 "часть карточек осталась непроверенной.")
        else:
            await send_issues(message, kind.capitalize(), issues,
                              max_items=None if kind == "аудит" else 30)
        if kind != "аудит" and unresolved:
            await message.answer("⚠️ Отдельно проверьте артикулы в МойСклад: " +
                                 ", ".join(unresolved[:10]) +
                                 (f" (ещё {len(unresolved) - 10})" if len(unresolved) > 10 else ""))
        if kind == "аудит" and manual:
            await send_issues(message, "Проверить вручную (WB не передал поля)", manual, max_items=None)
    except (CheckError, ValueError, TypeError) as exc:
        await status.edit_text(f"❌ VESCHI AI {VERSION}: проверка «{kind}» не выполнена: {exc}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global MY_CHAT_ID
    MY_CHAT_ID = message.chat.id
    FEEDBACK_STATE["chat_id"] = MY_CHAT_ID
    save_feedback_state()
    await message.answer(f"VESCHI AI {VERSION}: отправьте «остатки», «новинки» или «аудит». "
                         "Команда /version показывает версию. Проверяю новые отзывы раз в час.")


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


@dp.message(Command("msdiagnostics"))
async def cmd_msdiagnostics(message: types.Message):
    try:
        summary = await asyncio.to_thread(ms_store_diagnostics)
        lines = [f"VESCHI AI {VERSION}: остатки по отдельным складам МойСклад (физический stock):"]
        lines.extend(f"{name}: товаров с остатком {count}, всего {total:g} шт."
                     for name, count, total in summary)
        await message.answer("\n".join(lines)[:3800])
    except CheckError as exc:
        await message.answer(f"❌ Диагностика МойСклад не выполнена: {exc}")


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


async def run_scheduled_feedback_check():
    """One read each hour; notify only about unseen review IDs."""
    chat_id = MY_CHAT_ID or FEEDBACK_STATE.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        return
    try:
        from wildberries import get_unanswered_feedbacks
        feedbacks = await asyncio.to_thread(get_unanswered_feedbacks, take=100)
    except (RuntimeError, ValueError, TypeError):
        # On 429 or a transient error, wait until the next scheduled hour.
        return
    current_ids = [str(item["id"]) for item in feedbacks if item.get("id")]
    previous = set(FEEDBACK_STATE.get("seen") or [])
    if not FEEDBACK_STATE.get("initialized"):
        FEEDBACK_STATE["initialized"] = True
        FEEDBACK_STATE["seen"] = current_ids[:500]
        save_feedback_state()
        return  # The first poll establishes a baseline, not a flood of old reviews.
    new_ids = [item for item in current_ids if item not in previous]
    if new_ids:
        try:
            await bot.send_message(chat_id, "📩 У вас новый отзыв на Wildberries." if len(new_ids) == 1
                                   else f"📩 У вас новые отзывы на Wildberries: {len(new_ids)}.")
        except Exception:
            return  # Preserve unseen IDs for the next scheduled attempt.
    FEEDBACK_STATE["seen"] = list(dict.fromkeys(current_ids + list(previous)))[:500]
    save_feedback_state()


class _BotMessage:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    async def answer(self, text):
        await bot.send_message(self.chat_id, text)


async def run_queued_review_replies():
    from review_queue import process_due
    try:
        results = await asyncio.to_thread(process_due)
    except (RuntimeError, OSError) as exc:
        if MY_CHAT_ID or FEEDBACK_STATE.get("chat_id"):
            await bot.send_message(MY_CHAT_ID or FEEDBACK_STATE["chat_id"],
                                   f"⚠️ Очередь ответов WB не обработана: {exc}")
        return
    for feedback_id, state in results:
        if state in ("sent", "needs_check") and (MY_CHAT_ID or FEEDBACK_STATE.get("chat_id")):
            label = "опубликован" if state == "sent" else "требует ручной проверки"
            await bot.send_message(MY_CHAT_ID or FEEDBACK_STATE["chat_id"],
                                   f"Ответ на отзыв {feedback_id}: {label}.")


async def main():
    global bot
    required_tokens("BOT_TOKEN")
    load_feedback_state()
    bot = Bot(token=BOT_TOKEN)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(run_scheduled_stock_check, "cron", hour="9,15", minute=0)
    scheduler.add_job(run_scheduled_feedback_check, "cron", minute=10,
                      max_instances=1, coalesce=True)
    scheduler.add_job(run_queued_review_replies, "interval", minutes=5,
                      max_instances=1, coalesce=True)
    scheduler.start()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, handle_signals=False)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
