/**
 * Exit a helper when its parent process is gone.
 *
 * A hard kill of the parent does not run the parent's atexit, and on Windows
 * it does not always deliver a signal. Stdin EOF is the other signal (the
 * pipe closes with the parent). This watch covers the case where the pipe
 * stays open.
 */

export function parentAlive(pid, kill = process.kill) {
  const id = Number(pid);
  if (!Number.isInteger(id) || id <= 0) return false;
  try {
    kill(id, 0);
    return true;
  } catch (error) {
    return !error || error.code !== "ESRCH";
  }
}

export function watchParent(pid, options = {}) {
  const exit = options.exit || process.exit;
  const kill = options.kill || process.kill;
  const intervalMs = options.intervalMs || 500;
  if (!parentAlive(pid, kill)) {
    exit(0);
    return null;
  }
  const timer = setInterval(() => {
    if (!parentAlive(pid, kill)) exit(0);
  }, intervalMs);
  if (timer && typeof timer.unref === "function") timer.unref();
  return timer;
}
