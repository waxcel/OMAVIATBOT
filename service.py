import re
import time
from datetime import date

import changes as changes_mod
import timetable
import week as week_mod
from config import BELL_DEFAULT, BELL_THU_CLASS, BELL_THU_METHOD, VIEW_TTL, WATERMARK
from timetable import Lesson

RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_SUBGROUP_RE = re.compile(r"(\d)\s*пг", re.IGNORECASE)

_text_cache: dict[str, tuple[float, str]] = {}


def _config_bell(target: date, week: int) -> dict[int, tuple[str, str]]:
    if target.weekday() == 3:
        return BELL_THU_CLASS if week % 2 == 1 else BELL_THU_METHOD
    return BELL_DEFAULT


def _lesson_line(lesson: Lesson, mark: dict | None) -> str:
    line = lesson.name
    if lesson.subgroup:
        line += f" ({lesson.subgroup})"
    if lesson.teacher:
        line += f" — {lesson.teacher}"
    if lesson.room:
        line += f", ауд. {lesson.room}"
    if mark:
        if mark.get("cancelled"):
            line = f"❌ {line} — отменена"
        elif mark.get("replaced"):
            line = f"⚠️ {line} — замена"
        elif mark.get("added"):
            line = f"➕ {line}"
    return line


def _format_day(
    target: date,
    week: int,
    day_lessons: dict[int, list[Lesson]],
    bell_cfg: dict[int, tuple[str, str]],
    bell_site: dict[int, tuple[str, str]],
    marks: dict[int, dict[int, dict]],
) -> str:
    header = f"📅 {RU_DAYS[target.weekday()].capitalize()}, {target.strftime('%d.%m.%Y')} — {week} учебная неделя"

    event = None
    if target.weekday() == 3:
        if week % 2 == 1:
            event = ("🔔 Классный час (2-я смена)", "13:20", "14:00")
        else:
            event = ("🔔 Единый методический час", "13:15", "14:15")

    if not day_lessons and not event:
        return f"{header}\n\n🎉 Пар нет — можно отдыхать"

    lines = [header, ""]

    def pair_time(number: int, lessons: list[Lesson]) -> str:
        if number in bell_cfg:
            t = bell_cfg[number]
        elif lessons and lessons[0].time_start:
            t = (lessons[0].time_start, lessons[0].time_end)
        else:
            t = bell_site.get(number, ("", ""))
        if t[0] and t[1]:
            return f"{t[0]}–{t[1]}"
        return f"пара {number}"

    event_inserted = False
    for number in sorted(day_lessons):
        lessons = day_lessons[number]
        if not lessons:
            continue
        if event and number > 3 and not event_inserted:
            lines.append(f"{event[0]} — {event[1]}–{event[2]}")
            lines.append("")
            event_inserted = True
        lines.append(f"{number}. {pair_time(number, lessons)}")
        pair_marks = marks.get(number, {})
        for lesson in lessons:
            lines.append(f"   {_lesson_line(lesson, pair_marks.get(id(lesson)))}")
        lines.append("")

    if event and not event_inserted:
        lines.append(f"{event[0]} — {event[1]}–{event[2]}")
        lines.append("")

    return "\n".join(lines).strip()


def _apply_change(lessons: dict[int, list[Lesson]], change, marks: dict[int, dict[int, dict]]) -> None:
    def mark(pair: int, lesson: Lesson, **info) -> None:
        marks.setdefault(pair, {})[id(lesson)] = info

    def subgroup_label(n: int) -> str:
        return f"Подгруппа {n}"

    def make_new(pair: int, subgroup: str | None) -> Lesson:
        return Lesson(
            number=pair,
            time_start="",
            time_end="",
            name=change.new_subject or change.old_subject or "",
            teacher=change.new_teacher or change.old_teacher or None,
            room=change.new_room or change.old_room or None,
            subgroup=subgroup,
        )

    old_pair = change.old_pair
    new_pair = change.new_pair if change.new_pair is not None else old_pair

    if old_pair is None and new_pair is None:
        return

    source_lessons = lessons.get(old_pair, []) if old_pair is not None else []

    if change.is_cancellation:
        target = old_pair if old_pair is not None else new_pair
        sub = _subgroup_number(change.old_subject or change.new_subject or "")
        for lesson in lessons.get(target, []):
            if sub is None or (lesson.subgroup and lesson.subgroup.endswith(str(sub))):
                mark(target, lesson, cancelled=True)
        return

    old_empty = not (change.old_subject or change.old_teacher or change.old_room)
    if old_empty and not source_lessons and new_pair is not None:
        lessons.setdefault(new_pair, []).append(make_new(new_pair, None))
        return

    sub = _subgroup_number(change.old_subject) or _subgroup_number(change.new_subject)
    if sub is not None:
        matched = [l for l in source_lessons if l.subgroup and l.subgroup.endswith(str(sub))]
        if matched:
            for lesson in matched:
                source_lessons.remove(lesson)
            if not source_lessons:
                lessons.pop(old_pair, None)
            if change.new_subject or change.new_teacher or change.new_room:
                lessons.setdefault(new_pair, []).append(make_new(new_pair, subgroup_label(sub)))
        return

    if not (change.new_subject or change.new_teacher or change.new_room):
        for lesson in source_lessons:
            mark(old_pair, lesson, cancelled=True)
        return

    for lesson in source_lessons:
        mark(old_pair, lesson, replaced=True)
    lessons.pop(old_pair, None)
    lessons.setdefault(new_pair, []).append(make_new(new_pair, None))


def _build_day(target: date, week: int, changes_page: changes_mod.ChangesPage) -> str:
    schedule = timetable.fetch_schedule()
    day_lessons = {
        number: list(lessons)
        for number, lessons in schedule.get(week, {}).get(target.weekday(), {}).items()
    }

    marks: dict[int, dict[int, dict]] = {}
    for change in changes_mod.changes_for_group(changes_page.changes):
        _apply_change(day_lessons, change, marks)

    return _format_day(
        target,
        week,
        day_lessons,
        _config_bell(target, week),
        changes_page.bell_times,
        marks,
    )


async def get_day_view(target: date, refresh: bool = False) -> str:
    key = target.isoformat()
    if not refresh:
        hit = _text_cache.get(key)
        if hit and time.time() - hit[0] < VIEW_TTL:
            return hit[1]

    changes_page = await changes_mod.fetch_changes(target, refresh=refresh)
    week = week_mod.week_parity_for(target)
    text = _build_day(target, week, changes_page)
    text = f"{text}\n\n─────\n{WATERMARK}"
    _text_cache[key] = (time.time(), text)
    return text
