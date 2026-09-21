import logging
import os
from pathlib import Path

import boto3
import yt_dlp
from botocore.exceptions import ClientError

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


def download(video_link: str) -> tuple[Path, Path | None]:
    """Качает аудио и обложку. Возвращает пути к mp3 и jpg."""
    Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

    options = dict(YT_DLP_DOWNLOAD_OPTIONS)
    if cookies := cookies_file():
        options["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(video_link, download=True)
        base = Path(ydl.prepare_filename(info))

    audio = base.with_suffix(".mp3")
    thumbnail = base.with_suffix(".jpg")

    if not thumbnail.is_file():
        logging.warning("No thumbnail for %s", video_link)
        thumbnail = None

    return audio, thumbnail
