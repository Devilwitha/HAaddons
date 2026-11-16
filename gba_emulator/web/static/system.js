/* Systembeschreibung – die einzige Web-Datei, die sich zwischen GBA- und N64-Add-on unterscheidet. */
window.EMU = {
  id: "gba",
  name: "GBA Emulator",
  short: "GBA",
  core: "gba",                                  // EmulatorJS-System (mGBA, spielt auch GB/GBC)
  exts: [".gba", ".gbc", ".gb", ".zip", ".7z"],
  folder: "/share/gba",
  accent: "#7c5cff",
  accent2: "#9d85ff",
  // Natürliche Größe des Touch-Gamepads in px: Breite links+rechts, Höhe inkl. L/R darüber
  pad: { width: 125 + 130, height: 125 + 90 },
  // Echte Controller: Tabelle (RetroPad-Nummer -> Konsolentaste) und Vorlagen mit EmulatorJS-Namen
  // (BUTTON_1 = untere Taste, BUTTON_2 = rechte, BUTTON_3 = linke, BUTTON_4 = obere)
  controller: {
    rows: [[8, "A"], [0, "B"], [10, "L"], [11, "R"], [3, "Start"], [2, "Select"],
      [[4, 5, 6, 7], "Steuerkreuz ↑ ↓ ← →"]],
    defaultPreset: "label",
    proPreset: "position",
    presets: {
      label: {
        name: { xbox: "Nach Beschriftung (Xbox A = A, B = B)", ps: "Bestätigen unten (✕ = A, ○ = B)",
          pro: "Vertauscht (B-Taste = A, A-Taste = B)" },
        map: { 8: "BUTTON_1", 0: "BUTTON_2", 10: "LEFT_TOP_SHOULDER", 11: "RIGHT_TOP_SHOULDER",
          3: "START", 2: "SELECT", 4: "DPAD_UP", 5: "DPAD_DOWN", 6: "DPAD_LEFT", 7: "DPAD_RIGHT" },
      },
      position: {
        name: { xbox: "Wie Nintendo-Anordnung (rechte Taste B = A)", ps: "Wie Nintendo-Anordnung (○ = A, ✕ = B)",
          pro: "Nach Beschriftung (A = A, B = B)" },
        map: { 8: "BUTTON_2", 0: "BUTTON_1", 10: "LEFT_TOP_SHOULDER", 11: "RIGHT_TOP_SHOULDER",
          3: "START", 2: "SELECT", 4: "DPAD_UP", 5: "DPAD_DOWN", 6: "DPAD_LEFT", 7: "DPAD_RIGHT" },
      },
    },
  },
};
document.documentElement.style.setProperty("--accent", window.EMU.accent);
document.documentElement.style.setProperty("--accent-2", window.EMU.accent2);
