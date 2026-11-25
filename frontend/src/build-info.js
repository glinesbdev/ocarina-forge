/**
 * Page revision, stamped by Vite from `git rev-parse HEAD`.
 * The API reports its own revision on `/api/health`. A mismatch means one
 * process is still on an older checkout, or the browser is holding an old bundle.
 */
export function frontendBuild() {
  const revision = typeof __FORGE_REVISION__ === "string" ? __FORGE_REVISION__ : "";
  const committedAt = Number(typeof __FORGE_COMMITTED_AT__ === "string" ? __FORGE_COMMITTED_AT__ : 0) || 0;
  return { revision, committedAt };
}

export function shortRevision(revision) {
  const text = String(revision || "");
  if (!text || text === "unknown") return "";
  return text.slice(0, 7);
}

/**
 * `server-older` is a frontend that moved ahead of the running API.
 * `frontend-older` is a cached or stale bundle talking to a newer API.
 * That second case is how a working adapter can still show plain WebGL.
 */
export function describeBuildMismatch(page, server) {
  const pageRev = page && page.revision;
  if (!pageRev || pageRev === "unknown") return null;
  const serverRev = server && server.revision;
  if (!serverRev) {
    return {
      kind: "server-older",
      message: "The server did not report a build stamp. It is older than this page. Restart the Forge server.",
    };
  }
  if (serverRev === "unknown") return null;
  if (serverRev === pageRev) return null;
  const pageAt = Number(page && page.committedAt) || 0;
  const serverAt = Number(server && server.committedAt) || 0;
  const pageShort = shortRevision(pageRev);
  const serverShort = shortRevision(serverRev);
  if (pageAt && serverAt && serverAt < pageAt) {
    return {
      kind: "server-older",
      message: `Server build ${serverShort} is older than this page ${pageShort}. Restart the Forge server.`,
    };
  }
  if (pageAt && serverAt && pageAt < serverAt) {
    return {
      kind: "frontend-older",
      message: `This page is build ${pageShort}, older than the server ${serverShort}. A cached or stale bundle can stay on plain WebGL. Hard-reload. If the Python server is serving frontend/dist, rebuild that bundle.`,
    };
  }
  return {
    kind: "mismatch",
    message: `This page (${pageShort}) does not match the server (${serverShort}). Restart the Forge server and hard-reload.`,
  };
}

export const BUILD_NOTICE_KEY = "ocarinaforge.buildNotice";

export function buildNoticeId(page, server) {
  const skew = describeBuildMismatch(page, server);
  if (!skew) return "";
  return `${skew.kind}:${(page && page.revision) || ""}:${(server && server.revision) || ""}`;
}

export function buildNoticeVisible(storage, id) {
  if (!id) return false;
  try {
    return storage.getItem(BUILD_NOTICE_KEY) !== id;
  } catch {
    return true;
  }
}

export function dismissBuildNotice(storage, id) {
  try {
    storage.setItem(BUILD_NOTICE_KEY, id);
  } catch {
    /* private mode */
  }
}
