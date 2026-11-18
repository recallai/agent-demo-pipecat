"""Carry meeting PCM and avatar PCM/JPEG over one authenticated WebSocket."""

import asyncio
import contextlib
from io import BytesIO

from fastapi import WebSocket, WebSocketDisconnect
from PIL import Image
from pipecat.frames.frames import (
    CancelFrame, ClientConnectedFrame, EndFrame, Frame, InputAudioRawFrame,
    InterruptionFrame, OutputAudioRawFrame, OutputImageRawFrame, StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


def encode_jpeg(frame: OutputImageRawFrame) -> bytes:
    image = Image.frombytes(frame.format or "RGB", frame.size, frame.image)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


class BrowserTransport(BaseTransport):
    """Input is raw 16 kHz PCM16 mono; output tags are 1=24 kHz PCM, 2=JPEG."""

    def __init__(self, websocket: WebSocket, params: TransportParams):
        super().__init__()
        # Cycling the latest image bounds video memory when the connection slows.
        params = params.model_copy(update={
            "video_out_is_live": False,
            "video_out_framerate": min(15, params.video_out_framerate),
            "audio_in_sample_rate": 16000, "audio_out_sample_rate": 24000,
            "audio_in_channels": 1, "audio_out_channels": 1,
        })
        self.websocket = websocket
        self.audio_generation = 0
        self._closed = False
        self._send_lock = asyncio.Lock()
        self._input = BrowserInput(self, params)
        self._output = BrowserOutput(self, params)
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self) -> BaseInputTransport:
        return self._input

    def output(self) -> BaseOutputTransport:
        return self._output

    async def connected(self):
        await self._call_event_handler("on_client_connected", self.websocket)

    async def disconnected(self):
        if not self._closed:
            await self.close()
            await self._call_event_handler("on_client_disconnected", self.websocket)

    async def send(self, payload: bytes | str, *, audio_generation: int | None = None) -> bool:
        try:
            async with self._send_lock:
                if self._closed:
                    return False
                if audio_generation is not None and audio_generation != self.audio_generation:
                    return True  # The browser already discarded this interrupted reply.
                async with asyncio.timeout(5):
                    if isinstance(payload, bytes):
                        await self.websocket.send_bytes(payload)
                    else:
                        await self.websocket.send_text(payload)
            return True
        except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            await self.disconnected()
            return False

    async def close(self, code: int = 1000):
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            async with asyncio.timeout(2):
                async with self._send_lock:
                    await self.websocket.close(code=code)


class BrowserInput(BaseInputTransport):
    def __init__(self, transport: BrowserTransport, params: TransportParams):
        super().__init__(params)
        self._transport = transport
        self._receive_task = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        await self.push_frame(ClientConnectedFrame())
        await self._transport.connected()
        self._receive_task = self.create_task(self._receive_audio())

    async def _receive_audio(self):
        try:
            while True:
                audio = await self._transport.websocket.receive_bytes()
                if not audio or len(audio) > 32000 or len(audio) % 2:
                    await self.push_error(
                        "Invalid browser PCM packet", force_treat_as_permanent=True,
                    )
                    break
                await self.push_audio_frame(InputAudioRawFrame(audio, 16000, 1))
        except (WebSocketDisconnect, RuntimeError, OSError, KeyError):
            pass
        finally:
            await self._transport.disconnected()

    async def _teardown(self):
        await self._transport.close()
        task, self._receive_task = self._receive_task, None
        if task and task is not asyncio.current_task():
            await self.cancel_task(task)

    async def stop(self, frame: EndFrame):
        await self._teardown()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        await self._teardown()
        await super().cancel(frame)

    async def cleanup(self):
        await self._teardown()
        await super().cleanup()
        await self._transport.cleanup()


class BrowserOutput(BaseOutputTransport):
    def __init__(self, transport: BrowserTransport, params: TransportParams):
        super().__init__(params)
        self._transport = transport
        self._next_audio_time = 0.0

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, InterruptionFrame):
            # The base retains an in-progress audio write; invalidate it as well.
            self._transport.audio_generation += 1
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self._next_audio_time = 0.0
            await self._transport.send("interrupt")

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        generation = self._transport.audio_generation
        loop = asyncio.get_running_loop()
        deadline = max(loop.time(), self._next_audio_time)
        await asyncio.sleep(max(0, deadline - loop.time()))
        sent = await self._transport.send(b"\x01" + frame.audio, audio_generation=generation)
        if generation == self._transport.audio_generation:
            self._next_audio_time = deadline + len(frame.audio) / (24000 * 2)
        return sent

    async def write_video_frame(self, frame: OutputImageRawFrame) -> bool:
        jpeg = await asyncio.to_thread(encode_jpeg, frame)
        return await self._transport.send(b"\x02" + jpeg)
