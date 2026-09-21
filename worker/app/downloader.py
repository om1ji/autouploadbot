import logging
import os
from pathlib import Path
from typing import NamedTuple

import boto3
import yt_dlp
from botocore.exceptions import ClientError

from app import artwork

MEDIA_DIR = os.environ.get("MEDIA_DIR", "/tmp/media")

YT_DLP_DOWNLOAD_OPTIONS = {
    'format': 'bestaudio/best',
    # %(id)s уникален: на %(title)s два трека с одинаковым заголовком дрались за файл
    'outtmpl': f'{MEDIA_DIR}/%(id)s.%(ext)s',
    'overwrites': True,
    'encoding': 'utf-8',
    'writethumbnail': True,
    'noprogress': True,
    'cachedir': '/tmp/yt-dlp-cache',
    'postprocessors': [
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '0',
        },
        {
            "key": "FFmpegThumbnailsConvertor",
            "format": "jpg",
        },
    ],
}

# YouTube блокирует yt-dlp с IP дата-центров; cookies снимают проверку.
# Лежат в S3, а не в образе: это учётные данные, и менять их надо без пересборки
COOKIES_BUCKET = os.environ.get("COOKIES_BUCKET")
COOKIES_KEY = os.environ.get("COOKIES_KEY", "cookies.txt")
COOKIES_PATH = Path("/tmp/cookies.txt")

_cookies_loaded = False


def cookies_file() -> str | None:
    """Путь к cookies: локально — YTDLP_COOKIES_FILE, в Lambda — копия из S3."""
    global _cookies_loaded

    local = os.environ.get("YTDLP_COOKIES_FILE")
    if local and Path(local).is_file():
        return local

    if not COOKIES_BUCKET:
        return None

    # раз на холодный старт: дальше yt-dlp сам дописывает в файл обновлённые cookies
    if not _cookies_loaded:
        try:
            boto3.client("s3").download_file(COOKIES_BUCKET, COOKIES_KEY, str(COOKIES_PATH))
        except ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey"):
                logging.warning("No cookies in s3://%s/%s, going without", COOKIES_BUCKET, COOKIES_KEY)
                return None
            raise
        _cookies_loaded = True

    return str(COOKIES_PATH)


class Download(NamedTuple):
    audio: Path
    thumbnail: Path | None
    # секунды; без неё клиенты Telegram у VBR-mp3 порой показывают 0:00
    duration: int | None


def download(video_link: str, artist: str | None = None, title: str | None = None) -> Download:
    """Качает аудио, готовит обложку и встраивает её в mp3 вместе с тегами.

    Длительность берётся из метаданных ролика. `thumbnail` в результате —
    уже превью 320×320 для Telegram.
    """
    Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

    options = dict(YT_DLP_DOWNLOAD_OPTIONS)
    if cookies := cookies_file():
        options["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(video_link, download=True)
        base = Path(ydl.prepare_filename(info))

    audio = base.with_suffix(".mp3")
    source = base.with_suffix(".jpg")
    thumbnail = None

    if not source.is_file():
        logging.warning("No thumbnail for %s", video_link)
    else:
        cover = None
        try:
            cover = artwork.square_cover(source)
            thumbnail = artwork.telegram_thumbnail(cover)
            artwork.embed(audio, cover, artist, title)
        except Exception:
            # обложка не должна мешать доставке: трек уйдёт без неё
            logging.exception("Artwork failed for %s, sending without it", video_link)
            thumbnail = None
        finally:
            for path in (source, cover):
                if path:
                    path.unlink(missing_ok=True)

    duration = info.get("duration")
    return Download(audio, thumbnail, round(duration) if duration else None)
