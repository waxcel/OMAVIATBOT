import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from config import CHANGES_URL, TIMEZONE, TIMETABLE_URL_TEMPLATE, WEEK1_START, WEEK_TTL
from utils import norm

_cache: dict[str, tuple[float, object]] = {}

_WEEK_RE = re.compile(r"(\d+)\s*учебн\w*\s*недел", re.IGNORECASE)
_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_TITLE_DATE_RE = re.compile(r"на\s+(\d{1,2})\s+([а-яё]+)", re.IGNORECASE)


def _cached(key: str, ttl: int, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


def today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _week_number_from_page(day: date) -> tuple[int, date] | None:
    """Fetch changes page for a date (static HTML, no JS) and parse 'N учебная неделя'."""
    url = f"{CHANGES_URL}/{day.strftime('%d.%m.%Y')}"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    subtitle = soup.find("h3", class_="section-subtitle-small")
    if not subtitle:
        return None
    match = _WEEK_RE.search(norm(subtitle.get_text()))
    if not match:
        return None
    week = int(match.group(1))
    if week not in (1, 2):
        return None

    title = soup.find("h2", class_="section-title")
    parsed_day = None
    if title:
        m = _TITLE_DATE_RE.search(norm(title.get_text()))
        if m:
            d, month_name = int(m.group(1)), m.group(2).lower()
            month = _RU_MONTHS.get(month_name)
            if month:
                try:
                    parsed_day = date(day.year, month, d)
                except ValueError:
                    parsed_day = None

    return week, parsed_day


def _parity_from_reference(day: date) -> int:
    ref = date(*WEEK1_START)
    ref_monday = ref - timedelta(days=ref.weekday())
    day_monday = day - timedelta(days=day.weekday())
    weeks = (day_monday - ref_monday).days // 7
    return 1 if weeks % 2 == 0 else 2


def week_parity_for(day: date) -> int:
    """Week number (1 or 2) for a date: from the college site, with fallbacks."""
    key = f"week:{day.isoformat()}"
    return _cached(
        key,
        WEEK_TTL,
        lambda: _week_parity_impl(day),
    )


def _week_parity_impl(day: date) -> int:
    result = _week_number_from_page(day)
    if result:
        week, parsed_day = result
        if parsed_day == day:
            return week

    base = today()
    base_result = _week_number_from_page(base)
    if base_result:
        base_week, parsed_day = base_result
        if parsed_day is None or parsed_day == base:
            base_monday = base - timedelta(days=base.weekday())
            day_monday = day - timedelta(days=day.weekday())
            offset = (day_monday - base_monday).days // 7
            return base_week if offset % 2 == 0 else 3 - base_week

    return _parity_from_reference(day)
