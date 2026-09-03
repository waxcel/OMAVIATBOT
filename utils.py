import re
from html import unescape


def norm(text: str | None) -> str:
    if text is None:
        return ""
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def norm_group(value: str) -> str:
    return re.sub(r"[\s\-–—]+", "", value).upper()
