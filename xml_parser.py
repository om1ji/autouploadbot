import xml.etree.ElementTree as ET
import re

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

def parse_artist_title(title: str) -> tuple[str, str]:
    title = title.split("—")
    
    artist = title[0].strip()
    track_name = title[1].strip()
    track_name = re.sub(r"\s*\[.*?\]", "", track_name)
    
    return artist, track_name


def parse_xml(xml: str) -> dict:
    parsed_body: ET.Element = ET.fromstring(xml)

    entry = parsed_body.find("atom:entry", NS)

    title = entry.find("atom:title", NS).text
    video_link = entry.find("atom:link", NS).attrib["href"]
    
    artist, track_name = parse_artist_title(title)

    return {"video_link": video_link, 
            "artist": artist,
            "track_name": track_name}