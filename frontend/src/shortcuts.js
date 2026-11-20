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
      { keys: "B", action: "Open the behavior graph for the selection. It sits in the large panel under the view" },
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
      { keys: "Stats", action: "The overlay starts on. The toolbar checkbox hides it. The Stats panel shows the same line" },
    ],
  },
  {
    id: "windows",
    label: "Windows",
    items: [
      { keys: "Window menu", action: "Show or hide Viewport, Scenes, Actors, Inspector, Behavior, Details, Edits, Problems, and Stats" },
      { keys: "Drag a tab", action: "Dock that panel beside another, or drop it on a tab to share the group" },
      { keys: "Drag a split", action: "Resize the panels" },
      { keys: "Double-click a tab, or Max", action: "Fill the window with that group. Do it again to restore" },
      { keys: "Window, Pop out Behavior", action: "Open the behavior graph in its own window. Details and Inspector have the same command. Selection, edits, and undo stay in sync" },
      { keys: "Window, Reset layout", action: "Put the panels back, with the behavior graph under the view. This browser remembers the layout" },
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
