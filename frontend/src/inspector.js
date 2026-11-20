/** One line under the actor name. The material dump lives in the Details panel. */

export function modelLine(info) {
  if (!info || info.status === "loading" || info.status === "queued") return "Waiting to convert";
  if (info.status === "mesh") {
    const file = fileName(info.source) || "model";
    const count = Number(info.triangles) || 0;
    const triangles = count === 1 ? "1 triangle" : `${count.toLocaleString("en-US")} triangles`;
    return `${file} · ${triangles}`;
  }
  const reason = String(info.reason || "").trim();
  if (reason) return reason;
  const file = fileName(info.source);
  return file || "No model file";
}

function fileName(source) {
  if (!source) return "";
  const parts = String(source).split(/[/\\]/);
  return parts[parts.length - 1] || "";
}
