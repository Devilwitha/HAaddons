"""Video der Spielkonsole von der USB-Capture-Card (/dev/video0) für Home Assistant.

HTTP-Server (Standard-Port 8766):
  POST /offer         WebRTC-Signaling (aiortc): {"sdp", "type"} -> Antwort {"sdp", "type"}
                      Bringt das Angebot einen Datenkanal "input" mit, kommen die Eingaben direkt darüber.
  GET  /frames        WebSocket mit Einzelbildern (JPEG), der Browser dekodiert. Jede Nachricht: 4 Byte Länge
                      + JSON-Kopf {id, age, enc, pt} + JPEG. Der Browser bestätigt gezeichnete Bilder mit
                      {"type": "ack"}; höchstens JPEG_WINDOW Bilder sind unterwegs – so passt sich die Bildrate
                      der Leitung an, statt dass sich Bilder aufstauen (auch über Home Assistant/unterwegs).
  GET  /stream.mjpg   MJPEG-Stream (multipart/x-mixed-replace)
  GET  /snapshot.jpg  einzelnes Bild
  GET  /status        Zustand der Capture-Card als JSON

Ton: Bringt das WebRTC-Angebot eine Tonspur mit, nimmt die Bridge den Ton der Capture-Card per arecord
auf (siehe AudioSource). Die Tonspur bekommt eine eigene Stream-ID, damit der Browser das Bild nicht
zugunsten der Lippensynchronität verzögert.

Die Capture-Card wird im MJPEG-Format geöffnet (geringste USB-Latenz). Liefert sie wirklich JPEG, werden
die Bilder unverändert durchgereicht (jpeg_passthrough): für Einzelbilder kodiert der Pi dann gar nichts,
dekodiert wird nur, wenn ein WebRTC-Zuschauer da ist. Karten ohne MJPEG (z. B. Cam Link: YUYV) werden
umgerechnet und für Einzelbilder als JPEG kodiert (libjpeg-turbo, deutlich billiger als VP8).
Bietet die Karte NV12 (z. B. Cam Link 4K), wird NV12 roh gelesen: halb so viele Daten wie BGR, und für WebRTC
entsteht das I420-Bild des Encoders direkt daraus (ohne Umweg über BGR). Liefert die Karte ein größeres Bild als
die gewählte Bildqualität (die Cam Link gibt immer das HDMI-Signal aus, z. B. 1080p), verkleinert die Bridge es
vor dem Kodieren. Ist sie nicht da oder
liefert sie kein Bild (Konsole im Standby), wird ein "Kein Signal"-Bild gezeigt und alle
2 Sekunden neu verbunden. device: "test" erzeugt ein Testbild ohne Capture-Card.

Bildqualität (MODES) lässt sich im Browser umschalten und wird in state_file gemerkt. „Capture-Card neu
starten“ schließt die Karte, setzt ihren USB-Anschluss zurück (USBDEVFS_RESET, sonst über „authorized“ in sysfs),
wartet, bis sie sich wieder angemeldet hat, und öffnet sie neu.
"""
import asyncio
import collections
import concurrent.futures
import errno
import fractions
import glob
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from recorder import REC

try:
    import fcntl                    # nur unter Linux (USB-Reset)
except ImportError:
    fcntl = None

# Ohne HDMI-Signal liefert die Capture-Card keine Bilder; OpenCV wartet dann pro Leseversuch 10 s (select) und
# schreibt jedes Mal eine Warnung ins Protokoll. 2 s reichen, „kein Bild“ wird so schneller erkannt; die Warnungen
# ersetzt die Bridge durch eine eigene Meldung beim Wechsel. Muss vor dem ersten Öffnen gesetzt sein (Sekunden).
os.environ.setdefault("OPENCV_VIDEOIO_V4L_SELECT_TIMEOUT", "2")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from aiohttp import web  # noqa: E402

try:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
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
JPEG_WINDOW = 2                     # so viele Einzelbilder dürfen unbestätigt unterwegs sein
JPEG_PIPELINE = 2                   # so viele Einzelbilder kodiert der Pi gleichzeitig (1080p-JPEG dauert länger als 17 ms)
CACHE_ENTRIES = 4                   # so viele Bilder pro Art (BGR, I420, JPEG) bleiben im Zwischenspeicher


USBDEVFS_RESET = 0x5514              # _IO('U', 20)
REAPPEAR_SECONDS = 15.0             # so lange darf sich die Karte nach dem USB-Reset neu anmelden
BLANK_SPREAD = 12                   # Helligkeitsunterschied im Bild darunter = einfarbig (schwarz)
BLANK_SECONDS = 5.0                 # so lange einfarbig = vermutlich kein HDMI-Signal (Konsole im Standby)

# Bildqualität: Aufnahme (Breite, Höhe, fps der Capture-Card), gesendete Bilder pro Sekunde (send: Video/WebRTC,
# jpeg: Einzelbilder, Vorgabe wie send) und höchste Bitrate. 1080p kodiert der Pi 5 als Video (H.264/VP8, Software)
# nur mit etwa 30 Bildern pro Sekunde; JPEG ist viel weniger Arbeit, daher 1080p60 nur für Einzelbilder.
MODES = {
    "720p60": {"label": "720p · 60 fps (schnell)", "width": 1280, "height": 720, "fps": 60, "send": 60,
               "bitrate": 6000000},
    "1080p30": {"label": "1080p · 30 fps (scharf)", "width": 1920, "height": 1080, "fps": 30, "send": 30,
                "bitrate": 10000000},
    "1080p60": {"label": "1080p · 60 fps (nur Einzelbilder, Video 30 fps)", "width": 1920, "height": 1080,
                "fps": 60, "send": 30, "jpeg": 60, "jpeg_quality": 75, "bitrate": 10000000},
}


def set_bitrate(max_bitrate, min_bitrate=0, fixed=False):
    """aiortc begrenzt die Bitrate von VP8/H.264 fest und startet niedrig. Die Schätzung des Browsers (REMB)
    hält das Bild sonst lange unscharf; im Heimnetz ist eine Untergrenze von einigen Mbit/s kein Problem.

    fixed: Untergrenze = Obergrenze. aiortc legt den Encoder bei jeder Bitraten-Änderung über 10 % neu an –
    das erzwingt ein großes Schlüsselbild und lässt das Bild kurz hängen. Mit fester Bitrate passiert das nie."""
    if not HAVE_WEBRTC or not max_bitrate:
        return
    try:
        from aiortc.codecs import vpx, h264
        top = int(max_bitrate)
        low = top if fixed else max(0, min(int(min_bitrate or 0), top))
        for mod in (vpx, h264):
            mod.MAX_BITRATE = top
            if low:
                mod.MIN_BITRATE = low
            mod.DEFAULT_BITRATE = top if fixed else min(top, max(low, top // 2))
    except (ImportError, AttributeError) as err:
        log.debug("Bitrate nicht anpassbar: %s", err)


def use_all_cores(limit=4):
    """aiortc kodiert VP8 auf einem Pi 5 (4 Kerne) bei 720p und 1080p mit nur 2 Threads. Mit allen Kernen
    sinkt die Zeit pro Bild deutlich – wichtig für gleichmäßige 60 fps."""
    if not HAVE_WEBRTC:
        return
    try:
        from aiortc.codecs import vpx
        original = vpx.number_of_threads

        def number_of_threads(pixels, cpus):
            if pixels > 640 * 480 and cpus >= 3:
                return max(1, min(cpus, int(limit)))
            return original(pixels, cpus)

        vpx.number_of_threads = number_of_threads
    except (ImportError, AttributeError) as err:
        log.debug("Threads nicht anpassbar: %s", err)


# Priorität (nice) der Threads, die VP8 kodieren. Höher als die übrige Bridge (-10): bei aufwendigen Bildern rechnet
# libvpx mit allen Kernen am Anschlag - dann sollen Eingaben, nxbt und das Einlesen der Karte trotzdem sofort drankommen
# und nur das Video Bilder auslassen. None = unverändert.
ENCODE_NICE = None


def set_encode_priority(value):
    global ENCODE_NICE
    try:
        ENCODE_NICE = None if value in (None, "", "off") else max(-20, min(19, int(value)))
    except (TypeError, ValueError):
        ENCODE_NICE = None


def _lower_thread_priority():
    """Setzt den aufrufenden Thread auf ENCODE_NICE, wenn er höher priorisiert ist; gibt den alten Wert zurück.
    libvpx legt seine Arbeits-Threads beim ersten Kodieren aus diesem Thread an - sie erben den Wert dauerhaft."""
    if ENCODE_NICE is None or not hasattr(os, "setpriority"):
        return None
    try:
        tid = threading.get_native_id()
        old = os.getpriority(os.PRIO_PROCESS, tid)
        if old >= ENCODE_NICE:
            return None
        os.setpriority(os.PRIO_PROCESS, tid, ENCODE_NICE)
        return old
    except (OSError, AttributeError):
        return None


def _restore_thread_priority(old):
    if old is None:
        return
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), old)
    except OSError:
        pass


class _EncoderCodec:
    """Hülle um den Encoder von aiortc (libvpx/libx264): ergänzt beim Setzen der Optionen eigene Werte, kodiert mit
    niedrigerer Priorität (ENCODE_NICE) und misst für die Aufzeichnung."""

    def __init__(self, codec, kind):
        object.__setattr__(self, "_codec", codec)
        object.__setattr__(self, "_kind", kind)

    def __getattr__(self, name):
        return getattr(self._codec, name)

    def __setattr__(self, name, value):
        if name == "options" and isinstance(value, dict):
            value = dict(value)
            if self._kind == "vp8" and VP8_SPEED is not None:
                value["cpu-used"] = str(VP8_SPEED)
            elif self._kind == "h264":
                # aiortc nimmt x264 mit Standard-Preset "medium" - auf dem Pi viel zu langsam für Echtzeit.
                # Level 4.2 erlaubt 1080p60 (aiortc schreibt 3.1 = höchstens 720p30). Datenrate gedeckelt
                # (VBV), damit einzelne Bilder nicht riesig werden.
                bitrate = int(getattr(self._codec, "bit_rate", 0) or 0)
                # g: kein Schlüsselbild alle ~4 s von selbst (groß, kostet Zeit) - der Browser fordert bei Bedarf eins an
                value.update({"preset": H264_PRESET, "tune": "zerolatency", "level": "42",
                              "threads": str(H264_THREADS), "g": "3000"})
                if bitrate:
                    value.update({"maxrate": str(bitrate), "bufsize": str(bitrate // 2)})
        setattr(self._codec, name, value)

    def encode(self, frame=None):
        old = _lower_thread_priority()
        start = time.perf_counter()
        try:
            packets = self._codec.encode(frame)
        finally:
            _restore_thread_priority(old)
        if REC.active:
            ms = (time.perf_counter() - start) * 1000.0
            size = sum(p.size for p in packets)
            REC.sample("enc", ms)
            REC.count("bytes", size)
            if any(p.is_keyframe for p in packets):
                REC.count("kf")
                REC.event("keyframe", f"Schlüsselbild: {size / 1024:.0f} KB, {ms:.0f} ms kodiert")
            elif ms > max(25.0, FRAME_BUDGET_MS):          # länger als die Zeit pro Bild (60 fps: 17 ms, 30 fps: 33 ms)
                REC.event("encode", f"langsames Bild: {ms:.0f} ms kodiert ({size / 1024:.0f} KB, "
                                    f"Zeit pro Bild {FRAME_BUDGET_MS:.0f} ms)")
        return packets


class _CodecAv:
    """Ersatz für das Modul "av" in aiortc.codecs.vpx/h264: alles wie av, nur CodecContext.create liefert für die
    Encoder die Hülle _EncoderCodec."""

    def __init__(self, real):
        self._real = real
        real_ctx = real.CodecContext

        class CodecContext:
            @staticmethod
            def create(name, mode=None):
                codec = real_ctx.create(name, mode)
                if mode == "w" and name in ("libvpx", "libx264"):
                    kind = "vp8" if name == "libvpx" else "h264"
                    detail = f"cpu-used {VP8_SPEED}" if kind == "vp8" else f"x264 {H264_PRESET}"
                    REC.event("encoder", f"{kind.upper()}-Encoder angelegt ({detail}) - beginnt mit Schlüsselbild")
                    return _EncoderCodec(codec, kind)
                return codec

        self.CodecContext = CodecContext

    def __getattr__(self, name):
        return getattr(self._real, name)


# Einstellungen der Encoder (gelten für neu angelegte Encoder)
VP8_SPEED = None
H264_PRESET = "superfast"
H264_THREADS = 4
FRAME_BUDGET_MS = 1000.0 / 60       # Zeit pro Bild der gewählten Bildqualität (für „langsames Bild“)
HAVE_X264 = False
if HAVE_WEBRTC:
    try:
        HAVE_X264 = "libx264" in av.codecs_available
    except AttributeError:
        HAVE_X264 = False


def _install_codec_hooks():
    if not HAVE_WEBRTC:
        return
    try:
        from aiortc.codecs import h264, vpx
        for mod in (vpx, h264):
            if not isinstance(mod.av, _CodecAv):
                mod.av = _CodecAv(mod.av)
    except (ImportError, AttributeError) as err:
        log.debug("Encoder nicht anpassbar: %s", err)


def set_vp8_speed(speed=-12):
    """Geschwindigkeit des VP8-Encoders. aiortc nimmt fest -6 (Wert von libwebrtc für x86-PCs); libwebrtc
    selbst nutzt auf ARM-Geräten -12. Negativ = libvpx passt die Geschwindigkeit an die Zeit pro Bild an,
    je größer der Betrag, desto weniger Rechenzeit darf ein Bild kosten."""
    global VP8_SPEED
    try:
        VP8_SPEED = None if speed in (None, "", "aiortc") else int(speed)
    except (TypeError, ValueError):
        VP8_SPEED = None
    _install_codec_hooks()


def set_h264(preset="superfast", threads=4, fps=30):
    """x264 für H.264: auf dem Pi 5 etwa doppelt so schnell wie VP8 bei gleicher Datenrate (NEON-optimiert, verteilt
    jedes Bild auf alle Kerne). fps: Bildrate für die Ratensteuerung (aiortc nimmt fest 30)."""
    global H264_PRESET, H264_THREADS
    H264_PRESET = str(preset or "superfast")
    try:
        H264_THREADS = max(1, min(8, int(threads)))
    except (TypeError, ValueError):
        H264_THREADS = 4
    set_h264_fps(fps)
    _install_codec_hooks()


def set_h264_fps(fps):
    global FRAME_BUDGET_MS
    try:
        FRAME_BUDGET_MS = 1000.0 / max(1, int(fps))
    except (TypeError, ValueError):
        pass
    if not HAVE_WEBRTC:
        return
    try:
        from aiortc.codecs import h264
        h264.MAX_FRAME_RATE = max(1, int(fps))
    except (ImportError, AttributeError, ValueError):
        pass


def codec_preferences(name):
    """Video-Codecs von aiortc mit dem gewünschten ("h264"/"vp8") zuerst. aiortc sendet mit dem ersten Codec, den
    auch der Browser kann; kann er den gewünschten nicht, bleibt der nächste (z. B. VP8)."""
    caps = RTCRtpSender.getCapabilities("video").codecs
    first = [c for c in caps if c.mimeType.lower() == "video/" + name.lower()]
    return first + [c for c in caps if c not in first]


def answer_codec(sdp):
    """Codec, mit dem gesendet wird: erstes Format der Video-Zeile in der Antwort."""
    names = {}
    first = None
    in_video = False
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
            if in_video and first is None:
                parts = line.split(" ")
                first = parts[3] if len(parts) > 3 else None
        elif in_video and line.startswith("a=rtpmap:"):
            pt, _, rest = line[9:].partition(" ")
            names[pt] = rest.split("/")[0]
    return names.get(first)


def pace(next_at, picked, period):
    """Nächster Sendezeitpunkt im festen Raster. Die Bilder der Karte kommen nicht auf die Millisekunde genau;
    ein Raster ab dem letzten Senden würde bei 60 von 60 fps immer wieder ein Bild auslassen (Ruckler)."""
    if next_at and picked - next_at < period:
        return max(next_at + period, picked + period * 0.5)
    return picked + period


def _is_usb_video(node):
    """/sys/class/video4linux/videoN gehört zu einem USB-Gerät und ist dessen erster Videoeingang."""
    try:
        if "/usb" not in os.path.realpath(os.path.join(node, "device")).replace(os.sep, "/"):
            return False
        index = Path(node, "index")
        return not index.exists() or index.read_text().strip() == "0"
    except OSError:
        return False


def find_capture_device(dev_root="/dev", sys_root="/sys"):
    """Erste USB-Capture-Card (UVC). Die Nummer /dev/videoN ändert sich nach Umstecken, Neustart oder
    USB-Reset; der Pi 5 legt außerdem eigene Videogeräte an. Darum: fester Name unter /dev/v4l/by-id,
    sonst das erste USB-Videogerät in /sys/class/video4linux."""
    by_id = sorted(glob.glob(os.path.join(dev_root, "v4l", "by-id", "usb-*-video-index0")))
    if by_id:
        return by_id[0]
    nodes = glob.glob(os.path.join(sys_root, "class", "video4linux", "video*"))
    nodes.sort(key=lambda n: int("".join(ch for ch in os.path.basename(n) if ch.isdigit()) or 0))
    for node in nodes:
        if _is_usb_video(node):
            return dev_root.rstrip("/\\") + "/" + os.path.basename(node)
    return None


def fourcc_name(value):
    code = int(value or 0)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")


def as_jpeg(frame):
    """Rohdaten der Capture-Card (CAP_PROP_CONVERT_RGB = 0) als JPEG-Bytes, sonst None."""
    if frame is None or frame.dtype != np.uint8 or frame.ndim > 2 or frame.size < 4:
        return None
    flat = frame.reshape(-1)
    if flat[0] != 0xFF or flat[1] != 0xD8:
        return None
    return flat.tobytes()


def nv12_to_i420(y, uv):
    """Y-Ebene + verschränkte UV-Ebene (h/2, w/2, 2) -> I420 (h*3/2, w), wie es PyAV als yuv420p erwartet."""
    h, w = y.shape
    out = np.empty((h * 3 // 2, w), np.uint8)
    out[:h] = y
    chroma = out[h:].reshape(-1)
    quarter = (h // 2) * (w // 2)
    chroma[:quarter] = uv[..., 0].reshape(-1)
    chroma[quarter:] = uv[..., 1].reshape(-1)
    return out


def usb_device_dir(video_device):
    """sysfs-Ordner des USB-Geräts zu /dev/videoN (der mit busnum/devnum)."""
    name = os.path.basename(os.path.realpath(video_device))
    path = os.path.realpath(f"/sys/class/video4linux/{name}/device")
    while not os.path.exists(os.path.join(path, "busnum")) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path if os.path.exists(os.path.join(path, "busnum")) else None


def usb_reauthorize(usb_dir):
    """USB-Gerät ab- und wieder anmelden (sysfs „authorized“) - wirkt wie Aus- und Einstecken."""
    path = os.path.join(usb_dir, "authorized")
    try:
        with open(path, "w") as fh:
            fh.write("0")
        time.sleep(0.5)
        with open(path, "w") as fh:
            fh.write("1")
    except OSError as err:
        log.info("Capture-Card über %s neu anmelden fehlgeschlagen: %s", path, err)
        return False
    return True


def usb_reset(usb_dir):
    """USB-Gerät zurücksetzen (wie Aus- und Einstecken). Braucht Root-Rechte.

    Manche Karten (z. B. Cam Link 4K) melden sich beim Zurücksetzen komplett ab und neu an – der Kernel
    antwortet dann mit ENODEV. Das ist kein Fehler: die Karte kommt unter neuer Gerätenummer zurück."""
    if fcntl is None:
        return False, "USB-Reset gibt es nur unter Linux"
    if not usb_dir or not os.path.isdir(usb_dir):
        return False, "USB-Anschluss der Capture-Card nicht gefunden (Karte abgesteckt?)"
    try:
        bus = int(Path(usb_dir, "busnum").read_text())
        dev = int(Path(usb_dir, "devnum").read_text())
        fd = os.open(f"/dev/bus/usb/{bus:03d}/{dev:03d}", os.O_WRONLY)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
        return True, "Capture-Card neu gestartet"
    except OSError as err:
        if err.errno == errno.ENODEV:
            return True, "Capture-Card meldet sich neu am USB an"
        reason = err
    except ValueError as err:
        reason = err
    log.info("USB-Reset der Capture-Card ging nicht (%s) - melde sie über sysfs neu an", reason)
    if usb_reauthorize(usb_dir):
        return True, "Capture-Card neu angemeldet"
    return False, f"USB-Reset fehlgeschlagen: {reason}"


class FrameSource:
    """Liest Bilder in einem eigenen Thread und hält das jeweils neueste bereit."""

    def __init__(self, cfg):
        self.device = str(cfg.get("device", "auto"))
        self.current_device = self.device       # tatsächlich geöffnetes Gerät
        self._fallback_logged = None
        self.width = int(cfg.get("width", 1280))
        self.height = int(cfg.get("height", 720))
        self.fps = int(cfg.get("fps", 60))
        self.fourcc = str(cfg.get("fourcc", "MJPG"))[:4]
        self.jpeg_quality = int(cfg.get("jpeg_quality", 80))
        self.allow_passthrough = bool(cfg.get("jpeg_passthrough", True))
        self.allow_nv12 = bool(cfg.get("nv12", True))
        # Bildpuffer im Treiber. Mit nur einem Puffer hat die Karte keinen Platz für das nächste Bild, solange die
        # Bridge das aktuelle kopiert - bei 60 fps gehen so immer wieder Bilder verloren (~40-45 fps). Der
        # Lese-Thread holt die Bilder sofort ab, die Puffer füllen sich also nicht (keine zusätzliche Verzögerung).
        self.buffers = max(2, min(8, int(cfg.get("capture_buffers", 3))))
        self.format = ""                       # Bildformat, das die Karte tatsächlich liefert
        self.passthrough = False               # JPEG der Karte wird unverändert weitergegeben
        self.raw_mode = None                   # "jpeg" / "nv12" = Rohdaten der Karte, None = OpenCV rechnet um
        self._raw_failed = set()               # Geräte, deren Rohdaten kein JPEG waren
        self._nv12_failed = set()              # Geräte, deren NV12-Rohdaten nicht passten
        # Wird vom VideoStreamer gesetzt: True, solange jemand dekodierte Bilder braucht (WebRTC)
        self.want_decoded = None
        self.frame = None
        self.frame_id = 0
        self.frame_time = 0.0
        # (id, BGR-Bild, JPEG der Karte, NV12 der Karte, Zeitpunkt) - jeweils None, wenn nicht vorhanden
        self._latest = (0, None, None, None, 0.0)
        self._cache = {kind: collections.OrderedDict() for kind in ("bgr", "i420", "jpeg")}
        self._cache_locks = {kind: threading.Lock() for kind in self._cache}
        self.quality_limit = 100          # Obergrenze der JPEG-Qualität aus der Bildqualität (1080p60: 75)
        self.state = "unavailable"
        self.detail = "noch nicht gestartet"
        self.actual = (0, 0, 0)
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._waiters = set()
        self._measured_fps = 0.0
        self.placeholder = self._make_placeholder()
        # Testbild: liefert True, solange eine Taste gedrückt ist (Reaktionstest ohne Konsole)
        self.input_active = None
        self._reopen = threading.Event()
        self._restart_usb = None
        self._restart_done = threading.Event()
        self._restart_result = (False, "")
        self._usb_dir = None
        self.blank_since = 0.0                 # seit wann das Bild einfarbig ist (0 = nicht)
        self._blank_checked = 0.0

    def _resolve_device(self):
        """Gerät für den nächsten Öffnungsversuch: „auto“ sucht die Karte; ein eingetragenes /dev/videoN, das
        fehlt oder keine USB-Karte ist, wird durch die gefundene Karte ersetzt (Nummer hat sich geändert)."""
        if self.device == "auto":
            return find_capture_device()
        if not os.path.exists("/sys/class/video4linux"):
            return self.device                    # kein Linux (lokaler Test)
        node = "/sys/class/video4linux/" + os.path.basename(os.path.realpath(self.device))
        if os.path.exists(self.device) and _is_usb_video(node):
            return self.device
        found = find_capture_device()
        if found and found != self._fallback_logged:
            self._fallback_logged = found
            log.warning("%s ist keine Capture-Card (mehr) - nehme %s. Tipp: device: \"auto\" in der config.yaml",
                        self.device, found)
        return found or self.device

    # ------------------------------------------------------------------ Format und Neustart
    def set_format(self, width, height, fps):
        """Andere Auflösung: Capture-Card schließen und mit dem neuen Format öffnen."""
        self.width, self.height, self.fps = int(width), int(height), int(fps)
        self.placeholder = self._make_placeholder()
        self._reopen.set()

    def restart(self, timeout=REAPPEAR_SECONDS + 10.0):
        """Capture-Card schließen, USB zurücksetzen, neu öffnen (blockiert – im Executor aufrufen)."""
        if self.device == "test":
            return True, "Testbild neu gestartet"
        self._restart_done.clear()
        self._restart_usb = True
        self._reopen.set()
        if not self._restart_done.wait(timeout):
            return False, "Capture-Card reagiert nicht – Bridge neu starten oder USB-Stecker ziehen"
        return self._restart_result

    def _handle_reopen(self):
        """Nach dem Schließen: angeforderten Neustart ausführen. True = sofort neu öffnen."""
        if not self._reopen.is_set():
            return False
        self._reopen.clear()
        if self._restart_usb:
            self._restart_usb = None
            self.state = "unavailable"
            self.detail = "wird neu gestartet"
            # sysfs-Ordner frisch ermitteln: nach einem früheren Neustart hat die Karte evtl. eine neue Nummer
            usb_dir = None
            try:
                usb_dir = usb_device_dir(self.current_device)
            except OSError:
                pass
            ok, text = usb_reset(usb_dir or self._usb_dir)
            log.info("Capture-Card: %s", text)
            if ok:
                ok, text = self._wait_reappear(text)
            self._restart_result = (ok, text)
            self._restart_done.set()
        return True

    def _wait_reappear(self, text):
        """Nach dem USB-Reset warten, bis die Karte wieder als Videogerät da ist."""
        self._stop.wait(1.5)                      # Kernel meldet das alte Gerät erst ab
        deadline = time.monotonic() + REAPPEAR_SECONDS
        while not self._stop.is_set():
            device = self._resolve_device()
            if device and os.path.exists(device):
                return True, text
            if time.monotonic() > deadline:
                break
            self._stop.wait(0.5)
        msg = ("Capture-Card meldet sich nach dem Neustart nicht zurück – USB-Stecker ziehen und wieder "
               "einstecken (Netzteil des Pi stark genug?)")
        log.warning(msg)
        return False, msg

    # ------------------------------------------------------------------ Thread
    def start(self, loop):
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _publish(self, frame=None, jpeg=None, nv12=None):
        now = time.monotonic()
        self._latest = (self.frame_id + 1, frame, jpeg, nv12, now)
        self.frame = frame
        self.frame_id += 1
        if self.frame_time:
            dt = now - self.frame_time
            if dt > 0:
                self._measured_fps = self._measured_fps * 0.9 + (1.0 / dt) * 0.1
            if REC.active:
                REC.sample("cap_gap", dt * 1000.0)
                period = 1.0 / max(1, self.actual[2] or self.fps)
                if dt > max(0.025, period * 1.6):
                    REC.event("capture", f"Capture-Card: {dt * 1000:.0f} ms kein Bild "
                                         f"(erwartet alle {period * 1000:.0f} ms)")
        REC.count("cap")
        self.frame_time = now
        if now - self._blank_checked >= 0.5:
            self._blank_checked = now
            self._check_blank(frame, nv12, now)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake)

    def _check_blank(self, frame, nv12, now):
        """Einfarbiges Bild (schwarz): Die Cam Link liefert ohne HDMI-Signal weiter Bilder – ohne diese Prüfung
        stünde „Videosignal ok“ da, obwohl die Konsole schläft."""
        if nv12 is not None:
            sample = nv12[:nv12.shape[0] * 2 // 3:32, ::32]           # Helligkeit (Y-Ebene)
        elif frame is not None:
            sample = frame[::32, ::32]
        else:
            return                                                     # nur JPEG der Karte: nicht dekodieren
        if sample.size and int(sample.max()) - int(sample.min()) < BLANK_SPREAD:
            if not self.blank_since:
                self.blank_since = now
        else:
            self.blank_since = 0.0

    def blank_seconds(self):
        """Wie lange das Bild schon einfarbig ist, 0 = Bild hat Inhalt (oder erst kurz einfarbig)."""
        if not self.blank_since or self.state != "ok":
            return 0
        held = time.monotonic() - self.blank_since
        return int(held) if held >= BLANK_SECONDS else 0

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
            device = self._resolve_device()
            if device is None:
                self.state = "unavailable"
                self.detail = "keine Capture-Card gefunden (USB prüfen)"
                if not self._handle_reopen():
                    self._reopen.wait(RECONNECT_SECONDS)
                    self._handle_reopen()
                continue
            self.current_device = device
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                self.state = "unavailable"
                self.detail = f"{device} nicht gefunden"
                cap.release()
                if not self._handle_reopen():
                    self._reopen.wait(RECONNECT_SECONDS)
                    self._handle_reopen()
                continue
            try:
                self._usb_dir = usb_device_dir(device) or self._usb_dir
            except OSError:
                pass
            self._set_format(cap, self.fourcc)
            self.format = fourcc_name(cap.get(cv2.CAP_PROP_FOURCC))
            if self.format not in ("MJPG", "NV12") and self.allow_nv12 and device not in self._nv12_failed:
                # Karten ohne MJPEG (Cam Link: YUYV/NV12): NV12 roh lesen spart die Umrechnung nach BGR
                self._set_format(cap, "NV12")
                self.format = fourcc_name(cap.get(cv2.CAP_PROP_FOURCC))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffers)
            self.actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                           int(round(cap.get(cv2.CAP_PROP_FPS))))
            self.passthrough = False
            self.raw_mode = None
            if self.allow_passthrough and self.format == "MJPG" and device not in self._raw_failed:
                self.raw_mode = "jpeg" if cap.set(cv2.CAP_PROP_CONVERT_RGB, 0) else None
            elif self.format == "NV12" and device not in self._nv12_failed:
                self.raw_mode = "nv12" if cap.set(cv2.CAP_PROP_CONVERT_RGB, 0) else None
            raw = self.raw_mode == "jpeg"
            scaled = self._scale_size(*self.actual[:2])
            REC.event("capture", "Capture-Card geöffnet: %dx%d @ %d fps" % self.actual)
            log.info("Capture-Card %s geöffnet: %dx%d @ %d fps, Format %s%s%s, %d Puffer", device, *self.actual,
                     self.format or "?", {"jpeg": " (JPEG wird durchgereicht)", "nv12": " (roh)"}.get(self.raw_mode, ""),
                     f", verkleinert auf {scaled[0]}x{scaled[1]}" if scaled else "",
                     int(cap.get(cv2.CAP_PROP_BUFFERSIZE) or self.buffers))
            self.state = "no_signal"
            self.detail = "warte auf Bild"
            self.blank_since = 0.0
            failures = 0
            while not self._stop.is_set() and not self._reopen.is_set():
                ok, frame = cap.read()
                if ok and frame is not None and self.raw_mode == "nv12":
                    w, h = self.actual[0], self.actual[1]
                    need = w * h * 3 // 2
                    flat = frame.reshape(-1)
                    if frame.dtype != np.uint8 or flat.size < need or w % 2 or h % 2 or not need:
                        log.info("NV12-Rohdaten passen nicht (%d statt %d Byte) - OpenCV rechnet um", flat.size, need)
                        self._nv12_failed.add(device)
                        self._reopen.set()
                        break
                    failures = 0
                    self.state = "ok"
                    self.detail = ""
                    self._publish(nv12=flat[:need].reshape(h * 3 // 2, w))
                    continue
                if ok and frame is not None and raw:
                    data = as_jpeg(frame)
                    if data is None:
                        log.info("Capture-Card liefert kein JPEG zum Durchreichen - Bilder werden umgerechnet")
                        self._raw_failed.add(device)
                        self._reopen.set()
                        break
                    decoded = None
                    if self.want_decoded is None or self.want_decoded():
                        decoded = cv2.imdecode(frame.reshape(-1), cv2.IMREAD_COLOR)
                        if decoded is None:            # beschädigtes Bild überspringen
                            continue
                    failures = 0
                    self.passthrough = True
                    self.state = "ok"
                    self.detail = ""
                    self._publish(frame=decoded, jpeg=data)
                    continue
                if ok and frame is not None:
                    failures = 0
                    self.state = "ok"
                    self.detail = ""
                    self._publish(frame)
                    continue
                failures += 1
                if time.monotonic() - self.frame_time > NO_SIGNAL_AFTER:
                    if self.state == "ok":
                        log.info("Capture-Card liefert kein Bild mehr (kein HDMI-Signal? Konsole im Standby?)")
                    self.state = "no_signal"
                    self.detail = "kein Bild (Konsole im Standby?)"
                if failures > max(30, self.fps):
                    log.warning("Capture-Card liefert keine Bilder mehr - verbinde neu")
                    break
                time.sleep(0.02)
            cap.release()
            if not self._handle_reopen():
                self._reopen.wait(RECONNECT_SECONDS)
                self._handle_reopen()

    def _set_format(self, cap, fourcc):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

    def _scale_size(self, w, h):
        """Zielgröße (gerade Zahlen) für die gewählte Bildqualität; None = Bild ist nicht größer."""
        tw, th = self.width, self.height
        if not w or not h or not tw or not th or (w <= tw and h <= th):
            return None
        s = min(tw / w, th / h)
        return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)

    def _test_base(self):
        self.actual = (self.width, self.height, self.fps)
        base = np.zeros((self.height, self.width, 3), np.uint8)
        colors = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
                  (255, 0, 255), (0, 0, 255), (255, 0, 0), (30, 30, 30)]
        bar = self.width // len(colors)
        for i, color in enumerate(colors):
            base[:, i * bar:(i + 1) * bar] = color
        # feine Linien: zeigen, ob das Bild scharf ankommt
        for x in range(0, self.width, 4):
            base[self.height - 140:self.height - 100, x] = (0, 0, 0)
        return base, 1.0 / max(1, self.fps)

    def _run_test_pattern(self):
        self.state = "ok"
        self.detail = "Testbild"
        base, period = self._test_base()
        n = 0
        while not self._stop.is_set():
            if self._reopen.is_set():
                self._reopen.clear()
                base, period = self._test_base()
                if self._restart_usb:
                    self._restart_usb = None
                    self._restart_result = (True, "Testbild neu gestartet")
                    self._restart_done.set()
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

    def _cached(self, kind, key, make):
        """Jedes Bild wird pro Art (BGR, I420, JPEG) höchstens einmal berechnet - mehrere Zuschauer teilen es.
        Verschiedene Bilder dürfen gleichzeitig berechnet werden (Einzelbilder: zwei JPEGs parallel)."""
        lock = self._cache_locks[kind]
        entries = self._cache[kind]
        with lock:
            fut = entries.get(key)
            owner = fut is None
            if owner:
                fut = concurrent.futures.Future()
                entries[key] = fut
                while len(entries) > CACHE_ENTRIES:
                    entries.popitem(last=False)
        if owner:
            try:
                fut.set_result(make())
            except BaseException as err:
                fut.set_exception(err)
                with lock:
                    if entries.get(key) is fut:
                        del entries[key]
        return fut.result()

    def snapshot(self):
        """Neuestes Bild (ID, BGR, JPEG der Karte, NV12, Aufnahmezeit) zum Weiterreichen an jpeg_frame."""
        return self._latest

    def _nv12_planes(self, nv12):
        """Y- und UV-Ebene, auf die gewählte Bildqualität verkleinert."""
        h = nv12.shape[0] * 2 // 3
        w = nv12.shape[1]
        y = nv12[:h]
        uv = nv12[h:].reshape(h // 2, w // 2, 2)
        size = self._scale_size(w, h)
        if size:
            y = cv2.resize(y, size, interpolation=cv2.INTER_LINEAR)
            uv = cv2.resize(uv, (size[0] // 2, size[1] // 2), interpolation=cv2.INTER_LINEAR)
        return y, uv

    def bgr_frame(self, latest=None):
        """Neuestes Bild (bzw. latest aus snapshot()) als BGR in der gewählten Größe, sonst das "Kein Signal"-Bild."""
        frame_id, bgr, jpeg, nv12, _taken = latest or self._latest
        if frame_id <= 0 or self.state != "ok":
            return self.placeholder, -1

        def make():
            if nv12 is not None:
                y, uv = self._nv12_planes(nv12)
                return cv2.cvtColor(np.vstack((y, uv.reshape(y.shape[0] // 2, y.shape[1]))), cv2.COLOR_YUV2BGR_NV12)
            img = bgr if bgr is not None else cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None
            size = self._scale_size(img.shape[1], img.shape[0])
            return cv2.resize(img, size, interpolation=cv2.INTER_LINEAR) if size else img

        frame = self._cached("bgr", frame_id, make)
        return (frame, frame_id) if frame is not None else (self.placeholder, -1)

    current = bgr_frame

    def i420_frame(self):
        """(Bild, Bild-ID, Format für PyAV): I420 für den Encoder - aus NV12 direkt, sonst aus BGR."""
        frame_id, _bgr, _jpeg, nv12, _taken = self._latest
        if frame_id > 0 and self.state == "ok" and nv12 is not None:
            return self._cached("i420", frame_id, lambda: nv12_to_i420(*self._nv12_planes(nv12))), frame_id, "yuv420p"
        frame, frame_id = self.bgr_frame()
        if frame.shape[0] % 2 or frame.shape[1] % 2:
            return frame, frame_id, "bgr24"
        key = frame_id if frame_id != -1 else ("placeholder", id(frame))
        # Farbumrechnung mit OpenCV (NEON-optimiert) statt im Encoder: spart pro Bild einige Millisekunden
        return self._cached("i420", key, lambda: cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)), frame_id, "yuv420p"

    def jpeg_frame(self, latest=None):
        """(JPEG, Bild-ID, Aufnahmezeit, Sekunden fürs Umrechnen + Kodieren). Durchgereichte Bilder kosten nichts."""
        latest = latest or self._latest
        frame_id, _bgr, jpeg, _nv12, taken = latest
        if frame_id > 0 and self.state == "ok" and jpeg is not None:
            return jpeg, frame_id, taken, 0.0
        started = time.monotonic()
        frame, frame_id = self.bgr_frame(latest)
        key = frame_id if frame_id != -1 else ("placeholder", id(frame))
        quality = min(self.jpeg_quality, self.quality_limit)

        def make():
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes() if ok else b""

        data = self._cached("jpeg", key, make)
        return data, frame_id, taken, time.monotonic() - started

    def jpeg(self):
        return self.jpeg_frame()[0]

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
        return {"state": self.state, "detail": self.detail, "device": self.current_device,
                "width": w, "height": h, "fps": round(self._measured_fps) if self.state == "ok" else 0,
                "webrtc": HAVE_WEBRTC, "can_restart": True, "target_fps": self.fps,
                "format": self.format, "raw": self.raw_mode, "scaled": self._scale_size(w, h),
                "passthrough": self.passthrough and self.state == "ok", "blank": self.blank_seconds()}


class CaptureTrack(MediaStreamTrack):
    """WebRTC-Videospur mit dem jeweils neuesten Bild, begrenzt auf max_fps."""

    kind = "video"

    def __init__(self, source, max_fps, stats):
        super().__init__()
        self.source = source
        # Zahl oder Funktion (Bildqualität lässt sich während des Streams umschalten)
        self.max_fps = max_fps if callable(max_fps) else (lambda: max_fps)
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
            if REC.active:
                spent = (now - self.returned_at) * 1000.0
                REC.sample("encsend", spent)
                if spent > 2000.0 / max(1, self.max_fps()):
                    REC.event("send", f"Kodieren + Senden: {spent:.0f} ms (aiortc)")
        if now < self.next_at - 0.004:            # 4 ms Spielraum: Bilder der Karte schwanken etwas
            await asyncio.sleep(self.next_at - 0.004 - now)
        await self.source.wait_frame(self.last_id, 0.5)
        picked = time.monotonic()
        frame, frame_id, fmt = await asyncio.get_running_loop().run_in_executor(None, self.source.i420_frame)
        if frame_id == self.last_id and frame_id != -1:
            REC.count("repeat")                   # kein neues Bild der Karte innerhalb von 0,5 s
        self.last_id = frame_id
        if frame_id != -1 and self.source.frame_time:
            self.stats.add("age", picked - self.source.frame_time)
            REC.sample("age", (picked - self.source.frame_time) * 1000.0)
        self.next_at = pace(self.next_at, picked, 1.0 / max(1, self.max_fps()))
        video_frame = av.VideoFrame.from_ndarray(frame, format=fmt)
        video_frame.pts = int((time.monotonic() - self.start) * VIDEO_CLOCK)
        video_frame.time_base = fractions.Fraction(1, VIDEO_CLOCK)
        done = time.monotonic()
        self.stats.add("convert", done - picked)
        self.stats.frame(done)
        REC.count("sent")
        REC.sample("conv", (done - picked) * 1000.0)
        self.returned_at = done
        return video_frame


class StreamStats:
    """Gleitende Messwerte (Millisekunden) und Bildrate für die Messanzeige im Browser."""

    PEAK_KEYS = ("encode",)
    PEAK_SECONDS = 3.0

    def __init__(self):
        self.values = {}
        self.frames = collections.deque(maxlen=240)
        self.recent = {key: collections.deque(maxlen=400) for key in self.PEAK_KEYS}

    def add(self, key, seconds):
        ms = seconds * 1000.0
        old = self.values.get(key)
        self.values[key] = ms if old is None else old * 0.9 + ms * 0.1
        if key in self.recent:
            self.recent[key].append((time.monotonic(), ms))

    def frame(self, now):
        self.frames.append(now)

    def fps(self):
        now = time.monotonic()
        return sum(1 for t in self.frames if now - t <= 1.0)

    def snapshot(self):
        out = {key: round(value, 1) for key, value in self.values.items()}
        now = time.monotonic()
        for key, items in self.recent.items():
            peak = [ms for t, ms in items if now - t <= self.PEAK_SECONDS]
            if peak:
                # zweitgrößter Wert: ein einzelner Ausreißer (z. B. Schlüsselbild) soll nicht dauernd rot zeigen
                out[key + "_peak"] = round(sorted(peak)[-2] if len(peak) > 1 else peak[0], 1)
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
        self.jpeg_fps = self.webrtc_fps   # Einzelbilder pro Sekunde (1080p60: 60, Video dort 30)
        self.max_bitrate = int(cfg.get("webrtc_max_bitrate", 6000000))
        self.min_bitrate = int(cfg.get("webrtc_min_bitrate", 3000000))
        self.fixed_bitrate = bool(cfg.get("webrtc_fixed_bitrate", True))
        self.state_file = Path(cfg.get("state_file", "/var/lib/switch2-bridge/video_mode.json"))
        self.source = FrameSource(cfg)
        self.mode = self._initial_mode(cfg)
        self.audio = AudioSource(audio_cfg, test_pattern=self.source.device == "test")
        self.pcs = set()
        # Wird von der Bridge gesetzt: on_channel(channel, pc) für den Eingabe-Datenkanal
        self.on_channel = None
        self.webrtc_stats = StreamStats()
        self.mjpeg_stats = StreamStats()
        self.jpeg_stats = StreamStats()
        self.jpeg_clients = 0
        self.source.want_decoded = lambda: bool(self.pcs)
        self.runner = None
        set_bitrate(self.max_bitrate, self.min_bitrate, self.fixed_bitrate)
        use_all_cores(cfg.get("webrtc_threads", 4))
        set_vp8_speed(cfg.get("webrtc_speed", -12))
        # Video-Codec: der Browser wünscht ihn beim Verbindungsaufbau ("codec"), sonst gilt webrtc_codec
        self.default_codec = str(cfg.get("webrtc_codec", "h264")).lower()
        self.codec_in_use = None
        set_h264(cfg.get("h264_preset", "superfast"), cfg.get("webrtc_threads", 4), self.webrtc_fps)
        set_encode_priority(cfg.get("webrtc_encode_nice", -5))
        if self.mode in MODES:
            self._apply_mode(self.mode, reopen=False)

    def record_info(self):
        """Einstellungen und Zustand für den Kopf einer Aufzeichnung."""
        src = self.source
        info = {
            "mode": self.mode, "webrtc_fps": self.webrtc_fps, "jpeg_fps": self.jpeg_fps, "bitrate": self.max_bitrate,
            "fixed_bitrate": self.fixed_bitrate, "threads": self.cfg.get("webrtc_threads", 4),
            "speed": self.cfg.get("webrtc_speed", -12), "encode_nice": ENCODE_NICE,
            "codec": self.codec_in_use, "h264": H264_PRESET if HAVE_X264 else None,
            "device": src.current_device, "format": src.format,
            "actual": list(src.actual), "scaled": src._scale_size(*src.actual[:2]), "raw": src.raw_mode,
            "buffers": src.buffers, "webrtc_clients": len(self.pcs), "jpeg_clients": self.jpeg_clients,
            "blank": src.blank_seconds(),
            "audio": self.audio.active_device or self.audio.state,
        }
        try:
            import aiortc
            info["aiortc"] = aiortc.__version__
            info["av"] = av.__version__
        except (ImportError, AttributeError, NameError):
            pass
        info["opencv"] = cv2.__version__
        return info

    # ------------------------------------------------------------------ Bildqualität
    def _initial_mode(self, cfg):
        try:
            saved = json.loads(self.state_file.read_text()).get("mode")
            if saved in MODES:
                return saved
        except (OSError, ValueError, AttributeError):
            pass
        for key, mode in MODES.items():
            if (mode["width"], mode["height"], mode["send"]) == (self.source.width, self.source.height, self.webrtc_fps):
                return key
        return "config"

    def _apply_mode(self, key, reopen=True):
        mode = MODES[key]
        self.mode = key
        self.webrtc_fps = mode["send"]
        self.jpeg_fps = mode.get("jpeg", mode["send"])
        self.source.quality_limit = mode.get("jpeg_quality", 100)
        set_h264_fps(self.webrtc_fps)
        set_bitrate(max(mode["bitrate"], self.max_bitrate if key == "720p60" else 0), self.min_bitrate,
                    self.fixed_bitrate)
        if reopen:
            self.source.set_format(mode["width"], mode["height"], mode["fps"])
        else:
            self.source.width, self.source.height, self.source.fps = mode["width"], mode["height"], mode["fps"]
            self.source.placeholder = self.source._make_placeholder()

    def set_mode(self, key):
        if key not in MODES:
            return False, "Unbekannte Bildqualität"
        if key == self.mode:
            return True, f"Bildqualität: {MODES[key]['label']}"
        self._apply_mode(key)
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"mode": key}))
        except OSError as err:
            log.debug("Bildqualität nicht gespeichert: %s", err)
        log.info("Bildqualität: %s", MODES[key]["label"])
        REC.event("mode", f"Bildqualität: {MODES[key]['label']}")
        return True, f"Bildqualität: {MODES[key]['label']}"

    async def restart_capture(self):
        return await asyncio.get_running_loop().run_in_executor(None, self.source.restart)

    def modes(self):
        items = [{"id": k, "label": m["label"]} for k, m in MODES.items()]
        if self.mode == "config":
            items.insert(0, {"id": "config", "label": f"{self.source.width}×{self.source.height} · "
                                                      f"{self.webrtc_fps} fps (config.yaml)"})
        return items

    # ------------------------------------------------------------------ HTTP
    async def handle_offer(self, request):
        if not HAVE_WEBRTC:
            return web.json_response({"error": "aiortc ist nicht installiert"}, status=503)
        try:
            params = await request.json()
            wanted = str(params.get("codec") or self.default_codec).lower()
            if wanted == "h264" and not HAVE_X264:
                wanted = "vp8"                    # PyAV ohne x264: VP8
            offer = RTCSessionDescription(sdp=str(params["sdp"]), type=str(params["type"]))
        except (ValueError, KeyError, TypeError, AttributeError):
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

        tracks.append(CaptureTrack(self.source, lambda: self.webrtc_fps, self.webrtc_stats))
        pc.addTrack(tracks[-1])
        if self.audio.enabled and "m=audio" in offer.sdp:
            tracks.append(AudioTrack(self.audio))
            sender = pc.addTrack(tracks[-1])
            # Eigene Stream-ID: ohne gemeinsame Gruppe gleicht der Browser Bild und Ton nicht ab –
            # sonst würde er das Bild verzögern, bis der (etwas spätere) Ton aufholt.
            sender._stream_id = str(uuid.uuid4())
        # Vor setRemoteDescription: dort wählt aiortc die gemeinsamen Codecs in dieser Reihenfolge
        for transceiver in pc.getTransceivers():
            if transceiver.kind == "video":
                transceiver.setCodecPreferences(codec_preferences(wanted))
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        self.codec_in_use = answer_codec(pc.localDescription.sdp)
        log.info("WebRTC %s: Video als %s (gewünscht: %s)", request.remote, self.codec_in_use or "?", wanted)
        REC.event("webrtc", f"neue WebRTC-Verbindung: Video als {self.codec_in_use or '?'}")
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

    async def handle_frames(self, request):
        """Einzelbilder über WebSocket: der Browser dekodiert (createImageBitmap), der Pi kodiert höchstens
        JPEG oder reicht das JPEG der Capture-Card durch. Neue Bilder gibt es erst nach Bestätigung.

        Bis zu JPEG_PIPELINE Bilder werden gleichzeitig kodiert (im Thread-Pool, OpenCV gibt dabei den GIL frei):
        ein 1080p-JPEG braucht auf dem Pi 5 etwa so lange wie ein Bild bei 60 fps dauert - nacheinander kodiert
        reichte es nur für etwa 45 fps. Gesendet wird immer in der Reihenfolge der Aufnahme."""
        ws = web.WebSocketResponse(heartbeat=20, compress=False, max_msg_size=64 * 1024)
        await ws.prepare(request)
        loop = asyncio.get_running_loop()
        inflight = [0]
        kick = asyncio.Event()                # Ack, neues Bild oder fertig kodiert: Schleife weiterlaufen lassen

        async def reader():
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "ack":
                    inflight[0] = max(0, inflight[0] - 1)
                    kick.set()

        read_task = asyncio.create_task(reader())
        read_task.add_done_callback(lambda _task: kick.set())
        self.jpeg_clients += 1
        log.info("Einzelbilder: Zuschauer %s verbunden", request.remote)
        pending = collections.deque()         # Futures der gerade kodierten Bilder, in Aufnahme-Reihenfolge
        frame_wait = None
        last_taken = None                     # ID des zuletzt zum Kodieren genommenen Bildes
        last_sent = None
        submitted_at = 0.0
        next_at = 0.0
        blocked_since = None                  # seit wann das Fenster voll ist (Bestätigung verloren?)
        try:
            while not ws.closed and not read_task.done():
                kick.clear()
                now = time.monotonic()
                # 1) Ältestes fertiges Bild senden, sobald das Fenster Platz hat
                if pending and pending[0].done() and inflight[0] < JPEG_WINDOW:
                    data, frame_id, taken, enc = pending.popleft().result()
                    if not data or (frame_id != -1 and frame_id == last_sent):
                        continue
                    last_sent = frame_id
                    sent = time.monotonic()
                    head = {"id": frame_id, "enc": round(enc * 1000, 1), "pt": self.source.passthrough}
                    if frame_id != -1:
                        head["age"] = round((sent - taken) * 1000, 1)
                        self.jpeg_stats.add("age", sent - taken)
                        self.jpeg_stats.add("encode", enc)
                        self.jpeg_stats.frame(sent)
                        REC.count("jpeg")
                        REC.sample("jpeg_enc", enc * 1000.0)
                    raw_head = json.dumps(head).encode()
                    await ws.send_bytes(len(raw_head).to_bytes(4, "big") + raw_head + data)
                    inflight[0] += 1
                    continue
                if inflight[0] >= JPEG_WINDOW:
                    blocked_since = blocked_since or now
                    if now - blocked_since > 3.0:
                        inflight[0] = 0               # Bestätigung verloren: weitermachen statt hängen
                        blocked_since = None
                        continue
                else:
                    blocked_since = None
                # 2) Nächstes Bild zum Kodieren geben: höchstens JPEG_PIPELINE gleichzeitig und nicht mehr, als
                #    nach den Bestätigungen gleich gesendet werden kann (sonst wären sie beim Senden schon alt)
                timeout = 0.5
                if len(pending) < JPEG_PIPELINE and inflight[0] + len(pending) <= JPEG_WINDOW:
                    period = 1.0 / max(1, self.jpeg_fps)
                    ok = self.source.state == "ok"
                    fresh = ok and self.source.frame_id != last_taken
                    if now < next_at - 0.004:
                        timeout = next_at - 0.004 - now   # Bildrate begrenzen (z. B. 30 von 60 fps der Karte)
                    elif fresh or (not ok and now - submitted_at >= 1.0):
                        snap = self.source.snapshot()
                        last_taken = snap[0]
                        submitted_at = now
                        fut = loop.run_in_executor(None, self.source.jpeg_frame, snap)
                        fut.add_done_callback(lambda _fut: kick.set())
                        pending.append(fut)
                        next_at = pace(next_at, now, period)
                        continue
                    elif frame_wait is None or frame_wait.done():
                        frame_wait = asyncio.ensure_future(self.source.wait_frame(last_taken, 1.0))
                        frame_wait.add_done_callback(lambda _task: kick.set())
                try:
                    await asyncio.wait_for(kick.wait(), timeout)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            read_task.cancel()
            if frame_wait is not None:
                frame_wait.cancel()
            self.jpeg_clients -= 1
            log.info("Einzelbilder: Zuschauer %s getrennt", request.remote)
        return ws

    def stats(self):
        """Messwerte für den Status an den Browser."""
        return {"webrtc": self.webrtc_stats.snapshot() if self.pcs else None,
                "jpeg": self.jpeg_stats.snapshot() if self.jpeg_clients else None,
                "jpeg_clients": self.jpeg_clients,
                "mjpeg_fps": self.mjpeg_stats.fps(),
                "webrtc_clients": len(self.pcs),
                "audio": self.audio.status(),
                "mode": self.mode, "modes": self.modes(), "target_fps": self.webrtc_fps, "jpeg_fps": self.jpeg_fps,
                "codec": self.codec_in_use if self.pcs else None, "codecs": ["h264", "vp8"] if HAVE_X264 else ["vp8"]}

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
        app.router.add_get("/frames", self.handle_frames)
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
