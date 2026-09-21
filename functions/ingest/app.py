"""Публичный вебхук PubSubHubbub. Только принимает и кладёт в очередь."""
import base64
import hashlib
import hmac
import json
import logging
import os

import boto3

from xml_parser import parse_xml

logging.getLogger().setLevel(logging.INFO)

QUEUE_URL = os.environ["QUEUE_URL"]
HUB_SECRET_PARAM = os.environ["HUB_SECRET_PARAM"]

sqs = boto3.client("sqs")
_hub_secret: str | None = None


def hub_secret() -> str:
    # читаем один раз на холодный старт и держим в памяти между вызовами
    global _hub_secret
    if _hub_secret is None:
        ssm = boto3.client("ssm")
        _hub_secret = ssm.get_parameter(Name=HUB_SECRET_PARAM, WithDecryption=True)["Parameter"]["Value"]
    return _hub_secret


def raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode()


def signature_valid(event: dict, body: bytes) -> bool:
    """Эндпоинт публичный, поэтому подпись хаба — единственное, что отличает
    настоящее уведомление от подделки."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    received = headers.get("x-hub-signature", "")

    if "=" not in received:
        return False

    algo, _, digest = received.partition("=")
    try:
        expected = hmac.new(hub_secret().encode(), body, getattr(hashlib, algo)).hexdigest()
    except AttributeError:
        return False

    return hmac.compare_digest(expected, digest)


def handler(event, context):
    method = event["requestContext"]["http"]["method"]

    # GET — верификация подписки: хаб ждёт обратно свой challenge
    if method == "GET":
        params = event.get("queryStringParameters") or {}
        return {"statusCode": 200, "body": params.get("hub.challenge", "")}

    body = raw_body(event)

    if not signature_valid(event, body):
        logging.warning("Rejected notification with invalid signature")
        return {"statusCode": 403, "body": ""}

    try:
        data = parse_xml(body)
    except Exception:
        # повтор не поможет: отвечаем 200, чтобы хаб не долбился
        logging.exception("Unparseable notification body")
        return {"statusCode": 200, "body": ""}

    sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(data))
    logging.info("Queued %s", data["video_id"])

    return {"statusCode": 202, "body": ""}
