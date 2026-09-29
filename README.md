# ReviewMind

**Code review that remembers how your team engineers.**

ReviewMind is an AI code reviewer that learns a specific team's review culture. It remembers the team's
conventions, architectural decisions, past review feedback, and which of its own suggestions developers
accepted or rejected. It uses [Hindsight](https://hindsight.vectorize.io) as its long-term memory.

Generic AI reviewers are noisy. They suggest the same things to every team, they don't know about local
decisions ("we use integer cents, not `Decimal`"), and they never learn from "we don't care about that here".
ReviewMind reviews a pull request the way *this* team reviews code, cites the past PR or ADR behind each
team-specific finding, and stores developer feedback so later reviews can recall it.

```
Historical PRs + ADRs ──retain──▶ Hindsight ──recall / reflect──▶ Review with cited evidence
        ▲                                                              │
        └──────── retain (experience) ◀── Developer accepts / rejects ─┘
```

---

## Demo (3 minutes)

Open **http://localhost:8010** once both servers are running (see [Quick start](#quick-start)).

1. **PR #137 · Add refund processing fee** loads with its saved ReviewMind review: code on the left, findings in the middle, Hindsight memory on the right.
2. Click **Run review**. The pipeline strip shows the real steps: reading the changed files, detecting code patterns, querying Hindsight, and how many memories came back.
3. On finding **F1** (direct `httpx.Client` construction), open **"Why did ReviewMind say this?"**. It shows the current code line, the recalled memory, the sources (ADR-006, PR #123, PR #127) and how they connect.
4. Click **Reject**, keep the reason *"Approved exception for this integration."*, then **Submit feedback**. You'll see **"✓ Feedback retained in Hindsight"**, with the decision, developer, memory types (`experience + world`) and the PR/finding it came from.
5. Click **Review again with learned memory**. The latest saved run is shown with an honest status line, and a live Hindsight recall returns the developer feedback as its top results.
6. Optional: choose another PR from the selector (#138, #139, #141, #143 have full saved reviews), or switch to **Try your own code** and run a *fast memory check* on any snippet or unified diff.

The browser never runs the expensive reasoning step (5–10 min). Dataset reviews show real saved ReviewMind
runs; the recall and feedback steps call Hindsight live.

---

## How ReviewMind uses Hindsight

ReviewMind uses Hindsight's memory features: fact extraction, observations, mental models and reflect. It does
not use Hindsight as a plain vector store.

| Hindsight feature | How ReviewMind uses it |
|---|---|
| **Banks + missions** | One bank per team (`reviewmind-acme-payments`). Its retain, observation and reflect missions tell Hindsight to extract conventions, decisions, outcomes and reasons. Its skeptical disposition (4/5) stops one reviewer's opinion from becoming a team rule. |
| **Retain** | One *narrated* document per historical PR: who asked for what, where, why, and whether it was **applied or rejected**, with the stated reason. It never retains raw diffs. ADRs and CONTRIBUTING are separate documents. Every retain carries provenance tags and metadata (team, repo, PR, thread ids, outcomes, dates). |
| **Deterministic `document_id`** | `pr:payments-service:<n>`, `adr:payments-service:<nnn>`, `feedback:payments-service:pr<n>:<finding>`. Re-retaining replaces instead of duplicating (verified, see below). |
| **Experience vs. world facts** | Developer feedback is written as ReviewMind's own log, so Hindsight stores an **experience** fact ("erin rejected my finding…") *and* a **world** fact about the team's position. |
| **Observations (consolidation)** | One manual consolidation pass turned 151 extracted facts into **105 observations**: consolidated, evidence-backed team norms. |
| **Mental models** | **Team Review Playbook** and **Noise Profile**: living summaries of what the team enforces and what it rejects. |
| **Recall** | The review agent detects generic code patterns (outbound HTTP, retries, time, money, config, errors, logging, sessions, endpoints, helpers, tests) and sends targeted recall queries, anchored to the PR's date. |
| **Reflect** | One reflect call per review reasons over the diff and the labelled recalled memories, and produces the team brief, the findings and the suppressed suggestions. |

Every team-specific finding must cite a recalled memory. The agent drops any team claim that has no memory
evidence. Reviewing never writes to memory; the only write path is developer feedback.

---

## Quick start

### Prerequisites

- Windows with Python 3.11+ (tested on 3.11.3) and Google Chrome.
- The **Claude Code CLI**, logged in (`claude auth status` shows `loggedIn: true`). The local Hindsight server uses it as its LLM through the `claude-code` provider, so no API key is needed. Hindsight documents this provider as *for personal development use only*.

### 1. Install

```powershell
cd reviewmind
python -m venv .hindsight-venv
.hindsight-venv\Scripts\python -m pip install hindsight-api==0.10.1

cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # then set HINDSIGHT_BASE_URL=http://localhost:8888
```

### 2. Start Hindsight (window 1, keep it open)

```powershell
cd reviewmind\backend
powershell -ExecutionPolicy Bypass -File scripts\start_hindsight_local.ps1
```

Wait for `Application startup complete` (about 40 s; the first start also downloads embedding models).
The script sets the settings this machine needs:

| Setting | Value | Why |
|---|---|---|
| `HINDSIGHT_API_LLM_PROVIDER` | `claude-code` | Uses the local Claude CLI login |
| `HINDSIGHT_API_RERANKER_PROVIDER` | `rrf` | The CPU cross-encoder took 15–217 s per recall on this laptop; RRF fusion takes about 1 s |
| `HINDSIGHT_API_REFLECT_WALL_TIMEOUT` | `1200` | Mental-model builds exceed the 300 s default |
| `PYTHONUTF8` | `1` | The Windows console can't print the server banner otherwise |

### 3. Build the team memory (once)

```powershell
cd reviewmind\backend
.venv\Scripts\python scripts\phase2_preflight.py                   # dataset + leak-guard checks
.venv\Scripts\python scripts\ingest_history.py --all --concurrency 5
.venv\Scripts\python scripts\ingest_architecture.py
.venv\Scripts\python scripts\phase2_verify.py --expect-arch        # 30/30 PRs, 7/7 docs, 0 leaks
.venv\Scripts\python scripts\phase2_finalize.py                    # one consolidation + 2 mental models
```

Ingestion is resumable and content-hash based. Re-running skips unchanged documents, and the finalize step
never consolidates twice (the state is kept in `backend\.state\`).

### 4. Start the demo UI (window 2)

```powershell
cd reviewmind\backend
powershell -ExecutionPolicy Bypass -File scripts\start_demo_ui.ps1
```

Open **http://localhost:8010**.

### Hindsight Cloud instead of local

Set `HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io` and `HINDSIGHT_API_KEY=…` in `backend\.env`.
The same code works against Cloud, but the account needs credits: our key authenticated fine, but every
retain, recall and reflect returned `402 Insufficient credits`.

---

## Command-line tools

All commands run from `reviewmind\backend`.

| Command | What it does |
|---|---|
| `.venv\Scripts\python scripts\review_pr.py --pr 137` | Full review of a dataset PR (recall + reflect, about 3–10 min). Writes `evaluation\results\reviews\pr-137-memory.json` |
| `… review_pr.py --pr 137 --bank reviewmind-acme-payments-demo` | Same review against the demo bank (baseline + feedback) |
| `… review_pr.py --pr 137 --no-memory` | Same reviewer on an empty bank (no team memory) |
| `… feedback_review.py --pr 137 --list` | List the findings of a saved review |
| `… feedback_review.py --pr 137 --finding F1 --decision REJECTED --reason "…" --developer erin` | Retain developer feedback in Hindsight (demo bank) |
| `… verify_feedback.py --pr 137 --finding F1` | Check the feedback is stored, is an experience, has provenance, can be recalled, and that the baseline bank is untouched |
| `… refresh_demo_mental_models.py` | Rebuild the two mental models in the demo bank only |
| `… memory_validation.py` | Targeted recall checks on known history (HTTP clients, retries, money, docstrings) |
| `… hindsight_experiments.py` | Re-run the Phase 1 Hindsight behaviour experiments |

---

## Dataset: Acme Payments

A realistic synthetic team, **Acme Payments**, working on the Python/FastAPI repository `payments-service`,
from January to September 2026. See [docs/demo-dataset.md](docs/demo-dataset.md).

| Split | PRs | Used for |
|---|---|---|
| History | #101–#130 (30 PRs, 65 review threads) | Ingested into Hindsight |
| Feedback | #131–#136 (31 labelled feedback events) | Simulated developer feedback (setup D) |
| Test | #137–#144 | Held out; **never ingested** |

- **Planted into the history PRs:**
  - 10 team conventions (e.g. money as `int` minor units per ADR-007, outbound HTTP only through `make_client()` per ADR-006, `@idempotent` on money-moving POSTs)
  - 7 kinds of suggestions the team rejects (e.g. "use Decimal", "add another retry layer", docstrings on private helpers)
  - 6 recurring mistakes
  - 3 documented exceptions
  - 4 conflicts, including two rules that changed over time and one reviewer whose opinions the team didn't adopt
- **Architecture docs:** 6 ADRs and CONTRIBUTING.md, in `demo-data/repositories/payments-service`.
- **Answer keys live only in `evaluation/labels/`.** Ingestion never reads them.
- **Leak guard:** only `split == "history"` PRs #101–#130 can enter memory, and PR numbers ≥ 131 are always rejected. This is verified against what is actually stored in Hindsight (document ids, metadata and text), not only against what the script intended to send.

The PRs are authored as Python in `evaluation/dataset_src/`. Rebuild and validate them with:

```powershell
python evaluation\dataset_src\build.py
python evaluation\validate_dataset.py
python evaluation\build_benchmark.py
```

---

## Results so far

### Team memory (`reviewmind-acme-payments`)

| | |
|---|---|
| Historical PR documents | 30 / 30 |
| Architecture documents | 7 / 7 |
| Memory units | 256 (151 world facts + 105 observations) |
| Mental models | Team Review Playbook, Noise Profile |
| Leak violations / provenance problems | 0 / 0 |
| Ingestion time | 27 PRs in 781 s at 5-way concurrency (3-PR pilot: 242 s); consolidation 693 s |

### Observed Hindsight behaviour (tested, not assumed)

- **Same `document_id`, new content:** the old facts are **replaced**. Retaining the same content twice *without* an id creates duplicates.
- **Classification by speaker:** ReviewMind's own actions are stored as **experience**. A human reviewer's first-person comment is stored as **world**. Developer feedback on a ReviewMind finding splits into both.

### Reviews

- **Saved full reviews:** #137 (baseline and after feedback), #138, #139, #141, #143. They are in `evaluation/results/reviews/`.
- **PR #137, the demo PR.** Three memory-backed findings, one real bug, and one held-back suggestion:
  - three team-specific findings citing memory: `make_client()` (ADR-006, PR #123, PR #127), missing `@idempotent` (PR #130), `clock.now()` (PR #120);
  - one generic finding, the planted real bug (the fee is computed on the wrong amount);
  - a docstring suggestion **held back**, because the team has rejected that kind of suggestion before;
  - no "use Decimal" suggestion, even though the code uses integer percentage maths.
- **PR #139:** caught the planted outdated comment "requests is fine here for now" via ADR-006.
- **PR #143:** flagged `Decimal` money as a **REPEATED_ISSUE**, citing PR #112 and PR #118.
- **Feedback loop:** rejecting a finding stores it in Hindsight (about 65 s). A plain-language recall then returns the feedback as its top 2 results, and a recall filtered to `experience` finds it too.
- **Timing:** recall takes about 1 s per review (RRF). Reflect takes 150–630 s with the local `claude-code` provider.

---

## Project structure

```
reviewmind/
├── backend/
│   ├── app/
│   │   ├── api/demo.py            demo API (PRs, saved reviews, analyze, recall, feedback)
│   │   ├── feedback/              feedback model + FeedbackStore (Hindsight retain, demo bank only)
│   │   ├── hindsight/             the only Hindsight client: service, errors, bank setup
│   │   ├── ingestion/             history loader + leak guard, architecture-doc loader
│   │   ├── review/                diff parsing, code signals, review agent (recall + reflect)
│   │   ├── config.py              settings from backend/.env
│   │   └── main.py                FastAPI app serving the API and the demo UI
│   ├── scripts/                   ingestion, review, feedback, verification, start scripts
│   └── tests/                     offline tests + live Hindsight test + Chrome demo flow
├── frontend/demo/                 the demo UI (static HTML/CSS/JS, served by FastAPI)
├── demo-data/                     Acme Payments repo snapshot, PRs, review threads
├── evaluation/
│   ├── dataset_src/               source of truth for the dataset
│   ├── labels/                    answer keys (never ingested)
│   ├── results/                   ingestion, verification, review and feedback reports
│   └── validate_dataset.py, build_benchmark.py, benchmark.json
└── docs/                          architecture.md, memory-design.md, demo-dataset.md
```

---

## Tests

```powershell
cd reviewmind\backend
# offline (no Hindsight needed): 50 tests
.venv\Scripts\python -m pytest tests/test_demo_api.py tests/test_feedback.py tests/test_hindsight_service.py tests/test_history_loader.py tests/test_review_bank.py -q

# live Hindsight integration (needs the server on :8888)
.venv\Scripts\python -m pytest tests/test_hindsight_integration.py -v -s

# browser demo flow at 1920x1080 (needs both servers + Chrome; uses Playwright)
.venv\Scripts\python -m pip install playwright
.venv\Scripts\python tests/ui_demo_flow.py <screenshot_dir>
```

---

## Status and limitations

**Done:** Hindsight integration, the leak-guarded dataset, ingestion of history and architecture docs,
consolidation, mental models, the review agent, the feedback → retain → recall loop, and the demo UI.

**Not done yet:**
- **The A/B/C/D evaluation isn't implemented.** The benchmark data is ready (`evaluation/benchmark.json`), but `evaluation/evaluate.py` is still empty, so there are **no benchmark numbers yet**.
- **No live reflect in the browser.** Dataset reviews in the UI show saved runs; a full live review takes 3–10 minutes and runs from the CLI.
- **The latest saved run of #137 still raises F1 after the rejection.** The feedback is stored and recallable, but that run did not apply it. The UI says so explicitly.
- **Recall ranking is noisier than it would be with the cross-encoder**, because RRF is used for speed on this machine.
- **`docker-compose.yml` and `frontend/package.json` are empty placeholders.** The demo UI is plain static files, and Hindsight runs through the pip package, not Docker.
- **Feedback on held-out PRs goes only into the demo bank** (`reviewmind-acme-payments-demo`, a clone of the baseline), so the baseline bank stays clean for evaluation.

## Documentation

- [docs/architecture.md](docs/architecture.md): product, system and agent architecture
- [docs/memory-design.md](docs/memory-design.md): what is retained, recalled and reflected on, and why
- [docs/demo-dataset.md](docs/demo-dataset.md): dataset specification and benchmark design
