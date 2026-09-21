"""Обложка трека: квадрат из лучшего превью YouTube, встроенный в mp3.

Лучшее превью ролика — 1280×720, а обложка у музыкальных лейблов квадратная
и стоит по центру с полями по бокам. Центральный квадрат даёт её целиком,
720×720; у обычных клипов обрезаются края кадра, как у любой обложки альбома.

Telegram принимает в `thumbnail` только JPEG до 320×320 и до 200 КБ, поэтому
обложка идёт двумя путями: полноразмерная — в ID3-тег mp3 (её видит плеер и
тот, кто скачал файл), уменьшенная — в `thumbnail` для превью в чате.
"""

import subprocess
from pathlib import Path

THUMB_SIZE = 320
THUMB_LIMIT = 200 * 1024


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True
    )


def square_cover(source: Path) -> Path:
    cover = source.with_name(f"{source.stem}.cover.jpg")
    ffmpeg(
        "-i",
        str(source),
        "-vf",
        "crop='min(iw,ih)':'min(iw,ih)'",
        "-q:v",
        "2",
        str(cover),
    )
    return cover


def telegram_thumbnail(cover: Path) -> Path:
    thumb = cover.with_name(f"{cover.stem}.thumb.jpg")
    # 320×320 обычно весит 30–60 КБ; качество снижаем, только если не влезли
    for quality in ("3", "6", "10"):
        ffmpeg(
            "-i",
            str(cover),
            "-vf",
            f"scale={THUMB_SIZE}:{THUMB_SIZE}",
            "-q:v",
            quality,
            str(thumb),
        )
        if thumb.stat().st_size < THUMB_LIMIT:
            break
    return thumb


def embed(audio: Path, cover: Path, artist: str | None, title: str | None) -> None:
    """Встраивает обложку и теги в mp3; аудиодорожка копируется без перекодирования."""
    tagged = audio.with_name(f"{audio.stem}.tagged.mp3")
    tags = []
    if artist:
        tags += ["-metadata", f"artist={artist}"]
    if title:
        tags += ["-metadata", f"title={title}"]
    ffmpeg(
        "-i",
        str(audio),
        "-i",
        str(cover),
        "-map",
        "0:a",
        "-map",
        "1:v",
        "-c",
        "copy",
        "-id3v2_version",
        "3",
        *tags,
        "-metadata:s:v",
        "title=Album cover",
        "-metadata:s:v",
        "comment=Cover (front)",
        "-disposition:v",
        "attached_pic",
        str(tagged),
    )
    tagged.replace(audio)
