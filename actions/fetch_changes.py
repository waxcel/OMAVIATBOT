import json
import os
import sys
import time
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
from playwright.sync_api import sync_playwright

from changes import _parse_changes, probe_site
from config import CHANGES_URL, GROUP, TIMEZONE, TIMETABLE_URL_TEMPLATE
from timetable import _parse_schedule
from week import _parity_from_reference, _week_number_from_page

DAYS_BACK = 1
DAYS_AHEAD = 7


def render(page, url: str, max_wait: float = 25.0, min_wait: float = 4.0, stability_window: float = 1.0) -> str:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"Playwright navigation warning for {url}: {e}")

    def snapshot() -> str:
        return page.evaluate(
            "() => { const t = document.querySelector('table'); return t ? t.innerHTML : ''; }"
        )

    started = time.monotonic()
    prev = snapshot()
    stable_checks = 1
    deadline = started + max_wait

    while time.monotonic() < deadline:
        time.sleep(stability_window)
        current = snapshot()
        if current and current == prev:
            stable_checks += 1
            if stable_checks >= 2 and time.monotonic() - started >= min_wait:
                break
        else:
            prev = current
            stable_checks = 1

    return page.content()


def main() -> None:
    ok, details = probe_site()
    print(f"PROBE oat.ru: {'OK' if ok else 'FAIL'} — {details}")
    if not ok:
        print("oat.ru недоступен с GitHub Actions — зеркало собрать нельзя.")
        sys.exit(1)

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    days = [today + timedelta(days=i) for i in range(-DAYS_BACK, DAYS_AHEAD + 1)]

    # Получение базового расписания
    group_encoded = urllib.parse.quote(GROUP)
    tt_url = TIMETABLE_URL_TEMPLATE.format(group=group_encoded)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    schedule = {}
    local_schedule_file = os.path.join(ROOT, "schedule.html")
    if os.path.exists(local_schedule_file):
        try:
            with open(local_schedule_file, "r", encoding="utf-8") as f:
                schedule = _parse_schedule(f.read())
            print("Основное расписание загружено из локального schedule.html:", sorted(schedule.keys()))
        except Exception as e:
            print(f"Ошибка чтения локального schedule.html: {e}")

    if not schedule:
        try:
            resp = requests.get(tt_url, headers=headers, timeout=25)
            resp.encoding = resp.apparent_encoding or "utf-8"
            if resp.status_code == 200:
                schedule = _parse_schedule(resp.text)
                print("Основное расписание получено с сайта: недели", sorted(schedule.keys()))
            else:
                print(f"Warning: сайт вернул статус {resp.status_code}")
        except Exception as e:
            print(f"Warning: could not fetch base timetable: {e}")

    days_data: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
        page = context.new_page()

        for day in days:
            url = f"{CHANGES_URL}/{day.strftime('%d.%m.%Y')}"
            html_content = render(page, url)
            parsed = _parse_changes(html_content)
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
    print(f"OK: {total} changes, {len(days_data)} days, schedule x{len(schedule)} -> {out_path}")


if __name__ == "__main__":
    main()
