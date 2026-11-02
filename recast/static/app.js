"use strict";
const $ = (id) => document.getElementById(id);
let job = null, reference = null, sourceRaw = null, sourceDirty = false, pollTimer = null;
let showChanges = true;
const busyStates = new Set(["queued", "analyzing", "rewriting", "cancelling"]);

function showError(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The local request failed.");
  return result;
}
function base64(bytes) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("Could not read the selected file."));
    reader.readAsDataURL(new Blob([bytes]));
  });
}
function sourceChanged() {
  const count = $("source-text").value.trim().split(/\s+/u).filter(Boolean).length;
  $("word-count").textContent = `${count.toLocaleString()} ${count === 1 ? "word" : "words"}`;
}
function resetJobView() {
  clearTimeout(pollTimer); job = null; localStorage.removeItem("recast-job");
  for (const id of ["profile-section", "result-section", "progress-section", "delete-button", "new-button", "reference-preview-wrap"]) $(id).hidden = true;
  setBusy(false); setStep("documents");
}
function setBusy(busy) {
  const lockedInputs = new Set(["analyze-button", "example-button", "source-text", "source-file", "source-format", "reference-file", "model", "strength"]);
  for (const id of ["analyze-button", "example-button", "source-text", "source-file", "source-format", "reference-file", "model", "strength", "rewrite-button", "profile-summary", "profile-rules", "profile-avoid", "delete-button", "new-button"]) $(id).disabled = busy || (!!job && lockedInputs.has(id));
  $("progress-section").hidden = !busy;
}
function setStep(step) {
  for (const item of ["documents", "style", "rewrite"]) {
    if (item === step) $("step-" + item).setAttribute("aria-current", "step");
    else $("step-" + item).removeAttribute("aria-current");
  }
}
function referenceSelected(name, description) {
  $("reference-name").textContent = name;
  $("reference-description").textContent = description;
  $("reference-picker").classList.add("selected");
}
$("source-text").addEventListener("input", () => { sourceDirty = true; sourceChanged(); });
$("source-format").addEventListener("change", () => {
  $("format-note").textContent = $("source-format").value === ".tex" ? "LaTeX commands, equations, citations, comments, and opaque environments are protected." : "Quotations, numbers, citations, code, and document structure are protected.";
});
$("source-file").addEventListener("change", async (event) => {
  try {
    showError(""); const file = event.target.files[0]; if (!file) return;
    if (file.size > 4 * 1024 * 1024) throw new Error("Choose a source file smaller than 4 MB.");
    if (!/\.(txt|md|markdown|tex|latex)$/i.test(file.name)) throw new Error("Use a text, Markdown, or LaTeX source file.");
    sourceRaw = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
    $("source-text").value = sourceRaw; sourceDirty = false;
    $("source-name").textContent = file.name;
    $("source-format").value = /\.(tex|latex)$/i.test(file.name) ? ".tex" : /\.(md|markdown)$/i.test(file.name) ? ".md" : ".txt";
    $("source-format").dispatchEvent(new Event("change")); sourceChanged();
  } catch (error) { showError(error.message); }
});
$("reference-file").addEventListener("change", async (event) => {
  try {
    showError(""); const file = event.target.files[0]; if (!file) return;
    if (file.size > 40 * 1024 * 1024) throw new Error("Choose a style reference smaller than 40 MB.");
    if (!/\.(docx|pdf|txt|md|markdown)$/i.test(file.name)) throw new Error("Use a DOCX, PDF, text, or Markdown style reference.");
    reference = { name: file.name, data: await base64(await file.arrayBuffer()) };
    referenceSelected(file.name, `${(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 0 })} KB · ready to analyze`);
  } catch (error) { reference = null; showError(error.message); }
});
$("reference-picker").addEventListener("keydown", (event) => {
  if (event.target !== $("reference-picker")) return;
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!$("reference-file").disabled) $("reference-file").click(); }
});
$("example-button").addEventListener("click", async () => {
  showError("");
  $("source-text").value = "The pilot study included 24 participants. The findings suggest that the new process may reduce waiting times by 12%. The team did not establish a causal relationship. Further studies are needed before the process is adopted more broadly.";
  sourceRaw = null; sourceDirty = true; $("source-format").value = ".txt"; $("source-name").textContent = "Example content"; sourceChanged();
  const text = "Start with what you can see. Then ask what it tells you. Good explanations move at a steady pace: a clear observation, a little context, and room to think. There is no need to dress a small result in grand language.\n\nKeep the reader beside you. Use ordinary words when they do the job. A short sentence can hold attention. A longer one can make the connection between an observation and the careful judgment that follows from it. Both have their place.\n\nAbove all, give each idea the space it needs. Say what happened. Explain why it matters. When the evidence leaves a question open, leave it open for the reader too.";
  reference = { name: "example-voice.txt", data: await base64(new TextEncoder().encode(text)) };
  referenceSelected("Example voice", "Plain, measured explanations with varied sentence lengths.");
});
$("analyze-button").addEventListener("click", async () => {
  try {
    showError("");
    const text = !sourceDirty && sourceRaw !== null ? sourceRaw : $("source-text").value;
    if (!text.trim()) throw new Error("Add the source text you want to rewrite.");
    if (!reference) throw new Error("Choose a style reference first.");
    const bytes = new TextEncoder().encode(text);
    if (bytes.length > 4 * 1024 * 1024) throw new Error("Source text exceeds the 4 MB limit.");
    resetJobView(); setBusy(true); $("progress-label").textContent = "Extracting the documents…";
    const created = await api("/api/jobs", { method: "POST", body: JSON.stringify({ source: { name: "source" + $("source-format").value, data: await base64(bytes) }, reference, model: $("model").value, strength: $("strength").value }) });
    localStorage.setItem("recast-job", created.id); await poll(created.id);
  } catch (error) { setBusy(false); showError(error.message); }
});
function renderProfile(current) {
  const profile = current.profile;
  if (!profile) return;
  $("profile-section").hidden = false;
  if ($("profile-section").dataset.profileJob !== current.id || $("profile-section").dataset.profileVersion !== JSON.stringify(profile)) {
    $("profile-summary").value = profile.summary;
    $("profile-rules").value = profile.rules.join("\n");
    $("profile-avoid").value = profile.avoid.join("\n");
    $("profile-section").dataset.profileJob = current.id;
    $("profile-section").dataset.profileVersion = JSON.stringify(profile);
    $("traits").replaceChildren();
    for (const trait of profile.traits) {
      const item = document.createElement("div"); item.className = "trait";
      const title = document.createElement("div"); title.className = "trait-title";
      const dimension = document.createElement("strong"); dimension.textContent = trait.dimension;
      const confidence = document.createElement("span"); confidence.textContent = trait.confidence + " confidence";
      const observation = document.createElement("p"); observation.textContent = trait.observation;
      title.append(dimension, confidence); item.append(title, observation);
      if (trait.evidence) { const quote = document.createElement("blockquote"); quote.textContent = trait.evidence; item.append(quote); }
      $("traits").append(item);
    }
  }
  $("download-profile").href = `/api/jobs/${current.id}/download?artifact=profile`;
}
function renderResult(current) {
  $("result-section").hidden = false;
  renderComparison(current);
  const findings = current.report.findings;
  $("result-status").textContent = findings.length ? `${findings.length} review ${findings.length === 1 ? "finding" : "findings"}. Check the notes before using the result.` : "No changes in meaning were flagged by the automated checks.";
  $("download-output").href = `/api/jobs/${current.id}/download?artifact=output`;
  $("download-report").href = `/api/jobs/${current.id}/download?artifact=report`;
  $("findings").replaceChildren();
  if (!findings.length) { const text = document.createElement("p"); text.textContent = "Protected spans were retained, and the model-assisted check reported no content changes."; $("findings").append(text); }
  findings.forEach((finding, index) => {
    const detail = document.createElement("details"); detail.className = "finding";
    const summary = document.createElement("summary"); summary.textContent = `${index + 1}. ${finding.source_retained ? "Original section retained" : finding.type.replaceAll("_", " ")} (${finding.block_id})`;
    const explanation = document.createElement("p"); explanation.textContent = finding.explanation;
    detail.append(summary, explanation);
    if (finding.source_excerpt) { const quote = document.createElement("blockquote"); quote.textContent = "Source: " + finding.source_excerpt; detail.append(quote); }
    if (finding.output_excerpt) { const quote = document.createElement("blockquote"); quote.textContent = "Attempt: " + finding.output_excerpt; detail.append(quote); }
    $("findings").append(detail);
  });
}
function renderComparison(current) {
  const original = $("original-output"), rewritten = $("rewritten-output");
  if (!showChanges) {
    original.textContent = current.source_text;
    rewritten.textContent = current.output_text;
    return;
  }
  const left = document.createDocumentFragment(), right = document.createDocumentFragment();
  for (const run of RecastDiff.compare(current.source_text, current.output_text)) {
    for (const [side, target] of [["remove", left], ["add", right]]) {
      if (run.kind !== side && run.kind !== "equal") continue;
      if (run.kind === "equal") target.append(document.createTextNode(run.text));
      else {
        const highlight = document.createElement("span");
        highlight.className = side === "remove" ? "change-removed" : "change-added";
        highlight.textContent = run.text;
        target.append(highlight);
      }
    }
  }
  original.replaceChildren(left);
  rewritten.replaceChildren(right);
}
function render(current) {
  job = current; const busy = busyStates.has(current.status); setBusy(busy);
  if ($("source-text").dataset.job !== current.id) {
    $("source-text").dataset.job = current.id; $("source-text").value = current.source_text;
    sourceRaw = current.source_text; sourceDirty = false; sourceChanged();
    $("source-name").textContent = current.source.name;
    $("source-format").value = current.source.format === "latex" ? ".tex" : current.source.format === "markdown" ? ".md" : ".txt";
    $("source-format").dispatchEvent(new Event("change"));
    $("model").value = current.model; $("strength").value = current.strength;
    referenceSelected(current.reference.name, "Reference text extracted and saved locally.");
  }
  $("delete-button").hidden = false; $("new-button").hidden = false;
  $("progress-label").textContent = current.stage;
  $("progress-detail").textContent = current.status === "rewriting" ? "Each section is rewritten, checked, and saved." : "Larger models can take a moment to load.";
  if (current.status === "rewriting") $("progress-bar").value = current.progress;
  else $("progress-bar").removeAttribute("value");
  $("cancel-button").disabled = current.status === "cancelling";
  $("reference-preview-wrap").hidden = false;
  $("reference-preview").textContent = current.reference_preview + (current.reference_preview_truncated ? "\n\n[Preview shortened; analysis samples are drawn across the complete extraction.]" : "");
  $("warnings").replaceChildren();
  for (const warning of current.warnings) { const text = document.createElement("p"); text.textContent = warning; $("warnings").append(text); }
  renderProfile(current);
  if (current.profile) setStep("style");
  if (current.status === "rewriting" || ["completed", "needs_review"].includes(current.status)) setStep("rewrite");
  if (["completed", "needs_review"].includes(current.status)) renderResult(current);
  if (current.error) showError(current.error);
  if (["cancelled", "interrupted"].includes(current.status)) showError(current.stage);
  $("rewrite-button").textContent = ["cancelled", "failed", "interrupted"].includes(current.status) ? "Resume rewriting" : "Rewrite my document";
}
async function poll(id) {
  clearTimeout(pollTimer);
  try {
    const current = await api(`/api/jobs/${id}`); render(current);
    if (busyStates.has(current.status)) pollTimer = setTimeout(() => poll(id), 1100);
  } catch (error) { setBusy(false); showError(error.message); }
}
$("rewrite-button").addEventListener("click", async () => {
  try {
    showError("");
    const profile = structuredClone(job.profile);
    profile.summary = $("profile-summary").value.trim();
    profile.rules = $("profile-rules").value.split("\n").map(x => x.trim()).filter(Boolean);
    profile.avoid = $("profile-avoid").value.split("\n").map(x => x.trim()).filter(Boolean);
    await api(`/api/jobs/${job.id}/rewrite`, { method: "POST", body: JSON.stringify({ profile }) });
    $("result-section").hidden = true; await poll(job.id);
  } catch (error) { showError(error.message); }
});
$("cancel-button").addEventListener("click", async () => { try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST", body: "{}" }); await poll(job.id); } catch (error) { showError(error.message); } });
$("copy-button").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("rewritten-output").textContent); $("copy-button").textContent = "Copied"; setTimeout(() => { $("copy-button").textContent = "Copy output"; }, 1800); } catch { showError("Use Download document to save the output, or select its text to copy it."); } });
$("changes-button").addEventListener("click", () => {
  showChanges = !showChanges;
  $("changes-button").textContent = showChanges ? "Hide highlights" : "Show highlights";
  $("changes-button").setAttribute("aria-pressed", String(showChanges));
  if (job && job.output_text !== undefined) renderComparison(job);
});
$("delete-button").addEventListener("click", async () => { try { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); resetJobView(); showError(""); } catch (error) { showError(error.message); } });
$("new-button").addEventListener("click", () => { resetJobView(); showError(""); window.scrollTo({ top: 0, behavior: "auto" }); });
async function loadModels() {
  try {
    const result = await api("/api/models");
    const selected = job?.model || $("model").value;
    $("model").replaceChildren(...result.models.map(model => { const option = document.createElement("option"); option.value = model.id; option.textContent = model.label; return option; }));
    $("model").value = result.models.some(model => model.id === selected) ? selected : "recast-default";
    $("connection-text").textContent = result.available ? "Local models connected" : "Local models are offline";
    $("connection-dot").classList.toggle("available", result.available);
    if (!result.available) $("connection-text").title = result.message;
  } catch (error) { showError(error.message); }
}
loadModels();
setInterval(loadModels, 15000);
const savedJob = localStorage.getItem("recast-job");
if (savedJob && /^[a-f0-9]{32}$/.test(savedJob)) poll(savedJob);
