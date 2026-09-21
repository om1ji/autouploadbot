#!/usr/bin/env python3
"""Connect the admin bot to Telegram.

    python3 tools/telegram_webhook.py          # set up or refresh
    python3 tools/telegram_webhook.py --info   # show what Telegram has now

Creates the webhook secret in SSM if it does not exist, registers the
AdminBotUrl stack output as the bot's webhook with that secret and the update
types the admin bot handles, and sets the /menu and /status commands for the
admin chat. Run it once after the first deploy, and again only if the
AdminBotUrl output changes.

Once a webhook is set, getUpdates stops working for the bot — that is how
Telegram works, not a problem.
"""

import json
import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack

TOKEN_PARAM = "/autouploadbot/bot-token"
SECRET_PARAM = "/autouploadbot/telegram-secret"


def ssm_get(name: str) -> str | None:
    result = subprocess.run(
        [
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            name,
            "--with-decryption",
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def telegram(token: str, method: str, **params) -> dict:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read(), strict=False)


def main() -> None:
    token = ssm_get(TOKEN_PARAM)
    if not token:
        sys.exit(f"No bot token in SSM {TOKEN_PARAM}")

    if "--info" in sys.argv:
        info = telegram(token, "getWebhookInfo")["result"]
        print(
            json.dumps(
                {
                    k: info.get(k)
                    for k in (
                        "url",
                        "pending_update_count",
                        "last_error_message",
                        "allowed_updates",
                    )
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    secret = ssm_get(SECRET_PARAM)
    if not secret:
        # Telegram допускает в секрете A–Z, a–z, 0–9, _ и -
        secret = secrets.token_hex(32)
        stack.aws(
            "ssm",
            "put-parameter",
            "--name",
            SECRET_PARAM,
            "--type",
            "SecureString",
            "--value",
            secret,
        )
        print(f"Created {SECRET_PARAM}")

    url = stack.output("AdminBotUrl")
    if not url or url == "None":
        sys.exit("No AdminBotUrl output — deploy the stack first")

    result = telegram(
        token,
        "setWebhook",
        url=url,
        secret_token=secret,
        allowed_updates=["message", "callback_query", "my_chat_member"],
        drop_pending_updates=True,
    )
    print("setWebhook:", result.get("description"))

    admin_chat = stack.aws(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        stack.stack_name(),
        "--query",
        "Stacks[0].Parameters[?ParameterKey=='AdminChatId'].ParameterValue",
        "--output",
        "text",
    )
    commands = [
        {"command": "menu", "description": "Меню бота"},
        {"command": "status", "description": "Статус: очередь, cookies, отправки"},
    ]
    if admin_chat:
        telegram(
            token,
            "setMyCommands",
            commands=commands,
            scope={"type": "chat", "chat_id": int(admin_chat)},
        )
        print("Commands set for the admin chat")


if __name__ == "__main__":
    main()
