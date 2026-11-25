import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  actorMatchesQuery,
  buildOutlinerRows,
  centeredScrollTop,
  followSelection,
  measuredScrollTop,
  planViewportReveal,
  revealHint,
  virtualRevealTop,
  virtualSlice,
} from "./outliner.js";

const GROUPS = [
  ["npc", "NPC"],
  ["enemy", "Enemy"],
  ["object", "Object"],
  ["other", "Other"],
];

function actor(id, category, name = id) {
  return { id, gyaml: `Obj_${id}`, name, category };
}

function many(count, category = "object") {
  return Array.from({ length: count }, (_, index) => actor(`a${index}`, category, `Actor ${index}`));
}

test("search matches the name, class, hash, and category", () => {
  const row = { id: "Hash123", gyaml: "Obj_Tree", name: "Old Tree", category: "object" };
  assert.equal(actorMatchesQuery(row, "hash123"), true);
  assert.equal(actorMatchesQuery(row, "OBJ_tree"), true);
  assert.equal(actorMatchesQuery(row, "old"), true);
  assert.equal(actorMatchesQuery(row, "object"), true);
  assert.equal(actorMatchesQuery(row, "missing"), false);
  assert.equal(actorMatchesQuery(row, "  "), true);
});

test("rows group by category and keep scene order inside the group", () => {
  const rows = buildOutlinerRows({
    actors: [
      actor("tree", "object", "Tree"),
      actor("fado", "npc", "Fado"),
      actor("rock", "object", "Rock"),
    ],
    groups: GROUPS,
  });
  assert.deepEqual(
    rows.map((row) => (row.kind === "group" ? `group:${row.id}` : row.id)),
    ["group:npc", "fado", "group:object", "tree", "rock"],
  );
  assert.equal(rows[0].label, "NPC");
  assert.equal(rows[0].count, 1);
  assert.equal(rows[2].count, 2);
});

test("a collapsed group keeps its header and hides its actors", () => {
  const rows = buildOutlinerRows({
    actors: [actor("fado", "npc", "Fado"), actor("tree", "object", "Tree")],
    groups: GROUPS,
    collapsed: { npc: true },
  });
  assert.deepEqual(
    rows.map((row) => (row.kind === "group" ? `group:${row.id}:${row.collapsed}` : row.id)),
    ["group:npc:true", "group:object:false", "tree"],
  );
});

test("search drops sections with no matches", () => {
  const rows = buildOutlinerRows({
    actors: [actor("fado", "npc", "Fado"), actor("tree", "object", "Tree")],
    query: "fado",
    groups: GROUPS,
  });
  assert.deepEqual(
    rows.map((row) => row.id),
    ["npc", "fado"],
  );
  assert.equal(rows[0].count, 1);
});

test("a viewport pick in a collapsed group expands that group only", () => {
  const plan = planViewportReveal({
    actors: [actor("fado", "npc", "Fado"), actor("a0", "object", "Actor 0"), actor("a1", "object", "Actor 1")],
    selectedId: "a1",
    source: "viewport",
    groups: GROUPS,
    collapsed: { npc: true, object: true },
    clientHeight: 400,
  });
  assert.equal(plan.action, "reveal");
  assert.deepEqual(plan.expand, ["object"]);
  assert.equal(plan.virtual, false);
  assert.equal(plan.measure, true);
  assert.equal(plan.scrollTop, null);
  assert.equal(plan.behavior, "smooth");
  assert.equal(plan.block, "center");
  assert.equal(plan.rows.some((row) => row.kind === "actor" && row.id === "fado"), false);
  assert.equal(plan.rows.some((row) => row.kind === "actor" && row.id === "a1"), true);
  assert.equal(plan.rows.find((row) => row.kind === "group" && row.id === "npc").collapsed, true);
  assert.equal(plan.rows.find((row) => row.kind === "group" && row.id === "object").collapsed, false);
});

test("a search that hides the actor shows a hint and does not expand or scroll", () => {
  const hidden = actor("tree", "object", "Tree");
  assert.equal(revealHint(hidden, "fado"), "Search is hiding Tree.");
  assert.equal(revealHint(hidden, ""), "");
  const plan = planViewportReveal({
    actors: [actor("fado", "npc", "Fado"), hidden],
    selectedId: "tree",
    source: "viewport",
    query: "fado",
    groups: GROUPS,
    collapsed: { object: true },
    scrollTop: 80,
    clientHeight: 120,
  });
  assert.equal(plan.action, "hint");
  assert.equal(plan.hint, "Search is hiding Tree.");
  assert.deepEqual(plan.expand, []);
  let scrolled = false;
  const list = {
    scrollTop: 80,
    scrollTo() {
      scrolled = true;
    },
  };
  assert.equal(followSelection(list, plan, null), "none");
  assert.equal(scrolled, false);
  assert.equal(list.scrollTop, 80);
});

test("selecting from the panel does not scroll, even when the row would be off-screen", () => {
  const plan = planViewportReveal({
    actors: many(30),
    selectedId: "a20",
    source: "panel",
    groups: GROUPS,
    collapsed: { object: true },
    scrollTop: 0,
    clientHeight: 80,
    actorHeight: 40,
    groupHeight: 20,
    virtualizeAt: 1,
  });
  assert.equal(plan.action, "none");
  let scrolled = false;
  assert.equal(followSelection({ scrollTop: 0, scrollTo() { scrolled = true; } }, plan, null), "none");
  assert.equal(scrolled, false);
});

test("clearing the selection does not reveal", () => {
  assert.equal(planViewportReveal({ actors: many(2), selectedId: null, source: "viewport", groups: GROUPS }).action, "none");
  assert.equal(planViewportReveal({ actors: many(2), selectedId: "missing", source: "viewport", groups: GROUPS }).action, "none");
});

test("a virtualized list scrolls by index and does not measure rows", () => {
  const actors = many(500);
  const actorHeight = 40;
  const groupHeight = 20;
  const clientHeight = 100;
  // Header plus 100 actors before a100: 20 + 100 * 40 = 4020.
  // Center in a 100px view: 4020 - (100 - 40) / 2 = 3990.
  const plan = planViewportReveal({
    actors,
    selectedId: "a100",
    source: "viewport",
    groups: GROUPS,
    scrollTop: 0,
    clientHeight,
    actorHeight,
    groupHeight,
  });
  assert.equal(plan.virtual, true);
  assert.equal(plan.measure, false);
  assert.equal(plan.index, 101);
  assert.equal(plan.scrollTop, 3990);
  assert.equal(virtualRevealTop(plan.rows, plan.index, { scrollTop: 0, clientHeight, actorHeight, groupHeight }), 3990);
  assert.equal(plan.behavior, "smooth");
  let measured = 0;
  const calls = [];
  const list = {
    scrollTop: 0,
    clientHeight,
    getBoundingClientRect() {
      measured += 1;
      return { top: 0, height: clientHeight };
    },
    scrollTo(options) {
      calls.push(options);
      this.scrollTop = options.top;
    },
  };
  const row = {
    getBoundingClientRect() {
      measured += 1;
      return { top: 0, height: actorHeight };
    },
  };
  assert.equal(followSelection(list, plan, row), "index");
  assert.equal(measured, 0);
  assert.deepEqual(calls, [{ top: 3990, behavior: "smooth" }]);
  const slice = virtualSlice(plan.rows, { scrollTop: plan.scrollTop, clientHeight, actorHeight, groupHeight, overscan: 8 });
  assert.ok(slice.items.length < 40);
  assert.ok(slice.items.some((item) => item.row.id === "a100"));
  assert.ok(slice.total > clientHeight);
});

test("a virtualized row already in view does not move", () => {
  const plan = planViewportReveal({
    actors: many(500),
    selectedId: "a100",
    source: "viewport",
    groups: GROUPS,
    scrollTop: 3990,
    clientHeight: 100,
    actorHeight: 40,
    groupHeight: 20,
  });
  assert.equal(plan.action, "reveal");
  assert.equal(plan.scrollTop, null);
  let scrolled = false;
  assert.equal(followSelection({ scrollTop: 3990, scrollTo() { scrolled = true; } }, plan, null), "stay");
  assert.equal(scrolled, false);
});

test("a row above the fold scrolls back to center by index", () => {
  const plan = planViewportReveal({
    actors: many(500),
    selectedId: "a100",
    source: "viewport",
    groups: GROUPS,
    scrollTop: 5000,
    clientHeight: 100,
    actorHeight: 40,
    groupHeight: 20,
  });
  assert.equal(plan.scrollTop, 3990);
});

test("centered scroll leaves a fully visible row alone and centers one that is clipped", () => {
  assert.equal(centeredScrollTop({ scrollTop: 100, clientHeight: 200, rowTop: 130, rowHeight: 56 }), null);
  assert.equal(centeredScrollTop({ scrollTop: 0, clientHeight: 200, rowTop: 400, rowHeight: 56 }), 328);
  assert.equal(centeredScrollTop({ scrollTop: 0, clientHeight: 200, rowTop: 180, rowHeight: 56 }), 108);
  assert.equal(centeredScrollTop({ scrollTop: 40, clientHeight: 200, rowTop: 0, rowHeight: 56 }), 0);
});

test("measuring one row centers it only when it is outside the list", () => {
  const visible = {
    scrollTop: 100,
    clientHeight: 200,
    getBoundingClientRect: () => ({ top: 10, bottom: 210, height: 200 }),
  };
  const inside = { getBoundingClientRect: () => ({ top: 40, bottom: 96, height: 56 }) };
  assert.equal(measuredScrollTop(visible, inside), null);
  const below = {
    scrollTop: 0,
    clientHeight: 200,
    getBoundingClientRect: () => ({ top: 0, bottom: 200, height: 200 }),
  };
  const row = { getBoundingClientRect: () => ({ top: 400, bottom: 456, height: 56 }) };
  assert.equal(measuredScrollTop(below, row), 328);
  const calls = [];
  below.scrollTo = (options) => {
    calls.push(options);
    below.scrollTop = options.top;
  };
  const plan = { action: "reveal", virtual: false, measure: true, behavior: "smooth" };
  assert.equal(followSelection(below, plan, row), "measured");
  assert.deepEqual(calls, [{ top: 328, behavior: "smooth" }]);
});

test("the same scroll offset is not applied twice", () => {
  let calls = 0;
  const list = { scrollTop: 40, scrollTo() { calls += 1; } };
  assert.equal(followSelection(list, { action: "reveal", virtual: true, scrollTop: 40, behavior: "smooth" }, null), "stay");
  assert.equal(calls, 0);
});

test("the viewport pick reveals and a panel click does not", () => {
  const main = readFileSync(new URL("./main.js", import.meta.url), "utf8");
  const select = main.slice(main.indexOf("onSelect: (id) => {"), main.indexOf("onPreview:"));
  assert.match(select, /renderOutliner\(\{ reveal: "viewport" \}\)/);
  const row = main.slice(main.indexOf("function actorRow("), main.indexOf("function groupRow("));
  assert.match(row, /renderOutliner\(\{ reveal: "panel" \}\)/);
  assert.doesNotMatch(row, /reveal: "viewport"/);
  const group = main.slice(main.indexOf("function groupRow("), main.indexOf("function renderFilters("));
  assert.match(group, /renderOutliner\(\{ reveal: "panel" \}\)/);
  assert.doesNotMatch(group, /reveal: "viewport"/);
  const render = main.slice(main.indexOf("function renderOutliner("), main.indexOf("function renderFilters("));
  assert.match(render, /planViewportReveal\(/);
  assert.match(render, /followSelection\(/);
  assert.match(render, /virtualSlice\(/);
  assert.doesNotMatch(render, /scrollIntoView/);
  assert.doesNotMatch(render, /\.slice\(0,\s*400\)/);
});
