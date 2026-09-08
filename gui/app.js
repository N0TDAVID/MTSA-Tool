/* MTSA CSP GUI. Vanilla JS. Talks to /api on 127.0.0.1 and nowhere else.
   The facade decides what is real; this file only renders. Anything the API
   marks {placeholder: true} is rendered with a PLACEHOLDER badge.

   The page is served under a strict Content Security Policy (no inline script,
   no inline style), so every control is wired through delegated listeners on
   data-action (click) and data-change (change) attributes. Never use onclick. */

"use strict";

const state = { plan: null, data: null, screen: "facilities", simulate: false, auditRun: null, facility: null };
const lists = {};   // entity_list editors on the interview screen, keyed by question id
let categories = [];

// ---------------------------------------------------------------- helpers

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const get = p => api("GET", p);
const post = (p, b) => api("POST", p, b || {});

function ph(p) {
  if (!p) return "";
  const reason = typeof p === "string" ? p : p.reason;
  return `<span class="ph">PLACEHOLDER</span><span class="small">${esc(reason)}</span>`;
}
function phBox(p) { return p ? `<div class="ph-box">${ph(p)}</div>` : ""; }

function cite(c) {
  if (!c) return "";
  if (c.missing) return `<span class="cite missing" title="${esc(c.placeholder.reason)}">${esc(c.id)} (not in registry)</span>`;
  const cls = c.binding ? "binding" : "guidance";
  const t = `${c.title} [${c.authority_type}${c.binding ? ", binding" : ", guidance"}]`;
  return `<span class="cite ${cls}" title="${esc(t)}">${esc(c.cite)}</span>`;
}
function cites(list) { return (list || []).map(cite).join(""); }

function badge(status) {
  const map = { pass: "pass", fail: "fail", not_applicable: "na", unavailable: "unavailable",
    binding: "binding", best_practice: "best_practice" };
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
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return esc(v.join(", "));
  if (typeof v === "object") return `<code>${esc(JSON.stringify(v))}</code>`;
  return esc(v);
}
function pre(o) { return `<pre>${esc(JSON.stringify(o, null, 1))}</pre>`; }
function err(e) { return `<div class="error">${esc(e.message || e)}</div>`; }
function attr(el, name) { return el.dataset[name]; }
function val(id) { const el = document.getElementById(id); return el ? el.value : ""; }
function notify(msg) { const n = document.getElementById("notice"); n.textContent = msg; n.hidden = false; clearTimeout(notify.t); notify.t = setTimeout(() => { n.hidden = true; }, 2500); }

const main = document.getElementById("main");
function setMain(html) { main.innerHTML = html; }
function ssi(on, extra) {
  document.getElementById("ssi").textContent = on ? (state.data.ssi_notice + (extra ? " " + extra : "")) : "";
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

async function boot() {
  state.data = await get("/api/state");
  const sel = document.getElementById("plan");
  sel.innerHTML = state.data.plans.map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
  state.plan = state.plan || state.data.plans[0].id;
  sel.value = state.plan;
  sel.addEventListener("change", () => { state.plan = sel.value; route(); });
  const v = state.data.versions;
  document.getElementById("asof").textContent = `as_of ${state.data.as_of}`;
  document.getElementById("footer").innerHTML =
    `ruleset ${esc(v.ruleset_pin)} | question module ${esc(v.question_module)} | clause library ${esc(v.clause_library)} | ` +
    `entitlements: ${esc(state.data.capabilities.join(", ") || "(none)")} | ${ph(v.ruleset_signature)}`;
  route();
}

window.addEventListener("hashchange", route);

async function route() {
  const screen = (location.hash || "#facilities").slice(1);
  state.screen = screen;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + screen));
  setMain(`<p class="muted">Loading.</p>`);
  try {
    state.data = await get("/api/state");
    document.getElementById("footer").querySelector("span") && (document.getElementById("footer").innerHTML =
      `ruleset ${esc(state.data.versions.ruleset_pin)} | question module ${esc(state.data.versions.question_module)} | clause library ${esc(state.data.versions.clause_library)} | ` +
      `entitlements: ${esc(state.data.capabilities.join(", ") || "(none)")} | ${ph(state.data.versions.ruleset_signature)}`);
    await (SCREENS[screen] || SCREENS.facilities)();
  } catch (e) { setMain(err(e)); }
}

const SCREENS = {};

// ---------------------------------------------------------------- 1 facilities

SCREENS.facilities = async function () {
  ssi(true, "Facility names and plan metadata are shown; no plan answers on this screen.");
  const d = state.data;
  const plan = await get(`/api/plan/${state.plan}`);
  const tenantName = id => (d.tenants.find(t => t.id === id) || {}).name || id;
  const facName = id => (d.facilities.find(f => f.id === id) || {}).name || id;

  setMain(`
  <h1>Facilities and plans</h1>
  <p class="lead">Tenant is the consulting org. Facility is a child entity whose owner or operator is the responsible party.
  Plan to facility is many-to-many. Facility transfer exists from day one because clients change consultants.</p>

  <div class="grid2">
  <div class="panel"><h2>Tenants</h2>
    ${table(["id", "name", "kind", "facilities", "plans"], d.tenants.map(t => ({
      id: t.id, name: t.name, kind: t.kind,
      facilities: d.facilities.filter(f => f.tenant_id === t.id).length,
      plans: d.plans.filter(p => p.tenant_id === t.id).length })))}
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
    <p class="small muted">A plan that also covers a facility staying behind is a multi-facility plan under the cited provision and is held for a human to split, not moved.</p>
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
    <p class="small">${cites(d.shared_cyso[0] ? d.shared_cyso[0].citations : [])} Every facility a shared CySO covers must be listed in every one of their plans. Computed across facilities.</p>
    ${table(["cyso", "facilities", "must_list_in_every_plan"], d.shared_cyso)}
  </div>

  <div class="panel"><h2>Frozen versions of ${esc(plan.title)}</h2>
    <p class="small">Every export writes an immutable plan_version pinning four things: ruleset (with the KEV catalog inside the pin), question module, clause library, plan. Attachments are pinned by hash, not embedded.</p>
    <div class="inline"><button data-action="freezePlan">Freeze current answers</button>
    <span class="small muted">Live answers are never rendered or audited; freeze first.</span></div>
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
ACTIONS.freezePlan = async () => { const v = await post(`/api/plan/${state.plan}/freeze`, { frozen_by: "gui" }); notify(`Frozen as sequence ${v.sequence}.`); route(); };
ACTIONS.showDiff = async el => {
  const d = await get(`/api/plan/${state.plan}/diff?from=${attr(el, "from")}&to=${attr(el, "to")}`);
  document.getElementById("diff").innerHTML = `<p class="small">${cites(d.citations)} Answer changes between versions. Pin changes: ${d.pins.length}.</p>` +
    table(["path", "change", "from", "to"], d.answers);
};

// ---------------------------------------------------------------- 2 interview

SCREENS.interview = async function () {
  ssi(true);
  const q = await get(`/api/plan/${state.plan}/questions`);
  const bySection = {};
  q.questions.forEach(n => (bySection[n.section] = bySection[n.section] || []).push(n));
  const titles = Object.fromEntries(state.data.spine.map(s => [s.number, s.title]));
  let html = `<h1>Interview</h1>
  <p class="lead">Question module ${esc(q.question_module_version)}. Applicability is decided by the engine from each node's applies_to predicate against the asset type discriminator.
  Constrained answer types only; identifier and short_text fields may only be presence-checked; narrative fields are never read by a predicate.
  Single answers save when you change them. Lists save with their Save list button. Open the gap report to see the effect.</p>`;
  for (const sec of Object.keys(bySection).sort((a, b) => a - b)) {
    html += `<div class="panel"><h2>Section ${sec}: ${esc(titles[sec])}</h2>` + bySection[sec].map(renderQuestion).join("") + `</div>`;
  }
  setMain(html);
};

function renderQuestion(n) {
  const meta = `<div class="meta">${cites(n.citations)} <code>${esc(n.path)}</code> type <b>${esc(n.type)}</b>` +
    (n.read_by_rules.length ? ` | read by rules: ${esc(n.read_by_rules.join(", "))}` : ` | <span class="muted">no rule reads this path</span>`) +
    (n.applies_to ? ` | applies_to <code>${esc(JSON.stringify(n.applies_to))}</code>` : "") +
    (n.help ? `<br>${esc(n.help)}` : "") + `</div>`;
  if (!n.applicable) {
    return `<div class="q na"><div class="prompt">${esc(n.prompt)} <span class="badge na">not applicable</span></div>${meta}</div>`;
  }
  return `<div class="q" id="q-${esc(n.id)}"><div class="prompt">${esc(n.prompt)}</div>${meta}${inputFor(n)}</div>`;
}

function scalarInput(id, type, value, options, format, change) {
  const v = value == null ? "" : value;
  const ch = change ? ` data-change="${change}"` : "";
  if (type === "boolean") return `<select id="${id}"${ch}><option value="">(unanswered)</option><option value="true" ${v === true ? "selected" : ""}>true</option><option value="false" ${v === false ? "selected" : ""}>false</option></select>`;
  if (type === "enum" || type === "tri_state") return `<select id="${id}"${ch}><option value="">(unanswered)</option>${options.map(o => `<option ${v === o ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  if (type === "enum_multi") return `<span id="${id}" class="inline">${options.map(o => `<label><input type="checkbox" value="${esc(o)}"${ch} ${(Array.isArray(v) && v.includes(o)) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</span>`;
  if (type === "date") return `<input type="date" id="${id}" value="${esc(v)}"${ch}>`;
  if (type === "number") return `<input type="number" id="${id}" value="${esc(v)}"${ch}>`;
  if (type === "narrative") return `<textarea id="${id}"${ch}>${esc(v)}</textarea>`;
  if (type === "identifier_list") return `<input type="text" id="${id}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}" placeholder="comma separated${format ? ", format " + format : ""}"${ch}>`;
  return `<input type="text" id="${id}" value="${esc(v)}" ${format ? `pattern="${esc(format)}" title="format ${esc(format)}"` : ""}${ch}>`;
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

function inputFor(n) {
  const id = "in-" + n.id;
  if (n.type === "entity_list") {
    lists[n.id] = { node: n, rows: JSON.parse(JSON.stringify(Array.isArray(n.value) ? n.value : [])) };
    return `<div id="${id}">${renderList(n.id)}</div>`;
  }
  // The input carries what the save needs; the change listener reads it back.
  return `<div class="inline" data-qid="${esc(n.id)}" data-path="${esc(n.path)}" data-type="${esc(n.type)}">
    ${scalarInput(id, n.type, n.value, n.options, n.format, "saveScalar")}<span class="small muted saved"></span></div>`;
}
function renderList(qid) {
  const { node, rows } = lists[qid];
  const head = node.fields.map(f => `<th>${esc(f.prompt)}<br><span class="small muted">${esc(f.type)}</span></th>`).join("") + "<th></th>";
  const body = rows.map((r, i) => `<tr>${node.fields.map(f => `<td>${scalarInput(`li-${qid}-${i}-${f.key}`, f.type, r[f.key], f.options, f.format)}</td>`).join("")}
    <td><button class="secondary" data-action="listRemove" data-qid="${esc(qid)}" data-index="${i}">Remove</button></td></tr>`).join("");
  return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="inline"><button class="secondary" data-action="listAdd" data-qid="${esc(qid)}">Add row</button>
    <button data-action="listSave" data-qid="${esc(qid)}">Save list (${rows.length} rows)</button><span class="small muted saved"></span></div>`;
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
  await post(`/api/plan/${state.plan}/answer`, { path: lists[qid].node.path, value: rows });
  lists[qid].rows = rows;
  document.getElementById("in-" + qid).innerHTML = renderList(qid);
  document.querySelector(`#in-${CSS.escape(qid)} .saved`).textContent = `saved ${rows.length} rows`;
  notify(`Saved ${lists[qid].node.path} (${rows.length} rows).`);
};
CHANGES.saveScalar = async el => {
  const wrap = el.closest("[data-qid]");
  const qid = attr(wrap, "qid"), type = attr(wrap, "type"), path = attr(wrap, "path");
  const value = readScalar("in-" + qid, type);
  await post(`/api/plan/${state.plan}/answer`, { path, value });
  wrap.querySelector(".saved").textContent = "saved";
};

// ---------------------------------------------------------------- 3 criticality

SCREENS.criticality = async function () {
  ssi(true);
  const c = await get(`/api/plan/${state.plan}/criticality`);
  const s = c.session;
  const pOpts = sel => s.participants.map(p => `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.name)} (${esc(p.role)})</option>`).join("");
  state.recorder = s.participants.some(p => p.id === state.recorder) ? state.recorder : s.facilitator;
  state.onBehalf = s.participants.some(p => p.id === state.onBehalf) ? state.onBehalf : "";
  const q1 = c.questions.find(q => q.id === "is_critical"), q2 = c.questions.find(q => q.id === "tsi_possible");

  const effCell = (a, qid) => {
    const e = a.effective[qid];
    const by = Object.entries(e.by).map(([k, v]) => `${k}: ${v}`).join("; ");
    const cls = e.state === "agreed" ? (e.value === "yes" ? "pass" : "na") : (e.state === "disputed" ? "fail" : "warn");
    return `<span class="badge ${cls}">${esc(e.state)}${e.value ? " " + e.value : ""}</span>
      <select data-change="critAnswer" data-asset="${esc(a.nickname)}" data-question="${qid}" title="record an answer as the participant selected above">
        <option value="">record answer</option><option>yes</option><option>no</option><option>not_applicable</option></select>
      ${by ? `<div class="small muted">${esc(by)}</div>` : ""}`;
  };

  setMain(`<h1>Criticality workshop</h1>
  <p class="lead">Two-stage narrowing. Stage 1 scopes each asset with two separate questions. Stage 2 ranks survivors by consequence.
  Stage 1 is a group activity: every answer is attributed to a participant, and a facilitator may record on a client's behalf.
  ${c.licensed ? "" : `<span class="badge unavailable">criticality capability not licensed: the projection onto the answer set is not applied</span>`}</p>

  <div class="panel"><h2>Session ${esc(s.id)}</h2>
    <p class="small">Opened ${esc(s.opened_at)}. Facilitator: ${esc(s.facilitator)}. ${s.answers.length} attributed answers recorded.</p>
    ${table(["id", "name", "role"], s.participants)}
    <h3>Add participant</h3>
    <div class="inline"><input type="text" id="p-id" placeholder="id"><input type="text" id="p-name" placeholder="name">
      <select id="p-role">${c.roles.map(r => `<option>${esc(r)}</option>`).join("")}</select>
      <button class="secondary" data-action="addParticipant">Add</button></div>
  </div>

  <div class="stage"><h2>Stage 1: scope</h2>
    <div class="panel"><div class="inline">
      <label>Recording as <select id="recorder" data-change="setRecorder">${pOpts(state.recorder)}</select></label>
      <label>On behalf of <select id="on-behalf" data-change="setOnBehalf"><option value="">(self)</option>${pOpts(state.onBehalf)}</select></label>
      <span class="small muted">Attribution for every answer recorded below. The source is the on-behalf participant when set, otherwise the recorder.</span>
    </div></div>

    <h3>Question 1: ${esc(q1.prompt)} ${cites(q1.citations)}</h3>
    ${table(["asset", "OT", "SAM is_critical_system", "is_critical"], c.assets, (col, a) => {
      if (col === "asset") return esc(a.nickname);
      if (col === "OT") return fmt(a.is_ot);
      if (col === "SAM is_critical_system") return fmt(a.sam_is_critical_system);
      return effCell(a, "is_critical");
    })}

    <h3>Question 2: ${esc(q2.prompt)} ${cites(q2.citations)}</h3>
    ${table(["asset", "OT", "tsi_possible"], c.assets, (col, a) => {
      if (col === "asset") return esc(a.nickname);
      if (col === "OT") return fmt(a.is_ot);
      return effCell(a, "tsi_possible");
    })}

    <p class="small muted">Consensus rule: the latest answer from every source must agree. Disagreement is <b>disputed</b> and does not scope an asset in. An asset is in scope only when both questions are agreed <b>yes</b>.</p>
    <div class="grid2">
      <div class="panel"><h3>In scope (${c.stage1.in_scope.length})</h3>${table(["asset_id", "outcome"], c.stage1.in_scope)}</div>
      <div class="panel"><h3>Out of scope (${c.stage1.out_of_scope.length}) / pending (${c.stage1.pending.length})</h3>${table(["asset_id", "outcome", "reason"], c.stage1.out_of_scope.concat(c.stage1.pending))}</div>
    </div>
  </div>

  <div class="stage"><h2>Stage 2: rank</h2>
    ${phBox(c.stage2.rank)}
    <p class="small">Survivors awaiting ranking: ${esc(c.stage2.survivors.map(r => r.asset_id).join(", ") || "none")}. The CFDD weighted instrument lives in SAM and is extended there with TSI questions; no ordering is invented here.</p>
  </div>`);
};
CHANGES.setRecorder = el => { state.recorder = el.value; };
CHANGES.setOnBehalf = el => { state.onBehalf = el.value; };
ACTIONS.addParticipant = async () => {
  await post(`/api/plan/${state.plan}/criticality/participant`, { id: val("p-id"), name: val("p-name"), role: val("p-role") });
  notify("Participant added."); route();
};
CHANGES.critAnswer = async el => {
  if (!el.value) return;
  await post(`/api/plan/${state.plan}/criticality/answer`, {
    asset_id: attr(el, "asset"), question_id: attr(el, "question"), value: el.value,
    participant_id: state.recorder, on_behalf_of: state.onBehalf || null });
  notify(`Recorded ${attr(el, "question")} = ${el.value} for ${attr(el, "asset")}.`);
  route();
};

// ---------------------------------------------------------------- 4 registers

SCREENS.registers = async function () {
  ssi(true);
  const regs = await get(`/api/plan/${state.plan}/registers`);
  setMain(`<h1>Registers</h1>
  <p class="lead">Seven entity-store views, rendered from the answer set, never authored. Each is a controlled attachment: it versions separately from the plan body, so a switch replacement does not trigger an amendment filing, and a plan_version pins it by hash.</p>
  ${regs.map(r => `<div class="panel"><h2>Appendix ${esc(r.designation)}: ${esc(r.title)}</h2>
    <p class="small">${cites(r.citations)} register <code>${esc(r.register)}</code> | attachment version <code>${esc(r.attachment_version)}</code> | controlled attachment: <b>${r.controlled_attachment}</b> | ${r.count} rows${r.catalog_version ? ` | KEV catalog ${esc(r.catalog_version)}` : ""}</p>
    ${r.unavailable ? `<span class="badge unavailable">unavailable</span> <span class="small">${esc(r.unavailable)}</span>` : table(r.columns, r.rows)}
    ${r.fields_not_in_answer_model.length ? `<p class="small muted">Fields this appendix needs that the answer model does not capture yet (shown as absent, not invented): ${esc(r.fields_not_in_answer_model.join(", "))}</p>` : ""}
  </div>`).join("")}`);
};

// ---------------------------------------------------------------- 5 crosswalk

SCREENS.crosswalk = async function () {
  ssi(true);
  const x = await get(`/api/plan/${state.plan}/crosswalk`);
  if (x.unavailable) { setMain(`<h1>Mitigation crosswalk</h1><p><span class="badge unavailable">unavailable</span> ${esc(x.reason)}</p>`); return; }
  setMain(`<h1>Mitigation crosswalk</h1>
  <p class="lead">${cites(x.citations)} One table: critical asset, known vulnerability, what was done. A composite view over appendices ${esc(x.source_appendices.join(", "))}, deliberately not an eighth register. Rows resolve to mitigated, planned, accepted, or unresolved.</p>
  <div class="panel">
    <p class="small">KEV catalog ${esc(x.catalog.version)} (${x.catalog.count} entries). Critical assets: ${x.critical_assets}, of which OT: ${x.ot_critical_assets}. Critical assets with no KEV row: ${esc(x.critical_assets_without_rows.join(", ") || "none")}.</p>
    ${x.ot_critical_assets ? `<p class="small"><b>Read an empty or thin join as absence of coverage, not compliance.</b> KEV is effectively an IT catalog; the real OT signal is ICS-CERT and vendor advisories, which are not KEVs. A critical asset with no CVE source is the no-kev-identification gap, not a clean row.</p>` : ""}
    ${table(["asset", "domain", "cve", "vendor_project", "product", "date_added", "ransomware", "disposition", "compensating_control"], x.rows, (c, r) => {
      if (c === "disposition") return `<span class="badge ${r.disposition === "unresolved" ? "fail" : "pass"}">${esc(r.disposition)}</span>`;
      return fmt(r[c]);
    })}
    <p class="small muted">${esc(x.disposition_note)}. The CISA dueDate is deliberately not shown: it is a federal civilian agency deadline with no application here and more permissive than "without delay".</p>
  </div>`);
};

// ---------------------------------------------------------------- 6 gap report

SCREENS.gap = async function () {
  ssi(true);
  const run = await get(`/api/plan/${state.plan}/evaluate`);
  const s = run.summary;
  setMain(`<h1>Gap report</h1>
  <p class="lead">Real: engine.run_ruleset over the live answer set, ruleset ${esc(run.ruleset_version)}, as_of ${esc(run.as_of)}, enrichment applied: ${esc(run.enrichment_applied.join(", ") || "none")}, capabilities ${esc((run.capabilities || []).join(", ") || "(none)")}.
  A binding failure blocks export. A best-practice failure warns. An unavailable binding rule blocks export because the obligation was not demonstrated.</p>
  <div class="panel">
    <p>${s.export_allowed ? `<span class="ok">EXPORT ALLOWED</span>` : `<span class="blocks">EXPORT BLOCKED</span>`}
    | evaluated ${s.evaluated} | passed ${s.passed} | <b>blocking ${s.blocking}</b> | warnings ${s.warnings} | not applicable ${s.not_applicable} | unavailable ${s.unavailable}</p>
  </div>
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

// ---------------------------------------------------------------- 7 licensing

SCREENS.licensing = async function () {
  ssi(false);
  const L = await get("/api/license");
  const run = await get(`/api/plan/${state.plan}/evaluate`);
  const kv = run.verdicts.find(v => v.rule_id === "kev-without-delay");
  setMain(`<h1>Licensing</h1>
  <p class="lead">No SSI on this screen: entitlement names, rule ids, and hashes only. The module boundary is the licensing boundary. A rule declares requires_capability, a module declares CAPABILITY, the facade passes the entitlement set to the engine. An unlicensed rule is unavailable, never pass.</p>

  <div class="panel"><h2>Active entitlement set (draft toggle)</h2>
    ${phBox(L.placeholders.override)}
    <div class="inline">${L.toggleable.map(c => `<label><input type="checkbox" class="ent" value="${esc(c)}" ${L.active_entitlements.includes(c) ? "checked" : ""}> ${esc(c)}</label>`).join("")}
    <button data-action="saveEntitlements">Apply</button></div>
    <p>Live effect on <code>kev-without-delay</code>: ${badge(kv.status)} ${badge(kv.severity)} | export allowed: <b>${run.summary.export_allowed}</b> | unavailable ${run.summary.unavailable}</p>
  </div>

  <div class="grid2">
  <div class="panel"><h2>Capability modules</h2>
    ${table(["module", "module_version", "licensed", "rules gated"], Object.entries(L.modules).map(([k, m]) => ({ module: k, module_version: m.module_version, licensed: m.licensed, "rules gated": m.rules.join(", ") || "(none yet)" })))}
    <p class="small muted">Tier ceilings: ${esc(Object.entries(L.tier_ceiling).map(([t, c]) => `${t} = {${c.join(", ")}}`).join("; "))}</p>
  </div>
  <div class="panel"><h2>Token</h2>
    ${phBox(L.placeholders.signature)}
    <table class="kv"><tbody>
      ${Object.entries({ tenant: L.token.tenant, tier: L.token.tier, seat: L.token.seat, install_id: L.token.install_id, expires: L.token.expires, ruleset_entitlement: L.token.ruleset_entitlement, capabilities: L.token.capabilities.join(", ") }).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
      ${Object.entries(L.token.checks).map(([k, v]) => `<tr><td>check: ${esc(k)}</td><td>${badge(v === "valid" || v === "match" || v === "verified" ? "pass" : v === "unverified" || v === "not_checked" ? "unavailable" : "fail")} ${esc(v)}</td></tr>`).join("")}
      <tr><td>honoured</td><td><b>${L.token.honoured}</b> ${L.token.reasons.map(r => `<br><span class="small">${esc(r)}</span>`).join("")}</td></tr>
    </tbody></table>
  </div>
  </div>

  <div class="grid2">
  <div class="panel"><h2>Machine fingerprint (${esc(L.fingerprint.threshold)} match)</h2>
    ${table(["component", "status"], Object.entries(L.fingerprint.identifiers).map(([k, v]) => ({ component: k, status: v })))}
    <p class="small">composite <code>${esc(L.fingerprint.composite || "(fewer than 2 identifiers collected)")}</code>. Raw identifiers never leave the machine; only hashes travel.</p>
  </div>
  <div class="panel"><h2>Offline activation challenge</h2>
    ${phBox(L.placeholders.activation)}
    ${L.offline_challenge ? pre(L.offline_challenge) : `<p class="muted">No fingerprint, no challenge.</p>`}
  </div>
  </div>`);
};
ACTIONS.saveEntitlements = async () => {
  const caps = Array.from(document.querySelectorAll("input.ent:checked")).map(i => i.value);
  await post("/api/license/entitlements", { capabilities: caps });
  notify(`Entitlements now: ${caps.join(", ") || "(none)"}.`); route();
};

// ---------------------------------------------------------------- 8 surveillance

SCREENS.surveillance = async function () {
  ssi(true, "Agent-local findings below name assets; the outbound shapes do not.");
  const S = await get(`/api/plan/${state.plan}/surveillance?simulate=${state.simulate ? "1" : "0"}`);
  const toggle = `<div class="inline"><label><input type="checkbox" id="sim" data-change="toggleSim" ${state.simulate ? "checked" : ""}> Simulate a change (labelled placeholder) so the shape of each path is visible</label></div>`;
  if (!S.frozen_version) { setMain(`<h1>Surveillance</h1><p>${esc(S.note)}</p><p><a href="#facilities">Freeze on the facilities screen.</a></p>`); return; }
  const t1 = S.trigger1, t2 = S.trigger2;
  setMain(`<h1>Surveillance</h1>
  <p class="lead">Runs over frozen version <code>${esc(S.frozen_version.version_id.slice(0, 16))}</code> (sequence ${S.frozen_version.sequence}), never live answers, so any change is attributable to the ruleset or the catalog rather than to the customer. Two triggers, two separate paths.</p>
  ${toggle}
  <div class="panel"><h2>Trigger 1: ruleset change (engine.diff_runs)</h2>
    <p class="small">pinned ${esc(t1.ruleset_pinned)} vs current ${esc(t1.ruleset_current)} | changed: <b>${t1.changed}</b></p>
    ${phBox(t1.simulation)}
    <div class="grid2">
      <div><h3>Agent-local finding</h3>${table(["rule_id", "change", "from", "to", "severity", "blocks_export", "citations"], t1.local_finding, (c, r) => c === "citations" ? cites(r.citations) : fmt(r[c]))}
        <p class="small muted">Severity is compared as well as status: best practice to binding flips a compliant plan without a status change.</p></div>
      <div><h3>Outbound telemetry (the only sendable shape)</h3>${pre(t1.outbound_telemetry)}<p class="small muted">Redacted: ${esc(t1.redacted)}.</p></div>
    </div>
  </div>
  <div class="panel"><h2>Trigger 2: KEV catalog change (kev.newly_affected, kev.telemetry)</h2>
    <p class="small">pinned catalog ${esc(t2.catalog_pinned)} vs current ${esc(t2.catalog_current)}</p>
    ${t2.unavailable ? `<span class="badge unavailable">unavailable</span> ${esc(t2.reason)}` : `
    ${phBox(t2.simulation)}
    <p class="small">catalog diff: ${t2.catalog_diff.added.length} added, ${t2.catalog_diff.removed.length} withdrawn | verdict diff across the change: <b>${t2.verdict_diff_changes}</b> changes. ${esc(t2.note)}.</p>
    <div class="grid2">
      <div><h3>Agent-local finding (SSI, never leaves)</h3>${table(["asset", "cve", "change", "critical"], t2.local_finding)}</div>
      <div><h3>Outbound telemetry</h3>${pre(t2.outbound_telemetry)}<p class="small muted">Redacted: ${esc(t2.redacted)}. Asset-level alerts: ${esc(S.asset_level_alerts)}.</p></div>
    </div>`}
  </div>
  <div class="panel"><h2>Trigger 3: criticality re-check</h2>${phBox(S.trigger3)}</div>`);
};
CHANGES.toggleSim = el => { state.simulate = el.checked; route(); };

// ---------------------------------------------------------------- 9 audit

SCREENS.audit = async function () {
  ssi(true);
  const runs = await get(`/api/plan/${state.plan}/audits`);
  const spine = state.data.spine;
  setMain(`<h1>Audit mode</h1>
  <p class="lead">Section-scoped. An auditor samples a few sections chosen with the client; the report states its scope and names what it did not examine. Deficiency (failed binding requirement, cited, carries remediation) and recommendation (enhancement, no failure behind it) are different objects. Runs are over a frozen plan_version.</p>
  <div class="panel"><h2>New run</h2>
    <div class="inline">${spine.map(s => `<label title="${esc(s.title)}"><input type="checkbox" class="scope" value="${s.number}"> ${s.number}</label>`).join(" ")}</div>
    <div class="inline"><input type="text" id="auditor" placeholder="auditor" value="auditor"><input type="text" id="client-rep" placeholder="client representative"><button data-action="startAudit">Start scoped run</button></div>
  </div>
  <div class="panel"><h2>Runs</h2>${table(["id", "scope", "auditor", "as_of", "status", "findings", "open"], runs, (c, r) => c === "open" ? `<button class="secondary" data-action="openAudit" data-run="${esc(r.id)}">Open</button>` : fmt(r[c]))}</div>
  <div id="audit-run"></div>`);
  if (state.auditRun && runs.some(r => r.id === state.auditRun)) await openAudit(state.auditRun);
};
ACTIONS.startAudit = async () => {
  const scope = Array.from(document.querySelectorAll("input.scope:checked")).map(i => Number(i.value));
  const v = await post(`/api/plan/${state.plan}/audit`, { scope, auditor: val("auditor"), client_representative: val("client-rep") });
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
    <p class="small">Over frozen version <code>${esc(v.frozen_version.version_id.slice(0, 16))}</code>, pins ${esc(Object.values(v.frozen_version.pins).join(" | "))}. Finding templates version ${esc(v.templates_version)}: none authored.</p>
    <h3>Candidate findings from in-scope verdicts (${v.candidates.length}; ${v.out_of_scope_verdicts} verdicts out of scope, dropped)</h3>
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
  await post(`/api/audit/${attr(el, "run")}/accept`, { candidate_ids: ids });
  notify(`Accepted ${ids.length} finding(s).`); await openAudit(attr(el, "run"));
};
ACTIONS.addRec = async el => {
  await post(`/api/audit/${attr(el, "run")}/recommendation`, { section: val("rec-sec"), text: val("rec-text") });
  notify("Recommendation added."); await openAudit(attr(el, "run"));
};
ACTIONS.closeAudit = async el => { await post(`/api/audit/${attr(el, "run")}/close`); notify("Run closed."); route(); };

// ---------------------------------------------------------------- 10 records

SCREENS.records = async function () {
  ssi(true);
  const plan = state.data.plans.find(p => p.id === state.plan);
  const fid = state.facility && plan.facility_ids.includes(state.facility) ? state.facility : plan.facility_ids[0];
  state.facility = fid;
  const R = await get(`/api/facility/${fid}/records`);
  categories = R.categories;
  setMain(`<h1>Records</h1>
  <p class="lead">The six activities records must be created for at a minimum, with retention read from each category, never a constant. The ${esc(R.custodian_role)} keeps the record; the ${esc(R.accountable_role)} ensures it is maintained. These are the only artifacts with a real retention clock and the one thing an inspection looks back at.</p>
  <div class="inline"><label>Facility <select id="facility" data-change="setFacility">${plan.facility_ids.map(f => `<option value="${esc(f)}" ${f === fid ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
    <span class="small">asset type <b>${esc(R.asset_type)}</b> | recordkeeping section: ${R.recordkeeping_authority.placeholder ? ph(R.recordkeeping_authority) : cite(R.recordkeeping_authority)}</span></div>

  <div class="panel"><h2>Categories for ${esc(R.asset_type)}</h2>
    ${table(["id", "label", "section", "fields", "retention", "source_paragraph", "citations"], R.categories, (c, k) => {
      if (c === "retention") return esc(k.retention.basis);
      if (c === "source_paragraph") return `<code>${esc(k.source_paragraph)}</code> <span class="small muted">verified against reference text; not a registry entry</span>`;
      if (c === "citations") return cites(k.citations);
      return fmt(k[c]);
    })}
  </div>

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
    <div class="inline"><button data-action="addRecord" data-fid="${esc(fid)}">Add</button></div>
  </div>

  <div class="panel"><h2>Drill and exercise cadence ${cites(R.cadence_citations)}</h2>
    <table class="kv"><tbody>${Object.entries(R.cadence).filter(([k]) => k !== "authority_ids").map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmt(v)}</td></tr>`).join("")}</tbody></table>
    <p class="small muted">Counts and gaps only. Whether the cadence is compliant is a ruleset question, and no rule reads these records yet.</p>
  </div>`);
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
