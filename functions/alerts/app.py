"""Пересылает алярмы CloudWatch в служебный чат Telegram.

Алярм → SNS → эта функция → сообщение админу. Приходит и срабатывание, и
возврат в норму, так что по чату видно, когда проблема началась и когда ушла.
"""
import json
import logging
import os
import urllib.error
import urllib.request
from html import escape

import boto3

logging.getLogger().setLevel(logging.INFO)

ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]
BOT_TOKEN_PARAM = os.environ["BOT_TOKEN_PARAM"]

_token: str | None = None


def bot_token() -> str:
    global _token
    if _token is None:
        ssm = boto3.client("ssm")
        _token = ssm.get_parameter(Name=BOT_TOKEN_PARAM, WithDecryption=True)["Parameter"]["Value"]
    return _token


def send(text: str, chat_id: str | int = ADMIN_CHAT_ID) -> None:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token()}/sendMessage",
        data=json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            # кнопку обрабатывает бот-пульт: статус приходит новым сообщением
            "reply_markup": {"inline_keyboard": [[{"text": "📊 Статус", "callback_data": "newstatus"}]]},
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except urllib.error.HTTPError as error:
        # обычная группа при апгрейде до супергруппы получает новый ID; без этого
        # алярмы молча уходили бы в пустоту
        new_id = json.load(error).get("parameters", {}).get("migrate_to_chat_id")
        if not new_id or chat_id != ADMIN_CHAT_ID:
            raise
        logging.error("Admin chat moved to %s: update AdminChatId in samconfig.toml and redeploy", new_id)
        send(text, new_id)


def format_alarm(alarm: dict) -> str | None:
    state = alarm.get("NewStateValue")
    # в описании алярма — что случилось и что делать; первая строка — заголовок
    title, _, advice = (alarm.get("AlarmDescription") or alarm.get("AlarmName", "")).partition("\n")

    if state == "ALARM":
        text = f"🔴 <b>{escape(title)}</b>"
        if advice.strip():
            text += f"\n\n{escape(advice.strip())}"
        text += f"\n\n<i>{escape(alarm.get('NewStateReason', ''))}</i>"
        return text
    if state == "OK":
        was = title[:1].lower() + title[1:]
        return f"✅ <b>Снова в норме.</b> Было: {escape(was)}"
    return None                       # INSUFFICIENT_DATA — не повод будить


def handler(event, context):
    for record in event["Records"]:
        alarm = json.loads(record["Sns"]["Message"])
        text = format_alarm(alarm)
        if text:
            send(text)
            logging.info("Sent %s → %s", alarm.get("AlarmName"), alarm.get("NewStateValue"))
