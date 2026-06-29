import payroll_tax_tables as t

def test_bracket_tax_single_2025_known_point():
    sched = t.fed_schedule(2025, "single")
    # annual adjusted 52,000 -> 1192.50 + 12% of (52000-18325)
    assert round(t.bracket_tax(sched, 52000.0), 2) == 5233.50

def test_fed_year_clamp():
    # 2022/2023 clamp to 2024; 2026 clamps to 2025
    assert t.fed_schedule(2022, "single") == t.fed_schedule(2024, "single")
    assert t.fed_schedule(2026, "married_jointly") == t.fed_schedule(2025, "married_jointly")

def test_ss_wage_base_per_year():
    assert t.ss_wage_base(2024) == 168600
    assert t.ss_wage_base(2025) == 176100
    assert t.ss_wage_base(2026) == 183600

def test_fica_futa_constants():
    assert t.SS_RATE == 0.062
    assert t.MEDICARE_RATE == 0.0145
    assert t.ADDL_MEDICARE_RATE == 0.009
    assert t.ADDL_MEDICARE_THRESHOLD == 200000
    assert t.FUTA_RATE == 0.006
    assert t.FUTA_WAGE_BASE == 7000

def test_state_rules_archetypes():
    assert t.STATE_RULES["PA"].kind == "flat"
    assert round(t.STATE_RULES["PA"].flat_rate, 4) == 0.0307
    assert t.STATE_RULES["CA"].kind == "progressive"
    assert t.STATE_RULES["TX"].kind == "none"
    assert t.STATE_RULES["FL"].kind == "none"
    # TX/FL still levy SUTA even with no income tax
    assert t.STATE_RULES["TX"].suta_rate > 0
