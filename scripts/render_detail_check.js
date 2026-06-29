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
const html = renderClientDetail(detail);

for (const tile of ["Accounts Receivable", "Accounts Payable", "Admin",
                    "Historical", "Payroll"]) {
  assert(html.includes(tile), `${tile} tile present`);
}
assert(html.includes("‹ all clients"), "back link present");
assert(!/\b\d{2}-\d{7}\b/.test(html), "raw EIN must not appear");
// Masking is only asserted when this client actually has an EIN; demo books
// legitimately carry null EINs (rendered as "—"), so don't couple the test to data.
if (detail.admin && detail.admin.ein_masked) {
  assert(html.includes("•"), "masked EIN dots present when EIN exists");
}
console.log(`OK ${slug}: all 5 tiles render (${html.length} bytes)`);
