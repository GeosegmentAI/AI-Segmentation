
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer

from .api import login_page_url

PAGE = ("<html><head><meta charset='utf-8'></head>"
        "<body style='font-family:sans-serif;text-align:center;margin-top:15%'>{}</body></html>")


class LoginServer(QObject):
    token_received = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_connection)
        self.state = None

    def start(self):
        """Pokreće lokalni server i vraća URL koji treba otvoriti u browseru."""
        self.stop()
        if not self.server.listen(QHostAddress.LocalHost, 0):  # 0 = slobodan port
            raise RuntimeError("Could not start local login server")
        self.state = secrets.token_urlsafe(16)
        redirect = f"http://127.0.0.1:{self.server.serverPort()}/callback"
        return f"{login_page_url()}?{urlencode({'redirect_uri': redirect, 'state': self.state})}"

    def stop(self):
        if self.server.isListening():
            self.server.close()

    def _on_connection(self):
        sock = self.server.nextPendingConnection()
        sock.readyRead.connect(lambda: self._handle(sock))

    def _handle(self, sock):
        request_line = bytes(sock.readAll()).decode(errors="ignore").split("\r\n", 1)[0]
        parts = request_line.split(" ")  # GET /callback?token=..&state=.. HTTP/1.1
        if len(parts) < 2 or not parts[1].startswith("/callback"):
            return self._reply(sock, "404 Not Found", "Not found")  #  /favicon.ico

        q = parse_qs(urlparse(parts[1]).query)
        token, state = q.get("token", [""])[0], q.get("state", [""])[0]
        if not token or state != self.state:
            self._reply(sock, "400 Bad Request", "<h2>Sign in failed</h2><p>Please try again from QGIS.</p>")
            self.failed.emit("Invalid response from the sign in page")
            return

        self._reply(sock, "200 OK", "<h2 style=\'color:#2563EB\'>GeoSegment AI</h2><h3>You are signed in ✔</h3><p>You can close this tab and return to QGIS.</p>")
        self.stop()
        self.token_received.emit(token)

    def _reply(self, sock, status, body):
        html = PAGE.format(body).encode()
        head = (f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(html)}\r\nConnection: close\r\n\r\n").encode()
        sock.write(head + html)
        sock.flush()
        sock.disconnectFromHost()
