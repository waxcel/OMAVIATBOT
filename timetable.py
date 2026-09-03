import logging
import re
import time
from dataclasses import dataclass

from bs4 import BeautifulSoup

import http_client
from config import CHANGES_JSON_URL, GROUP, TIMETABLE_TTL, TIMETABLE_URL_TEMPLATE
from utils import norm

logger = logging.getLogger("timetable")

_warned_no_schedule = False

_cache: dict[str, tuple[float, object]] = {}

RU_DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


@dataclass
class Lesson:
    number: int
    time_start: str
    time_end: str
    name: str
    teacher: str | None = None
    room: str | None = None
    subgroup: str | None = None


# schedule[week][weekday 0..6 (Пн..Вс)][pair_number] -> list[Lesson]
Schedule = dict[int, dict[int, dict[int, list[Lesson]]]]


def _cached(key: str, ttl: int, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


def _parse_time_cell(cell) -> tuple[str, str]:
    tokens = [t for t in norm(cell.get_text()).split(" ") if ":" in t]
    start = tokens[0] if tokens else ""
    end = tokens[1] if len(tokens) > 1 else ""
    return start, end


def _parse_subject_cell(cell, number: int, t_start: str, t_end: str) -> list[Lesson]:
    lessons: list[Lesson] = []
    for subj in cell.select("div.subjectt"):
        name = norm(subj.select_one("div.subjectt-name").get_text()) if subj.select_one("div.subjectt-name") else ""
        if not name:
            continue
        for item in subj.select("div.subjectt-more-item"):
            subgroup, teacher, room = None, None, None
            for div in item.find_all("div", recursive=False):
                cls = div.get("class", []) or []
                text = norm(div.get_text())
                if not text:
                    continue
                if "subject-teacher" in cls:
                    if text.lower().startswith("подгруппа"):
                        subgroup = text
                    else:
                        teacher = text
                elif "subjectt-teacher" in cls:
                    room = text
            lessons.append(
                Lesson(number=number, time_start=t_start, time_end=t_end, name=name, teacher=teacher, room=room, subgroup=subgroup)
            )
    return lessons


def _parse_schedule(html: str) -> Schedule:
    soup = BeautifulSoup(html, "html.parser")
    schedule: Schedule = {1: {}, 2: {}}

    for table in soup.find_all("table"):
        headers = [norm(th.get_text()) for th in table.find_all("th")]
        day_headers = [(idx, h) for idx, h in enumerate(headers) if re.search(r"-(1|2)$", h)]
        if not day_headers:
            continue
        week = 1 if day_headers[0][1].endswith("-1") else 2
        day_map = {}
        for idx, h in day_headers:
            day_name = h[:-2].strip()
            if day_name in RU_DAYS:
                day_map[idx] = RU_DAYS.index(day_name)

        for row in table.find("tbody").find_all("tr", recursive=False) if table.find("tbody") else []:
            tds = row.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            try:
                number = int(norm(tds[0].get_text()))
            except ValueError:
                continue
            t_start, t_end = _parse_time_cell(tds[1])
            for idx, weekday in day_map.items():
                if idx >= len(tds):
                    continue
                lessons = _parse_subject_cell(tds[idx], number, t_start, t_end)
                if lessons:
                    schedule[week].setdefault(weekday, {})[number] = lessons

    return schedule


def _schedule_from_mirror(data: dict) -> Schedule:
    schedule: Schedule = {1: {}, 2: {}}
    for week_key, days in (data or {}).items():
        try:
            week = int(week_key)
        except (TypeError, ValueError):
            continue
        for day_key, pairs in days.items():
            try:
                weekday = int(day_key)
            except (TypeError, ValueError):
                continue
            for pair_key, lessons in pairs.items():
                try:
                    number = int(pair_key)
                except (TypeError, ValueError):
                    continue
                parsed = [
                    Lesson(
                        number=int(l["number"]),
                        time_start=l.get("time_start", ""),
                        time_end=l.get("time_end", ""),
                        name=l.get("name", ""),
                        teacher=l.get("teacher"),
                        room=l.get("room"),
                        subgroup=l.get("subgroup"),
                    )
                    for l in lessons
                ]
                if parsed:
                    schedule[week].setdefault(weekday, {})[number] = parsed
    return schedule


def fetch_schedule() -> Schedule:
    url = TIMETABLE_URL_TEMPLATE.format(group=GROUP)

    import mirror
    if CHANGES_JSON_URL:
        data = mirror.load()
        schedule_data = (data or {}).get("schedule") if isinstance(data, dict) else None
        if schedule_data:
            parsed = _schedule_from_mirror(schedule_data)
            _cache["schedule"] = (time.time(), parsed)
            return parsed
        global _warned_no_schedule
        if not _warned_no_schedule:
            logger.warning(
                "В зеркале нет расписания — обнови actions/fetch_changes.py в GitHub-репозитории "
                "на новую версию и перезапусти workflow. До обновления расписание пустое."
            )
            _warned_no_schedule = True
        return {1: {}, 2: {}}

    def factory():
        resp = http_client.get(url, timeout=30)
        resp.raise_for_status()
        return _parse_schedule(resp.text)

    return _cached("schedule", TIMETABLE_TTL, factory)
