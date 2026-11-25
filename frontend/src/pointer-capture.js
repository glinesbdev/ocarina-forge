// Three's controls call these methods directly. Capture can be unavailable while
// pointer lock is active, after the pointer ends, or while the canvas is detached.
export function guardPointerCapture(element) {
  for (const method of ["setPointerCapture", "releasePointerCapture"]) {
    const capture = element[method].bind(element);
    element[method] = (id) => {
      try {
        capture(id);
      } catch (error) {
        if (error?.name !== "InvalidStateError" && error?.name !== "NotFoundError") throw error;
      }
    };
  }
}
