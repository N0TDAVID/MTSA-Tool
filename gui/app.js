/* MTSA CSP GUI. Vanilla JS. Talks to /api on 127.0.0.1 and nowhere else.
   The facade decides what is real; this file only renders. Anything the API
   marks {placeholder: true} is rendered with a PLACEHOLDER badge.

   Two areas. The plan builder is the guided walk-through a client uses: a few
   questions at a time, percent complete, what is missing, no regulation text.
   The consultant tools are the working screens with citations and evidence.

   Served under a strict Content Security Policy (no inline script or style),
   so every control is wired through delegated listeners on data-action (click)
   and data-change (change) attributes. Never use onclick. */

"use strict";

const state = { plan: null, data: null, screen: "builder", simulate: false, auditRun: null, facility: null,
  recorder: null, onBehalf: "", pendingInventory: null, pendingScan: null };
const lists = {};
let categories = [];

// ---------------------------------------------------------------- helpers

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const get = p => api("GET", p);
const post = (p, b) => api("POST", p, b || {});
const P = () => `/api/plan/${state.plan}`;

function ph(p) { if (!p) return ""; const r = typeof p === "string" ? p : p.reason; return `<span class="ph">PLACEHOLDER</span><span class="small">${esc(r)}</span>`; }
function phBox(p) { return p ? `<div class="ph-box">${ph(p)}</div>` : ""; }
function cite(c) {
  if (!c) return "";
  if (c.missing) return `<span class="cite missing" title="${esc(c.placeholder.reason)}">${esc(c.id)} (not in registry)</span>`;
  return `<span class="cite ${c.binding ? "binding" : "guidance"}" title="${esc(c.title + " [" + c.authority_type + (c.binding ? ", binding]" : ", guidance]"))}">${esc(c.cite)}</span>`;
}
function cites(list) { return (list || []).map(cite).join(""); }
function badge(status) {
  const map = { pass: "pass", fail: "fail", not_applicable: "na", unavailable: "unavailable", binding: "binding", best_practice: "best_practice" };
  return `<span class="badge ${map[status] || "info"}">${esc(String(status).replace("_", " "))}</span>`;
}
function table(columns, rows, render) {
  if (!rows.length) return `<p class="muted small">No rows.</p>`;
  const head = columns.map(c => `<th>${esc(c)}</th>`).join("");
  const body = rows.map(r => `<tr>${columns.map(c => `<td>${render ? render(c, r) : fmt(r[c])}</td>`).join("")}</tr>`).join("");
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function fmt(v) {
  if (v === null || v === undefined) return `<span class="muted">(absent)</span>`;
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return esc(v.join(", "));
  if (typeof v === "object") return `<code>${esc(JSON.stringify(v))}</code>`;
  return esc(v);
}
function pre(o) { return `<pre>${esc(JSON.stringify(o, null, 1))}</pre>`; }
function err(e) { return `<div class="error">${esc(e.message || e)}</div>`; }
function attr(el, name) { return el.dataset[name]; }
function val(id) { const el = document.getElementById(id); return el ? el.value : ""; }
function bar(percent) { return `<div class="bar"><div data-w="${Math.max(0, Math.min(100, percent || 0))}"></div></div>`; }
function notify(msg) { const n = document.getElementById("notice"); n.textContent = msg; n.hidden = false; clearTimeout(notify.t); notify.t = setTimeout(() => { n.hidden = true; }, 2800); }
function statusBadge(s) { return s === "complete" ? `<span class="badge pass">Complete</span>` : s === "in_progress" ? `<span class="badge info">In progress</span>` : `<span class="badge">Not started</span>`; }

const main = document.getElementById("main");
function setMain(html) { main.innerHTML = html; }
function ssi(on, extra) { document.getElementById("ssi").textContent = on ? (state.data.ssi_notice + (extra ? " " + extra : "")) : ""; }

// The progress bar width is the one style this page has to compute. A strict
// CSP blocks style attributes, so widths are applied here after each render.
function applyBars() {
  document.querySelectorAll(".bar > div[data-w]").forEach(d => { d.style.width = d.dataset.w + "%"; });
}

// ---------------------------------------------------------------- delegated events

const ACTIONS = {};
const CHANGES = {};
document.addEventListener("click", async e => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  e.preventDefault();
  const fn = ACTIONS[attr(el, "action")];
  if (!fn) return;
  try { await fn(el); } catch (ex) { notify("Error: " + ex.message); console.error(ex); }
});
document.addEventListener("change", async e => {
  const el = e.target.closest("[data-change]");
  if (!el) return;
  const fn = CHANGES[attr(el, "change")];
  if (!fn) return;
  try { await fn(el); } catch (ex) { notify("Error: " + ex.message); console.error(ex); }
});

// ---------------------------------------------------------------- boot and routing

const TOOL_SCREENS = ["facilities", "interview", "criticality", "registers", "crosswalk", "gap", "licensing", "surveillance", "audit", "records"];

async function boot() {
  state.data = await get("/api/state");
  const sel = document.getElementById("plan");
  sel.innerHTML = state.data.plans.map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
  state.plan = state.plan || state.data.plans[0].id;
  sel.value = state.plan;
  sel.addEventListener("change", () => { state.plan = sel.value; route(); });
  route();
}
window.addEventListener("hashchange", route);

async function route() {
  // Whatever is still unsaved on the screen being left is written first, so
  // Continue, Back, the navigation bar, and the browser back button all keep
  // typed answers.
  try { await saveDirty(); } catch (e) { notify("Could not save: " + e.message); }
  Object.keys(lists).forEach(k => delete lists[k]);
  const hash = (location.hash || "#builder").slice(1);
  const [screen, arg] = hash.split("/");
  state.screen = screen;
  state.stepId = screen === "step" ? arg : null;
  const area = TOOL_SCREENS.includes(screen) ? "tools" : "builder";
  document.querySelectorAll("#primary-nav a").forEach(a => a.classList.toggle("active", a.dataset.area === area));
  const tools = document.getElementById("tools-nav");
  tools.hidden = area !== "tools";
  tools.querySelectorAll("a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + screen));
  setMain(`<p class="muted">Loading.</p>`);
  try {
    state.data = await get("/api/state");
    const v = state.data.versions;
    document.getElementById("footer").innerHTML = `as_of ${esc(state.data.as_of)} | ruleset ${esc(v.ruleset_pin)} | question module ${esc(v.question_module)} | ` +
      `entitlements: ${esc(state.data.capabilities.join(", ") || "(none)")} | ${ph(v.ruleset_signature)}`;
    await (SCREENS[screen] || SCREENS.builder)(arg);
    applyBars();
  } catch (e) { setMain(err(e)); }
}
const SCREENS = {};

// ---------------------------------------------------------------- dirty tracking
// Every editable control carries the value it was rendered with. collectDirty
// compares the current value against it; saveDirty writes the differences in
// one request. Change handlers also save immediately and refresh the marker.

function collectDirty() {
  const patches = [];
  document.querySelectorAll("[data-qid][data-path]").forEach(wrap => {
    const qid = attr(wrap, "qid");
    if (!document.getElementById("in-" + qid)) return;
    const value = readScalar("in-" + qid, attr(wrap, "type"));
    if (JSON.stringify(value) !== wrap.dataset.initial) patches.push({ path: attr(wrap, "path"), value, _wrap: wrap });
  });
  Object.keys(lists).forEach(qid => {
    if (!document.getElementById("in-" + qid)) return;
    const rows = collectList(qid);
    if (JSON.stringify(rows) !== lists[qid].saved) patches.push({ path: lists[qid].node.path, value: rows, _list: qid, _rows: rows });
  });
  document.querySelectorAll("input[data-change=deviceField]").forEach(el => {
    const value = el.value || null;
    if (JSON.stringify(value) !== el.dataset.initial) patches.push({ device: attr(el, "nickname"), field: attr(el, "field"), value, _el: el });
  });
  return patches;
}
async function saveDirty() {
  if (!state.plan) return 0;
  const patches = collectDirty();
  if (!patches.length) return 0;
  await post(`${P()}/answers/patch`, { patches: patches.map(p => ({ path: p.path, device: p.device, field: p.field, value: p.value })) });
  patches.forEach(p => {
    if (p._wrap) { p._wrap.dataset.initial = JSON.stringify(p.value); const s = p._wrap.querySelector(".saved"); if (s) s.textContent = "saved"; }
    if (p._list) { lists[p._list].rows = p._rows; lists[p._list].saved = JSON.stringify(p._rows); }
    if (p._el) p._el.dataset.initial = JSON.stringify(p.value);
  });
  notify(`Saved ${patches.length} change${patches.length === 1 ? "" : "s"}.`);
  return patches.length;
}

// ================================================================ PLAN BUILDER

SCREENS.builder = async function () {
  ssi(true, "Answers on this screen are plan content.");
  const w = await get(`${P()}/wizard`);
  const prog = await get(`${P()}/progress`);
  const next = w.steps.find(s => s.status !== "complete" && s.kind !== "review") || w.steps[w.steps.length - 1];
  const required = prog.sections.reduce((n, s) => n + s.missing.filter(m => m.kind === "required").length, 0);
  const recommended = prog.sections.reduce((n, s) => n + s.missing.filter(m => m.kind === "recommended").length, 0);
  setMain(`
  <h1>${esc(w.plan.title)}</h1>
  <p class="lead">A few questions at a time. Your answers save as you go, and every step shows what is still missing.</p>
  <div class="grid2">
    <div class="panel">
      <div class="row between"><h2 class="no-top">Plan progress</h2>${statusBadge(w.overall.percent === 100 ? "complete" : w.overall.percent ? "in_progress" : "not_started")}</div>
      <p class="pct"><b>${w.overall.done} of ${w.overall.total}</b> items complete</p>
      ${bar(w.overall.percent)}
      <p class="pct"><b>${w.overall.percent}%</b> complete</p>
      <div class="inline"><button class="primary" data-action="go" data-hash="step/${esc(next.id)}">${w.overall.done ? "Continue" : "Start"}: ${esc(next.title)}</button></div>
    </div>
    <div class="panel">
      <h2 class="no-top">What is missing</h2>
      <p class="pct"><b>${required}</b> required item${required === 1 ? "" : "s"} and <b>${recommended}</b> recommended item${recommended === 1 ? "" : "s"} across the plan.</p>
      <p class="pct">Ready to save a version: <b>${prog.export.allowed ? "yes" : "not yet"}</b>${prog.export.allowed ? "" : ` (${prog.export.blocking} required item${prog.export.blocking === 1 ? "" : "s"} outstanding)`}</p>
      <div class="inline"><button data-action="go" data-hash="step/review">See the full list</button></div>
    </div>
  </div>
  <div class="panel step-list">
    <h2 class="no-top">Steps in this plan</h2>
    ${w.steps.map(s => `<div class="step-row">
      <div class="num">${s.index}</div>
      <div class="body"><div class="title">${esc(s.title)}</div>
        <div class="sub">${s.kind === "review" ? "Percent complete and what is missing" : s.total ? `${s.done} of ${s.total} complete` : esc(s.note || s.blurb)}${s.note && s.total ? ` &middot; ${esc(s.note)}` : ""}</div></div>
      ${bar(s.percent)}
      <span class="pct">${s.percent}%</span>
      <button data-action="go" data-hash="step/${esc(s.id)}">${s.kind === "review" ? "Review" : s.status === "complete" ? "Revisit" : s.status === "in_progress" ? "Continue" : "Start"}</button>
    </div>`).join("")}
  </div>`);
};
ACTIONS.go = el => { location.hash = "#" + attr(el, "hash"); };

function stepHead(st) {
  return `<p class="pct" id="step-line">Step ${st.index} of ${st.count} &middot; plan <b>${st.overall.percent}%</b> complete</p>
  ${bar(st.overall.percent)}
  <h1>${esc(st.title)}</h1>
  <p class="lead">${esc(st.blurb)}</p>`;
}
function stepMissing(st) {
  const chip = k => `<span class="chip ${k}">${k}</span>`;
  const missing = st.missing || [];
  const list = missing.length ? `<ul class="missing small">${missing.map(m => `<li>${chip(m.kind)} ${esc(m.text)}</li>`).join("")}</ul>` : "";
  // "Nothing missing" is only ever said about a finished step. An unstarted
  // step says so, whether or not there is anything to list yet.
  if (st.status === "complete") return `<p class="small ok">Complete. Nothing missing on this step.</p>`;
  if (st.status === "not_started") {
    const why = st.total ? `${missing.length} item${missing.length === 1 ? "" : "s"} needed on this step` : esc(st.note || "nothing to answer here yet");
    return `<h3 class="no-top"><span class="badge">Not started</span> <span class="small muted">${why}</span></h3>${list}`;
  }
  return `<h3 class="no-top"><span class="badge info">In progress</span> Still needed on this step (${missing.length})</h3>${list}`;
}
function stepCount(st) { return st.total ? `${st.done} of ${st.total} on this step` : ""; }

SCREENS.step = async function (stepId) {
  ssi(true);
  const st = await get(`${P()}/step/${stepId}`);
  const nav = `<div class="stepnav">
    <div>${st.previous ? `<button class="secondary" data-action="go" data-hash="step/${esc(st.previous)}">Back</button>` : `<button class="secondary" data-action="go" data-hash="builder">Overview</button>`}</div>
    <div class="inline"><span class="pct" id="step-count">${stepCount(st)}</span>
      ${st.next ? `<button class="primary" data-action="go" data-hash="step/${esc(st.next)}">Save and continue</button>` : `<button class="primary" data-action="go" data-hash="builder">Save and return to overview</button>`}</div>
  </div>`;
  let body;
  if (st.kind === "questions") body = st.questions.map(guidedQuestion).join("");
  else if (st.kind === "inventory") body = inventoryStep(st.inventory);
  else if (st.kind === "sort") body = sortStep(st.sort);
  else if (st.kind === "followups") body = followupsStep(st.followups);
  else body = reviewStep(st.progress, st.versions);
  const missingPanel = st.kind === "review" ? "" : `<div class="panel" id="step-missing">${stepMissing(st)}</div>`;
  setMain(`<div id="step-head">${stepHead(st)}</div>` + missingPanel + `<div class="panel" id="step-body">${body}</div>` + nav);
  if (st.kind === "inventory") renderPending();
};

// Re-fetch the current step and update the parts that depend on answers, in
// place: the header percent, the still-needed list, the count, and which
// conditional questions are applicable. Nothing the user is typing in is
// re-rendered.
async function refreshStep() {
  if (state.screen !== "step" || !state.stepId) return;
  const st = await get(`${P()}/step/${state.stepId}`);
  const head = document.getElementById("step-head");
  if (head) { head.innerHTML = stepHead(st); applyBars(); }
  const miss = document.getElementById("step-missing");
  if (miss) miss.innerHTML = stepMissing(st);
  const count = document.getElementById("step-count");
  if (count) count.textContent = stepCount(st);
  if (st.kind !== "questions") return;
  const body = document.getElementById("step-body");
  let prev = null;
  st.questions.forEach(q => {
    let block = document.getElementById("gq-" + q.id);
    if (q.applicable && !block) {
      const html = guidedQuestion(q);
      if (prev) prev.insertAdjacentHTML("afterend", html); else body.insertAdjacentHTML("afterbegin", html);
      block = document.getElementById("gq-" + q.id);
    } else if (!q.applicable && block) {
      block.remove();
      delete lists[q.id];
      block = null;
    }
    if (block) prev = block;
  });
}

// ---- guided questions: prompt, help, input. No citations, no paths.

function guidedQuestion(n) {
  if (!n.applicable) return "";
  const help = (n.optional || n.help) ? `<div class="help">${n.optional ? "Optional. " : ""}${esc(n.help || "")}</div>` : "";
  if (n.type === "entity_list") {
    const rows = JSON.parse(JSON.stringify(Array.isArray(n.value) ? n.value : []));
    lists[n.id] = { node: n, rows, saved: JSON.stringify(rows) };
    return `<div class="gq" id="gq-${esc(n.id)}"><div class="prompt">${esc(n.prompt)}</div>${help}<div id="in-${esc(n.id)}">${renderList(n.id)}</div></div>`;
  }
  return `<div class="gq" id="gq-${esc(n.id)}"><div class="prompt">${esc(n.prompt)}</div>${help}
    <div class="field inline" data-qid="${esc(n.id)}" data-path="${esc(n.path)}" data-type="${esc(n.type)}" data-initial="${esc(JSON.stringify(n.value === undefined ? null : n.value))}">
      ${scalarInput("in-" + n.id, n.type, n.value, n.options, n.format, "saveScalar")}<span class="saved">${n.value != null && n.value !== "" ? "answered" : ""}</span></div></div>`;
}

function scalarInput(id, type, value, options, format, change) {
  const v = value == null ? "" : value;
  const ch = change ? ` data-change="${change}"` : "";
  if (type === "boolean") return `<select id="${id}"${ch}><option value="">(not answered)</option><option value="true" ${v === true ? "selected" : ""}>Yes</option><option value="false" ${v === false ? "selected" : ""}>No</option></select>`;
  if (type === "enum" || type === "tri_state") return `<select id="${id}"${ch}><option value="">(not answered)</option>${options.map(o => `<option value="${esc(o)}" ${v === o ? "selected" : ""}>${esc(o.replace(/_/g, " "))}</option>`).join("")}</select>`;
  if (type === "enum_multi") return `<span id="${id}" class="inline">${options.map(o => `<label><input type="checkbox" value="${esc(o)}"${ch} ${(Array.isArray(v) && v.includes(o)) ? "checked" : ""}> ${esc(o.replace(/_/g, " "))}</label>`).join("")}</span>`;
  if (type === "date") return `<input type="date" id="${id}" value="${esc(v)}"${ch}>`;
  if (type === "number") return `<input type="number" id="${id}" value="${esc(v)}"${ch}>`;
  if (type === "narrative") return `<textarea id="${id}"${ch}>${esc(v)}</textarea>`;
  if (type === "identifier_list") return `<input type="text" id="${id}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}" placeholder="comma separated"${ch}>`;
  return `<input type="text" id="${id}" value="${esc(v)}" ${format ? `pattern="${esc(format)}"` : ""}${ch}>`;
}
function readScalar(id, type) {
  const el = document.getElementById(id);
  if (type === "enum_multi") return Array.from(el.querySelectorAll("input:checked")).map(i => i.value);
  const v = el.value;
  if (v === "") return null;
  if (type === "boolean") return v === "true";
  if (type === "number") return Number(v);
  if (type === "identifier_list") return v.split(",").map(s => s.trim()).filter(Boolean);
  return v;
}
CHANGES.saveScalar = async el => {
  const wrap = el.closest("[data-qid]");
  const value = readScalar("in-" + attr(wrap, "qid"), attr(wrap, "type"));
  await post(`${P()}/answer`, { path: attr(wrap, "path"), value });
  wrap.dataset.initial = JSON.stringify(value);
  wrap.querySelector(".saved").textContent = "saved";
  await refreshStep();
};

function renderList(qid) {
  const { node, rows } = lists[qid];
  const head = node.fields.map(f => `<th>${esc(f.prompt)}</th>`).join("") + "<th></th>";
  const body = rows.map((r, i) => `<tr>${node.fields.map(f => `<td>${scalarInput(`li-${qid}-${i}-${f.key}`, f.type, r[f.key], f.options, f.format)}</td>`).join("")}
    <td><button class="secondary" data-action="listRemove" data-qid="${esc(qid)}" data-index="${i}">Remove</button></td></tr>`).join("");
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="inline"><button class="secondary" data-action="listAdd" data-qid="${esc(qid)}">Add row</button>
    <button class="primary" data-action="listSave" data-qid="${esc(qid)}">Save list (${rows.length})</button><span class="saved"></span></div>`;
}
function collectList(qid) {
  const { node, rows } = lists[qid];
  return rows.map((r, i) => { const o = {}; node.fields.forEach(f => { o[f.key] = readScalar(`li-${qid}-${i}-${f.key}`, f.type); }); return o; });
}
ACTIONS.listAdd = el => { const qid = attr(el, "qid"); lists[qid].rows = collectList(qid); lists[qid].rows.push({}); document.getElementById("in-" + qid).innerHTML = renderList(qid); };
ACTIONS.listRemove = el => { const qid = attr(el, "qid"); const rows = collectList(qid); rows.splice(Number(attr(el, "index")), 1); lists[qid].rows = rows; document.getElementById("in-" + qid).innerHTML = renderList(qid); };
ACTIONS.listSave = async el => {
  const qid = attr(el, "qid");
  const rows = collectList(qid);
  await post(`${P()}/answer`, { path: lists[qid].node.path, value: rows });
  lists[qid].rows = rows;
  lists[qid].saved = JSON.stringify(rows);
  document.getElementById("in-" + qid).innerHTML = renderList(qid);
  document.querySelector(`#in-${CSS.escape(qid)} .saved`).textContent = `saved ${rows.length} rows`;
  notify(`Saved ${rows.length} rows.`);
  await refreshStep();
};

// ---- inventory step: by hand, spreadsheet upload, scanner import

function inventoryStep(inv) {
  const f = Object.fromEntries(inv.fields.map(x => [x.key, x]));
  const sortBadge = s => s === "in_scope" ? `<span class="badge pass">in scope</span>` : s === "out_of_scope" ? `<span class="badge na">out of scope</span>` : `<span class="badge warn">not sorted</span>`;
  return `
  <div class="grid3">
    <div class="card"><h3>Add an asset by hand</h3>
      <div class="inline"><input type="text" id="dev-nickname" placeholder="Asset name (required)"><input type="text" id="dev-ip" placeholder="IP address"></div>
      <div class="inline">${scalarInput("dev-component_type", "enum", null, f.component_type.options)}${scalarInput("dev-system_function", "enum", null, f.system_function.options)}</div>
      <div class="inline"><label>OT system ${scalarInput("dev-is_ot", "boolean", null)}</label><label>Reachable from the internet ${scalarInput("dev-public_facing", "boolean", null)}</label></div>
      <div class="inline"><button class="primary" data-action="deviceAdd">Add asset</button></div>
    </div>
    <div class="card"><h3>Upload a spreadsheet</h3>
      <p class="small muted">${esc(inv.formats.inventory)}. <a href="${esc(inv.template_url)}" download>Download the template</a>.</p>
      <div class="dropzone"><input type="file" id="inv-file" accept=".csv,text/csv" data-change="readInventoryFile"></div>
      <div id="inv-preview"></div>
    </div>
    <div class="card"><h3>Import scanner output</h3>
      <p class="small muted">${esc(inv.formats.scan)}. Hosts are matched to assets by IP or name; you confirm before anything is applied. <a href="${esc(inv.example_scan_url)}" download>Example file</a>.</p>
      <div class="dropzone"><input type="file" id="scan-file" accept=".nessus,.csv,.xml" data-change="readScanFile"></div>
      <div id="scan-preview"></div>
    </div>
  </div>
  <h3>Assets (${inv.devices.length})</h3>
  ${table(["asset", "ip", "type", "function", "OT", "internet", "vulnerabilities observed", "sort", ""], inv.devices, (c, d) => {
    if (c === "asset") return `<b>${esc(d.nickname)}</b>${d.operating_system ? `<br><span class="small muted">${esc(d.operating_system)}</span>` : ""}`;
    if (c === "type") return fmt(d.component_type);
    if (c === "function") return fmt(d.system_function);
    if (c === "OT") return fmt(d.is_ot);
    if (c === "internet") return fmt(d.public_facing);
    if (c === "vulnerabilities observed") return `${d.observed_cve_count}${d.scan_sources ? `<br><span class="small muted">${esc(d.scan_sources.join(", "))}</span>` : ""}`;
    if (c === "sort") return sortBadge(d.sort);
    if (c === "") return `<button class="secondary" data-action="deviceRemove" data-nickname="${esc(d.nickname)}">Remove</button>`;
    return fmt(d[c]);
  })}
  ${inv.imports.length ? `<p class="small muted">Imports applied: ${inv.imports.map(i => `${esc(i.source)} (${i.devices.length} assets)`).join("; ")}</p>` : ""}`;
}
ACTIONS.deviceAdd = async () => {
  const device = { nickname: val("dev-nickname"), ip: val("dev-ip"), component_type: val("dev-component_type") || null,
    system_function: val("dev-system_function") || null, is_ot: readScalar("dev-is_ot", "boolean"), public_facing: readScalar("dev-public_facing", "boolean") };
  await post(`${P()}/device`, { device });
  notify(`Added ${device.nickname}.`); route();
};
ACTIONS.deviceRemove = async el => { await post(`${P()}/device/remove`, { nickname: attr(el, "nickname") }); notify("Removed."); route(); };

function readFile(input) {
  return new Promise((resolve, reject) => {
    const file = input.files && input.files[0];
    if (!file) return reject(new Error("choose a file first"));
    const r = new FileReader();
    r.onload = () => resolve({ name: file.name, text: r.result });
    r.onerror = () => reject(new Error("could not read the file"));
    r.readAsText(file);
  });
}
CHANGES.readInventoryFile = async el => {
  const { name, text } = await readFile(el);
  state.pendingInventory = await post(`${P()}/import/inventory/preview`, { text });
  state.pendingInventory.filename = name;
  renderPending();
};
CHANGES.readScanFile = async el => {
  const { name, text } = await readFile(el);
  state.pendingScan = await post(`${P()}/import/scan/preview`, { text, filename: name });
  renderPending();
};
function renderPending() {
  const ip = document.getElementById("inv-preview");
  const sp = document.getElementById("scan-preview");
  if (!ip || !sp) return;
  const pi = state.pendingInventory;
  ip.innerHTML = !pi ? "" : `<p class="small">${esc(pi.filename)}: ${pi.rows} assets read. ${pi.problems.length ? `<span class="warn-text">${pi.problems.length} note${pi.problems.length === 1 ? "" : "s"}</span>` : ""}</p>
    ${pi.problems.length ? `<ul class="small">${pi.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
    ${table(["action", "nickname", "ip", "component_type", "system_function", "is_ot", "public_facing"], pi.devices, (c, d) => c === "action" ? `<span class="badge ${d._action === "add" ? "info" : "warn"}">${d._action}</span>` : fmt(d[c]))}
    <div class="inline"><button class="primary" data-action="applyInventory">Apply ${pi.rows} rows</button><button class="secondary" data-action="discardInventory">Discard</button></div>`;
  const ps = state.pendingScan;
  const devOpts = sel => (ps ? ps.devices : []).map(d => `<option ${d === sel ? "selected" : ""}>${esc(d)}</option>`).join("");
  sp.innerHTML = !ps ? "" : `<p class="small">${esc(ps.filename)} (${esc(ps.format)}): ${ps.hosts} host${ps.hosts === 1 ? "" : "s"}, ${ps.matched.length} matched, ${ps.unmatched.length} unmatched.${ps.kev_licensed ? "" : " Known-exploited counts unavailable: kev capability not licensed."}</p>
    ${table(["apply", "host", "matched asset", "by", "CVEs", "known exploited"], ps.matched.concat(ps.unmatched), (c, h) => {
      const i = ps.matched.indexOf(h);
      const key = i >= 0 ? "m" + i : "u" + ps.unmatched.indexOf(h);
      if (c === "apply") return `<input type="checkbox" class="scan-row" data-key="${key}" ${i >= 0 ? "checked" : ""}>`;
      if (c === "host") return `${esc(h.host)}${h.names.length ? `<br><span class="small muted">${esc(h.names.join(", "))}</span>` : ""}`;
      if (c === "matched asset") return `<select class="scan-dev" data-key="${key}"><option value="">(leave unmatched)</option>${devOpts(h.device)}</select>`;
      if (c === "by") return i >= 0 ? esc(h.match_by) : `<span class="small muted">${esc(h.reason)}</span>`;
      if (c === "CVEs") return String(h.cve_count);
      if (c === "known exploited") return h.kev_count == null ? `<span class="muted">n/a</span>` : String(h.kev_count);
      return fmt(h[c]);
    })}
    <div class="inline"><button class="primary" data-action="applyScan">Apply to matched assets</button><button class="secondary" data-action="discardScan">Discard</button></div>`;
}
ACTIONS.applyInventory = async () => {
  const r = await post(`${P()}/import/inventory/apply`, { devices: state.pendingInventory.devices });
  state.pendingInventory = null; notify(`Inventory applied: ${r.added.length} added, ${r.updated.length} updated.`); route();
};
ACTIONS.discardInventory = () => { state.pendingInventory = null; renderPending(); };
ACTIONS.applyScan = async () => {
  const ps = state.pendingScan;
  const all = ps.matched.concat(ps.unmatched);
  const assignments = [];
  document.querySelectorAll("input.scan-row:checked").forEach(cb => {
    const key = cb.dataset.key;
    const h = key[0] === "m" ? ps.matched[Number(key.slice(1))] : ps.unmatched[Number(key.slice(1))];
    const dev = document.querySelector(`select.scan-dev[data-key="${key}"]`).value;
    if (h && dev) assignments.push({ host: h.host, device: dev, cves: h.cves });
  });
  if (!assignments.length) throw new Error("no host is assigned to an asset");
  const r = await post(`${P()}/import/scan/apply`, { assignments, source: ps.filename });
  state.pendingScan = null; notify(`Scan applied to ${r.applied.length} asset(s).`); route();
  void all;
};
ACTIONS.discardScan = () => { state.pendingScan = null; renderPending(); };

// ---- sort step: two questions per asset, attributed

function sortStep(c) {
  const s = c.session;
  state.recorder = s.participants.some(p => p.id === state.recorder) ? state.recorder : s.facilitator;
  state.onBehalf = s.participants.some(p => p.id === state.onBehalf) ? state.onBehalf : "";
  const pOpts = sel => s.participants.map(p => `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
  const q1 = c.questions.find(q => q.id === "is_critical"), q2 = c.questions.find(q => q.id === "tsi_possible");
  if (!c.assets.length) return `<p>No assets yet. <a href="#step/inventory">Add your inventory first.</a></p>`;
  const cell = (a, qid) => {
    const e = a.effective[qid];
    const cls = e.state === "agreed" ? (e.value === "yes" ? "pass" : "na") : e.state === "disputed" ? "fail" : "warn";
    const by = Object.entries(e.by).map(([k, v]) => `${k}: ${v}`).join("; ");
    return `<select data-change="critAnswer" data-asset="${esc(a.nickname)}" data-question="${qid}">
      <option value="">${e.state === "unanswered" ? "choose" : "change"}</option><option>yes</option><option>no</option><option>not_applicable</option></select>
      <div><span class="badge ${cls}">${esc(e.state === "agreed" ? e.value : e.state)}</span>${by ? ` <span class="small muted">${esc(by)}</span>` : ""}</div>`;
  };
  return `
  <div class="card"><div class="inline">
    <label>Who is answering <select id="recorder" data-change="setRecorder">${pOpts(state.recorder)}</select></label>
    <label>On behalf of <select id="on-behalf" data-change="setOnBehalf"><option value="">(self)</option>${pOpts(state.onBehalf)}</select></label>
    <span class="small muted">Answer these as a group: operations, security, and IT together.</span></div>
    <div class="inline"><input type="text" id="p-id" placeholder="id"><input type="text" id="p-name" placeholder="name"><select id="p-role">${c.roles.map(r => `<option>${esc(r)}</option>`).join("")}</select><button class="secondary" data-action="addParticipant">Add participant</button></div>
  </div>
  ${c.licensed ? "" : `<p><span class="badge unavailable">criticality capability not licensed</span></p>`}
  <h3>${esc(q1.prompt)}</h3>
  ${table(["asset", "OT", "answer"], c.assets, (col, x) => col === "asset" ? esc(x.nickname) : col === "OT" ? fmt(x.is_ot) : cell(x, "is_critical"))}
  <h3>${esc(q2.prompt)}</h3>
  ${table(["asset", "OT", "answer"], c.assets, (col, x) => col === "asset" ? esc(x.nickname) : col === "OT" ? fmt(x.is_ot) : cell(x, "tsi_possible"))}
  <div class="grid3" id="sort-summary">${sortSummary(c.stage1)}</div>
  <p class="small muted">An asset is in scope when both answers are yes and everyone who answered agrees. The follow-up questions, the registers, and what is missing all update as you sort. ${c.stage2 && c.stage2.rank ? ph(c.stage2.rank) : ""}</p>`;
}
function sortSummary(a) {
  return `<div class="card"><h3>In scope (${a.in_scope.length})</h3><p class="small">${esc(a.in_scope.map(r => r.asset_id).join(", ") || "none yet")}</p></div>
    <div class="card"><h3>Out of scope (${a.out_of_scope.length})</h3><p class="small">${esc(a.out_of_scope.map(r => r.asset_id).join(", ") || "none yet")}</p></div>
    <div class="card"><h3>Not yet sorted (${a.pending.length})</h3><p class="small">${esc(a.pending.map(r => r.asset_id).join(", ") || "none")}</p></div>`;
}
// After an answer is recorded, update only the badge beside each select and
// the summary cards. The page, the scroll position, and the other selects
// stay where they are.
function refreshSortView(c) {
  document.querySelectorAll("select[data-change=critAnswer]").forEach(sel => {
    const a = c.assets.find(x => x.nickname === attr(sel, "asset"));
    if (!a) return;
    const e = a.effective[attr(sel, "question")];
    const cls = e.state === "agreed" ? (e.value === "yes" ? "pass" : "na") : e.state === "disputed" ? "fail" : "warn";
    const by = Object.entries(e.by).map(([k, v]) => `${k}: ${v}`).join("; ");
    sel.value = "";
    sel.options[0].textContent = e.state === "unanswered" ? "choose" : "change";
    sel.nextElementSibling.innerHTML = `<span class="badge ${cls}">${esc(e.state === "agreed" ? e.value : e.state)}</span>${by ? ` <span class="small muted">${esc(by)}</span>` : ""}`;
  });
  const summary = document.getElementById("sort-summary");
  if (summary) summary.innerHTML = sortSummary(c.stage1);
}
CHANGES.setRecorder = el => { state.recorder = el.value; };
CHANGES.setOnBehalf = el => { state.onBehalf = el.value; };
ACTIONS.addParticipant = async () => {
  await post(`${P()}/criticality/participant`, { id: val("p-id"), name: val("p-name"), role: val("p-role") });
  notify("Participant added."); route();
};
CHANGES.critAnswer = async el => {
  if (!el.value) return;
  const c = await post(`${P()}/criticality/answer`, { asset_id: attr(el, "asset"), question_id: attr(el, "question"), value: el.value,
    participant_id: state.recorder, on_behalf_of: state.onBehalf || null });
  notify(`Recorded ${attr(el, "question").replace("_", " ")} = ${el.value} for ${attr(el, "asset")}.`);
  refreshSortView(c);
  await refreshStep();
};

// ---- follow-ups: per-asset questions the sorted inventory needs

function followupsStep(f) {
  if (!f.items.length) return `<p>Nothing to ask yet. ${f.placeholder ? ph(f.placeholder) : "Follow-ups appear once assets are sorted in scope and scan data is imported."}</p>`;
  return `<p class="pct"><b>${f.total - f.outstanding} of ${f.total}</b> answered</p>${phBox(f.placeholder)}` + f.items.map(i => {
    if (i.kind === "kev_disposition") {
      return `<div class="gq"><div class="prompt">${esc(i.prompt)} ${i.done ? `<span class="done-mark">answered</span>` : ""}</div>
        <div class="help">Fill in whichever applies. One is enough; the crosswalk records what was done.</div>
        ${i.fields.map(fl => `<div class="field inline"><label class="small" for="fu-${esc(i.device)}-${fl.key}">${esc(fl.prompt)}</label>
          <input type="text" id="fu-${esc(i.device)}-${fl.key}" value="${esc(i.values[fl.key] || "")}" data-initial="${esc(JSON.stringify(i.values[fl.key] || null))}" data-change="deviceField" data-nickname="${esc(i.device)}" data-field="${fl.key}"><span class="saved"></span></div>`).join("")}</div>`;
    }
    return `<div class="gq"><div class="prompt">${esc(i.prompt)} ${i.done ? `<span class="done-mark">answered</span>` : ""}</div>
      <div class="field inline"><input type="text" id="fu-${esc(i.device)}-${i.field}" value="${esc(i.value || "")}" data-initial="${esc(JSON.stringify(i.value || null))}" data-change="deviceField" data-nickname="${esc(i.device)}" data-field="${i.field}"><span class="saved"></span></div></div>`;
  }).join("");
}
CHANGES.deviceField = async el => {
  const value = el.value || null;
  await post(`${P()}/device/field`, { nickname: attr(el, "nickname"), field: attr(el, "field"), value });
  el.dataset.initial = JSON.stringify(value);
  el.parentElement.querySelector(".saved").textContent = "saved";
  await refreshStep();
};

// ---- review: percent per section, what is missing, save a version

function reviewStep(prog, versions) {
  const chip = k => `<span class="chip ${k}">${k}</span>`;
  return `
  <div class="grid2">
    <div class="card"><h3>Overall</h3><p class="pct"><b>${prog.overall.done} of ${prog.overall.total}</b> items complete</p>${bar(prog.overall.percent)}<p class="pct"><b>${prog.overall.percent}%</b></p></div>
    <div class="card"><h3>Saving a version</h3>
      <p class="small">${prog.export.allowed ? `<span class="ok">Ready.</span> Every required item is complete.` : `<span class="blocks">Not yet.</span> ${prog.export.blocking} required item${prog.export.blocking === 1 ? "" : "s"} outstanding${prog.export.unavailable ? `, ${prog.export.unavailable} cannot be checked on this install` : ""}.`}</p>
      <div class="inline"><button class="primary" data-action="freezePlan">Save a version of the answers as they are</button></div>
      ${versions.length ? `<p class="small muted">Saved versions: ${versions.map(v => `#${v.sequence} on ${esc(v.as_of)}`).join(", ")}.</p>` : ""}
    </div>
  </div>
  ${prog.sections.map(s => {
    const complete = s.total > 0 && s.done === s.total && !s.missing.length;
    const started = s.done > 0;
    const status = complete ? `<span class="badge pass">Complete</span>` : started ? `<span class="badge info">In progress</span>` : `<span class="badge">Not started</span>`;
    const list = s.missing.length ? `<ul class="missing small">${s.missing.map(m => `<li>${chip(m.kind)} ${esc(m.text)}</li>`).join("")}</ul>` : "";
    const note = complete ? `<p class="small ok">Nothing missing.</p>` : (s.total ? "" : `<p class="small muted">Not started${s.in_interview ? "" : ": no questions in this walk-through yet"}.</p>`);
    return `<div class="card">
    <div class="row between"><b>Section ${s.number}: ${esc(s.title)}</b><span class="row">${status}${s.percent == null ? "" : `<span class="pct"><b>${s.percent}%</b> (${s.done} of ${s.total})</span>`}</span></div>
    ${s.percent == null ? "" : bar(s.percent)}
    ${note}${list}
  </div>`;
  }).join("")}`;
}
ACTIONS.freezePlan = async () => { const v = await post(`${P()}/freeze`, { frozen_by: "gui" }); notify(`Saved version #${v.sequence}.`); route(); };

// ================================================================ CONSULTANT TOOLS

SCREENS.facilities = async function () {
  ssi(true, "Facility names and plan metadata are shown; no plan answers on this screen.");
  const d = state.data;
  const plan = await get(`${P()}`);
  const tenantName = id => (d.tenants.find(t => t.id === id) || {}).name || id;
  const facName = id => (d.facilities.find(f => f.id === id) || {}).name || id;
  setMain(`
  <h1>Facilities and plans</h1>
  <p class="lead">Tenant is the consulting org. Facility is a child entity whose owner or operator is the responsible party. Plan to facility is many-to-many.</p>
  <div class="grid2">
  <div class="panel"><h2>Tenants</h2>
    ${table(["id", "name", "kind", "facilities", "plans"], d.tenants.map(t => ({ id: t.id, name: t.name, kind: t.kind,
      facilities: d.facilities.filter(f => f.tenant_id === t.id).length, plans: d.plans.filter(p => p.tenant_id === t.id).length })))}
  </div>
  <div class="panel"><h2>Facilities</h2>
    ${table(["name", "asset_type", "tenant", "owner_operator", "plans", "transfer"], d.facilities, (c, f) => {
      if (c === "tenant") return esc(tenantName(f.tenant_id));
      if (c === "plans") return esc(f.plan_ids.join(", ")) + (f.transfer_history ? `<br><span class="small muted">transferred ${f.transfer_history.length}x</span>` : "");
      if (c === "transfer") {
        const others = d.tenants.filter(t => t.id !== f.tenant_id);
        return `<div class="inline"><select id="xfer-${esc(f.id)}">${others.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</select>
          <button class="secondary" data-action="transferFacility" data-fid="${esc(f.id)}">Transfer</button></div>`;
      }
      return fmt(f[c]);
    })}
    <p class="small muted">A plan that also covers a facility staying behind is a multi-facility plan and is held for a human to split, not moved.</p>
  </div>
  </div>
  <div class="panel"><h2>Plans</h2>
    ${table(["title", "asset_type", "facility_ids", "delivery_mode", "tenant", "versions", "seed"], d.plans, (c, p) => {
      if (c === "facility_ids") return esc(p.facility_ids.map(facName).join(" + "));
      if (c === "tenant") return esc(tenantName(p.tenant_id));
      if (c === "seed") return `<span class="small muted">${esc(p.seed)}</span>`;
      return fmt(p[c]);
    })}
  </div>
  <div class="panel"><h2>Shared CySO coverage (derived, never answered)</h2>
    <p class="small">${cites(d.shared_cyso[0] ? d.shared_cyso[0].citations : [])} Every facility a shared CySO covers must be listed in every one of their plans.</p>
    ${table(["cyso", "facilities", "must_list_in_every_plan"], d.shared_cyso)}
  </div>
  <div class="panel"><h2>Frozen versions of ${esc(plan.title)}</h2>
    <div class="inline"><button data-action="freezePlan">Freeze current answers</button><span class="small muted">Live answers are never rendered or audited; freeze first.</span></div>
    ${table(["sequence", "version_id", "as_of", "frozen_by", "pins", "attachments", "verify"], plan.versions, (c, v) => {
      if (c === "version_id") return `<code>${esc(v.version_id.slice(0, 16))}</code>`;
      if (c === "pins") return `<code class="small">${esc(Object.entries(v.pins).map(([k, x]) => k + "=" + x).join(" | "))}</code>`;
      if (c === "attachments") return `<span class="small">${esc(Object.keys(v.attachments).length)} pinned by sha256</span>`;
      if (c === "verify") return v.verify.length ? `<span class="badge fail">hash mismatch</span>` : `<span class="badge pass">hashes verify</span>`;
      return fmt(v[c]);
    })}
    ${plan.versions.length > 1 ? `<div class="inline"><button class="secondary" data-action="showDiff" data-from="${esc(plan.versions[plan.versions.length - 2].version_id)}" data-to="${esc(plan.versions[plan.versions.length - 1].version_id)}">Amendment diff: last two versions</button></div><div id="diff"></div>` : ""}
  </div>
  <div class="grid2">
  <div class="panel"><h2>14-section spine</h2>${table(["number", "title", "status", "requirements"], d.spine)}</div>
  <div class="panel"><h2>22 appendices</h2>${table(["designation", "title", "kind", "register", "controlled_attachment"], d.appendices)}</div>
  </div>`);
};
ACTIONS.transferFacility = async el => {
  const fid = attr(el, "fid");
  const r = await post(`/api/facility/${fid}/transfer`, { to_tenant_id: val("xfer-" + fid), reason: "transfer from GUI" });
  notify(`Moved ${r.facility_id} from ${r.from} to ${r.to}. Plans moved: ${r.plans_moved.join(", ") || "none"}. Held: ${r.plans_held_multi_facility.map(h => h.plan_id).join(", ") || "none"}.`);
  route();
};
ACTIONS.showDiff = async el => {
  const d = await get(`${P()}/diff?from=${attr(el, "from")}&to=${attr(el, "to")}`);
  document.getElementById("diff").innerHTML = `<p class="small">${cites(d.citations)} Answer changes between versions. Pin changes: ${d.pins.length}.</p>` + table(["path", "change", "from", "to"], d.answers);
};

SCREENS.interview = async function () {
  ssi(true);
  const q = await get(`${P()}/questions`);
  const bySection = {};
  q.questions.forEach(n => (bySection[n.section] = bySection[n.section] || []).push(n));
  const titles = Object.fromEntries(state.data.spine.map(s => [s.number, s.title]));
  let html = `<h1>Interview (consultant view)</h1>
  <p class="lead">Question module ${esc(q.question_module_version)}, every node with its citation, path, type, and the rules that read it. Clients use the plan builder instead.</p>`;
  for (const sec of Object.keys(bySection).sort((a, b) => a - b)) {
    html += `<div class="panel"><h2>Section ${sec}: ${esc(titles[sec])}</h2>` + bySection[sec].map(renderQuestion).join("") + `</div>`;
  }
  setMain(html);
};
function renderQuestion(n) {
  const meta = `<div class="meta">${cites(n.citations)} <code>${esc(n.path)}</code> type <b>${esc(n.type)}</b>` +
    (n.read_by_rules.length ? ` | read by rules: ${esc(n.read_by_rules.join(", "))}` : ` | <span class="muted">no rule reads this path</span>`) +
    (n.applies_to ? ` | applies_to <code>${esc(JSON.stringify(n.applies_to))}</code>` : "") + (n.help ? `<br>${esc(n.help)}` : "") + `</div>`;
  if (!n.applicable) return `<div class="q na"><div class="prompt">${esc(n.prompt)} <span class="badge na">not applicable</span></div>${meta}</div>`;
  if (n.type === "entity_list") {
    lists[n.id] = { node: n, rows: JSON.parse(JSON.stringify(Array.isArray(n.value) ? n.value : [])) };
    return `<div class="q"><div class="prompt">${esc(n.prompt)}</div>${meta}<div id="in-${esc(n.id)}">${renderList(n.id)}</div></div>`;
  }
  return `<div class="q"><div class="prompt">${esc(n.prompt)}</div>${meta}<div class="inline" data-qid="${esc(n.id)}" data-path="${esc(n.path)}" data-type="${esc(n.type)}">
    ${scalarInput("in-" + n.id, n.type, n.value, n.options, n.format, "saveScalar")}<span class="saved"></span></div></div>`;
}

SCREENS.criticality = async function () {
  ssi(true);
  const c = await get(`${P()}/criticality`);
  setMain(`<h1>Criticality workshop</h1>
  <p class="lead">${c.questions.map(q => `<b>${esc(q.id)}</b>: ${esc(q.prompt)} ${cites(q.citations)}`).join("<br>")}</p>
  <p class="small">Session ${esc(c.session.id)}, opened ${esc(c.session.opened_at)}, ${c.session.answers.length} attributed answers. ${table(["id", "name", "role"], c.session.participants)}</p>
  <div class="panel">${sortStep(c)}</div>
  <div class="stage"><h2>Stage 2: rank</h2>${phBox(c.stage2.rank)}<p class="small">Survivors awaiting ranking: ${esc(c.stage2.survivors.map(r => r.asset_id).join(", ") || "none")}.</p></div>`);
};

SCREENS.registers = async function () {
  ssi(true);
  const regs = await get(`${P()}/registers`);
  setMain(`<h1>Registers</h1>
  <p class="lead">Seven entity-store views, rendered from the answer set, never authored. Each is a controlled attachment pinned by hash.</p>
  ${regs.map(r => `<div class="panel"><h2>Appendix ${esc(r.designation)}: ${esc(r.title)}</h2>
    <p class="small">${cites(r.citations)} register <code>${esc(r.register)}</code> | attachment version <code>${esc(r.attachment_version)}</code> | ${r.count} rows${r.catalog_version ? ` | KEV catalog ${esc(r.catalog_version)}` : ""}</p>
    ${r.unavailable ? `<span class="badge unavailable">unavailable</span> <span class="small">${esc(r.unavailable)}</span>` : table(r.columns, r.rows)}
    ${r.fields_not_in_answer_model.length ? `<p class="small muted">Fields this appendix needs that the answer model does not capture yet: ${esc(r.fields_not_in_answer_model.join(", "))}</p>` : ""}
  </div>`).join("")}`);
};

SCREENS.crosswalk = async function () {
  ssi(true);
  const x = await get(`${P()}/crosswalk`);
  if (x.unavailable) { setMain(`<h1>Mitigation crosswalk</h1><p><span class="badge unavailable">unavailable</span> ${esc(x.reason)}</p>`); return; }
  setMain(`<h1>Mitigation crosswalk</h1>
  <p class="lead">${cites(x.citations)} Critical asset, known vulnerability, what was done. A composite view over appendices ${esc(x.source_appendices.join(", "))}.</p>
  <div class="panel">
    <p class="small">KEV catalog ${esc(x.catalog.version)} (${x.catalog.count} entries). Critical assets: ${x.critical_assets}, of which OT: ${x.ot_critical_assets}. Critical assets with no KEV row: ${esc(x.critical_assets_without_rows.join(", ") || "none")}.</p>
    ${x.ot_critical_assets ? `<p class="small"><b>Read an empty or thin join as absence of coverage, not compliance.</b> KEV is effectively an IT catalog; the real OT signal is ICS-CERT and vendor advisories.</p>` : ""}
    ${table(["asset", "domain", "cve", "vendor_project", "product", "date_added", "ransomware", "disposition", "compensating_control", "remediation_plan", "risk_acceptance"], x.rows, (c, r) => c === "disposition" ? `<span class="badge warn">${esc(r.disposition)}</span>` : fmt(r[c]))}
    <p class="small muted">${esc(x.disposition_note)}. The CISA dueDate is deliberately not shown.</p>
  </div>`);
};

SCREENS.gap = async function () {
  ssi(true);
  const run = await get(`${P()}/evaluate`);
  const s = run.summary;
  setMain(`<h1>Gap report</h1>
  <p class="lead">engine.run_ruleset over the live answer set, ruleset ${esc(run.ruleset_version)}, as_of ${esc(run.as_of)}, enrichment: ${esc(run.enrichment_applied.join(", ") || "none")}.</p>
  <div class="panel"><p>${s.export_allowed ? `<span class="ok">EXPORT ALLOWED</span>` : `<span class="blocks">EXPORT BLOCKED</span>`}
    | evaluated ${s.evaluated} | passed ${s.passed} | <b>blocking ${s.blocking}</b> | warnings ${s.warnings} | not applicable ${s.not_applicable} | unavailable ${s.unavailable}</p></div>
  ${table(["status", "severity", "rule", "section", "citations", "message", "evidence"], run.verdicts, (c, v) => {
    if (c === "status") return badge(v.status) + (v.status === "fail" && v.severity === "binding" ? `<br><span class="blocks small">blocks export</span>` : v.status === "fail" ? `<br><span class="small warn-text">warning</span>` : v.status === "unavailable" && v.severity === "binding" ? `<br><span class="blocks small">blocks export</span>` : "");
    if (c === "severity") return badge(v.severity);
    if (c === "rule") return `<code>${esc(v.rule_id)}</code>${v.requires_capability ? `<br><span class="small muted">requires ${esc(v.requires_capability)}</span>` : ""}`;
    if (c === "section") return `${v.section} <span class="small muted">${esc(v.section_title)}</span>`;
    if (c === "citations") return cites(v.citations);
    if (c === "message") return esc(v.message || "");
    if (c === "evidence") return v.evidence.length ? `<details><summary>${v.evidence.length} paths read</summary>${table(["path", "value", "ok"], v.evidence, (cc, e) => cc === "ok" ? (e.ok ? `<span class="badge pass">ok</span>` : `<span class="badge fail">not satisfied</span>`) : fmt(e[cc]))}</details>` : `<span class="muted small">none</span>`;
    return fmt(v[c]);
  })}`);
};

SCREENS.licensing = async function () {
  ssi(false);
  const L = await get("/api/license");
  const run = await get(`${P()}/evaluate`);
  const kv = run.verdicts.find(v => v.rule_id === "kev-without-delay");
  setMain(`<h1>Licensing</h1>
  <p class="lead">No SSI on this screen. The module boundary is the licensing boundary. An unlicensed rule is unavailable, never pass.</p>
  <div class="panel"><h2>Active entitlement set (draft toggle)</h2>${phBox(L.placeholders.override)}
    <div class="inline">${L.toggleable.map(c => `<label><input type="checkbox" class="ent" value="${esc(c)}" ${L.active_entitlements.includes(c) ? "checked" : ""}> ${esc(c)}</label>`).join("")}<button data-action="saveEntitlements">Apply</button></div>
    <p>Live effect on <code>kev-without-delay</code>: ${badge(kv.status)} ${badge(kv.severity)} | export allowed: <b>${run.summary.export_allowed}</b> | unavailable ${run.summary.unavailable}</p></div>
  <div class="grid2">
  <div class="panel"><h2>Capability modules</h2>
    ${table(["module", "module_version", "licensed", "rules gated"], Object.entries(L.modules).map(([k, m]) => ({ module: k, module_version: m.module_version, licensed: m.licensed, "rules gated": m.rules.join(", ") || "(none yet)" })))}
    <p class="small muted">Tier ceilings: ${esc(Object.entries(L.tier_ceiling).map(([t, c]) => `${t} = {${c.join(", ")}}`).join("; "))}</p></div>
  <div class="panel"><h2>Token</h2>${phBox(L.placeholders.signature)}
    <table class="kv"><tbody>
      ${Object.entries({ tenant: L.token.tenant, tier: L.token.tier, seat: L.token.seat, install_id: L.token.install_id, expires: L.token.expires, ruleset_entitlement: L.token.ruleset_entitlement, capabilities: L.token.capabilities.join(", ") }).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
      ${Object.entries(L.token.checks).map(([k, v]) => `<tr><td>check: ${esc(k)}</td><td>${badge(v === "valid" || v === "match" || v === "verified" ? "pass" : v === "unverified" || v === "not_checked" ? "unavailable" : "fail")} ${esc(v)}</td></tr>`).join("")}
      <tr><td>honoured</td><td><b>${L.token.honoured}</b> ${L.token.reasons.map(r => `<br><span class="small">${esc(r)}</span>`).join("")}</td></tr>
    </tbody></table></div>
  </div>
  <div class="grid2">
  <div class="panel"><h2>Machine fingerprint (${esc(L.fingerprint.threshold)} match)</h2>
    ${table(["component", "status"], Object.entries(L.fingerprint.identifiers).map(([k, v]) => ({ component: k, status: v })))}
    <p class="small">composite <code>${esc(L.fingerprint.composite || "(fewer than 2 identifiers collected)")}</code>. Raw identifiers never leave the machine.</p></div>
  <div class="panel"><h2>Offline activation challenge</h2>${phBox(L.placeholders.activation)}${L.offline_challenge ? pre(L.offline_challenge) : `<p class="muted">No fingerprint, no challenge.</p>`}</div>
  </div>`);
};
ACTIONS.saveEntitlements = async () => {
  const caps = Array.from(document.querySelectorAll("input.ent:checked")).map(i => i.value);
  await post("/api/license/entitlements", { capabilities: caps }); notify(`Entitlements now: ${caps.join(", ") || "(none)"}.`); route();
};

SCREENS.surveillance = async function () {
  ssi(true, "Agent-local findings below name assets; the outbound shapes do not.");
  const S = await get(`${P()}/surveillance?simulate=${state.simulate ? "1" : "0"}`);
  const toggle = `<div class="inline"><label><input type="checkbox" id="sim" data-change="toggleSim" ${state.simulate ? "checked" : ""}> Simulate a change (labelled placeholder) so the shape of each path is visible</label></div>`;
  if (!S.frozen_version) { setMain(`<h1>Surveillance</h1><p>${esc(S.note)}</p><p><a href="#facilities">Freeze on the facilities screen.</a></p>`); return; }
  const t1 = S.trigger1, t2 = S.trigger2;
  setMain(`<h1>Surveillance</h1>
  <p class="lead">Runs over frozen version <code>${esc(S.frozen_version.version_id.slice(0, 16))}</code> (sequence ${S.frozen_version.sequence}), never live answers. Two triggers, two separate paths.</p>${toggle}
  <div class="panel"><h2>Trigger 1: ruleset change (engine.diff_runs)</h2>
    <p class="small">pinned ${esc(t1.ruleset_pinned)} vs current ${esc(t1.ruleset_current)} | changed: <b>${t1.changed}</b></p>${phBox(t1.simulation)}
    <div class="grid2"><div><h3>Agent-local finding</h3>${table(["rule_id", "change", "from", "to", "severity", "blocks_export", "citations"], t1.local_finding, (c, r) => c === "citations" ? cites(r.citations) : fmt(r[c]))}</div>
      <div><h3>Outbound telemetry (the only sendable shape)</h3>${pre(t1.outbound_telemetry)}<p class="small muted">Redacted: ${esc(t1.redacted)}.</p></div></div></div>
  <div class="panel"><h2>Trigger 2: KEV catalog change (kev.newly_affected, kev.telemetry)</h2>
    <p class="small">pinned catalog ${esc(t2.catalog_pinned)} vs current ${esc(t2.catalog_current)}</p>
    ${t2.unavailable ? `<span class="badge unavailable">unavailable</span> ${esc(t2.reason)}` : `${phBox(t2.simulation)}
    <p class="small">catalog diff: ${t2.catalog_diff.added.length} added, ${t2.catalog_diff.removed.length} withdrawn | verdict diff: <b>${t2.verdict_diff_changes}</b> changes. ${esc(t2.note)}.</p>
    <div class="grid2"><div><h3>Agent-local finding (SSI, never leaves)</h3>${table(["asset", "cve", "change", "critical"], t2.local_finding)}</div>
      <div><h3>Outbound telemetry</h3>${pre(t2.outbound_telemetry)}<p class="small muted">Redacted: ${esc(t2.redacted)}. Asset-level alerts: ${esc(S.asset_level_alerts)}.</p></div></div>`}</div>
  <div class="panel"><h2>Trigger 3: criticality re-check</h2>${phBox(S.trigger3)}</div>`);
};
CHANGES.toggleSim = el => { state.simulate = el.checked; route(); };

SCREENS.audit = async function () {
  ssi(true);
  const runs = await get(`${P()}/audits`);
  setMain(`<h1>Audit mode</h1>
  <p class="lead">Section-scoped. The report states its scope and names what it did not examine. Deficiency and recommendation are different objects. Runs are over a frozen plan_version.</p>
  <div class="panel"><h2>New run</h2>
    <div class="inline">${state.data.spine.map(s => `<label title="${esc(s.title)}"><input type="checkbox" class="scope" value="${s.number}"> ${s.number}</label>`).join(" ")}</div>
    <div class="inline"><input type="text" id="auditor" placeholder="auditor" value="auditor"><input type="text" id="client-rep" placeholder="client representative"><button data-action="startAudit">Start scoped run</button></div></div>
  <div class="panel"><h2>Runs</h2>${table(["id", "scope", "auditor", "as_of", "status", "findings", "open"], runs, (c, r) => c === "open" ? `<button class="secondary" data-action="openAudit" data-run="${esc(r.id)}">Open</button>` : fmt(r[c]))}</div>
  <div id="audit-run"></div>`);
  if (state.auditRun && runs.some(r => r.id === state.auditRun)) await openAudit(state.auditRun);
};
ACTIONS.startAudit = async () => {
  const scope = Array.from(document.querySelectorAll("input.scope:checked")).map(i => Number(i.value));
  const v = await post(`${P()}/audit`, { scope, auditor: val("auditor"), client_representative: val("client-rep") });
  state.auditRun = v.run.id; notify(`Run ${v.run.id} opened.`); route();
};
ACTIONS.openAudit = el => openAudit(attr(el, "run"));
async function openAudit(id) {
  state.auditRun = id;
  const v = await get(`/api/audit/${id}`);
  const rep = v.report;
  const findingRow = (c, f) => {
    if (c === "citations") return cites(f.citations);
    if (c === "remediation") return f.remediation ? esc(f.remediation) : (f.kind === "deficiency" ? ph(f.remediation_placeholder) : `<span class="muted small">n/a for a recommendation</span>`);
    if (c === "severity") return badge(f.severity);
    return fmt(f[c]);
  };
  const open = v.run.status === "open";
  document.getElementById("audit-run").innerHTML = `
  <div class="panel"><h2>Run ${esc(v.run.id)} <span class="badge ${open ? "info" : "na"}">${esc(v.run.status)}</span></h2>
    <p><b>${esc(rep.scope_statement)}</b></p>
    <p class="small">Over frozen version <code>${esc(v.frozen_version.version_id.slice(0, 16))}</code>. Finding templates version ${esc(v.templates_version)}: none authored.</p>
    <h3>Candidate findings from in-scope verdicts (${v.candidates.length}; ${v.out_of_scope_verdicts} out of scope, dropped)</h3>
    ${table(["accept", "kind", "section", "citations", "text", "template"], v.candidates, (c, f) => {
      if (c === "accept") return f.already_added ? `<span class="badge pass">added</span>` : `<input type="checkbox" class="cand" value="${esc(f.id)}">`;
      if (c === "kind") return badge(f.severity) + " " + esc(f.kind) + (f.kind === "deficiency" && !f.cites_binding_authority ? `<br><span class="badge fail">cites no binding authority</span>` : "");
      if (c === "citations") return cites(f.citations);
      if (c === "template") return ph(f.template);
      return fmt(f[c]);
    })}
    <div class="inline"><button ${open ? "" : "disabled"} data-action="acceptCands" data-run="${esc(v.run.id)}">Accept selected into the run</button></div>
    <h3>Deficiencies (${rep.counts.deficiencies}; ${rep.counts.deficiencies_without_remediation} without remediation)</h3>
    ${table(["id", "section", "severity", "citations", "text", "remediation"], rep.deficiencies, findingRow)}
    <h3>Recommendations (${rep.counts.recommendations})</h3>
    ${table(["id", "section", "severity", "citations", "text", "remediation"], rep.recommendations, findingRow)}
    <div class="inline"><select id="rec-sec">${v.run.scope.map(s => `<option>${s}</option>`).join("")}</select><input type="text" id="rec-text" placeholder="recommendation text (free text is allowed here)">
      <button class="secondary" ${open ? "" : "disabled"} data-action="addRec" data-run="${esc(v.run.id)}">Add recommendation</button>
      <button class="secondary" ${open ? "" : "disabled"} data-action="closeAudit" data-run="${esc(v.run.id)}">Close run</button></div>
  </div>`;
}
ACTIONS.acceptCands = async el => {
  const ids = Array.from(document.querySelectorAll("input.cand:checked")).map(i => i.value);
  await post(`/api/audit/${attr(el, "run")}/accept`, { candidate_ids: ids }); notify(`Accepted ${ids.length} finding(s).`); await openAudit(attr(el, "run"));
};
ACTIONS.addRec = async el => {
  await post(`/api/audit/${attr(el, "run")}/recommendation`, { section: val("rec-sec"), text: val("rec-text") }); notify("Recommendation added."); await openAudit(attr(el, "run"));
};
ACTIONS.closeAudit = async el => { await post(`/api/audit/${attr(el, "run")}/close`); notify("Run closed."); route(); };

SCREENS.records = async function () {
  ssi(true);
  const plan = state.data.plans.find(p => p.id === state.plan);
  const fid = state.facility && plan.facility_ids.includes(state.facility) ? state.facility : plan.facility_ids[0];
  state.facility = fid;
  const R = await get(`/api/facility/${fid}/records`);
  categories = R.categories;
  setMain(`<h1>Records</h1>
  <p class="lead">The six activities records must be created for, with retention read from each category. The ${esc(R.custodian_role)} keeps the record; the ${esc(R.accountable_role)} ensures it is maintained.</p>
  <div class="inline"><label>Facility <select id="facility" data-change="setFacility">${plan.facility_ids.map(f => `<option value="${esc(f)}" ${f === fid ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
    <span class="small">asset type <b>${esc(R.asset_type)}</b> | recordkeeping section: ${R.recordkeeping_authority.placeholder ? ph(R.recordkeeping_authority) : cite(R.recordkeeping_authority)}</span></div>
  <div class="panel"><h2>Categories for ${esc(R.asset_type)}</h2>
    ${table(["id", "label", "section", "fields", "retention", "source_paragraph", "citations"], R.categories, (c, k) => {
      if (c === "retention") return esc(k.retention.basis);
      if (c === "source_paragraph") return `<code>${esc(k.source_paragraph)}</code> <span class="small muted">verified against reference text; not a registry entry</span>`;
      if (c === "citations") return cites(k.citations);
      return fmt(k[c]);
    })}</div>
  <div class="panel"><h2>Records (${R.records.length})</h2>
    ${table(["id", "category", "clock", "custodian", "accountable_officer", "retain_until", "status", "created_by"], R.records, (c, r) => {
      if (c === "clock") return esc(r.fields[R.categories.find(k => k.id === r.category).clock_field]);
      if (c === "custodian") return `${esc(r.custodian.role)}: ${esc(r.custodian.name)}`;
      if (c === "accountable_officer") return `${esc(r.accountable_officer.role)}: ${esc(r.accountable_officer.name)}`;
      if (c === "retain_until") return `${esc(r.retention.retain_until)} <span class="small muted">(${r.retention.days_remaining} days)</span>`;
      if (c === "status") return `<span class="badge ${r.retention.status === "retain" ? "info" : "na"}">${esc(r.retention.status)}</span>`;
      return fmt(r[c]);
    })}
    <h3>Add record</h3>
    <div class="inline"><select id="rec-cat" data-change="recordFields">${R.categories.map(c => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("")}</select>
      <input type="text" id="rec-cust" placeholder="custodian (${esc(R.custodian_role)})"><input type="text" id="rec-acct" placeholder="accountable officer (${esc(R.accountable_role)})"></div>
    <div id="rec-fields"></div>
    <div class="inline"><button data-action="addRecord" data-fid="${esc(fid)}">Add</button></div></div>
  <div class="panel"><h2>Drill and exercise cadence ${cites(R.cadence_citations)}</h2>
    <table class="kv"><tbody>${Object.entries(R.cadence).filter(([k]) => k !== "authority_ids").map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmt(v)}</td></tr>`).join("")}</tbody></table>
    <p class="small muted">Counts and gaps only. No rule reads these records yet.</p></div>`);
  CHANGES.recordFields();
};
CHANGES.setFacility = el => { state.facility = el.value; route(); };
CHANGES.recordFields = () => {
  const cat = categories.find(c => c.id === val("rec-cat"));
  if (!cat) return;
  document.getElementById("rec-fields").innerHTML = `<div class="inline">` + cat.fields.map(f =>
    `<input type="${f === cat.clock_field ? "date" : "text"}" id="rf-${esc(f)}" placeholder="${esc(f)}${f === "attendees" || f === "participants" ? " (comma separated)" : ""}">`).join("") + `</div>`;
};
ACTIONS.addRecord = async el => {
  const cat = categories.find(c => c.id === val("rec-cat"));
  const fields = {};
  cat.fields.forEach(f => { const v = val("rf-" + f); fields[f] = (f === "attendees" || f === "participants") ? v.split(",").map(s => s.trim()).filter(Boolean) : v; });
  await post(`/api/facility/${attr(el, "fid")}/record`, { category: cat.id, fields, custodian: val("rec-cust"), accountable_officer: val("rec-acct"), created_by: "gui" });
  notify("Record added."); route();
};

boot().catch(e => setMain(err(e)));
