"""Экраны бота-пульта: текст и инлайн-кнопки. Логики тут нет — только отрисовка.

callback_data ограничен 64 байтами, поэтому команды короткие:
  menu, status, subs, chs            — разделы
  ch:<UC>                            — карточка канала
  mx:<UC> / mr:<UC> / mrc:<UC>:<chat> — добавить / выбрать / убрать зеркало
  bf:<UC>                            — залить последние 3
  del:<UC> / delok:<UC>              — удалить канал / подтвердить
  add, addc:<chat>, addok, addbf, cancel — диалог добавления
"""

import datetime
import os
from html import escape

from admin.telegram import chat_title

ALARM_LABELS = {
    "DeadLetterAlarm": "DLQ",
    "QueueStuckAlarm": "очередь",
    "CookieProbeAlarm": "cookies",
}
BACK_TO_MENU = [("« Меню", "menu")]


def when(timestamp: float) -> str:
    # часовой пояс служебного чата — параметр AdminUtcOffset
    offset = datetime.timedelta(hours=float(os.environ.get("ADMIN_UTC_OFFSET", "0")))
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone(offset)).strftime("%d.%m %H:%M")


def menu(channel_count: int, chat_count: int):
    text = f"<b>Mirror bot</b>\n{channel_count} каналов YouTube → {chat_count} чатов Telegram"
    rows = [
        [("📊 Статус", "status"), ("📺 Каналы", "chs")],
        [("➕ Добавить канал", "add")],
    ]
    return text, rows


def status(channels, queue, dlq, alarms, probe, uploads):
    lines = [f"<b>📊 Статус</b> · {when(datetime.datetime.now().timestamp())}", ""]
    lines.append(f"Очередь: {queue} · DLQ: {'🔴 ' if dlq else ''}{dlq}")

    if probe is None:
        lines.append("Cookies: проба ещё не запускалась")
    else:
        at, ok = probe
        lines.append(
            f"Cookies: {'✅ живы' if ok else '🔴 протухли'} · проба {when(at)}"
        )

    firing = [
        ALARM_LABELS.get(name, name) for name, state in alarms if state == "ALARM"
    ]
    lines.append(
        "Алярмы: " + ("🔴 " + ", ".join(firing) if firing else "✅ всё в норме")
    )

    lines += ["", f"<b>За сутки отправлено: {sum(uploads.values())}</b>"]
    titles = {str(c): None for ch in channels for c in ch.get("chats", [])}
    for chat, count in uploads.most_common():
        lines.append(
            f"  {escape(chat_title(chat) if chat in titles else chat)} — {count}"
        )

    rows = [[("🔄 Обновить", "status"), ("🔔 Подписки", "subs")], BACK_TO_MENU]
    return "\n".join(lines), rows


def subscriptions(channels, states):
    marks = {"verified": "✅", "unverified": "⏳"}
    lines = ["<b>🔔 Подписки на хаб YouTube</b>", ""]
    for channel in channels:
        state, expires = states.get(channel["youtube_id"], ("?", ""))
        tail = (
            f" · до {escape(expires[:16])}"
            if state == "verified" and expires
            else f" · {escape(state)}"
        )
        lines.append(f"{marks.get(state, '⚠️')} {escape(channel['name'])}{tail}")
    lines += ["", "Даже без подписки ролики приходят через опрос RSS раз в 15 минут."]
    return "\n".join(lines), [[("« Статус", "status")]]


def channel_list(channels):
    text = f"<b>📺 Каналы</b> · {len(channels)}" if channels else "Каналов пока нет."
    rows = [[(channel["name"], f"ch:{channel['youtube_id']}")] for channel in channels]
    rows += [[("➕ Добавить канал", "add")], BACK_TO_MENU]
    return text, rows


def channel_card(channel, titles: dict):
    youtube_id = channel["youtube_id"]
    lines = [
        f"<b>{escape(channel['name'])}</b>",
        f'<a href="https://www.youtube.com/channel/{youtube_id}">youtube.com/channel/{youtube_id}</a>',
        "",
        "Зеркала:",
    ]
    lines += [
        f"  • {escape(titles.get(str(chat), str(chat)))}"
        for chat in channel.get("chats", [])
    ]
    rows = [
        [("➕ Зеркало", f"mx:{youtube_id}"), ("➖ Зеркало", f"mr:{youtube_id}")],
        [("⏪ Залить последние 3", f"bf:{youtube_id}")],
        [("🗑 Удалить канал", f"del:{youtube_id}")],
        [("« Каналы", "chs")],
    ]
    return "\n".join(lines), rows


def remove_mirror(channel, titles: dict):
    youtube_id = channel["youtube_id"]
    rows = [
        [(f"✖ {titles.get(str(chat), str(chat))}", f"mrc:{youtube_id}:{chat}")]
        for chat in channel["chats"]
    ]
    rows.append([("« Назад", f"ch:{youtube_id}")])
    return f"Какое зеркало убрать у <b>{escape(channel['name'])}</b>?", rows


def confirm_delete(channel):
    youtube_id = channel["youtube_id"]
    text = (
        f"Удалить <b>{escape(channel['name'])}</b>?\n\n"
        "Бот перестанет следить за каналом и отпишется от него. Уже отправленные треки останутся."
    )
    return text, [
        [("🗑 Да, удалить", f"delok:{youtube_id}"), ("Отмена", f"ch:{youtube_id}")]
    ]


def ask_youtube():
    text = (
        "➕ <b>Новый канал</b>\n\nПришлите ссылку на канал YouTube: "
        "youtube.com/@имя, youtube.com/channel/UC… или просто ID."
    )
    return text, [[("Отмена", "cancel")]]


def ask_chat(name: str, known: list[tuple[str, str]]):
    text = (
        f"Канал <b>{escape(name)}</b>.\n\nКуда отправлять его треки? "
        "Перешлите сюда любой пост из канала-зеркала или пришлите его @username."
    )
    if known:
        text += "\n\nИли выберите канал, где бот уже админ:"
    rows = [[(title, f"addc:{chat}")] for chat, title in known]
    rows.append([("Отмена", "cancel")])
    return text, rows


def confirm_add(name: str, chat_title_: str, is_mirror: bool):
    action = "Добавить зеркало" if is_mirror else "Добавить"
    text = f"{action}: <b>{escape(name)}</b> → <b>{escape(chat_title_)}</b>?"
    return text, [
        [("✅ Добавить", "addok")],
        [("✅ Добавить и залить последние 3", "addbf")],
        [("Отмена", "cancel")],
    ]


def bot_became_admin(title: str, chat):
    text = (
        f"Меня сделали админом в <b>{escape(title)}</b>.\n"
        "Привязать этот канал к каналу YouTube?"
    )
    return text, [[("➕ Привязать", f"bind:{chat}")]]
