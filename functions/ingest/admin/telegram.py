"""Тонкая обёртка над Bot API: без зависимостей, только urllib."""
import json
import os
import urllib.error
import urllib.request

import boto3

_token: str | None = None
_bot_id: int | None = None


def token() -> str:
    global _token
    if _token is None:
        ssm = boto3.client("ssm")
        _token = ssm.get_parameter(Name=os.environ["BOT_TOKEN_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return _token


def call(method: str, **params) -> dict:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token()}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(), strict=False)
    except urllib.error.HTTPError as error:
        # ответ с ошибкой тоже JSON: {"ok": false, "description": …}
        return json.loads(error.read(), strict=False)


def bot_id() -> int:
    global _bot_id
    if _bot_id is None:
        _bot_id = call("getMe")["result"]["id"]
    return _bot_id


def keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """[[(текст, callback_data), …], …] → inline_keyboard."""
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in rows]}


def send(chat_id, text: str, rows=None, **extra) -> dict:
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **extra}
    if rows is not None:
        params["reply_markup"] = keyboard(rows)
    return call("sendMessage", **params)


def edit(chat_id, message_id: int, text: str, rows=None) -> dict:
    params = {"chat_id": chat_id, "message_id": message_id, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": True}
    if rows is not None:
        params["reply_markup"] = keyboard(rows)
    result = call("editMessageText", **params)
    # «message is not modified» — нажали «Обновить», а ничего не поменялось: не ошибка
    if not result.get("ok") and "not modified" not in str(result.get("description")):
        raise RuntimeError(f"editMessageText: {result.get('description')}")
    return result


def chat_title(chat_id) -> str:
    info = call("getChat", chat_id=chat_id)
    if not info.get("ok"):
        return str(chat_id)
    return info["result"].get("title") or info["result"].get("username") or str(chat_id)


def can_post(chat_id) -> tuple[bool, str]:
    """Может ли бот публиковать в чате; второе значение — причина для человека."""
    info = call("getChat", chat_id=chat_id)
    if not info.get("ok"):
        hint = " — возможно, в ID потерян минус" if "not found" in str(info.get("description")) else ""
        return False, f"Telegram не находит чат {chat_id}{hint}"
    member = call("getChatMember", chat_id=chat_id, user_id=bot_id()).get("result", {})
    status = member.get("status")
    if status == "creator" or (status == "administrator" and member.get("can_post_messages", True)):
        return True, ""
    return False, "бот не администратор с правом публикации — добавьте его в админы канала"
