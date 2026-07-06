"use strict";

const OPTIONAL_FIELDS = [
  { key: "ar_count", label: "AR count" },
  { key: "ap_count", label: "AP count" },
  { key: "entities", label: "Entities" },
  { key: "next_filing_due", label: "Next filing" },
  { key: "status", label: "Status" },
];

const LS_LAYOUT = "lisza_layout";
const LS_FIELDS = "lisza_card_fields";
const LS_PROFILE_DRAFT_PREFIX = "lisza_profile_draft_";

let DATA = null;
let layout = "tile";
let cardFields = [];
let roloIndex = 0;
let workflowFilter = "due_now";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function money(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function profileDraftKey(slug) {
  return LS_PROFILE_DRAFT_PREFIX + slug;
}

function readProfileDraft(slug) {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(profileDraftKey(slug));
    return raw ? JSON.parse(raw) : null;
  } catch (_e) {
    localStorage.removeItem(profileDraftKey(slug));
    return null;
  }
}

function effectiveProfile(d) {
  return readProfileDraft(d.slug) || d.automation_profile || {};
}

function loadPrefs() {
  layout = localStorage.getItem(LS_LAYOUT) || DATA.prefs.layout || "tile";
  const fallback = DATA.prefs.card_fields || [];
  const saved = localStorage.getItem(LS_FIELDS);
  if (!saved) { cardFields = fallback; return; }
  try {
    const parsed = JSON.parse(saved);
    cardFields = Array.isArray(parsed) ? parsed : fallback;
  } catch (_e) {
    localStorage.removeItem(LS_FIELDS);
    cardFields = fallback;
  }
}

function savePrefs() {
  localStorage.setItem(LS_LAYOUT, layout);
  localStorage.setItem(LS_FIELDS, JSON.stringify(cardFields));
}

function has(field) { return cardFields.includes(field); }

function badge(c) { return c.consolidates ? " ⛓" : ""; }

function optionalRows(c) {
  let rows = "";
  if (has("ar_count")) rows += kv("Open invoices", c.ar_count ?? "—");
  if (has("ap_count")) rows += kv("Open bills", c.ap_count ?? "—");
  if (has("entities")) rows += kv("Entities", c.entity_count);
  if (has("next_filing_due")) rows += kv("Next filing", c.next_filing_due || "—");
  if (has("status")) rows += kv("Status", c.status);
  return rows;
}

function kv(k, v) {
  return `<div class="kv"><span>${esc(k)}</span><span class="money">${esc(v)}</span></div>`;
}

function renderTiles() {
  const cards = DATA.clients.map(c => `
    <div class="card clickable" data-slug="${esc(c.slug)}">
      <h3><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</h3>
      <div class="muted">${esc(c.entity_type || "")}</div>
      <div class="kv"><span>Cash</span><span class="money pos">${money(c.cash)}</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${esc(c.last_entry || "—")}</div>
    </div>`).join("");
  return renderWorkflowQueue() + `<div class="tiles">${cards}</div>`;
}

function renderList() {
  const head = `<tr><th>Client</th><th>Type</th><th>Cash</th><th>Open AR</th>
    <th>Open AP</th><th>Last entry</th></tr>`;
  const body = DATA.clients.map(c => `
    <tr class="clickable" data-slug="${esc(c.slug)}"><td><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</td><td style="text-align:left">${esc(c.entity_type || "")}</td>
    <td class="money">${money(c.cash)}</td><td class="money">${money(c.open_ar)}</td>
    <td class="money">${money(c.open_ap)}</td><td>${esc(c.last_entry || "—")}</td></tr>`).join("");
  return renderWorkflowQueue() + `<table>${head}${body}</table>`;
}

function renderRolodex() {
  if (DATA.clients.length === 0) return "";
  if (roloIndex >= DATA.clients.length) roloIndex = 0;
  const c = DATA.clients[roloIndex];
  return renderWorkflowQueue() + `
    <div class="rolo">
      <div class="rolo-nav">
        <button id="rolo-prev" aria-label="Previous client">‹ prev</button>
        <strong><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</strong>
        <button id="rolo-next" aria-label="Next client">next ›</button>
      </div>
      <div class="muted">${esc(c.entity_type || "")} · client ${roloIndex + 1} of ${DATA.clients.length}</div>
      <div class="big pos money" style="margin-top:10px">${money(c.cash)}
        <span class="muted" style="font-weight:400">cash</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${esc(c.last_entry || "—")}</div>
    </div>`;
}

function allDueJobs() {
  if (DATA.workflow && Array.isArray(DATA.workflow.jobs)) {
    return DATA.workflow.jobs.map(j => ({
      ...j,
      key: j.job_key || j.key,
      status: j.planner_status || j.status,
    }));
  }
  return (DATA.clients || []).flatMap(c => (c.due_jobs || []).map(j => ({
    ...j,
    client_slug: c.slug,
    client_name: c.display_name,
  })));
}

function renderWorkflowQueue() {
  const jobs = allDueJobs();
  const visible = jobs.filter(j =>
    workflowFilter === "all" ||
    j.status === workflowFilter ||
    j.workflow_status === workflowFilter);
  const counts = {
    due_now: jobs.filter(j => j.status === "due_now").length,
    upcoming: jobs.filter(j => j.status === "upcoming").length,
    pending_approval: jobs.filter(j => j.workflow_status === "pending_approval").length,
    approved: jobs.filter(j => j.workflow_status === "approved").length,
    completed: jobs.filter(j => j.workflow_status === "completed").length,
    blocked: jobs.filter(j => j.status === "blocked").length,
    all: jobs.length,
  };
  const buttons = ["pending_approval", "approved", "completed", "due_now", "upcoming", "blocked", "all"].map(k =>
    `<button data-workflow-filter="${k}" class="${workflowFilter === k ? "active" : ""}">` +
    `${esc(k.replace("_", " "))} ${counts[k] || 0}</button>`).join("");
  const summary = DATA.workflow && DATA.workflow.summary
    ? `<div class="muted">Control plane: ${esc(JSON.stringify(DATA.workflow.summary))}</div>`
    : `<div class="muted">Advisory queue only. Approval state not generated.</div>`;
  const run = counts.approved
    ? `<button data-workflow-run-approved>Run approved safe reports</button>`
    : "";
  const rows = visible.length ? visible.map(j =>
    `<div class="workflow-row" data-slug="${esc(j.client_slug)}">` +
    `<span><strong>${esc(j.client_name)}</strong> ${esc(j.label || j.key)} ` +
    `<span class="muted">${esc(j.source || "profile")}</span></span>` +
    `<span class="money">${esc(j.workflow_status || "advisory")} · ${esc(j.status || "—")} · ${esc(j.due_date || "—")}</span>` +
    `${j.workflow_status === "pending_approval" ? `<span class="actions">` +
      `<button data-workflow-action="approve" data-workflow-id="${esc(j.workflow_job_id)}">Approve</button>` +
      `<button data-workflow-action="skip" data-workflow-id="${esc(j.workflow_job_id)}">Skip</button>` +
      `</span>` : ""}</div>`
  ).join("") : `<div class="muted">No workflow items in this filter.</div>`;
  return `<div class="card workflow-queue"><h3>Due Work</h3>` +
    `${summary}<div class="queue-tabs">${buttons}${run}</div>${rows}</div>`;
}

function workflowPost(params) {
  return fetch(`/api/lisza?${new URLSearchParams(params).toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).then(r => r.json()).then(j => {
    if (j.error) throw new Error(j.error);
    return refreshWorkflow().then(() => { render(); return j; });
  });
}

function refreshWorkflow() {
  return fetch("/api/lisza?mode=workflow_queue")
    .then(r => r.json())
    .then(j => {
      if (!j.error && DATA) DATA.workflow = j;
      return j;
    })
    .catch(() => DATA && DATA.workflow);
}

function render() {
  const board = document.getElementById("board");
  if (!DATA.clients.length) { board.innerHTML = `<p class="muted">No active clients.</p>`; return; }
  if (layout === "list") board.innerHTML = renderList();
  else if (layout === "rolodex") board.innerHTML = renderRolodex();
  else board.innerHTML = renderTiles();

  document.querySelectorAll("#layout-seg button").forEach(b =>
    b.classList.toggle("active", b.dataset.layout === layout));

  board.querySelectorAll("[data-slug]").forEach(el => {
    el.onclick = (ev) => {
      if (ev.target.closest("a")) return;
      location.hash = "client/" + el.dataset.slug;
    };
  });
  board.querySelectorAll("[data-workflow-filter]").forEach(btn => {
    btn.onclick = () => { workflowFilter = btn.dataset.workflowFilter || "due_now"; render(); };
  });
  board.querySelectorAll("[data-workflow-action]").forEach(btn => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      btn.textContent = "Working...";
      workflowPost({
        mode: "workflow_action",
        action: btn.dataset.workflowAction,
        job_id: btn.dataset.workflowId,
      }).catch(e => { btn.textContent = e.message || "Failed"; });
    };
  });
  const runApproved = board.querySelector("[data-workflow-run-approved]");
  if (runApproved) {
    runApproved.onclick = (ev) => {
      ev.stopPropagation();
      runApproved.textContent = "Running...";
      workflowPost({ mode: "workflow_run_approved" })
        .catch(e => { runApproved.textContent = e.message || "Failed"; });
    };
  }

  if (layout === "rolodex") {
    const prev = document.getElementById("rolo-prev");
    const next = document.getElementById("rolo-next");
    if (prev) prev.onclick = () => { roloIndex = (roloIndex - 1 + DATA.clients.length) % DATA.clients.length; render(); };
    if (next) next.onclick = () => { roloIndex = (roloIndex + 1) % DATA.clients.length; render(); };
  }
}

function buildFieldToggles() {
  const host = document.getElementById("field-toggles");
  host.innerHTML = OPTIONAL_FIELDS.map(f =>
    `<label><input type="checkbox" data-field="${f.key}" ${has(f.key) ? "checked" : ""}> ${f.label}</label>`
  ).join("");
  host.querySelectorAll("input").forEach(inp => {
    inp.onchange = () => {
      const k = inp.dataset.field;
      if (inp.checked) { if (!cardFields.includes(k)) cardFields.push(k); }
      else { cardFields = cardFields.filter(x => x !== k); }
      savePrefs(); render();
    };
  });
}

function initLayoutButtons() {
  document.querySelectorAll("#layout-seg button").forEach(b => {
    b.onclick = () => { layout = b.dataset.layout; savePrefs(); render(); };
  });
}

function agingRows(a) {
  const b = a.aging;
  return [["Current", b.current], ["1–30", b.d1_30], ["31–60", b.d31_60],
          ["61–90", b.d61_90], ["90+", b.d90_plus]]
    .map(([k, v]) => kv(k, money(v))).join("");
}

function topOpenRows(items) {
  if (!items || !items.length) return `<div class="muted">None open</div>`;
  return items.map(it =>
    `<div class="kv"><span>${esc(it.party)} ` +
    `<span class="muted">${esc(it.due_date)}` +
    `${it.days_past_due > 0 ? " · " + it.days_past_due + "d" : ""}</span></span>` +
    `<span class="money">${money(it.amount)}</span></div>`).join("");
}

function trendRows(monthly) {
  return (monthly || []).slice(-6).map(m =>
    `<div class="kv"><span>${esc(m.month)}</span>` +
    `<span class="money">${money(m.revenue)} ` +
    `<span class="muted">rev</span> · ` +
    `<span class="${m.net >= 0 ? "pos" : ""}">${money(m.net)} net</span>` +
    `</span></div>`).join("");
}

function w2Rows(list) {
  if (!list || !list.length) return `<div class="muted">No W-2s</div>`;
  return list.map(w =>
    `<div class="kv"><span>${esc(w.employee)} ` +
    `<span class="muted">${esc(w.entity)}</span></span>` +
    `<span class="money">${money(w.box1_wages)} ` +
    `<span class="muted">wages</span> · ` +
    `${money(w.box2_fed_wh)} fed</span></div>`).join("");
}

function form941Rows(list) {
  if (!list || !list.length) return `<div class="muted">No 941 periods</div>`;
  return list.map(f =>
    `<div class="kv"><span>${esc(f.entity)} ` +
    `<span class="muted">Q${esc(f.quarter)} ${esc(f.year)}</span></span>` +
    `<span class="money">${money(f.total_liability)} ` +
    `<span class="muted">liability</span></span></div>`).join("");
}

function payrollTile(p) {
  if (!p || p.status !== "active") {
    return `<div class="card payroll-stub"><h3>Payroll</h3>` +
      `<div class="muted">${esc((p && p.message) || "No payroll runs on file")}</div></div>`;
  }
  const s = p.summary;
  return `<div class="card"><h3>Payroll <span class="muted">${esc(p.year)}</span></h3>` +
    kv("Employees", s.employees) +
    kv("Pay runs", s.run_count) +
    kv("Gross", money(s.gross)) +
    kv("Net", money(s.net)) +
    kv("Employer tax", money(s.employer_tax_total)) +
    `<h4>W-2 (per employee)</h4>${w2Rows(p.w2)}` +
    `<h4>941 (per entity / quarter)</h4>${form941Rows(p.form941)}` +
    `</div>`;
}

function inspectionRows(periods) {
  const order = ["month", "quarter", "year"];
  return order.map(k => {
    const p = periods && periods[k];
    if (!p) return kv(k, "—");
    const balanced = Number(p.debit_total || 0) === Number(p.credit_total || 0);
    const range = `${p.start || "—"} → ${p.end || "—"}`;
    const flags = [
      `${p.row_count || 0} rows`,
      balanced ? "balanced" : "out of balance",
      p.truncated ? "truncated" : null,
    ].filter(Boolean).join(" · ");
    return `<div class="kv"><span>${esc(k)} <span class="muted">${esc(range)}</span></span>` +
      `<span class="money">${esc(flags)}</span></div>`;
  }).join("");
}

function inspectionTile(i) {
  if (!i || !i.periods) {
    return `<div class="card"><h3>Inspection</h3>` +
      `<div class="muted">No inspection views available</div></div>`;
  }
  return `<div class="card"><h3>Inspection</h3>` +
    `<div class="muted">Read-only ledger slices for bookkeeper review.</div>` +
    `${inspectionRows(i.periods)}` +
    `</div>`;
}


function cashFlowTile(c) {
  if (!c || c.status !== "active") {
    return `<div class="card"><h3>Cash Flow</h3><div class="muted">No posted activity</div></div>`;
  }
  return `<div class="card"><h3>Cash Flow</h3>` +
    kv("Inflow", money(c.inflow)) +
    kv("Outflow", money(c.outflow)) +
    kv("Net", money(c.net)) +
    kv("Ending cash", money(c.ending_cash)) +
    `<div class="muted">${esc(c.start || "—")} → ${esc(c.end || "—")}</div></div>`;
}

function pnlBalanceTile(p) {
  if (!p || p.status !== "active") {
    return `<div class="card"><h3>P&L / Balance Sheet</h3><div class="muted">No posted activity</div></div>`;
  }
  const period = p.period || {};
  const bs = p.balance_sheet || {};
  return `<div class="card"><h3>P&L / Balance Sheet</h3>` +
    kv("Income", money(period.income)) +
    kv("Expense", money(period.expense)) +
    kv("Net income", money(period.net_income)) +
    `<h4>Balance sheet</h4>` +
    kv("Assets", money(bs.assets)) +
    kv("Liabilities", money(bs.liabilities)) +
    kv("Equity", money(bs.equity_total)) +
    `</div>`;
}

function reconciliationTile(r) {
  if (!r) {
    return `<div class="card"><h3>Reconciliation</h3><div class="muted">No statement data</div></div>`;
  }
  const lines = (r.lines || []).slice(0, 8).map(l =>
    `<div class="kv"><span>${esc(l.description || "Statement line")} ` +
    `<span class="muted">${esc(l.statement_date || "—")} · ${esc(l.status || "—")}</span></span>` +
    `<span class="money">${money(l.amount)}</span></div>`).join("");
  return `<div class="card"><h3>Reconciliation</h3>` +
    kv("Status", r.status || "—") +
    kv("Statements", r.statement_count ?? 0) +
    kv("Matched", r.matched_count ?? 0) +
    kv("Unmatched", r.unmatched_count ?? 0) +
    kv("Latest", r.latest_statement_date || "—") +
    `<h4>Recent lines</h4>${lines || `<div class="muted">No statement lines</div>`}` +
    `</div>`;
}

function filingTile(f) {
  if (!f) {
    return `<div class="card"><h3>Filing / Tax</h3><div class="muted">No filing profile</div></div>`;
  }
  return `<div class="card"><h3>Filing / Tax</h3>` +
    kv("Status", f.status || "—") +
    kv("Cadence", f.filing_cadence || "—") +
    kv("Next due", f.next_filing_due || "—") +
    kv("Days", f.days_until_due ?? "—") +
    kv("Est. tax paid YTD", money(f.estimated_tax_paid_ytd || 0)) +
    kv("Payroll tax liability", money(f.payroll_tax_liability || 0)) +
    `</div>`;
}

function dueJobRows(jobs) {
  if (!jobs || !jobs.length) return `<div class="muted">No due jobs in the current window</div>`;
  return jobs.map(j =>
    `<div class="kv"><span>${esc(j.label || j.key)} ` +
    `<span class="muted">${esc(j.source || "profile")}</span></span>` +
    `<span class="money">${esc(j.status || "—")} · ${esc(j.due_date || "—")}</span></div>`
  ).join("");
}

function automationWorkflowTile(d) {
  const p = effectiveProfile(d);
  const reports = p.reports || {};
  const jurisdictions = (p.sales_tax_jurisdictions || []).join(", ");
  return `<div class="card workflow-card" data-workflow-slug="${esc(d.slug)}"><h3>Automation Workflow</h3>` +
    kv("Delivery", p.delivery || "dashboard") +
    kv("Filing cadence", p.filing_cadence || "quarterly") +
    kv("Active window", p.active_window || "1y") +
    kv("Payroll", p.payroll_schedule || "none") +
    kv("Sales tax", jurisdictions || "—") +
    `<h4>Due jobs</h4>${dueJobRows(d.due_jobs)}` +
    `<h4>Profile draft</h4>` +
    `<div class="form-grid">` +
      `<label>Delivery<select data-profile-field="delivery">` +
        `${["dashboard", "email", "telegram"].map(v => `<option value="${v}" ${p.delivery === v ? "selected" : ""}>${v}</option>`).join("")}` +
      `</select></label>` +
      `<label>Filing<select data-profile-field="filing_cadence">` +
        `${["monthly", "quarterly", "annual"].map(v => `<option value="${v}" ${p.filing_cadence === v ? "selected" : ""}>${v}</option>`).join("")}` +
      `</select></label>` +
      `<label>Active window<input data-profile-field="active_window" value="${esc(p.active_window || "1y")}"></label>` +
      `<label>Payroll<input data-profile-field="payroll_schedule" value="${esc(p.payroll_schedule || "none")}"></label>` +
      `<label class="wide">Sales tax jurisdictions<input data-profile-field="sales_tax_jurisdictions" value="${esc(jurisdictions)}"></label>` +
      `<label><input type="checkbox" data-profile-report="weekly_digest" ${reports.weekly_digest !== false ? "checked" : ""}> Weekly digest</label>` +
      `<label><input type="checkbox" data-profile-report="monthly_close" ${reports.monthly_close !== false ? "checked" : ""}> Monthly close</label>` +
      `<label><input type="checkbox" data-profile-report="quarterly_packet" ${reports.quarterly_packet !== false ? "checked" : ""}> Quarterly packet</label>` +
    `</div>` +
    `<div class="actions"><button data-profile-save="${esc(d.slug)}">Save draft</button>` +
    `<button data-profile-persist="${esc(d.slug)}">Persist profile</button>` +
    `<button data-profile-clear="${esc(d.slug)}">Clear draft</button></div>` +
    `<div class="muted" data-profile-status>Save a draft locally, or persist through the API profile writer.</div>` +
    `</div>`;
}


function renderClientDetail(d) {
  const ents = (d.admin.entities || []);
  const entRows = ents.length > 1
    ? "<h4>Entities</h4>" + ents.map(e =>
        `<div class="kv"><span>${esc(e.name)}</span>` +
        `<span class="muted">${esc(e.type)}</span></div>`).join("")
    : "";
  return `
    <div class="detail">
      <a class="back" href="#">‹ all clients</a>
      <h2>${esc(d.display_name)} <span class="muted">${esc(d.entity_type || "")}</span></h2>
      <div class="stamp">As of ${esc(d.as_of || "—")}</div>
      <div class="detail-tiles">
        <div class="card"><h3>Accounts Receivable</h3>
          <div class="kv"><span>Open</span><span class="money">${money(d.ar.open_total)} · ${esc(d.ar.open_count)}</span></div>
          ${agingRows(d.ar)}
          <h4>Top open</h4>${topOpenRows(d.ar.top_open)}
        </div>
        <div class="card"><h3>Accounts Payable</h3>
          <div class="kv"><span>Open</span><span class="money">${money(d.ap.open_total)} · ${esc(d.ap.open_count)}</span></div>
          ${agingRows(d.ap)}
          <h4>Top open</h4>${topOpenRows(d.ap.top_open)}
        </div>
        <div class="card"><h3>Admin</h3>
          ${kv("Legal name", d.admin.legal_name || "—")}
          ${kv("EIN", d.admin.ein_masked || "—")}
          ${kv("Fiscal year end", d.admin.fiscal_year_end || "—")}
          ${kv("Filing cadence", d.admin.filing_cadence || "—")}
          ${kv("Next filing due", d.admin.next_filing_due || "—")}
          ${entRows}
        </div>
        <div class="card"><h3>Historical</h3>
          ${kv("Data span", (d.historical.span.first || "—") + " → " + (d.historical.span.last || "—"))}
          ${kv("Posted entries", d.historical.entry_count)}
          <h4>Recent months</h4>${trendRows(d.historical.monthly)}
        </div>
        ${cashFlowTile(d.cash_flow)}
        ${pnlBalanceTile(d.pnl_balance)}
        ${reconciliationTile(d.reconciliation)}
        ${filingTile(d.filing_obligations)}
        ${automationWorkflowTile(d)}
        ${inspectionTile(d.inspection)}
        ${payrollTile(d.payroll)}
      </div>
    </div>`;
}

function bindWorkflowDrafts(host, d) {
  const collectProfile = (card) => {
    const profile = JSON.parse(JSON.stringify(effectiveProfile(d)));
    profile.reports = profile.reports || {};
    card.querySelectorAll("[data-profile-field]").forEach(inp => {
      const key = inp.dataset.profileField;
      if (key === "sales_tax_jurisdictions") {
        profile[key] = inp.value.split(",").map(x => x.trim()).filter(Boolean);
      } else {
        profile[key] = inp.value;
      }
    });
    card.querySelectorAll("[data-profile-report]").forEach(inp => {
      profile.reports[inp.dataset.profileReport] = inp.checked;
    });
    return profile;
  };
  host.querySelectorAll("[data-profile-save]").forEach(btn => {
    btn.onclick = () => {
      const card = btn.closest("[data-workflow-slug]");
      const profile = collectProfile(card);
      localStorage.setItem(profileDraftKey(d.slug), JSON.stringify(profile));
      card.querySelector("[data-profile-status]").textContent = "Draft saved in this browser.";
    };
  });
  host.querySelectorAll("[data-profile-persist]").forEach(btn => {
    btn.onclick = () => {
      const card = btn.closest("[data-workflow-slug]");
      const profile = collectProfile(card);
      card.querySelector("[data-profile-status]").textContent = "Persisting profile...";
      fetch(`/api/lisza?mode=automation_profile&client=${encodeURIComponent(d.slug)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      }).then(r => r.json()).then(j => {
        if (j.error) throw new Error(j.error);
        localStorage.removeItem(profileDraftKey(d.slug));
        d.automation_profile = j.profile || profile;
        d.due_jobs = j.due_jobs || d.due_jobs || [];
        host.innerHTML = renderClientDetail(d);
        bindWorkflowDrafts(host, d);
      }).catch(e => {
        card.querySelector("[data-profile-status]").textContent = `Profile write failed: ${e.message || e}`;
      });
    };
  });
  host.querySelectorAll("[data-profile-clear]").forEach(btn => {
    btn.onclick = () => {
      localStorage.removeItem(profileDraftKey(d.slug));
      host.innerHTML = renderClientDetail(d);
      bindWorkflowDrafts(host, d);
    };
  });
}

function parseHash() {
  const h = (typeof location !== "undefined" ? location.hash : "").replace(/^#/, "");
  if (h.indexOf("client/") === 0) return { view: "client", slug: h.slice(7) };
  return { view: "dashboard" };
}

function route() {
  const r = parseHash();
  const controls = document.querySelector(".controls");
  const board = document.getElementById("board");
  const stamp = document.getElementById("stamp");
  if (r.view === "client") {
    if (controls) controls.style.visibility = "hidden";
    if (stamp) stamp.textContent = "";
    board.innerHTML = `<p class="muted">Loading…</p>`;
    fetch(`clients/${encodeURIComponent(r.slug)}.json`)
      .then(x => x.json())
      .then(d => { board.innerHTML = renderClientDetail(d); bindWorkflowDrafts(board, d); })
      .catch(e => { board.innerHTML =
        `<p class="muted">Could not load client (${esc(e)}).</p>`; });
  } else {
    if (controls) controls.style.visibility = "visible";
    if (stamp && DATA) stamp.textContent = "Data as of " + DATA.generated_at;
    render();
  }
}

if (typeof document !== "undefined" && document.getElementById) {
  window.addEventListener("hashchange", route);
  fetch("dashboard.json").then(r => r.json()).then(d => {
    DATA = d;
    loadPrefs();
    initLayoutButtons();
    buildFieldToggles();
    refreshWorkflow().finally(route);
  }).catch(e => {
    document.getElementById("board").innerHTML =
      `<p class="muted">Could not load dashboard.json (${esc(e)}).</p>`;
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    esc, money, kv, renderClientDetail, renderTiles, parseHash, payrollTile,
    inspectionTile, inspectionRows, cashFlowTile, pnlBalanceTile, reconciliationTile,
    filingTile, dueJobRows, automationWorkflowTile
  };
}
