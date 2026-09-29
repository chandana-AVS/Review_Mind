/* ReviewMind review workspace. All data is real: dataset PRs, saved ReviewMind runs, live Hindsight recall. */
const API = "/api/demo";
const EXAMPLE = `import httpx
from datetime import datetime

from tenacity import retry, stop_after_attempt


@retry(stop=stop_after_attempt(5))
def fetch_quote(base: str, quote: str) -> float:
    resp = httpx.get(f"https://fx.example/quotes/{base}{quote}", timeout=5)
    resp.raise_for_status()
    return float(resp.json()["rate"])


def quote_expires_at() -> str:
    return datetime.utcnow().isoformat()
`;

const state = {
  mode: "dataset",      // dataset | custom
  prs: [],
  prNumber: 137,
  pr: null,             // {number,title,author,files:[{path,lines}]}
  reviews: {},          // saved ReviewMind runs for the selected PR: {baseline, latest}
  run: "baseline",
  analysis: null,       // /analyze result for current PR or custom code
  recall: null,         // /memory-recall result
  file: null,
  selected: null,
  source: null,
  feedback: {},         // `${pr}:${run}:${id}` -> {decision, status, res}
  stage: "review",
  status: null,
  pipeline: null,       // [{key,label,state,detail}]
  noticeHtml: "",
  recallHtml: "",
  editing: false,
  customCode: EXAMPLE,
};

const CLS = {
  ARCHITECTURAL_DECISION: ["Architectural decision", "var(--arch)"],
  TEAM_CONVENTION: ["Team convention", "var(--conv)"],
  HISTORICAL_PATTERN: ["Historical pattern", "var(--hist)"],
  REPEATED_ISSUE: ["Repeated issue", "var(--hist)"],
  NEW: ["Generic", "var(--generic)"],
  PREVIOUSLY_REJECTED: ["Previously rejected", "var(--rejected)"],
};
const SEV = { high: "var(--high)", medium: "var(--medium)", low: "var(--low)", nit: "var(--low)" };
const PATTERN = {
  outbound_http: "Outbound HTTP call", retry: "Retry / backoff", time: "Current time", money: "Money / fees",
  config: "Configuration read", errors: "Error handling", logging: "Logging", db_session: "DB session / query",
  endpoint: "Mutating endpoint", webhook_health: "Webhook / health endpoint", private_helper: "Private helper",
  migration: "Schema migration", tests: "Tests", scripts: "One-off script",
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const review = () => state.reviews[state.run];
const hasReview = () => state.mode === "dataset" && !!review();
const findingById = (id) => review()?.findings.find((f) => f.id === id);
const fbKey = (id, run = state.run) => `${state.prNumber}:${run}:${id}`;
const shortFile = (p) => p.split("/").pop();
const firstSentence = (t) => ((String(t || "").match(/^.*?[.!?](\s|$)/) || [t])[0] || "").trim();
const isFeedbackDoc = (d) => (d || "").startsWith("feedback:");

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/* ---------------- syntax highlighting (Python, minimal) ---------------- */
const KW = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(" "));
function highlight(src) {
  const re = /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*')|(@[\w.]+)|(\b\d[\d_]*(?:\.\d+)?\b)|([A-Za-z_]\w*)(?=\s*\()|([A-Za-z_]\w*)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(src))) {
    out += esc(src.slice(last, m.index));
    const [t] = m;
    if (m[1]) out += `<span class="tok-com">${esc(t)}</span>`;
    else if (m[2]) out += `<span class="tok-str">${esc(t)}</span>`;
    else if (m[3]) out += `<span class="tok-dec">${esc(t)}</span>`;
    else if (m[4]) out += `<span class="tok-num">${esc(t)}</span>`;
    else if (m[5]) out += KW.has(t) ? `<span class="tok-kw">${t}</span>` : `<span class="tok-fn">${esc(t)}</span>`;
    else out += KW.has(t) ? `<span class="tok-kw">${t}</span>` : esc(t);
    last = m.index + t.length;
  }
  return out + esc(src.slice(last));
}

/* ---------------- data helpers ---------------- */
function sourceChips(sources) {
  return (sources || []).map((s) => `<button class="chip ${s.startsWith("ADR") ? "adr" : ""} ${state.source === s ? "active" : ""}" data-source="${esc(s)}" title="Highlight memories from ${esc(s)}">${esc(s)}</button>`).join("");
}
function signalLines() {
  // path -> line -> [pattern names], from the real /analyze result
  const out = {};
  for (const [name, locs] of Object.entries(state.analysis?.signals || {})) {
    for (const loc of locs) {
      const i = loc.lastIndexOf(":");
      const path = loc.slice(0, i), line = +loc.slice(i + 1);
      ((out[path] ||= {})[line] ||= []).push(name);
    }
  }
  return out;
}
function marksFor(file) {
  const flags = {}, supp = new Set();
  if (hasReview()) {
    review().findings.forEach((f) => { if (f.file === file && f.line) (flags[f.line] ||= []).push(f); });
    review().suppressed.filter((s) => s.file === file).forEach((s) => supp.add(s.line));
  }
  return { flags, supp, signals: signalLines()[file] || {} };
}
const currentFiles = () => state.mode === "custom" ? (state.analysis?.files || []) : (state.pr?.files || []);

/* ---------------- render: header / workspace ---------------- */
function renderHeader() {
  if (state.mode === "custom") {
    $("#pr-title").innerHTML = `<span class="num">Fast memory check</span>${esc($("#custom-file").value || "snippet.py")}`;
  } else if (state.pr) {
    const p = state.pr;
    $("#pr-title").innerHTML = `<span class="num">PR #${p.number}</span>${esc(p.title)}<span class="by">by ${esc(p.author)}</span>`;
  }
}

function renderStatus() {
  const s = state.status, el = $("#status");
  $("#bank-name").textContent = s?.demo_bank || "…";
  $("#modal-bank").textContent = s?.demo_bank || "";
  if (!s) return;
  if (s.connected) {
    const demo = s.banks?.[s.demo_bank];
    el.className = "status ok";
    $("#status-text").textContent = `Hindsight memory connected${demo ? ` · ${demo.total} memories` : ""}`;
    el.title = Object.entries(s.banks || {}).map(([b, c]) => `${b}: ${c.total} memories`).join("\n");
  } else {
    el.className = "status err";
    $("#status-text").textContent = "Hindsight offline · saved results only";
    el.title = s.error || "";
  }
}

function renderSelector() {
  $("#pr-select").innerHTML = state.prs.map((p) => {
    const tag = p.saved_runs.length ? "saved full review" : "memory check";
    return `<option value="${p.number}" ${p.number === state.prNumber ? "selected" : ""}>#${p.number} · ${esc(p.title)} — ${p.split} set · ${tag}</option>`;
  }).join("");
}

function renderWorkspace() {
  const custom = state.mode === "custom";
  document.querySelectorAll("#mode-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.mode === state.mode));
  $("#dataset-input").hidden = custom;
  $("#custom-input").hidden = !custom;
  $("#run-btn").textContent = custom ? "Run memory check" : "Run review";
  const pl = state.pipeline;
  $("#pipeline").innerHTML = pl
    ? pl.map((s) => `<li class="${s.state}"><span class="ic">${s.state === "done" ? "✓" : s.state === "running" ? '<span class="spinner"></span>' : s.state === "skip" ? "–" : "○"}</span>${esc(s.label)}${s.detail ? ` <b>${esc(s.detail)}</b>` : ""}</li>`).join("")
    : `<li class="idle">${custom ? "Paste code or a unified diff, then run a fast memory check." : hasReview() ? "Saved ReviewMind run loaded · press Run review to run the live memory steps" : "Press Run review"}</li>`;
}

/* ---------------- render: code ---------------- */
function renderTabs() {
  const custom = state.mode === "custom";
  $("#edit-btn").hidden = !(custom && !state.editing);
  $("#code-title").textContent = custom ? (state.editing ? "Your code" : "Your code · analyzed") : "Code under review";
  const files = currentFiles();
  $("#file-tabs").innerHTML = (custom && state.editing) ? "" : files.map((f) => {
    const n = Object.keys(marksFor(f.path).flags).length;
    return `<button data-file="${esc(f.path)}" class="${f.path === state.file ? "active" : ""}" title="${esc(f.path)}">${esc(shortFile(f.path))}${n ? `<span class="marks">●${n > 1 ? n : ""}</span>` : ""}</button>`;
  }).join("");
}

function renderCode() {
  const editing = state.mode === "custom" && state.editing;
  $("#editor").hidden = !editing;
  $("#code").hidden = editing;
  if (editing) { if ($("#editor").value !== state.customCode) $("#editor").value = state.customCode; return; }
  const file = currentFiles().find((f) => f.path === state.file) || currentFiles()[0];
  if (!file) { $("#code").innerHTML = ""; return; }
  state.file = file.path;
  const { flags, supp, signals } = marksFor(file.path);
  const sel = findingById(state.selected);
  $("#code").innerHTML = file.lines.map((l) => {
    const fs = flags[l.n] || [];
    const pats = signals[l.n] || [];
    const mark = fs.length ? (SEV[fs[0].severity] || "var(--high)") : "";
    const classes = ["line", l.kind === "+" ? "add" : "", fs.length ? "flag" : "", !fs.length && pats.length && !hasReview() ? "signal" : "",
      supp.has(l.n) && !fs.length ? "supp" : "", sel && sel.file === file.path && sel.line === l.n ? "selected" : ""].join(" ");
    const pins = fs.map((f) => `<span class="pin" data-finding="${f.id}" style="--mark:${CLS[f.classification]?.[1] || "var(--high)"}">${f.id}</span>`).join("")
      + (!hasReview() && pats.length ? `<span class="pin pat" title="${esc(pats.map((p) => PATTERN[p] || p).join(", "))}">${esc(PATTERN[pats[0]] || pats[0])}</span>` : "");
    return `<div class="${classes}" id="L${l.n}" style="${mark ? `--mark:${mark}` : ""}"><span class="ln">${l.n}</span><span class="gut">${l.kind === "+" ? "+" : ""}</span><span class="src">${highlight(l.text) || " "}</span><span class="pins">${pins}</span></div>`;
  }).join("");
}

/* ---------------- render: findings ---------------- */
function renderRunTabs() {
  const runs = state.mode === "dataset" ? Object.keys(state.reviews) : [];
  $("#run-tabs").hidden = runs.length < 2;
  $("#run-tabs").innerHTML = runs.length < 2 ? "" : [["baseline", "Historical memory"], ["latest", "After feedback"]]
    .filter(([k]) => runs.includes(k))
    .map(([k, label]) => `<button data-run="${k}" class="${state.run === k ? "active" : ""}">${label}</button>`).join("");
  const r = review();
  if (hasReview()) {
    const when = new Date(r.created_at).toLocaleString([], { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
    $("#run-meta").innerHTML = `Saved ReviewMind run · <b>${when}</b> · bank <code>${esc(r.bank)}</code> · ${r.memories_recalled} memories recalled · reasoning ${Math.round(r.seconds.reflect_s)}s`;
  } else {
    $("#run-meta").innerHTML = state.recall ? `Fast memory check · no reflect · bank <code>${esc(state.recall.bank)}</code>` : "";
  }
}

function findingCard(f) {
  const [label, color] = CLS[f.classification] || CLS.NEW;
  const selected = state.selected === f.id;
  const fb = state.feedback[fbKey(f.id)];
  const mem = f.basis === "hindsight_memory";
  const ev = f.memory_evidence || [];
  const codeLine = state.pr?.files.find((x) => x.path === f.file)?.lines.find((l) => l.n === f.line)?.text?.trim();
  const topSource = (f.sources || []).find((s) => s.startsWith("ADR")) || (f.sources || [])[0];
  const why = mem ? `
    <details class="why" ${selected ? "open" : ""}>
      <summary>Why did ReviewMind say this?</summary>
      <dl class="why-grid">
        <dt>Current code</dt><dd><pre>${esc(shortFile(f.file))}:${f.line}  ${esc(codeLine || "")}</pre></dd>
        <dt>Recalled memory</dt><dd>${ev.slice(0, 2).map((e) => `<div class="mem-quote">${esc(e.text.split(" | ")[0])}<div class="t">${esc(e.type)} · ${esc(e.document_id || "consolidated observation")}</div></div>`).join("")}</dd>
        <dt>Historical source</dt><dd class="chips" style="margin:0">${sourceChips(f.sources)}</dd>
        <dt>Relationship</dt><dd><div class="chain"><span>${esc(topSource || "team memory")}</span><i>→</i><span>${esc(f.category)}</span><i>→</i><span>${esc(shortFile(f.file))}:${f.line}</span><i>→</i><span>${esc(f.id)}</span></div><div style="margin-top:5px">${esc(firstSentence(f.explanation))}</div></dd>
      </dl>
    </details>` : "";
  let actions = `<button class="btn sm" data-accept="${f.id}">Accept</button><button class="btn sm" data-reject="${f.id}">Reject</button>`;
  let result = "";
  if (fb) {
    if (fb.status === "pending") {
      actions = `<span class="spinner"></span><span class="state">Retaining in Hindsight… <span class="elapsed">0</span>s</span>`;
      result = `<div class="result-card pending"><div class="hint" style="margin:0">Hindsight is extracting memory facts from this feedback (usually about a minute; unchanged feedback returns instantly).</div></div>`;
    } else if (fb.status === "error") {
      result = `<div class="result-card error"><h4>Feedback not stored</h4><div>${esc(fb.error)}</div></div>`;
    } else {
      actions = `<span class="state ${fb.decision.toLowerCase()}">${fb.decision === "REJECTED" ? "✕ Rejected" : "✓ Accepted"} by ${esc(fb.developer)}</span>`;
      result = feedbackCard(fb.res);
    }
  }
  return `
    <article class="finding ${selected ? "selected" : ""}" data-id="${f.id}" style="--cls:${color};--sevc:${SEV[f.severity] || "var(--low)"}">
      <div class="f-head">
        <span class="tag">${label.toUpperCase()}</span><span class="sev">${esc(f.severity.toUpperCase())}</span>
        <a class="loc" data-goto="${f.id}">${esc(f.file)}:${f.line ?? "–"}</a>
      </div>
      <div class="f-title">${esc(f.id)} · ${esc(f.title)}</div>
      <p class="f-expl">${esc(f.explanation)}</p>
      ${selected ? `<div class="f-rec"><span class="label">Recommendation</span>${esc(f.recommendation)}</div>` : ""}
      <div class="chips">${mem ? `<span class="basis mem">Memory evidence</span>${sourceChips(f.sources)}` : `<span class="basis">No team memory · generic finding</span>`}</div>
      ${why}
      <div class="f-actions">${actions}</div>
      ${result}
    </article>`;
}

function feedbackCard(res) {
  const units = res.stored_units || [];
  const types = [...new Set(units.map((u) => u.type))].join(" + ") || "—";
  return `
    <div class="result-card">
      <h4><span class="check">✓</span> Feedback retained in Hindsight</h4>
      <dl class="kv">
        <dt>Decision</dt><dd>${esc(res.feedback.decision)}</dd>
        <dt>Developer</dt><dd>${esc(res.feedback.developer)}</dd>
        ${res.feedback.reason ? `<dt>Reason</dt><dd>${esc(res.feedback.reason)}</dd>` : ""}
        <dt>Memory type</dt><dd>${esc(types)}</dd>
        <dt>Provenance</dt><dd>PR #${res.feedback.pr} · Finding ${esc(res.feedback.finding_id)}</dd>
        <dt>Hindsight</dt><dd>${res.status === "retained" ? `stored in ${res.seconds}s` : "already stored · unchanged (idempotent)"} · <code>${esc(res.document_id)}</code></dd>
      </dl>
      <div class="stored">${units.map((u) => `<div class="unit"><span class="t">${esc(u.type)}</span><br>${esc(u.text.split(" | ")[0])}</div>`).join("")}</div>
    </div>`;
}

function patternList() {
  const sig = state.analysis?.signals || {};
  const probes = state.analysis?.probes || [];
  if (!probes.length) return `<div class="empty"><b>No known code patterns detected</b>Nothing in this code matched the patterns ReviewMind recalls team memory for.</div>`;
  return probes.map((p) => {
    const locs = sig[p.name] || [];
    return `<div class="pattern"><div class="p-name">${esc(PATTERN[p.name] || p.name)}</div>
      <div class="p-q">Hindsight query · “${esc(p.query)}”</div>
      <div class="chips">${locs.slice(0, 8).map((l) => `<button class="chip" data-jump="${esc(l)}">${esc(shortFile(l))}</button>`).join("") || '<span class="basis">related pattern</span>'}</div></div>`;
  }).join("");
}

function renderFindings() {
  const count = $("#finding-count");
  if (hasReview()) {
    const r = review();
    const mem = r.findings.filter((f) => f.basis === "hindsight_memory");
    const gen = r.findings.filter((f) => f.basis !== "hindsight_memory");
    const group = (title, n) => `<div class="group-title">${title} <span class="count" style="margin:0">${n}</span><span class="rule"></span></div>`;
    let html = group("Backed by team memory", mem.length) + mem.map(findingCard).join("");
    html += group("Generic code quality", gen.length) + gen.map(findingCard).join("");
    if (r.suppressed.length) {
      html += group("Held back · team previously rejected", r.suppressed.length) + r.suppressed.map((s) => `
        <div class="supp-item" style="--cls:var(--rejected)">
          <div class="f-head"><span class="tag">PREVIOUSLY REJECTED</span><span class="loc">${esc(s.file)}:${s.line}</span></div>
          <div class="f-title">${esc(s.suggestion)}</div>
          <p class="f-expl">${esc(s.reason)}</p>
          <div class="chips"><span class="basis mem">Memory evidence</span>${sourceChips(s.sources)}</div>
        </div>`).join("");
    }
    $("#findings").innerHTML = html;
    count.textContent = `${r.findings.length} findings · ${r.suppressed.length} held back`;
    return;
  }
  count.textContent = "";
  if (!state.analysis) {
    $("#findings").innerHTML = state.mode === "custom"
      ? `<div class="empty"><b>Try your own code</b>Paste code or a unified diff on the left and press <b style="display:inline">Run memory check</b>. ReviewMind detects code patterns and asks Hindsight which team memories apply. It is a fast check, not a full review.</div>`
      : `<div class="empty"><b>PR #${state.prNumber} has no saved full review</b>Press Run review to read the diff, detect code patterns and recall matching team memory from Hindsight.</div>`;
    return;
  }
  const head = state.mode === "custom"
    ? `<div class="notice"><b>Fast memory check</b> · code patterns + real Hindsight recall. No full reasoning pass was run, so there are no findings, only the team memory that applies to this code.</div>`
    : `<div class="notice"><b>No saved full review for PR #${state.prNumber}.</b> Showing the fast memory check instead. A full review takes 5–10 minutes and runs offline: <code>review_pr.py --pr ${state.prNumber}</code></div>`;
  $("#findings").innerHTML = head + `<div class="group-title">Detected code patterns <span class="count" style="margin:0">${(state.analysis.probes || []).length}</span><span class="rule"></span></div>` + patternList();
}

/* ---------------- render: memory ---------------- */
function recalledList(highlightIds) {
  const mems = state.recall?.memories || [];
  return mems.map((m) => {
    const fb = isFeedbackDoc(m.document_id);
    const hit = highlightIds.has(m.id) || (state.source && m.sources.includes(state.source));
    return `<div class="mem-item ${fb ? "fb" : ""} ${hit ? "hit" : ""}"><span class="t">${esc(m.label)} · ${esc(m.type)}${fb ? " · developer feedback" : ""}</span>${esc(m.text.split(" | ")[0])}
      ${m.sources.length ? `<div class="chips" style="margin-top:4px">${sourceChips(m.sources)}</div>` : ""}</div>`;
  }).join("");
}

function renderMemory() {
  const sel = findingById(state.selected);
  const ids = new Set((sel?.memory_evidence || []).map((e) => e.memory_id));
  const liveN = state.recall?.memories.length || 0;
  if (hasReview()) {
    const r = review();
    const selAdrs = new Set([...(sel?.sources || []).filter((s) => s.startsWith("ADR")), ...(((sel?.title || "") + " " + (sel?.explanation || "")).match(/ADR-\d{3}/g) || [])]);
    const rules = r.team_brief.map((b) => {
      const srcs = [...new Set(b.evidence.flatMap((e) => e.sources))];
      const adr = (b.rule.match(/ADR-\d{3}/) || [])[0] || srcs.find((s) => s.startsWith("ADR"));
      const hit = (sel && (b.evidence.some((e) => ids.has(e.memory_id)) || (adr && selAdrs.has(adr)))) || (state.source && (srcs.includes(state.source) || adr === state.source));
      const chips = srcs.length ? `<div class="chips"><span class="label">Sources</span>${sourceChips(srcs)}</div>` : "";
      return `<div class="rule-card ${hit ? "hit" : ""}"><div class="kicker">Memory · ${esc(adr || srcs[0] || "Team memory")}</div><p>${esc(b.rule)}</p>${chips}</div>`;
    }).join("");
    const live = liveN ? `<details class="more"><summary>Live recall · ${liveN} memories · ${state.recall.seconds}s</summary>${recalledList(ids)}</details>` : "";
    $("#memory").innerHTML = `<div class="section-title" style="margin-top:0">Team rules this review applied</div>` + rules + live;
    $("#memory-count").textContent = `${r.team_brief.length} rules${liveN ? ` · ${liveN} recalled live` : ""}`;
  } else if (liveN) {
    $("#memory").innerHTML = `<div class="section-title" style="margin-top:0">Recalled from Hindsight · ${state.recall.seconds}s</div>` + recalledList(ids);
    $("#memory-count").textContent = `${liveN} memories`;
  } else {
    $("#memory").innerHTML = `<div class="empty" style="padding:12px 4px">Run a review or memory check to see which team memories Hindsight returns.</div>`;
    $("#memory-count").textContent = "";
  }
}

function renderTimeline() {
  const order = ["history", "review", "rejected", "retained", "future", "complete"];
  const idx = order.indexOf(state.stage);
  document.querySelectorAll("#timeline li").forEach((li) => {
    const i = order.indexOf(li.dataset.step);
    li.className = i < idx ? "done" : i === idx ? "active" : "";
  });
  const canAgain = state.mode === "dataset" && !!state.reviews.latest && state.run !== "latest";
  const btn = $("#review-again");
  btn.disabled = !canAgain;
  btn.textContent = state.mode === "dataset" && state.reviews.latest && state.run === "latest" ? "Showing latest run with learned memory"
    : state.mode === "dataset" && !state.reviews.latest ? "No learned-memory run saved for this PR" : "Review again with learned memory";
}

function renderAll() {
  renderHeader(); renderWorkspace(); renderTabs(); renderCode(); renderRunTabs(); renderFindings(); renderMemory(); renderTimeline();
  const latest = state.mode === "dataset" && state.run === "latest";
  $("#run-notice").hidden = !(latest && state.noticeHtml);
  $("#run-notice").innerHTML = latest ? state.noticeHtml : "";
  $("#recall-result").innerHTML = latest ? state.recallHtml : "";
}

/* ---------------- flows ---------------- */
async function loadDatasetPr(n) {
  state.prNumber = n;
  state.selected = null; state.source = null; state.analysis = null; state.recall = null; state.pipeline = null;
  state.noticeHtml = ""; state.recallHtml = "";
  const [pr, reviews] = await Promise.all([api(`/pr/${n}`), api(`/pr/${n}/reviews`).catch(() => ({}))]);
  state.pr = pr; state.reviews = reviews;
  state.run = reviews.baseline ? "baseline" : Object.keys(reviews)[0] || "baseline";
  state.stage = hasReview() ? "review" : "history";
  const firstMem = review()?.findings.find((f) => f.basis === "hindsight_memory");
  state.file = firstMem?.file || pr.files[0]?.path;
  renderAll();
  if (firstMem) select(firstMem.id);
}

function setStep(key, st, detail) {
  const s = state.pipeline.find((x) => x.key === key);
  s.state = st; if (detail !== undefined) s.detail = detail;
  renderWorkspace();
}

async function runReview() {
  const custom = state.mode === "custom";
  const body = custom ? { file_name: $("#custom-file").value, code: state.customCode } : { pr: state.prNumber };
  if (custom && !state.customCode.trim()) return;
  state.pipeline = [
    { key: "read", label: "Reading changed files", state: "running" },
    { key: "detect", label: "Detecting code patterns", state: "" },
    { key: "query", label: "Querying Hindsight", state: "" },
    { key: "match", label: "Matching team conventions", state: "" },
    { key: "ready", label: custom ? "Fast memory check ready" : "Review ready", state: "" },
  ];
  $("#run-btn").disabled = true;
  renderWorkspace();
  try {
    const a = await post("/analyze", body);
    state.analysis = a;
    if (custom) { state.editing = false; state.file = a.files[0]?.path; }
    const changed = a.files.reduce((n, f) => n + f.added, 0);
    setStep("read", "done", `${a.files.length} file${a.files.length === 1 ? "" : "s"} · ${changed} changed lines`);
    setStep("detect", "done", `${Object.keys(a.signals).length} patterns → ${a.probes.length} queries`);
    setStep("query", "running");
    renderHeader(); renderTabs(); renderCode(); renderFindings();
    const r = await post("/memory-recall", body);
    state.recall = r;
    setStep("query", "done", `retrieved ${r.memories.length} memories · ${r.seconds}s`);
    if (!custom && hasReview()) {
      const rv = review();
      const mem = rv.findings.filter((f) => f.basis === "hindsight_memory").length;
      setStep("match", "done", `${mem} memory-backed findings · ${rv.suppressed.length} held back (saved run)`);
    } else {
      setStep("match", "skip", custom ? "memory matches only · no reflect" : "no saved full review · memory matches only");
    }
    setStep("ready", "done");
    state.stage = hasReview() ? "review" : "history";
  } catch (e) {
    const running = state.pipeline.find((s) => s.state === "running");
    if (running) setStep(running.key, "skip", `failed: ${e.message}`);
  } finally {
    $("#run-btn").disabled = false;
  }
  renderAll();
}

function select(id, scroll = true) {
  state.selected = state.selected === id ? null : id;
  const f = findingById(state.selected);
  if (f && f.file !== state.file) state.file = f.file;
  renderTabs(); renderCode(); renderFindings(); renderMemory();
  if (f && scroll) document.getElementById(`L${f.line}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function openReject(id) {
  const f = findingById(id);
  $("#modal-finding").innerHTML = `<b>${esc(f.id)} · ${esc(f.title)}</b><br>PR #${state.prNumber} · ${esc(f.file)}:${f.line}`;
  $("#reject-form").dataset.finding = id;
  $("#modal").hidden = false;
  setTimeout(() => $("#reason").focus(), 30);
}

async function submitFeedback(id, decision, reason, developer) {
  const run = state.run, pr = state.prNumber, key = fbKey(id, run);
  state.feedback[key] = { decision, developer, status: "pending" };
  state.selected = id;
  state.stage = "retained";
  renderFindings(); renderMemory(); renderTimeline();
  document.querySelector(`.finding[data-id="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const started = Date.now();
  const tick = setInterval(() => {
    const el = document.querySelector(`.finding[data-id="${id}"] .elapsed`);
    if (el) el.textContent = Math.round((Date.now() - started) / 1000);
  }, 250);
  try {
    const res = await post(`/pr/${pr}/feedback`, { run, finding_id: id, decision, reason, developer });
    state.feedback[key] = { decision, developer, status: "done", res };
    state.stage = "future";
  } catch (e) {
    state.feedback[key] = { decision, developer, status: "error", error: e.message };
    state.stage = "rejected";
  } finally {
    clearInterval(tick);
  }
  if (state.prNumber === pr) renderFindings();
  renderTimeline();
  document.querySelector(`.finding[data-id="${id}"] .result-card`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function reviewAgain() {
  if (!state.reviews.latest) return;
  const base = state.reviews.baseline, latest = state.reviews.latest;
  const rejected = Object.entries(state.feedback)
    .filter(([k, v]) => k.startsWith(`${state.prNumber}:baseline:`) && v.decision === "REJECTED" && v.status === "done")
    .map(([k]) => base.findings.find((f) => f.id === k.split(":")[2]));
  const notes = rejected.map((bf) => {
    const still = latest.findings.find((f) => f.file === bf.file && f.line === bf.line);
    return still
      ? `<b>Latest ReviewMind run still raises ${esc(bf.id)}</b> (now ${esc(still.id)} · ${esc(still.title)}, ${esc(shortFile(still.file))}:${still.line}).`
      : `<b>Latest ReviewMind run no longer raises ${esc(bf.id)}</b> (${esc(bf.title)}).`;
  });
  state.noticeHtml = `Latest saved ReviewMind run against <code>${esc(latest.bank)}</code> (a live review takes several minutes).${notes.length ? "<br>" + notes.join("<br>") : ""}`;
  const target = rejected[0] || base.findings[0];
  const q = `What did developers say about ReviewMind's finding '${target.title}' in ${target.file}?`;
  state.recallHtml = `<div class="recall-card"><div class="q"><span class="spinner"></span> Recalling developer feedback from Hindsight…</div></div>`;
  state.run = "latest"; state.selected = null; state.stage = "future";
  renderAll();
  $("#findings-scroll").scrollTop = 0;
  try {
    const r = await api(`/recall?q=${encodeURIComponent(q)}`);
    const fbHits = r.memories.filter((m) => isFeedbackDoc(m.document_id)).length;
    state.recallHtml = `
      <div class="recall-card">
        <div class="section-title" style="margin-top:0">${fbHits ? "Developer feedback is now recallable from Hindsight" : "Live recall from Hindsight"} · ${r.seconds}s</div>
        <div class="q">“${esc(q)}”</div>
        ${r.memories.slice(0, 4).map((m) => {
          const fb = isFeedbackDoc(m.document_id);
          return `<div class="hit ${fb ? "fb" : ""}"><span class="t">${esc(m.type)} · ${esc(m.document_id || "observation")}${fb ? " · developer feedback" : ""}</span><br>${esc(m.text.split(" | ")[0])}</div>`;
        }).join("")}
        <div class="hint">${fbHits ? `${fbHits} of the top results come from the developer feedback; it is now part of the team memory a future review can retrieve.` : "No feedback memory in the top results."}</div>
      </div>`;
    if (fbHits) state.stage = "complete";
  } catch (e) {
    state.recallHtml = `<div class="recall-card"><div class="q">Recall failed: ${esc(e.message)}</div></div>`;
  }
  if (state.run === "latest") $("#recall-result").innerHTML = state.recallHtml;
  renderTimeline();
}

function setMode(mode) {
  if (mode === state.mode) return;
  state.mode = mode;
  state.selected = null; state.source = null; state.pipeline = null;
  if (mode === "custom") {
    state.analysis = null; state.recall = null; state.editing = true; state.file = null; state.stage = "history";
    renderAll();
    $("#editor").focus();
  } else {
    loadDatasetPr(state.prNumber);
  }
}

function jumpTo(loc) {
  const i = loc.lastIndexOf(":");
  state.file = loc.slice(0, i);
  renderTabs(); renderCode();
  document.getElementById(`L${loc.slice(i + 1)}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function wire() {
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-source],[data-goto],[data-accept],[data-reject],[data-finding],[data-file],[data-jump],[data-mode],[data-run],.finding");
    if (!t) return;
    if (t.dataset.source) { e.stopPropagation(); state.source = state.source === t.dataset.source ? null : t.dataset.source; renderFindings(); renderMemory(); return; }
    if (t.dataset.reject) { e.stopPropagation(); openReject(t.dataset.reject); return; }
    if (t.dataset.accept) { e.stopPropagation(); submitFeedback(t.dataset.accept, "ACCEPTED", "", "erin"); return; }
    if (t.dataset.goto) { e.stopPropagation(); state.selected = null; select(t.dataset.goto); return; }
    if (t.dataset.jump) { jumpTo(t.dataset.jump); return; }
    if (t.dataset.finding) { select(t.dataset.finding); return; }
    if (t.dataset.file) { state.file = t.dataset.file; renderTabs(); renderCode(); return; }
    if (t.dataset.mode) { setMode(t.dataset.mode); return; }
    if (t.dataset.run) { state.run = t.dataset.run; state.selected = null; renderAll(); return; }
    if (t.classList.contains("finding") && !e.target.closest("details, .result-card")) select(t.dataset.id);
  });
  $("#pr-select").addEventListener("change", (e) => loadDatasetPr(+e.target.value));
  $("#run-btn").addEventListener("click", runReview);
  $("#review-again").addEventListener("click", reviewAgain);
  $("#editor").addEventListener("input", (e) => { state.customCode = e.target.value; });
  $("#custom-file").addEventListener("input", renderHeader);
  $("#edit-btn").addEventListener("click", () => { state.editing = true; state.pipeline = null; renderAll(); $("#editor").focus(); });
  const close = () => { $("#modal").hidden = true; };
  $("#modal-close").addEventListener("click", close);
  $("#modal-cancel").addEventListener("click", close);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  $("#reject-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const reason = $("#reason").value.trim();
    if (!reason) { $("#reason").focus(); return; }
    close();
    state.stage = "rejected";
    submitFeedback(e.target.dataset.finding, "REJECTED", reason, $("#developer").value);
  });
  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("rm-theme", root.dataset.theme); } catch {}
  });
}

async function init() {
  try { const t = localStorage.getItem("rm-theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  wire();
  api("/status").then((s) => { state.status = s; renderStatus(); }).catch(() => { state.status = { connected: false }; renderStatus(); });
  state.prs = await api("/prs");
  renderSelector();
  await loadDatasetPr(137);
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice">Failed to load: ${esc(e.message)}</div>`); });
