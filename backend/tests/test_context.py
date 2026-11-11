from datetime import UTC, datetime

from app.ai.context import build_context_block, build_messages, transcript


def test_messages_start_with_user_alternate_and_end_with_current():
    history = [
        {"role": "assistant", "content": "greeting"},
        {"role": "user", "content": "first"},
        {"role": "user", "content": "retry of a failed one"},
        {"role": "assistant", "content": "reply"},
    ]
    msgs = build_messages(history, "now")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "first\n\nretry of a failed one"
    assert msgs[-1]["content"] == "now"


def test_current_message_merges_into_trailing_user_turn():
    msgs = build_messages([{"role": "user", "content": "unanswered"}], "again")
    assert msgs == [{"role": "user", "content": "unanswered\n\nagain"}]


def test_context_block_contents():
    block = build_context_block(
        traits=[
            {"category": "slang", "key": "slang.ngl", "value": "Says ngl", "source": "inferred",
             "confidence": 0.62, "evidence_count": 3},
            {"category": "interests", "key": "interests.bouldering", "value": "Loves <b>bouldering</b>",
             "source": "user_stated", "confidence": 1.0, "evidence_count": 1},
        ],
        rejected_keys=["humour.sarcasm"],
        summary="- Has a job interview Friday",
        examples=[{"text": "omw!! 🏃"}],
        now=datetime(2026, 11, 11, 3, 0, tzinfo=UTC),
        timezone="Asia/Kuala_Lumpur",
    )
    assert "<now>Wednesday 2026-11-11 11:00 (Asia/Kuala_Lumpur)</now>" in block
    assert "(tentative 62%, seen 3x) key=slang.ngl" in block
    assert "Loves ‹b›bouldering‹/b›" in block  # user text can't inject tags
    assert "<rejected_keys>humour.sarcasm</rejected_keys>" in block
    assert "job interview" in block and "omw!!" in block


def test_empty_profile():
    block = build_context_block(traits=[], rejected_keys=[], summary=None, examples=[],
                                now=datetime.now(UTC), timezone="Asia/Kuala_Lumpur")
    assert "(nothing learned yet)" in block and "rejected_keys" not in block


def test_transcript():
    assert transcript([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]) == "User: hi\nECHO: yo"
