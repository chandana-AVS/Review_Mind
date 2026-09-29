"""The selected Hindsight bank reaches every recall and reflect call. Offline (fake Hindsight)."""

import importlib.util
import json
from pathlib import Path

from app.config import get_settings
from app.hindsight import ReflectResult
from app.review.agent import ReviewAgent
from app.review.diff import load_pr

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_pr.py"
spec = importlib.util.spec_from_file_location("review_pr", SCRIPT)
review_pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_pr)


class FakeHS:
    def __init__(self):
        self.calls = []

    def recall_with_sources(self, bank, query, **kw):
        self.calls.append(("recall", bank))
        return [], {}

    def reflect(self, bank, query, **kw):
        self.calls.append(("reflect", bank))
        return ReflectResult(text=json.dumps({"team_brief": [], "findings": [], "suppressed": []}),
                             structured=None, structured_error=None, based_on=None)


def test_review__custom_bank__used_for_every_recall_and_reflect():
    hs = FakeHS()
    result = ReviewAgent(hs=hs, bank="reviewmind-acme-payments-demo").review(load_pr(137))

    assert result.bank == "reviewmind-acme-payments-demo"
    assert any(op == "recall" for op, _ in hs.calls) and any(op == "reflect" for op, _ in hs.calls)
    assert {bank for _, bank in hs.calls} == {"reviewmind-acme-payments-demo"}


def test_review__no_bank__defaults_to_baseline_team_bank():
    hs = FakeHS()
    ReviewAgent(hs=hs).review(load_pr(137))
    assert {bank for _, bank in hs.calls} == {get_settings().team_bank_id}


def test_cli__bank_flag__parsed_and_default_unchanged():
    assert review_pr.parse_args(["--pr", "137", "--bank", "reviewmind-acme-payments-demo"]).bank == \
        "reviewmind-acme-payments-demo"
    default = review_pr.parse_args(["137"])
    assert default.pr == 137 and default.bank == "reviewmind-acme-payments"


def test_cli__output_file__demo_bank_does_not_overwrite_baseline_review():
    base = review_pr.output_path(137, True, "reviewmind-acme-payments")
    demo = review_pr.output_path(137, True, "reviewmind-acme-payments-demo")
    assert base.name == "pr-137-memory.json"
    assert demo.name == "pr-137-memory-reviewmind-acme-payments-demo.json"
