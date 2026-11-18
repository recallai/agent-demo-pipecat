"""Accept Recall's WebRTC offers and own each short-lived meeting session."""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger as sdk_logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pydantic import BaseModel, Field

from app.config import require_env, session_timeout, webrtc_ice_servers
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
    connection: SmallWebRTCConnection | None = None
    offer_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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
            if session.connection.pc.connectionState in {"closed", "failed"}:
                raise RuntimeError("The meeting connection ended before startup.")
            from app.agent import run_agent

            await run_agent(session.connection, session.bot_id)
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
        if leave_bot and session.bot_id is not None:
            await self._leave_bot(session.bot_id)
        try:
            if session.connection is not None:
                # A failed peer can stall during SDK teardown; do not keep the bot in the call.
                async with asyncio.timeout(5):
                    await session.connection.disconnect()
        except Exception as error:
            logger.warning("WebRTC cleanup failed (%s)", type(error).__name__)

    async def close_all(self) -> None:
        await asyncio.gather(
            *(self.close(session, leave_bot=True) for session in list(self.entries.values())),
        )


sessions = MeetingSessions()
media_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class OfferRequest(BaseModel):
    sdp: str = Field(min_length=1, max_length=1_048_576)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=256)
    restart_pc: bool = False


@media_app.post("/sessions/{token}/api/offer")
async def accept_offer(token: str, offer: OfferRequest):
    session = sessions.require(token)
    # Serialize initial offers and renegotiation so a session owns only one peer.
    async with session.offer_lock:
        sessions.require(token)
        if session.connection is not None:
            if offer.pc_id != session.connection.pc_id:
                raise HTTPException(409, "This meeting already has a connection.")
        elif offer.pc_id is not None:
            raise HTTPException(404, "This meeting connection is unavailable.")

        try:
            async with asyncio.timeout(30):
                if session.connection is None:
                    # Own the peer before ICE gathering so even cancelled setup can close it.
                    session.connection = SmallWebRTCConnection(
                        ice_servers=webrtc_ice_servers(),
                    )

                    @session.connection.event_handler("closed")
                    async def on_closed(_connection):
                        if session.task is None or not session.bot_ready.is_set():
                            await sessions.close(session, leave_bot=True)

                    await session.connection.initialize(sdp=offer.sdp, type=offer.type)
                    if not session.closed:
                        session.task = asyncio.create_task(sessions.run_agent(session))
                else:
                    await session.connection.renegotiate(
                        sdp=offer.sdp, type=offer.type, restart_pc=offer.restart_pc,
                    )
            # Expiry or shutdown can happen while ICE candidates are gathered.
            if session.closed:
                raise HTTPException(410, "This meeting session has ended.")
            answer = session.connection.get_answer()
            if answer is None:
                raise RuntimeError("No WebRTC answer was produced.")
            return JSONResponse(answer, headers={"Cache-Control": "no-store"})
        except HTTPException:
            raise
        except asyncio.CancelledError:
            await sessions.close(session, leave_bot=True)
            raise
        except Exception as error:
            logger.warning("WebRTC setup failed (%s)", type(error).__name__)
            await sessions.close(session, leave_bot=True)
            raise HTTPException(502, "Could not establish the meeting connection.") from None
