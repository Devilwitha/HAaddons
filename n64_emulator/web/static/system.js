/* Systembeschreibung – die einzige Web-Datei, die sich zwischen GBA- und N64-Add-on unterscheidet. */
window.EMU = {
  id: "n64",
  name: "N64 Emulator",
  short: "N64",
  core: "n64",                                  // EmulatorJS: mupen64plus_next, auf iPhone/iPad parallel_n64
  exts: [".z64", ".n64", ".v64", ".zip", ".7z"],
  folder: "/share/n64",
  accent: "#1fa37a",
  accent2: "#35c497",
  // Natürliche Größe des Touch-Gamepads in px: Breite links+rechts (C-Tasten ragen 15 px über),
  // Höhe Block + L/Z/R darüber + Stick, der unten übersteht
  pad: { width: 125 + 145, height: 125 + 140 + 50 },
  // Echte Controller: Tabelle (RetroPad-Nummer -> N64-Taste) und Vorlagen mit EmulatorJS-Namen.
  // mupen64plus: RetroPad B (0) = N64 A, RetroPad Y (1) = N64 B, L2 (12) = Z,
  // linker Stick (16-19) = Analogstick, rechter Stick (20-23) = C-Tasten.
  controller: {
    rows: [[0, "A"], [1, "B"], [12, "Z"], [10, "L"], [11, "R"], [3, "Start"],
      [[16, 17, 18, 19], "Analogstick"], [[20, 21, 22, 23], "C-Tasten"], [[4, 5, 6, 7], "Steuerkreuz ↑ ↓ ← →"]],
    defaultPreset: "standard",
    presets: {
      standard: {
        name: { xbox: "Standard (A = A, X = B, LT = Z)", ps: "Standard (✕ = A, □ = B, L2 = Z)" },
        map: { 0: "BUTTON_1", 1: "BUTTON_3", 12: "LEFT_BOTTOM_SHOULDER", 10: "LEFT_TOP_SHOULDER",
          11: "RIGHT_TOP_SHOULDER", 3: "START", 4: "DPAD_UP", 5: "DPAD_DOWN", 6: "DPAD_LEFT", 7: "DPAD_RIGHT",
          16: "LEFT_STICK_X:+1", 17: "LEFT_STICK_X:-1", 18: "LEFT_STICK_Y:+1", 19: "LEFT_STICK_Y:-1",
          20: "RIGHT_STICK_X:+1", 21: "RIGHT_STICK_X:-1", 22: "RIGHT_STICK_Y:+1", 23: "RIGHT_STICK_Y:-1" },
      },
      alternative: {
        name: { xbox: "Alternative (A = A, B = B, RT = Z)", ps: "Alternative (✕ = A, ○ = B, R2 = Z)" },
        map: { 0: "BUTTON_1", 1: "BUTTON_2", 12: "RIGHT_BOTTOM_SHOULDER", 10: "LEFT_TOP_SHOULDER",
          11: "RIGHT_TOP_SHOULDER", 3: "START", 4: "DPAD_UP", 5: "DPAD_DOWN", 6: "DPAD_LEFT", 7: "DPAD_RIGHT",
          16: "LEFT_STICK_X:+1", 17: "LEFT_STICK_X:-1", 18: "LEFT_STICK_Y:+1", 19: "LEFT_STICK_Y:-1",
          20: "RIGHT_STICK_X:+1", 21: "RIGHT_STICK_X:-1", 22: "RIGHT_STICK_Y:+1", 23: "RIGHT_STICK_Y:-1" },
      },
    },
  },
  // Touch-Layout: Belegung wie der N64-Standard von EmulatorJS, aber L/Z hängen am Stick-Block
  // und R am C-Tasten-Block – so wachsen sie beim Vergrößern mit und nichts überdeckt sich.
  layout: [
    { type: "zone", id: "dpad", location: "left", left: "50%", top: "0%", joystickInput: false, inputValues: [4, 5, 6, 7] },
    { type: "zone", id: "stick", location: "left", left: "50%", top: "100%", joystickInput: true, inputValues: [16, 17, 18, 19] },
    { type: "button", text: "L", id: "l", location: "left", left: 3, top: -140, bold: true, block: true, input_value: 10 },
    { type: "button", text: "Z", id: "z", location: "left", left: 3, top: -100, bold: true, block: true, input_value: 12 },
    { type: "button", text: "R", id: "r", location: "right", right: 3, top: -140, bold: true, block: true, input_value: 11 },
    { type: "button", text: "B", id: "b", location: "right", left: -10, top: 95, bold: true, input_value: 1 },
    { type: "button", text: "A", id: "a", location: "right", left: 40, top: 150, bold: true, input_value: 0 },
    { type: "button", text: "CU", id: "cu", location: "right", left: 25, top: -65, fontSize: 20, joystickInput: true, input_value: 23 },
    { type: "button", text: "CD", id: "cd", location: "right", left: 25, top: 15, fontSize: 20, joystickInput: true, input_value: 22 },
    { type: "button", text: "CL", id: "cl", location: "right", left: -15, top: -25, fontSize: 20, joystickInput: true, input_value: 21 },
    { type: "button", text: "CR", id: "cr", location: "right", left: 65, top: -25, fontSize: 20, joystickInput: true, input_value: 20 },
    // Start oben neben R (in der Mitte unten würde er beim Vergrößern B überdecken)
    { type: "button", text: "Start", id: "start", location: "right", right: 63, top: -140, fontSize: 15, block: true, input_value: 3 },
  ],
};
document.documentElement.style.setProperty("--accent", window.EMU.accent);
document.documentElement.style.setProperty("--accent-2", window.EMU.accent2);
