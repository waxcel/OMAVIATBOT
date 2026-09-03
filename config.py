import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

GROUP = "БП324"

WATERMARK = "Расписание от любимого Баристера <3"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

NOTIFY_TIME = (20, 0)  # часы, минуты по Омскому времени

TIMETABLE_URL_TEMPLATE = "https://www.oat.ru/timetable/timetable/ul_lenina_24/{group}"
CHANGES_URL = "https://www.oat.ru/timetable/Changes/b1"

# URL на changes_data.json (например, raw.githubusercontent).
# Если задан — изменения берутся из него без запуска браузера (лёгкий режим для слабых серверов).
# Если пуст — изменения рендерятся локально через Playwright.
CHANGES_JSON_URL = os.getenv("CHANGES_JSON_URL", "")

# Прокси для доступа к oat.ru (формат http://user:pass@host:port или socks5://host:port).
# Нужен, когда сайт блокирует IP датацентра сервера. Применяется только к запросам на oat.ru.
CHANGES_PROXY = os.getenv("CHANGES_PROXY", "")

TIMEZONE = "Asia/Omsk"

WEEK1_START = (2026, 8, 31)

TIMETABLE_TTL = 6 * 3600
CHANGES_TTL = 10 * 60
WEEK_TTL = 3600
VIEW_TTL = 10 * 60

# Таймаут и ретраи загрузки страницы изменений в браузере
GOTO_TIMEOUT_MS = 90_000
GOTO_RETRIES = 2

# Явно привязывать oat.ru к IPv4 в Chromium (обход зависаний на IPv6)
CHROMIUM_FORCE_IPV4 = True

# Режим звонков (официальный, из колледжа)
# Все дни, кроме четверга
BELL_DEFAULT = {
    1: ("08:00", "09:30"),
    2: ("09:40", "11:00"),
    3: ("11:40", "13:00"),
    4: ("13:40", "15:10"),
    5: ("15:20", "16:40"),
    6: ("17:20", "18:40"),
}
# Четверг нечётной учебной недели (1-й и 3-й четверг месяца — классные часы)
BELL_THU_CLASS = {
    1: ("08:00", "09:20"),
    2: ("09:30", "10:50"),
    3: ("11:50", "13:10"),
    4: ("14:10", "15:20"),
    5: ("15:30", "16:50"),
    6: ("17:30", "18:40"),
}
# Четверг чётной учебной недели (2-й и 4-й четверг месяца — методические часы)
BELL_THU_METHOD = {
    1: ("08:00", "09:30"),
    2: ("09:40", "11:10"),
    3: ("11:40", "13:00"),
    4: ("14:20", "15:40"),
    5: ("15:50", "17:00"),
    6: ("17:30", "18:40"),
}
