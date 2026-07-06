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

let DATA = null;
let layout = "tile";
let cardFields = [];
let roloIndex = 0;

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function money(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
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
  return `<div class="tiles">${cards}</div>`;
}

function renderList() {
  const head = `<tr><th>Client</th><th>Type</th><th>Cash</th><th>Open AR</th>
    <th>Open AP</th><th>Last entry</th></tr>`;
  const body = DATA.clients.map(c => `
    <tr class="clickable" data-slug="${esc(c.slug)}"><td><a class="clink" href="#client/${esc(c.slug)}">${esc(c.display_name)}</a>${badge(c)}</td><td style="text-align:left">${esc(c.entity_type || "")}</td>
    <td class="money">${money(c.cash)}</td><td class="money">${money(c.open_ar)}</td>
    <td class="money">${money(c.open_ap)}</td><td>${esc(c.last_entry || "—")}</td></tr>`).join("");
  return `<table>${head}${body}</table>`;
}

function renderRolodex() {
  if (DATA.clients.length === 0) return "";
  if (roloIndex >= DATA.clients.length) roloIndex = 0;
  const c = DATA.clients[roloIndex];
  return `
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
  return `<div class="card"><h3>Reconciliation</h3>` +
    kv("Status", r.status || "—") +
    kv("Statements", r.statement_count ?? 0) +
    kv("Matched", r.matched_count ?? 0) +
    kv("Unmatched", r.unmatched_count ?? 0) +
    kv("Latest", r.latest_statement_date || "—") +
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
        ${inspectionTile(d.inspection)}
        ${payrollTile(d.payroll)}
      </div>
    </div>`;
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
      .then(d => { board.innerHTML = renderClientDetail(d); })
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
    route();
  }).catch(e => {
    document.getElementById("board").innerHTML =
      `<p class="muted">Could not load dashboard.json (${esc(e)}).</p>`;
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    esc, money, kv, renderClientDetail, renderTiles, parseHash, payrollTile,
    inspectionTile, inspectionRows, cashFlowTile, pnlBalanceTile, reconciliationTile, filingTile
  };
}
