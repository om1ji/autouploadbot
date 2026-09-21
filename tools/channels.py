#!/usr/bin/env python3
"""The channel map: YouTube channel → Telegram chats, stored in ChannelsTable.

    python3 tools/channels.py list                     # what the bot watches now
    python3 tools/channels.py import [channels.yaml]   # validate, check, upsert
    python3 tools/channels.py export [channels.yaml]   # back up the table as YAML

Day to day the map is edited from the admin chat; these commands are for the
first import, backups and bulk changes. Import checks every YouTube ID against
its feed and that the bot is an admin allowed to post in every chat — the usual
reason for `chat not found` is a chat ID without its leading minus. It adds and
updates entries and never removes any. The functions pick changes up on their
next run, no deploy needed.

Import needs PyYAML:  pip install pyyaml   (or: uv run --with pyyaml tools/channels.py)
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack  # noqa: E402

CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


def fail(message: str) -> None:
    sys.exit(f"ERROR: {message}")


def load_yaml(path: Path) -> tuple[dict[str, list], dict[str, str]]:
    try:
        import yaml
    except ImportError:
        fail(
            "PyYAML is required: pip install pyyaml  (or: uv run --with pyyaml tools/channels.py)"
        )
    if not path.exists():
        fail(
            f"{path.name} not found. Start from the template: cp channels.yaml.example channels.yaml"
        )

    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list):
        fail(f"{path.name} must be a list of entries")

    channel_map: dict[str, list] = {}
    names: dict[str, str] = {}
    errors: list[str] = []

    for number, entry in enumerate(entries, 1):
        where = f"entry {number}"
        if not isinstance(entry, dict):
            errors.append(f"{where}: expected youtube/telegram keys")
            continue
        youtube = str(entry.get("youtube", "")).strip()
        where = f"entry {number} ({entry.get('name') or youtube})"

        if not CHANNEL_ID.match(youtube):
            errors.append(
                f"{where}: youtube must be 'UC' + 22 characters, got {youtube!r}"
            )
            continue
        if youtube in channel_map:
            errors.append(
                f"{where}: {youtube} is listed twice — merge its chats into one entry"
            )
            continue

        raw = entry.get("telegram")
        chats = raw if isinstance(raw, list) else [raw]
        clean = []
        for chat in chats:
            if isinstance(chat, int):
                if chat > 0:
                    errors.append(
                        f"{where}: chat {chat} is positive — channels and groups are negative"
                    )
                clean.append(chat)
            elif isinstance(chat, str) and re.fullmatch(r"@\w{5,}", chat.strip()):
                clean.append(chat.strip())
            else:
                errors.append(
                    f"{where}: telegram must be a chat ID or @username, got {chat!r}"
                )
        if not clean:
            errors.append(f"{where}: no telegram chats")
            continue

        channel_map[youtube] = list(dict.fromkeys(clean))
        names[youtube] = str(entry.get("name") or youtube)

    if errors:
        fail("channels.yaml:\n  " + "\n  ".join(errors))
    if not channel_map:
        fail(f"{path.name} lists no channels")
    return channel_map, names


def check_youtube(channel_map, names) -> list[str]:
    warnings = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for channel in channel_map:
        try:
            with urllib.request.urlopen(FEED.format(channel), timeout=15) as response:
                author = ET.fromstring(response.read()).find("a:author/a:name", ns)
        except urllib.error.HTTPError as error:
            fail(
                f"YouTube channel {channel} ({names[channel]}): feed returned {error.code} — wrong ID?"
            )
        # YouTube иногда отдаёт имя канала с пробелом на конце
        actual = (author.text or "").strip() if author is not None else "?"
        mark = "ok  " if names[channel] in (channel, actual) else "warn"
        if mark == "warn":
            warnings.append(
                f"{channel}: name {names[channel]!r}, YouTube says {actual!r}"
            )
        print(f"  {mark} {channel}  {actual}")
    return warnings


def telegram(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}?" + urllib.parse.urlencode(
        params
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return json.load(error)


def check_telegram(channel_map) -> None:
    param = "/autouploadbot/bot-token"
    token = subprocess.run(
        [
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            param,
            "--with-decryption",
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not token:
        fail(f"could not read the bot token from SSM {param}")

    me = telegram(token, "getMe")
    if not me.get("ok"):
        fail(f"Telegram rejected the bot token: {me.get('description')}")
    bot_id = me["result"]["id"]

    problems = []
    for chat in sorted({c for chats in channel_map.values() for c in chats}, key=str):
        info = telegram(token, "getChat", chat_id=chat)
        if not info.get("ok"):
            hint = (
                " (a chat ID without its leading minus?)"
                if "not found" in str(info.get("description"))
                else ""
            )
            problems.append(f"{chat}: {info.get('description')}{hint}")
            continue
        title = info["result"].get("title") or info["result"].get("username") or "?"
        member = telegram(token, "getChatMember", chat_id=chat, user_id=bot_id).get(
            "result", {}
        )
        status = member.get("status")
        can_post = status == "creator" or (
            status == "administrator" and member.get("can_post_messages", True)
        )
        print(f"  {'ok  ' if can_post else 'FAIL'} {chat}  «{title}»  bot is {status}")
        if not can_post:
            problems.append(
                f"{chat} «{title}»: the bot is {status}, it must be an admin allowed to post"
            )

    if problems:
        fail("Telegram:\n  " + "\n  ".join(problems))


def to_item(youtube: str, name: str, chats: list) -> str:
    return json.dumps({
        "youtube_id": {"S": youtube},
        "name": {"S": name},
        "chats": {"L": [{"S": str(chat)} for chat in chats]},
        "added_at": {"N": str(int(time.time()))},
    })


def cmd_list() -> None:
    channels = stack.channels_from_table()
    for youtube, entry in sorted(channels.items(), key=lambda kv: kv[1]["name"].lower()):
        print(f"{entry['name']:24} {youtube}  → {', '.join(map(str, entry['chats']))}")
    print(f"{len(channels)} channel(s)")


def cmd_import(path: Path, skip_checks: bool) -> None:
    channel_map, names = load_yaml(path)
    if not skip_checks:
        print("YouTube:")
        warnings = check_youtube(channel_map, names)
        print("Telegram:")
        check_telegram(channel_map)
        for warning in warnings:
            print(f"warning: {warning}")
    table = stack.resource("ChannelsTable")
    for youtube, chats in channel_map.items():
        stack.aws("dynamodb", "put-item", "--table-name", table, "--item", to_item(youtube, names[youtube], chats))
    print(f"Imported {len(channel_map)} channel(s). Functions pick them up on their next run.")


def cmd_export(path: Path) -> None:
    lines = ["# Exported from ChannelsTable. Re-import with: python3 tools/channels.py import", ""]
    for youtube, entry in sorted(stack.channels_from_table().items(), key=lambda kv: kv[1]["name"].lower()):
        lines += [f"- name: {entry['name']}", f"  youtube: {youtube}"]
        if len(entry["chats"]) == 1:
            lines.append(f"  telegram: {json.dumps(entry['chats'][0])}")
        else:
            lines.append("  telegram:")
            lines += [f"    - {json.dumps(chat)}" for chat in entry["chats"]]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or args[0] not in ("list", "import", "export"):
        sys.exit(__doc__)
    path = Path(args[1]) if len(args) > 1 else stack.ROOT / "channels.yaml"
    if args[0] == "list":
        cmd_list()
    elif args[0] == "import":
        cmd_import(path, "--no-check" in sys.argv)
    else:
        cmd_export(path)


if __name__ == "__main__":
    main()
