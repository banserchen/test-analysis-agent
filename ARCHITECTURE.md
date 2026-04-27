# Architecture & Developer Guide (for AI Agents)

> This document is written for AI coding agents that need to understand and modify the
> **test-analysis-agent** codebase quickly. It focuses on *where things live*, *how data
> flows*, and *where to make changes* — not on end-user usage. For user-facing docs see
> [`README.md`](README.md) / [`README.zh-CN.md`](README.zh-CN.md).

---

## 1. Purpose in One Paragraph

`test-analysis-agent` is an AI-powered service that analyzes **CD pipeline failures**.
It pulls **Jenkins build logs**, **Workflow API (wfapi) stage data**, and optional
**Allure test reports**, finds the first genuinely failing stage using its real name,
runs a pluggable **LLM analyzer** plus a set of **rule-based + LLM-driven skills** over
the data, and emits a structured `AnalysisReport` that can be rendered as Markdown /
JSON / HTML. It exposes both a **CLI** (for Jenkins post-failure hooks) and a **FastAPI
HTTP service**.

---

## 2. Top-Level Layout

```
test-analysis-agent/
├── pyproject.toml              # Package metadata, deps, scripts, ruff/pytest config
├── Dockerfile                  # Image for the API service (entrypoint: `serve`)
├── .env.example                # All TAA_* environment variables documented here
├── README.md                   # User-facing docs (English)
├── README.zh-CN.md             # User-facing docs (Chinese)
├── ARCHITECTURE.md             # THIS FILE — agent-facing technical doc
├── src/test_analysis_agent/    # All production code lives here (src layout)
│   ├── __init__.py             # Exposes __version__
│   ├── agent.py                # AnalysisAgent — top-level orchestrator
│   ├── config.py               # Settings (pydantic-settings, env prefix TAA_)
│   ├── cli.py                  # Click-based CLI (entry: test-analysis-agent)
│   ├── llm_client.py           # LLMClient ABC + OpenAI & Copilot backends
│   ├── llm_analyzer.py         # Prompt templates + higher-level LLM tasks
│   ├── report_generator.py     # Jinja2 templates → Markdown / JSON / HTML
│   ├── api/
│   │   └── app.py              # FastAPI application
│   ├── models/
│   │   └── schemas.py          # Pydantic models + enums (stages, categories, …)
│   ├── parsers/
│   │   ├── jenkins_parser.py   # Fetch + parse Jenkins console logs / stages
│   │   └── allure_parser.py    # Parse allure-report JSON
│   ├── feishu_reporter.py      # Feishu cloud document creation + listing
│   ├── skills/                 # Extensible skill/plugin system
│   │   ├── base.py             # BaseSkill + SkillRegistry
│   │   ├── log_pattern_skill.py            # Built-in regex-based skill
│   │   ├── jenkins_log_root_cause/         # Structured skill (yaml+py+prompt)
│   │   ├── jenkins_downstream_trace/
│   │   ├── test_bootstrap_failure/
│   │   ├── allure_case_failure_classifier/
│   │   ├── code_stacktrace_mapper/
│   │   ├── env_instability_detector/
│   │   ├── bug_draft_generator/
│   │   ├── report_summary_generator/
│   │   ├── infrastructure_inspector/       # SSH-based env health check skill
│   │   ├── stage_failure_analyzer/         # Per-stage failure root cause skill
│   │   ├── pipeline_summary_generator/     # High-level pipeline summary skill
│   │   └── test_failure_batch_analyzer/    # Batch Allure failure analysis skill
│   └── templates/              # (reserved — Jinja templates currently inline)
└── tests/                      # Pytest suite mirroring src modules
```

Package uses **src-layout**; `pyproject.toml` declares
`[tool.setuptools.packages.find] where = ["src"]` and the console script
`test-analysis-agent = "test_analysis_agent.cli:main"`.

---

## 3. Request → Report Data Flow

```
AnalysisRequest                                 (models.schemas)
       │
       ▼
AnalysisAgent.analyze()                         (agent.py)
  1. JenkinsParser.get_build_log()              → raw log text
  2. JenkinsParser.get_wfapi_stages()           → authoritative stage list (name/status/error)
  3. JenkinsParser.get_jenkinsfile()            → Jenkinsfile content (via replay endpoint)
  4. _build_stage_results(log, wfapi, parser)   → list[PipelineStageResult]
       • Prefers wfapi data; falls back to parse_log_text()
       • For failed stages: fetches per-step logs via get_stage_log() → stageFlowNodes
       • Merges triggered_jobs from log-parsed data
  5. _find_first_failing_stage()                → first PipelineStageResult with status=="fail"
  6. SkillRegistry.execute_applicable(ctx)      → list[skill results]
       ctx includes: log_text, stage_results, failure_stage_name, jenkinsfile
  7. _analyze_triggered_jobs()                  → enrich stage_results with downstream logs
  8. Branch on failure_stage_name:
        test stage (name matches 测试/test/allure/…)
          → AllureReportParser.parse({build_url}/allure)
          → LLMAnalyzer.analyze_test_failure() (per case) or analyze_test_failures_batch()
        any other stage
          → LLMAnalyzer.analyze_stage_failure() per failing stage
  9. _enrich_with_skill_findings()              → merge skill findings into issues
 10. LLMAnalyzer.generate_summary(failure_stage_name=…) → summary + bug_recommendations
 11. Filter: stage_results → only status∉{"pass","success"} (passing stages excluded)
 12. Build AnalysisReport
 13. SkillRegistry.apply_post_processing(report)
       │
       ▼
AnalysisReport → report_generator (md/json/html) → CLI stdout / file / HTTP response
```

There is also a lighter path `AnalysisAgent.analyze_log_text()` for the
`analyze-log` CLI command and `/api/v1/analyze/log` endpoint — it skips Jenkins
fetch, wfapi, and Allure logic.

---

## 4. Key Modules (agent-oriented reference)

### 4.1 `agent.py` — `AnalysisAgent`

The **single orchestrator**. If you are adding a new analysis step, this is almost
always where you wire it in. Notable private helpers:

- `_fetch_build_data(request)` — talks to Jenkins, returns `(log_text, build_info)`.
- `_build_stage_results(log_text, wfapi_stages, parser, request)` — builds the
  canonical stage list. Prefers wfapi data (accurate names/statuses/errors); for failed
  stages it calls `parser.get_stage_log()` which fetches child step logs via
  `stageFlowNodes`. Falls back to `parse_log_text()` when wfapi is unavailable.
- `_find_first_failing_stage(stage_results)` — returns the first stage with
  `status=="fail"`, preferring ones with failing triggered jobs.
- `_is_test_stage(stage_name)` — regex heuristic to detect test stages by name.
- `_analyze_triggered_jobs(...)` — recursively fetches downstream job logs and
  appends their error summaries.
- `_parse_allure_report(request)` — tolerant of missing/broken reports.
- `_find_test_code(failure, request)` — sandboxed lookup of test source under
  `request.test_repo_path` (uses `_class_to_paths` + `_extract_method`). Path traversal
  is explicitly guarded (`resolved.relative_to(base)`). The result is stored in
  `failure.related_code` and passed to skills (e.g., `allure_case_failure_classifier`)
  for deeper root cause analysis of broken tests.
- `_enrich_with_skill_findings(issues, skill_results)` — converts skill
  `pattern_findings` into `AnalyzedIssue`s when a category is not already covered.
- `_determine_failure_stage(stage_results)` — legacy helper kept for backward
  compatibility; prefer `_find_first_failing_stage` for new code.

### 4.2 `config.py` — `Settings`

`pydantic_settings.BaseSettings` with `env_prefix="TAA_"` and `.env` support.
All tunable knobs live here (LLM provider/model/tokens, Jenkins creds, skills
dir, log limits, report language, API host/port). Read via `get_settings()`.

### 4.3 `llm_client.py` & `llm_analyzer.py`

- `llm_client.LLMClient` is an ABC with `chat_completion(...)`. Two
  implementations are selected by `TAA_LLM_PROVIDER`:
  - `OpenAIClient` (default) — any OpenAI-compatible endpoint.
  - `CopilotClient` — uses the optional `github-copilot-sdk` extra.
  Use `create_llm_client(settings)` to get the right one.
- `llm_analyzer.LLMAnalyzer` wraps the client with **prompts** and returns typed
  objects (`AnalyzedIssue`, `TestCaseFailure` with `ai_analysis`, summary +
  bug recs). Language is controlled by `settings.report_language`.

> When adding new LLM-driven analysis, prefer adding a method on `LLMAnalyzer`
> (not calling `llm_client` directly) so prompt/language handling stays
> centralized.

### 4.4 `parsers/`

- `jenkins_parser.JenkinsParser` — wrapper around `python-jenkins` extended with
  several direct `requests`-based methods:
  - `get_build_log` / `get_build_info` / `get_build_url` — standard log fetch.
  - `get_triggered_job_log(job_name, build_number)` — log for a downstream job.
  - `get_wfapi_stages(job_name, build_number)` — calls `{build_url}/wfapi/describe`
    for the authoritative stage list (name, status, error message, node id).
  - `get_stage_log(stage_node_id, job_name, build_number)` — describes the stage
    node to find its `stageFlowNodes`, collects logs from failed child steps; falls
    back to the stage-level log endpoint.
  - `get_jenkinsfile(job_name, build_number)` — fetches `{build_url}/replay` and
    extracts the Groovy script from the `<textarea>` element.
  - `get_workspace_file(job_name, build_number, file_path)` — fetches a file from
    `{build_url}/ws/{file_path}`.
  - `_get_auth()` — extracts `(username, password)` tuple from `server._auths` for
    use with `requests` (handles bytes-encoded credentials from python-jenkins).
- `jenkins_parser.parse_log_text(text)` — pure function, regex-driven stage
  classifier returning `list[PipelineStageResult]`. Used as fallback when wfapi is
  unavailable. Patterns live in `_STAGE_PATTERNS`.
- `allure_parser.AllureReportParser` — loads a local directory or remote URL of
  Allure JSON files and returns `statistics` + `get_failures() -> list[TestCaseFailure]`.

### 4.5 `models/schemas.py`

The **contract** of the whole system. Enums:

- `PipelineStage`: `preparation | deployment | testing | unknown` — kept for internal
  classification and backward compat, but **excluded from JSON output** (`exclude=True`).
  The user-facing identifier is `stage_name` (the real Jenkins stage name).
- `FailureCategory`: environment/dependency/deployment/configuration/network/
  permission/test_startup/test_case/test_infrastructure/timeout/resource/
  function_bug/unknown
- `Severity`: `critical | high | medium | low | info`

Core models and key fields:

| Model | Key fields |
|-------|-----------|
| `AnalysisRequest` | `job_name`, `build_number`, `jenkins_url`, `allure_report_url` |
| `PipelineStageResult` | `stage_name` (real Jenkins name), `status`, `error_summary`, `log_excerpt`, `triggered_jobs`; `stage` is excluded from JSON |
| `TriggeredJobInfo` | `job_name`, `build_number`, `status`, `url` |
| `TestCaseFailure` | `test_name`, `status`, `error_message`, `stack_trace`, `ai_analysis` |
| `AnalyzedIssue` | `title`, `description`, `category`, `severity`, `root_cause`, `suggestion`; `stage` is excluded from JSON |
| `BugRecommendation` | `summary`, `detail_description`, `severity`, `category`, `affected_jobs`; `affected_stage` is excluded from JSON |
| `AnalysisReport` | `failure_stage_name` (real stage name), `stage_results` (failure/skipped only), `issues`, `bug_recommendations`, `module_versions` (dict of module→version); `failure_stage` is excluded from JSON |

> Legacy fields (`PipelineStageResult.stage`, `AnalyzedIssue.stage`,
> `BugRecommendation.affected_stage`, `AnalysisReport.failure_stage`) are kept on the
> models for internal use but have `Field(exclude=True)` so they are invisible in JSON
> output. Do not reintroduce them in the output schema.
>
> If you change an enum value or a field name, grep for the string — it is
> frequently used in prompts (`llm_analyzer.py`) and templates
> (`report_generator.py`).

### 4.6 `report_generator.py`

Pure rendering. Three functions: `generate_markdown_report`,
`generate_json_report`, `generate_html_report`. Jinja2 templates are **defined
inline** in this file (not under `templates/`). No LLM calls here.

### 4.7 `api/app.py` — FastAPI service

Singleton agent via `get_agent()`. Endpoints:

| Method | Path | Body | Notes |
|--------|------|------|-------|
| `GET`  | `/health` | — | health + skills count |
| `POST` | `/api/v1/analyze` | `AnalysisRequest` | full Jenkins+Allure pipeline |
| `POST` | `/api/v1/analyze/log` | `AnalyzeLogRequest` | raw-text analysis |
| `POST` | `/api/v1/analyze/report/{format}` | `AnalysisRequest` | md/json/html |
| `POST` | `/api/v1/analyze/log/report/{format}` | `AnalyzeLogRequest` | md/json/html |
| `GET`  | `/api/v1/skills` | — | list registered skills |
| `POST` | `/api/v1/report/feishu` | `AnalysisReport` | create a Feishu cloud document; returns `{"document_url": "..."}` |
| `GET`  | `/api/v1/reports/feishu` | — | list all Feishu reports in the configured folder |

`format` must be one of `markdown | json | html`, otherwise 400.

### 4.8 `feishu_reporter.py`

Creates and lists Feishu cloud documents from `AnalysisReport` data using the Feishu Open API.

Key public functions:

- `create_feishu_report(report, app_id, app_secret, folder_token="")` — full workflow:
  acquires tenant access token, creates a blank docx in `folder_token`, then writes all
  content blocks (module versions table, test statistics, bug recommendations table,
  issues table), and returns `(document_id, document_url)`.
- `list_feishu_reports(app_id, app_secret, folder_token="")` — lists all items in the
  configured folder using `GET /drive/explorer/v2/folder/{token}/children`, then batch-
  queries their metadata (`POST /drive/v1/metas/batch_query`) to obtain tenant-specific
  URLs; returns a list of `{"name", "url", "created_time"}` dicts.

Internal helpers: `_get_tenant_token`, `_create_document`, `_get_document_url` (uses
batch_query for correct subfolder URL), `_append_blocks`, `_create_table`.

> `_append_blocks` and `_create_table` cell writes include 429 rate-limit retry with
> exponential backoff (up to 3 attempts, delays: 1s / 2s / 4s).

### 4.9 `cli.py`

Click group. Commands: `analyze`, `analyze-log`, `serve`, `skills`.
Global options override `Settings` via `ctx.obj`. Rendered with `rich`.

### 4.10 `skills/` — extension system

Two flavors of skill coexist:

1. **Module-style skill** (single file) — e.g. `log_pattern_skill.py`. Subclass
   `BaseSkill`, implement `can_handle(context)` and `execute(context)`, return
   a dict that may include a `pattern_findings` list (each finding: `category`,
   `description`, `match_count`, …). `SkillRegistry` adds `_skill_name` and,
   on error, `_error`.

2. **Structured skill package** — a directory containing:
   - `skill.yaml` — metadata, input/output JSON schema, tags (Chinese text
     allowed — used for catalog/docs only).
   - `prompt.md` — LLM prompt template.
   - `skill.py` — `class <Name>Skill(BaseSkill)` wiring schema + prompt.
   - `postprocess.py` — optional `AnalysisReport → AnalysisReport` mutation,
     invoked from `BaseSkill.post_process`.
   - `__init__.py` — re-export the skill class.

`SkillRegistry` (in `skills/base.py`) supports:

- `register(skill)` / `unregister(name)` / `get(name)` / `list_skills()`
- `find_applicable(ctx)` → skills whose `can_handle` returns True
- `execute_applicable(ctx)` → runs them, swallows exceptions into `_error`
- `apply_post_processing(report)` → runs every skill’s `post_process`
- `load_from_directory(dir)` → auto-discover user plugins (see `TAA_SKILLS_DIR`)

Built-ins are registered in `AnalysisAgent._setup_skills()`. **When adding a
new built-in skill, register it there and export it from
`skills/__init__.py`.**

---

## 5. Configuration Surface

All env vars are prefixed `TAA_`. Canonical list is in `.env.example` and
`config.py`. Notable:

| Variable | Meaning |
|----------|---------|
| `TAA_LLM_PROVIDER` | `openai` (default) or `copilot` |
| `TAA_LLM_API_KEY` / `TAA_LLM_BASE_URL` / `TAA_LLM_MODEL` | LLM backend |
| `TAA_LLM_MAX_TOKENS` / `TAA_LLM_TEMPERATURE` | LLM generation params |
| `TAA_JENKINS_URL` / `TAA_JENKINS_USERNAME` / `TAA_JENKINS_PASSWORD` | Jenkins |
| `TAA_SKILLS_DIR` | Extra directory scanned by `SkillRegistry.load_from_directory` |
| `TAA_MAX_LOG_LINES` | Hard cap on lines shipped to the LLM |
| `TAA_REPORT_LANGUAGE` | `zh-CN` (default) or `en`; threaded into prompts |
| `TAA_API_HOST` / `TAA_API_PORT` | uvicorn binding |
| `TAA_ENVIRONMENTS_API_URL` | Base URL for test-environment info API; required by `infrastructure_inspector` skill |
| `TAA_FEISHU_APP_ID` | Feishu application App ID for cloud document creation |
| `TAA_FEISHU_APP_SECRET` | Feishu application App Secret |
| `TAA_FEISHU_FOLDER_TOKEN` | Feishu folder token where reports are saved (bot must own the folder) |

---

## 6. Build / Lint / Test

The project uses `ruff` and `pytest` (declared in `pyproject.toml`):

```bash
pip install -e ".[dev]"       # editable + dev deps
pytest tests/ -v              # run the whole suite
ruff check src/ tests/        # lint
ruff format src/ tests/       # format (line-length 120, target py310)
```

Ruff lint rules: `E, F, I, W`. Pytest uses `asyncio_mode = "auto"`.

### Test files → production module map

| Test | Covers |
|------|--------|
| `tests/test_models.py` | `models/schemas.py` |
| `tests/test_jenkins_parser.py` | `parsers/jenkins_parser.py` |
| `tests/test_allure_parser.py` | `parsers/allure_parser.py` |
| `tests/test_llm_client.py` | `llm_client.py` |
| `tests/test_skills.py` | `skills/base.py` + built-ins |
| `tests/test_new_skills.py` | structured skill packages |
| `tests/test_report_generator.py` | `report_generator.py` |

There is no dedicated test for `agent.py` end-to-end; integration is covered
indirectly via skills + parsers + report generator tests. If you change
orchestration in `agent.py`, add a focused unit test alongside.

---

## 7. Common Change Recipes

- **Add a new failure category**: extend `FailureCategory` in `models/schemas.py`,
  reflect it in prompts (`llm_analyzer.py`) and any regex in
  `skills/log_pattern_skill.py`. Update `tests/test_models.py`.

- **Add a new built-in skill**: create a module or directory under `skills/`,
  subclass `BaseSkill`, register it in `AnalysisAgent._setup_skills()`, export
  it from `skills/__init__.py`, add a test in `tests/test_new_skills.py`.

- **Add a new CLI subcommand**: add a `@main.command()` in `cli.py`; reuse
  `_create_agent(ctx)` to respect global flags.

- **Add a new HTTP endpoint**: add a route in `api/app.py`; use `get_agent()`
  and return an existing pydantic model where possible to keep the OpenAPI
  schema clean.

- **Add a new LLM backend**: implement `LLMClient` in `llm_client.py`, wire it
  into `create_llm_client(settings)`, and declare any extra deps as an optional
  extra in `pyproject.toml` (mirror the `copilot` extra).

- **Change report rendering**: edit the inline Jinja strings in
  `report_generator.py`; keep field access (`report.*`) in sync with
  `AnalysisReport` in `models/schemas.py`.

- **Tweak stage detection**: wfapi is the primary source — stage names come directly
  from Jenkins and need no tuning. To change how the log-parsing fallback works, edit
  `_STAGE_PATTERNS` in `parsers/jenkins_parser.py` and extend
  `tests/test_jenkins_parser.py`. To change how per-stage step logs are collected,
  edit `JenkinsParser.get_stage_log()` (specifically the `stageFlowNodes` logic).

---

## 8. Invariants & Things Not To Break

- `AnalysisReport` is the single public output contract — both CLI and HTTP
  consume it. Keep field names and enum values stable, or update all
  renderers (`report_generator.py`) and prompts (`llm_analyzer.py`) together.
- Legacy `stage`/`failure_stage`/`affected_stage` fields on models carry
  `Field(exclude=True)`. Do not expose them in JSON or add new stage-category
  fields to the output; use `stage_name`/`failure_stage_name` (real names) instead.
- `BaseSkill.execute` must be **exception-safe enough** for the registry — the
  registry swallows errors into `{"_error": str(exc)}`, but a skill that
  silently corrupts `context` can still break downstream skills. Treat
  `context` as read-mostly.
- `_find_test_code` intentionally validates that the resolved file stays
  inside `test_repo_path`. Do not remove this check — it prevents path
  traversal when `test_repo_path` is attacker-influenced.
- The `src/` layout means imports **must** be `from test_analysis_agent…` —
  never relative to `src/`.
- All prompts must respect `settings.report_language`; the pattern is
  `"respond in {language}"` — don’t hard-code English in new prompts.

---

## 9. Runtime Entry Points

| Entry | Command | Code |
|-------|---------|------|
| CLI | `test-analysis-agent …` | `cli.main` |
| HTTP | `test-analysis-agent serve` / Docker | `api.app:app` |
| Library | `from test_analysis_agent.agent import AnalysisAgent` | `agent.AnalysisAgent` |

The Docker image (`Dockerfile`) installs the package and defaults to
`serve --host 0.0.0.0 --port 9090`.

---

## 10. Quick Orientation Checklist for an Agent

Before making a change, skim in this order:

1. `src/test_analysis_agent/models/schemas.py` — learn the data shapes.
2. `src/test_analysis_agent/agent.py` — learn the orchestration.
3. The module directly relevant to the task (parser / skill / LLM / API / CLI).
4. The matching test file under `tests/`.
5. `config.py` + `.env.example` — check whether a new env var is needed.

Then: write the smallest change, run `ruff check` + `pytest`, and **update this
file and `README.md` / `README.zh-CN.md` if you**:
- Added a new public concept (skill kind, endpoint, enum, env var)
- Changed the analysis flow or data flow
- Added/removed/renamed methods on `JenkinsParser`, `AnalysisAgent`, or key schemas
- Changed what appears in the JSON output (`AnalysisReport` fields)
