"""House Expense Tracker — Telegram Bot entry point (aiogram 3)."""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from db.database import engine
from db.models import Base
from bot.handlers import router

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    """Drop all tables and recreate them (full reset)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables reset (drop + create).")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    logger.info("Bot is starting …")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
