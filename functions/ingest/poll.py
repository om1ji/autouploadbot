"""Опрос RSS-фидов каналов — страховка к push-уведомлениям хаба.

Раз в 15 минут забирает фид каждого канала и кладёт новые ролики в ту же
очередь, что и вебхук. Дубли не страшны: если ролик уже пришёл через push,
воркер отсечёт его по таблице дедупликации.

Фид всегда содержит 15 последних роликов, поэтому при первом опросе канала
всё, что в нём есть, только помечается виденным — иначе в чат уехал бы весь
бэк-каталог. Так же отрабатывает и канал, добавленный в ChannelIds позже.
"""
import json
import logging
import os
import urllib.request
import xml.etree.ElementTree as ET

import boto3
from botocore.exceptions import ClientError

from xml_parser import NS, parse_artist_title

logging.getLogger().setLevel(logging.INFO)

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

QUEUE_URL = os.environ["QUEUE_URL"]

sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["SEEN_TABLE"])
channels_table = dynamodb.Table(os.environ["CHANNELS_TABLE"])


def channel_ids() -> list[str]:
    """Каналы из ChannelsTable — читаем на каждом запуске, чтобы правки из
    служебного чата действовали без деплоя."""
    items = channels_table.scan(ProjectionExpression="youtube_id")["Items"]
    return [item["youtube_id"] for item in items]


def fetch_entries(channel_id: str) -> list[dict]:
    with urllib.request.urlopen(FEED_URL.format(channel_id), timeout=15) as response:
        root = ET.fromstring(response.read())

    return [
        {
            "video_id": entry.find("yt:videoId", NS).text,
            "title": entry.find("atom:title", NS).text,
            "video_link": entry.find("atom:link", NS).attrib["href"],
        }
        for entry in root.findall("atom:entry", NS)
    ]


def seen_keys(keys: list[str]) -> set[str]:
    """Какие из ключей уже отмечены — одним запросом на весь фид.

    Чтение в on-demand таблице вчетверо дешевле записи, а условная запись
    тарифицируется, даже когда условие не прошло. Поэтому сначала читаем,
    а пишем только то, чего ещё нет.
    """
    response = dynamodb.batch_get_item(RequestItems={
        table.name: {"Keys": [{"item_id": k} for k in keys], "ProjectionExpression": "item_id"},
    })
    # необработанные ключи считаем невиденными: условная запись всё равно
    # не даст отметить их дважды
    return {item["item_id"] for item in response["Responses"].get(table.name, [])}


def mark_seen(key: str) -> bool:
    """True, если отметки ещё не было."""
    try:
        table.put_item(
            Item={"item_id": key},
            ConditionExpression="attribute_not_exists(item_id)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def poll_channel(channel_id: str) -> dict:
    entries = fetch_entries(channel_id)
    channel_key = f"channel#{channel_id}"
    seen = seen_keys([channel_key] + [f"video#{e['video_id']}" for e in entries])
    bootstrap = channel_key not in seen
    queued = []

    for entry in entries:
        key = f"video#{entry['video_id']}"
        if key in seen or not mark_seen(key) or bootstrap:
            continue

        try:
            artist, track_name = parse_artist_title(entry["title"])
        except ValueError:
            # отметка остаётся: повторный разбор того же заголовка ничего не даст
            logging.warning("Skipping %s: unparseable title %r", entry["video_id"], entry["title"])
            continue

        message = {
            "video_link": entry["video_link"],
            "video_id": entry["video_id"],
            "channel_id": channel_id,
            "artist": artist,
            "track_name": track_name,
        }
        try:
            sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))
        except Exception:
            # снимаем отметку, иначе ролик потеряется навсегда
            table.delete_item(Key={"item_id": key})
            raise

        queued.append(entry["video_id"])
        logging.info("Queued %s from %s", entry["video_id"], channel_id)

    # отметку канала ставим последней: упади мы раньше, следующий запуск
    # повторит начальную разметку, а не отправит бэк-каталог
    if bootstrap:
        mark_seen(channel_key)
        logging.info("Bootstrapped %s: %d existing videos marked as seen", channel_id, len(entries))

    return {"entries": len(entries), "bootstrap": bootstrap, "queued": queued}


def handler(event, context):
    results = {}
    failed = []

    # каналы независимы: недоступный фид одного не мешает остальным
    ids = channel_ids()
    for channel_id in ids:
        try:
            results[channel_id] = poll_channel(channel_id)
        except Exception:
            logging.exception("Polling %s failed", channel_id)
            failed.append(channel_id)

    logging.info("Result: %s", json.dumps(results))

    if failed and len(failed) == len(ids):
        raise RuntimeError("All feeds failed")

    return results
