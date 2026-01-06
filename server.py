from fastapi import FastAPI, Request, status, Response
from xml_parser import parse_xml
from dotenv import load_dotenv

load_dotenv()

from downloader import handle_video
from threading import Thread

app = FastAPI()

@app.get("/")
async def process_youtube_get_webhook(request: Request):
    challenge = request.query_params.get("hub.challenge")
    response = Response(challenge, status_code=status.HTTP_200_OK)
    return response

@app.post("/")
async def process_youtube_post_webhook(request: Request):
    try:
        body: bytes = await request.body()
        data = parse_xml(body)
        Thread(target=handle_video, args=(data,)).start()
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except Exception as e:
        return str(e)
    
PUBSUBHUBBUB_API_LINK="https://pubsubhubbub.appspot.com/subscribe"
