import yt_dlp
from telegram import send_track
from pathlib import Path
import asyncio
import os
from threading import Semaphore

YT_DLP_DOWNLOAD_OPTIONS =  {
    'format': 'bestaudio/best',
    'outtmpl': 'media/%(title)s.%(ext)s',
    'overwrites': False,
    'encoding': 'utf-8',
    "writethumbnail": True,
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

TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "10"))

download_sem = Semaphore(MAX_CONCURRENT_DOWNLOADS)

def handle_video(data: dict, loop: asyncio.AbstractEventLoop) -> None:
    with download_sem:
        with yt_dlp.YoutubeDL(YT_DLP_DOWNLOAD_OPTIONS) as ydl:
            info = ydl.extract_info(data["video_link"], download=True)

        filename = Path(ydl.prepare_filename(info)).with_suffix(".mp3")
        thumb_path = Path(ydl.prepare_filename(info)).with_suffix(".jpg")

        asyncio.run_coroutine_threadsafe(
            send_track(
                chat_id=TARGET_CHAT_ID,
                audio_path=filename,
                metadata=data,
                thumbnail_path=thumb_path,
            ),
            loop,
        ).result()
        
        os.remove(filename)
        os.remove(thumb_path)
