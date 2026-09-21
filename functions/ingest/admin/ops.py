"""Операции бота-пульта над AWS и YouTube: каналы, статус, подписки, заливка."""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import boto3
from xml_parser import NS, parse_artist_title

dynamodb = boto3.resource("dynamodb")
CHANNELS = dynamodb.Table(os.environ["CHANNELS_TABLE"])
DEDUP = dynamodb.Table(os.environ["DEDUP_TABLE"])
SEEN = dynamodb.Table(os.environ["SEEN_TABLE"])

QUEUE_URL = os.environ["QUEUE_URL"]
DLQ_URL = os.environ["DLQ_URL"]
HUB = "https://pubsubhubbub.appspot.com"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
CLAIM_TTL = 60 * 60 * 24 * 30  # как в worker/app/dedup.py

sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")
lambda_client = boto3.client("lambda")


# ---------- каналы ----------


def list_channels() -> list[dict]:
    items = CHANNELS.scan()["Items"]
    return sorted(items, key=lambda item: str(item.get("name", "")).lower())


def get_channel(youtube_id: str) -> dict | None:
    return CHANNELS.get_item(Key={"youtube_id": youtube_id}).get("Item")


def add_chat(youtube_id: str, name: str, chat) -> bool:
    """Добавляет канал или зеркало к существующему. True — если канал новый."""
    existing = get_channel(youtube_id)
    chats = [str(c) for c in (existing or {}).get("chats", [])]
    if str(chat) not in chats:
        chats.append(str(chat))
    CHANNELS.put_item(
        Item={
            "youtube_id": youtube_id,
            "name": (existing or {}).get("name") or name,
            "chats": chats,
            "added_at": (existing or {}).get("added_at") or int(time.time()),
        }
    )
    return existing is None


def remove_chat(youtube_id: str, chat) -> None:
    channel = get_channel(youtube_id)
    chats = [c for c in channel.get("chats", []) if str(c) != str(chat)]
    CHANNELS.update_item(
        Key={"youtube_id": youtube_id},
        UpdateExpression="SET chats = :c",
        ExpressionAttributeValues={":c": chats},
    )


def delete_channel(youtube_id: str) -> None:
    CHANNELS.delete_item(Key={"youtube_id": youtube_id})
    # без отметки канала повторное добавление снова разметит фид, а не зальёт бэк-каталог
    SEEN.delete_item(Key={"item_id": f"channel#{youtube_id}"})
    hub_request("unsubscribe", youtube_id)


# ---------- подписка на хаб ----------


def subscribe_now() -> None:
    """Переподписка сразу, не дожидаясь ежечасного запуска."""
    lambda_client.invoke(
        FunctionName=os.environ["RESUBSCRIBE_FUNCTION"],
        InvocationType="Event",
        Payload=b'{"force": false}',
    )


def hub_request(mode: str, youtube_id: str) -> None:
    data = urllib.parse.urlencode(
        {
            "hub.callback": os.environ["HUB_CALLBACK_URL"],
            "hub.topic": FEED.format(youtube_id),
            "hub.mode": mode,
            "hub.verify": "async",
        }
    ).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{HUB}/subscribe", data=data), timeout=25
        ).read()
    except Exception:
        pass  # хаб бывает в 503; отписка — не повод ронять удаление


def subscription_states(
    youtube_ids: list[str], hub_secret: str
) -> dict[str, tuple[str, str]]:
    """{канал: (состояние, срок)} со страницы хаба; запросы параллельно."""

    def one(youtube_id):
        query = urllib.parse.urlencode(
            {
                "hub.callback": os.environ["HUB_CALLBACK_URL"],
                "hub.topic": FEED.format(youtube_id),
                "hub.secret": hub_secret,
            }
        )
        try:
            with urllib.request.urlopen(
                f"{HUB}/subscription-details?{query}", timeout=15
            ) as response:
                html = response.read().decode(errors="replace")
        except Exception:
            return youtube_id, ("недоступно", "")
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
        lines = [
            l.strip() for l in re.sub(r"<[^>]+>", "\n", text).split("\n") if l.strip()
        ]
        fields = {
            line: lines[i + 1]
            for i, line in enumerate(lines[:-1])
            if line in ("State", "Expiration time")
        }
        return youtube_id, (fields.get("State", "?"), fields.get("Expiration time", ""))

    with ThreadPoolExecutor(max_workers=max(1, len(youtube_ids))) as pool:
        return dict(pool.map(one, youtube_ids))


# ---------- статус ----------


def queue_depth(url: str) -> int:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return sum(int(v) for v in attrs.values())


def alarm_states() -> list[tuple[str, str]]:
    prefix = f"{os.environ['STACK_NAME']}-"
    alarms = cloudwatch.describe_alarms(AlarmNamePrefix=prefix)["MetricAlarms"]
    return [
        (a["AlarmName"][len(prefix) :].split("-")[0], a["StateValue"]) for a in alarms
    ]


def last_probe() -> tuple[float, bool] | None:
    now = time.time()
    points = cloudwatch.get_metric_statistics(
        Namespace="autouploadbot",
        MetricName="CookieProbe",
        StartTime=now - 3 * 3600,
        EndTime=now,
        Period=300,
        Statistics=["Minimum"],
    )["Datapoints"]
    if not points:
        return None
    point = max(points, key=lambda p: p["Timestamp"])
    return point["Timestamp"].timestamp(), point["Minimum"] >= 1


def uploads_since(seconds: int) -> Counter:
    """{чат: треков} по заявкам дедупликации: время заявки = срок жизни − 30 дней."""
    since = time.time() - seconds
    counts, kwargs = Counter(), {"ProjectionExpression": "video_id, expires_at"}
    while True:
        page = DEDUP.scan(**kwargs)
        for item in page["Items"]:
            video, _, chat = item["video_id"].partition("#")
            if chat and int(item.get("expires_at", 0)) - CLAIM_TTL >= since:
                counts[chat] += 1
        if "LastEvaluatedKey" not in page:
            return counts
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# ---------- заливка последних роликов ----------


def latest_videos(youtube_id: str, count: int) -> list[dict]:
    """Последние `count` роликов с разбираемым заголовком, от старых к новым."""
    with urllib.request.urlopen(FEED.format(youtube_id), timeout=15) as response:
        root = ET.fromstring(response.read())
    videos = []
    for entry in root.findall("atom:entry", NS):
        try:
            artist, track_name = parse_artist_title(entry.find("atom:title", NS).text)
        except ValueError:
            continue
        video_id = entry.find("yt:videoId", NS).text
        videos.append(
            {
                "video_link": f"https://www.youtube.com/watch?v={video_id}",
                "video_id": video_id,
                "channel_id": youtube_id,
                "artist": artist,
                "track_name": track_name,
            }
        )
        if len(videos) == count:
            break
    return list(reversed(videos))


def backfill(youtube_id: str, chats: list, count: int = 3) -> int:
    """Ставит в очередь последние ролики, которых ещё нет хотя бы в одном из чатов.

    Задержка растёт от старых к новым — так внутри чата сохраняется порядок
    YouTube, а Lambda не ждёт, пока очередь разберётся.
    """
    queued = 0
    for video in latest_videos(youtube_id, count):
        missing = [
            c
            for c in chats
            if "Item"
            not in DEDUP.get_item(Key={"video_id": f"{video['video_id']}#{c}"})
        ]
        if not missing:
            continue
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(video),
            DelaySeconds=min(queued * 120, 900),
        )
        queued += 1
    return queued
