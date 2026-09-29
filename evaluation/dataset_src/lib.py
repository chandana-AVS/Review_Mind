"""Tiny DSL for authoring the Acme Payments demo dataset.

Each `prs_*.py` module in this folder defines PRs with these helpers; `build.py` imports them all
and writes demo-data/ + evaluation/labels/. Patches, counts, SHAs and line numbers are computed,
never typed by hand.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRS_DIR = ROOT / "demo-data" / "pull-requests" / "payments-service"
REVIEWS_DIR = ROOT / "demo-data" / "reviews" / "payments-service"
LABELS_DIR = ROOT / "evaluation" / "labels"

REGISTRY: list["PR"] = []


def sha(*parts: object) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def ts(value: str) -> str:
    """'2026-03-16 14:55' -> '2026-03-16T14:55:00Z'."""
    value = value.strip().replace(" ", "T")
    if len(value) == 16:
        value += ":00"
    return value if value.endswith("Z") else value + "Z"


def dedent(text: str) -> str:
    return textwrap.dedent(text).strip("\n") + "\n"


def prose(text: str) -> str:
    """Dedent and unwrap hard-wrapped paragraphs (lists, quotes and code blocks keep their lines)."""
    paragraphs = re.split(r"\n\s*\n", textwrap.dedent(text).strip())
    out = []
    for p in paragraphs:
        lines = p.split("\n")
        keep = any(re.match(r"^\s*([-*>]|\d+\.|```|    )", l) for l in lines)
        out.append(p if keep else " ".join(l.strip() for l in lines))
    return "\n\n".join(out)


@dataclass
class File:
    path: str
    status: str
    patch: str
    added: dict[int, str] = field(default_factory=dict)    # new line no -> text
    visible: dict[int, str] = field(default_factory=dict)  # new line no -> text

    @property
    def additions(self) -> int:
        return sum(1 for l in self.patch.split("\n") if l.startswith("+"))

    @property
    def deletions(self) -> int:
        return sum(1 for l in self.patch.split("\n") if l.startswith("-"))

    def line(self, marker: str, *, added_only: bool = True, nth: int = 1) -> int:
        pool = self.added if added_only else self.visible
        hits = [n for n, text in sorted(pool.items()) if marker in text]
        if len(hits) < nth:
            raise ValueError(f"{self.path}: marker {marker!r} not found (added_only={added_only})")
        return hits[nth - 1]

    def span(self, start_marker: str, end_marker: str | None = None) -> list[int]:
        a = self.line(start_marker)
        b = self.line(end_marker) if end_marker else a
        if b < a:
            raise ValueError(f"{self.path}: span end before start")
        missing = [n for n in range(a, b + 1) if n not in self.added]
        if missing:
            raise ValueError(f"{self.path}: span {a}-{b} contains non-added lines {missing}")
        return [a, b]


def _index(patch: str) -> tuple[dict[int, str], dict[int, str]]:
    added: dict[int, str] = {}
    visible: dict[int, str] = {}
    new_line = 0
    for line in patch.split("\n"):
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            new_line = int(m.group(1))
            continue
        if line.startswith("+"):
            added[new_line] = line[1:]
            visible[new_line] = line[1:]
            new_line += 1
        elif line.startswith(" "):
            visible[new_line] = line[1:]
            new_line += 1
    return added, visible


def new_file(path: str, content: str) -> File:
    lines = dedent(content).rstrip("\n").split("\n")
    patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines)
    added, visible = _index(patch)
    return File(path, "added", patch, added, visible)


def modified(path: str, before: str, after: str, start: int = 1, context: int = 3) -> File:
    """`before`/`after` are an excerpt of the file beginning at line `start` (same in old and new)."""
    old = dedent(before).rstrip("\n").split("\n")
    new = dedent(after).rstrip("\n").split("\n")
    diff = list(difflib.unified_diff(old, new, lineterm="", n=context))[2:]
    out = []
    for line in diff:
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            o_start = int(m.group(1)) + start - 1
            n_start = int(m.group(3)) + start - 1
            o_len = m.group(2) if m.group(2) is not None else "1"
            n_len = m.group(4) if m.group(4) is not None else "1"
            out.append(f"@@ -{o_start},{o_len} +{n_start},{n_len} @@")
        else:
            out.append(line if line else " ")
    patch = "\n".join(out)
    if not patch:
        raise ValueError(f"{path}: before and after are identical")
    added, visible = _index(patch)
    return File(path, "modified", patch, added, visible)


@dataclass
class Thread:
    path: str | None
    line: int | None
    comments: list[tuple[str, str, str]]
    resolution: str
    decided_by: str | None = None
    reason: str | None = None
    label: dict = field(default_factory=dict)


def thread(file: File | None, marker: str | None, comments, resolution, decided_by=None,
           reason=None, *, visible=False, **label) -> Thread:
    line = file.line(marker, added_only=not visible) if file and marker else None
    return Thread(file.path if file else None, line, comments, resolution, decided_by, reason, label)


def c(author: str, when: str, body: str) -> tuple[str, str, str]:
    return (author, when, prose(body))


def label(convention=None, noise=None, mistake=None, exception=None, conflict=None,
          signal=True, strength="enforced") -> dict:
    return {"convention": convention, "noise": noise, "mistake": mistake, "exception": exception,
            "conflict": conflict, "is_convention_signal": signal, "expected_strength": strength}


FILLER = dict(convention=None, noise=None, mistake=None, exception=None, conflict=None,
              is_convention_signal=False, expected_strength=None)


@dataclass
class PR:
    number: int
    title: str
    body: str
    author: str
    created: str
    files: list[File]
    merged: str | None = None
    state: str = "merged"
    labels: list[str] = field(default_factory=list)
    adr: str | None = None
    reviews: list[tuple[str, str, str, str]] = field(default_factory=list)  # reviewer, state, when, body
    threads: list[Thread] = field(default_factory=list)
    heldout: dict | None = None  # {"should_flag": [...], "should_not_flag": [...], "drift_trap":..., "demo":...}

    def f(self, path: str) -> File:
        return next(x for x in self.files if x.path == path)


def register(pr: PR) -> PR:
    REGISTRY.append(pr)
    return pr


def flag(fid, kind, file: File, start, end=None, *, convention=None, rationale, fix):
    return {"id": fid, "kind": kind, "convention": convention, "path": file.path,
            "line_range": file.span(start, end), "rationale": rationale, "expected_fix": fix}


def trap(fid, file: File, start, end=None, *, noise=None, exception=None, trap, reject):
    return {"id": fid, "noise": noise, "exception": exception, "path": file.path,
            "line_range": file.span(start, end), "trap": trap, "reject_reason": reject}


def split_of(n: int) -> str:
    return "history" if n <= 130 else "feedback" if n <= 136 else "test"


def write_all() -> None:
    for d in (PRS_DIR, REVIEWS_DIR, *(LABELS_DIR / s for s in ("history", "feedback", "test"))):
        d.mkdir(parents=True, exist_ok=True)
    for pr in REGISTRY:
        split = split_of(pr.number)
        doc = {
            "number": pr.number, "title": pr.title, "body": prose(pr.body),
            "author": pr.author, "created_at": ts(pr.created),
            "merged_at": ts(pr.merged) if pr.merged else None,
            "state": pr.state if split == "history" else "open", "split": split,
            "labels": pr.labels, "base_sha": sha(pr.number, "base"), "head_sha": sha(pr.number, "head"),
            "adr": pr.adr,
            "files": [{"path": f.path, "status": f.status, "additions": f.additions,
                       "deletions": f.deletions, "patch": f.patch} for f in pr.files],
        }
        _dump(PRS_DIR / f"pr-{pr.number}.json", doc)
        if split == "history":
            threads, labels = [], {}
            for i, t in enumerate(pr.threads, 1):
                tid = f"t{pr.number}-{i}"
                threads.append({
                    "id": tid, "path": t.path, "line": t.line,
                    "comments": [{"author": a, "created_at": ts(w), "body": b} for a, w, b in t.comments],
                    "resolution": t.resolution, "decided_by": t.decided_by,
                    "rejection_reason": t.reason if t.resolution == "rejected" else None,
                    "follow_up_commit": sha(pr.number, tid, "fix") if t.resolution == "applied" else None,
                })
                labels[tid] = t.label or dict(FILLER)
            _dump(REVIEWS_DIR / f"pr-{pr.number}.json", {
                "pr": pr.number,
                "reviews": [{"reviewer": r, "state": s, "submitted_at": ts(w), "body": prose(b)}
                            for r, s, w, b in pr.reviews],
                "threads": threads,
            })
            _dump(LABELS_DIR / "history" / f"pr-{pr.number}.json", {"pr": pr.number, "threads": labels})
        else:
            h = pr.heldout or {}
            _dump(LABELS_DIR / split / f"pr-{pr.number}.json", {
                "pr": pr.number, "split": split, "demo": h.get("demo", False),
                "should_flag": h.get("should_flag", []),
                "should_not_flag": h.get("should_not_flag", []),
                "drift_trap": h.get("drift_trap"),
            })


def _dump(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
