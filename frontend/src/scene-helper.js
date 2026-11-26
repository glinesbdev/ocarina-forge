/**
 * Black boards, dark planes, shadow boards, and black fades.
 *
 * The game hides these, or blends them as a fade or a shadow. Drawn opaque,
 * with no TEXCOORD, they are flat black sheets over the tree and the ground.
 * The names are the shape and the material. ``BlackBorad`` is the spelling
 * in the file. ``fadeblack`` is not a substring of ``BlackFade``, which is
 * why the old omit left them in the mesh.
 */
const HELPERS = [
  ["blackfade", "fade"],
  ["fadeblack", "fade"],
  ["blackborad", "board"],
  ["blackboard", "board"],
  ["darkplane", "dark"],
  ["shadowboard", "shadow"],
];

export function helperKind(...names) {
  const compact = names.join(" ").toLowerCase().replace(/[_\s]/g, "");
  for (const [token, kind] of HELPERS) {
    if (compact.includes(token)) return kind;
  }
  return "";
}
