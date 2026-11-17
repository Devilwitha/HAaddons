"""Aus wenigen Einstellungen (Farben, Rundung, Schatten …) ein vollständiges Home-Assistant-Theme erzeugen.

Ein Theme im Editor besteht aus Parametern wie in presets.py. render() macht daraus die
HA-Variablen je Modus (dark/light), card_mod_css() das optionale CSS für card-mod und to_yaml()
die Datei für /config/themes.
"""
import copy
import json
import re

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
NAME_RE = re.compile(r"^[^\s:#\"'][^:#\"'\n]{0,47}$")
VAR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")

FONTS = {
    "system": '"Segoe UI", Roboto, system-ui, -apple-system, sans-serif',
    "roboto": "Roboto, Noto, sans-serif",
    "rounded": 'ui-rounded, "SF Pro Rounded", Nunito, "Varela Round", "Segoe UI", system-ui, sans-serif',
    "serif": 'Georgia, "Iowan Old Style", Cambria, "Times New Roman", serif',
    "mono": 'ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas, monospace',
    "condensed": 'Bahnschrift, "Roboto Condensed", "Arial Narrow", system-ui, sans-serif',
}
BACKGROUNDS = ("flat", "glow", "gradient", "image")
BARS = ("transparent", "card", "accent")
MODES = ("dark", "light")

DEFAULTS = {
    "name": "Mein Theme",
    "modes": ["dark"],
    "primary": "#38bdf8",
    "accent": "#a855f7",
    "dark": {"background": "#0b1120", "card": "#1e293b", "text": "#f1f5f9"},
    "light": {"background": "#f1f5f9", "card": "#ffffff", "text": "#0f172a"},
    "background": "glow",
    "background_image": "",
    "intensity": 50,
    "card_opacity": 80,
    "radius": 16,
    "border": 1,
    "border_accent": False,
    "shadow": 40,
    "font": "system",
    "header": "transparent",
    "sidebar": "transparent",
    "blur": 0,
    "hover": False,
    "gradient_buttons": False,
    "overrides": {},
}

STATUS = {
    "dark": {"success": "#4ade80", "error": "#f87171", "warning": "#fbbf24", "light": "#fbbf24",
             "heat": "#fb923c"},
    "light": {"success": "#16a34a", "error": "#dc2626", "warning": "#d97706", "light": "#f59e0b",
              "heat": "#ea580c"},
}


# --------------------------------------------------------------------------- Farben
def hex_rgb(value):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a, b, t):
    """Farbe a, um t (0…1) Richtung b verschoben."""
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def rgba(color, alpha):
    r, g, b = hex_rgb(color)
    return f"rgba({r}, {g}, {b}, {round(alpha, 2)})"


def rgb_list(color):
    return ", ".join(str(c) for c in hex_rgb(color))


def luminance(color):
    def chan(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in hex_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(color):
    """Schriftfarbe auf einer Fläche dieser Farbe."""
    return "#0b1120" if contrast(color, "#0b1120") >= contrast(color, "#ffffff") else "#ffffff"


def ensure_contrast(color, background, minimum=3.0):
    """Akzentfarbe so weit abdunkeln/aufhellen, dass sie sich vom Hintergrund abhebt."""
    if contrast(color, background) >= minimum:
        return color
    target = "#000000" if luminance(background) > 0.4 else "#ffffff"
    for step in range(1, 11):
        candidate = mix(color, target, step * 0.08)
        if contrast(candidate, background) >= minimum:
            return candidate
    return mix(color, target, 0.8)


# --------------------------------------------------------------------------- Prüfen
def _color(value, fallback):
    value = str(value or "").strip()
    if HEX_RE.match(value):
        return rgb_hex(hex_rgb(value))
    return fallback


def _int(value, low, high, fallback):
    try:
        return max(low, min(high, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


def normalize(params):
    """Eingaben aus dem Editor prüfen und mit Standardwerten auffüllen."""
    params = params if isinstance(params, dict) else {}
    out = copy.deepcopy(DEFAULTS)
    name = " ".join(str(params.get("name") or "").split())
    if not NAME_RE.match(name):
        raise ValueError("Name ungültig: 1–48 Zeichen, ohne : # und Anführungszeichen")
    out["name"] = name
    modes = [m for m in MODES if m in (params.get("modes") or [])]
    out["modes"] = modes or ["dark"]
    out["primary"] = _color(params.get("primary"), DEFAULTS["primary"])
    out["accent"] = _color(params.get("accent"), DEFAULTS["accent"])
    for mode in MODES:
        given = params.get(mode) if isinstance(params.get(mode), dict) else {}
        for key in ("background", "card", "text"):
            out[mode][key] = _color(given.get(key), DEFAULTS[mode][key])
    out["background"] = params.get("background") if params.get("background") in BACKGROUNDS else "flat"
    image = str(params.get("background_image") or "").strip()
    if image and not re.match(r"^(/local/|https?://)[^\s\"'()\\]+$", image):
        raise ValueError("Hintergrundbild: Adresse muss mit /local/ oder http(s):// beginnen")
    out["background_image"] = image
    if out["background"] == "image" and not image:
        out["background"] = "flat"
    out["intensity"] = _int(params.get("intensity"), 0, 100, DEFAULTS["intensity"])
    out["card_opacity"] = _int(params.get("card_opacity"), 20, 100, DEFAULTS["card_opacity"])
    out["radius"] = _int(params.get("radius"), 0, 32, DEFAULTS["radius"])
    out["border"] = _int(params.get("border"), 0, 3, DEFAULTS["border"])
    out["border_accent"] = bool(params.get("border_accent"))
    out["shadow"] = _int(params.get("shadow"), 0, 100, DEFAULTS["shadow"])
    out["font"] = params.get("font") if params.get("font") in FONTS else "system"
    out["header"] = params.get("header") if params.get("header") in BARS else "transparent"
    out["sidebar"] = params.get("sidebar") if params.get("sidebar") in BARS else "transparent"
    out["blur"] = _int(params.get("blur"), 0, 30, 0)
    out["hover"] = bool(params.get("hover"))
    out["gradient_buttons"] = bool(params.get("gradient_buttons"))
    overrides = {}
    for key, value in (params.get("overrides") or {}).items():
        key = str(key).strip().lstrip("-")
        value = str(value).strip()
        if not key or not value:
            continue
        if not VAR_RE.match(key) or "\n" in value or len(value) > 500:
            raise ValueError(f"Eigene Variable „{key}“ ungültig")
        overrides[key] = value
    out["overrides"] = overrides
    return out


# --------------------------------------------------------------------------- Erzeugen
def _shadow(strength, dark):
    if strength <= 0:
        return "none"
    alpha = (0.12 + 0.38 * strength / 100) if dark else (0.04 + 0.14 * strength / 100)
    return f"0 {round(2 + strength * 0.18)}px {round(6 + strength * 0.54)}px rgba(0, 0, 0, {round(alpha, 2)})"


def _lovelace_background(p, bg, dark):
    k = p["intensity"] / 100
    primary, accent = p["primary"], p["accent"]
    if p["background"] == "glow":
        alpha = (0.08 + 0.3 * k) if dark else (0.06 + 0.22 * k)
        return (f"radial-gradient(60vmax circle at 0% 0%, {rgba(primary, alpha)}, transparent 60%), "
                f"radial-gradient(60vmax circle at 100% 100%, {rgba(accent, alpha)}, transparent 60%), "
                f"{bg} fixed")
    if p["background"] == "gradient":
        t = 0.06 + 0.3 * k
        return f"linear-gradient(160deg, {mix(bg, primary, t)} 0%, {bg} 55%, {mix(bg, accent, t)} 100%) fixed"
    if p["background"] == "image":
        veil = rgba(bg, 0.15 + 0.7 * k)
        return (f"linear-gradient({veil}, {veil}), "
                f"center / cover no-repeat url(\"{p['background_image']}\") fixed, {bg}")
    return bg


def _bar(style, bg, card, primary, opacity):
    if style == "accent":
        return primary, readable_on(primary)
    if style == "card":
        return card, None
    return rgba(bg, opacity), None


def render_mode(p, mode):
    dark = mode == "dark"
    colors = p[mode]
    bg, card, text = colors["background"], colors["card"], colors["text"]
    primary = ensure_contrast(p["primary"], bg, 2.6)
    accent = ensure_contrast(p["accent"], bg, 2.2)
    dim = mix(text, bg, 0.32)
    disabled = mix(text, bg, 0.55)
    line = rgba(text, 0.12 if dark else 0.14)
    surface = mix(card, bg, 0.35) if dark else card
    status = STATUS[mode]
    on_primary = readable_on(primary)
    border_color = rgba(primary, 0.45) if p["border_accent"] else line
    r = p["radius"]
    small_r = round(r * 0.65)
    font = FONTS[p["font"]]
    header_bg, header_text = _bar(p["header"], bg, card, primary, 0.6)
    sidebar_bg, sidebar_text = _bar(p["sidebar"], bg, card, primary, 0.85)
    side_text = sidebar_text or dim
    side_sel = sidebar_text or primary
    fill = rgba(text, 0.07 if dark else 0.05)

    v = {
        # Grundfarben
        "primary-color": primary,
        "accent-color": accent,
        "dark-primary-color": mix(primary, "#000000", 0.18),
        "light-primary-color": mix(primary, "#ffffff", 0.35),
        "primary-text-color": text,
        "secondary-text-color": dim,
        "text-primary-color": on_primary,
        "text-light-primary-color": text,
        "disabled-text-color": disabled,
        "divider-color": line,
        "outline-color": line,
        "rgb-primary-color": rgb_list(primary),
        "rgb-accent-color": rgb_list(accent),
        "rgb-primary-text-color": rgb_list(text),
        "rgb-secondary-text-color": rgb_list(dim),
        "rgb-text-primary-color": rgb_list(on_primary),
        "rgb-card-background-color": rgb_list(card),
        # Hintergrund
        "primary-background-color": bg,
        "secondary-background-color": mix(bg, card, 0.6),
        "card-background-color": card,
        "lovelace-background": _lovelace_background(p, bg, dark),
        # Karten
        "ha-card-background": rgba(card, p["card_opacity"] / 100),
        "ha-card-border-radius": f"{r}px",
        "ha-card-border-width": f"{p['border']}px",
        "ha-card-border-color": border_color,
        "ha-card-box-shadow": _shadow(p["shadow"], dark),
        "ha-card-header-color": text,
        "ha-dialog-border-radius": f"{min(r + 4, 28)}px",
        "ha-dialog-surface-background": surface,
        "mdc-theme-surface": surface,
        "dialog-backdrop-filter": "blur(8px)",
        "ha-dialog-scrim-backdrop-filter": "blur(8px)",
        # Kopfzeile und Seitenleiste
        "app-header-background-color": header_bg,
        "app-header-text-color": header_text or text,
        "app-header-selection-bar-color": header_text or primary,
        "app-header-edit-background-color": mix(bg, card, 0.8),
        "sidebar-background-color": sidebar_bg,
        "sidebar-text-color": side_text,
        "sidebar-icon-color": side_text,
        "sidebar-selected-text-color": side_sel,
        "sidebar-selected-icon-color": side_sel,
        "sidebar-menu-button-text-color": sidebar_text or text,
        "sidebar-menu-button-background-color": "transparent",
        # Zustände
        "state-icon-color": dim,
        "state-icon-active-color": primary,
        "state-active-color": primary,
        "state-inactive-color": dim,
        "state-on-color": primary,
        "state-off-color": dim,
        "state-unavailable-color": rgba(text, 0.3),
        "state-light-active-color": status["light"],
        "state-switch-active-color": primary,
        "state-fan-active-color": primary,
        "state-cover-active-color": accent,
        "state-media_player-active-color": accent,
        "state-climate-heat-color": status["heat"],
        "state-climate-cool-color": primary,
        "state-binary_sensor-active-color": accent,
        "state-person-home-color": status["success"],
        "state-person-not_home-color": dim,
        "success-color": status["success"],
        "error-color": status["error"],
        "warning-color": status["warning"],
        "info-color": primary,
        "green-color": status["success"],
        "red-color": status["error"],
        "amber-color": status["warning"],
        "blue-color": primary,
        "purple-color": accent,
        # Steuerelemente
        "switch-checked-color": primary,
        "switch-checked-button-color": primary,
        "switch-checked-track-color": primary,
        "switch-unchecked-button-color": mix(text, bg, 0.15) if dark else "#ffffff",
        "switch-unchecked-track-color": rgba(text, 0.25),
        "slider-color": primary,
        "slider-bar-color": rgba(text, 0.14),
        "paper-slider-knob-color": primary,
        "paper-slider-active-color": primary,
        "paper-slider-container-color": rgba(text, 0.14),
        "ha-control-border-radius": f"{small_r}px",
        "control-button-border-radius": f"{small_r}px",
        "mush-control-border-radius": f"{small_r}px",
        "mush-card-primary-color": text,
        "mush-card-secondary-color": dim,
        "ha-badge-border-radius": "999px" if r >= 10 else f"{r}px",
        # Eingabefelder
        "input-fill-color": fill,
        "input-disabled-fill-color": rgba(text, 0.02),
        "input-ink-color": text,
        "input-label-ink-color": dim,
        "input-idle-line-color": line,
        "input-hover-line-color": primary,
        "input-dropdown-icon-color": dim,
        "mdc-text-field-fill-color": fill,
        "mdc-text-field-ink-color": text,
        "mdc-text-field-label-ink-color": dim,
        "mdc-text-field-idle-line-color": line,
        "mdc-text-field-disabled-ink-color": disabled,
        "mdc-select-fill-color": fill,
        "mdc-select-ink-color": text,
        "mdc-select-label-ink-color": dim,
        "mdc-select-idle-line-color": line,
        "mdc-select-dropdown-icon-color": dim,
        "mdc-select-disabled-ink-color": disabled,
        "mdc-theme-primary": primary,
        "mdc-theme-secondary": accent,
        "mdc-theme-on-primary": on_primary,
        "mdc-theme-text-primary-on-background": text,
        "mdc-theme-text-secondary-on-background": dim,
        "mdc-theme-text-hint-on-background": dim,
        "mdc-checkbox-unchecked-color": dim,
        "mdc-radio-unchecked-color": dim,
        "mdc-button-outline-color": line,
        "mdc-button-disabled-ink-color": disabled,
        "ha-chip-background-color": fill,
        "ha-chip-text-color": text,
        "ha-assist-chip-filled-container-color": fill,
        # Badges, Tabellen, Code
        "label-badge-red": accent,
        "label-badge-background-color": rgba(card, p["card_opacity"] / 100),
        "label-badge-text-color": text,
        "table-row-background-color": "transparent",
        "table-row-alternative-background-color": rgba(text, 0.03),
        "data-table-background-color": "transparent",
        "code-editor-background-color": mix(bg, "#000000", 0.25) if dark else mix(bg, "#000000", 0.03),
        "markdown-code-background-color": rgba(text, 0.06),
        "scrollbar-thumb-color": rgba(text, 0.18),
        # Schrift
        "primary-font-family": font,
        "paper-font-common-base_-_font-family": font,
        "mdc-typography-font-family": font,
        "ha-font-family-body": font,
        "ha-font-family-heading": font,
        # Diagramme
        "history-unavailable-color": rgba(text, 0.2),
        "graph-color-1": primary,
        "graph-color-2": accent,
        "graph-color-3": status["success"],
        "graph-color-4": status["warning"],
        "graph-color-5": status["error"],
    }
    v.update(p["overrides"])
    return v


def card_mod_css(p):
    """CSS für card-mod (HACS): Weichzeichner, Anheben beim Darüberfahren, Verlaufs-Knöpfe."""
    if not (p["blur"] or p["hover"] or p["gradient_buttons"]):
        return None
    primary, accent = p["primary"], p["accent"]
    card, root = [], []
    if p["blur"]:
        card.append("ha-card {\n"
                    f"  backdrop-filter: blur({p['blur']}px);\n"
                    f"  -webkit-backdrop-filter: blur({p['blur']}px);\n"
                    "}")
        root.append(".header, app-toolbar, .toolbar {\n"
                    f"  backdrop-filter: blur({p['blur']}px);\n"
                    f"  -webkit-backdrop-filter: blur({p['blur']}px);\n"
                    "}")
    if p["hover"]:
        card.append("ha-card {\n  transition: transform 0.12s ease, box-shadow 0.12s ease;\n}\n"
                    "ha-card.type-button:hover,\nha-card.type-tile:hover {\n"
                    "  transform: translateY(-2px);\n"
                    f"  box-shadow: 0 10px 24px {rgba(primary, 0.25)};\n"
                    "}\n"
                    "ha-card.type-button:active {\n  transform: scale(0.96);\n}")
    if p["gradient_buttons"]:
        card.append("ha-card.type-button {\n"
                    f"  background: linear-gradient(160deg, {rgba(primary, 0.2)}, {rgba(accent, 0.14)});\n"
                    "}")
    return {"card": "\n".join(card), "root": "\n".join(root)}


def render(params):
    """Fertiges Theme: {"name", "modes": {mode: {var: wert}}, "card_mod": {...}|None}."""
    p = normalize(params)
    return {"name": p["name"], "modes": {m: render_mode(p, m) for m in p["modes"]}, "card_mod": card_mod_css(p)}


# --------------------------------------------------------------------------- YAML
def _scalar(value):
    # JSON-Zeichenketten sind gültiges YAML (doppelt gequotet)
    return json.dumps(str(value), ensure_ascii=False)


def _block(key, text, indent):
    pad = " " * indent
    lines = [f"{pad}{key}: |"]
    lines += [f"{pad}  {line}" if line else "" for line in text.split("\n")]
    return lines


def to_yaml(theme, header):
    """theme wie von render(); header = Kommentarzeilen am Anfang der Datei."""
    name = theme["name"]
    lines = [f"# {line}" if line else "#" for line in header]
    lines += [f"{_scalar(name)}:", "  modes:"]
    for mode, variables in theme["modes"].items():
        lines.append(f"    {mode}:")
        lines += [f"      {key}: {_scalar(value)}" for key, value in variables.items()]
    cm = theme.get("card_mod")
    if cm:
        lines.append("  # Nur mit card-mod (HACS), sonst ohne Wirkung")
        lines.append(f"  card-mod-theme: {_scalar(name)}")
        if cm.get("card"):
            lines += _block("card-mod-card", cm["card"], 2)
        if cm.get("root"):
            lines += _block("card-mod-root-yaml", ".: |\n" + "\n".join(
                "  " + line if line else "" for line in cm["root"].split("\n")), 2)
    return "\n".join(lines) + "\n"
