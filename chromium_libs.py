import glob
import logging
import os
import subprocess

logger = logging.getLogger("chromium_libs")

_ROOT = os.path.dirname(os.path.abspath(__file__))
LIBS_ROOT = os.path.join(_ROOT, "chromium_libs")
APT_ROOT = os.path.join(_ROOT, "apt_root")

# Верхний уровень: библиотеки, которые нужны chrome-headless-shell (Debian 12).
# Транзитивные зависимости apt скачает сам.
PACKAGES = [
    "libnspr4", "libnss3", "libexpat1", "libfontconfig1", "libfreetype6",
    "libasound2", "libatk-bridge2.0-0", "libatk1.0-0", "libatspi2.0-0",
    "libcairo2", "libcups2", "libdbus-1-3", "libdrm2", "libgbm1",
    "libglib2.0-0", "libpango-1.0-0", "libx11-6", "libxcb1",
    "libxcomposite1", "libxdamage1", "libxext6", "libxfixes3",
    "libxkbcommon0", "libxrandr2", "libjpeg62-turbo", "libwebp7",
]

APT_OPTS = [
    "-o", f"Dir::State::lists={APT_ROOT}/lists/",
    "-o", f"Dir::Cache={APT_ROOT}/cache/",
    "-o", f"Dir::State::status={APT_ROOT}/dpkg/status",
    "-o", f"Dir::State::extended_states={APT_ROOT}/extended_states",
    "-o", f"Dir::Log={APT_ROOT}/log/",
    "-o", "Debug::NoLocking=1",
]


def apply_ld_library_path() -> None:
    """Прописывает скачанные библиотеки в LD_LIBRARY_PATH (наследуется браузером)."""
    if not os.path.isdir(LIBS_ROOT):
        return
    dirs: list[str] = []
    for pattern in ("usr/lib/*-linux-gnu*", "lib/*-linux-gnu*", "usr/lib", "lib"):
        dirs.extend(sorted(glob.glob(os.path.join(LIBS_ROOT, pattern))))
    dirs = [d for d in dirs if os.path.isdir(d)]
    if not dirs:
        return
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    merged = dirs + [d for d in existing.split(":") if d and d not in dirs]
    os.environ["LD_LIBRARY_PATH"] = ":".join(merged)
    logger.info("LD_LIBRARY_PATH дополнен каталогами из %s", LIBS_ROOT)


def _libs_extracted() -> bool:
    return any(glob.glob(os.path.join(LIBS_ROOT, "usr/lib/*-linux-gnu*")))


def _extract_deb(deb_path: str, dest: str) -> None:
    result = subprocess.run(["dpkg-deb", "-x", deb_path, dest], capture_output=True)
    if result.returncode == 0:
        return
    _extract_deb_python(deb_path, dest)


def _extract_deb_python(deb_path: str, dest: str) -> None:
    """Фолбэк-распаковка .deb без dpkg-deb (ar + tar средствами python)."""
    import io
    import tarfile

    with open(deb_path, "rb") as f:
        data = f.read()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError(f"Не похоже на .deb: {deb_path}")
    pos = 8
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        name = header[0:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        content = data[pos + 60:pos + 60 + size]
        pos = pos + 60 + size + (size % 2)
        if name.startswith("data.tar"):
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as tar:
                tar.extractall(dest, filter="data")


def _run_apt(args: list[str]) -> subprocess.CompletedProcess:
    cmd = ["apt-get", *APT_OPTS, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


def ensure_libs() -> bool:
    """Скачивает недостающие системные библиотеки без root (apt в домашнюю папку)."""
    try:
        if _libs_extracted():
            apply_ld_library_path()
            return True

        logger.info("Скачиваю системные библиотеки для Chromium (без root, это пару минут)...")
        os.makedirs(f"{APT_ROOT}/lists/partial", exist_ok=True)
        os.makedirs(f"{APT_ROOT}/cache/archives/partial", exist_ok=True)
        os.makedirs(f"{APT_ROOT}/dpkg", exist_ok=True)
        os.makedirs(f"{APT_ROOT}/log", exist_ok=True)

        status_src = "/var/lib/dpkg/status"
        status_dst = f"{APT_ROOT}/dpkg/status"
        if not os.path.exists(status_dst):
            try:
                with open(status_src, "rb") as src, open(status_dst, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                with open(status_dst, "wb") as dst:
                    dst.write(b"")

        update = _run_apt(["update"])
        if update.returncode != 0:
            logger.error("apt-get update не удался: %s", (update.stderr or update.stdout)[-2000:])
            return False

        packages = list(PACKAGES)
        download = None
        for _ in range(4):
            download = _run_apt(["install", "--download-only", "-y", *packages])
            if download.returncode == 0:
                break
            output = (download.stderr or "") + (download.stdout or "")
            missing = [p for p in packages if f"Unable to locate package {p}" in output]
            if not missing:
                logger.error("apt-get install --download-only не удался: %s", output[-2000:])
                return False
            logger.warning("В репозитории нет пакетов: %s — пробую без них", ", ".join(missing))
            packages = [p for p in packages if p not in missing]
        else:
            return False

        if download is None or download.returncode != 0:
            return False

        debs = glob.glob(f"{APT_ROOT}/cache/archives/*.deb")
        if not debs:
            logger.error("apt не скачал ни одного .deb")
            return False
        for deb in debs:
            try:
                _extract_deb(deb, LIBS_ROOT)
            except Exception:
                logger.exception("Не удалось распаковать %s", deb)
                return False

        apply_ld_library_path()
        logger.info("Библиотеки браузера установлены в %s", LIBS_ROOT)
        return True
    except Exception:
        logger.exception("Ошибка при скачивании системных библиотек для Chromium")
        return False
