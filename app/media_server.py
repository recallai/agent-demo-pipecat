"""Serve each meeting's private media page and own its short-lived agent session."""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
from loguru import logger as sdk_logger

from app.config import ROOT, require_env, session_timeout
from app.recall_client import RecallClient, RecallError

logger = logging.getLogger("meeting-demo")
# Provider logs can contain transcripts or response bodies. Report only failures.
sdk_logger.disable("pipecat")
sdk_logger.disable("simli")


@dataclass
class MeetingSession:
    token: str
    expires_at: float
    bot_id: str | None = None
    websocket: WebSocket | None = None
    task: asyncio.Task | None = None
    expiry_task: asyncio.Task | None = None
    bot_ready: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False


class MeetingSessions:
    def __init__(self):
        self.entries: dict[str, MeetingSession] = {}

    def create(self) -> MeetingSession:
        duration = session_timeout()
        session = MeetingSession(
            token=secrets.token_urlsafe(32), expires_at=time.monotonic() + duration,
        )
        self.entries[session.token] = session
        session.expiry_task = asyncio.create_task(self._expire(session, duration))
        return session

    def require(self, token: str) -> MeetingSession:
        session = self.entries.get(token)
        if session is None or session.closed or time.monotonic() >= session.expires_at:
            raise HTTPException(404, "This meeting session is unavailable or expired.")
        return session

    async def bind_bot(self, session: MeetingSession, bot_id: str) -> None:
        session.bot_id = bot_id
        if session.closed:
            await self._leave_bot(bot_id)
            raise HTTPException(504, "The session expired before the bot request completed.")
        session.bot_ready.set()

    async def run_agent(self, session: MeetingSession) -> None:
        failed = False
        try:
            await session.bot_ready.wait()
            if session.closed or session.bot_id is None:
                return
            from app.agent import run_agent

            await run_agent(session.websocket, session.bot_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failed = True
            logger.error("Meeting agent failed (%s); closing the session", type(error).__name__)
        finally:
            await self.close(session, leave_bot=failed)

    async def _expire(self, session: MeetingSession, duration: int) -> None:
        await asyncio.sleep(duration)
        logger.info("Meeting session reached its time limit")
        await self.close(session, leave_bot=True)

    async def _leave_bot(self, bot_id: str) -> None:
        try:
            async with RecallClient(
                require_env("RECALL_API_KEY"), require_env("RECALL_API_BASE_URL"),
            ) as client:
                await client.leave_call(bot_id)
        except (RecallError, ValueError):
            logger.warning("Could not confirm bot departure during cleanup; not retrying")

    async def close(self, session: MeetingSession, *, leave_bot: bool) -> None:
        if session.closed:
            return
        session.closed = True
        self.entries.pop(session.token, None)
        current = asyncio.current_task()
        tasks = [task for task in (session.task, session.expiry_task)
                 if task is not None and task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if session.websocket is not None:
            try:
                await session.websocket.close()
            except (RuntimeError, OSError):
                pass  # The transport or remote page may have already closed it.
        if leave_bot and session.bot_id is not None:
            await self._leave_bot(session.bot_id)

    async def close_all(self) -> None:
        await asyncio.gather(
            *(self.close(session, leave_bot=True) for session in list(self.entries.values())),
        )


sessions = MeetingSessions()
media_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@media_app.get("/media/{token}")
async def meeting_page(token: str):
    sessions.require(token)
    return FileResponse(ROOT / "app/templates/renderer.html", headers=PRIVATE_HEADERS)


@media_app.get("/media/{token}/microphone.js")
async def microphone_processor(token: str):
    sessions.require(token)
    return FileResponse(
        ROOT / "app/templates/microphone.js", media_type="text/javascript", headers=PRIVATE_HEADERS,
    )


@media_app.websocket("/media/{token}/stream")
async def meeting_stream(websocket: WebSocket, token: str):
    try:
        session = sessions.require(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    if session.websocket is not None:
        await websocket.close(code=1008)
        return
    # Claim before awaiting: one peer can consume each meeting session.
    session.websocket = websocket
    try:
        await websocket.accept()
        session.task = asyncio.create_task(sessions.run_agent(session))
        await session.task
    except asyncio.CancelledError:
        if not session.closed:
            await sessions.close(session, leave_bot=True)
            raise
    except Exception:
        await sessions.close(session, leave_bot=True)
