/** localStorage keys are `ocarinaforge.*`. `bancwright.*` is copied once. */

const LEGACY = "bancwright.";
const CURRENT = "ocarinaforge.";

export function migrateStorage(storage) {
  if (!storage || typeof storage.length !== "number") return;
  const keys = [];
  for (let index = 0; index < storage.length; index += 1) keys.push(storage.key(index));
  for (const key of keys) {
    if (!key || !key.startsWith(LEGACY)) continue;
    const next = CURRENT + key.slice(LEGACY.length);
    if (storage.getItem(next) === null) {
      const value = storage.getItem(key);
      if (value !== null) storage.setItem(next, value);
    }
  }
}
