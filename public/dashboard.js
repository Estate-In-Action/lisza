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

function money(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function loadPrefs() {
  layout = localStorage.getItem(LS_LAYOUT) || DATA.prefs.layout || "tile";
  const saved = localStorage.getItem(LS_FIELDS);
  cardFields = saved ? JSON.parse(saved) : (DATA.prefs.card_fields || []);
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
  return `<div class="kv"><span>${k}</span><span class="money">${v}</span></div>`;
}

function renderTiles() {
  const cards = DATA.clients.map(c => `
    <div class="card">
      <h3>${c.display_name}${badge(c)}</h3>
      <div class="muted">${c.entity_type || ""}</div>
      <div class="kv"><span>Cash</span><span class="money pos">${money(c.cash)}</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${c.last_entry || "—"}</div>
    </div>`).join("");
  return `<div class="tiles">${cards}</div>`;
}

function renderList() {
  const head = `<tr><th>Client</th><th>Type</th><th>Cash</th><th>Open AR</th>
    <th>Open AP</th><th>Last entry</th></tr>`;
  const body = DATA.clients.map(c => `
    <tr><td>${c.display_name}${badge(c)}</td><td style="text-align:left">${c.entity_type || ""}</td>
    <td class="money">${money(c.cash)}</td><td class="money">${money(c.open_ar)}</td>
    <td class="money">${money(c.open_ap)}</td><td>${c.last_entry || "—"}</td></tr>`).join("");
  return `<table>${head}${body}</table>`;
}

function renderRolodex() {
  if (DATA.clients.length === 0) return "";
  if (roloIndex >= DATA.clients.length) roloIndex = 0;
  const c = DATA.clients[roloIndex];
  return `
    <div class="rolo">
      <div class="rolo-nav">
        <button id="rolo-prev">‹ prev</button>
        <strong>${c.display_name}${badge(c)}</strong>
        <button id="rolo-next">next ›</button>
      </div>
      <div class="muted">${c.entity_type || ""} · client ${roloIndex + 1} of ${DATA.clients.length}</div>
      <div class="big pos money" style="margin-top:10px">${money(c.cash)}
        <span class="muted" style="font-weight:400">cash</span></div>
      <div class="kv"><span>Open AR</span><span class="money">${money(c.open_ar)}</span></div>
      <div class="kv"><span>Open AP</span><span class="money">${money(c.open_ap)}</span></div>
      ${optionalRows(c)}
      <div class="muted" style="margin-top:6px">Last entry ${c.last_entry || "—"}</div>
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

fetch("dashboard.json").then(r => r.json()).then(d => {
  DATA = d;
  loadPrefs();
  document.getElementById("stamp").textContent = "Data as of " + d.generated_at;
  initLayoutButtons();
  buildFieldToggles();
  render();
}).catch(e => {
  document.getElementById("board").innerHTML =
    `<p class="muted">Could not load dashboard.json (${e}).</p>`;
});
