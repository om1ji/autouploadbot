import yt_dlp
from telegram import send_track
from pathlib import Path
import asyncio
from pprint import pprint
import os

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


def handle_video(data: dict) -> None:
    with yt_dlp.YoutubeDL(YT_DLP_DOWNLOAD_OPTIONS) as ydl:
        info = ydl.extract_info(data["video_link"], download=True)

    filename = Path(ydl.prepare_filename(info)).with_suffix(".mp3")
    thumb_path = Path(ydl.prepare_filename(info)).with_suffix(".jpg")

    asyncio.run(
        send_track(audio_path=filename, metadata=data, thumbnail_path=thumb_path)
    )
    
    os.remove(filename)
    os.remove(thumb_path)
