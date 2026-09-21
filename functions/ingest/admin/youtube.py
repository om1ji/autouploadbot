"""Канал YouTube по тому, что прислал человек: ID, ссылка или @handle."""

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CHANNEL_ID = re.compile(r"\b(UC[\w-]{22})\b")
HANDLE = re.compile(r"(?:youtube\.com/)?(@[\w.-]{3,})")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
# на странице канала встречаются ID чужих каналов (рекомендации, коллаборации),
# поэтому доверяем только каноничной ссылке и мета-тегу самого канала
OWN_ID = (
    re.compile(
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
    ),
    re.compile(r'<meta itemprop="identifier" content="(UC[\w-]{22})"'),
)


class NotFound(ValueError):
    pass


def channel_name(channel_id: str) -> str:
    """Имя автора из фида; заодно проверка, что канал существует."""
    try:
        with urllib.request.urlopen(FEED.format(channel_id), timeout=15) as response:
            author = ET.fromstring(response.read()).find("a:author/a:name", ATOM)
    except urllib.error.HTTPError as error:
        raise NotFound(
            f"у канала {channel_id} нет фида (HTTP {error.code}) — проверьте ID"
        ) from None
    return (author.text or channel_id).strip() if author is not None else channel_id


def id_from_handle(handle: str) -> str:
    request = urllib.request.Request(
        f"https://www.youtube.com/{handle}",
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            html = response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        raise NotFound(
            f"YouTube не отдал страницу {handle} (HTTP {error.code})"
        ) from None
    for pattern in OWN_ID:
        if found := pattern.search(html):
            return found.group(1)
    # из AWS YouTube иногда отдаёт страницу согласия или проверку вместо канала
    raise NotFound(
        f"не смог прочитать ID канала {handle} со страницы YouTube. "
        "Пришлите ссылку вида youtube.com/channel/UC…"
    )


def resolve(text: str) -> tuple[str, str]:
    """(ID канала, имя) по ID, ссылке /channel/UC… или @handle."""
    text = text.strip()
    if found := CHANNEL_ID.search(text):
        channel_id = found.group(1)
    elif found := HANDLE.search(text):
        channel_id = id_from_handle(found.group(1))
    else:
        raise NotFound(
            "не похоже на канал YouTube. Пришлите ссылку youtube.com/@… или youtube.com/channel/UC…"
        )
    return channel_id, channel_name(channel_id)
