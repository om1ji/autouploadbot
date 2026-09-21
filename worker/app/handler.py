import asyncio
import json
import logging
import os

import boto3

from app import dedup
from app.downloader import download
from app.telegram import send_track

logging.getLogger().setLevel(logging.INFO)

TARGET_CHAT_ID = os.environ["TARGET_CHAT_ID"]
BOT_TOKEN_PARAM = os.environ["BOT_TOKEN_PARAM"]

_bot_token: str | None = None


def bot_token() -> str:
    global _bot_token
    if _bot_token is None:
        ssm = boto3.client("ssm")
        _bot_token = ssm.get_parameter(Name=BOT_TOKEN_PARAM, WithDecryption=True)["Parameter"]["Value"]
    return _bot_token


def handler(event, context):
    for record in event["Records"]:
        process(json.loads(record["body"]))


def process(data: dict) -> None:
    video_id = data["video_id"]

    if not dedup.claim(video_id):
        logging.info("Skipping %s: already uploaded", video_id)
        return

    audio = thumbnail = None
    try:
        audio, thumbnail = download(data["video_link"])
        asyncio.run(send_track(bot_token(), TARGET_CHAT_ID, audio, data, thumbnail))
        logging.info("Uploaded %s", video_id)
    except Exception:
        # снимаем заявку, иначе повтор из SQS отсечётся дедупликацией
        dedup.release(video_id)
        raise
    finally:
        for path in (audio, thumbnail):
            if path:
                path.unlink(missing_ok=True)
