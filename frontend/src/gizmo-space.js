/** World or local orientation for the move and rotate gizmos.
 *
 * Scale is always local: a world-space scale gizmo shears when the actor is
 * rotated. The stored choice is the one Move and Rotate use, and it is what
 * the toolbar shows. */

export const GIZMO_SPACES = ["world", "local"];
export const GIZMO_SPACE_KEY = "ocarinaforge.gizmoSpace";

export function normalizeSpace(value) {
  return value === "local" ? "local" : "world";
}

export function spaceForMode(mode, chosen) {
  if (mode === "scale") return "local";
  return normalizeSpace(chosen);
}

export function toggleSpace(chosen) {
  return normalizeSpace(chosen) === "local" ? "world" : "local";
}

export function readStoredSpace(storage) {
  try {
    return normalizeSpace(storage && storage.getItem(GIZMO_SPACE_KEY));
  } catch {
    return "world";
  }
}

export function writeStoredSpace(storage, space) {
  if (!storage) return;
  try {
    storage.setItem(GIZMO_SPACE_KEY, normalizeSpace(space));
  } catch {
    /* private mode */
  }
}
