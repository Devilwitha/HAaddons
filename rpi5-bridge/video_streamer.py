"""Video der Spielkonsole von der USB-Capture-Card (/dev/video0) für Home Assistant.

HTTP-Server (Standard-Port 8766):
  POST /offer         WebRTC-Signaling (aiortc): {"sdp", "type"} -> Antwort {"sdp", "type"}
  GET  /stream.mjpg   MJPEG-Stream (multipart/x-mixed-replace)
  GET  /snapshot.jpg  einzelnes Bild
  GET  /status        Zustand der Capture-Card als JSON

Die Capture-Card wird im MJPEG-Format geöffnet (geringste USB-Latenz). Ist sie nicht da oder
liefert sie kein Bild (Konsole im Standby), wird ein "Kein Signal"-Bild gezeigt und alle
2 Sekunden neu verbunden. device: "test" erzeugt ein Testbild ohne Capture-Card.
"""
import asyncio
import fractions
import logging
import threading
import time

import cv2
import numpy as np
from aiohttp import web

try:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import MediaStreamTrack
    import av
    HAVE_WEBRTC = True
except ImportError:                 # MJPEG funktioniert auch ohne aiortc
    HAVE_WEBRTC = False
    MediaStreamTrack = object

log = logging.getLogger("bridge.video")

RECONNECT_SECONDS = 2.0
NO_SIGNAL_AFTER = 1.5               # so lange ohne Bild = kein Signal
VIDEO_CLOCK = 90000


def set_max_bitrate(bitrate):
    """aiortc begrenzt die Bitrate von VP8/H.264 fest; für 720p etwas mehr erlauben."""
    if not HAVE_WEBRTC or not bitrate:
        return
    try:
        from aiortc.codecs import vpx, h264
        vpx.MAX_BITRATE = int(bitrate)
        vpx.DEFAULT_BITRATE = min(int(bitrate), max(vpx.DEFAULT_BITRATE, int(bitrate) // 2))
        h264.MAX_BITRATE = int(bitrate)
        h264.DEFAULT_BITRATE = min(int(bitrate), max(h264.DEFAULT_BITRATE, int(bitrate) // 2))
    except (ImportError, AttributeError) as err:
        log.debug("Bitrate nicht anpassbar: %s", err)


class FrameSource:
    """Liest Bilder in einem eigenen Thread und hält das jeweils neueste bereit."""

    def __init__(self, cfg):
        self.device = str(cfg.get("device", "/dev/video0"))
        self.width = int(cfg.get("width", 1280))
        self.height = int(cfg.get("height", 720))
        self.fps = int(cfg.get("fps", 60))
        self.fourcc = str(cfg.get("fourcc", "MJPG"))[:4]
        self.jpeg_quality = int(cfg.get("jpeg_quality", 80))
        self.frame = None
        self.frame_id = 0
        self.frame_time = 0.0
        self.state = "unavailable"
        self.detail = "noch nicht gestartet"
        self.actual = (0, 0, 0)
        self._jpeg = (None, -1)
        self._jpeg_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._waiters = set()
        self._measured_fps = 0.0
        self.placeholder = self._make_placeholder()

    # ------------------------------------------------------------------ Thread
    def start(self, loop):
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _publish(self, frame):
        self.frame = frame
        self.frame_id += 1
        now = time.monotonic()
        if self.frame_time:
            dt = now - self.frame_time
            if dt > 0:
                self._measured_fps = self._measured_fps * 0.9 + (1.0 / dt) * 0.1
        self.frame_time = now
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake)

    def _wake(self):
        for fut in list(self._waiters):
            if not fut.done():
                fut.set_result(True)
        self._waiters.clear()

    def _run(self):
        if self.device == "test":
            self._run_test_pattern()
            return
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if not cap.isOpened():
                self.state = "unavailable"
                self.detail = f"{self.device} nicht gefunden"
                cap.release()
                self._stop.wait(RECONNECT_SECONDS)
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                           int(round(cap.get(cv2.CAP_PROP_FPS))))
            log.info("Capture-Card %s geöffnet: %dx%d @ %d fps", self.device, *self.actual)
            self.state = "no_signal"
            self.detail = "warte auf Bild"
            failures = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    failures = 0
                    self.state = "ok"
                    self.detail = ""
                    self._publish(frame)
                    continue
                failures += 1
                if time.monotonic() - self.frame_time > NO_SIGNAL_AFTER:
                    self.state = "no_signal"
                    self.detail = "kein Bild (Konsole im Standby?)"
                if failures > max(30, self.fps):
                    log.warning("Capture-Card liefert keine Bilder mehr - verbinde neu")
                    break
                time.sleep(0.02)
            cap.release()
            self._stop.wait(RECONNECT_SECONDS)

    def _run_test_pattern(self):
        self.state = "ok"
        self.detail = "Testbild"
        self.actual = (self.width, self.height, self.fps)
        period = 1.0 / max(1, self.fps)
        base = np.zeros((self.height, self.width, 3), np.uint8)
        colors = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
                  (255, 0, 255), (0, 0, 255), (255, 0, 0), (30, 30, 30)]
        bar = self.width // len(colors)
        for i, color in enumerate(colors):
            base[:, i * bar:(i + 1) * bar] = color
        n = 0
        while not self._stop.is_set():
            frame = base.copy()
            x = int((n * 8) % self.width)
            cv2.rectangle(frame, (x, self.height // 2 - 40), (x + 80, self.height // 2 + 40), (0, 0, 0), -1)
            cv2.putText(frame, "Game Remote Bridge - Testbild", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
            cv2.putText(frame, time.strftime("%H:%M:%S") + f"  #{n}", (40, self.height - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
            self._publish(frame)
            n += 1
            self._stop.wait(period)

    # ------------------------------------------------------------------ Zugriff
    def _make_placeholder(self):
        img = np.full((self.height, self.width, 3), 16, np.uint8)
        text = "Kein Signal"
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 2.0, 4)[0]
        cv2.putText(img, text, ((self.width - size[0]) // 2, (self.height + size[1]) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.0, (200, 200, 200), 4)
        return img

    def current(self):
        """Neuestes Bild oder das "Kein Signal"-Bild."""
        if self.frame is not None and self.state == "ok":
            return self.frame, self.frame_id
        return self.placeholder, -1

    def jpeg(self):
        frame, frame_id = self.current()
        with self._jpeg_lock:
            if frame_id != -1 and self._jpeg[1] == frame_id:
                return self._jpeg[0]
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            data = buf.tobytes() if ok else b""
            self._jpeg = (data, frame_id)
            return data

    async def wait_frame(self, last_id, timeout):
        """Wartet auf ein neueres Bild als last_id (oder bis timeout)."""
        if self.frame_id != last_id and self.state == "ok":
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.add(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._waiters.discard(fut)

    def status(self):
        w, h, f = self.actual
        return {"state": self.state, "detail": self.detail, "device": self.device,
                "width": w, "height": h, "fps": round(self._measured_fps) if self.state == "ok" else 0,
                "webrtc": HAVE_WEBRTC}


class CaptureTrack(MediaStreamTrack):
    """WebRTC-Videospur mit dem jeweils neuesten Bild, begrenzt auf max_fps."""

    kind = "video"

    def __init__(self, source, max_fps):
        super().__init__()
        self.source = source
        self.period = 1.0 / max(1, max_fps)
        self.last_id = None
        self.start = None
        self.next_at = 0.0

    async def recv(self):
        now = time.monotonic()
        if self.start is None:
            self.start = now
        if now < self.next_at:
            await asyncio.sleep(self.next_at - now)
        await self.source.wait_frame(self.last_id, 0.5)
        frame, frame_id = self.source.current()
        self.last_id = frame_id
        self.next_at = time.monotonic() + self.period
        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = int((time.monotonic() - self.start) * VIDEO_CLOCK)
        video_frame.time_base = fractions.Fraction(1, VIDEO_CLOCK)
        return video_frame


class VideoStreamer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.host = str(cfg.get("host", "0.0.0.0"))
        self.port = int(cfg.get("port", 8766))
        self.mjpeg_fps = int(cfg.get("mjpeg_fps", 30))
        self.webrtc_fps = int(cfg.get("webrtc_fps", 30))
        self.source = FrameSource(cfg)
        self.pcs = set()
        self.runner = None
        set_max_bitrate(cfg.get("webrtc_max_bitrate", 6000000))

    # ------------------------------------------------------------------ HTTP
    async def handle_offer(self, request):
        if not HAVE_WEBRTC:
            return web.json_response({"error": "aiortc ist nicht installiert"}, status=503)
        try:
            params = await request.json()
            offer = RTCSessionDescription(sdp=str(params["sdp"]), type=str(params["type"]))
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "ungültiges Angebot"}, status=400)

        # Ohne STUN-Server: Das Video läuft im Heimnetz direkt. Der Standard-STUN-Server von
        # aiortc (Google) würde den Verbindungsaufbau um Sekunden verzögern.
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("WebRTC %s: %s", request.remote, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                self.pcs.discard(pc)

        pc.addTrack(CaptureTrack(self.source, self.webrtc_fps))
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    async def handle_mjpeg(self, request):
        boundary = "frame"
        response = web.StreamResponse(headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={boundary}",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        period = 1.0 / max(1, self.mjpeg_fps)
        last_id = None
        loop = asyncio.get_running_loop()
        try:
            while True:
                started = time.monotonic()
                await self.source.wait_frame(last_id, 1.0)
                _frame, last_id = self.source.current()
                data = await loop.run_in_executor(None, self.source.jpeg)
                await response.write(
                    f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode()
                    + data + b"\r\n")
                wait = period - (time.monotonic() - started)
                if wait > 0:
                    await asyncio.sleep(wait)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def handle_snapshot(self, _request):
        data = await asyncio.get_running_loop().run_in_executor(None, self.source.jpeg)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def handle_status(self, _request):
        return web.json_response(self.source.status())

    # ------------------------------------------------------------------ Start / Stopp
    async def start(self):
        self.source.start(asyncio.get_running_loop())
        app = web.Application(client_max_size=256 * 1024)
        app.router.add_post("/offer", self.handle_offer)
        app.router.add_get("/stream.mjpg", self.handle_mjpeg)
        app.router.add_get("/snapshot.jpg", self.handle_snapshot)
        app.router.add_get("/status", self.handle_status)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()
        log.info("Video-Server auf %s:%d (WebRTC %s)", self.host, self.port, "an" if HAVE_WEBRTC else "aus - aiortc fehlt")

    async def stop(self):
        await asyncio.gather(*(pc.close() for pc in list(self.pcs)), return_exceptions=True)
        self.pcs.clear()
        if self.runner:
            await self.runner.cleanup()
        self.source.stop()
