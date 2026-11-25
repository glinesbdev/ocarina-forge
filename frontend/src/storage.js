/** localStorage keys are `ocarinaforge.*`. `bancwright.*` is copied once. */

const LEGACY = "bancwright.";
const CURRENT = "ocarinaforge.";
/** The Stats toggle is gone. Old values are dropped and never copied forward. */
const RETIRED = new Set(["stats"]);

export function migrateStorage(storage) {
  if (!storage || typeof storage.length !== "number") return;
  const keys = [];
  for (let index = 0; index < storage.length; index += 1) keys.push(storage.key(index));
  for (const key of keys) {
    if (!key || !key.startsWith(LEGACY)) continue;
    const name = key.slice(LEGACY.length);
    if (RETIRED.has(name)) continue;
    const next = CURRENT + name;
    if (storage.getItem(next) === null) {
      const value = storage.getItem(key);
      if (value !== null) storage.setItem(next, value);
    }
  }
  for (const name of RETIRED) {
    const current = CURRENT + name;
    if (storage.getItem(current) !== null) storage.removeItem(current);
  }
}
