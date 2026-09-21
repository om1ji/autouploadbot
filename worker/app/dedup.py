"""Защита от повторной отправки.

SQS доставляет минимум один раз, а YouTube шлёт уведомление заново при любой
правке метаданных видео — без этой таблицы один трек уедет в чат дважды.
"""
import os
import time

import boto3
from botocore.exceptions import ClientError

TTL_SECONDS = 60 * 60 * 24 * 30

table = boto3.resource("dynamodb").Table(os.environ["DEDUP_TABLE"])


def claim(video_id: str) -> bool:
    """True, если этот ролик ещё не обрабатывали."""
    try:
        table.put_item(
            Item={"video_id": video_id, "expires_at": int(time.time()) + TTL_SECONDS},
            ConditionExpression="attribute_not_exists(video_id)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def exists(key: str) -> bool:
    return "Item" in table.get_item(Key={"video_id": key})


def release(video_id: str) -> None:
    table.delete_item(Key={"video_id": video_id})
