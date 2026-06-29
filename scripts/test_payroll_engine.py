from payroll_engine import Employee, compute_paycheck

def _emp(**kw):
    base = dict(name="X", filing_status="single", work_state="CA",
                residence_state="CA", annual_salary=52000.0)
    base.update(kw)
    return Employee(**base)

def test_federal_single_2025_known_point():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.fed_wh == 201.29   # 5233.50 annual / 26

def test_federal_dependent_credit_and_extra():
    r = compute_paycheck(_emp(w4_dependents_amt=2600.0, w4_extra_withholding=15.0),
                         gross=2000.0, ytd=0.0, year=2025)
    # 201.288.. - 2600/26(=100.0) + 15.0 = 116.29
    assert r.fed_wh == 116.29

def test_fica_basic():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.ss_ee == 124.0 and r.ss_er == 124.0
    assert r.medi_ee == 29.0 and r.medi_er == 29.0
    assert r.addl_medi == 0.0

def test_ss_caps_at_wage_base():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=175000.0, year=2025)
    assert r.ss_ee == round(1100.0 * 0.062, 2)   # only 1,100 under 176,100 cap
    over = compute_paycheck(_emp(), gross=2000.0, ytd=176100.0, year=2025)
    assert over.ss_ee == 0.0 and over.ss_er == 0.0

def test_additional_medicare_over_200k_ee_only():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=199000.0, year=2025)
    assert r.addl_medi == round(1000.0 * 0.009, 2)   # only 1,000 over 200k
    assert compute_paycheck(_emp(), gross=2000.0, ytd=100000.0, year=2025).addl_medi == 0.0

def test_state_pa_flat():
    r = compute_paycheck(_emp(work_state="PA", residence_state="PA"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == round(2000.0 * 0.0307, 2)   # 61.40

def test_state_ca_progressive():
    r = compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == 66.65   # (965 + 6% of (52000-39200)) / 26

def test_state_none_tx_fl_zero():
    assert compute_paycheck(_emp(work_state="TX", residence_state="CA"),
                            gross=2000.0, ytd=0.0, year=2025).state_wh == 0.0
    assert compute_paycheck(_emp(work_state="FL", residence_state="FL"),
                            gross=2000.0, ytd=0.0, year=2025).state_wh == 0.0

def test_multistate_work_state_governs():
    # works PA, lives FL -> PA withheld
    r = compute_paycheck(_emp(work_state="PA", residence_state="FL"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.state_wh == round(2000.0 * 0.0307, 2)

def test_futa_caps_at_7000():
    assert compute_paycheck(_emp(), gross=2000.0, ytd=0.0, year=2025).futa == 12.0
    assert compute_paycheck(_emp(), gross=2000.0, ytd=6000.0, year=2025).futa == 6.0
    assert compute_paycheck(_emp(), gross=2000.0, ytd=7000.0, year=2025).futa == 0.0

def test_suta_pa_rate_and_base():
    r = compute_paycheck(_emp(work_state="PA", residence_state="PA"),
                         gross=2000.0, ytd=0.0, year=2025)
    assert r.suta == round(2000.0 * 0.03689, 2)

def test_balance_identity_holds_to_the_cent():
    configs = [
        _emp(),
        _emp(work_state="PA", residence_state="FL"),
        _emp(filing_status="married_jointly", annual_salary=95000.0),
        _emp(annual_salary=245000.0),       # exercises SS cap + addl medicare
        _emp(work_state="TX", residence_state="TX"),
    ]
    for e in configs:
        for ytd in (0.0, 6500.0, 175000.0, 205000.0):
            r = compute_paycheck(e, gross=round(e.annual_salary / 26, 2),
                                 ytd=ytd, year=2025)
            lhs = round(r.gross + r.ss_er + r.medi_er + r.futa + r.suta, 2)
            fica = r.ss_ee + r.ss_er + r.medi_ee + r.medi_er + r.addl_medi
            rhs = round(r.fed_wh + fica + r.state_wh + r.futa + r.suta + r.net, 2)
            assert lhs == rhs, (e.work_state, ytd, lhs, rhs)
