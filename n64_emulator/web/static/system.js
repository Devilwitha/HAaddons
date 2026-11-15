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
