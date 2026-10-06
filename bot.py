"""Точка входа Telegram-бота «Инвестиционный помощник»."""
import asyncio
import html
import logging
import math
import os
import sys
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from t_tech.invest.exceptions import RequestError

from tinkoff_api import get_portfolio_snapshot
from recommender import TARGETS, recommend
from ai_advisor import analyze_portfolio, is_enabled

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_CHAT_ID = int(os.getenv("MY_CHAT_ID") or "0")

# Без токена и владельца бот либо не стартует, либо молча игнорирует всех
if not BOT_TOKEN or MY_CHAT_ID == 0:
    sys.exit("Ошибка: задайте BOT_TOKEN и MY_CHAT_ID в .env (переменные пусты или не заданы)")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Таймауты внешних вызовов (сек): потоки с API не должны вешать обработчик навсегда
SNAPSHOT_TIMEOUT = 120
PLAN_TIMEOUT = 330
AI_TIMEOUT = 90
TG_MESSAGE_LIMIT = 4096

# Явный часовой пояс: в Docker системное время — UTC, и без этого
# «отчёт в 19:00» приходил бы в 22:00 по Москве
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


SECTOR_EMOJI = {
    "Электроэнергетика": "⚡",
    "Сырьевая": "⛏️",
    "Потребительские": "🛒",
    "Финансовый": "💰",
    "IT": "💻",
    "Машиностроение и транспорт": "✈️",
    "Телекоммуникации": "📡",
    "Здравоохранение": "🏥",
    "Энергетика": "🛢️",
    "Прочее": "🔸",
}


# ---------- Middleware: доступ только для владельца ----------

class WhitelistMiddleware(BaseMiddleware):
    """Пропускает только сообщения от MY_CHAT_ID. Остальные молча игнорирует."""

    def __init__(self, allowed_user_id: int):
        self.allowed_user_id = allowed_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user and user.id == self.allowed_user_id:
            return await handler(event, data)

        # Чужой пользователь — молчим, ничего не отвечаем
        logging.warning(
            f"Отклонён доступ: user_id={user.id if user else 'unknown'}, "
            f"username={user.username if user else 'unknown'}"
        )
        return None


# Регистрируем middleware на сообщения и callback-кнопки
dp.message.middleware(WhitelistMiddleware(MY_CHAT_ID))
dp.callback_query.middleware(WhitelistMiddleware(MY_CHAT_ID))


# ---------- Клавиатуры ----------

def main_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Портфель"), KeyboardButton(text="⚖️ Структура")],
            [KeyboardButton(text="🎯 Что купить")],
            [KeyboardButton(text="🤖 AI-анализ")],
            [KeyboardButton(text="📖 Справка")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def buy_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="500 ₽", callback_data="buy_500"),
            InlineKeyboardButton(text="5 000 ₽", callback_data="buy_5000"),
        ],
        [
            InlineKeyboardButton(text="15 000 ₽", callback_data="buy_15000"),
            InlineKeyboardButton(text="50 000 ₽", callback_data="buy_50000"),
        ],
        [
            InlineKeyboardButton(text="💵 Свободные деньги", callback_data="buy_free"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back"),
        ],
    ])


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back")],
    ])


def main_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Портфель", callback_data="portfolio"),
            InlineKeyboardButton(text="⚖️ Структура", callback_data="structure"),
        ],
        [InlineKeyboardButton(text="🎯 Что купить", callback_data="buy_menu")],
        [InlineKeyboardButton(text="🤖 AI-анализ", callback_data="analyze")],
        [InlineKeyboardButton(text="📖 Справка", callback_data="help")],
    ])


# ---------- Редактирование сообщений ----------

async def _safe_edit(message, text, **kwargs):
    """edit_text, который не падает при повторном нажатии той же кнопки.

    Telegram отвечает ошибкой «message is not modified», если новый текст
    совпадает со старым. Это не ошибка для пользователя — просто игнорируем.
    """
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def _err_text(e):
    """Текст ошибки для пользователя: у RequestError str(e) часто пустой."""
    if isinstance(e, RequestError):
        parts = [getattr(e.code, "name", str(e.code))]
        details = getattr(e, "details", None)
        if details:
            parts.append(str(details))
        message = getattr(getattr(e, "metadata", None), "message", None)
        if message and message != details:
            parts.append(str(message))
        return ": ".join(parts)
    return str(e) or type(e).__name__


async def _with_timeout(func, timeout, *args, **kwargs):
    """asyncio.to_thread с таймаутом и понятным сообщением об ошибке."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"нет ответа за {timeout} с") from None


async def _get_snapshot():
    return await _with_timeout(get_portfolio_snapshot, SNAPSHOT_TIMEOUT)


def _u16len(s):
    """Длина строки в UTF-16 единицах — так Telegram считает лимит сообщения."""
    return len(s.encode("utf-16-le")) // 2


def _cut_u16(line, limit):
    """(голова, хвост): голова ≤ limit UTF-16 единиц, суррогатные пары не рвутся."""
    used = 0
    for i, ch in enumerate(line):
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > limit:
            return line[:i], line[i:]
        used += w
    return line, ""


def _split_message(text, limit=TG_MESSAGE_LIMIT):
    """Режет текст на части ≤ limit UTF-16 единиц по переводам строки."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        while _u16len(line) > limit:  # одна очень длинная строка — режем жёстко
            if current:
                chunks.append(current)
                current = ""
            head, line = _cut_u16(line, limit)
            chunks.append(head)
        if current and _u16len(current) + 1 + _u16len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def _answer_long(message, text, reply_markup=None, **kwargs):
    """message.answer для длинных текстов: клавиатура — только на последней части."""
    chunks = _split_message(text) or ["(пустой ответ)"]
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.answer(chunk, reply_markup=reply_markup if is_last else None, **kwargs)


# ---------- Форматирование ----------

NBSP = " "  # неразрывный пробел: «4 489 ₽» не разорвётся на две строки

CATEGORY_EMOJI = {"Валюта": "💵", "Золото": "🥇", "Облигации": "📜", "Акции": "📈"}


def _rub(x, decimals=0):
    """Сумма по-русски: 4489.5 → «4 490 ₽», с decimals=2 → «4 489,50 ₽»."""
    s = f"{x:,.{decimals}f}".replace(",", NBSP).replace(".", ",")
    return f"{s}{NBSP}₽"


def _pct(x, decimals=1):
    """Процент по-русски: 10.26 → «10,3%»."""
    return f"{x:.{decimals}f}".replace(".", ",") + "%"


def _num(x, decimals=2):
    return f"{x:,.{decimals}f}".replace(",", NBSP).replace(".", ",")


def format_portfolio(snapshot):
    positions = [p for p in snapshot["positions"] if p["type"] == "share"]
    positions.sort(key=lambda p: p["value"], reverse=True)

    lines = ["📊 <b>Ваш портфель</b>\n"]

    for p in positions[:10]:
        lines.append(
            f"• <b>{html.escape(p['name'])}</b> — {html.escape(p['ticker'])}\n"
            f"  {p['quantity']:.0f} шт × {_num(p['price'])} ₽ = "
            f"<b>{_rub(p['value'], 2)}</b>"
        )

    if len(positions) > 10:
        lines.append(f"\n<i>… и ещё {len(positions) - 10} акций</i>")

    cats = snapshot["categories"]
    lines.append(f"\n💰 <b>Всего в категориях:</b> {_rub(snapshot['total_value'], 2)}")
    lines.append(
        f"   Акции {_pct(cats['Акции']['percent'])} · "
        f"Облигации {_pct(cats['Облигации']['percent'])} · "
        f"Золото {_pct(cats['Золото']['percent'])} · "
        f"Валюта {_pct(cats['Валюта']['percent'])}"
    )
    if snapshot["free_cash_rub"] > 0:
        lines.append(f"💵 <b>Свободно:</b> {_rub(snapshot['free_cash_rub'], 2)}")

    return "\n".join(lines)


def format_structure(snapshot):
    lines = ["⚖️ <b>Структура портфеля</b>\n"]

    cats = snapshot["categories"]

    lines.append("<b>Категории:</b>")
    for name, data in cats.items():
        target = TARGETS[name]
        diff = data["percent"] - target
        arrow = "🟢" if abs(diff) < 0.5 else ("🔴" if diff < 0 else "🟡")
        sign = "+" if diff >= 0 else "−"
        lines.append(
            f"{arrow} {name}: {_pct(data['percent'], 2)} "
            f"(цель {_pct(target, 0)}, {sign}{_num(abs(diff))} п.п.)"
        )

    lines.append("\n<b>Отрасли (акции):</b>")
    sectors = sorted(snapshot["by_sector"].items(), key=lambda x: x[1]["value"])
    for sector, data in sectors:
        icon = SECTOR_EMOJI.get(sector, "🔸")
        lines.append(f"{icon} {sector}: {_pct(data['percent'], 2)}")

    return "\n".join(lines)


def _item_line(title, r):
    """Строка позиции плана: «• Название — 5 шт × 897,82 ₽ = 4 489 ₽»."""
    return (
        f"   • <b>{html.escape(title)}</b>\n"
        f"      {r['quantity']:.0f} шт × {_num(r['price'])} ₽ = <b>{_rub(r['amount'])}</b>"
    )


def format_plan(recs, budget, snapshot=None):
    """План покупок.

    Доли категорий: «сейчас → после покупки». Сейчас — от реального портфеля
    (как в «Структуре»). После — портфель + весь бюджет: в обоих режимах
    (свободные деньги или сумма вручную) итог после покупки = бумаги + бюджет.
    """
    if not recs:
        return (
            "✅ <b>Рекомендаций нет.</b>\n\n"
            "Либо всё на цели, либо свободных денег нет на минимальный лот."
        )

    by_cat = {}
    for r in recs:
        by_cat.setdefault(r["category"], []).append(r)

    total_now = snapshot["total_with_cash"] if snapshot else 0.0
    total_after = (snapshot["total_value"] + budget) if snapshot else 0.0

    stocks_now = snapshot["categories"]["Акции"]["value"] if snapshot else 0.0
    stocks_after = stocks_now + sum(r["amount"] for r in by_cat.get("Акции", []))

    lines = [f"📋 <b>План покупок на {_rub(budget)}</b>"]

    total_plan = 0
    for cat in ["Валюта", "Золото", "Облигации", "Акции"]:
        items = by_cat.get(cat)
        if not items:
            continue

        cat_sum = sum(r["amount"] for r in items)
        total_plan += cat_sum
        lines.append(f"\n{CATEGORY_EMOJI[cat]} <b>{cat} — {_rub(cat_sum)}</b>")

        if snapshot and total_now > 0 and total_after > 0:
            value_now = snapshot["categories"][cat]["value"]
            pct_now = value_now / total_now * 100
            pct_after = (value_now + cat_sum) / total_after * 100
            lines.append(
                f"   доля {_pct(pct_now)} → {_pct(pct_after)} · цель {_pct(TARGETS[cat], 0)}"
            )

        if cat == "Акции":
            by_sector = {}
            for r in items:
                by_sector.setdefault(r["sector"], []).append(r)

            for sector, sector_items in sorted(
                by_sector.items(), key=lambda x: sum(r["amount"] for r in x[1]),
            ):
                sector_sum = sum(r["amount"] for r in sector_items)
                icon = SECTOR_EMOJI.get(sector, "🔸")
                header = f"\n   {icon} <b>{sector}</b> — {_rub(sector_sum)}"
                if snapshot and sector in snapshot.get("by_sector", {}) and stocks_now > 0:
                    sector_now = snapshot["by_sector"][sector]["value"]
                    header += (
                        f"\n      в акциях {_pct(sector_now / stocks_now * 100)}"
                        f" → {_pct((sector_now + sector_sum) / stocks_after * 100)}"
                    )
                lines.append(header)

                for r in sector_items:
                    name_part = r["comment"]
                    if ": " in name_part:
                        name_part = name_part.split(": ", 1)[1]
                    name_part = name_part.replace(" (добор)", "")
                    lines.append(
                        f"      • <b>{html.escape(r['ticker'])}</b> — {html.escape(name_part)}\n"
                        f"         {r['quantity']:.0f} шт × {_num(r['price'])} ₽ = <b>{_rub(r['amount'])}</b>"
                    )
        else:
            for r in items:
                if cat == "Облигации":
                    lines.append(_item_line(f"{r['name']} ({r['ticker']})", r))
                    details = []
                    if r.get("ytm"):
                        details.append(f"доходность {_pct(r['ytm'], 2)}")
                    if r.get("maturity"):
                        y, m, d = r["maturity"].split("-")
                        details.append(f"погашение {d}.{m}.{y}")
                    if details:
                        lines.append(f"      {' · '.join(details)}")
                    if r.get("aci"):
                        lines.append(
                            f"      цена {_num(r['clean_price'])} ₽ + НКД {_num(r['aci'])} ₽"
                        )
                else:
                    lines.append(_item_line(r["name"], r))

    lines.append(f"\n💰 <b>Итого:</b> {_rub(total_plan, 2)} из {_rub(budget, 2)}")
    lines.append(f"💵 <b>Остаток:</b> {_rub(budget - total_plan, 2)}")

    return "\n".join(lines)


def help_text():
    return (
        "📖 <b>Справка</b>\n\n"
        "Бот читает портфель через Tinkoff Invest API "
        "и присылает ежедневный отчёт в 19:00.\n\n"
        "<b>Возможности:</b>\n"
        "📊 Портфель — топ-10 позиций\n"
        "⚖️ Структура — категории и отрасли\n"
        "🎯 Что купить — план покупок на сумму\n"
        "🤖 AI-анализ — анализ от YandexGPT\n\n"
        "Кнопки внизу экрана всегда под рукой.\n"
        "В разделе «Что купить» можно указать сумму вручную:\n"
        "<code>/what_to_buy 15000</code>"
    )


# ---------- Команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n\n"
        "Я — твой инвестиционный помощник.\n"
        "Пользуйся кнопками внизу экрана 👇",
        reply_markup=main_reply_kb(),
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(
        "🎛 <b>Главное меню</b>\n\nКнопки — внизу экрана.",
        parse_mode="HTML",
        reply_markup=main_reply_kb(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(help_text(), parse_mode="HTML", reply_markup=back_kb())


@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    await message.answer("⏳ Запрашиваю портфель…")
    try:
        snapshot = await _get_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await message.answer(
            f"⚠️ Не удалось получить портфель.\n\n<code>{html.escape(_err_text(e))}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        format_portfolio(snapshot),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )


@dp.message(Command("structure"))
async def cmd_structure(message: Message):
    await message.answer("⏳ Считаю структуру…")
    try:
        snapshot = await _get_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await message.answer(
            f"⚠️ Ошибка: <code>{html.escape(_err_text(e))}</code>",
            parse_mode="HTML",
        )
        return

    await _answer_long(
        message,
        format_structure(snapshot),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )


@dp.message(Command("what_to_buy"))
async def cmd_what_to_buy(message: Message):
    text = message.text or ""
    parts = text.split(maxsplit=1)

    override = None
    if len(parts) > 1:
        raw = parts[1].strip().replace(" ", "").replace(",", ".")
        try:
            override = float(raw)
            if not (math.isfinite(override) and 0 < override <= 1e8):
                raise ValueError("сумма вне допустимого диапазона")
        except ValueError:
            await message.answer(
                f"⚠️ Не понял бюджет: <code>{html.escape(parts[1])}</code>\n"
                f"Нужно положительное число не больше 100 000 000.\n"
                f"Пример: <code>/what_to_buy 15000</code>",
                parse_mode="HTML",
            )
            return

    await message.answer("⏳ Считаю план покупок…")
    await _send_plan(message, override)


@dp.message(Command("analyze"))
async def cmd_analyze(message: Message):
    """AI-анализ портфеля через YandexGPT."""
    if not is_enabled():
        await message.answer(
            "⚠️ AI-анализ не настроен.\n\n"
            "Добавьте <code>YANDEX_API_KEY</code> и <code>YANDEX_FOLDER_ID</code> в .env",
            parse_mode="HTML",
        )
        return

    await message.answer("🤔 Анализирую портфель… Это может занять несколько секунд.")

    try:
        snapshot = await _get_snapshot()
        analysis = await _with_timeout(analyze_portfolio, AI_TIMEOUT, snapshot)
        await _answer_long(message, analysis)
    except Exception as e:
        logging.exception("Ошибка AI-анализа")
        await message.answer(f"⚠️ Не удалось выполнить анализ: {e}")


async def _send_plan(message: Message, override):
    try:
        snapshot = await _get_snapshot()
        budget = override if override is not None else snapshot["free_cash_rub"]

        if budget <= 0:
            await message.answer(
                "💵 <b>Свободных денег нет.</b>\n\n"
                "Выбери сумму кнопкой ниже или пополни счёт.",
                parse_mode="HTML",
                reply_markup=buy_menu_kb(),
            )
            return

        # Расчёт плана ходит в API (ОФЗ, wishlist) — выносим в поток,
        # чтобы бот не «замирал» для остальных сообщений
        recs = await _with_timeout(recommend, PLAN_TIMEOUT, snapshot, override_budget=budget)
    except Exception as e:
        logging.exception("Ошибка при расчёте плана")
        await message.answer(
            f"⚠️ Не удалось рассчитать план.\n\n<code>{html.escape(_err_text(e))}</code>",
            parse_mode="HTML",
        )
        return

    await _answer_long(
        message,
        format_plan(recs, budget, snapshot),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )


# ---------- Reply-кнопки ----------

@dp.message(F.text == "📊 Портфель")
async def msg_portfolio(message: Message):
    await cmd_portfolio(message)


@dp.message(F.text == "⚖️ Структура")
async def msg_structure(message: Message):
    await cmd_structure(message)


@dp.message(F.text == "🎯 Что купить")
async def msg_buy(message: Message):
    await message.answer(
        "💰 <b>На какую сумму считать план?</b>\n\n"
        "Выбери быструю сумму или отправь вручную:\n"
        "<code>/what_to_buy 50000</code>",
        parse_mode="HTML",
        reply_markup=buy_menu_kb(),
    )


@dp.message(F.text == "🤖 AI-анализ")
async def msg_analyze(message: Message):
    await cmd_analyze(message)


@dp.message(F.text == "📖 Справка")
async def msg_help(message: Message):
    await message.answer(help_text(), parse_mode="HTML", reply_markup=back_kb())


# ---------- Callback ----------

@dp.callback_query(F.data == "back")
async def cb_back(callback: CallbackQuery):
    await _safe_edit(callback.message,
        "🎛 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=main_inline_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    await _safe_edit(callback.message,
        help_text(),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "portfolio")
async def cb_portfolio(callback: CallbackQuery):
    await callback.answer("⏳ Загружаю…")
    try:
        snapshot = await _get_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await _safe_edit(callback.message,
            f"⚠️ Ошибка: <code>{html.escape(_err_text(e))}</code>",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    await _safe_edit(callback.message,
        format_portfolio(snapshot),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )


@dp.callback_query(F.data == "structure")
async def cb_structure(callback: CallbackQuery):
    await callback.answer("⏳ Считаю…")
    try:
        snapshot = await _get_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await _safe_edit(callback.message,
            f"⚠️ Ошибка: <code>{html.escape(_err_text(e))}</code>",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
        return

    await _safe_edit(callback.message,
        format_structure(snapshot),
        parse_mode="HTML",
        reply_markup=back_kb(),
    )


@dp.callback_query(F.data == "analyze")
async def cb_analyze(callback: CallbackQuery):
    if not is_enabled():
        await callback.answer("AI-анализ не настроен", show_alert=True)
        return

    await callback.answer("🤔 Анализирую…")
    try:
        snapshot = await _get_snapshot()
        analysis = await _with_timeout(analyze_portfolio, AI_TIMEOUT, snapshot)
    except Exception as e:
        logging.exception("Ошибка AI-анализа")
        text = f"⚠️ Ошибка AI-анализа: <code>{html.escape(_err_text(e))}</code>"
        try:
            await _safe_edit(callback.message, text, parse_mode="HTML", reply_markup=back_kb())
        except Exception:
            logging.exception("Не удалось показать ошибку правкой сообщения")
            try:
                await callback.message.answer(text, parse_mode="HTML", reply_markup=back_kb())
            except Exception:
                logging.exception("Не удалось отправить сообщение об ошибке")
        return

    # Ответ может не влезть в одно сообщение: первая часть — правкой, остальные — новыми
    chunks = _split_message(analysis) or ["(пустой ответ)"]
    try:
        await _safe_edit(callback.message,
            chunks[0],
            reply_markup=back_kb() if len(chunks) == 1 else None,
        )
        for i, chunk in enumerate(chunks[1:], start=1):
            is_last = i == len(chunks) - 1
            await callback.message.answer(chunk, reply_markup=back_kb() if is_last else None)
    except Exception as e:
        logging.exception("Не удалось отправить результат AI-анализа")
        try:
            await callback.message.answer(
                f"⚠️ Не удалось отправить результат анализа: {e}",
                reply_markup=back_kb(),
            )
        except Exception:
            logging.exception("Не удалось отправить сообщение об ошибке")


@dp.callback_query(F.data == "buy_menu")
async def cb_buy_menu(callback: CallbackQuery):
    await _safe_edit(callback.message,
        "💰 <b>На какую сумму считать план?</b>\n\n"
        "Выбери быструю сумму или отправь вручную:\n"
        "<code>/what_to_buy 50000</code>",
        parse_mode="HTML",
        reply_markup=buy_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_free")
async def cb_buy_free(callback: CallbackQuery):
    await callback.answer("⏳ Считаю…")
    await _send_plan(callback.message, override=None)


@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy_amount(callback: CallbackQuery):
    try:
        amount = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return

    await callback.answer("⏳ Считаю…")
    await _send_plan(callback.message, override=amount)


# ---------- Планировщик ----------

async def send_daily_report():
    logging.info("Отправка ежедневного отчёта")
    try:
        snapshot = await _get_snapshot()
        text = (
            "🌙 <b>Вечерний отчёт</b>\n\n"
            + format_portfolio(snapshot)
            + "\n\n"
            + format_structure(snapshot)
        )
        chunks = _split_message(text)
        for i, chunk in enumerate(chunks):
            await bot.send_message(
                chat_id=MY_CHAT_ID,
                text=chunk,
                parse_mode="HTML",
                reply_markup=main_reply_kb() if i == len(chunks) - 1 else None,
            )
        logging.info("Отчёт отправлен")
    except Exception:
        logging.exception("Ошибка при отправке отчёта")


# ---------- Точка входа ----------

async def main():
    scheduler.add_job(send_daily_report, "cron", hour=19, minute=0)
    scheduler.start()
    logging.info("Планировщик запущен: ежедневный отчёт в 19:00")

    logging.info(f"Бот запущен. Разрешён только user_id={MY_CHAT_ID}")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())