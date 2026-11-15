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
};
document.documentElement.style.setProperty("--accent", window.EMU.accent);
document.documentElement.style.setProperty("--accent-2", window.EMU.accent2);
