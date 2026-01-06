from aiogram import Bot
from aiogram.types import FSInputFile
from pathlib import Path
from os import getenv

bot = Bot(token=getenv("BOT_TOKEN"))

async def send_track(chat_id: str | int, audio_path: Path, metadata: dict, thumbnail_path: Path) -> None:
    file = FSInputFile(audio_path)
    thumbnail = FSInputFile(thumbnail_path)
    
    await bot.send_audio(chat_id, 
                         file, 
                         performer=metadata["artist"], 
                         title=metadata["track_name"], 
                         thumbnail=thumbnail
                         )