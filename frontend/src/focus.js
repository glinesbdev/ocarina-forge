/** Whether hovering the view may move keyboard focus onto the canvas. */

const PLAIN_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "file", "image", "hidden", "range", "color"]);

export function canHoverFocus(active, dialogOpen) {
  if (dialogOpen) return false;
  if (!active) return true;
  const tag = active.tagName || "";
  if (tag === "TEXTAREA" || tag === "SELECT") return false;
  if (tag === "INPUT" && !PLAIN_INPUTS.has(String(active.type || "text").toLowerCase())) return false;
  if (active.isContentEditable) return false;
  return true;
}
