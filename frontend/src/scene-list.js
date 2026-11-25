// Pair only exact sibling bank paths. Age and other scene variants are part of the stem.
export function sceneEntries(scenes) {
  const byPath = new Map(scenes.map((scene) => [scene.path, scene]));
  const used = new Set();
  const entries = [];
  for (const scene of scenes) {
    if (used.has(scene.path)) continue;
    const match = /_(Static|Dynamic)(?=\.bcett\.byml(?:\.zs)?$)/.exec(scene.path);
    const siblingPath = match && (scene.path.slice(0, match.index) +
      (match[1] === "Static" ? "_Dynamic" : "_Static") + scene.path.slice(match.index + match[0].length));
    const sibling = byPath.get(siblingPath);
    used.add(scene.path);
    if (!sibling) {
      entries.push({ ...scene, banks: [scene.path] });
      continue;
    }
    used.add(sibling.path);
    const primary = match[1] === "Dynamic" ? scene : sibling;
    const companion = primary === scene ? sibling : scene;
    entries.push({ ...primary, name: primary.name.replace(/_Dynamic$/, ""), banks: [primary.path, companion.path] });
  }
  return entries;
}
