// forge-entry: gfx-probe
// Installed as main.js by lighting-mod --install --gfx-probe.
// Spec revision 4, section 7.2: every API module's default export is its
// frozen namespace. A dynamic import is not available, so this file uses
// a static import. It is for a build that already reports has("gfx.world").
// Exports whose feature is missing still exist and throw NotAvailableError
// when called. This file reads names and types only. It does not call them.
// Permission gfx.world is the one the spec attaches to longshot/gfx.

import gfx from "longshot/gfx";
import log from "longshot/log";
import { describeModule, start } from "./capture.js";

const note = { present: true, staticImport: true, ...describeModule(gfx) };
log.info("FORGE_LIGHTING gfx " + JSON.stringify(note));
start(() => note);
