# autouploadbot

A serverless bot that watches YouTube channels and reposts every new upload
to a Telegram channel as an MP3 with cover art, artist and title, captioned
with an "Original upload" link back to the video.

It runs entirely on AWS Lambda and costs cents per month: the only paid
line items are ECR image storage and a few DynamoDB reads.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/architecture-dark.png">
  <img alt="Architecture: YouTube and the WebSub hub feed a webhook and a poller, both enqueue into SQS, a worker Lambda downloads audio and sends it to Telegram; an admin bot edits the channel table from a Telegram group, where CloudWatch alarms also post" src="docs/images/architecture-light.png">
</picture>

## How it works

New videos arrive through **two independent paths**, and both end up in the
same SQS queue:

| Path | Trigger | Latency | Depends on |
| --- | --- | --- | --- |
| **Push** | the WebSub hub POSTs a signed Atom entry to `WebhookFunction` | seconds | a verified hub subscription |
| **Poll** | `PollFunction` reads each channel's RSS feed every 15 minutes | up to 15 min | nothing but YouTube |

Both paths produce identical messages carrying the video's YouTube channel.
`ChannelsTable` in DynamoDB maps every YouTube channel to one or more Telegram
chats, so a channel can have its own mirror, several channels can share a chat,
and one channel can be posted to several chats. The table is edited from the
[admin chat](#admin-chat) with inline buttons, and every function reads it at
run time, so adding a channel or a mirror needs no deploy.

For every target chat the worker claims `videoId#chat` in DynamoDB first, so
each chat gets a video exactly once — even when it arrives through both paths,
when YouTube re-sends a notification after a title edit, or when SQS delivers
a message twice. If one chat fails, the retry goes only to that chat. The
audio is downloaded once, however many chats receive it.

The worker downloads the audio with `yt-dlp`, converts it to MP3 with
`ffmpeg` and sends it through the Telegram Bot API with an "Original upload"
link to the video in the caption. A failed attempt releases
its claim and raises, so SQS retries it; after three failed receives the
message moves to `VideoDLQ` instead of disappearing.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/video-pipeline-dark.png">
  <img alt="Sequence: the webhook accepts a signed notification and enqueues it, the worker claims the video id, loads cookies, downloads from YouTube and sends to Telegram, or releases the claim and retries" src="docs/images/video-pipeline-light.png">
</picture>

### Why every piece is there

- **The webhook only enqueues.** Lambda freezes an instance as soon as it
  returns, so work cannot continue in a background thread after the `202`.
  All downloading happens inside the worker's own invocation.
- **The webhook is a zip on the standard library**, while the worker is a
  container. The webhook has to answer the hub's verification quickly, so it
  cold-starts in a fraction of a second; the worker needs `ffmpeg` and `deno`,
  so it ships as a ~1 GB image and a ~10 s cold start that nobody waits on.
- **Worker concurrency is capped on the SQS event source**
  (`MaximumConcurrency`), not with reserved concurrency: new AWS accounts
  have a Lambda concurrency limit of 10, which reserved concurrency cannot
  carve up.
- **`/tmp` on Lambda is real ephemeral disk** (4 GB here) and does not eat
  into the function's memory, so long mixes fit without a bigger instance.
- **Artwork goes two ways.** The Bot API accepts a `thumbnail` of at most
  320×320 and 200 KB, so the worker crops the best YouTube thumbnail
  (1280×720) to its centre square — label artwork sits there between black
  bars — embeds the full 720×720 cover in the MP3's ID3 tags together with
  artist and title, and sends a 320×320 copy as the thumbnail. The track also
  carries its duration from the YouTube metadata: without it Telegram clients
  may show `0:00` for a VBR MP3 until it is played.

## Repository layout

```
functions/ingest/        zip, stdlib only
  app.py                   WebhookFunction: hub verification, HMAC check, enqueue
  poll.py                  PollFunction: RSS polling with backlog protection
  xml_parser.py            Atom parsing and "Artist — Track" title parsing
  admin/                   AdminBotFunction: the admin chat bot
    app.py                   Telegram webhook, routing, the add-channel dialog
    screens.py               message texts and inline keyboards
    ops.py                   channels, status, hub subscriptions, backfill
    youtube.py               channel ID from a link or @handle
functions/alerts/        zip, stdlib only
  app.py                   AlertFunction: CloudWatch alarm → Telegram message
functions/resubscribe/   zip, stdlib only
  app.py                   ResubscribeFunction: WebSub subscribe and renew
worker/                  container image (yt-dlp, deno, ffmpeg)
  Dockerfile
  app/handler.py           WorkerFunction entry point
  app/downloader.py        yt-dlp download, cookies from S3
  app/artwork.py           square cover: embedded in the MP3, 320×320 thumbnail
  app/probe.py             hourly cookie probe, reported as a CloudWatch metric
  app/telegram.py          sendAudio via aiogram
  app/dedup.py             DynamoDB claim / release
tools/
  channels.py              list, import and export ChannelsTable as YAML
  telegram_webhook.py      connect the admin bot to Telegram
  stack.py                 shared helpers: stack name, outputs, resources
  send_test_event.py       sign and send a fake hub notification
  curl_to_cookies.py       turn DevTools cookies into cookies.txt
  upload_cookies.sh        clipboard → cookies.txt → S3
  backfill.py              post the latest N videos of each channel to its chats
docs/diagrams/           archify sources (.json) and interactive diagrams (.html)
template.yaml            the whole stack: queues, tables, bucket, functions, alarms, IAM
channels.yaml.example    YouTube channel → Telegram chats, for tools/channels.py
samconfig.toml.example   deploy parameters to copy
```

## Deploy

### Prerequisites

- AWS CLI with credentials — preferably an IAM user, not the root account
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Docker, running: `sam build` builds the worker image locally
- Python 3.11+; PyYAML only for `tools/channels.py import` (`pip install pyyaml`)
- A Telegram bot, and a Telegram group for administering it with the bot added
  as a member

### 1. Secrets

Both live in SSM Parameter Store; the template only knows their names.
Standard parameters are free.

```bash
aws ssm put-parameter --name /autouploadbot/bot-token \
  --type SecureString --value 'TELEGRAM_BOT_TOKEN'

aws ssm put-parameter --name /autouploadbot/hub-secret \
  --type SecureString --value "$(openssl rand -hex 32)"
```

`hub-secret` is shared with the WebSub hub, which signs every notification
with it. The webhook's Function URL is public by necessity — the hub cannot
authenticate to AWS — and rejects anything without a valid
`X-Hub-Signature` with `403`.

A third secret, `/autouploadbot/telegram-secret`, guards the admin bot's
webhook the same way; `tools/telegram_webhook.py` creates it after the first
deploy.

### 2. Parameters

```bash
cp samconfig.toml.example samconfig.toml
```

| Parameter | Meaning |
| --- | --- |
| `AdminChatId` | ID of the admin group: alerts go there, and every member can manage the bot from it. Add the bot to the group, send any message, then read `chat.id` from `getUpdates` — a negative number. |
| `AdminUtcOffset` | Hours from UTC for times the admin bot shows, e.g. `3` or `5.5`. Default `0`. |
| `LeaseSeconds` | WebSub lease, default and maximum `432000` (5 days). |
| `WorkerConcurrency` | How many tracks download in parallel, default `5`, minimum `2`. |
| `BotTokenParam`, `HubSecretParam`, `TelegramSecretParam` | SSM parameter names, defaults as above. |

### 3. Deploy

```bash
sam build && sam deploy
```

On the first deploy SAM warns that `WebhookFunction` and `AdminBotFunction`
have no authentication — answer `y`, that is intended: both check a secret
themselves.

### 4. Connect the admin bot

```bash
python3 tools/telegram_webhook.py          # secret, webhook, /menu and /status
python3 tools/telegram_webhook.py --info   # what Telegram has now
```

Run it once; again only if the `AdminBotUrl` output changes. From then on the
bot receives updates through the webhook, and `getUpdates` stops working for
it.

### 5. Channels

Send `/menu` in the admin group and press **➕ Добавить канал** — see
[Admin chat](#admin-chat). To load many channels at once, describe them in a
file instead:

```bash
cp channels.yaml.example channels.yaml
python3 tools/channels.py import           # validate, check, write to ChannelsTable
python3 tools/channels.py list             # what the table holds now
python3 tools/channels.py export           # back the table up to channels.yaml
```

```yaml
- name: My channel                   # optional, for your reference
  youtube: UCxxxxxxxxxxxxxxxxxxxxxx
  telegram: -1001234567890           # one chat…

- name: Another channel
  youtube: UCyyyyyyyyyyyyyyyyyyyyyy
  telegram:                          # …or several
    - -1001234567890
    - "@public_mirror"
```

`import` checks that every YouTube ID has a feed (and that its author matches
`name`) and that the bot is an admin allowed to post in every chat, then
upserts the entries; channels missing from the file are left alone.
`channels.yaml` is git-ignored — chat IDs are private. Channels and supergroups
have IDs starting with `-100`; without the minus Telegram answers
`chat not found`. A public channel can be given as `"@username"`.

### 6. First run

```bash
STACK=autouploadbot   # your stack_name
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

# subscribe right away instead of waiting for the hourly schedule
aws lambda invoke --function-name "$(out ResubscribeFunctionName)" /dev/stdout
```

The first `PollFunction` run for a channel (within 15 minutes) posts nothing:
it marks the 15 videos already in the feed as seen, otherwise the whole back
catalogue would land in the chat. A channel added later is handled the same
way.

## Admin chat

`AdminBotFunction` turns the admin group into a control panel. Every member of
that group can use it; updates from any other chat or from private messages
are ignored. Telegram signs each update with the `telegram-secret`, and the
function rejects anything else with `403`.

| Screen | What it does |
| --- | --- |
| 📊 Статус | queue and DLQ depth, cookie probe result, firing alarms, tracks sent per chat in the last 24 hours |
| 🔔 Подписки | the hub's state and expiry for every channel's WebSub subscription |
| 📺 Каналы | every channel with its mirrors; add or remove a mirror, post the latest 3 videos, delete the channel |
| ➕ Добавить канал | a dialog: YouTube link or `@handle` → target chat → confirm, optionally with the latest 3 videos |

The target chat is chosen by forwarding any post from it, by its `@username`,
or with a button: when someone makes the bot an admin in a channel, the bot
remembers it, offers **➕ Привязать** right away and lists the channel on the
chat step. Before saving, the bot checks that it can post there.

A new channel is subscribed on the hub at once. Deleting a channel
unsubscribes it and forgets its seen videos, so adding it back later does not
flood the chat with its back catalogue.

The dialog lives in one message that the bot edits step by step; its state is
kept in `BotStateTable` for an hour, because Lambda remembers nothing between
updates. `/menu` and `/status` work at any time; every alert has a
**📊 Статус** button too.

A new channel or mirror starts empty. **⏪ Залить последние 3** on its card
queues the latest videos a couple of minutes apart, oldest first, and skips
anything a chat already has. For several channels at once there is also:

```bash
python3 tools/backfill.py --latest 3 [--channel UC…] [--dry-run]
```

It queues the chosen videos in waves — the oldest first, the next once the
queue has drained — so every chat gets them in YouTube order.

## Titles

The worker needs an artist and a track name. Titles are split on the first of
` — `, ` – `, ` - ` (em dash, en dash, hyphen — each with spaces around), in
that order, so `Artist — Track - Extended Mix` keeps the hyphen inside the
track name. A trailing `[CATALOG123]` is removed. Videos with none of these
separators are skipped with a log line.

## YouTube from a datacenter IP

Out of the box YouTube answers requests from AWS with
`Sign in to confirm you're not a bot`. It takes two things to get through, and
you need both:

1. **A JavaScript runtime and the signature solver.** The image ships `deno`,
   and the dependency is `yt-dlp[default]`, which pulls in `yt-dlp-ejs`.
   Without the solver `deno` is useless: signatures are not solved and yt-dlp
   fails with `Requested format is not available`.
2. **Cookies of a logged-in account** in `CookiesBucket`. The worker copies
   them to `/tmp` on every cold start.

Use a separate account, not your main one: regular downloads from a datacenter
IP with its cookies can get it blocked.

Export the cookies from a **private (incognito) window**, not from your normal
browser session: the browser keeps rotating the cookies of a session in use,
and an exported copy of such a session dies within the hour. Log in, open any
video, then DevTools → Application → Cookies → `https://www.youtube.com`,
select all rows and copy. Close the window without logging out — logging out
invalidates the session — and do not use that session again.

Then **type** (do not copy-paste — that would overwrite the cookies in the
clipboard):

```bash
tools/upload_cookies.sh
```

It converts the clipboard with `tools/curl_to_cookies.py`, uploads the result
to `CookiesBucket` of the stack named in `samconfig.toml` and deletes the local
copy. The converter accepts rows from Application → Cookies, "Copy as cURL"
from the Network tab or a bare `Cookie` header value, never prints cookie
values, and refuses anything without signs of a logged-in session — so a
clipboard holding something else stops the upload instead of replacing good
cookies with junk.

When cookies expire, downloads fail with `Sign in to confirm you're not a bot`
and the videos pile up in `VideoDLQ`. Upload fresh cookies, then move the
failed videos back:

```bash
aws sqs start-message-move-task --source-arn \
  "$(aws sqs get-queue-attributes --queue-url "$(out DeadLetterQueueUrl)" \
     --attribute-names QueueArn --query Attributes.QueueArn --output text)"
```

Deduplication skips anything that already reached the channel. Messages stay
in the DLQ for 14 days, counted from when the video was first queued.

## WebSub subscription

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/websub-handshake-dark.png">
  <img alt="Sequence: EventBridge triggers ResubscribeFunction, which checks subscription details and posts a subscribe request; the hub verifies the callback with a challenge, then delivers signed notifications" src="docs/images/websub-handshake-light.png">
</picture>

A subscription becomes active only after the hub verifies it: it sends a
`GET` with a random `hub.challenge` to the callback, and the callback must echo
it back. Until then the hub keeps the request as `unverified` and delivers
nothing. The check exists so nobody can subscribe someone else's URL to a
stream of notifications.

`ResubscribeFunction` runs on two schedules:

| Schedule | Payload | What it does |
| --- | --- | --- |
| `Retry`, hourly | `{"force": false}` | re-submits subscriptions that are not `verified` yet |
| `Renew`, daily | `{"force": true}` | renews every lease regardless of state |

Channels are processed in parallel: the hub can hold each request for ~20 s.
A `5xx` or timeout from the hub is logged as "degraded" and does not fail the
run; a `4xx` means our parameters are wrong and does.

Check the state:

```bash
sam logs --stack-name "$STACK" -n ResubscribeFunction | grep 'state='
```

> [!NOTE]
> The hub can be down for days. From 19 to 21 September 2026
> `pubsubhubbub.appspot.com` answered every subscribe request with
> `503 Transient error` after ~20 s and sent no verification, so all
> subscriptions stayed `unverified`. Nothing was lost: the poll path delivered
> every video, and once the hub recovered the hourly schedule verified the
> subscriptions without any action. A `5xx` from the hub is logged as
> "degraded" for exactly this reason.

Use the real channel feed as the topic,
`https://www.youtube.com/feeds/videos.xml?channel_id=…`. The similar-looking
`/xml/feeds/videos.xml` is a static 463-byte stub that ignores `channel_id`.
The function builds the URL itself, so this only matters when subscribing by
hand.

## Testing without the hub

```bash
python3 tools/send_test_event.py \
  --video 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --title 'Artist — Track' \
  --channel UC…            # routes like a video of that channel
```

The script signs a fake Atom notification with the same `hub-secret` and posts
it to the webhook, so everything after the hub runs for real: signature check,
queue, worker, YouTube, Telegram. The track is actually posted. Running it
again for the same video does nothing — deduplication skips it. `--channel`
defaults to the first channel in `ChannelsTable`, the stack name to
`stack_name` from `samconfig.toml`.

## Operations

```bash
sam logs --stack-name "$STACK" --tail                   # all functions
sam logs --stack-name "$STACK" -n WorkerFunction --tail

aws sqs receive-message --queue-url "$(out DeadLetterQueueUrl)"   # failed videos
```

The console shows the stack under CloudFormation → Stacks, in the region from
`samconfig.toml`. Treat it as read-only: `sam deploy` overwrites manual changes.

Every worker deploy adds an image to ECR. Keep only the last two:

```bash
aws ecr put-lifecycle-policy --repository-name <repo> --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"keep last 2","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":2},"action":{"type":"expire"}}]}'
```

## Alerts

Three CloudWatch alarms post to the admin chat, both when something breaks
and when it recovers:

| Alarm | Fires when | Usually means |
| --- | --- | --- |
| `DeadLetterAlarm` | a message lands in `VideoDLQ` | a video failed three times — expired cookies, a file over 50 MB |
| `QueueStuckAlarm` | a message waits in `VideoQueue` over an hour | the worker is not running or dies before processing |
| `CookieProbeAlarm` | the hourly cookie probe fails | cookies expired — upload fresh ones before tracks start failing |

The cookie probe runs `WorkerFunction` hourly with `{"probe": true}`: it asks
YouTube for the metadata of a recent video exactly as a real download would —
cookies, signature solving and all — but downloads nothing, and reports the
result as the `autouploadbot/CookieProbe` metric. It never raises, because
EventBridge Scheduler would otherwise retry a failed run up to 185 times.

Alarm → SNS topic → `AlertFunction` → Telegram. Test the path without
breaking anything:

```bash
aws cloudwatch set-alarm-state --state-value ALARM --state-reason test \
  --alarm-name "$(aws cloudwatch describe-alarms --alarm-name-prefix "$STACK-CookieProbe" \
     --query 'MetricAlarms[0].AlarmName' --output text)"
```

The alarm returns to `OK` on its next evaluation, which posts the recovery
message too.

## Cost

Assuming 200–400 tracks a month — a handful of channels posting once or twice
a day:

| Service | Usage | Cost |
| --- | --- | --- |
| Lambda | ~20–30k GB-s, a few thousand invocations; free tier is 400k GB-s | $0 |
| ECR | worker images, ~350 MB each, one more per deploy | ~$0.20, ~$0.07 with the lifecycle rule above |
| DynamoDB on-demand | polling reads the seen table, writes only new videos | ~$0.02 |
| SQS, EventBridge Scheduler, S3, SSM, data out | well inside free tiers | $0 |

The number of chats barely matters: a video is downloaded once, and each extra
chat costs a second of sending and ~10 MB of traffic. A track takes 15–50 s at
2 GB, so the Lambda free tier lasts for roughly 8–10 thousand tracks a month.
Peak memory is ~600 MB, so `MemorySize` could drop to 1024, at the price of
slower `ffmpeg` since Lambda CPU scales with memory.

## Known limitations

- The Telegram Bot API rejects files over 50 MB, so long mixes fail at the send
  step, after downloading and converting.
- A Lambda invocation is capped at 15 minutes per track.
- A deleted video arrives as `<at:deleted-entry>`; the webhook logs
  `Unparseable notification body` and answers `200` so the hub does not retry.
- YouTube cookies expire and have to be re-exported now and then.

## Diagrams

The images above are screenshots of interactive diagrams generated with
[archify](https://github.com/tt-a1i/archify). The interactive versions — pan and
zoom, search, relationship tracing, light and dark themes, exports — are in
`docs/diagrams/*.html`; download one and open it in a browser. The `.json`
files next to them are the sources:

```bash
ARCHIFY=<path-to-archify>/bin/archify.mjs
node $ARCHIFY deliver architecture docs/diagrams/architecture.json docs/diagrams/architecture.html --quality showcase
node $ARCHIFY deliver sequence docs/diagrams/video-pipeline.json docs/diagrams/video-pipeline.html --quality showcase
node $ARCHIFY deliver sequence docs/diagrams/websub-handshake.json docs/diagrams/websub-handshake.html --quality showcase
```

then replace the PNGs in `docs/images/` with a screenshot or an export of
each diagram, in the light and dark themes.
