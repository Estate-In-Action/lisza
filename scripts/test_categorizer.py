import categorizer
import post_entry
import tenancy


def _client(tmp_path, monkeypatch, slug="acme"):
    monkeypatch.setenv("LISZA_HOME", str(tmp_path))
    tenancy.register_client(slug=slug, display_name="Acme Co")
    return slug


def _post_expense(slug, payee, expense_code, amount=10.0, desc=None):
    db = str(tenancy.resolve_db(slug))
    return post_entry.post_json({
        "entry_date": "2026-07-01",
        "description": desc or f"{payee} charge",
        "payee": payee,
        "post": True,
        "splits": [
            {"account": expense_code, "dr": amount, "cr": 0},
            {"account": "102", "dr": 0, "cr": amount},
        ],
    }, db)


# --- Phase 1: rule CRUD ------------------------------------------------------

def test_add_and_list_rule(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    rid = categorizer.add_rule(slug, pattern="AWS", account_code="540")
    rules = categorizer.list_rules(slug)
    assert len(rules) == 1
    r = rules[0]
    assert r["id"] == rid
    assert r["pattern"] == "AWS"
    assert r["account_code"] == "540"
    assert r["account_name"] == "Software & Subscriptions"
    assert r["source"] == "user"
    assert r["match_kind"] == "contains"


def test_add_rule_rejects_unknown_account(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    try:
        categorizer.add_rule(slug, pattern="AWS", account_code="999")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "account" in str(e).lower()


def test_add_rule_rejects_bad_match_kind(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    try:
        categorizer.add_rule(slug, pattern="AWS", account_code="540", match_kind="regex")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "match_kind" in str(e)


def test_delete_rule_is_soft(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    rid = categorizer.add_rule(slug, pattern="AWS", account_code="540")
    categorizer.delete_rule(slug, rid)
    assert categorizer.list_rules(slug) == []
    archived = categorizer.list_rules(slug, include_inactive=True)
    assert len(archived) == 1
    assert archived[0]["active"] == 0


# --- Phase 2: learning -------------------------------------------------------

def test_learn_creates_rule_for_dominant_payee(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    for _ in range(3):
        _post_expense(slug, "AWS", "540")
    learned = categorizer.learn(slug)
    assert len(learned) == 1
    rec = learned[0]
    assert rec["pattern"] == "aws"
    assert rec["account_code"] == "540"
    assert rec["confidence"] == 1.0
    assert rec["action"] == "created"
    # persisted as a learned rule below the user tier
    rules = categorizer.list_rules(slug)
    assert rules[0]["source"] == "learned"
    assert rules[0]["priority"] == 500


def test_learn_skips_thin_and_ambiguous(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    _post_expense(slug, "ONCE", "540")                 # thin: 1 < min_count
    for _ in range(2):
        _post_expense(slug, "SPLIT", "540")
    for _ in range(2):
        _post_expense(slug, "SPLIT", "525")            # ambiguous: 0.5 share
    learned = categorizer.learn(slug)
    assert learned == []


def test_learn_does_not_override_user_rule(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    categorizer.add_rule(slug, pattern="AWS", account_code="530")  # human says Travel
    for _ in range(5):
        _post_expense(slug, "AWS", "540")
    learned = categorizer.learn(slug)
    assert learned == []
    rules = categorizer.list_rules(slug)
    assert len(rules) == 1
    assert rules[0]["account_code"] == "530"
    assert rules[0]["source"] == "user"


def test_learn_is_idempotent(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    for _ in range(3):
        _post_expense(slug, "AWS", "540")
    first = categorizer.learn(slug)
    assert first[0]["action"] == "created"
    second = categorizer.learn(slug)
    assert second[0]["action"] == "updated"
    assert len(categorizer.list_rules(slug)) == 1


def test_learn_dry_run_does_not_persist(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    for _ in range(3):
        _post_expense(slug, "AWS", "540")
    proposed = categorizer.learn(slug, apply=False)
    assert proposed[0]["action"] == "proposed"
    assert categorizer.list_rules(slug) == []


# --- Phase 2: suggestion surface --------------------------------------------

def test_suggest_flags_agreement(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    _post_expense(slug, "AWS", "540", desc="AWS cloud")
    categorizer.add_rule(slug, pattern="aws", account_code="540")
    suggestions = categorizer.suggest(slug)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["current_code"] == "540"
    assert s["suggested_code"] == "540"
    assert s["source"] == "payee_rule"
    assert s["agree"] is True


def test_suggest_flags_disagreement(tmp_path, monkeypatch):
    slug = _client(tmp_path, monkeypatch)
    _post_expense(slug, "AWS", "590", desc="AWS cloud")   # miscategorized as Misc
    categorizer.add_rule(slug, pattern="aws", account_code="540")
    suggestions = categorizer.suggest(slug)
    s = suggestions[0]
    assert s["current_code"] == "590"
    assert s["suggested_code"] == "540"
    assert s["agree"] is False
    assert s["confidence"] == 0.95
