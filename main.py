import asyncio
import contextlib
import html
import logging
import sys
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import notifications
import service
from config import BOT_TOKEN, WATERMARK
from week import today

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("bot")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN или пропишите его в config.py")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

REPLY_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Расписание")]],
    resize_keyboard=True,
)

WATERMARK_HTML = html.escape(WATERMARK)

GREETING = (
    "Привет! Я показываю расписание группы <b>БП324</b> (ул. Ленина, 24).\n\n"
    "Кнопка «Расписание» — расписание на сегодня.\n"
    "Листать дни — стрелками ◀ ▶ под расписанием.\n"
    "Каждый вечер в 20:00 проверяю изменения на завтра и предупрежу, если они появятся.\n\n"
    f"<i>{WATERMARK_HTML}</i>"
)


def _nav_keyboard(current: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀", callback_data=f"nav:{(current - timedelta(days=1)).isoformat()}"),
                InlineKeyboardButton(text="Сегодня", callback_data=f"nav:{today().isoformat()}"),
                InlineKeyboardButton(text="▶", callback_data=f"nav:{(current + timedelta(days=1)).isoformat()}"),
            ]
        ]
    )


async def _day_text(target: date) -> str:
    return html.escape(await service.get_day_view(target))


async def send_day(message: Message, target: date) -> None:
    try:
        text = await _day_text(target)
    except Exception:
        logger.exception("Не удалось собрать расписание на %s", target)
        await message.answer("⚠️ Не удалось получить расписание. Попробуйте ещё раз чуть позже.", reply_markup=REPLY_KEYBOARD)
        return
    await message.answer(text, reply_markup=_nav_keyboard(target))


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    try:
        is_new = notifications.add_user(message.chat.id)
    except Exception:
        logger.exception("Не удалось сохранить подписчика %s", message.chat.id)
        is_new = False
    note = "\n🔔 Ты подписан на уведомления об изменениях в 20:00." if is_new else ""
    await message.answer(GREETING + note, reply_markup=REPLY_KEYBOARD)


@dp.message(F.text == "Расписание")
async def btn_schedule(message: Message) -> None:
    await send_day(message, today())


@dp.callback_query(F.data.startswith("nav:"))
async def cb_nav(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        target = date.fromisoformat(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError):
        return

    edited = False
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text("⏳ Загружаю расписание…")
        edited = True

    try:
        text = await _day_text(target)
    except Exception:
        logger.exception("Не удалось собрать расписание на %s", target)
        err = "⚠️ Не удалось получить расписание. Попробуйте ещё раз чуть позже."
        if edited:
            with contextlib.suppress(TelegramBadRequest):
                await callback.message.edit_text(err)
        else:
            await callback.message.answer(err)
        return

    if edited:
        try:
            await callback.message.edit_text(text, reply_markup=_nav_keyboard(target))
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
    await callback.message.answer(text, reply_markup=_nav_keyboard(target))


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer("Используйте кнопку «Расписание» или стрелки под расписанием.", reply_markup=REPLY_KEYBOARD)


async def main() -> None:
    asyncio.create_task(notifications.scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
