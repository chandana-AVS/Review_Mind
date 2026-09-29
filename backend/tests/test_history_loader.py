"""Leak guard + narration tests. Offline."""

import json

import pytest

from app.ingestion import history
from app.ingestion.history import LeakError, assert_history, load_history_pr, load_history_prs


@pytest.mark.parametrize("number", [131, 136, 137, 144])
def test_load_history_pr__held_out_pr__raises_leak_error(number):
    with pytest.raises(LeakError):
        load_history_pr(number)


@pytest.mark.parametrize("number", [131, 140, 999, 100])
def test_load_history_prs__any_number_outside_history__raises_before_loading(number):
    with pytest.raises(LeakError):
        load_history_prs([101, number])


def test_assert_history__history_number_but_wrong_split__raises():
    with pytest.raises(LeakError):
        assert_history(113, "test")


def test_load_history_prs__all__returns_exactly_the_30_history_prs():
    docs = load_history_prs()
    assert [d.number for d in docs] == list(range(101, 131))
    assert all(d.metadata["split"] == "history" for d in docs)
    assert all(d.document_id == f"pr:payments-service:{d.number}" for d in docs)


def test_every_held_out_pr_file_is_marked_non_history():
    for n in range(131, 145):
        pr = json.loads((history.DEMO_DATA / "pull-requests" / "payments-service" / f"pr-{n}.json").read_text())
        assert pr["split"] in ("feedback", "test")


def test_narration__pr_113__has_provenance_outcomes_and_reasons():
    doc = load_history_pr(113)

    assert doc.metadata["pr_number"] == "113"
    assert doc.metadata["thread_ids"] == "t113-1,t113-2"  # ci-bot noise thread dropped
    assert doc.metadata["outcomes"] == "t113-1=applied,t113-2=rejected"
    assert {"team:acme-payments", "repo:payments-service", "pr:113", "source:pr_history"} <= set(doc.tags)
    assert "Rejection reason: make_client() already retries" in doc.content
    assert "REJECTED" in doc.content and "APPLIED" in doc.content
    assert "Coverage report" not in doc.content


def test_narration__never_contains_answer_key_ids():
    for doc in load_history_prs():
        for token in (" C1 ", " N4 ", " M1 ", "should_flag", "planted"):
            assert token not in doc.content


def test_content_hash__is_stable_across_loads():
    assert load_history_pr(101).content_hash == load_history_pr(101).content_hash
