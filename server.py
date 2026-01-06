from fastapi import FastAPI, Request, Response, status
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import asyncio
from threading import Thread

load_dotenv()

from subscribe import subscription_watcher
from xml_parser import parse_xml
from downloader import handle_video


stop_event: asyncio.Event | None = None
watcher_task: asyncio.Task | None = None
loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global stop_event, watcher_task, loop

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(subscription_watcher(stop_event))

    try:
        yield
    finally:
        stop_event.set()
        await watcher_task


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def process_youtube_get_webhook(request: Request):
    challenge = request.query_params.get("hub.challenge")
    return Response(challenge, status_code=status.HTTP_200_OK)


@app.post("/")
async def process_youtube_post_webhook(request: Request):
    body: bytes = await request.body()
    data = parse_xml(body)

    Thread(
        target=handle_video,
        args=(data, loop),
        daemon=True,
    ).start()

    return Response(status_code=status.HTTP_202_ACCEPTED)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
