"""ReviewMind review agent (minimum working version).

Flow for one PR:
  1. inspect changed code (numbered diff) + repository map
  2. detect code signals -> targeted Hindsight RECALL (facts + observations, with provenance)
  3. one Hindsight REFLECT with the diff + labelled memories as context and a JSON schema:
     team brief, findings, and suppressed suggestions
  4. validate: every team-specific finding must cite recalled memory; otherwise it is dropped
  5. attach evidence (memory text/type/id) and sources (PR #, ADR, CONTRIBUTING)

Reviewing never writes to memory. Feedback (Phase 5) is the only write path.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.hindsight import HindsightService, Memory
from app.review.diff import PullRequest, detect_signals, recall_probes, repository_map

CLASSIFICATIONS = ["NEW", "TEAM_CONVENTION", "HISTORICAL_PATTERN", "REPEATED_ISSUE",
                   "PREVIOUSLY_REJECTED", "ARCHITECTURAL_DECISION"]
TEAM_CLASSES = set(CLASSIFICATIONS) - {"NEW"}
GENERIC_BANK = "reviewmind-generic-empty"  # no memories: same reviewer, no team knowledge

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "team_brief": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "rule": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            }, "required": ["rule", "evidence"]},
        },
        "findings": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "title": {"type": "string"},
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "severity": {"type": "string", "enum": ["high", "medium", "low", "nit"]},
                "category": {"type": "string"},
                "classification": {"type": "string", "enum": CLASSIFICATIONS},
                "explanation": {"type": "string"},
                "recommendation": {"type": "string"},
                "confidence": {"type": "number"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            }, "required": ["title", "file", "line", "severity", "category", "classification",
                            "explanation", "recommendation", "confidence", "evidence"]},
        },
        "suppressed": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "suggestion": {"type": "string"},
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "reason": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            }, "required": ["suggestion", "file", "line", "reason", "evidence"]},
        },
    },
    "required": ["team_brief", "findings", "suppressed"],
}

INSTRUCTIONS = """You are reviewing a pull request. Review ONLY lines marked "+" in the diff below.

First read the changed logic line by line for real bugs: wrong variable or amount used,
off-by-one errors, missing checks, wrong units. Then apply the team memories.

Report two kinds of findings:
- Generic code-quality findings (bugs, logic errors, security): classification NEW, evidence [].
- Team-specific findings: ONLY when one of the labelled team memories [M#] below supports it.
  Put the supporting labels in "evidence". Classify as:
  TEAM_CONVENTION (a rule the team enforces), ARCHITECTURAL_DECISION (an ADR),
  HISTORICAL_PATTERN (same situation discussed in a past review),
  REPEATED_ISSUE (a mistake the team corrected several times before).
Never claim a team rule that is not in the memories.

Suppression: check every memory about a suggestion the team REJECTED (or that contradicts their
infrastructure, or a documented exception). If that kind of suggestion would apply to this diff,
do NOT report it as a finding; list it under "suppressed" with the file/line where it would have
applied, the team's reason, and the evidence labels. Only suppress what the memories support.

"team_brief": the team rules from the memories that apply to this change, each with evidence.
Use exact file paths and the line numbers shown. Be concise and specific."""


OUTPUT_FORMAT = """Answer with ONLY one JSON object (no prose, no markdown fences):
{"team_brief": [{"rule": str, "evidence": ["M1", ...]}],
 "findings": [{"title": str, "file": str, "line": int, "severity": "high"|"medium"|"low"|"nit",
               "category": str, "classification": one of """ + "|".join(CLASSIFICATIONS) + """,
               "explanation": str (max 2 sentences), "recommendation": str (max 2 sentences),
               "confidence": number 0-1, "evidence": ["M1", ...]}],
 "suppressed": [{"suggestion": str, "file": str, "line": int, "reason": str (1 sentence),
                 "evidence": ["M1", ...]}]}"""


def parse_review_json(text: str) -> tuple[dict, str | None]:
    """Extract the JSON object from the reflect answer. Returns (data, error)."""
    empty = {"team_brief": [], "findings": [], "suppressed": []}
    if not text:
        return empty, "empty answer"
    candidate = text.strip()
    if fence := re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL):
        candidate = fence.group(1)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        return empty, "no JSON object in answer"
    try:
        data = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as exc:
        return empty, f"invalid JSON: {exc}"
    return {**empty, **{k: v for k, v in data.items() if k in empty and isinstance(v, list)}}, None


@dataclass
class Evidence:
    label: str
    memory_id: str
    type: str | None
    text: str
    document_id: str | None
    sources: list[str]


@dataclass
class Finding:
    title: str
    file: str
    line: int | None
    severity: str
    category: str
    classification: str
    basis: str  # "hindsight_memory" | "generic"
    explanation: str
    recommendation: str
    confidence: float
    memory_evidence: list[Evidence] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    pr: int
    title: str
    bank: str
    use_memory: bool
    created_at: str
    seconds: dict[str, float]
    signals: dict[str, list[str]]
    recall_probes: list[str]
    memories_recalled: int
    team_brief: list[dict]
    findings: list[Finding]
    suppressed: list[dict]
    dropped_unsupported: list[dict]
    reflect_based_on: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def source_of(document_id: str | None) -> str | None:
    if not document_id:
        return None
    if m := re.fullmatch(r"pr:[\w-]+:(\d+)", document_id):
        return f"PR #{m.group(1)}"
    if m := re.fullmatch(r"adr:[\w-]+:(\d+)", document_id):
        return f"ADR-{m.group(1)}"
    if document_id.startswith("contributing:"):
        return "CONTRIBUTING.md"
    return document_id


class ReviewAgent:
    def __init__(self, hs: HindsightService | None = None, bank: str | None = None) -> None:
        self.hs = hs or HindsightService()
        self.bank = bank or get_settings().team_bank_id

    # 2. recall ---------------------------------------------------------------------------
    def recall(self, pr: PullRequest) -> tuple[list[tuple[str, Memory, list[str]]], list[str]]:
        """Returns [(label, memory, sources)], probe names. Deduplicated across probes."""
        probes = recall_probes(pr.files)
        seen: dict[str, tuple[Memory, list[str]]] = {}
        for _, query in probes:
            mems, source_facts = self.hs.recall_with_sources(
                self.bank, query, max_tokens=1200, budget="low", query_timestamp=pr.created_at)
            for m in mems[:5]:
                if m.id in seen:
                    continue
                docs = [m.document_id] if m.document_id else [
                    source_facts[s].document_id for s in m.source_fact_ids if s in source_facts]
                srcs = sorted({s for s in (source_of(d) for d in docs) if s})
                seen[m.id] = (m, srcs)
        labelled = [(f"M{i}", m, srcs) for i, (m, srcs) in enumerate(seen.values(), 1)]
        return labelled, [name for name, _ in probes]

    # 3-5. reflect + validate ----------------------------------------------------------------
    def review(self, pr: PullRequest, *, use_memory: bool = True) -> ReviewResult:
        t0 = time.monotonic()
        timings: dict[str, float] = {}
        labelled: list[tuple[str, Memory, list[str]]] = []
        probes: list[str] = []
        if use_memory:
            labelled, probes = self.recall(pr)
        timings["recall_s"] = round(time.monotonic() - t0, 1)

        memory_block = "\n".join(
            f"[{label}] ({m.type}; {', '.join(srcs) or 'no source'}) {m.text}" for label, m, srcs in labelled
        ) or "(no team memories available)"
        context = "\n\n".join([
            INSTRUCTIONS,
            OUTPUT_FORMAT,
            f"PULL REQUEST #{pr.number}: {pr.title} (by {pr.author})\n{' '.join(pr.body.split())}",
            "REPOSITORY MAP (existing modules and helpers):\n" + repository_map(),
            "TEAM MEMORIES:\n" + memory_block,
            "DIFF:\n" + "\n\n".join(f.render() for f in pr.files),
        ])
        bank = self.bank if use_memory else GENERIC_BANK
        if not use_memory:
            self.hs.ensure_bank(GENERIC_BANK, name="ReviewMind generic (no memory)")
        t1 = time.monotonic()
        # The answer itself is the JSON (no separate structured-output pass: it was slow and
        # truncated long outputs with the claude-code provider).
        result = self.hs.reflect(
            bank, f"Review pull request #{pr.number}. Answer with only the JSON object described.",
            context=context, budget="low", include_facts=True, max_tokens=8000,
        )
        timings["reflect_s"] = round(time.monotonic() - t1, 1)
        out, parse_error = parse_review_json(result.text)
        if parse_error:
            out["error"] = parse_error
            out["raw_answer"] = result.text

        by_label = {label: (m, srcs) for label, m, srcs in labelled}

        def evidence(labels: list[str]) -> list[Evidence]:
            ev = []
            for lb in labels:
                lb = lb.strip("[] ")
                if lb in by_label:
                    m, srcs = by_label[lb]
                    ev.append(Evidence(lb, m.id, m.type, m.text, m.document_id, srcs))
            return ev

        findings, dropped = [], []
        for f in out.get("findings", []):
            ev = evidence(f.get("evidence", []))
            cls = f.get("classification", "NEW")
            if cls in TEAM_CLASSES and not ev:
                dropped.append({**f, "reason": "team-specific claim without recalled memory evidence"})
                continue
            if cls == "PREVIOUSLY_REJECTED":  # the team rejected this kind of suggestion: never surface it
                out.setdefault("suppressed", []).append({
                    "suggestion": f.get("title"), "file": f.get("file"), "line": f.get("line"),
                    "reason": f.get("explanation"), "evidence": f.get("evidence", [])})
                continue
            team = cls in TEAM_CLASSES
            findings.append(Finding(
                title=f["title"], file=f["file"], line=f.get("line"), severity=f["severity"],
                category=f["category"], classification=cls if team else "NEW",
                basis="hindsight_memory" if team else "generic",
                explanation=f["explanation"], recommendation=f["recommendation"],
                confidence=float(f.get("confidence", 0.5)),
                memory_evidence=ev if team else [],
                sources=sorted({s for e in ev for s in e.sources}) if team else [],
            ))
        suppressed = []
        for s in out.get("suppressed", []):
            ev = evidence(s.get("evidence", []))
            suppressed.append({**s, "classification": "PREVIOUSLY_REJECTED",
                               "memory_evidence": [asdict(e) for e in ev],
                               "sources": sorted({x for e in ev for x in e.sources})})
        brief = [{"rule": b["rule"], "evidence": [asdict(e) for e in evidence(b.get("evidence", []))]}
                 for b in out.get("team_brief", [])]
        based_on = []
        if isinstance(result.based_on, dict):
            based_on = [str(m.get("id")) for m in (result.based_on.get("memories") or [])]

        timings["total_s"] = round(time.monotonic() - t0, 1)
        return ReviewResult(
            pr=pr.number, title=pr.title, bank=bank, use_memory=use_memory,
            created_at=datetime.now(UTC).isoformat(), seconds=timings,
            signals=detect_signals(pr.files), recall_probes=probes, memories_recalled=len(labelled),
            team_brief=brief, findings=findings, suppressed=suppressed, dropped_unsupported=dropped,
            reflect_based_on=based_on,
        )
