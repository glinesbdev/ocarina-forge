# Shortcuts

The editor shows this same list with ? or the Shortcuts button.

## Camera

- Hover the view: Focus the viewport so fly keys work without a click
- Right-drag: Look around
- W A S D: Move
- Q / E: Down / up
- Shift or X: Move faster. Use X while right-dragging in Firefox
- Alt+left-drag: Orbit the point under the cursor, or the selection
- Middle-drag: Pan
- Scroll: Zoom
- F: Frame the selection, or the whole scene. Over the behavior graph, F fits that graph

## Selection and editing

- Click: Select the actor under the cursor
- Alt-click, or click again: Cycle overlapping actors
- H: Hide or show the selection
- Delete: Delete the selection
- B: Open the behavior graph for the selection. It sits in the large panel under the view

## Load filters

- Actors panel, top: Choose Full, Visual only, Layout, Terrain + water, or Logic. Custom appears when the checkboxes do not match a preset
- Layer checkbox: Fetch that layer. Turning one on loads it without reopening the scene
- Show / Hide: Hide a loaded layer. The Ground and Water stage-bar boxes stay in step with Terrain and Water
- Load textures: Download images after the mesh. Layout and textures=0 leave this off. Images already stored stay in memory
- ?load=terrain,models: Open with those layers. ?load=logic uses a preset. textures=0 skips images

## Gizmo

- G: Move
- R: Rotate
- Scale button: Scale the selection. S stays fly-back, so scale has no letter.
- Drag a handle: Transform the selection

## Undo and redo

- Ctrl+Z or Cmd+Z: Undo
- Ctrl+Shift+Z, Cmd+Shift+Z, or Ctrl+Y: Redo

## Snap

- V: Toggle snap to ground and collision
- X, Z, or XZ drag: With Snap on, put the actor on the walkable surface: the ground, a collision mesh, or the room model when the map has no ground. The gizmo, the visible model, and the saved transform all use that height. A miss keeps the current height. Releasing any translate handle except Y does the same
- Y drag: With Snap on, keep the height you leave the actor at, including after release. The gizmo stays on the model, so a second Y move is not needed to catch the mesh up
- Dragged mesh: Left out of the snap query, including its proxies and a Land_ piece, so it does not snap to itself

## Search

- Ctrl+K or Cmd+K: Open search
- ?: Open this shortcut list

## View toggles

- Esc: Close a dialog, release mouse look, or clear the selection
- Textures: Show or hide images on the meshes. The switch is immediate. Downloads continue while it is off, and the status label counts them
- Status: The viewport label stays on. It names the backend, including a WebGPU fallback reason, the page revision, the draw count, layers that were not loaded, and texture progress while images stream
- P: Toggle the frame profile. It starts off, so timestamp queries stay out of the frame until the HUD is open

## Behavior graph

- Middle-drag, right-drag, or Space-drag: Pan the graph
- Scroll: Zoom toward the cursor
- F, or Fit: Fit the graph in the panel
- Minimap: Drag the map in the corner to pan. The rectangle is the current view
- Click a node: Select it and edit its fields. The change is a set field op. Its wires brighten and thicken, and flow wires animate. Nodes that feed it get a blue outline, nodes it feeds get an orange outline, and everything else dims to about 25%
- Hover a node: The same highlight, for that node, until the pointer leaves
- Hover a wire or a pin: That wire and its two ends stay bright. The rest dims
- Esc, or click empty space: Clear the highlight and the node selection. Esc still clears the actor when the pointer is not over the graph
- Connections: Inputs and outputs in the inspector, grouped by pin, in that pin's color. Hover a row to light that wire. Click a row to select the other node and pan to it
- Outside view: A wire whose other end is past the 160-node neighborhood. Clicking it recenters the neighborhood on that node
- Alt+Left / Alt+Right: Over the graph, back and forward through those connection jumps

## Windows

- Window menu: Show or hide Viewport, Scenes, Actors, Inspector, Behavior, Details, Edits, and Problems
- Drag a tab: Dock that panel beside another, or drop it on a tab to share the group
- Drag a split: Resize the panels
- Double-click a tab, or Max: Fill the window with that group. Do it again to restore
- Window, Pop out Behavior: Open the behavior graph in its own window. Details and Inspector have the same command. Selection, edits, and undo stay in sync
- Window, Reset layout: Put the panels back, with the behavior graph under the view. This browser remembers the layout
