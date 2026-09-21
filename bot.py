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


def format_plan(snapshot, recs):
    """Форматирует план покупок."""
    if not recs:
        return (
            "✅ <b>Рекомендаций нет</b>\n\n"
            f"Свободно: {snapshot['free_cash_rub']:,.2f} ₽\n"
            "Либо всё на цели, либо не хватает на минимальную покупку."
        )

    lines = [f"🎯 <b>План покупок на {snapshot['free_cash_rub']:,.2f} ₽</b>\n"]

    total = 0
    for i, r in enumerate(recs, 1):
        lines.append(
            f"{i}. [{r['category']}] <b>{r['name']}</b>\n"
            f"   {r['quantity']:.0f} шт × {r['price']:.2f} ₽ = "
            f"{r['amount']:,.2f} ₽\n"
            f"   <i>{r['comment']}</i>"
        )
        total += r["amount"]

    lines.append(f"\n💰 <b>Итого:</b> {total:,.2f} ₽")
    lines.append(f"💵 <b>Остаток:</b> {snapshot['free_cash_rub'] - total:,.2f} ₽")
    lines.append("\n<i>Проверяйте актуальные цены перед покупкой.</i>")

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
        "/what_to_buy — план покупок на свободные деньги\n"
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
        "/what_to_buy — что купить на свободные средства",
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
        await message.answer(f"⚠️ Ошибка: <code>{e}</code>", parse_mode="HTML")
        return

    await message.answer(format_structure(snapshot), parse_mode="HTML")


@dp.message(Command("what_to_buy"))
async def cmd_what_to_buy(message: Message):
    await message.answer("⏳ Считаю рекомендации…")
    try:
        snapshot = get_portfolio_snapshot()
        recs = recommend(snapshot)
    except Exception as e:
        logging.exception("Ошибка рекомендаций")
        await message.answer(f"⚠️ Ошибка: <code>{e}</code>", parse_mode="HTML")
        return

    await message.answer(format_plan(snapshot, recs), parse_mode="HTML")


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