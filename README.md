# YouTube audio uploader

Telegram bot for automatically uploading audio from YouTube channels.
Target chat is configured via `.env` values.

## Run bot

Run via command:

```bash
python3 server.py
```


## Manually subscribe to channel updates

Go to this link to manually subscribe and test your bot:
https://pubsubhubbub.appspot.com/subscribe
### Fields required

- `Callback URL` - URL to your server, accessible from Internet. All events will be sent to this URL as POST requests from the PubSubHubbub API.

- `Topic URL` - XML feed URL of a specific YouTube channel.

> [!WARNING] PubSubHubbub requires proper link for `Topic URL` field. In official Google [documentation](https://google.com) the example link is outdated ([explained here](https://issuetracker.google.com/issues/204101548#comment36)).
> Use the following `Topic URL` format:
>
> ```
> https://www.youtube.com/xml/feeds/videos.xml?channel_id=CHANNEL_ID
> ```


- `Verify Type`
	- `Synchronous` - the hub waits for a verification request and expects a valid response.
	- `Asynchronous` - verification happens without waiting for an immediate response.

- `Mode` - Two options: `Subscribe`/`Unsubscribe`

- `Lease seconds` (optional) - duration of subscription

## Subscribe to channel updates automatically

To keep the subscription active, the bot must periodically re-subscribe to the PubSubHubbub topic.

Before `hub.lease_seconds` expires, the bot sends the following `POST` request:

```bash
curl -X POST https://pubsubhubbub.appspot.com/subscribe \
-H "Content-Type: application/x-www-form-urlencoded" \
--data-urlencode "hub.callback=CALLBACK_URL" \
--data-urlencode "hub.topic=TOPIC_URL" \
--data-urlencode "hub.verify=async" \
--data-urlencode "hub.mode=subscribe" \
--data-urlencode "hub.verify_token=" \
--data-urlencode "hub.secret=" \
--data-urlencode "hub.lease_numbers=LEASE_SECONDS"
```

Don't forget to replace `CALLBACK_URL`, `TOPIC_URL`, `LEASE_SECONDS` with your values.

> [!NOTE] Maximum supported value for `LEASE_SECONDS` is `432000`
