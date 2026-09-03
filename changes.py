import asyncio
import json
import logging
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

import chromium_libs
import http_client
import mirror
from config import CHANGES_JSON_URL, CHANGES_PROXY, CHANGES_TTL, CHANGES_URL, CHROMIUM_FORCE_IPV4, GOTO_RETRIES, GOTO_TIMEOUT_MS, GROUP
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


CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

_ipv4_cache: dict[str, str | None] = {}


def _resolve_ipv4(host: str) -> str | None:
    if host in _ipv4_cache:
        return _ipv4_cache[host]
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET)
        ip = infos[0][4][0] if infos else None
    except OSError:
        ip = None
    _ipv4_cache[host] = ip
    return ip


def _playwright_proxy() -> dict | None:
    if not CHANGES_PROXY:
        return None
    parsed = urlparse(CHANGES_PROXY)
    server = f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
    proxy: dict = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def _launch_args() -> list[str]:
    args = list(CHROMIUM_ARGS)
    if CHROMIUM_FORCE_IPV4:
        rules = []
        for host in ("www.oat.ru", "oat.ru"):
            ip = _resolve_ipv4(host)
            if ip:
                rules.append(f"MAP {host} {ip}")
        if rules:
            args.append("--host-resolver-rules=" + ",".join(rules))
    return args


def probe_site(timeout: tuple[float, float] = (10, 25)) -> tuple[bool, str]:
    """Проверяет доступность oat.ru с этой машины (для диагностики на сервере)."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    try:
        infos = socket.getaddrinfo("www.oat.ru", 443)
        ips = sorted({i[4][0] for i in infos})
    except OSError as e:
        return False, f"DNS не разрешился: {e}"
    try:
        started = time.monotonic()
        resp = http_client.get("https://www.oat.ru/timetable/Changes/b1", headers=headers, timeout=timeout)
        elapsed = time.monotonic() - started
        ok = resp.status_code == 200
        return ok, f"DNS={ips}, HTTP {resp.status_code} за {elapsed:.1f}с"
    except requests.RequestException as e:
        detail = str(e).split("(")[0][-160:]
        hint = ""
        if not CHANGES_PROXY:
            hint = " | Подсказка: если сайт блокирует IP датацентра — задай CHANGES_PROXY (прокси с российским IP)"
        return False, f"DNS={ips}, запрос не прошёл: {detail}{hint}"


def _try_launch() -> bool:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_launch_args(), proxy=_playwright_proxy())
        browser.close()
    return True


def ensure_browser_installed() -> bool:
    """Готовит Chromium к работе: скачивает браузер и системные библиотеки при необходимости."""
    chromium_libs.apply_ld_library_path()
    installed = False
    for _ in range(3):
        try:
            return _try_launch()
        except Exception as e:
            message = str(e)
            if "Executable doesn't exist" in message or "playwright was just installed" in message.lower():
                if installed:
                    logger.error("Chromium скачан, но не запускается: %s", message[-500:])
                    return False
                logger.info("Chromium не найден — скачиваю (один раз, может занять пару минут)...")
                try:
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        check=True,
                    )
                    installed = True
                    continue
                except Exception:
                    logger.exception("Не удалось скачать Chromium командой playwright install")
                    return False
            if "shared libraries" in message or "libnspr4" in message or "libnss3" in message:
                logger.info("В контейнере нет системных библиотек браузера — скачиваю их без root...")
                if chromium_libs.ensure_libs():
                    continue
                logger.error("Не удалось докачать библиотеки автоматически.")
                return False
            logger.exception("Chromium не запускается по неизвестной причине")
            return False
    return False


async def _render_page(url: str, max_wait: float = 25.0, min_wait: float = 6.0, stability_window: float = 1.5) -> str:
    async with _lock:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=_launch_args(), proxy=_playwright_proxy())
            page = await browser.new_page()
            try:
                last_error: Exception | None = None
                for attempt in range(1, GOTO_RETRIES + 1):
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning("Загрузка страницы не удалась (попытка %d/%d): %s", attempt, GOTO_RETRIES, str(e)[-300:])
                        if attempt < GOTO_RETRIES:
                            await asyncio.sleep(3)
                if last_error is not None:
                    raise last_error

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
    """Берёт изменения из зеркала (changes_data.json, обновляемого GitHub Actions).

    Возвращает None, если зеркало не настроено (тогда используем Playwright).
    Если зеркало настроено, но данных за дату нет — считаем, что изменений нет
    (это безопаснее, чем запускать браузер в памяти маленького сервера).
    """
    if not CHANGES_JSON_URL:
        return None
    data = mirror.load()
    if data is None:
        return _empty_page()
    entry = (data.get("days") or {}).get(day.isoformat()) or data.get(day.isoformat())
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
