# Design 02: GitHub Action

> Status: **implemented** and verified on a test repo (designed 2026-09-23). Facts marked **(verified)** were checked
> against docs.github.com, with sources in [../decisions.md](../decisions.md#verified-external-facts).
> Items marked **(verified on a test repo)** are undocumented by GitHub and were
> confirmed on `Badri-Rajendran/codesage-sandbox` on 2026-09-25.

## Goals

- Any of the owner's repos can add CodeSage with a ~15-line workflow and one secret.
- It reviews automatically when a PR is opened, and afterwards only when asked.
- Results live in GitHub: a PR review with inline comments, a `CodeSage` check run,
  and a job summary.
- It is cheap: $0.50 cap per review, a lean install, and no always-on server.

## Packaging

- **Composite action** at the repo root (`action.yml`), used as
  `uses: Badri-Rajendran/CodeSage@v1`. Composite actions run on any OS and avoid
  the container build of Docker actions, which are Linux-only and slower **(verified)**.
- **Versioning:** semver tags plus a moving major tag (`v1`). During development,
  repos can point at `@feat/agentic-workflows`.
- **Lean install:** `pyproject.toml` is split so the action installs only what the
  engine needs:
  - `dependencies` (engine): `anthropic`, `langchain`, `langchain-anthropic`,
    `langgraph`, `pydantic`, `pydantic-settings`, `httpx`, `pyyaml`, `tenacity`.
  - `[server]` extra (local mode only): `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`,
    `asyncpg`, `pgvector`, `numpy`, `mcp`, `langgraph-checkpoint-postgres`, `psycopg[binary]`,
    `psycopg-pool`.
  - Engine modules must not import server-only modules at import time.
    `semantic_search` is injected, not imported.

### `action.yml` (shape)

```yaml
name: CodeSage review
description: Agentic PR review with Claude
inputs:
  anthropic-api-key: { required: true }
  github-token:      { required: false, default: "${{ github.token }}" }
  config-path:       { required: false, default: ".codesage.yml" }
outputs:
  score:      { value: "${{ steps.review.outputs.score }}" }
  gate:       { value: "${{ steps.review.outputs.gate }}" }       # pass | tripped | skipped
  cost-usd:   { value: "${{ steps.review.outputs.cost_usd }}" }
  review-url: { value: "${{ steps.review.outputs.review_url }}" }
runs:
  using: composite
  steps:
    - uses: actions/setup-python@v7            # current major (verified 2026-09-24)
      with: { python-version: "3.12" }
    - shell: bash
      run: python -m venv "$RUNNER_TEMP/codesage" && "$RUNNER_TEMP/codesage/bin/pip" install -q "${{ github.action_path }}"
    - id: resolve                               # decide whether to run; find head SHA
      shell: bash
      env: { GITHUB_TOKEN: "${{ inputs.github-token }}" }
      run: '"$RUNNER_TEMP/codesage/bin/codesage-action" resolve'
    - if: steps.resolve.outputs.run == 'true'
      uses: actions/checkout@v7                 # current major (verified); pin in Phase 5
      with: { ref: "${{ steps.resolve.outputs.head_sha }}", fetch-depth: 0, persist-credentials: false }
    - id: review
      if: steps.resolve.outputs.run == 'true'
      shell: bash
      env:
        ANTHROPIC_API_KEY: "${{ inputs.anthropic-api-key }}"
        GITHUB_TOKEN: "${{ inputs.github-token }}"
        CODESAGE_CONFIG: "${{ inputs.config-path }}"
      run: '"$RUNNER_TEMP/codesage/bin/codesage-action" review'
```

Composite steps may use `uses:` (such as setup-python and checkout) and must set `shell:`
on `run:` steps. `github.action_path` points at the action's own files **(verified)**.
Inputs are read from the `inputs` context rather than `INPUT_*` environment variables
**(verified)**. The current majors of `actions/checkout` and `actions/setup-python` are both
v7 **(verified 2026-09-24)**.

- **Console script, not `python -m`.** The steps call the `codesage-action` console script.
  `python -m app.action` would put the working directory (the reviewed repo) first on
  `sys.path`, so a repo with its own `app/` package would have its code imported. This was
  found during the Phase 5 dry run.
- **No persisted credentials.** The checkout uses `persist-credentials: false`, so the token
  isn't written to `.git/config`, where `run_tests` code could read it.
- **No `.env` file.** Settings are built without reading a `.env` file, because the working
  directory is the reviewed repo.

### Example consumer workflow (`.github/workflows/codesage.yml`)

```yaml
name: CodeSage
on:
  pull_request:
    types: [opened, ready_for_review, labeled]
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
  checks: write          # pull-requests: write also covers removing the label (verified on a test repo)
concurrency:
  group: codesage-${{ github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: true
jobs:
  review:
    runs-on: ubuntu-latest
    if: >-
      (github.event_name == 'pull_request' && (
        (github.event.action == 'opened' && !github.event.pull_request.draft) ||
        github.event.action == 'ready_for_review' ||
        (github.event.action == 'labeled' && github.event.label.name == 'codesage:review'))) ||
      (github.event_name == 'issue_comment' && github.event.issue.pull_request &&
        startsWith(github.event.comment.body, '/codesage review') &&
        contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
    steps:
      - uses: Badri-Rajendran/CodeSage@v1
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The job-level `if` avoids starting a runner for events that won't be reviewed. The
`resolve` step applies the same rules again, so a mistake in a copied workflow can't
cause unintended runs.

## Trigger rules (`app/action/rules.py`)

| Event | Runs when | Head SHA source |
|---|---|---|
| `pull_request` `opened` | not a draft | `pull_request.head.sha` |
| `pull_request` `ready_for_review` | always | `pull_request.head.sha` |
| `pull_request` `labeled` | `label.name == "codesage:review"`; the label is removed after the review | `pull_request.head.sha` |
| `issue_comment` `created` | `issue.pull_request` is set, body starts with `/codesage review`, and `author_association` is OWNER, MEMBER or COLLABORATOR | `GET /repos/{r}/pulls/{n}`, then `head.sha`, because the payload doesn't include it **(verified)** |
| anything else | never | |

- **Fork PRs are skipped:** if `head.repo.full_name != github.repository`, the step exits 0
  with a job-summary note. Fork PRs get no secrets and a read-only token anyway **(verified)**.
- **`issue_comment` runs the workflow from the default branch** **(verified)**, so
  the consumer workflow must exist on the default branch before comments trigger it.
  The README must say this.

## `.codesage.yml` (`app/review_config.py`)

A Pydantic model. Every key is optional; defaults are shown.

```yaml
models:
  reviewer: claude-sonnet-5
  judge: claude-opus-5
effort:
  reviewer: medium
  judge: high
budget_usd: 0.50
gate:
  threshold: 0.6            # judge score below this trips the gate
  fail_on: [critical, high] # severities that trip the gate
ignore_paths:               # glob patterns, merged with built-in defaults
  - "**/*.lock"
  - "**/package-lock.json"
  - "dist/**"
  - "build/**"
max_files: 50               # files beyond this are listed in the body, not reviewed
tests:
  setup: null               # e.g. "pip install -e .[dev]" or "npm ci"
  command: null             # e.g. "pytest -q {target}" or "npm test -- {target}"
  timeout_s: 300
```

Unknown keys or bad values fail fast, with a clear error in the check run and the job summary.

## Publishing (`app/publish/github.py`)

### Check run

- Created at the start of `review` with `status: in_progress`, named `CodeSage`, on
  the head SHA, so the PR shows a pending check while the review runs.
- Completed at the end:

| Outcome | `conclusion` | Title |
|---|---|---|
| Gate passes | `success` | "No blocking issues · score 0.82" |
| Gate trips | `failure` | "Needs attention · 1 critical, 2 high" or "score 0.52 < 0.60" |
| Budget-limited before the judge ran, and it's the **only** gate reason | `neutral` | "Budget reached before judging" |
| Engine error | `failure` | "CodeSage error: <message>" |

Precedence: engine error > any critical/high finding or low score (`failure`) >
budget-limited-only (`neutral`) > pass (`success`).

- `output.summary` holds the same markdown as the review body, truncated to stay
  under 65,535 characters **(verified limit)**.
- Up to 50 findings with a line become annotations (`failure` for critical and high,
  `warning` for medium, `notice` for the rest), within the 50-per-request limit **(verified)**.
- `GITHUB_TOKEN` can create check runs when `checks: write` is granted **(verified)**.

### PR review

- One review per run: `POST /repos/{r}/pulls/{n}/reviews`, with `event: COMMENT` and
  `commit_id` set to the head SHA.
- **Inline comments:** only findings where `Diff.is_commentable(path, line)` is true, using
  `path`, `line`, `side: RIGHT` (plus `start_line` and `start_side` for ranges). The
  deprecated `position` field isn't used **(verified)**.
  - At most 30 inline comments; the rest go in the body. GitHub documents no
    maximum, so this is our own conservative choice.
  - Comment body: a severity badge, the title, the rationale, the suggestion,
    `evidence` in a collapsed `<details>` block, and the reviewer name.
- **Review body:**
  - a header with the judge score and gate result
  - counts by severity
  - findings that couldn't be anchored inline
  - reflection's summary
  - a per-agent cost table, and a "budget-limited" note when relevant
  - a footer with the CodeSage version
- **Fallback:** if the create-review call returns 422, it's retried once with no
  inline comments and every finding in the body. One comment on a line outside the
  diff fails the **whole** review with 422 "Line could not be resolved", and nothing
  is posted **(verified on a test repo)**.
- `APPROVE` and `REQUEST_CHANGES` are never used. `APPROVE` by `GITHUB_TOKEN` is off
  by default in new repos **(verified)**, and the gate's blocking signal is the check run.

### Job summary and outputs

- `$GITHUB_STEP_SUMMARY` gets the review body plus the full agent traces, in collapsed
  sections, capped at 1 MiB **(verified limit)**.
- The step outputs are `score`, `gate`, `cost_usd` and `review_url`.

### Label housekeeping

After a label-triggered run, `codesage:review` is removed
(`DELETE /repos/{r}/issues/{n}/labels/codesage:review`), so adding it again
triggers a new review. `pull-requests: write` is enough; `issues: write` isn't
needed **(verified on a test repo)**: a run whose token had only checks, contents,
metadata and pull-requests removed the label.

## Process exit semantics

| Situation | Exit code | Why |
|---|---|---|
| Skipped (fork, rules not met) | 0 | Nothing to do |
| Review published, gate passes or trips | 0 | The pass/fail signal is the `CodeSage` check run, which branch protection can require |
| Engine or publishing error | 1 | Shows as a failed job; the check run says `CodeSage error` |

## Security

- The Anthropic key comes only from the `anthropic-api-key` input (a repo secret).
  It is never passed to `run_tests` (see [01-agent-engine.md](01-agent-engine.md#run_tests)).
- The workflow grants only `contents: read`, `pull-requests: write` and `checks: write`.
- Only same-repo PRs from trusted authors are reviewed. `pull_request_target` is **not**
  used, because GitHub warns against running PR code under it **(verified)**.
- Repo names are checked with `validate_repo` (`app/github/client.py`) before any
  API call that carries the token.

## Local reproduction

`codesage-action review --event path/to/event.json --event-name pull_request --repository owner/name --workspace /path/to/repo --dry-run --local-diff`
runs the same code outside Actions, using a saved event payload. It's used for
Phase 5 verification and for debugging without pushing.
