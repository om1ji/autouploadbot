from locust import HttpUser, task, between
import random
import json

with open("tracks.json", "r", encoding="utf-8") as f:
    TRACK_IDS = json.load(f)

XML_TEMPLATE = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>{title}</title>
    <link rel="alternate" href="{url}"/>
  </entry>
</feed>
"""

class YouTubeWebhookUser(HttpUser):
    wait_time = between(0.1, 1.0)

    @task
    def send_event(self):
        track_id = random.choice(TRACK_IDS)

        xml = XML_TEMPLATE.format(
            title="Om1ji — Test Track",
            url=f"https://www.youtube.com/watch?v={track_id}",
        )

        self.client.post(
            "/",
            data=xml,
            headers={"Content-Type": "application/atom+xml"},
        )