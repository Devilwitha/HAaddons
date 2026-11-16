"""Bestand: Orte (z. B. Kühlschrank) mit Vorräten, Änderungsverlauf und Speicherung in /data.

Ein Durchlauf der Kameras liefert für einen Ort die gezählten Vorräte. Dieser Stand wird übernommen:
Neue Vorräte kommen dazu, fehlende stehen auf 0, und jede Änderung landet im Verlauf
(„Dose Cola 2 → 1“). Von Hand geänderte Werte gelten bis zum nächsten Durchlauf.
"""
import json
import re
import time
import unicodedata
from pathlib import Path

HISTORY_MAX = 500
CLEANUP_SECONDS = 3 * 86400          # automatisch erkannte Vorräte, die so lange auf 0 stehen, verschwinden
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def slugify(text):
    text = unicodedata.normalize("NFKD", str(text).translate(UMLAUTS)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def norm(name):
    """Vergleichsform eines Namens (Groß-/Kleinschreibung und Leerzeichen egal)."""
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name)).strip(" .,;:-")
    return name[:1].upper() + name[1:60] if name else ""


class Inventory:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {"locations": {}, "history": []}
        self.load()

    # ------------------------------------------------------------------ Speichern
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data.get("locations"), dict):
                self.data["locations"] = data["locations"]
            if isinstance(data.get("history"), list):
                self.data["history"] = data["history"][-HISTORY_MAX:]
        except (OSError, ValueError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ Zugriff
    def location(self, name):
        loc = self.data["locations"].setdefault(name, {})
        loc.setdefault("items", {})
        loc.setdefault("aliases", {})
        loc.setdefault("last_scan", None)
        loc.setdefault("error", "")
        return loc

    def sync_locations(self, names):
        """Orte aus der Konfiguration anlegen (vorhandene Daten bleiben, auch für entfernte Orte)."""
        for name in names:
            self.location(name)

    def known_names(self, location):
        loc = self.location(location)
        return sorted(name for name, item in loc["items"].items() if not item.get("ignored"))

    def find(self, location, name):
        """Gespeicherter Name zu einem (vielleicht anders geschriebenen) Namen, sonst None."""
        loc = self.location(location)
        key = norm(name)
        target = loc["aliases"].get(key)
        if target in loc["items"]:
            return target
        for existing in loc["items"]:
            if norm(existing) == key:
                return existing
        return None

    def _log(self, location, item, old, new, source):
        self.data["history"].append({"time": time.time(), "location": location, "item": item,
                                     "old": old, "new": new, "source": source})
        del self.data["history"][:-HISTORY_MAX]

    # ------------------------------------------------------------------ Ergebnis eines Durchlaufs
    def apply_scan(self, location, counts):
        """counts: {Name: Anzahl} aus allen Kameras des Ortes. Gibt die Änderungen zurück."""
        loc = self.location(location)
        now = time.time()
        seen = {}
        for raw, count in counts.items():
            name = clean_name(raw)
            if not name or count <= 0:
                continue
            target = self.find(location, name) or name
            seen[target] = seen.get(target, 0) + int(count)
        changes = []
        for name, count in seen.items():
            item = loc["items"].get(name)
            if item is None:
                item = loc["items"][name] = {"count": 0, "min": 0, "ignored": False, "manual": False,
                                             "since_zero": None}
            if item.get("ignored"):
                continue
            if item["count"] != count:
                changes.append((name, item["count"], count))
                self._log(location, name, item["count"], count, "kamera")
                item["count"] = count
            item["since_zero"] = None
            item["seen"] = now
        for name, item in list(loc["items"].items()):
            if name in seen or item.get("ignored"):
                continue
            if item["count"] != 0:
                changes.append((name, item["count"], 0))
                self._log(location, name, item["count"], 0, "kamera")
                item["count"] = 0
            if not item.get("since_zero"):
                item["since_zero"] = now
            # Nur von der Kamera erkannt, nie angepasst und lange leer: aufräumen (sonst sammeln sich
            # verschiedene Schreibweisen desselben Produkts an)
            if (not item.get("manual") and not item.get("min") and now - item["since_zero"] > CLEANUP_SECONDS):
                del loc["items"][name]
        loc["last_scan"] = now
        loc["error"] = ""
        self.save()
        return changes

    def scan_failed(self, location, error):
        self.location(location)["error"] = str(error)[:300]
        self.save()

    # ------------------------------------------------------------------ Änderungen von Hand
    def set_count(self, location, name, count):
        loc = self.location(location)
        item = loc["items"].get(name)
        if item is None:
            name = clean_name(name)
            if not name:
                raise ValueError("Name fehlt")
            item = loc["items"][name] = {"count": 0, "min": 0, "ignored": False, "manual": True,
                                         "since_zero": None}
        count = max(0, min(9999, int(count)))
        if item["count"] != count:
            self._log(location, name, item["count"], count, "hand")
            item["count"] = count
        item["manual"] = True
        item["since_zero"] = None if count else time.time()
        self.save()
        return name

    def set_min(self, location, name, minimum):
        item = self.location(location)["items"][name]
        item["min"] = max(0, min(9999, int(minimum)))
        item["manual"] = True
        self.save()

    def set_ignored(self, location, name, ignored):
        item = self.location(location)["items"][name]
        item["ignored"] = bool(ignored)
        item["manual"] = True
        if ignored:
            item["count"] = 0
        self.save()

    def rename(self, location, name, new_name):
        """Umbenennen; gibt es den neuen Namen schon, werden beide zusammengeführt. Die Kamera
        verwendet ab jetzt den neuen Namen (der alte wird als Schreibweise gemerkt)."""
        loc = self.location(location)
        new_name = clean_name(new_name)
        if not new_name:
            raise ValueError("Name fehlt")
        item = loc["items"].pop(name)
        target = self.find(location, new_name)
        if target and target != name:
            other = loc["items"][target]
            other["count"] += item["count"]
            other["min"] = max(other.get("min", 0), item.get("min", 0))
            other["manual"] = True
            new_name = target
        else:
            item["manual"] = True
            loc["items"][new_name] = item
        loc["aliases"][norm(name)] = new_name
        for key, value in list(loc["aliases"].items()):
            if value == name:
                loc["aliases"][key] = new_name
        self.save()
        return new_name

    def delete(self, location, name):
        loc = self.location(location)
        loc["items"].pop(name, None)
        for key, value in list(loc["aliases"].items()):
            if value == name:
                del loc["aliases"][key]
        self.save()

    def remove_location(self, location):
        self.data["locations"].pop(location, None)
        self.save()

    # ------------------------------------------------------------------ Ausgabe
    def low_items(self, location):
        """Vorräte unter ihrem Mindestbestand (für die Einkaufsliste)."""
        loc = self.location(location)
        return sorted(name for name, item in loc["items"].items()
                      if not item.get("ignored") and item.get("min", 0) > 0 and item["count"] < item["min"])

    def snapshot(self):
        return self.data
