"""Inspect a PR's changed code: parse patches, number the lines, detect code signals.

Signals are generic code features (outbound HTTP, retries, time, money, config, errors,
logging, DB sessions, endpoints, tests, helpers). They only decide WHICH team memories to
recall; the reviewer decides what is actually a finding.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.history import DEMO_DATA, REPO

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class ChangedFile:
    path: str
    status: str
    lines: list[tuple[int, str, str]] = field(default_factory=list)  # (new_line_no, "+"/" ", text)

    @property
    def added(self) -> list[tuple[int, str]]:
        return [(n, t) for n, kind, t in self.lines if kind == "+"]

    def render(self) -> str:
        out = [f"FILE {self.path} ({self.status})"]
        for n, kind, text in self.lines:
            out.append(f"{n:>4} {kind} {text}")
        return "\n".join(out)


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    author: str
    created_at: str
    split: str
    files: list[ChangedFile]


def parse_patch(path: str, status: str, patch: str) -> ChangedFile:
    cf = ChangedFile(path, status)
    n = 0
    for raw in patch.split("\n"):
        m = _HUNK.match(raw)
        if m:
            n = int(m.group(1))
        elif raw.startswith("+"):
            cf.lines.append((n, "+", raw[1:]))
            n += 1
        elif raw.startswith(" "):
            cf.lines.append((n, " ", raw[1:]))
            n += 1
    return cf


def load_pr(number: int, repo: str = REPO) -> PullRequest:
    """Load a PR to REVIEW. Reviewing reads the diff only; it never writes to memory."""
    pr = json.loads((DEMO_DATA / "pull-requests" / repo / f"pr-{number}.json").read_text(encoding="utf-8"))
    return PullRequest(pr["number"], pr["title"], pr["body"], pr["author"], pr["created_at"], pr["split"],
                       [parse_patch(f["path"], f["status"], f["patch"]) for f in pr["files"]])


# --- code signals -> recall probes ------------------------------------------------------------

SIGNALS: list[tuple[str, re.Pattern, str]] = [
    ("outbound_http", re.compile(r"\bhttpx\.|\brequests\.|import requests|import httpx|urllib\.request"),
     "How must outbound HTTP calls and HTTP clients be created in payments-service?"),
    ("retry", re.compile(r"retry|backoff|tenacity|attempts?\b", re.I),
     "Should extra retry or backoff logic be added around client calls in payments-service?"),
    ("time", re.compile(r"datetime\.(utc)?now|time\.time\(|date\.today"),
     "How should code in payments-service get the current time?"),
    ("money", re.compile(r"Decimal|float\(|amount|_cents|fee|bps|currency", re.I),
     "How must money amounts and fees be represented and rounded in payments-service?"),
    ("config", re.compile(r"os\.environ|os\.getenv"),
     "How should configuration and environment variables be read in payments-service?"),
    ("errors", re.compile(r"HTTPException|raise |except "),
     "What errors should services raise, and where may HTTPException be used, in payments-service?"),
    ("logging", re.compile(r"log\.|logger\.|logging\.|print\("),
     "How should logging be written in payments-service, including customer data?"),
    ("db_session", re.compile(r"session_scope|Session\b|\.execute\("),
     "Where may database sessions and queries live in payments-service?"),
    ("endpoint", re.compile(r"@router\.(post|put|patch|delete)"),
     "What do new POST endpoints that move money need in payments-service?"),
    ("webhook_health", re.compile(r"webhook|healthz|/health", re.I),
     "What exceptions apply to webhook and health-check endpoints in payments-service?"),
    ("private_helper", re.compile(r"^\s*def _\w+\("),
     "Do private helper functions need docstrings in payments-service?"),
    ("migration", re.compile(r"op\.(drop_column|alter_column|add_column|rename)"),
     "What are the rules for database migrations in payments-service?"),
]
TEST_PROBE = "What are the conventions for tests in payments-service (naming, factories, type hints)?"
SCRIPT_PROBE = "What rules apply to one-off scripts under scripts/ in payments-service?"


def detect_signals(files: list[ChangedFile]) -> dict[str, list[str]]:
    """signal name -> ['path:line', ...] where it appears in ADDED lines."""
    hits: dict[str, list[str]] = {}
    for f in files:
        for n, text in f.added:
            for name, pattern, _ in SIGNALS:
                if pattern.search(text):
                    hits.setdefault(name, []).append(f"{f.path}:{n}")
    return hits


def recall_probes(files: list[ChangedFile]) -> list[tuple[str, str]]:
    """(probe name, query) pairs to send to Hindsight recall."""
    hits = detect_signals(files)
    if "outbound_http" in hits:
        hits.setdefault("retry", [])  # reviewers tend to suggest retries on any client call
    probes = [(name, q) for name, _, q in SIGNALS if name in hits]
    if any(f.path.startswith("tests/") for f in files):
        probes.append(("tests", TEST_PROBE))
    if any(f.path.startswith("scripts/") for f in files):
        probes.append(("scripts", SCRIPT_PROBE))
    return probes


# --- repository context -----------------------------------------------------------------------

def repository_map(repo_dir: Path | None = None) -> str:
    """Top-level functions/classes of the repo snapshot: generic context, not memory."""
    repo_dir = repo_dir or DEMO_DATA / "repositories" / REPO
    lines = []
    for path in sorted((repo_dir / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if names:
            lines.append(f"{path.relative_to(repo_dir).as_posix()}: {', '.join(names)}")
    return "\n".join(lines)
