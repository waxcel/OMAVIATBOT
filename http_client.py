import requests

from config import CHANGES_PROXY


def _proxies_for(url: str) -> dict | None:
    """Прокси применяется только к запросам на oat.ru (он блокирует зарубежные датацентры)."""
    if CHANGES_PROXY and "oat.ru" in url:
        return {"http": CHANGES_PROXY, "https": CHANGES_PROXY}
    return None


def get(url: str, timeout: float = 30, **kwargs) -> requests.Response:
    return requests.get(url, timeout=timeout, proxies=_proxies_for(url), **kwargs)
