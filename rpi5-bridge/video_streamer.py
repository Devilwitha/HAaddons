"""Video der Spielkonsole von der USB-Capture-Card (/dev/video0) für Home Assistant.

HTTP-Server (Standard-Port 8766):
  POST /offer         WebRTC-Signaling (aiortc): {"sdp", "type"} -> Antwort {"sdp", "type"}
                      Bringt das Angebot einen Datenkanal "input" mit, kommen die Eingaben direkt darüber.
  GET  /stream.mjpg   MJPEG-Stream (multipart/x-mixed-replace)
  GET  /snapshot.jpg  einzelnes Bild
  GET  /status        Zustand der Capture-Card als JSON

Ton: Bringt das WebRTC-Angebot eine Tonspur mit, nimmt die Bridge den Ton der Capture-Card per arecord
auf (siehe AudioSource). Die Tonspur bekommt eine eigene Stream-ID, damit der Browser das Bild nicht
zugunsten der Lippensynchronität verzögert.

Die Capture-Card wird im MJPEG-Format geöffnet (geringste USB-Latenz). Ist sie nicht da oder
liefert sie kein Bild (Konsole im Standby), wird ein "Kein Signal"-Bild gezeigt und alle
2 Sekunden neu verbunden. device: "test" erzeugt ein Testbild ohne Capture-Card.
"""
import asyncio
import collections
import fractions
import logging
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from aiohttp import web

try:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
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
        # Testbild: liefert True, solange eine Taste gedrückt ist (Reaktionstest ohne Konsole)
        self.input_active = None

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
            if self.input_active is not None and self.input_active():
                cv2.rectangle(frame, (self.width * 2 // 3, 0), (self.width, self.height // 3), (255, 255, 255), -1)
                cv2.putText(frame, "Taste", (self.width * 2 // 3 + 40, self.height // 6 + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
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

    def __init__(self, source, max_fps, stats):
        super().__init__()
        self.source = source
        self.period = 1.0 / max(1, max_fps)
        self.last_id = None
        self.start = None
        self.next_at = 0.0
        self.stats = stats
        self.returned_at = None

    async def recv(self):
        now = time.monotonic()
        if self.start is None:
            self.start = now
        if self.returned_at is not None:
            # Zeit seit der Rückgabe des letzten Bildes = Kodieren + Senden in aiortc
            self.stats.add("encode", now - self.returned_at)
        if now < self.next_at:
            await asyncio.sleep(self.next_at - now)
        await self.source.wait_frame(self.last_id, 0.5)
        frame, frame_id = self.source.current()
        self.last_id = frame_id
        picked = time.monotonic()
        if frame_id != -1 and self.source.frame_time:
            self.stats.add("age", picked - self.source.frame_time)
        self.next_at = picked + self.period
        # Farbumrechnung mit OpenCV (NEON-optimiert) statt im Encoder: spart pro Bild einige Millisekunden
        if frame.shape[0] % 2 == 0 and frame.shape[1] % 2 == 0:
            yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
            video_frame = av.VideoFrame.from_ndarray(yuv, format="yuv420p")
        else:
            video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = int((time.monotonic() - self.start) * VIDEO_CLOCK)
        video_frame.time_base = fractions.Fraction(1, VIDEO_CLOCK)
        done = time.monotonic()
        self.stats.add("convert", done - picked)
        self.stats.frame(done)
        self.returned_at = done
        return video_frame


class StreamStats:
    """Gleitende Messwerte (Millisekunden) und Bildrate für die Messanzeige im Browser."""

    def __init__(self):
        self.values = {}
        self.frames = collections.deque(maxlen=240)

    def add(self, key, seconds):
        ms = seconds * 1000.0
        old = self.values.get(key)
        self.values[key] = ms if old is None else old * 0.9 + ms * 0.1

    def frame(self, now):
        self.frames.append(now)

    def fps(self):
        now = time.monotonic()
        return sum(1 for t in self.frames if now - t <= 1.0)

    def snapshot(self):
        out = {key: round(value, 1) for key, value in self.values.items()}
        out["fps"] = self.fps()
        return out


AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_SAMPLES = 960                 # 20 ms pro Paket (Opus)
AUDIO_BYTES = AUDIO_SAMPLES * AUDIO_CHANNELS * 2
AUDIO_BACKLOG = 3                   # höchstens 60 ms Ton puffern, Älteres wird verworfen


def find_audio_device():
    """Erste USB-Soundkarte mit Aufnahme-Eingang (die Capture-Card) als ALSA-Name."""
    for card in sorted(Path("/proc/asound").glob("card[0-9]*")):
        captures = sorted(card.glob("pcm*c"))
        if not (card / "usbid").exists() or not captures:
            continue
        try:
            card_id = (card / "id").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return f"plughw:CARD={card_id},DEV={captures[0].name[3:-1]}"
    return None


class AudioSource:
    """Ton der Capture-Card über arecord (ALSA), 48 kHz Stereo in Paketen zu 20 ms.

    Läuft nur, solange mindestens ein Browser per WebRTC zuhört. device: "auto" sucht die erste
    USB-Soundkarte mit Eingang, "off" schaltet den Ton ab, "test" erzeugt einen Piepton, solange
    eine Taste gedrückt ist (zum Testen ohne Capture-Card)."""

    def __init__(self, cfg, test_pattern=False):
        device = str((cfg or {}).get("device", "auto")).strip() or "auto"
        self.device = "test" if test_pattern and device == "auto" else device
        self.enabled = self.device != "off"
        self.state = "off" if self.enabled else "disabled"
        self.detail = ""
        self.active_device = ""
        self.input_active = None
        self._queues = set()
        self._task = None

    def subscribe(self):
        queue = asyncio.Queue(maxsize=AUDIO_BACKLOG)
        self._queues.add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())
        return queue

    def unsubscribe(self, queue):
        self._queues.discard(queue)

    def _publish(self, data):
        for queue in list(self._queues):
            if queue.full():
                queue.get_nowait()          # Ton bleibt aktuell statt sich aufzustauen
            queue.put_nowait(data)

    async def _run(self):
        try:
            if self.device == "test":
                await self._run_test()
                return
            while self._queues:
                device = find_audio_device() if self.device == "auto" else self.device
                if not device:
                    self.state = "unavailable"
                    self.detail = "keine Ton-Quelle gefunden (Capture-Card ohne Audio?)"
                    await asyncio.sleep(5)
                    continue
                await self._record(device)
                if self._queues:
                    await asyncio.sleep(RECONNECT_SECONDS)
        finally:
            self.state = "off" if self.enabled else "disabled"
            self.active_device = ""

    async def _record(self, device):
        try:
            proc = await asyncio.create_subprocess_exec(
                "arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(AUDIO_RATE), "-c", str(AUDIO_CHANNELS),
                "-t", "raw", "--buffer-time=40000", "--period-time=10000",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            self.state = "unavailable"
            self.detail = "arecord fehlt (Paket alsa-utils)"
            await asyncio.sleep(10)
            return
        self.state = "ok"
        self.detail = ""
        self.active_device = device
        log.info("Ton-Aufnahme gestartet: %s", device)
        try:
            while self._queues:
                self._publish(await proc.stdout.readexactly(AUDIO_BYTES))
        except asyncio.IncompleteReadError:
            err = (await proc.stderr.read()).decode("utf-8", "replace").strip()
            self.state = "unavailable"
            self.detail = (err.splitlines() or ["arecord beendet"])[-1][:200]
            log.warning("Ton-Aufnahme beendet: %s", self.detail)
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            log.info("Ton-Aufnahme gestoppt")

    async def _run_test(self):
        self.state = "ok"
        self.detail = "Testton"
        self.active_device = "test"
        t = np.arange(AUDIO_SAMPLES) / AUDIO_RATE
        silence = bytes(AUDIO_BYTES)
        phase = 0
        next_at = time.monotonic()
        while self._queues:
            if self.input_active is not None and self.input_active():
                wave = (np.sin(2 * np.pi * 660 * (t + phase * AUDIO_SAMPLES / AUDIO_RATE)) * 8000).astype(np.int16)
                self._publish(np.repeat(wave, AUDIO_CHANNELS).tobytes())
            else:
                self._publish(silence)
            phase += 1
            next_at += AUDIO_SAMPLES / AUDIO_RATE
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))

    def status(self):
        return {"state": self.state, "detail": self.detail, "device": self.active_device,
                "listeners": len(self._queues)}


class AudioTrack(MediaStreamTrack):
    """WebRTC-Tonspur aus der AudioSource."""

    kind = "audio"

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.queue = source.subscribe()
        self.pts = 0

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        data = await self.queue.get()
        frame = av.AudioFrame(format="s16", layout="stereo", samples=AUDIO_SAMPLES)
        frame.planes[0].update(data)
        frame.sample_rate = AUDIO_RATE
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, AUDIO_RATE)
        self.pts += AUDIO_SAMPLES
        return frame

    def stop(self):
        super().stop()
        self.source.unsubscribe(self.queue)


class VideoStreamer:
    def __init__(self, cfg, audio_cfg=None):
        self.cfg = cfg
        self.host = str(cfg.get("host", "0.0.0.0"))
        self.port = int(cfg.get("port", 8766))
        self.mjpeg_fps = int(cfg.get("mjpeg_fps", 30))
        self.webrtc_fps = int(cfg.get("webrtc_fps", 60))
        self.source = FrameSource(cfg)
        self.audio = AudioSource(audio_cfg, test_pattern=self.source.device == "test")
        self.pcs = set()
        # Wird von der Bridge gesetzt: on_channel(channel, pc) für den Eingabe-Datenkanal
        self.on_channel = None
        self.webrtc_stats = StreamStats()
        self.mjpeg_stats = StreamStats()
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
        tracks = []

        @pc.on("connectionstatechange")
        async def on_state():
            log.info("WebRTC %s: %s", request.remote, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                for track in tracks:
                    track.stop()
                await pc.close()
                self.pcs.discard(pc)

        @pc.on("datachannel")
        def on_datachannel(channel):
            if channel.label == "input" and self.on_channel is not None:
                self.on_channel(channel, pc)

        tracks.append(CaptureTrack(self.source, self.webrtc_fps, self.webrtc_stats))
        pc.addTrack(tracks[-1])
        if self.audio.enabled and "m=audio" in offer.sdp:
            tracks.append(AudioTrack(self.audio))
            sender = pc.addTrack(tracks[-1])
            # Eigene Stream-ID: ohne gemeinsame Gruppe gleicht der Browser Bild und Ton nicht ab –
            # sonst würde er das Bild verzögern, bis der (etwas spätere) Ton aufholt.
            sender._stream_id = str(uuid.uuid4())
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
                self.mjpeg_stats.frame(time.monotonic())
                wait = period - (time.monotonic() - started)
                if wait > 0:
                    await asyncio.sleep(wait)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    def stats(self):
        """Messwerte für den Status an den Browser."""
        return {"webrtc": self.webrtc_stats.snapshot() if self.pcs else None,
                "mjpeg_fps": self.mjpeg_stats.fps(),
                "webrtc_clients": len(self.pcs),
                "audio": self.audio.status()}

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
