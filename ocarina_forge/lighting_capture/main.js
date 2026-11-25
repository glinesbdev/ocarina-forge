// Default capture entry. Spec revision 4, section 7.2: there is no import().
// A mod's modules are its static imports, and a longshot module this API
// version lacks is refused at link time. This file therefore does not
// static-import longshot/gfx, so a build without that module still links.
// The dynamic import below is attempted only to record the runtime's
// error.message. On the 2026-11-25 run it threw TypeError, which matches
// the spec. lighting-mod --install --gfx-probe is the static-import build.

import { has } from "longshot";
import log from "longshot/log";
import { describeModule, errorDetail, start } from "./capture.js";

start(async () => {
  if (!has("gfx.world")) {
    const note = { present: false };
    log.info("FORGE_LIGHTING gfx " + JSON.stringify(note));
    return note;
  }
  try {
    const mod = await import("longshot/gfx");
    const note = { present: true, ...describeModule(mod) };
    log.info("FORGE_LIGHTING gfx " + JSON.stringify(note));
    return note;
  } catch (error) {
    const note = { present: true, import: errorDetail(error) };
    log.warn("FORGE_LIGHTING gfx " + JSON.stringify(note));
    return note;
  }
});
