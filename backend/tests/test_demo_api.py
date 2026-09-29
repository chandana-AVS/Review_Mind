"""Demo API tests. Offline: Hindsight is replaced by a fake; saved review files are real."""

from fastapi.testclient import TestClient

from app.api import demo
from app.main import app


class FakeHS:
    def __init__(self):
        self.retained = []

    def bank_exists(self, bank):
        return True

    def retain(self, bank, content, **kw):
        self.retained.append({"bank": bank, **kw})
        return {"success": True}

    def list_memories(self, bank, limit=100):
        return []


def client(monkeypatch, tmp_path):
    fake = FakeHS()
    monkeypatch.setattr(demo, "hs", lambda: fake)
    monkeypatch.setattr("app.feedback.store.BACKEND_DIR", tmp_path)  # isolated feedback state file
    return TestClient(app), fake


def test_pr_and_saved_reviews__served_from_real_files(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    pr = c.get("/api/demo/pr/137").json()
    assert pr["title"] == "Add refund processing fee"
    assert any(f["path"] == "app/services/refund_fees.py" for f in pr["files"])
    reviews = c.get("/api/demo/pr/137/reviews").json()
    assert reviews["baseline"]["findings"][0]["id"] == "F1"
    assert reviews["baseline"]["bank"] == "reviewmind-acme-payments"


def test_feedback__goes_through_feedback_store_to_demo_bank_only(monkeypatch, tmp_path):
    c, fake = client(monkeypatch, tmp_path)
    res = c.post("/api/demo/pr/137/feedback", json={
        "run": "baseline", "finding_id": "F1", "decision": "REJECTED",
        "reason": "Approved exception for this integration.", "developer": "erin"}).json()
    assert res["status"] == "retained" and res["bank"] == "reviewmind-acme-payments-demo"
    assert res["document_id"] == "feedback:payments-service:pr137:F1"
    assert [r["bank"] for r in fake.retained] == ["reviewmind-acme-payments-demo"]


def test_feedback__rejection_without_reason__422(monkeypatch, tmp_path):
    c, fake = client(monkeypatch, tmp_path)
    r = c.post("/api/demo/pr/137/feedback", json={"finding_id": "F1", "decision": "REJECTED", "reason": ""})
    assert r.status_code == 422 and not fake.retained


def test_prs__lists_held_out_prs_only_with_saved_run_flags(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    prs = c.get("/api/demo/prs").json()
    numbers = [p["number"] for p in prs]
    assert numbers == list(range(131, 145))  # never history PRs
    by_n = {p["number"]: p for p in prs}
    assert set(by_n[137]["saved_runs"]) == {"baseline", "latest"}
    assert by_n[131]["split"] == "feedback" and by_n[140]["split"] == "test"


def test_analyze__another_dataset_pr__real_files_and_patterns(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    a = c.post("/api/demo/analyze", json={"pr": 139}).json()
    assert "app/clients/card_network.py" in [f["path"] for f in a["files"]]
    assert "outbound_http" in a["signals"] and any(p["name"] == "retry" for p in a["probes"])


def test_analyze__custom_code__plain_code_and_unified_diff(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    plain = c.post("/api/demo/analyze", json={"file_name": "app/x.py",
                                              "code": "import httpx\nx = datetime.utcnow()\n"}).json()
    assert plain["files"][0]["path"] == "app/x.py" and plain["files"][0]["added"] == 2
    assert set(plain["signals"]) >= {"outbound_http", "time"}
    diff = c.post("/api/demo/analyze", json={"file_name": "app/y.py",
                                             "code": "@@ -1,1 +1,2 @@\n import os\n+v = os.environ['X']\n"}).json()
    assert diff["signals"] == {"config": ["app/y.py:2"]}


def test_analyze__empty_custom_code__422(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    assert c.post("/api/demo/analyze", json={"code": "  "}).status_code == 422


def test_memory_recall__uses_review_agent_recall_on_demo_bank(monkeypatch, tmp_path):
    from app.hindsight import Memory
    c, fake = client(monkeypatch, tmp_path)
    calls = []

    def recall_with_sources(bank, query, **kw):
        calls.append(bank)
        return [Memory(id="m1", text="Use make_client()", type="world",
                       document_id="adr:payments-service:006")], {}
    fake.recall_with_sources = recall_with_sources
    r = c.post("/api/demo/memory-recall", json={"file_name": "a.py", "code": "import httpx\n"}).json()
    assert set(calls) == {"reviewmind-acme-payments-demo"} and len(calls) == 2  # http + retry probes
    assert r["memories"] == [{"label": "M1", "id": "m1", "type": "world", "text": "Use make_client()",
                              "document_id": "adr:payments-service:006", "sources": ["ADR-006"]}]


def test_ui__index_served(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, tmp_path)
    assert "<title>ReviewMind</title>" in c.get("/").text
