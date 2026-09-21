"""Shared helpers for the tools: the stack, its outputs and resources.

The stack name comes from samconfig.toml, the same one `sam deploy` uses.
Everything goes through the AWS CLI, so the tools need no Python packages.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def aws(*args: str) -> str:
    return subprocess.run(
        ["aws", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def stack_name() -> str:
    config = ROOT / "samconfig.toml"
    if config.is_file():
        found = re.search(
            r'^stack_name\s*=\s*"([^"]+)"', config.read_text(), re.MULTILINE
        )
        if found:
            return found.group(1)
    return "autouploadbot"


def output(key: str) -> str:
    return aws(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        stack_name(),
        "--query",
        f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue",
        "--output",
        "text",
    )


def resource(logical_id: str) -> str:
    return aws(
        "cloudformation",
        "describe-stack-resources",
        "--stack-name",
        stack_name(),
        "--query",
        f"StackResources[?LogicalResourceId=='{logical_id}'].PhysicalResourceId",
        "--output",
        "text",
    )


def channels_from_table() -> dict[str, dict]:
    """{youtube_id: {"name": …, "chats": […]}} from ChannelsTable."""
    items = json.loads(
        aws(
            "dynamodb",
            "scan",
            "--table-name",
            resource("ChannelsTable"),
            "--output",
            "json",
        )
    )["Items"]
    result = {}
    for item in items:
        chats = [c.get("S") or c.get("N") for c in item.get("chats", {}).get("L", [])]
        result[item["youtube_id"]["S"]] = {
            "name": item.get("name", {}).get("S", item["youtube_id"]["S"]),
            "chats": [int(c) if c.lstrip("-").isdigit() else c for c in chats],
        }
    return result
