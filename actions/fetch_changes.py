import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright

from changes import _parse_changes
from config import CHANGES_URL, TIMEZONE

DAYS_AHEAD = 7


def render(page, url):
    try:
        page.goto(url, wait_until="commit", timeout=20000)
        return page.content()
    except Exception as e:
        print(f"Playwright error for {url}: {e}, trying requests fallback...")
        import requests
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as req_err:
            print(f"Requests fallback failed: {req_err}")
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


def main() -> None:
    tz = ZoneInfo(TIMEZONE)
    start = datetime.now(tz).date()
    days = [start + timedelta(days=i) for i in range(DAYS_AHEAD)]

    data: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for day in days:
            url = f"{CHANGES_URL}/{day.strftime('%d.%m.%Y')}"
            parsed = _parse_changes(render(page, url))
            data[day.isoformat()] = {
                "changes": [asdict(c) for c in parsed.changes],
                "bell_times": {str(k): list(v) for k, v in parsed.bell_times.items()},
            }
            print(f"{day.isoformat()}: {len(parsed.changes)} changes")
        browser.close()

    out_path = os.path.join(ROOT, "changes_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    total = sum(len(v["changes"]) for v in data.values())
    print(f"OK: {total} changes across {len(data)} days -> {out_path}")


if __name__ == "__main__":
    main()
