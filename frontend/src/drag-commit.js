import { applyActorMatrix } from "./instance.js";
import { isExplicitLift, shouldSnapTranslate, translateAfterSnap } from "./snap.js";

/**
 * One step of a translate drag. The pivot and the visible instance are written
 * before this returns. A later ground reply must not be required to catch the
 * mesh up: the gizmo is attached to `position`, and the mesh the user sees is
 * the batched instance, which does not follow the pivot on its own.
 *
 * An explicit Y lift does not take the ground height. Any other snap that has
 * a finite surface writes that height into the pivot and the instance together.
 */
export function commitTranslateDrag({
  position,
  axis,
  phase,
  snapOn = false,
  surfaceY = null,
  slots = null,
  actorKey = "",
  matrix = null,
  write = null,
}) {
  const hold = Boolean(snapOn) && isExplicitLift(axis);
  const canSnap = Boolean(snapOn) && !hold && shouldSnapTranslate(axis, phase);
  const posed = translateAfterSnap(
    [position.x, position.y, position.z],
    canSnap ? surfaceY : null,
    { axis, phase, snapOn: canSnap },
  );
  position.y = posed.translate[1];
  let written = 0;
  if (typeof write === "function") {
    written = Number(write()) || 0;
  } else if (matrix && slots) {
    if (typeof matrix.setPosition === "function") matrix.setPosition(position.x, position.y, position.z);
    written = applyActorMatrix(slots, actorKey, matrix);
  }
  return {
    translate: [position.x, position.y, position.z],
    snapped: posed.snapped,
    hold,
    written,
  };
}
