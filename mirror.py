import json
import logging
import time

import requests

from config import CHANGES_JSON_URL

logger = logging.getLogger("mirror")

MIRROR_TTL = 5 * 60

_cache = {"fetched_at": 0.0, "data": None}


def load(force: bool = False) -> dict | None:
    """Загружает данные зеркала (changes_data.json из GitHub) или None, если зеркало недоступно."""
    if not CHANGES_JSON_URL:
        return None
    now = time.time()
    if not force and now - _cache["fetched_at"] < MIRROR_TTL:
        return _cache["data"]
    try:
        resp = requests.get(f"{CHANGES_JSON_URL}?t={int(now)}", timeout=20)
        resp.raise_for_status()
        data = json.loads(resp.text)
        if not isinstance(data, dict):
            raise ValueError("JSON зеркала не является объектом")
        _cache["fetched_at"] = now
        _cache["data"] = data
        logger.info("Зеркало загружено (generated_at=%s)", data.get("generated_at", "?"))
        return data
    except Exception as e:
        logger.warning("Зеркало недоступно: %s", str(e)[-200:])
        _cache["fetched_at"] = now
        _cache["data"] = None
        return None
