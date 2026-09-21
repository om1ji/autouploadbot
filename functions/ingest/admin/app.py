"""Бот-пульт: вебхук Telegram → экраны и действия в служебном чате.

Доступ — у всех участников служебного чата и только в нём: апдейты из других
чатов и из лички бот игнорирует. Подлинность запроса проверяется заголовком
X-Telegram-Bot-Api-Secret-Token, который Telegram шлёт с каждым апдейтом.

Диалог добавления канала ведётся в одном сообщении: бот редактирует его на
каждом шаге. Шаг и собранные данные хранятся в BotStateTable, потому что
Lambda между сообщениями ничего не помнит.
"""

import base64
import json
import logging
import os
import re
import time
from html import escape

import boto3
from boto3.dynamodb.conditions import Attr

from admin import ops, screens, youtube
from admin import telegram as tg

logging.getLogger().setLevel(logging.INFO)

ADMIN_CHAT = int(os.environ["ADMIN_CHAT_ID"])
STATE = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
FLOW_TTL = 60 * 60
CHAT_REF = re.compile(r"^(@\w{5,}|-?\d{6,})$")

_secrets: dict[str, str] = {}


def secret(env_name: str) -> str:
    if env_name not in _secrets:
        ssm = boto3.client("ssm")
        _secrets[env_name] = ssm.get_parameter(
            Name=os.environ[env_name], WithDecryption=True
        )["Parameter"]["Value"]
    return _secrets[env_name]


# ---------- состояние ----------


def get_flow() -> dict | None:
    item = STATE.get_item(Key={"key": "flow"}).get("Item")
    # TTL в DynamoDB удаляет с опозданием до двух суток — проверяем срок сами
    if not item or int(item["expires_at"]) < time.time():
        return None
    return json.loads(item["data"])


def set_flow(flow: dict) -> None:
    STATE.put_item(
        Item={
            "key": "flow",
            "data": json.dumps(flow),
            "expires_at": int(time.time()) + FLOW_TTL,
        }
    )


def clear_flow() -> None:
    STATE.delete_item(Key={"key": "flow"})


def known_chats(exclude=()) -> list[tuple[str, str]]:
    """Каналы, где бот админ: запоминаются по событиям my_chat_member."""
    items = STATE.scan(FilterExpression=Attr("key").begins_with("known#"))["Items"]
    excluded = {str(c) for c in exclude}
    return [
        (item["key"][len("known#") :], item["title"])
        for item in items
        if item.get("status") == "administrator"
        and item.get("type") == "channel"
        and item["key"][len("known#") :] not in excluded
    ]


# ---------- экраны с данными ----------


def menu_screen():
    channels = ops.list_channels()
    chats = {str(c) for ch in channels for c in ch.get("chats", [])}
    return screens.menu(len(channels), len(chats))


def status_screen():
    return screens.status(
        ops.list_channels(),
        ops.queue_depth(ops.QUEUE_URL),
        ops.queue_depth(ops.DLQ_URL),
        ops.alarm_states(),
        ops.last_probe(),
        ops.uploads_since(24 * 3600),
    )


def titles_of(channel) -> dict:
    return {str(chat): tg.chat_title(chat) for chat in channel.get("chats", [])}


def card_screen(youtube_id: str):
    channel = ops.get_channel(youtube_id)
    if not channel:
        return screens.channel_list(ops.list_channels())
    return screens.channel_card(channel, titles_of(channel))


# ---------- маршрутизация ----------


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-telegram-bot-api-secret-token") != secret(
        "TELEGRAM_SECRET_PARAM"
    ):
        return {"statusCode": 403, "body": ""}

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body)
    update = json.loads(body)

    try:
        if "my_chat_member" in update:
            on_membership(update["my_chat_member"])
        elif "callback_query" in update:
            on_callback(update["callback_query"])
        elif "message" in update:
            on_message(update["message"])
    except Exception as error:
        logging.exception("Update %s failed", update.get("update_id"))
        tg.send(ADMIN_CHAT, f"⚠️ Не получилось: <code>{escape(str(error))[:300]}</code>")

    # всегда 200: на ошибку Telegram повторял бы тот же апдейт снова и снова
    return {"statusCode": 200, "body": ""}


def on_membership(update: dict) -> None:
    chat = update["chat"]
    if chat["id"] == ADMIN_CHAT:
        return
    status = update["new_chat_member"]["status"]
    key = f"known#{chat['id']}"
    if status in ("left", "kicked"):
        STATE.delete_item(Key={"key": key})
        return
    title = chat.get("title") or chat.get("username") or str(chat["id"])
    STATE.put_item(
        Item={"key": key, "title": title, "type": chat["type"], "status": status}
    )
    if (
        status == "administrator"
        and chat["type"] == "channel"
        and update["old_chat_member"]["status"] != "administrator"
    ):
        tg.send(ADMIN_CHAT, *screens.bot_became_admin(title, chat["id"]))


def on_callback(query: dict) -> None:
    message = query.get("message") or {}
    if message.get("chat", {}).get("id") != ADMIN_CHAT:
        tg.call(
            "answerCallbackQuery", callback_query_id=query["id"], text="Нет доступа"
        )
        return

    data, message_id = query.get("data", ""), message["message_id"]
    command, _, arg = data.partition(":")
    toast = ""

    def show(screen):
        tg.edit(ADMIN_CHAT, message_id, *screen)

    if command == "menu":
        show(menu_screen())
    elif command == "status":
        show(status_screen())
    elif command == "newstatus":
        # кнопка под алярмом: статус новым сообщением, сам алярм остаётся в истории
        tg.send(ADMIN_CHAT, *status_screen())
    elif command == "subs":
        channels = ops.list_channels()
        states = ops.subscription_states(
            [c["youtube_id"] for c in channels], secret("HUB_SECRET_PARAM")
        )
        show(screens.subscriptions(channels, states))
    elif command == "chs":
        show(screens.channel_list(ops.list_channels()))
    elif command == "ch":
        show(card_screen(arg))
    elif command == "mr":
        channel = ops.get_channel(arg)
        show(screens.remove_mirror(channel, titles_of(channel)))
    elif command == "mrc":
        youtube_id, _, chat = arg.partition(":")
        channel = ops.get_channel(youtube_id)
        if len(channel.get("chats", [])) <= 1:
            toast = "Это единственное зеркало — удалите канал целиком"
        else:
            ops.remove_chat(youtube_id, chat)
            toast = "Зеркало убрано"
        show(card_screen(youtube_id))
    elif command == "bf":
        channel = ops.get_channel(arg)
        queued = ops.backfill(arg, channel.get("chats", []))
        toast = (
            f"В очереди: {queued}. Придут с интервалом в пару минут"
            if queued
            else "Последние треки уже во всех зеркалах"
        )
    elif command == "del":
        show(screens.confirm_delete(ops.get_channel(arg)))
    elif command == "delok":
        ops.delete_channel(arg)
        toast = "Канал удалён"
        show(screens.channel_list(ops.list_channels()))
    elif command == "add":
        set_flow({"step": "youtube", "mode": "add", "message_id": message_id})
        show(screens.ask_youtube())
    elif command == "bind":
        # бота сделали админом в канале: YouTube спросим, чат уже известен
        set_flow(
            {
                "step": "youtube",
                "mode": "add",
                "message_id": message_id,
                "chat": arg,
                "chat_title": tg.chat_title(arg),
            }
        )
        show(screens.ask_youtube())
    elif command == "mx":
        channel = ops.get_channel(arg)
        set_flow(
            {
                "step": "chat",
                "mode": "mirror",
                "message_id": message_id,
                "youtube_id": arg,
                "name": channel["name"],
            }
        )
        show(
            screens.ask_chat(
                channel["name"], known_chats(exclude=channel.get("chats", []))
            )
        )
    elif command == "addc":
        flow = get_flow()
        if not flow or flow["step"] != "chat":
            toast = "Этот диалог уже закончился"
        else:
            toast = choose_chat(flow, arg)
    elif command in ("addok", "addbf"):
        toast = finish_add(command == "addbf")
    elif command == "cancel":
        clear_flow()
        show(menu_screen())

    tg.call("answerCallbackQuery", callback_query_id=query["id"], text=toast)


def on_message(message: dict) -> None:
    if message.get("chat", {}).get("id") != ADMIN_CHAT:
        return
    text = (message.get("text") or "").strip()

    if text.startswith(("/start", "/menu")):
        tg.send(ADMIN_CHAT, *menu_screen())
        return
    if text.startswith("/status"):
        tg.send(ADMIN_CHAT, *status_screen())
        return

    flow = get_flow()
    if not flow:
        return  # обычная переписка в группе
    reply = {"reply_parameters": {"message_id": message["message_id"]}}

    if flow["step"] == "youtube":
        try:
            youtube_id, name = youtube.resolve(text)
        except youtube.NotFound as error:
            tg.send(ADMIN_CHAT, f"Не нашёл канал: {escape(str(error))}", **reply)
            return
        flow.update(youtube_id=youtube_id, name=name)
        if flow.get("chat"):
            flow["step"] = "confirm"
            set_flow(flow)
            tg.edit(
                ADMIN_CHAT,
                flow["message_id"],
                *screens.confirm_add(name, flow["chat_title"], False),
            )
        else:
            flow["step"] = "chat"
            set_flow(flow)
            tg.edit(
                ADMIN_CHAT, flow["message_id"], *screens.ask_chat(name, known_chats())
            )

    elif flow["step"] == "chat":
        origin = message.get("forward_origin") or {}
        chat = (origin.get("chat") or message.get("forward_from_chat") or {}).get("id")
        if chat is None and CHAT_REF.match(text):
            chat = text
        if chat is None:
            tg.send(
                ADMIN_CHAT,
                "Перешлите пост из канала-зеркала или пришлите его @username.",
                **reply,
            )
            return
        problem = choose_chat(flow, chat)
        if problem:
            tg.send(ADMIN_CHAT, f"Не подходит: {escape(problem)}", **reply)


def choose_chat(flow: dict, chat) -> str:
    """Проверяет чат и переводит диалог к подтверждению. Возвращает проблему или ""."""
    ok, reason = tg.can_post(chat)
    if not ok:
        return reason
    info = tg.call("getChat", chat_id=chat)["result"]
    # @username превращаем в числовой ID: он не меняется при переименовании
    flow.update(
        step="confirm",
        chat=str(info["id"]),
        chat_title=info.get("title") or info.get("username") or str(info["id"]),
    )
    set_flow(flow)
    tg.edit(
        ADMIN_CHAT,
        flow["message_id"],
        *screens.confirm_add(
            flow["name"], flow["chat_title"], flow["mode"] == "mirror"
        ),
    )
    return ""


def finish_add(with_backfill: bool) -> str:
    flow = get_flow()
    if not flow or flow["step"] != "confirm":
        return "Этот диалог уже закончился"
    is_new = ops.add_chat(flow["youtube_id"], flow["name"], flow["chat"])
    clear_flow()
    if is_new:
        ops.subscribe_now()
    note = "Канал добавлен" if is_new else "Зеркало добавлено"
    if with_backfill:
        channel = ops.get_channel(flow["youtube_id"])
        note += f", в очереди {ops.backfill(flow['youtube_id'], channel['chats'])}"
    tg.edit(ADMIN_CHAT, flow["message_id"], *card_screen(flow["youtube_id"]))
    return note
