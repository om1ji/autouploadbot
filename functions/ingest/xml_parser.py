import xml.etree.ElementTree as ET
import re

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# Каналы пишут разделитель по-разному: HATE — длинным тире, losprimerosVIIVI —
# дефисом. Порядок важен: длинное тире проверяем первым, чтобы дефис внутри
# названия трека («Artist — Track - Remix») не разрезал его не в том месте
SEPARATORS = (" — ", " – ", " - ")


def parse_artist_title(title: str) -> tuple[str, str]:
    for separator in SEPARATORS:
        if separator in title:
            artist, track_name = title.split(separator, 1)
            break
    else:
        raise ValueError(f"No artist/track separator in {title!r}")

    artist = artist.strip()
    track_name = re.sub(r"\s*\[.*?\]", "", track_name).strip()

    return artist, track_name

def parse_xml(xml: bytes | str) -> dict:
    parsed_body: ET.Element = ET.fromstring(xml)

    entry = parsed_body.find("atom:entry", NS)

    title = entry.find("atom:title", NS).text
    video_link = entry.find("atom:link", NS).attrib["href"]

    # videoId нужен как ключ дедупликации и как уникальная часть имени файла
    video_id_node = entry.find("yt:videoId", NS)
    if video_id_node is not None and video_id_node.text:
        video_id = video_id_node.text
    else:
        video_id = video_link.rsplit("v=", 1)[-1]

    artist, track_name = parse_artist_title(title)

    return {"video_link": video_link,
            "video_id": video_id,
            "artist": artist,
            "track_name": track_name}
