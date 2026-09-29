"""Load the team's architecture documents (ADRs + CONTRIBUTING) as Hindsight documents.

Only files that exist in the repository snapshot are loaded; nothing is invented. These are
kept separate from PR-history documents (own document ids, source=architecture_docs).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from app.ingestion.history import DEMO_DATA, REPO, TEAM, TEAM_NAME

REPO_DIR = DEMO_DATA / "repositories" / REPO


@dataclass(frozen=True)
class ArchDocument:
    document_id: str
    content: str
    context: str
    timestamp: datetime
    tags: list[str]
    metadata: dict[str, str]

    @property
    def content_hash(self) -> str:
        payload = json.dumps([self.content, self.context, self.tags, self.metadata,
                              self.timestamp.isoformat()], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


def _field(text: str, name: str) -> str:
    m = re.search(rf"^- {name}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def load_adrs() -> list[ArchDocument]:
    docs = []
    for path in sorted((REPO_DIR / "docs" / "adr").glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8")
        number = re.match(r"ADR-(\d+)", path.name).group(1)
        adr_id = f"ADR-{number}"
        title = text.splitlines()[0].lstrip("# ").strip()
        date = _field(text, "Date")
        related_pr = _field(text, "PR").lstrip("#")
        docs.append(ArchDocument(
            document_id=f"adr:{REPO}:{number}",
            content=f"Architecture decision record {adr_id} of the {TEAM_NAME} team, repository {REPO}.\n\n{text}",
            context=(f"Accepted architecture decision record written by the {TEAM_NAME} engineering "
                     f"team (human engineers), stored at docs/adr/{path.name} in {REPO}."),
            timestamp=datetime.fromisoformat(date + "T00:00:00+00:00"),
            tags=sorted({f"team:{TEAM}", f"repo:{REPO}", "source:architecture_docs", "doc:adr",
                         f"adr:{adr_id}"}),
            metadata={"team": TEAM, "repo": REPO, "source": "architecture_docs", "document_type": "adr",
                      "adr_id": adr_id, "title": title, "status": _field(text, "Status"), "date": date,
                      "deciders": _field(text, "Deciders"), "related_pr": related_pr,
                      "path": f"docs/adr/{path.name}", "split": "history"},
        ))
    return docs


def load_contributing() -> ArchDocument | None:
    path = REPO_DIR / "CONTRIBUTING.md"
    if not path.exists():
        return None
    # CONTRIBUTING has no date of its own; its last change in history is PR #121 (merged 2026-05-12).
    pr121 = json.loads((DEMO_DATA / "pull-requests" / REPO / "pr-121.json").read_text(encoding="utf-8"))
    last_changed = pr121["merged_at"]
    return ArchDocument(
        document_id=f"contributing:{REPO}",
        content=f"Contributing guide of the {TEAM_NAME} team for repository {REPO}.\n\n"
                + path.read_text(encoding="utf-8"),
        context=f"Team contribution guidelines (CONTRIBUTING.md) written by the {TEAM_NAME} engineers.",
        timestamp=datetime.fromisoformat(last_changed.replace("Z", "+00:00")),
        tags=sorted({f"team:{TEAM}", f"repo:{REPO}", "source:architecture_docs", "doc:contributing"}),
        metadata={"team": TEAM, "repo": REPO, "source": "architecture_docs",
                  "document_type": "contributing", "path": "CONTRIBUTING.md",
                  "last_changed": last_changed, "timestamp_source": "merged_at of PR #121",
                  "split": "history"},
    )


def load_architecture_docs() -> list[ArchDocument]:
    docs = load_adrs()
    contributing = load_contributing()
    return docs + ([contributing] if contributing else [])
