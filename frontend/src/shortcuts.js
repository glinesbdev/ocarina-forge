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
      { keys: "Shift or X", action: "Move faster. Use X while right-dragging in Firefox" },
      { keys: "Alt+left-drag", action: "Orbit the point under the cursor, or the selection" },
      { keys: "Middle-drag", action: "Pan" },
      { keys: "Scroll", action: "Zoom" },
      { keys: "F", action: "Frame the selection, or the whole scene. Over the behavior graph, F fits that graph" },
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
    id: "load",
    label: "Load filters",
    items: [
      { keys: "Actors panel, top", action: "Choose Full, Visual only, Layout, Terrain + water, or Logic. Custom appears when the checkboxes do not match a preset" },
      { keys: "Layer checkbox", action: "Fetch that layer. Turning one on loads it without reopening the scene" },
      { keys: "Show / Hide", action: "Hide a loaded layer. The Ground and Water stage-bar boxes stay in step with Terrain and Water" },
      { keys: "Load textures", action: "Download images after the mesh. Layout and textures=0 leave this off, and the editor shows a notice. The stage-bar Textures checkbox only shows or hides images already loaded, except the first check while this is off, which turns it on" },
      { keys: "?load=terrain,models", action: "Open with those layers. ?load=logic uses a preset. textures=0 skips images" },
    ],
  },
  {
    id: "gizmo",
    label: "Gizmo",
    items: [
      { keys: "G", action: "Move" },
      { keys: "R", action: "Rotate" },
      { keys: "Scale button", action: "Scale the selection. S stays fly-back, so scale has no letter." },
      { keys: "L", action: "Toggle world space and the selected actor's local space. Move and rotate follow it. Scale stays local. The toolbar shows the current space, and this browser remembers it" },
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
    items: [
      { keys: "V", action: "Toggle snap to ground and collision" },
      { keys: "Shift+V", action: "Toggle grid snap. Move uses 0.25, 0.5, 1, 5, or 10 metres. Rotate uses 5, 15, 45, or 90 degrees. Scale snap is the separate checkbox" },
      { keys: "Ctrl while dragging", action: "Flip grid snap for that drag. The toolbar toggle stays as it was" },
      { keys: "Arrow keys in a transform field", action: "With grid snap on, nudge position by the move increment and rotation by the degree increment. Scale nudges only when scale snap is on" },
      { keys: "Grid lines", action: "Draw a grid at the move increment, in world space or in the actor's local space" },
      { keys: "X, Z, or XZ drag", action: "With Snap on, change only the height. X and Z stay on the drag. The surface is the ground, a collision mesh, or the room model when the map has no ground. A hit more than a couple of metres above the actor is ignored. A miss keeps the current height. Releasing any translate handle except Y does the same" },
      { keys: "Y drag", action: "With Snap on, keep the height you leave the actor at, including after release. The gizmo stays on the model, so a second Y move is not needed to catch the mesh up" },
      { keys: "Dragged mesh", action: "Left out of the snap query: every submesh, batched instance, proxy, and companion copy of that actor, including a Land_ piece" },
    ],
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
      { keys: "Textures", action: "Show or hide images already loaded. Downloads continue while this is off when Load textures is on. The first check, when that filter is off, turns it on. The Actors panel checkbox is what skips the download, and the editor shows a notice then" },
      { keys: "Status", action: "The viewport label stays on. It names the backend, including a WebGPU fallback reason, the page revision, the draw count, layers that were not loaded, and texture progress while images stream" },
      { keys: "P", action: "Toggle the frame profile. It starts off, so timestamp queries stay out of the frame until the HUD is open" },
    ],
  },
  {
    id: "behavior",
    label: "Behavior graph",
    items: [
      { keys: "Middle-drag, right-drag, or Space-drag", action: "Pan the graph" },
      { keys: "Scroll", action: "Zoom toward the cursor" },
      { keys: "F, or Fit", action: "Fit the graph in the panel" },
      { keys: "Minimap", action: "Drag the map in the corner to pan. The rectangle is the current view" },
      { keys: "Click a node", action: "Select it and edit its fields. The change is a set field op. Its wires brighten and thicken, and flow wires animate. Nodes that feed it get a blue outline, nodes it feeds get an orange outline, and everything else dims to about 25%" },
      { keys: "Hover a node", action: "The same highlight, for that node, until the pointer leaves" },
      { keys: "Hover a wire or a pin", action: "That wire and its two ends stay bright. The rest dims" },
      { keys: "Esc, or click empty space", action: "Clear the highlight and the node selection. Esc still clears the actor when the pointer is not over the graph" },
      { keys: "Connections", action: "Inputs and outputs in the inspector, grouped by pin, in that pin's color. Hover a row to light that wire. Click a row to select the other node and pan to it" },
      { keys: "Outside view", action: "A wire whose other end is past the 160-node neighborhood. Clicking it recenters the neighborhood on that node" },
      { keys: "Alt+Left / Alt+Right", action: "Over the graph, back and forward through those connection jumps" },
    ],
  },
  {
    id: "windows",
    label: "Windows",
    items: [
      { keys: "Window menu", action: "Show or hide Viewport, Scenes, Actors, Inspector, Behavior, Details, Edits, and Problems" },
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
