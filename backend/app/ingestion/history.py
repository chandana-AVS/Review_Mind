"""Load historical PRs and narrate each one as a single Hindsight document.

Hard leak guard: only `split == "history"` PRs #101-#130 can ever leave this module.
Feedback (#131-#136) and test (#137-#144) PRs are rejected, and the answer keys in
evaluation/labels/ are never read.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_DATA = REPO_ROOT / "demo-data"
TEAM = "acme-payments"
TEAM_NAME = "Acme Payments"
REPO = "payments-service"
HISTORY_PRS = range(101, 131)  # the ONLY PR numbers that may be ingested
FIRST_HELD_OUT_PR = 131


class LeakError(RuntimeError):
    """A non-history PR (feedback/test) was about to be ingested."""


def assert_history(number: int, split: str | None) -> None:
    if split != "history" or number not in HISTORY_PRS or number >= FIRST_HELD_OUT_PR:
        raise LeakError(f"refusing to ingest PR #{number} (split={split!r}); only history PRs "
                        f"#{HISTORY_PRS.start}-#{HISTORY_PRS.stop - 1} may enter memory")


@dataclass(frozen=True)
class PRDocument:
    number: int
    document_id: str
    content: str
    context: str
    timestamp: datetime
    tags: list[str]
    metadata: dict[str, str]
    thread_ids: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        payload = json.dumps([self.content, self.context, self.tags, self.metadata,
                              self.timestamp.isoformat()], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


def load_history_pr(number: int, repo: str = REPO) -> PRDocument:
    pr_path = DEMO_DATA / "pull-requests" / repo / f"pr-{number}.json"
    pr = json.loads(pr_path.read_text(encoding="utf-8"))
    assert_history(pr.get("number", number), pr.get("split"))
    if pr["number"] != number:
        raise ValueError(f"{pr_path} has number {pr['number']}, expected {number}")
    review_path = DEMO_DATA / "reviews" / repo / f"pr-{number}.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    return narrate(pr, review, repo)


def load_history_prs(numbers: list[int] | None = None, repo: str = REPO) -> list[PRDocument]:
    wanted = list(numbers) if numbers is not None else list(HISTORY_PRS)
    for n in wanted:
        if n not in HISTORY_PRS:
            raise LeakError(f"PR #{n} is not a history PR; refusing to load it")
    docs = [load_history_pr(n, repo) for n in wanted]
    assert all(d.number < FIRST_HELD_OUT_PR for d in docs)
    return docs


# --- narration ------------------------------------------------------------------------------

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_OUTCOME = {
    "applied": "APPLIED: the author changed the code as the reviewer asked",
    "rejected": "REJECTED: the suggestion was not adopted",
    "discussed": "DISCUSSED: no code change was requested",
    "withdrawn": "WITHDRAWN by the reviewer",
}


def _new_file_lines(patch: str) -> dict[int, str]:
    lines: dict[int, str] = {}
    n = 0
    for raw in patch.split("\n"):
        m = _HUNK.match(raw)
        if m:
            n = int(m.group(1))
        elif raw.startswith("+") or raw.startswith(" "):
            lines[n] = raw[1:]
            n += 1
    return lines


def _area(path: str) -> str:
    parts = path.split("/")
    if parts[0] == "app" and len(parts) > 2:
        return parts[1]
    return parts[0].replace(".md", "").replace(".toml", "").lower()


def _date(ts: str) -> str:
    return ts[:10]


def narrate(pr: dict, review: dict, repo: str = REPO) -> PRDocument:
    number = pr["number"]
    assert_history(number, pr.get("split"))
    files = {f["path"]: f for f in pr["files"]}
    file_lines = {p: _new_file_lines(f["patch"]) for p, f in files.items()}

    out: list[str] = []
    merged = f", merged {_date(pr['merged_at'])}" if pr.get("merged_at") else f" ({pr['state']})"
    out.append(f"Historical code review from the {TEAM_NAME} team, repository {repo}.")
    out.append(f"Pull request #{number} \"{pr['title']}\" by {pr['author']}, opened "
               f"{_date(pr['created_at'])}{merged}.")
    if pr.get("adr"):
        out.append(f"This pull request introduced architecture decision record {pr['adr']}.")
    out.append("Description: " + " ".join(pr["body"].split()))
    out.append("Files changed: " + ", ".join(f"{p} ({f['status']})" for p, f in files.items()) + ".")

    thread_ids, outcomes, areas = [], [], {_area(p) for p in files}
    substantive = [t for t in review["threads"] if t["resolution"] != "noise"]
    if substantive:
        out.append("")
        out.append("Review discussion:")
    for i, t in enumerate(substantive, 1):
        thread_ids.append(t["id"])
        outcomes.append(t["resolution"])
        where = "general PR discussion"
        if t.get("path"):
            code = file_lines.get(t["path"], {}).get(t.get("line"), "").strip()
            where = f"{t['path']} line {t['line']}" + (f" (`{code}`)" if code else "")
        out.append(f"{i}. Thread {t['id']} on {where}:")
        for c in t["comments"]:
            out.append(f"   - {c['author']} on {_date(c['created_at'])}: {' '.join(c['body'].split())}")
        outcome = _OUTCOME.get(t["resolution"], t["resolution"].upper())
        decided = f", decided by {t['decided_by']}" if t.get("decided_by") else ""
        line = f"   Outcome: {outcome}{decided}."
        if t["resolution"] == "rejected" and t.get("rejection_reason"):
            line += f" Rejection reason: {t['rejection_reason']}"
        out.append(line)

    verdicts = [f"{r['reviewer']} {r['state'].replace('_', ' ')}" for r in review.get("reviews", [])]
    if verdicts:
        out.append("")
        out.append("Review verdicts: " + "; ".join(verdicts) + ".")

    applied = sum(o == "applied" for o in outcomes)
    rejected = sum(o == "rejected" for o in outcomes)
    tags = sorted({f"team:{TEAM}", f"repo:{repo}", "source:pr_history", f"pr:{number}",
                   f"author:{pr['author']}", *(f"area:{a}" for a in areas),
                   *(f"outcome:{o}" for o in outcomes),
                   *([f"adr:{pr['adr']}"] if pr.get("adr") else [])})
    metadata = {
        "team": TEAM,
        "repo": repo,
        "pr_number": str(number),
        "pr_title": pr["title"],
        "author": pr["author"],
        "created_at": pr["created_at"],
        "merged_at": pr.get("merged_at") or "",
        "state": pr["state"],
        "thread_ids": ",".join(thread_ids),
        "outcomes": ",".join(f"{tid}={o}" for tid, o in zip(thread_ids, outcomes)),
        "applied_count": str(applied),
        "rejected_count": str(rejected),
        "adr": pr.get("adr") or "",
        "source": "pr_history",
        "split": "history",
    }
    context = (f"Historical pull request review from the {TEAM_NAME} engineering team "
               f"({repo} PR #{number}). The speakers are human team members (authors and "
               f"reviewers), not ReviewMind. Record team conventions, architectural decisions, "
               f"accepted and rejected suggestions with their reasons.")
    return PRDocument(
        number=number,
        document_id=f"pr:{repo}:{number}",
        content="\n".join(out),
        context=context,
        timestamp=datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")),
        tags=tags,
        metadata=metadata,
        thread_ids=thread_ids,
    )
