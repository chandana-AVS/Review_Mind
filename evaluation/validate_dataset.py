"""Validate the Acme Payments demo dataset against docs/demo-dataset.md.

Usage:
    python evaluation/validate_dataset.py              # full dataset + coverage checks
    python evaluation/validate_dataset.py --only 101-110   # per-file checks for a PR range
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo-data"
PRS = DEMO / "pull-requests" / "payments-service"
REVIEWS = DEMO / "reviews" / "payments-service"
LABELS = ROOT / "evaluation" / "labels"

HISTORY = range(101, 131)
FEEDBACK = range(131, 137)
TEST = range(137, 145)

CONVENTIONS = {f"C{i}" for i in range(1, 11)}
NOISE = {f"N{i}" for i in range(1, 8)}
MISTAKES = {f"M{i}" for i in range(1, 7)}
EXCEPTIONS = {"E1", "E2", "E3"}
CONFLICTS = {"K1", "K2", "K3", "K4"}
RESOLUTIONS = {"applied", "rejected", "discussed", "withdrawn", "noise"}
STRENGTHS = {"enforced", "preferred", "contested", "not_a_rule", "superseded", None}
REVIEW_STATES = {"approved", "changes_requested", "commented"}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SHA = re.compile(r"^[0-9a-f]{40}$")

errors: list[str] = []


def err(where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


def split_of(n: int) -> str:
    return "history" if n in HISTORY else "feedback" if n in FEEDBACK else "test"


def parse_ts(where: str, value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        err(where, f"bad timestamp {value!r}")
        return None


def parse_patch(where: str, patch: str) -> tuple[set[int], set[int], int, int]:
    """Return (added new-file lines, all visible new-file lines, additions, deletions)."""
    added: set[int] = set()
    visible: set[int] = set()
    adds = dels = 0
    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    i = 0
    if not lines or not lines[0].startswith("@@"):
        err(where, "patch must start with a hunk header '@@ ... @@'")
        return added, visible, 0, 0
    while i < len(lines):
        m = HUNK.match(lines[i])
        if not m:
            err(where, f"expected hunk header at patch line {i + 1}: {lines[i][:60]!r}")
            return added, visible, adds, dels
        old_len = int(m.group(2) or 1)
        new_start, new_len = int(m.group(3)), int(m.group(4) or 1)
        i += 1
        old_seen = new_seen = 0
        new_line = new_start
        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            tag = line[:1]
            if tag == "+":
                added.add(new_line)
                visible.add(new_line)
                new_line += 1
                new_seen += 1
                adds += 1
            elif tag == "-":
                old_seen += 1
                dels += 1
            elif tag == " " or line == "":
                visible.add(new_line)
                new_line += 1
                new_seen += 1
                old_seen += 1
            elif line.startswith("\\"):
                pass
            else:
                err(where, f"patch line {i + 1} has no +/-/space prefix: {line[:60]!r}")
            i += 1
        if old_seen != old_len or new_seen != new_len:
            err(
                where,
                f"hunk header {m.group(0)} says -{old_len}/+{new_len} "
                f"but body has -{old_seen}/+{new_seen}",
            )
    return added, visible, adds, dels


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        err(str(path.relative_to(ROOT)), "missing")
    except json.JSONDecodeError as e:
        err(str(path.relative_to(ROOT)), f"invalid JSON: {e}")
    return None


def check_range(where: str, item: dict, added_by_path: dict[str, set[int]]) -> None:
    path = item.get("path")
    rng = item.get("line_range")
    if path not in added_by_path:
        err(where, f"path {path!r} not in PR files")
        return
    if not (isinstance(rng, list) and len(rng) == 2 and rng[0] <= rng[1]):
        err(where, f"bad line_range {rng!r}")
        return
    missing = [n for n in range(rng[0], rng[1] + 1) if n not in added_by_path[path]]
    if missing:
        err(where, f"line_range {rng} includes lines not added by the patch: {missing[:5]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="PR range like 101-110; skips global coverage checks")
    args = ap.parse_args()
    if args.only:
        lo, hi = (int(x) for x in args.only.split("-"))
        numbers = range(lo, hi + 1)
    else:
        numbers = range(101, 145)

    team = json.loads((DEMO / "team.json").read_text(encoding="utf-8"))
    members = {m["handle"] for m in team["members"]}

    coverage: dict[str, set[int]] = defaultdict(set)  # tag -> PR numbers
    prev_created: datetime | None = None

    for n in numbers:
        split = split_of(n)
        where = f"pr-{n}"
        pr = load(PRS / f"pr-{n}.json")
        if pr is None:
            continue
        for key in ("number", "title", "body", "author", "created_at", "state", "split",
                    "labels", "base_sha", "head_sha", "adr", "files"):
            if key not in pr:
                err(where, f"missing key {key!r}")
        if pr.get("number") != n:
            err(where, "number mismatch")
        if pr.get("split") != split:
            err(where, f"split should be {split!r}")
        if pr.get("author") not in members:
            err(where, f"unknown author {pr.get('author')!r}")
        for k in ("base_sha", "head_sha"):
            if not SHA.match(str(pr.get(k, ""))):
                err(where, f"{k} must be 40 lowercase hex chars")
        created = parse_ts(where, pr.get("created_at"))
        if created and prev_created and created < prev_created:
            err(where, "created_at earlier than previous PR (PRs must be chronological)")
        prev_created = created or prev_created
        if split == "history":
            if pr.get("state") not in {"merged", "closed"}:
                err(where, "history PR state must be merged|closed")
            if pr.get("state") == "merged":
                merged = parse_ts(where, pr.get("merged_at"))
                if created and merged and merged < created:
                    err(where, "merged_at before created_at")
        else:
            if pr.get("state") != "open":
                err(where, "held-out PR state must be 'open'")

        added_by_path: dict[str, set[int]] = {}
        visible_by_path: dict[str, set[int]] = {}
        for f in pr.get("files", []):
            fw = f"{where}:{f.get('path')}"
            if f.get("status") not in {"added", "modified", "removed", "renamed"}:
                err(fw, f"bad status {f.get('status')!r}")
            added, visible, adds, dels = parse_patch(fw, f.get("patch", ""))
            if adds != f.get("additions") or dels != f.get("deletions"):
                err(fw, f"additions/deletions {f.get('additions')}/{f.get('deletions')} "
                        f"!= counted {adds}/{dels}")
            if f.get("status") == "added" and dels:
                err(fw, "added file cannot have deletions")
            added_by_path[f["path"]] = added
            visible_by_path[f["path"]] = visible

        if split == "history":
            rv = load(REVIEWS / f"pr-{n}.json")
            lb = load(LABELS / "history" / f"pr-{n}.json")
            if rv is None or lb is None:
                continue
            for r in rv.get("reviews", []):
                if r.get("reviewer") not in members:
                    err(where, f"unknown reviewer {r.get('reviewer')!r}")
                if r.get("state") not in REVIEW_STATES:
                    err(where, f"bad review state {r.get('state')!r}")
                parse_ts(where, r.get("submitted_at"))
            thread_ids = []
            for t in rv.get("threads", []):
                tid = t.get("id", "?")
                tw = f"{where}:{tid}"
                thread_ids.append(tid)
                if not re.fullmatch(rf"t{n}-\d+", tid):
                    err(tw, "thread id must be t<pr>-<k>")
                if t.get("path") is not None:
                    if t["path"] not in visible_by_path:
                        err(tw, f"path {t['path']!r} not in PR files")
                    elif t.get("line") not in visible_by_path[t["path"]]:
                        err(tw, f"line {t.get('line')} not visible in the patch of {t['path']}")
                if t.get("resolution") not in RESOLUTIONS:
                    err(tw, f"bad resolution {t.get('resolution')!r}")
                if t.get("resolution") == "rejected" and not t.get("rejection_reason"):
                    err(tw, "rejected thread needs rejection_reason")
                if t.get("resolution") != "rejected" and t.get("rejection_reason"):
                    err(tw, "rejection_reason only allowed on rejected threads")
                if t.get("decided_by") not in members | {None}:
                    err(tw, f"unknown decided_by {t.get('decided_by')!r}")
                if not t.get("comments"):
                    err(tw, "thread has no comments")
                for c in t.get("comments", []):
                    if c.get("author") not in members | {"ci-bot", "dependabot"}:
                        err(tw, f"unknown comment author {c.get('author')!r}")
                    ts = parse_ts(tw, c.get("created_at"))
                    if ts and created and ts < created:
                        err(tw, "comment before PR created_at")
            labels = lb.get("threads", {})
            if set(labels) != set(thread_ids):
                err(where, f"label thread ids {sorted(labels)} != review thread ids {sorted(thread_ids)}")
            for tid, lab in labels.items():
                lw = f"{where}:{tid}:label"
                for key, allowed in (("convention", CONVENTIONS), ("noise", NOISE),
                                     ("mistake", MISTAKES), ("exception", EXCEPTIONS),
                                     ("conflict", CONFLICTS)):
                    v = lab.get(key)
                    if v is not None and v not in allowed:
                        err(lw, f"{key}={v!r} not allowed")
                    if v is not None:
                        coverage[v].add(n)
                if lab.get("noise") in {"N6", "N7"}:
                    err(lw, "N6/N7 must not appear in history")
                if lab.get("expected_strength") not in STRENGTHS:
                    err(lw, f"bad expected_strength {lab.get('expected_strength')!r}")
                if "is_convention_signal" not in lab:
                    err(lw, "missing is_convention_signal")
            if pr.get("adr"):
                coverage[pr["adr"]].add(n)
        else:
            if (REVIEWS / f"pr-{n}.json").exists():
                err(where, "held-out PRs must not have a reviews file")
            lb = load(LABELS / split / f"pr-{n}.json")
            if lb is None:
                continue
            ids = set()
            for kind in ("should_flag", "should_not_flag"):
                for item in lb.get(kind, []):
                    iw = f"{where}:{item.get('id')}"
                    if item.get("id") in ids:
                        err(iw, "duplicate id")
                    ids.add(item.get("id"))
                    check_range(iw, item, added_by_path)
                    if kind == "should_flag":
                        if item.get("kind") not in {"convention", "generic_bug"}:
                            err(iw, "kind must be convention|generic_bug")
                        if item.get("kind") == "convention" and item.get("convention") not in CONVENTIONS:
                            err(iw, "convention id required")
                        if item.get("kind") == "convention":
                            coverage[f"{split}:flag:{item['convention']}"].add(n)
                        else:
                            coverage[f"{split}:generic_bug"].add(n)
                        if not item.get("rationale"):
                            err(iw, "rationale required")
                    else:
                        if bool(item.get("noise")) == bool(item.get("exception")):
                            err(iw, "exactly one of noise|exception required")
                        if item.get("noise") and item["noise"] not in NOISE:
                            err(iw, "bad noise id")
                        if item.get("exception") and item["exception"] not in EXCEPTIONS:
                            err(iw, "bad exception id")
                        if not item.get("trap") or not item.get("reject_reason"):
                            err(iw, "trap and reject_reason required")
                        coverage[f"{split}:trap:{item.get('noise') or item.get('exception')}"].add(n)
            drift = lb.get("drift_trap")
            if drift:
                if drift.get("conflict") not in {"K1", "K2"}:
                    err(where, "drift_trap.conflict must be K1|K2")
                coverage[f"{split}:drift:{drift.get('conflict')}"].add(n)
            if split == "test":
                flags = lb.get("should_flag", [])
                if sum(1 for f in flags if f.get("kind") == "convention") < 2:
                    err(where, "test PR needs >=2 convention should_flag")
                if not any(f.get("kind") == "generic_bug" for f in flags):
                    err(where, "test PR needs a generic_bug should_flag")
                if sum(1 for x in lb.get("should_not_flag", []) if x.get("noise")) < 3:
                    err(where, "test PR needs >=3 noise traps")

    if not args.only:
        need = []
        need += [(m, 3, "history PRs") for m in sorted(MISTAKES)]
        need += [(nz, 2, "history PRs") for nz in ("N1", "N2", "N3", "N4", "N5")]
        need += [(x, 1, "history PRs") for x in sorted(EXCEPTIONS | CONFLICTS)]
        need += [(c, 1, "history PRs") for c in sorted(CONVENTIONS)]
        need += [(f"ADR-00{i}", 1, "PRs") for i in range(3, 9)]
        need += [("feedback:trap:N6", 2, "feedback PRs"), ("feedback:trap:N7", 2, "feedback PRs")]
        need += [("test:drift:K1", 1, "test PRs"), ("test:drift:K2", 1, "test PRs")]
        need += [("test:generic_bug", 8, "test PRs")]
        for tag, minimum, what in need:
            got = len(coverage[tag])
            if got < minimum:
                err("coverage", f"{tag} appears in {got} {what}, need >= {minimum}")
        if not any(k.startswith("test:trap:E") for k in coverage):
            err("coverage", "no exception trap (E1-E3) in test PRs")
        print("coverage:")
        for tag in sorted(coverage):
            print(f"  {tag:<24} {sorted(coverage[tag])}")

    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print("  -", e)
        return 1
    print(f"\nOK: PRs {numbers.start}-{numbers.stop - 1} valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
