"""Точка входа Telegram-бота «Инвестиционный помощник»."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

from sheets import get_portfolio_summary

# Загружаем переменные из .env (там лежит BOT_TOKEN)
load_dotenv()

# Настраиваем логирование: будем видеть в терминале, что происходит
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Создаём бота и диспетчер
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

# URL Google-таблицы с данными из tinkoff-to-gsheets
SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1uh6ZnHfDFtnM7bRJgTnvv9UWBVI0UtY-TRItbK96D-g/edit"
)


# ---------- Обработчики команд ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Реакция на /start."""
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n\n"
        "Я — твой инвестиционный помощник.\n"
        "Показываю портфель из Google-таблицы Tinkoff Invest.\n\n"
        "Доступные команды:\n"
        "/start — это сообщение\n"
        "/help — справка\n"
        "/portfolio — показать портфель"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Реакция на /help."""
    await message.answer(
        "📖 <b>Справка</b>\n\n"
        "Я читаю данные из твоей Google-таблицы, которую наполняет "
        "скрипт <code>tinkoff-to-gsheets</code>, и показываю их здесь.\n\n"
        "<b>Команды:</b>\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/portfolio — текущий портфель",
        parse_mode="HTML",
    )


@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    """Показывает портфель из Google Sheets."""
    # Небольшая подсказка, что бот «думает» — если запросов много, полезно
    await message.answer("⏳ Читаю таблицу…")

    try:
        summary = get_portfolio_summary(SPREADSHEET_URL)
    except Exception as e:
        logging.exception("Ошибка чтения таблицы")
        await message.answer(
            f"⚠️ Не удалось прочитать таблицу.\n\n<code>{e}</code>",
            parse_mode="HTML",
        )
        return

    if not summary["positions"]:
        await message.answer("📭 В таблице нет данных о позициях.")
        return

    # Формируем сообщение
    lines = ["📊 <b>Ваш портфель</b>\n"]

    # Топ-10 позиций — самые крупные сверху
    for p in summary["positions"][:10]:
        lines.append(
            f"• <b>{p['ticker']}</b> — {p['name']}\n"
            f"  {p['quantity']:.0f} шт. × {p['price']:.2f} ₽ = "
            f"<b>{p['value']:,.2f} ₽</b>"
        )

    if len(summary["positions"]) > 10:
        lines.append(
            f"\n<i>… и ещё {len(summary['positions']) - 10} позиций</i>"
        )

    # Итоги
    lines.append(f"\n💰 <b>Активы:</b> {summary['total_value']:,.2f} ₽")
    if summary["money_value"] > 0:
        lines.append(
            f"💵 <b>Свободные деньги:</b> {summary['money_value']:,.2f} ₽"
        )
    lines.append(f"📦 <b>Всего позиций:</b> {summary['count']}")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ---------- Точка входа ----------

async def main():
    """Запускаем бота в режиме опроса Telegram (long polling)."""
    logging.info("Бот запущен")
    # drop_pending_updates=True — игнорируем сообщения, пришедшие пока бот спал
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())