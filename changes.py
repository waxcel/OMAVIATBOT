import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from config import CHANGES_JSON_URL, CHANGES_TTL, CHANGES_URL, GROUP
from utils import norm, norm_group

logger = logging.getLogger("changes")

_cache: dict[str, tuple[float, object]] = {}
_lock = asyncio.Lock()


@dataclass
class Change:
    course: str
    group: str
    reason: str
    old_pair: int | None
    old_room: str
    old_subject: str
    old_teacher: str
    new_pair: int | None
    new_room: str
    new_subject: str
    new_teacher: str

    @property
    def is_cancellation(self) -> bool:
        if "отмен" in self.reason.lower():
            return True
        return not (self.new_subject or self.new_teacher or self.new_room)


@dataclass
class ChangesPage:
    changes: list[Change]
    bell_times: dict[int, tuple[str, str]]


def _cached(key: str, ttl: int, factory):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    _cache[key] = (now, value)
    return value


async def _render_page(url: str, max_wait: float = 25.0, min_wait: float = 6.0, stability_window: float = 1.5) -> str:
    async with _lock:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                async def snapshot() -> str:
                    return await page.evaluate(
                        "() => { const t = document.querySelector('table.customized.timetable tbody'); return t ? t.innerHTML : ''; }"
                    )

                started = time.monotonic()
                prev = await snapshot()
                stable_checks = 1
                deadline = started + max_wait
                while time.monotonic() < deadline:
                    await asyncio.sleep(stability_window)
                    current = await snapshot()
                    if current == prev:
                        stable_checks += 1
                        if stable_checks >= 2 and time.monotonic() - started >= min_wait:
                            break
                    else:
                        prev = current
                        stable_checks = 1

                return await page.content()
            finally:
                await browser.close()


def _parse_pair(value: str) -> int | None:
    value = norm(value)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_changes(html: str) -> ChangesPage:
    soup = BeautifulSoup(html, "html.parser")

    changes_table = None
    for table in soup.find_all("table"):
        headers = [norm(th.get_text()).lower() for th in table.find_all("th")]
        if "группа" in headers and "причина" in headers:
            changes_table = table
            break

    changes: list[Change] = []
    if changes_table:
        body = changes_table.find("tbody")
        if body:
            for row in body.find_all("tr", recursive=False):
                tds = [norm(td.get_text()) for td in row.find_all("td", recursive=False)]
                if len(tds) != 11:
                    continue
                change = Change(
                    course=tds[0],
                    group=tds[1],
                    old_pair=_parse_pair(tds[2]),
                    old_room=tds[3],
                    old_subject=tds[4],
                    old_teacher=tds[5],
                    reason=tds[6],
                    new_pair=_parse_pair(tds[7]),
                    new_room=tds[8],
                    new_subject=tds[9],
                    new_teacher=tds[10],
                )
                changes.append(change)

    bell_times: dict[int, tuple[str, str]] = {}
    for table in soup.find_all("table"):
        headers = [norm(th.get_text()).lower() for th in table.find_all("th")]
        if headers and all("режим звонков" in h for h in headers):
            body = table.find("tbody")
            if body:
                for row in body.find_all("tr", recursive=False):
                    tds = [norm(td.get_text()) for td in row.find_all("td", recursive=False)]
                    if len(tds) == 2 and "-" in tds[1]:
                        try:
                            pair = int(tds[0])
                            start, end = [t.strip() for t in tds[1].split("-", 1)]
                            bell_times[pair] = (start, end)
                        except ValueError:
                            continue
            break

    return ChangesPage(changes=changes, bell_times=bell_times)


def changes_for_group(changes: list[Change], group: str = GROUP) -> list[Change]:
    target = norm_group(group)
    result = []
    for change in changes:
        cell = norm_group(change.group)
        if cell == target:
            result.append(change)
        elif cell.startswith(target) and cell[len(target):len(target) + 1] and not cell[len(target)].isdigit():
            result.append(change)
    return result


def _empty_page() -> ChangesPage:
    return ChangesPage(changes=[], bell_times={})


def _page_from_json(entry: dict) -> ChangesPage:
    changes = [
        Change(
            course=str(c.get("course", "")),
            group=str(c.get("group", "")),
            reason=str(c.get("reason", "")),
            old_pair=c.get("old_pair"),
            old_room=str(c.get("old_room", "")),
            old_subject=str(c.get("old_subject", "")),
            old_teacher=str(c.get("old_teacher", "")),
            new_pair=c.get("new_pair"),
            new_room=str(c.get("new_room", "")),
            new_subject=str(c.get("new_subject", "")),
            new_teacher=str(c.get("new_teacher", "")),
        )
        for c in entry.get("changes", [])
    ]
    bell_times: dict[int, tuple[str, str]] = {}
    for k, v in entry.get("bell_times", {}).items():
        try:
            bell_times[int(k)] = (v[0], v[1])
        except (ValueError, IndexError, TypeError):
            continue
    return ChangesPage(changes=changes, bell_times=bell_times)


def _fetch_from_json_source(day: date) -> ChangesPage | None:
    """Берёт изменения из внешнего JSON (например, обновляемого GitHub Actions).

    Возвращает None, если источник не настроен (тогда используем Playwright).
    Если источник настроен, но данных за дату нет — считаем, что изменений нет
    (это безопаснее, чем запускать браузер в памяти маленького сервера).
    """
    if not CHANGES_JSON_URL:
        return None
    try:
        resp = requests.get(f"{CHANGES_JSON_URL}?t={int(time.time())}", timeout=20)
        resp.raise_for_status()
        raw = json.loads(resp.text)
    except Exception:
        logger.exception("Не удалось получить JSON изменений из %s", CHANGES_JSON_URL)
        return _empty_page()
    entry = raw.get(day.isoformat())
    if not entry:
        return _empty_page()
    return _page_from_json(entry)


async def fetch_changes(day: date, refresh: bool = False) -> ChangesPage:
    key = f"changes:{day.isoformat()}"

    if not refresh:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CHANGES_TTL:
            return hit[1]

    json_page = _fetch_from_json_source(day)
    if json_page is not None:
        page = json_page
    else:
        url = f"{CHANGES_URL}/{day.strftime('%d.%m.%Y')}"
        html = await _render_page(url)
        page = _parse_changes(html)

    _cache[key] = (time.time(), page)
    return page
