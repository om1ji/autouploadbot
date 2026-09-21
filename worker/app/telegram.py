from html import escape
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile


def source_url(metadata: dict) -> str:
    """Каноническая ссылка на ролик: без лишних параметров из исходного URL."""
    if video_id := metadata.get("video_id"):
        return f"https://www.youtube.com/watch?v={video_id}"
    return metadata["video_link"]


def caption(metadata: dict) -> str:
    return f'<a href="{escape(source_url(metadata), quote=True)}">Original upload</a>'


async def send_track(
    token: str,
    chat_id: str | int,
    audio_path: Path,
    metadata: dict,
    thumbnail_path: Path | None,
) -> None:
    # Bot создаётся на каждый вызов: его aiohttp-сессия привязана к event loop,
    # а asyncio.run в каждом вызове Lambda поднимает новый
    bot = Bot(token=token)

    try:
        await bot.send_audio(
            chat_id,
            FSInputFile(audio_path),
            performer=metadata["artist"],
            title=metadata["track_name"],
            thumbnail=FSInputFile(thumbnail_path) if thumbnail_path else None,
            caption=caption(metadata),
            parse_mode=ParseMode.HTML,
        )
    finally:
        await bot.session.close()
