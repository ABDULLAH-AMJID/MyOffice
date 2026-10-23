"""Single-instance support: a second launch forwards its files to the running window."""
from __future__ import annotations

import getpass
import hashlib
import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app.services.paths import user_dir


def server_name() -> str:
    h = hashlib.sha1(str(user_dir()).encode("utf-8")).hexdigest()[:10]
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"MyOffice-{user}-{h}"


def send_to_running(message: dict, timeout_ms: int = 600) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(message).encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    messageReceived = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        name = server_name()
        if not self.server.listen(name):
            QLocalServer.removeServer(name)  # stale socket after a crash
            return self.server.listen(name)
        return True

    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))

    def _read(self, sock: QLocalSocket):
        data = bytes(sock.readAll()).decode("utf-8", "replace")
        for line in data.splitlines():
            try:
                self.messageReceived.emit(json.loads(line))
            except ValueError:
                continue
