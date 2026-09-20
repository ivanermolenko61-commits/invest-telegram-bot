"""Точка входа Telegram-бота «Инвестиционный помощник»."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

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


# ---------- Обработчики команд ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Реакция на /start."""
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n\n"
        "Я — твой инвестиционный помощник.\n"
        "Пока я умею немного, но скоро научусь больше.\n\n"
        "Доступные команды:\n"
        "/start — это сообщение\n"
        "/help — справка\n"
        "/portfolio — показать портфель (скоро)"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Реакция на /help."""
    await message.answer(
        "📖 <b>Справка</b>\n\n"
        "Я помогаю следить за инвестиционным портфелем.\n"
        "Данные беру из твоей Google-таблицы.\n\n"
        "Команды:\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/portfolio — портфель (в разработке)",
        parse_mode="HTML",
    )


@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message):
    """Пока что заглушка — реальную логику добавим позже."""
    await message.answer("📊 Портфель: команда в разработке. Скоро здесь будут данные.")


# ---------- Точка входа ----------

async def main():
    """Запускаем бота в режиме опроса Telegram (long polling)."""
    logging.info("Бот запущен")
    # drop_pending_updates=True — игнорируем сообщения, пришедшие пока бот спал
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())