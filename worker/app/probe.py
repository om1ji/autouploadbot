"""Проба cookies: проходит ли запрос к YouTube с IP дата-центра.

Раз в час по расписанию воркер берёт свежий ролик из фида одного из каналов и
получает его метаданные тем же путём, что при настоящем скачивании — с
cookies и решением сигнатур, — но ничего не качает. Итог уходит метрикой
CookieProbe (1 — прошла, 0 — нет); алярм на ней предупреждает о протухших
cookies раньше, чем на них упадёт первый трек.

Проба не бросает исключений: EventBridge Scheduler повторяет упавший вызов
до 185 раз, и сбой превратился бы в шторм одинаковых проб.
"""

import logging
import urllib.request
import xml.etree.ElementTree as ET

import boto3
import yt_dlp

from app.downloader import YT_DLP_DOWNLOAD_OPTIONS, cookies_file

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {"yt": "http://www.youtube.com/xml/schemas/2015"}
NAMESPACE = "autouploadbot"


def latest_video(channel_id: str) -> str:
    with urllib.request.urlopen(FEED.format(channel_id), timeout=15) as response:
        return ET.fromstring(response.read()).find(".//yt:videoId", NS).text


def report(ok: bool) -> None:
    boto3.client("cloudwatch").put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {"MetricName": "CookieProbe", "Value": 1 if ok else 0, "Unit": "Count"}
        ],
    )


def run(channel_ids: list[str]) -> dict:
    video_id = None
    try:
        video_id = latest_video(channel_ids[0])
        options = {
            **YT_DLP_DOWNLOAD_OPTIONS,
            "skip_download": True,
            "postprocessors": [],
        }
        if cookies := cookies_file():
            options["cookiefile"] = cookies
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
        if not info.get("formats"):
            raise RuntimeError("no downloadable formats")
    except Exception as error:
        logging.error("Cookie probe failed on %s: %s", video_id, error)
        report(False)
        return {"ok": False, "video_id": video_id, "error": str(error)[:300]}

    logging.info("Cookie probe ok on %s", video_id)
    report(True)
    return {"ok": True, "video_id": video_id}
