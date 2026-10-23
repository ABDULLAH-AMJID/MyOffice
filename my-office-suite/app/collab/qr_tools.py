"""QR code image & share-URL builders for LAN collaboration rooms."""
from __future__ import annotations

import io
import urllib.parse
import qrcode
from PySide6.QtGui import QImage, QPixmap


def build_http_share_url(host: str, port: int, room_id: str, pin: str = "") -> str:
    """Universal LAN URL: works in ANY browser (phone/desktop) AND when pasted into MyOffice Join Dialog!"""
    base = f"http://{host}:{int(port)}/f/{urllib.parse.quote(room_id, safe='')}"
    if pin:
        return f"{base}?pin={urllib.parse.quote(pin, safe='')}"
    return base


def build_ws_url(host: str, port: int, room_id: str, pin: str = "") -> str:
    base = f"ws://{host}:{int(port)}/{urllib.parse.quote(room_id, safe='')}"
    if pin:
        return f"{base}?pin={urllib.parse.quote(pin, safe='')}"
    return base


def build_app_protocol_url(host: str, port: int, room_id: str, filename: str = "", pin: str = "") -> str:
    params = {"host": host, "port": str(int(port)), "room": room_id}
    if filename:
        params["name"] = filename
    if pin:
        params["pin"] = pin
    return "myoffice://collab?" + urllib.parse.urlencode(params)


def parse_collab_target(raw: str, fallback_room: str = "default") -> tuple[str, int, str, str]:
    """Parse ANY shared collaboration link (`http://...`, `ws://...`, `myoffice://...`, or `ip:port/room`)
    into ``(host, port, room_id, pin)``.
    """
    txt = (raw or "").strip()
    if not txt:
        raise ValueError("Paste a Collaboration Link, HTTP URL, or ws:// address.")

    if txt.lower().startswith("myoffice://"):
        u = urllib.parse.urlparse(txt)
        q = urllib.parse.parse_qs(u.query)
        ws_full = (q.get("ws") or [""])[0].strip()
        if ws_full:
            wu = urllib.parse.urlparse(ws_full)
            room = (q.get("room") or [urllib.parse.unquote(wu.path.strip("/")) or fallback_room])[0]
            pin = (q.get("pin") or [""])[0].strip()
            return wu.hostname or "127.0.0.1", int(wu.port or 8765), room, pin
        host = (q.get("host") or [""])[0].strip()
        port = int((q.get("port") or ["8765"])[0])
        room = (q.get("room") or [fallback_room])[0].strip() or fallback_room
        pin = (q.get("pin") or [""])[0].strip()
        if not host:
            raise ValueError(f"Invalid MyOffice link: {txt}")
        return host, port, room, pin

    if "://" not in txt:
        txt = "http://" + txt

    u = urllib.parse.urlparse(txt)
    host = u.hostname or ""
    if not host:
        raise ValueError(f"Could not extract host IP from {raw!r}")
    port = u.port or (443 if u.scheme in ("https", "wss") else 80)
    q = urllib.parse.parse_qs(u.query)
    pin = (q.get("pin") or [""])[0].strip()

    parts = [urllib.parse.unquote(p) for p in (u.path or "").split("/") if p]
    if len(parts) >= 2 and parts[0] == "f":
        room = parts[1]
    elif parts:
        room = parts[0]
    else:
        room = fallback_room
    return host, int(port), room, pin


def make_qr_png_bytes(data: str, box_size: int = 8, border: int = 2) -> bytes:
    """Render a high-contrast QR code PNG with M error correction for instant phone camera scanning."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(4, box_size),
        border=max(1, border),
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#111827", back_color="#FFFFFF").convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_qr_pixmap(data: str, target_px: int = 220) -> QPixmap:
    from PySide6.QtCore import Qt

    png = make_qr_png_bytes(data, box_size=10, border=2)
    qimg = QImage.fromData(png, "PNG")
    pm = QPixmap.fromImage(qimg)
    return pm.scaled(
        target_px,
        target_px,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
