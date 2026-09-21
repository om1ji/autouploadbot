import asyncio
import json
import logging
import os

import boto3

from app import dedup
from app.downloader import download
from app.telegram import send_track

logging.getLogger().setLevel(logging.INFO)

def parse_channel_map(raw: str) -> dict[str, list]:
    """«UCaaa:-100x|-100y,UCbbb:@name» → {"UCaaa": [-100x, -100y], "UCbbb": ["@name"]}.

    Формат без кавычек: SAM искажает кавычки в значениях параметров, а в ID
    каналов и чатов разделителей `,` `:` `|` не бывает.
    """
    channel_map = {}
    for pair in filter(None, raw.split(",")):
        channel, _, chats = pair.partition(":")
        channel_map[channel] = [int(c) if c.lstrip("-").isdigit() else c for c in chats.split("|")]
    return channel_map


# {канал YouTube: [чаты Telegram]} из channels.yaml
CHANNEL_MAP = parse_channel_map(os.environ["CHANNEL_MAP"])
BOT_TOKEN_PARAM = os.environ["BOT_TOKEN_PARAM"]

_bot_token: str | None = None


def bot_token() -> str:
    global _bot_token
    if _bot_token is None:
        ssm = boto3.client("ssm")
        _bot_token = ssm.get_parameter(Name=BOT_TOKEN_PARAM, WithDecryption=True)[
            "Parameter"
        ]["Value"]
    return _bot_token


def handler(event, context):
    for record in event["Records"]:
        process(json.loads(record["body"]))


def claim_key(video_id: str, chat) -> str:
    # отдельная заявка на каждый чат: сбой в одном не повторяет отправку в другие
    return f"{video_id}#{chat}"


def process(data: dict) -> None:
    video_id = data["video_id"]
    channel_id = data.get("channel_id")

    chats = CHANNEL_MAP.get(channel_id)
    if not chats:
        # без маршрута повтор ничего не изменит — не гоняем сообщение по ретраям
        logging.warning(
            "Skipping %s: channel %s is not in the channel map", video_id, channel_id
        )
        return

    # до маршрутизации по чатам заявка ставилась на сам ролик: такой уже отправлен
    if dedup.exists(video_id):
        logging.info("Skipping %s: already uploaded before per-chat routing", video_id)
        return

    pending = [chat for chat in chats if dedup.claim(claim_key(video_id, chat))]
    if not pending:
        logging.info("Skipping %s: already uploaded to every chat", video_id)
        return

    audio = thumbnail = None
    failed = []
    try:
        try:
            audio, thumbnail = download(data["video_link"])
        except Exception:
            for chat in pending:
                dedup.release(claim_key(video_id, chat))
            raise

        # качаем один раз, отправляем в каждый чат
        for chat in pending:
            try:
                asyncio.run(send_track(bot_token(), chat, audio, data, thumbnail))
                logging.info("Uploaded %s to %s", video_id, chat)
            except Exception:
                logging.exception("Sending %s to %s failed", video_id, chat)
                dedup.release(claim_key(video_id, chat))
                failed.append(chat)
    finally:
        for path in (audio, thumbnail):
            if path:
                path.unlink(missing_ok=True)

    if failed:
        # SQS повторит сообщение, но доставленные чаты заявка уже отсечёт
        raise RuntimeError(f"Sending {video_id} failed for chats {failed}")
