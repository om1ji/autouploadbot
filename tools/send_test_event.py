#!/usr/bin/env python3
"""Отправляет вебхуку подписанное уведомление, как это сделал бы хаб.

Проходит весь путь кроме самого PubSubHubbub: подпись → разбор XML → SQS →
воркер → yt-dlp → ffmpeg → Telegram.

    python3 tools/send_test_event.py \
        --video https://www.youtube.com/watch?v=VIDEO_ID \
        --title 'Исполнитель — Трек'
"""
import argparse
import hashlib
import hmac
import json
import pathlib
import re
import subprocess
import urllib.request

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>{video_id}</yt:videoId>
    <title>{title}</title>
    <link rel="alternate" href="{url}"/>
  </entry>
</feed>
"""


def default_stack() -> str:
    """Имя стека из samconfig.toml — тот же, что использует sam deploy."""
    config = pathlib.Path(__file__).resolve().parent.parent / "samconfig.toml"
    if config.is_file():
        found = re.search(r'^stack_name\s*=\s*"([^"]+)"', config.read_text(), re.M)
        if found:
            return found.group(1)
    return "autouploadbot"


def run(*command: str) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def stack_output(stack: str, key: str) -> str:
    return run(
        "aws", "cloudformation", "describe-stacks", "--stack-name", stack,
        "--query", f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue",
        "--output", "text",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", default=default_stack(), help="по умолчанию — stack_name из samconfig.toml")
    parser.add_argument("--video", required=True, help="ссылка на ролик YouTube")
    parser.add_argument("--title", default="Test Artist — Test Track",
                        help="заголовок в формате 'Исполнитель — Трек'")
    parser.add_argument("--secret-param", default="/autouploadbot/hub-secret")
    args = parser.parse_args()

    video_id = args.video.rsplit("v=", 1)[-1].split("&")[0]
    url = stack_output(args.stack, "WebhookUrl")
    secret = run(
        "aws", "ssm", "get-parameter", "--name", args.secret_param,
        "--with-decryption", "--query", "Parameter.Value", "--output", "text",
    )

    body = FEED.format(video_id=video_id, title=args.title, url=args.video).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()

    request = urllib.request.Request(
        url, data=body,
        headers={
            "Content-Type": "application/atom+xml",
            "X-Hub-Signature": f"sha1={signature}",
        },
    )

    print(f"→ {url}")
    print(f"  videoId={video_id} title={args.title!r}")

    with urllib.request.urlopen(request, timeout=30) as response:
        print(f"← {response.status} {response.read().decode(errors='replace')[:200]}")
        print("\nОжидается 202. Дальше смотри:")
        print(f"  sam logs --stack-name {args.stack} -n WorkerFunction --tail")


if __name__ == "__main__":
    main()
