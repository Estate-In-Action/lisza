// scripts/render_detail_check.js — verify detail tiles render + escape, no browser.
"use strict";
const fs = require("fs");
const path = require("path");

const mod = require(path.resolve(__dirname, "../public/dashboard.js"));
const { renderClientDetail, esc } = mod;

function assert(cond, msg) {
  if (!cond) { console.error("FAIL:", msg); process.exit(1); }
}

assert(esc("<b>&\"") === "&lt;b&gt;&amp;&quot;", "esc escapes");

const slug = process.argv[2] || "guitar-works";
const detail = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, `../public/clients/${slug}.json`), "utf8"));
if (!detail.inspection) {
  detail.inspection = {
    periods: {
      month: { start: "2026-06-01", end: "2026-06-30", row_count: 2, debit_total: 100, credit_total: 100, truncated: false },
      quarter: { start: "2026-04-01", end: "2026-06-30", row_count: 4, debit_total: 200, credit_total: 200, truncated: false },
      year: { start: "2026-01-01", end: "2026-06-30", row_count: 6, debit_total: 300, credit_total: 300, truncated: false },
    }
  };
}

if (!detail.cash_flow) {
  detail.cash_flow = { status: "active", start: "2026-06-01", end: "2026-06-30", inflow: 1000, outflow: 400, net: 600, ending_cash: 600 };
}
if (!detail.pnl_balance) {
  detail.pnl_balance = { status: "active", period: { income: 1000, expense: 400, net_income: 600 }, balance_sheet: { assets: 600, liabilities: 0, equity_total: 600 } };
}
if (!detail.reconciliation) {
  detail.reconciliation = {
    status: "needs_review", statement_count: 2, matched_count: 1,
    unmatched_count: 1, latest_statement_date: "2026-06-30",
    lines: [
      { statement_date: "2026-06-30", description: "Bank fee", amount: -12, status: "unmatched" },
      { statement_date: "2026-06-29", description: "Deposit", amount: 1000, status: "matched", match_method: "auto_exact", matched_entry_id: 12 },
    ],
  };
}
if (detail.reconciliation && Array.isArray(detail.reconciliation.lines) &&
    !detail.reconciliation.lines.some(l => l.match_method)) {
  detail.reconciliation.lines.push({
    statement_date: "2026-06-29",
    description: "Deposit",
    amount: 1000,
    status: "matched",
    match_method: "auto_exact",
    matched_entry_id: 12,
  });
}
if (!detail.filing_obligations) {
  detail.filing_obligations = { status: "due_soon", filing_cadence: "quarterly", next_filing_due: "2026-07-31", days_until_due: 22, estimated_tax_paid_ytd: 100, payroll_tax_liability: 25 };
}
if (!detail.automation_profile) {
  detail.automation_profile = {
    reports: { weekly_digest: true, monthly_close: true, quarterly_packet: true },
    filing_cadence: "quarterly",
    sales_tax_jurisdictions: ["DE"],
    active_window: "1y",
    payroll_schedule: "biweekly",
    delivery: "dashboard",
  };
}
if (!detail.automation_setup) {
  detail.automation_setup = {
    status: "configured",
    required_complete: 4,
    required_total: 4,
    questions: [
      { key: "delivery", label: "How should completed report-prep receipts be surfaced?", field: "delivery", current: "dashboard", required: true, complete: true },
    ],
    suggested_profile: detail.automation_profile,
  };
}
if (!detail.due_jobs) {
  detail.due_jobs = [{ key: "monthly_close", label: "Monthly close review", due_date: "2026-07-05", status: "due_now", source: "last_entry_date" }];
}
if (!detail.document_requests) {
  detail.document_requests = {
    summary: { open: 1, requested: 1, overdue: 0, received: 0, waived: 0 },
    requests: [
      { doc_key: "w9", label: "W-9 or tax identity form", due_date: "2026-07-20", status: "requested", followup_count: 0 },
    ],
  };
}


if (!detail.document_workspace) {
  detail.document_workspace = {
    schemas: {
      invoice: { label: "Invoice", sections: [{ key: "header", label: "Header", fields: ["number", "party", "amount", "status", "send_invoice"] }] },
      bill: { label: "Bill", sections: [{ key: "header", label: "Header", fields: ["number", "party", "amount", "status"] }] },
      journal: { label: "Journal Entry", sections: [{ key: "header", label: "Header", fields: ["number", "entry_date", "description", "status"] }] },
      payment: { label: "Payment", sections: [{ key: "header", label: "Header", fields: ["number", "entry_date", "party", "amount"] }] },
    },
    number_series: { invoice: { next_number: "INV-00001" }, bill: { next_number: "BILL-00001" }, journal: { next_number: "JRN-00001" }, payment: { next_number: "PAY-00001" } },
    documents: {
      invoice: [{ id: 7, party: "Customer A", issue_date: "2026-07-01", due_date: "2026-07-31", amount: 1200, status: "open" }],
      bill: [], journal: [], payment: [],
    },
    pending_actions: [{ document_type: "invoice", document_id: 7, action: "send_invoice", label: "Send invoice #7", party: "Customer A", preview_number: "INV-00001" }],
  };
}
if (!detail.financial_state) {
  detail.financial_state = {
    status: "active",
    liquidity: { cash: 600, open_ar: 1200, open_ap: 300, net_position: 1500, scale: 1200 },
    performance: { income: 1000, expense: 400, net_income: 600, operating_cash_net: 600, scale: 1000 },
    balance: { assets: 600, liabilities: 0, equity: 600 },
  };
}

const html = renderClientDetail(detail);

for (const tile of ["Accounts Receivable", "Accounts Payable", "Admin",
                    "Historical", "Cash Flow", "P&L / Balance Sheet",
                    "Reconciliation", "Filing / Tax", "Client Financial State",
                    "Automation Workflow", "Client Portal / Documents",
                    "Document Workspace", "Inspection", "Payroll"]) {
  assert(html.includes(tile), `${tile} tile present`);
}
assert(html.includes("Guided setup"), "guided setup section present");
assert(html.includes("Apply suggested setup"), "suggested setup action present");
assert(html.includes("‹ all clients"), "back link present");
assert(html.includes("Read-only ledger slices"), "inspection helper copy present");
assert(html.includes("auto exact"), "reconciliation match method renders");

assert(html.includes("INV-00001"), "number series preview renders");
assert(html.includes("Queue approval"), "approval-gated document action renders");
assert(html.includes("#client/" + detail.slug + "/document/invoice/"), "document detail links render");
const openInvoice = detail.document_workspace.documents.invoice.find(x => x.status === "open") ||
  detail.document_workspace.documents.invoice[0];
const docHtml = mod.renderDocumentDetail(detail, "invoice", openInvoice.id);
assert(docHtml.includes("Invoice #"), "invoice detail page renders");
if (openInvoice.status === "open") {
  assert(docHtml.includes("Queue send approval"), "invoice detail send approval action renders");
}
if (detail.inspection && detail.inspection.periods) {
  for (const period of ["month", "quarter", "year"]) {
    assert(html.includes(period), `${period} inspection row present`);
  }
}
assert(!/\b\d{2}-\d{7}\b/.test(html), "raw EIN must not appear");
// Masking is only asserted when this client actually has an EIN; demo books
// legitimately carry null EINs (rendered as "—"), so don't couple the test to data.
if (detail.admin && detail.admin.ein_masked) {
  assert(html.includes("•"), "masked EIN dots present when EIN exists");
}

const p = detail.payroll || {};
if (p.status === "active") {
  assert(html.includes("W-2 (per employee)"), "W-2 section renders for active payroll");
  assert(html.includes("941 (per entity / quarter)"), "941 section renders for active payroll");
  assert(p.w2 && p.w2.length > 0, "active payroll has at least one W-2");
} else {
  assert(html.includes("No payroll runs on file"), "empty payroll state renders");
}
console.log(`OK ${slug}: all 11 tiles render (${html.length} bytes)`);
