"use strict";

// Preserve every character while aligning unchanged lines, then changed words.
const RecastDiff = (() => {
  const MAX_CELLS = 300000;

  function push(runs, kind, text) {
    if (!text) return;
    const last = runs[runs.length - 1];
    if (last && last.kind === kind) last.text += text;
    else runs.push({ kind, text });
  }

  function align(before, after) {
    let prefix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
    let suffix = 0;
    while (suffix < before.length - prefix && suffix < after.length - prefix && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix++;
    const a = before.slice(prefix, before.length - suffix);
    const b = after.slice(prefix, after.length - suffix);
    const runs = [];
    push(runs, "equal", before.slice(0, prefix).join(""));
    if (a.length && b.length && a.length * b.length <= MAX_CELLS) {
      const width = b.length + 1;
      const table = new Uint32Array((a.length + 1) * width);
      for (let i = a.length - 1; i >= 0; i--) {
        for (let j = b.length - 1; j >= 0; j--) {
          const cell = i * width + j;
          table[cell] = a[i] === b[j] ? 1 + table[cell + width + 1] : Math.max(table[cell + width], table[cell + 1]);
        }
      }
      let i = 0, j = 0;
      while (i < a.length && j < b.length) {
        if (a[i] === b[j]) { push(runs, "equal", a[i]); i++; j++; }
        else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) { push(runs, "remove", a[i++]); }
        else { push(runs, "add", b[j++]); }
      }
      while (i < a.length) push(runs, "remove", a[i++]);
      while (j < b.length) push(runs, "add", b[j++]);
    } else {
      push(runs, "remove", a.join(""));
      push(runs, "add", b.join(""));
    }
    push(runs, "equal", before.slice(before.length - suffix).join(""));
    return runs;
  }

  function compare(original, rewritten) {
    const lines = (text) => text.match(/[^\n]*\n|[^\n]+$/gu) || [];
    const words = (text) => text.match(/\s+|[^\s]+/gu) || [];
    const lineRuns = align(lines(original), lines(rewritten));
    const refined = [];
    let removed = "", added = "";
    const flush = () => {
      for (const run of align(words(removed), words(added))) push(refined, run.kind, run.text);
      removed = ""; added = "";
    };
    for (const run of lineRuns) {
      if (run.kind === "remove") removed += run.text;
      else if (run.kind === "add") added += run.text;
      else { flush(); push(refined, "equal", run.text); }
    }
    flush();
    return refined;
  }

  return { compare };
})();

if (typeof module !== "undefined") module.exports = RecastDiff;
