import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
from playwright.sync_api import sync_playwright

from changes import _parse_changes, probe_site
from config import CHANGES_URL, GROUP, TIMEZONE, TIMETABLE_URL_TEMPLATE
from timetable import _parse_schedule
from week import _parity_from_reference, _week_number_from_page

DAYS_AHEAD = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def render(page, url: str, max_wait: float = 25.0, min_wait: float = 6.0, stability_window: float = 1.5) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            last_error = None
            break
        except Exception as e:
            last_error = e
            print(f"Playwright: попытка {attempt}/3 не удалась для {url}: {str(e)[-200:]}")
            if attempt < 3:
                time.sleep(3)
    if last_error is not None:
        print(f"Playwright error for {url}: {last_error}, trying requests...")
        try:
            r = requests.get(url, timeout=20, headers=HEADERS)
            r.encoding = r.apparent_encoding or "utf-8"
            r.raise_for_status()
            return r.text
        except Exception:
            return ""

    def snapshot() -> str:
        return page.evaluate(
            "() => { const t = document.querySelector('table.customized.timetable tbody'); return t ? t.innerHTML : ''; }"
        )

    started = time.monotonic()
    prev = snapshot()
    stable_checks = 1
    deadline = started + max_wait
    while time.monotonic() < deadline:
        time.sleep(stability_window)
        current = snapshot()
        if current == prev:
            stable_checks += 1
            if stable_checks >= 2 and time.monotonic() - started >= min_wait:
                break
        else:
            prev = current
            stable_checks = 1
    return page.content()


def fetch_schedule_html(url: str) -> str:
    last_error = ""
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=(15, 30))
            resp.encoding = resp.apparent_encoding or "utf-8"
            if resp.status_code == 200 and len(resp.text) > 10_000:
                return resp.text
            last_error = f"HTTP {resp.status_code}, {len(resp.text)} байт"
        except Exception as e:
            last_error = str(e)[:150]
        print(f"Расписание: попытка {attempt}/3 не удалась ({last_error})")
        time.sleep(4 * attempt)
    print("Расписание: requests не помог, пробую через браузер...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            return render(page, url)
        finally:
            browser.close()


def main() -> None:
    ok, details = probe_site()
    print(f"PROBE oat.ru: {'OK' if ok else 'FAIL'} — {details}")
    if not ok:
        print("oat.ru недоступен с GitHub Actions — зеркало собрать нельзя.")
        sys.exit(1)

    tz = ZoneInfo(TIMEZONE)
    start = datetime.now(tz).date()
    days = [start + timedelta(days=i) for i in range(DAYS_AHEAD)]

    tt_url = TIMETABLE_URL_TEMPLATE.format(group=quote(GROUP))
    schedule = {}
    try:
        html = fetch_schedule_html(tt_url)
        schedule = _parse_schedule(html)
        print("Основное расписание: недели", sorted(schedule.keys()))
    except Exception as e:
        print(f"Не удалось получить страницу расписания: {e}")

    total_lessons = sum(
        len(lessons)
        for days_map in schedule.values()
        for pairs in days_map.values()
        for lessons in pairs.values()
    )
    if total_lessons == 0:
        print("ОШИБКА: расписание пустое — коммит отменён, чтобы бот не показывал «Пар нет». "
              "Через 15 минут cron повторит попытку.")
        sys.exit(1)

    days_data: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for day in days:
            url = f"{CHANGES_URL}/{day.strftime('%d.%m.%Y')}"
            parsed = _parse_changes(render(page, url))
            days_data[day.isoformat()] = {
                "changes": [asdict(c) for c in parsed.changes],
                "bell_times": {str(k): list(v) for k, v in parsed.bell_times.items()},
            }
            print(f"{day.isoformat()}: {len(parsed.changes)} changes")
        browser.close()

    weeks: dict[str, int] = {}
    for day in days:
        result = _week_number_from_page(day)
        weeks[day.isoformat()] = result[0] if result else _parity_from_reference(day)
    print("Недели:", weeks)

    data = {
        "generated_at": datetime.now(tz).isoformat(timespec="seconds"),
        "schedule": {
            str(week): {
                str(weekday): {
                    str(number): [asdict(l) for l in lessons]
                    for number, lessons in pairs.items()
                }
                for weekday, pairs in day_map.items()
            }
            for week, day_map in schedule.items()
        },
        "weeks": weeks,
        "days": days_data,
    }

    out_path = os.path.join(ROOT, "changes_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    total = sum(len(v["changes"]) for v in days_data.values())
    print(f"OK: {total} changes, {len(days_data)} days, {total_lessons} lessons, schedule x{len(schedule)} -> {out_path}")


if __name__ == "__main__":
    main()
