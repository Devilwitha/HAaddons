import json
from datetime import datetime
from unittest import mock

import numpy as np
import pytest
from PIL import Image, ImageDraw

from werwardas.analyzers.reference import ReferenceAnalyzer
from werwardas.config import Config
from werwardas.service import Service, in_active_hours, normalize_active_hours
from werwardas.streak import StreakTracker


def room(clutter: int = 0, brightness: float = 1.0, seed: int = 1) -> Image.Image:
    """Synthetisches 'Zimmer': Wände, Bett, Schreibtisch und optional herumliegende Dinge."""
    img = Image.new("RGB", (640, 480), (200, 190, 170))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 300, 640, 480], fill=(120, 90, 60))  # Boden
    d.rectangle([40, 220, 300, 340], fill=(60, 80, 150))  # Bett
    d.rectangle([400, 200, 600, 260], fill=(90, 60, 40))  # Schreibtisch
    rng = np.random.default_rng(seed)
    for _ in range(clutter):
        x, y = int(rng.integers(0, 600)), int(rng.integers(310, 450))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        d.ellipse([x, y, x + 40, y + 25], fill=color)
    if brightness != 1.0:
        arr = np.clip(np.asarray(img, dtype=np.float32) * brightness, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    return img


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"mqtt:\n  host: localhost\ncamera:\n  type: file\n  path: {tmp_path / 'cam.jpg'}\n"
        f"storage:\n  dir: {tmp_path / 'data'}\nactive_hours: ''\n",
        encoding="utf-8",
    )
    return path


def make_analyzer(tmp_path):
    return ReferenceAnalyzer({"sensitivity": 25, "gain": 5, "ignore_areas": []}, tmp_path / "ref")


def test_reference_without_photo_is_unknown(tmp_path):
    result = make_analyzer(tmp_path).analyze(room())
    assert result.score is None


def test_reference_tidy_vs_messy(tmp_path):
    analyzer = make_analyzer(tmp_path)
    analyzer.add_reference(room())
    tidy = analyzer.analyze(room())
    darker = analyzer.analyze(room(brightness=0.75))
    messy = analyzer.analyze(room(clutter=40))
    assert tidy.score >= 95
    assert darker.score >= 80, "Lichtänderung darf nicht als Unordnung zählen"
    assert messy.score < 60
    assert messy.score < darker.score


def test_reference_keeps_latest_five(tmp_path):
    analyzer = make_analyzer(tmp_path)
    for i in range(7):
        with mock.patch("time.time", return_value=1000 + i):
            analyzer.add_reference(room())
    assert len(analyzer.references()) == 5


def test_ignore_area(tmp_path):
    analyzer = ReferenceAnalyzer(
        {"sensitivity": 25, "gain": 5, "ignore_areas": [[0, 60, 100, 40]]}, tmp_path / "ref"
    )
    analyzer.add_reference(room())
    assert analyzer.analyze(room(clutter=40)).score >= 90


@pytest.mark.parametrize(
    "spec,hour,expected",
    [
        ("08:00-22:00", 12, True),
        ("08:00-22:00", 23, False),
        ("20:00-02:00", 1, True),
        ("20:00-02:00", 12, False),
        ("", 3, True),
    ],
)
def test_active_hours(spec, hour, expected):
    assert in_active_hours(spec, datetime(2026, 1, 1, hour, 0)) is expected


def test_runtime_settings_persist(cfg_file):
    cfg = Config(cfg_file)
    cfg.set_runtime("threshold", 75)
    assert Config(cfg_file)["threshold"] == 75


def published(client, topic):
    return [json.loads(c.args[1]) for c in client.publish.call_args_list if c.args[0] == topic]


def test_service_check_publishes_state(cfg_file, tmp_path):
    cfg = Config(cfg_file)
    service = Service(cfg)
    service.client = mock.MagicMock()

    room().save(tmp_path / "cam.jpg")
    service.save_reference()
    room(clutter=40).save(tmp_path / "cam.jpg")
    state = service.check("test")
    assert state["tidy"] is False and state["score"] < 60

    room().save(tmp_path / "cam.jpg")
    state = service.check("pc")
    assert state["tidy"] is True
    assert published(service.client, "werwardas/cam/state")[-1]["trigger"] == "pc"


def test_skip_unchanged_reuses_last_score(cfg_file, tmp_path):
    cfg = Config(cfg_file)
    service = Service(cfg)
    service.client = mock.MagicMock()

    room().save(tmp_path / "cam.jpg")
    service.save_reference()

    messy = room(clutter=40, seed=1)
    messy.save(tmp_path / "cam.jpg")
    first = service.check("interval")  # erste echte Bewertung
    assert first["tidy"] is False

    with mock.patch.object(service.references, "analyze", wraps=service.references.analyze) as spy:
        messy.save(tmp_path / "cam.jpg")  # identisches Foto
        reused = service.check("interval")
        assert spy.call_count == 0, "unverändertes Foto darf nicht neu bewertet werden"
        assert reused["score"] == first["score"] and reused["tidy"] is False

        # Manuelle Prüfung bewertet trotz unverändertem Foto neu.
        service.check("manual")
        assert spy.call_count == 1

        # Deutlich verändertes (aufgeräumtes) Foto -> neu bewerten.
        room(seed=1).save(tmp_path / "cam.jpg")
        tidy = service.check("interval")
        assert spy.call_count == 2
        assert tidy["tidy"] is True


def test_skip_unchanged_off_always_reanalyzes(cfg_file, tmp_path):
    cfg = Config(cfg_file)
    cfg.set_runtime("skip_unchanged", False)
    service = Service(cfg)
    service.client = mock.MagicMock()

    room().save(tmp_path / "cam.jpg")
    service.save_reference()
    messy = room(clutter=40, seed=1)
    messy.save(tmp_path / "cam.jpg")
    service.check("interval")
    with mock.patch.object(service.references, "analyze", wraps=service.references.analyze) as spy:
        messy.save(tmp_path / "cam.jpg")
        service.check("interval")
        assert spy.call_count == 1, "ohne Überspringen wird immer neu bewertet"


def test_photo_history_is_capped(cfg_file, tmp_path):
    cfg = Config(cfg_file)
    cfg.data["storage"]["max_photos"] = 3
    service = Service(cfg)
    service.client = mock.MagicMock()
    for i in range(5):
        (service.photo_dir / f"20260101_00000{i}.jpg").write_bytes(b"x")
    Image.new("RGB", (1600, 1200)).save(tmp_path / "cam.jpg")
    service._capture()
    photos = sorted(p.name for p in service.photo_dir.glob("*.jpg"))
    assert len(photos) == 3
    assert photos[0] == "20260101_000003.jpg", "die ältesten Fotos fliegen raus"
    with Image.open(service.photo_dir / photos[-1]) as newest:
        assert newest.width == 1280


def test_service_paused_is_tidy(cfg_file):
    cfg = Config(cfg_file)
    cfg.set_runtime("active", False)
    service = Service(cfg)
    service.client = mock.MagicMock()
    state = service.check("interval")
    assert state["tidy"] is True and state["paused"] is True


def test_camera_error_fails_open(cfg_file):
    service = Service(Config(cfg_file))  # cam.jpg existiert nicht
    service.client = mock.MagicMock()
    state = service.check("interval")
    assert state["error"] is True and state["tidy"] is None


def test_streak_counts_tidy_days(tmp_path):
    tracker = StreakTracker(tmp_path / "streak.json")
    tracker.update(True, datetime(2026, 1, 1, 10))
    tracker.update(True, datetime(2026, 1, 2, 10))  # schließt Tag 1 ab -> Streak 1
    tracker.update(True, datetime(2026, 1, 3, 10))  # schließt Tag 2 ab -> Streak 2
    assert tracker.streak == 2
    assert tracker.record == 2


def test_streak_resets_on_messy_day(tmp_path):
    tracker = StreakTracker(tmp_path / "streak.json")
    tracker.update(True, datetime(2026, 1, 1, 10))
    tracker.update(True, datetime(2026, 1, 2, 10))  # Tag 1 war ok -> Streak 1
    tracker.update(False, datetime(2026, 1, 2, 18))  # noch am Tag 2: unordentlich
    tracker.update(True, datetime(2026, 1, 3, 10))  # schließt Tag 2 ab -> Streak reißt ab
    assert tracker.streak == 0
    assert tracker.record == 1


def test_streak_ignores_days_without_any_check(tmp_path):
    tracker = StreakTracker(tmp_path / "streak.json")
    tracker.update(True, datetime(2026, 1, 1, 10))
    tracker.update(True, datetime(2026, 1, 5, 10))  # Tage 2-4 ohne Prüfung -> zählen nicht gegen den Streak
    assert tracker.streak == 1


def test_streak_ignores_errors(tmp_path):
    tracker = StreakTracker(tmp_path / "streak.json")
    tracker.update(None, datetime(2026, 1, 1, 10))  # an Tag 1 nur ein Fehler
    tracker.update(True, datetime(2026, 1, 2, 10))  # schließt Tag 1 ab -> zählt nicht, weder für noch gegen
    assert tracker.streak == 0


def test_streak_persists(tmp_path):
    path = tmp_path / "streak.json"
    StreakTracker(path).update(True, datetime(2026, 1, 1, 10))
    tracker = StreakTracker(path)
    tracker.update(True, datetime(2026, 1, 2, 10))
    assert tracker.streak == 1


def test_service_includes_streak_in_state(cfg_file, tmp_path):
    service = Service(Config(cfg_file))
    service.client = mock.MagicMock()
    room().save(tmp_path / "cam.jpg")
    service.save_reference()
    state = service.check("test")
    assert state["streak"] == 0 and state["record"] == 0


def test_discovery(cfg_file):
    service = Service(Config(cfg_file))
    service.client = mock.MagicMock()
    service._publish_discovery()
    topics = [c.args[0] for c in service.client.publish.call_args_list]
    assert any(t.startswith("homeassistant/binary_sensor/") and t.endswith("/messy/config") for t in topics)
    assert any(t.startswith("homeassistant/camera/") for t in topics)


def test_ollama_parses_json():
    from werwardas.analyzers.ollama import OllamaAnalyzer

    response = mock.Mock()
    response.json.return_value = {"message": {"content": '{"score": 42, "reason": "Kleidung auf dem Boden."}'}}
    tags = mock.Mock()
    tags.json.return_value = {"models": [{"name": "m:latest"}]}
    with mock.patch("requests.get", return_value=tags), mock.patch("requests.post", return_value=response) as post:
        result = OllamaAnalyzer({"url": "http://x:11434", "model": "m", "timeout_seconds": 5}, "").analyze(room())
    assert (result.score, result.reason) == (42, "Kleidung auf dem Boden.")
    assert post.call_args.kwargs["json"]["messages"][0]["images"]


def test_ollama_falls_back_to_next_server():
    """PC aus -> der Pi übernimmt mit seinem eigenen Modell."""
    import requests as rq

    from werwardas.analyzers.ollama import OllamaAnalyzer

    def fake_get(url, timeout):
        if url.startswith("http://pc"):
            raise rq.ConnectionError("aus")
        resp = mock.Mock()
        resp.json.return_value = {"models": [{"name": "small:3b"}]}
        return resp

    response = mock.Mock()
    response.json.return_value = {"message": {"content": '{"score": 80, "reason": "Ordentlich."}'}}
    cfg = {"servers": [{"url": "http://pc:11434", "model": "big:7b"}, {"url": "http://pi:11434", "model": "small:3b"}]}
    with mock.patch("requests.get", side_effect=fake_get), mock.patch("requests.post", return_value=response) as post:
        result = OllamaAnalyzer(cfg, "").analyze(room())
    assert result.score == 80
    assert post.call_args.args[0] == "http://pi:11434/api/chat"
    assert post.call_args.kwargs["json"]["model"] == "small:3b"


def test_ollama_fallback_can_be_switched_off():
    """Schalter 'Pi-KI als Ersatz' aus: PC aus -> Fehler statt Pi."""
    import requests as rq

    from werwardas.analyzers import AnalyzerError
    from werwardas.analyzers.ollama import OllamaAnalyzer

    cfg = {"servers": [{"url": "http://pc:1", "model": "big:7b"}, {"url": "http://pi:1", "model": "s:3b", "fallback": True}]}
    analyzer = OllamaAnalyzer(cfg, "", use_fallback=False)
    assert [s["url"] for s in analyzer.servers] == ["http://pc:1"]
    with mock.patch("requests.get", side_effect=rq.ConnectionError("aus")) as get:
        with pytest.raises(AnalyzerError, match="Pi-KI als Ersatz ist ausgeschaltet"):
            analyzer.analyze(room())
    assert all("pi:1" not in c.args[0] for c in get.call_args_list)


def test_ollama_fallback_switch_via_mqtt(cfg_file):
    service = Service(Config(cfg_file))
    service.client = mock.MagicMock()
    service._on_message(service.client, None, mock.Mock(topic="werwardas/cam/set/ollama_fallback", payload=b"OFF"))
    assert Config(cfg_file)["ollama_fallback"] is False
    assert published(service.client, "werwardas/cam/settings")[-1]["ollama_fallback"] == "OFF"


def test_ollama_all_servers_down_is_error():
    import requests as rq

    from werwardas.analyzers import AnalyzerError
    from werwardas.analyzers.ollama import OllamaAnalyzer

    with mock.patch("requests.get", side_effect=rq.ConnectionError("aus")):
        with pytest.raises(AnalyzerError):
            OllamaAnalyzer({"servers": [{"url": "http://pc:1"}, {"url": "http://pi:1"}]}, "").analyze(room())


@pytest.mark.parametrize(
    "raw, expected",
    [(" 8:00 - 22:00 ", "08:00-22:00"), ("20:00-02:00", "20:00-02:00"), ("", ""), ("immer", "")],
)
def test_normalize_active_hours(raw, expected):
    assert normalize_active_hours(raw) == expected


@pytest.mark.parametrize("raw", ["8-22", "25:00-22:00", "abends"])
def test_normalize_active_hours_invalid(raw):
    with pytest.raises(ValueError):
        normalize_active_hours(raw)


def test_set_active_hours_via_mqtt(cfg_file):
    cfg = Config(cfg_file)
    service = Service(cfg)
    service.client = mock.MagicMock()
    msg = mock.Mock(topic="werwardas/cam/set/active_hours", payload=b"9:00-21:30")
    service._on_message(service.client, None, msg)
    assert Config(cfg_file)["active_hours"] == "09:00-21:30"
    assert published(service.client, "werwardas/cam/settings")[-1]["active_hours"] == "09:00-21:30"

    # ungültige Eingabe ändert nichts
    service._on_message(service.client, None, mock.Mock(topic=msg.topic, payload=b"quatsch"))
    assert Config(cfg_file)["active_hours"] == "09:00-21:30"
