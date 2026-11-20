/** Keyboard and mouse bindings. The Shortcuts dialog and docs/controls.md both use this list. */

export const SHORTCUT_GROUPS = [
  {
    id: "camera",
    label: "Camera",
    items: [
      { keys: "Hover the view", action: "Focus the viewport so fly keys work without a click" },
      { keys: "Right-drag", action: "Look around" },
      { keys: "W A S D", action: "Move" },
      { keys: "Q / E", action: "Down / up" },
      { keys: "Shift", action: "Move faster" },
      { keys: "Alt+left-drag", action: "Orbit the point under the cursor, or the selection" },
      { keys: "Middle-drag", action: "Pan" },
      { keys: "Scroll", action: "Zoom" },
      { keys: "F", action: "Frame the selection, or the whole scene" },
    ],
  },
  {
    id: "selection",
    label: "Selection and editing",
    items: [
      { keys: "Click", action: "Select the actor under the cursor" },
      { keys: "Alt-click, or click again", action: "Cycle overlapping actors" },
      { keys: "H", action: "Hide or show the selection" },
      { keys: "Delete", action: "Delete the selection" },
      { keys: "B", action: "Open the behavior graph for the selection" },
    ],
  },
  {
    id: "gizmo",
    label: "Gizmo",
    items: [
      { keys: "G", action: "Move" },
      { keys: "R", action: "Rotate" },
      { keys: "Drag a handle", action: "Transform the selection" },
    ],
  },
  {
    id: "history",
    label: "Undo and redo",
    items: [
      { keys: "Ctrl+Z or Cmd+Z", action: "Undo" },
      { keys: "Ctrl+Shift+Z, Cmd+Shift+Z, or Ctrl+Y", action: "Redo" },
    ],
  },
  {
    id: "snap",
    label: "Snap",
    items: [{ keys: "V", action: "Toggle snap to ground and collision" }],
  },
  {
    id: "search",
    label: "Search",
    items: [
      { keys: "Ctrl+K or Cmd+K", action: "Open search" },
      { keys: "?", action: "Open this shortcut list" },
    ],
  },
  {
    id: "view",
    label: "View toggles",
    items: [
      { keys: "Esc", action: "Close a dialog, release mouse look, or clear the selection" },
      { keys: "Stats", action: "The overlay starts on. The toolbar checkbox hides it." },
    ],
  },
];

export function shortcutText() {
  const lines = ["# Shortcuts", "", "The editor shows this same list with ? or the Shortcuts button.", ""];
  for (const group of SHORTCUT_GROUPS) {
    lines.push(`## ${group.label}`, "");
    for (const item of group.items) lines.push(`- ${item.keys}: ${item.action}`);
    lines.push("");
  }
  return lines.join("\n");
}
