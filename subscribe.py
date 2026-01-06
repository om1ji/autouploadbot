from datetime import datetime, timedelta
import asyncio
import logging
import httpx
import os

expires_at: datetime | None = None


async def subscription_watcher(stop_event: asyncio.Event):
    global expires_at

    await resubscribe()

    while not stop_event.is_set():
        try:
            if not expires_at or expires_at <= datetime.utcnow() + timedelta(minutes=10):
                await resubscribe()
        except Exception:
            logging.exception("Subscription watcher error")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


async def resubscribe():
    global expires_at

    callback = os.getenv("HOST_URL")
    topic = os.getenv("TOPIC_URL")
    lease = int(os.getenv("LEASE_SECONDS", "432000"))

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://pubsubhubbub.appspot.com/subscribe",
            data={
                "hub.callback": callback,
                "hub.topic": topic,
                "hub.verify": "async",
                "hub.mode": "subscribe",
                "hub.lease_seconds": lease,
            },
        )
        
        logging.info(r.content)

    if r.status_code not in (200, 202):
        logging.error("Resubscribe failed", extra={"status": r.status_code, "body": r.text})
        return

    expires_at = datetime.utcnow() + timedelta(seconds=lease)
    logging.info("Subscribed successfully, expires at %s", expires_at)
