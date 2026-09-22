"""Точка входа Telegram-бота «Инвестиционный помощник»."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from tinkoff_api import get_portfolio_snapshot
from recommender import recommend

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

MY_CHAT_ID = int(os.getenv("MY_CHAT_ID", "0"))

scheduler = AsyncIOScheduler()


# ---------- Форматирование ----------

def format_portfolio(snapshot):
    """Краткая сводка: топ-10 акций + итоги."""
    positions = [p for p in snapshot["positions"] if p["type"] == "share"]
    positions.sort(key=lambda p: p["value"], reverse=True)

    lines = ["📊 <b>Ваш портфель</b>\n"]

    for p in positions[:10]:
        lines.append(
            f"• <b>{p['name']}</b> — {p['ticker']}\n"
            f"  {p['quantity']:.0f} шт. × {p['price']:.2f} ₽ = "
            f"<b>{p['value']:,.2f} ₽</b>"
        )

    if len(positions) > 10:
        lines.append(f"\n<i>… и ещё {len(positions) - 10} акций</i>")

    cats = snapshot["categories"]
    lines.append(f"\n💰 <b>Всего:</b> {snapshot['total_value']:,.2f} ₽")
    lines.append(
        f"   Акции: {cats['Акции']['percent']:.1f}% · "
        f"Облигации: {cats['Облигации']['percent']:.1f}% · "
        f"Золото: {cats['Золото']['percent']:.1f}% · "
        f"Валюта: {cats['Валюта']['percent']:.1f}%"
    )
    if snapshot["free_cash_rub"] > 0:
        lines.append(f"💵 <b>Свободно:</b> {snapshot['free_cash_rub']:,.2f} ₽")

    return "\n".join(lines)


def format_structure(snapshot):
    """Структура по категориям и отраслям."""
    lines = ["⚖️ <b>Структура портфеля</b>\n"]

    cats = snapshot["categories"]
    targets = {"Акции": 70, "Облигации": 15, "Золото": 10, "Валюта": 5}

    lines.append("<b>Категории:</b>")
    for name, data in cats.items():
        target = targets[name]
        diff = data["percent"] - target
        arrow = "🟢" if abs(diff) < 0.5 else ("🔴" if diff < 0 else "🟡")
        sign = "+" if diff >= 0 else ""
        lines.append(
            f"{arrow} {name}: {data['percent']:.2f}% "
            f"(цель {target}%, {sign}{diff:.2f} п.п.)"
        )

    lines.append("\n<b>Отрасли (акции):</b>")
    sectors = sorted(snapshot["by_sector"].items(), key=lambda x: x[1]["value"])
    for sector, data in sectors:
        lines.append(f"• {sector}: {data['percent']:.2f}%")

    return "\n".join(lines)


def format_plan(recs, budget):
    """Форматирует план покупок с группировкой по категориям."""
    if not recs:
        return (
            "✅ <b>Рекомендаций нет.</b>\n\n"
            "Либо всё на цели, либо свободных денег нет на минимальный лот."
        )

    by_cat = {}
    for r in recs:
        by_cat.setdefault(r["category"], []).append(r)

    lines = [f"📋 <b>План покупок на {budget:,.0f} ₽</b>"]

    order = ["Валюта", "Золото", "Облигации", "Акции"]
    emoji = {"Валюта": "💵", "Золото": "🥇", "Облигации": "📜", "Акции": "📈"}

    total_plan = 0
    for cat in order:
        items = by_cat.get(cat)
        if not items:
            continue

        cat_sum = sum(r["amount"] for r in items)
        lines.append(f"\n{emoji[cat]} <b>{cat}</b> ({cat_sum:,.0f} ₽)")

        for r in items:
            lines.append(
                f"   • {r['comment']} — {r['quantity']:.0f} шт × "
                f"{r['price']:.2f} ₽ = <b>{r['amount']:,.0f} ₽</b>"
            )
            total_plan += r["amount"]

    lines.append(f"\n💰 <b>Итого:</b> {total_plan:,.2f} ₽ из {budget:,.2f} ₽")
    lines.append(f"💵 <b>Остаток:</b> {budget - total_plan:,.2f} ₽")

    return "\n".join(lines)


# ---------- Команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n\n"
        "Я читаю данные напрямую из Tinkoff Invest API.\n\n"
        "<b>Команды:</b>\n"
        "/portfolio — топ-10 позиций\n"
        "/structure — структура по категориям\n"
        "/what_to_buy — план покупок\n"
        "/help — справка",
        parse_mode="HTML",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Справка</b>\n\n"
        "Бот читает портфель через Tinkoff Invest API "
        "и присылает ежедневный отчёт в 19:00.\n\n"
        "<b>Команды:</b>\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/portfolio — топ-10 позиций\n"
        "/structure — категории и отрасли\n"
        "/what_to_buy — план покупок на свободные деньги\n"
        "/what_to_buy 15000 — виртуальный бюджет для теста",
        parse_mode="HTML",
    )


@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    await message.answer("⏳ Запрашиваю портфель…")
    try:
        snapshot = get_portfolio_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await message.answer(
            f"⚠️ Не удалось получить портфель.\n\n<code>{e}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(format_portfolio(snapshot), parse_mode="HTML")


@dp.message(Command("structure"))
async def cmd_structure(message: Message):
    await message.answer("⏳ Считаю структуру…")
    try:
        snapshot = get_portfolio_snapshot()
    except Exception as e:
        logging.exception("Ошибка Tinkoff API")
        await message.answer(
            f"⚠️ Ошибка: <code>{e}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(format_structure(snapshot), parse_mode="HTML")


@dp.message(Command("what_to_buy"))
async def cmd_what_to_buy(message: Message):
    # Парсим аргумент (виртуальный бюджет)
    text = message.text or ""
    parts = text.split(maxsplit=1)

    override = None
    if len(parts) > 1:
        raw = parts[1].strip().replace(" ", "").replace(",", ".")
        try:
            override = float(raw)
        except ValueError:
            await message.answer(
                f"⚠️ Не понял бюджет: <code>{parts[1]}</code>\n"
                f"Пример: <code>/what_to_buy 15000</code>",
                parse_mode="HTML",
            )
            return

    await message.answer("⏳ Считаю план покупок…")

    try:
        snapshot = get_portfolio_snapshot()
        budget = override if override is not None else snapshot["free_cash_rub"]

        if budget <= 0:
            await message.answer(
                "💵 <b>Свободных денег нет.</b>\n\n"
                "Пополните счёт или запустите с тестовым бюджетом:\n"
                "<code>/what_to_buy 15000</code>",
                parse_mode="HTML",
            )
            return

        recs = recommend(snapshot, override_budget=budget)

    except Exception as e:
        logging.exception("Ошибка при расчёте плана")
        await message.answer(
            f"⚠️ Не удалось рассчитать план.\n\n<code>{e}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(format_plan(recs, budget), parse_mode="HTML")


# ---------- Планировщик ----------

async def send_daily_report():
    logging.info("Отправка ежедневного отчёта")
    try:
        snapshot = get_portfolio_snapshot()
        text = (
            "🌙 <b>Вечерний отчёт</b>\n\n"
            + format_portfolio(snapshot)
            + "\n\n"
            + format_structure(snapshot)
        )
        await bot.send_message(chat_id=MY_CHAT_ID, text=text, parse_mode="HTML")
        logging.info("Отчёт отправлен")
    except Exception:
        logging.exception("Ошибка при отправке отчёта")


# ---------- Точка входа ----------

async def main():
    scheduler.add_job(send_daily_report, "cron", hour=19, minute=0)
    scheduler.start()
    logging.info("Планировщик запущен: ежедневный отчёт в 19:00")

    logging.info("Бот запущен")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())