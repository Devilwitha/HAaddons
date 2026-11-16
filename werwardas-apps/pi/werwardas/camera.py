"""Foto aufnehmen – Raspberry-Pi-Kamera, USB-Webcam, ESP32-CAM (HTTP) oder Datei."""

from __future__ import annotations

import io
import logging
import time

from PIL import Image

log = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


def _has_picamera2() -> bool:
    try:
        import picamera2  # noqa: F401
    except ImportError:
        return False
    return True


class Camera:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        cam_type = cfg["type"]
        if cam_type == "auto":
            if cfg.get("url"):
                cam_type = "http"
            elif _has_picamera2():
                cam_type = "picamera2"
            else:
                cam_type = "usb"
        self.type = cam_type
        log.info("Kamera-Typ: %s", self.type)

    def capture(self) -> Image.Image:
        """Nimmt ein Foto auf und gibt es als PIL-Bild (RGB) zurück."""
        grab = {
            "picamera2": self._capture_picamera2,
            "usb": self._capture_usb,
            "http": self._capture_http,
            "file": self._capture_file,
        }.get(self.type)
        if grab is None:
            raise CameraError(f"Unbekannter Kamera-Typ: {self.type}")
        try:
            img = grab()
        except CameraError:
            raise
        except Exception as exc:  # Hardware-Fehler sollen den Dienst nicht beenden
            raise CameraError(f"Foto fehlgeschlagen ({self.type}): {exc}") from exc
        img = img.convert("RGB")
        if self.cfg.get("rotation"):
            img = img.rotate(-int(self.cfg["rotation"]), expand=True)
        return img

    def _capture_picamera2(self) -> Image.Image:
        from picamera2 import Picamera2

        # Kamera nur kurz öffnen: so bleibt sie zwischen den Fotos aus.
        cam = Picamera2()
        try:
            config = cam.create_still_configuration(
                main={"size": (int(self.cfg["width"]), int(self.cfg["height"]))}
            )
            cam.configure(config)
            cam.start()
            time.sleep(float(self.cfg["warmup_seconds"]))  # Belichtung einpendeln lassen
            return cam.capture_image("main")
        finally:
            cam.close()

    def _capture_usb(self) -> Image.Image:
        try:
            import cv2
        except ImportError as exc:
            raise CameraError("Für USB-Kameras wird python3-opencv benötigt") from exc
        cap = cv2.VideoCapture(int(self.cfg["usb_index"]))
        try:
            if not cap.isOpened():
                raise CameraError("USB-Kamera nicht gefunden")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.cfg["width"]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.cfg["height"]))
            deadline = time.time() + float(self.cfg["warmup_seconds"])
            ok, frame = cap.read()
            while time.time() < deadline:
                ok, frame = cap.read()
            if not ok:
                raise CameraError("USB-Kamera liefert kein Bild")
            return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            cap.release()

    def _capture_http(self) -> Image.Image:
        import requests

        url = self.cfg["url"]
        if not url:
            raise CameraError("camera.url ist leer")
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))

    def _capture_file(self) -> Image.Image:
        return Image.open(self.cfg["path"])


def to_jpeg(img: Image.Image, max_width: int | None = None, quality: int = 85) -> bytes:
    if max_width and img.width > max_width:
        h = round(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
