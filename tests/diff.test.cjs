const assert = require("node:assert/strict");
const { compare } = require("../recast/static/diff.js");

for (const [original, rewritten, removed, added] of [
  ["A measured result.", "A careful result.", "measured", "careful"],
  ["# Report\n\nThe wait was 12%.\n", "# Report\n\nThe delay was 12%.\n", "wait", "delay"],
  ["same\r\nline", "same\r\nline", "", ""],
  ["before", "", "before", ""],
  ["", "after", "", "after"],
]) {
  const runs = compare(original, rewritten);
  assert.equal(runs.filter(run => run.kind !== "add").map(run => run.text).join(""), original);
  assert.equal(runs.filter(run => run.kind !== "remove").map(run => run.text).join(""), rewritten);
  assert(runs.some(run => run.kind === "remove" && run.text.includes(removed)) || !removed);
  assert(runs.some(run => run.kind === "add" && run.text.includes(added)) || !added);
}

const longCommon = "unchanged line\n".repeat(3000);
const runs = compare(longCommon + "old phrase", longCommon + "new phrase");
assert.equal(runs.filter(run => run.kind !== "add").map(run => run.text).join(""), longCommon + "old phrase");
assert.equal(runs.filter(run => run.kind !== "remove").map(run => run.text).join(""), longCommon + "new phrase");
