import asyncio
import html
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import changes as changes_mod
import service
from config import DATA_DIR, NOTIFY_TIME, TIMEZONE, USERS_FILE
from week import today

logger = logging.getLogger("notifier")


def load_users() -> list[int]:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return [int(u) for u in json.load(f)]
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return []


def save_users(users: list[int]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(set(users)), f, ensure_ascii=False)


def add_user(chat_id: int) -> bool:
    """Добавить подписчика. Возвращает True, если он новый."""
    users = load_users()
    if chat_id in users:
        return False
    users.append(chat_id)
    save_users(users)
    return True


def _next_run() -> datetime:
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    run = now.replace(hour=NOTIFY_TIME[0], minute=NOTIFY_TIME[1], second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


def build_notification_text(target: date, day_view: str) -> str:
    header = f"📢 На завтра ({target.strftime('%d.%m')}) есть изменения — посмотри расписание!"
    return f"{header}\n\n{day_view}"


async def check_tomorrow_changes(bot) -> str | None:
    """Свежая проверка изменений на завтра. Возвращает текст рассылки или None."""
    target = today() + timedelta(days=1)
    changes_page = await changes_mod.fetch_changes(target, refresh=True)
    group_changes = changes_mod.changes_for_group(changes_page.changes)
    if not group_changes:
        return None
    day_view = await service.get_day_view(target, refresh=True)
    return build_notification_text(target, day_view)


async def broadcast(bot, text: str) -> int:
    users = load_users()
    sent = 0
    for chat_id in users:
        try:
            await bot.send_message(chat_id, html.escape(text))
            sent += 1
        except Exception as e:
            logger.warning("Не доставлено %s: %s", chat_id, e)
    return sent


async def run_daily_check(bot) -> int:
    """Вечерняя проверка: рассылка при наличии изменений на завтра. Возвращает число отправленных."""
    text = await check_tomorrow_changes(bot)
    if text is None:
        logger.info("Проверка %02d:%02d: изменений на завтра нет", *NOTIFY_TIME)
        return 0
    sent = await broadcast(bot, text)
    logger.info("Проверка %02d:%02d: рассылка доставлена %d подписчикам", *NOTIFY_TIME, sent)
    return sent


async def scheduler(bot) -> None:
    while True:
        run_at = _next_run()
        wait = (run_at - datetime.now(ZoneInfo(TIMEZONE))).total_seconds()
        logger.info("Следующая проверка изменений: %s (через %.0f мин)", run_at.strftime("%d.%m %H:%M"), wait / 60)
        await asyncio.sleep(max(wait, 1))
        try:
            await run_daily_check(bot)
        except Exception:
            logger.exception("Ошибка при вечерней проверке изменений")
        await asyncio.sleep(5)
