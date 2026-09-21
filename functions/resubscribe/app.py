"""Продление подписки PubSubHubbub. Вызывается по расписанию EventBridge.

Хаб Google с сентября 2026 регулярно отвечает 503 «Transient error» примерно
через 20 секунд, но заявку при этом ставит в очередь и верифицирует позже.
Поэтому 5xx и таймаут здесь — не отказ, а «пока неизвестно»: функция их
логирует и завершается успешно, а расписание пробует снова.
"""
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request

import boto3

logging.getLogger().setLevel(logging.INFO)

HUB = "https://pubsubhubbub.appspot.com/subscribe"
DETAILS = "https://pubsubhubbub.appspot.com/subscription-details"

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

CALLBACK_URL = os.environ["CALLBACK_URL"]
# URL фида собираем сами: вариант с /xml/feeds/ выглядит похоже, но это заглушка
# карта «UC…:чаты,UC…:чаты» из channels.yaml; подписке нужны только каналы
CHANNEL_IDS = [pair.partition(":")[0] for pair in os.environ["CHANNEL_MAP"].split(",") if pair]
LEASE_SECONDS = os.environ.get("LEASE_SECONDS", "432000")
HUB_SECRET_PARAM = os.environ["HUB_SECRET_PARAM"]

INTERESTING_FIELDS = (
    "State",
    "Last successful verification",
    "Expiration time",
    "Last verification error",
    "Last delivery error",
)

_secret: str | None = None


def hub_secret() -> str:
    global _secret
    if _secret is None:
        ssm = boto3.client("ssm")
        _secret = ssm.get_parameter(Name=HUB_SECRET_PARAM, WithDecryption=True)["Parameter"]["Value"]
    return _secret


def subscription_details(topic: str) -> dict:
    """Состояние подписки по данным хаба. Сбой здесь не фатален."""
    query = urllib.parse.urlencode({
        "hub.callback": CALLBACK_URL,
        "hub.topic": topic,
        "hub.secret": hub_secret(),
    })

    try:
        with urllib.request.urlopen(f"{DETAILS}?{query}", timeout=15) as response:
            html = response.read().decode(errors="replace")
    except Exception:
        logging.warning("Could not read subscription details", exc_info=True)
        return {}

    # страница — HTML с парами «метка → значение» подряд
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    lines = [l.strip() for l in re.sub(r"<[^>]+>", "\n", text).split("\n") if l.strip()]

    return {
        line: lines[i + 1]
        for i, line in enumerate(lines[:-1])
        if line in INTERESTING_FIELDS
    }


def subscribe(topic: str) -> dict:
    payload = urllib.parse.urlencode({
        "hub.callback": CALLBACK_URL,
        "hub.topic": topic,
        "hub.verify": "async",
        "hub.mode": "subscribe",
        "hub.secret": hub_secret(),
        "hub.lease_seconds": LEASE_SECONDS,
    }).encode()

    request = urllib.request.Request(
        HUB, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            took = time.monotonic() - started
            logging.info("Hub accepted request: %s in %.1fs", response.status, took)
            return {"status": response.status, "accepted": True}

    except urllib.error.HTTPError as error:
        took = time.monotonic() - started
        body = error.read().decode(errors="replace")[:200]

        if error.code >= 500:
            # известная деградация хаба: заявка обычно всё равно встаёт в очередь
            logging.warning("Hub is degraded: %s in %.1fs (%s)", error.code, took, body)
            return {"status": error.code, "accepted": False}

        # 4xx — это уже наша ошибка в параметрах, о ней надо знать
        logging.error("Hub rejected request: %s in %.1fs (%s)", error.code, took, body)
        raise

    except (TimeoutError, urllib.error.URLError) as error:
        took = time.monotonic() - started
        logging.warning("Hub did not answer in %.1fs: %s", took, error)
        return {"status": None, "accepted": False}


def process_channel(channel_id: str, force: bool) -> tuple[dict, bool]:
    """Результат по каналу и признак отказа хаба (4xx)."""
    topic = FEED_URL.format(channel_id)
    details = subscription_details(topic)
    state = details.get("State", "unknown")

    logging.info(
        "Channel %s: state=%s verified=%s expires=%s verify_error=%s",
        channel_id,
        state,
        details.get("Last successful verification", "n/a"),
        details.get("Expiration time", "n/a"),
        details.get("Last verification error", "n/a"),
    )

    if state == "verified" and not force:
        return {"state": state, "skipped": True}, False

    try:
        return {"state_before": state, **subscribe(topic)}, False
    except urllib.error.HTTPError as error:
        return {"state_before": state, "status": error.code, "accepted": False}, True


def handler(event, context):
    # Ежечасный запуск лишь дожимает неподтверждённые подписки; продлением
    # аренды занимается суточный, он приходит с force=true
    force = bool((event or {}).get("force"))

    # секрет читаем до потоков, чтобы они не полезли в SSM наперегонки
    hub_secret()

    # Параллельно: хаб держит каждую заявку ~20 с перед 503, и по очереди
    # четыре канала уже не укладываются в таймаут. Каналы при этом
    # независимы — сбой одного не мешает остальным
    with ThreadPoolExecutor(max_workers=len(CHANNEL_IDS)) as pool:
        outcomes = dict(zip(CHANNEL_IDS, pool.map(lambda c: process_channel(c, force), CHANNEL_IDS)))

    results = {channel: result for channel, (result, _) in outcomes.items()}
    rejected = [channel for channel, (_, failed) in outcomes.items() if failed]

    logging.info("Result: %s", json.dumps(results))

    # 4xx — ошибка в наших параметрах; поднимаем после обхода всех каналов
    if rejected:
        raise RuntimeError(f"Hub rejected subscription for {', '.join(rejected)}")

    return results
