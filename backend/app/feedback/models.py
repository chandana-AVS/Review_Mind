"""Developer feedback on a ReviewMind finding, narrated as ReviewMind's own experience."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.history import REPO, TEAM

DECISIONS = ("ACCEPTED", "REJECTED")

AGENT_CONTEXT = ("ReviewMind's own log of developer feedback on the review findings it produced. "
                 "The speaker is ReviewMind, this bank's agent. The developer's reason states the "
                 "team's position on this kind of finding.")


class FeedbackError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewFinding:
    """A finding as stored in a saved review file (evaluation/results/reviews/*.json)."""
    finding_id: str
    title: str
    file: str
    line: int | None
    severity: str
    category: str
    classification: str
    basis: str
    sources: list[str]


@dataclass(frozen=True)
class Feedback:
    pr_number: int
    pr_title: str
    finding: ReviewFinding
    decision: str
    reason: str
    developer: str
    submitted_at: str
    review_file: str
    review_created_at: str
    repo: str = REPO
    team: str = TEAM

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise FeedbackError(f"decision must be one of {DECISIONS}, got {self.decision!r}")
        if self.decision == "REJECTED" and not self.reason.strip():
            raise FeedbackError("a REJECTED decision needs a reason")

    @property
    def document_id(self) -> str:
        # One document per (PR, finding): changing your mind replaces the earlier feedback.
        return f"feedback:{self.repo}:pr{self.pr_number}:{self.finding.finding_id}"

    @property
    def content(self) -> str:
        f = self.finding
        where = f"{f.file} line {f.line}" if f.line else f.file
        basis = (f"It was a team-specific finding backed by memory ({', '.join(f.sources)})."
                 if f.basis == "hindsight_memory" else "It was a generic finding not backed by team memory.")
        verb = "rejected" if self.decision == "REJECTED" else "accepted"
        lines = [
            f"I (ReviewMind) reviewed pull request #{self.pr_number} \"{self.pr_title}\" in {self.repo} "
            f"and reported finding {f.finding_id} at {where}: \"{f.title}\" "
            f"(classification {f.classification}, category {f.category}, severity {f.severity}). {basis}",
            f"On {self.submitted_at[:10]} developer {self.developer} {verb} this finding.",
        ]
        if self.reason.strip():
            label = "Rejection reason" if self.decision == "REJECTED" else "Comment"
            lines.append(f"{label}: \"{self.reason.strip()}\"")
        if self.decision == "REJECTED":
            lines.append("I should not repeat this kind of suggestion in the same situation unless "
                         "the team's position changes.")
        return "\n".join(lines)

    @property
    def tags(self) -> list[str]:
        f = self.finding
        return sorted({f"team:{self.team}", f"repo:{self.repo}", "source:review_feedback",
                       f"pr:{self.pr_number}", f"finding:{f.finding_id}",
                       f"outcome:{self.decision.lower()}", f"classification:{f.classification.lower()}",
                       f"category:{re.sub(r'[^a-z0-9_-]+', '-', f.category.lower())}"})

    @property
    def metadata(self) -> dict[str, str]:
        f = self.finding
        return {
            "team": self.team, "repo": self.repo, "source": "review_feedback",
            "pr_number": str(self.pr_number), "finding_id": f.finding_id, "finding_title": f.title,
            "category": f.category, "classification": f.classification, "severity": f.severity,
            "finding_basis": f.basis, "finding_sources": ",".join(f.sources),
            "decision": self.decision, "reason": self.reason.strip(), "file": f.file,
            "line": "" if f.line is None else str(f.line), "developer": self.developer,
            "submitted_at": self.submitted_at, "review_file": self.review_file,
            "review_created_at": self.review_created_at,
        }

    @property
    def content_hash(self) -> str:
        # submitted_at is excluded so resubmitting identical feedback is a no-op.
        payload = {"content": self.content.replace(self.submitted_at[:10], ""),
                   "meta": {k: v for k, v in self.metadata.items() if k != "submitted_at"}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {**asdict(self), "document_id": self.document_id, "tags": self.tags}


def load_review(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def findings_of(review: dict) -> list[ReviewFinding]:
    """Findings in review order; ids are F1..Fn (or the id stored in the review, if present)."""
    out = []
    for i, f in enumerate(review.get("findings", []), 1):
        out.append(ReviewFinding(
            finding_id=f.get("id") or f"F{i}", title=f["title"], file=f["file"], line=f.get("line"),
            severity=f["severity"], category=f["category"], classification=f["classification"],
            basis=f.get("basis", "generic"), sources=list(f.get("sources", [])),
        ))
    return out


def build_feedback(review: dict, review_file: str, finding_id: str, decision: str, reason: str,
                   developer: str, now: datetime | None = None) -> Feedback:
    matches = [f for f in findings_of(review) if f.finding_id == finding_id]
    if not matches:
        ids = [f.finding_id for f in findings_of(review)]
        raise FeedbackError(f"finding {finding_id!r} not in review (available: {ids})")
    return Feedback(
        pr_number=int(review["pr"]), pr_title=review["title"], finding=matches[0],
        decision=decision.upper(), reason=reason, developer=developer,
        submitted_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        review_file=review_file, review_created_at=review.get("created_at", ""),
    )
