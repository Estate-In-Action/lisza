import csv, os

COA = os.path.join(os.path.dirname(__file__), "..", "coa.csv")

def test_payroll_liability_accounts_present():
    rows = {r["code"]: r for r in csv.DictReader(open(COA))}
    for code, name in [
        ("245", "Federal Income Tax Withheld Payable"),
        ("246", "FICA Payable"),
        ("247", "State Income Tax Withheld Payable"),
        ("248", "FUTA Payable"),
        ("249", "SUTA Payable"),
    ]:
        assert code in rows, f"missing COA code {code}"
        assert rows[code]["name"] == name
        assert rows[code]["type"] == "liability"
        assert rows[code]["sign_normal"] == "credit"
        assert rows[code]["group"] == "current_liabilities"
