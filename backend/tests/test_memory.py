from app.domain.memory import ProposedUpdate, TraitState, contains_pii, merge, sanitize


def obs(key="slang.ngl", value="Says ngl", conf=0.5, category="slang"):
    return ProposedUpdate("set", category, key, value, "observation", conf)


def fact(key="interests.bouldering", value="Loves bouldering"):
    return ProposedUpdate("set", "interests", key, value, "stated_fact", 1.0)


def state(**kw):
    base = dict(category="slang", key="slang.ngl", value="Says ngl", source="inferred",
                confidence=0.5, evidence_count=1, status="active")
    base.update(kw)
    return TraitState(**base)


def test_new_observation_is_capped_and_tentative():
    s = merge(None, obs(conf=0.99))
    assert s.source == "inferred" and s.confidence == 0.6 and s.evidence_count == 1


def test_repeated_observation_approaches_but_never_reaches_certainty():
    s = merge(None, obs())
    for _ in range(40):
        s = merge(s, obs())
    assert s.confidence <= 0.95 and s.source == "inferred" and s.evidence_count == 41


def test_stated_fact_is_a_fact():
    s = merge(None, fact())
    assert s.source == "user_stated" and s.confidence == 1.0


def test_observation_does_not_downgrade_a_stated_fact():
    s = merge(state(source="user_stated", confidence=1.0), obs())
    assert s.source == "user_stated" and s.confidence == 1.0 and s.evidence_count == 2


def test_user_edits_win():
    edited = state(source="user_edited", confidence=1.0, value="Only with close friends")
    s = merge(edited, fact(key="slang.ngl", value="Says ngl all the time"))
    assert s.value == "Only with close friends" and s.source == "user_edited"


def test_rejected_traits_are_never_relearned():
    assert merge(state(status="rejected", value=""), obs()) is None
    assert merge(state(status="rejected", value=""), fact(key="slang.ngl")) is None


def test_remove_archives_and_can_return_later():
    archived = merge(state(category="interests", key="interests.anime"),
                     ProposedUpdate("remove", "interests", "interests.anime", "", "stated_fact", 1.0))
    assert archived.status == "archived"
    back = merge(archived, fact(key="interests.anime", value="Loves anime"))
    assert back.status == "active" and back.source == "user_stated"


def test_remove_of_unknown_trait_is_noop():
    assert merge(None, ProposedUpdate("remove", "interests", "interests.x", "", "stated_fact", 1.0)) is None


def test_sanitize_normalises_and_drops_bad_items():
    raw = [
        {"op": "SET", "category": "Slang", "key": "NGL usage!", "value": "  Says   ngl ", "kind": "Observation",
         "confidence": 3},
        {"op": "set", "category": "astrology", "key": "x", "value": "y", "kind": "observation", "confidence": 0.5},
        {"op": "set", "category": "interests", "key": "interests.mail", "value": "email me at a@b.co",
         "kind": "stated_fact", "confidence": 1},
        {"op": "set", "category": "interests", "key": "interests.phone", "value": "call +60 12-345 6789",
         "kind": "stated_fact", "confidence": 1},
        {"category": "tone"},  # missing key
        {"op": "set", "category": "slang", "key": "slang.ngl_usage", "value": "dup", "kind": "observation",
         "confidence": 0.5},
    ]
    clean = sanitize(raw)
    assert len(clean) == 1
    u = clean[0]
    assert (u.op, u.category, u.key, u.value, u.kind, u.confidence) == (
        "set", "slang", "slang.ngl_usage", "Says ngl", "observation", 1.0)


def test_sanitize_limits_updates_per_turn():
    raw = [{"op": "set", "category": "interests", "key": f"interests.k{i}", "value": f"v{i}",
            "kind": "stated_fact", "confidence": 1} for i in range(20)]
    assert len(sanitize(raw)) == 6


def test_pii_detection():
    assert contains_pii("my number is 012 345 6789")
    assert contains_pii("password is hunter2")
    assert not contains_pii("Loves bouldering on weekends")
