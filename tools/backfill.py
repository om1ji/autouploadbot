#!/usr/bin/env python3
"""Post the latest videos of each channel to its Telegram chats.

    python3 tools/backfill.py --latest 3                 # every channel in channels.yaml
    python3 tools/backfill.py --latest 3 --channel UC…   # one channel
    python3 tools/backfill.py --latest 3 --dry-run       # show the plan only

Useful after adding a channel or a mirror: new channels start empty, because
the first poll only marks the videos already in the feed as seen.

Videos go straight to the queue, in waves: the oldest of the selected videos
of every channel first, then the next once the queue is empty. Channels are
processed in parallel, while inside each chat the tracks keep their YouTube
order. Deduplication still applies — a video already posted to a chat is not
posted there again — so running this twice is harmless.
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

sys.path.insert(0, str(ROOT / "functions" / "ingest"))
from xml_parser import parse_artist_title

_spec = importlib.util.spec_from_file_location("deploy", ROOT / "tools" / "deploy.py")
deploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy)


def aws(*args: str) -> str:
    return subprocess.run(
        ["aws", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def stack_name() -> str:
    found = re.search(
        r'^stack_name\s*=\s*"([^"]+)"', (ROOT / "samconfig.toml").read_text(), re.MULTILINE
    )
    return found.group(1) if found else "autouploadbot"


def latest_videos(channel: str, count: int) -> list[dict]:
    """The newest `count` videos with a parseable title, oldest first."""
    with urllib.request.urlopen(FEED.format(channel), timeout=15) as response:
        root = ET.fromstring(response.read())
    videos = []
    for entry in root.findall("a:entry", NS):  # the feed lists newest first
        title = entry.find("a:title", NS).text
        try:
            artist, track_name = parse_artist_title(title)
        except ValueError:
            print(f"  skip (title has no artist — track separator): {title}")
            continue
        video_id = entry.find("yt:videoId", NS).text
        videos.append(
            {
                "video_link": f"https://www.youtube.com/watch?v={video_id}",
                "video_id": video_id,
                "channel_id": channel,
                "artist": artist,
                "track_name": track_name,
                "title": title,
            }
        )
        if len(videos) == count:
            break
    return list(reversed(videos))


def claimed(table: str, key: str) -> bool:
    return bool(
        aws(
            "dynamodb",
            "get-item",
            "--table-name",
            table,
            "--key",
            json.dumps({"video_id": {"S": key}}),
            "--output",
            "text",
        )
    )


def queue_busy(queue: str) -> int:
    counts = aws(
        "sqs",
        "get-queue-attributes",
        "--queue-url",
        queue,
        "--attribute-names",
        "ApproximateNumberOfMessages",
        "ApproximateNumberOfMessagesNotVisible",
        "--query",
        "Attributes.[ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible]",
        "--output",
        "text",
    ).split()
    return sum(int(c) for c in counts)


def wait_drained(queue: str, timeout: int = 15 * 60, stable: int = 3) -> bool:
    """Ждём, пока очередь пуста `stable` проверок подряд.

    Счётчики SQS приблизительные и могут на миг показать ноль, пока сообщение
    ещё в работе; одной проверки мало — следующая волна обогнала бы предыдущую.
    """
    deadline, zeros = time.time() + timeout, 0
    while time.time() < deadline:
        zeros = zeros + 1 if queue_busy(queue) == 0 else 0
        if zeros >= stable:
            return True
        time.sleep(10)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--latest", type=int, default=3, help="videos per channel")
    parser.add_argument("--channel", help="only this YouTube channel")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    channel_map, names = deploy.load_channels()
    if args.channel:
        if args.channel not in channel_map:
            sys.exit(f"{args.channel} is not in channels.yaml")
        channel_map = {args.channel: channel_map[args.channel]}

    stack = stack_name()
    outputs = json.loads(
        aws(
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack,
            "--query",
            "Stacks[0].Outputs",
            "--output",
            "json",
        )
    )
    queue = next(o["OutputValue"] for o in outputs if o["OutputKey"] == "QueueUrl")
    table = aws(
        "cloudformation",
        "describe-stack-resources",
        "--stack-name",
        stack,
        "--query",
        "StackResources[?LogicalResourceId=='DedupTable'].PhysicalResourceId",
        "--output",
        "text",
    )

    deployed = aws(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        stack,
        "--query",
        "Stacks[0].Parameters[?ParameterKey=='ChannelMap'].ParameterValue|[0]",
        "--output",
        "text",
    )
    missing = [c for c in channel_map if c not in deployed]
    if missing:
        message = f"Not deployed yet: {', '.join(names[c] for c in missing)}. Run python3 tools/deploy.py first."
        if not args.dry_run:
            sys.exit(message)
        print(f"warning: {message}\n")

    waves: list[list[dict]] = [[] for _ in range(args.latest)]
    for channel, chats in channel_map.items():
        print(f"{names[channel]} → {', '.join(map(str, chats))}")
        videos = latest_videos(channel, args.latest)
        # выравниваем по концу: самые свежие ролики всех каналов уходят последней волной
        offset = args.latest - len(videos)
        for i, video in enumerate(videos):
            if claimed(table, video["video_id"]):
                print(f"  skip (claimed before per-chat routing): {video['title']}")
                continue
            todo = [c for c in chats if not claimed(table, f"{video['video_id']}#{c}")]
            if not todo:
                print(f"  skip (already in every chat): {video['title']}")
                continue
            print(f"  wave {offset + i + 1}: {video['title']}")
            waves[offset + i].append(video)

    total = sum(len(w) for w in waves)
    print(f"\n{total} video(s) to post in {sum(1 for w in waves if w)} wave(s).")
    if args.dry_run or not total:
        return

    for number, wave in enumerate(waves, 1):
        if not wave:
            continue
        for video in wave:
            body = {k: v for k, v in video.items() if k != "title"}
            aws(
                "sqs",
                "send-message",
                "--queue-url",
                queue,
                "--message-body",
                json.dumps(body),
            )
        print(f"wave {number}: queued {len(wave)}, waiting for the worker…", flush=True)
        time.sleep(10)
        if not wait_drained(queue):
            sys.exit(
                "The queue did not drain in 15 minutes — check the worker logs and the DLQ."
            )
    print("Done. Failed videos, if any, are in the DLQ.")


if __name__ == "__main__":
    main()
