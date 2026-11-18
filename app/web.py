"""Run the local meeting launcher and its separately tunneled media server."""

import asyncio
import re
import signal
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from app.config import ROOT, public_origin, require_env, validate_agent_settings
from app.recall_client import RecallClient, RecallError
from app.media_server import sessions, media_app


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        await sessions.close_all()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
pending_meetings: set[str] = set()


class MeetingRequest(BaseModel):
    meeting_url: str

    @field_validator("meeting_url")
    @classmethod
    def validate_meeting_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.netloc != "meet.google.com"
            or not re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", parsed.path)
        ):
            raise ValueError("Enter a Google Meet URL, such as https://meet.google.com/abc-defg-hij.")
        return f"https://meet.google.com{parsed.path.rstrip('/')}"


@app.get("/")
async def index():
    return FileResponse(ROOT / "app" / "templates" / "index.html")


@app.post("/api/bots", status_code=201)
async def create_meeting_agent(request: MeetingRequest):
    if request.meeting_url in pending_meetings:
        raise HTTPException(409, "An agent request for this meeting is already in progress.")
    pending_meetings.add(request.meeting_url)
    session = None
    try:
        validate_agent_settings()
        async with RecallClient(
            require_env("RECALL_API_KEY"), require_env("RECALL_API_BASE_URL"),
        ) as client:
            # The media page can connect before create_bot returns its bot ID.
            session = sessions.create()
            media_url = f"{public_origin()}/media/{session.token}"
            bot_id = await client.create_bot(request.meeting_url, media_url)
            await sessions.bind_bot(session, bot_id)
        return {
            "bot_id": bot_id,
            "message": "Agent requested. Admit pipecat demo in Google Meet if prompted.",
        }
    except ValueError as error:
        if session is not None:
            await sessions.close(session, leave_bot=True)
        raise HTTPException(503, str(error)) from None
    except RecallError as error:
        if session is not None:
            await sessions.close(session, leave_bot=True)
        raise HTTPException(502, str(error)) from None
    except BaseException:
        if session is not None:
            await sessions.close(session, leave_bot=True)
        raise
    finally:
        pending_meetings.discard(request.meeting_url)


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Both servers share one shutdown handler instead of replacing each other's.
        yield


async def serve() -> None:
    servers = [
        LocalServer(uvicorn.Config(app, host="127.0.0.1", port=8001, access_log=False)),
        LocalServer(uvicorn.Config(
            media_app, host="127.0.0.1", port=7861, access_log=False,
            log_level="warning", ws_max_size=64_000, ws_max_queue=4,
        )),
    ]
    loop = asyncio.get_running_loop()

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop)
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sessions.close_all()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    asyncio.run(serve())
