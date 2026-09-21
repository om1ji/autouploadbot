#!/usr/bin/env python3
"""Validate channels.yaml, check access, build and deploy the stack.

    python3 tools/deploy.py              # validate, check, build, deploy
    python3 tools/deploy.py --dry-run    # validate and check only
    python3 tools/deploy.py --no-check   # skip the YouTube and Telegram checks

channels.yaml is the single source of truth for which YouTube channels are
watched and which Telegram chats mirror each one. It reaches the functions as
the ChannelMap stack parameter — "UCaaa:-100x|-100y,UCbbb:@name", without
quotes because SAM mangles quotes in parameter values — merged with the other
parameter_overrides from samconfig.toml.

Checks before deploying:
  * every YouTube ID has a feed; its author is compared with `name`;
  * the bot is an admin allowed to post in every Telegram chat — the usual
    reason for `chat not found` is a chat ID without its leading minus.

Needs PyYAML:  pip install pyyaml   (or: uv run --with pyyaml tools/deploy.py)
"""

import json
import re
import shlex
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANNELS = ROOT / "channels.yaml"
SAMCONFIG = ROOT / "samconfig.toml"

CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
# parameters that ChannelMap replaced; dropped from samconfig overrides
OBSOLETE = {"TargetChatId", "ChannelIds"}


def fail(message: str) -> None:
    sys.exit(f"ERROR: {message}")


def load_channels() -> tuple[dict[str, list], dict[str, str]]:
    try:
        import yaml
    except ImportError:
        fail(
            "PyYAML is required: pip install pyyaml  (or: uv run --with pyyaml tools/deploy.py)"
        )
    if not CHANNELS.exists():
        fail(
            f"{CHANNELS.name} not found. Start from the template: cp channels.yaml.example channels.yaml"
        )

    entries = yaml.safe_load(CHANNELS.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list):
        fail(f"{CHANNELS.name} must be a list of entries")

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
        fail(f"{CHANNELS.name} lists no channels")
    return channel_map, names


def samconfig_overrides() -> dict[str, str]:
    if not SAMCONFIG.exists():
        fail(
            "samconfig.toml not found. Start from the template: cp samconfig.toml.example samconfig.toml"
        )
    config = tomllib.loads(SAMCONFIG.read_text(encoding="utf-8"))
    raw = (
        config.get("default", {})
        .get("deploy", {})
        .get("parameters", {})
        .get("parameter_overrides", "")
    )
    overrides = {}
    for token in shlex.split(raw):
        key, _, value = token.partition("=")
        if key and key not in OBSOLETE:
            overrides[key] = value
    return overrides


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
        # YouTube иногда отдаёт имя с пробелом на конце («Lasha Mikaia »)
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


def check_telegram(channel_map, overrides) -> None:
    param = overrides.get("BotTokenParam", "/autouploadbot/bot-token")
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


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    skip_checks = "--no-check" in sys.argv

    channel_map, names = load_channels()
    overrides = samconfig_overrides()
    overrides["ChannelMap"] = ",".join(
        f"{channel}:{'|'.join(str(chat) for chat in chats)}"
        for channel, chats in channel_map.items()
    )

    chats = {c for cs in channel_map.values() for c in cs}
    print(f"{len(channel_map)} YouTube channel(s) → {len(chats)} Telegram chat(s)")

    if not skip_checks:
        print("YouTube:")
        warnings = check_youtube(channel_map, names)
        print("Telegram:")
        check_telegram(channel_map, overrides)
        for warning in warnings:
            print(f"warning: {warning}")

    if len(overrides["ChannelMap"]) > 4000:
        fail("the channel map exceeds the 4 KB limit of a stack parameter")

    args = [f"{key}={value}" for key, value in overrides.items()]
    if dry_run:
        print("\nDry run. Would deploy with:\n  " + "\n  ".join(args))
        return

    subprocess.run(["sam", "build"], cwd=ROOT, check=True)
    subprocess.run(
        ["sam", "deploy", "--parameter-overrides", *args], cwd=ROOT, check=True
    )


if __name__ == "__main__":
    main()
