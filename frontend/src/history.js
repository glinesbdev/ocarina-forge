/** Session undo. Each entry is one edit. The shot is the project diff and the
 * composed actor list after that edit. Undo restores the previous shot in
 * place; the file is written from that same shot, so the view does not wait
 * on a scene reload. */

export function blankHistory(baseline) {
  return { undo: [], redo: [], baseline: baseline || null };
}

export function pushEdit(history, entry) {
  const stack = history || blankHistory(null);
  return { undo: stack.undo.concat(entry), redo: [], baseline: stack.baseline || null };
}

export function popUndo(history) {
  const stack = history || blankHistory(null);
  if (!stack.undo.length) return null;
  const entry = stack.undo[stack.undo.length - 1];
  const undo = stack.undo.slice(0, -1);
  return {
    entry,
    scene: entry.local ? null : sceneFrom(stack.baseline, undo),
    history: { undo, redo: stack.redo.concat(entry), baseline: stack.baseline || null },
  };
}

export function popRedo(history) {
  const stack = history || blankHistory(null);
  if (!stack.redo.length) return null;
  const entry = stack.redo[stack.redo.length - 1];
  return {
    entry,
    scene: entry.local ? null : sceneFrom(stack.baseline, stack.undo),
    history: {
      undo: stack.undo.concat(entry),
      redo: stack.redo.slice(0, -1),
      baseline: stack.baseline || null,
    },
  };
}

function sceneFrom(baseline, undo) {
  for (let i = undo.length - 1; i >= 0; i -= 1) {
    if (!undo[i].local && undo[i].shot) return undo[i].shot;
  }
  return baseline || null;
}

export function historyRows(history) {
  const stack = history || blankHistory(null);
  const rows = [];
  for (let i = stack.undo.length - 1; i >= 0; i -= 1) {
    rows.push({ entry: stack.undo[i], place: "done", latest: i === stack.undo.length - 1 });
  }
  for (let i = stack.redo.length - 1; i >= 0; i -= 1) {
    rows.push({ entry: stack.redo[i], place: "redo", latest: false });
  }
  return rows;
}

function actorName(actor, command) {
  if (actor && (actor.name || actor.gyaml)) return actor.name || actor.gyaml;
  return (command && (command.actor || command.gyaml)) || "actor";
}

function sameVec(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  return left.every((value, index) => Math.abs(Number(value) - Number(right[index])) < 1e-4);
}

export function editLabel(command, actor, options = {}) {
  const name = actorName(actor, command);
  const kind = command && command.command;
  if (kind === "transform") {
    if (options.gizmo === "scale") return `Scale ${name}`;
    if (options.gizmo === "rotate") return `Rotate ${name}`;
    const moved = !actor || !sameVec(command.translate, actor.translate);
    const turned = !actor || !sameVec(command.rotate, actor.rotate);
    const scaled = Array.isArray(command.scale) && actor && !sameVec(command.scale, actor.scale);
    if (turned && !moved && !scaled) return `Rotate ${name}`;
    if (scaled && !moved && !turned) return `Scale ${name}`;
    return `Move ${name}`;
  }
  if (kind === "transform_set") {
    const extra = Array.isArray(command.actors) ? Math.max(0, command.actors.length - 1) : 0;
    return extra ? `Move ${name} · ${extra} decoration${extra === 1 ? "" : "s"}` : `Move ${name}`;
  }
  if (kind === "decoration_group") return command.parent ? `Group ${name}` : `Ungroup ${name}`;
  if (kind === "set") return `Edit ${name}`;
  if (kind === "set_name") return `Rename ${name}`;
  if (kind === "set_gyaml") return `Change ${name}`;
  if (kind === "add") return `Add ${command.actor || command.gyaml || name}`;
  if (kind === "duplicate") return `Duplicate ${name}`;
  if (kind === "remove") return `Delete ${name}`;
  if (kind === "hide") return `Hide ${name}`;
  if (kind === "unhide") return `Show ${name}`;
  if (kind === "revert" || kind === "revert_part") return `Revert ${name}`;
  if (kind === "lock") return `Lock ${options.category || name}`;
  return kind || "Edit";
}
