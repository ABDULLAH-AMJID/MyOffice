"""WebSocket client thread for the y-websocket protocol.

Runs the network loop in a dedicated QThread while keeping the Yjs CRDT document on the Qt GUI thread.
Reconnects automatically with smooth backoff if Wi-Fi briefly drops.
"""
from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class WsClientThread(QThread):
    messageReceived = Signal(bytes)
    connected = Signal()
    disconnected = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self._stopping = False
        self._queue: asyncio.Queue | None = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._queue = asyncio.Queue()
        try:
            self.loop.run_until_complete(self._main())
        finally:
            self.loop.close()

    async def _main(self):
        from websockets.asyncio.client import connect

        delay = 0.5
        while not self._stopping:
            try:
                async with connect(self.url, max_size=128 * 1024 * 1024, open_timeout=10) as ws:
                    self.ws = ws
                    delay = 0.5
                    self.connected.emit()
                    sender = asyncio.create_task(self._sender(ws))
                    try:
                        async for msg in ws:
                            if isinstance(msg, (bytes, bytearray)):
                                self.messageReceived.emit(bytes(msg))
                    finally:
                        sender.cancel()
                        self.ws = None
                if not self._stopping:
                    self.disconnected.emit("Connection closed by the server")
            except Exception as exc:
                self.ws = None
                if self._stopping:
                    break
                self.disconnected.emit(f"{exc.__class__.__name__}: {exc}")
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(15.0, delay * 2)

    async def _sender(self, ws):
        while True:
            data = await self._queue.get()
            await ws.send(data)

    def send(self, data: bytes):
        if self.loop is not None and self._queue is not None:
            try:
                self.loop.call_soon_threadsafe(self._queue.put_nowait, data)
            except RuntimeError:
                pass

    def stop(self):
        self._stopping = True
        if self.loop is not None:
            async def _close():
                if self.ws is not None:
                    await self.ws.close()
                for t in asyncio.all_tasks():
                    if t is not asyncio.current_task():
                        t.cancel()
            try:
                asyncio.run_coroutine_threadsafe(_close(), self.loop)
            except RuntimeError:
                pass
        self.wait(5000)
