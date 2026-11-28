# AI Persona-Based UX Testing Agent

A phased platform for evaluating web journeys from simulated user perspectives
and producing evidence-backed usability and accessibility findings.

## Test scenarios

`TC-002` targets Amazon:

> Search for "iPhone 17" and add a relevant result to the cart.

`TC-003` targets Thayyil Sports:

> Search for "jacket argentina", open the first relevant result, and add it to
> the cart.

Both runs stop after cart confirmation. They must not sign in, check out, enter
personal or payment information, or place an order. Each scenario is maintained
in its own JSON file in [test_cases/](test_cases/).

Public HTTPS assets may be loaded from CDNs; top-level browser navigation
remains restricted to the test case's authorized domain. Private/reserved
network requests are blocked.

## Current status

**Implemented:** FastAPI health/persona and run APIs, Azure AI Foundry
configuration, saved and custom public-HTTPS test runs, persona selection,
Playwright screenshots/video, evidence-backed UX analysis, per-call token usage
logs, Google Cloud Storage backups of each persona's result artifacts and
dashboard-generated PDF reports, run history, and a React dashboard for reports
and artifacts.

Custom task completion is agent-reported and is not independently verified.
**Not implemented yet:** axe-core scans, database-backed history/token usage,
user authentication, and human review.

## Quick start

See [docs/setup.md](docs/setup.md) for full setup instructions.

Backend (from the workspace root):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

The backend starts without Azure credentials. Copy `.env.example` to `.env` and
configure the Azure AI Foundry values to enable the LLM connectivity check.
Set `AZURE_AI_API_MODE` to `azure_openai`, `openai_v1`, or `foundry_models` to
match the endpoint format.
authenticate with Google Cloud Application Default Credentials to upload run
artifacts to the `autotest-screenshot` bucket; see [docs/setup.md](docs/setup.md).

You can instead create a root-level `settings.local.json` (it is gitignored)
and provide your deployment settings there:

```json
{
  "azure_ai": {
    "endpoint": "YOUR_AZURE_AI_ENDPOINT",
    "token": "YOUR_AZURE_AI_KEY",
    "model_deployment": "YOUR_MODEL_DEPLOYMENT",
    "api_mode": "azure_openai",
    "api_version": "YOUR_API_VERSION"
  }
}
```

The `.env` values take precedence. Do not put credentials in the tracked
`settings.json`, VS Code settings, or frontend files.

## Run a browser test from the terminal

Install the Playwright browser once:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Run either scenario from the workspace root:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-002
.\.venv\Scripts\python.exe backend\run_test.py TC-003
```

Run one test case with the three accessibility personas from a single command:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --persona elderly_user low_vision_user non_native_english_speaker
```

Select one persona by ID:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --persona low_vision_user
```

Use `--all-personas` to run every configured persona, including the default
first-time user and impatient power user:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --all-personas
```

Use `--headed` to watch the browser while it runs:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-002 --headed
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --headed
```

Add `--headed` to any persona command to show Chromium for each sequential run.

Test cases are independent JSON files in [test_cases/](test_cases/). Add a
uniquely named JSON file whose `test_case_id` matches its filename to add a
scenario. Persona runs are grouped under one batch/run ID at
`artifacts/<test-case-id>/<batch-id>/<persona-id>/`, with `result.json`,
`action_history.json`, screenshots, and a `video/journey.webm` recording,
grouped by test case.
The CLI exits with code 0 only when the cart goal is confirmed. Other outcomes
(blocked, incomplete, step limit, or error) produce a non-zero exit and a
result file.

## API endpoints

- `GET /api/health` — backend status and non-secret configuration readiness
- `GET /api/health/llm` — sends a small request to verify Azure connectivity
- `GET /api/personas` — returns the predefined personas
- `GET /api/test-cases` and `GET /api/test-cases/{id}` — list and retrieve
  runnable JSON test cases
- `POST /api/test-cases/{id}/runs` — queue one or more persona runs
- `POST /api/runs/custom` — run a user-supplied public HTTPS URL and task for
  selected personas; completion is agent-reported and is not independently
  verified
- `GET /api/runs` — list saved and custom runs for the dashboard history
- `GET /api/test-cases/{id}/runs` and
  `GET /api/test-cases/{id}/runs/{run_id}` — list runs or retrieve a run status
- `GET /api/test-cases/{id}/batches` and
  `GET /api/test-cases/{id}/batches/{batch_id}` — retrieve multi-persona run
  batches with each persona's run and evidence links
- `GET /api/test-cases/{id}/runs/{run_id}/artifacts/{path}` — retrieve a
  run's result, action history, persona UX analysis, token usage, screenshot,
  or video

Example custom-run request:

```json
{
  "website": "https://example.com/",
  "task": "Find the help page.",
  "persona_ids": ["first_time_user"]
}
```

Custom runs use the same browser safety checks as saved tests: only public
HTTPS navigation within the submitted website's domain is allowed; sign-in,
checkout, purchases, and sensitive data entry remain blocked.

Run creation accepts `{"persona_ids":["elderly_user","low_vision_user"]}`.
Omit `persona_ids` to use the persona configured on the test case. A queued
response includes each run ID; poll the run endpoint for status and use the
returned `artifact_urls` when a run has produced evidence.

Each run is stored under its test case:

```text
artifacts/
  TC-003/
    batches/
      <batch-id>.json
    <batch-id>/
      elderly_user/
        result.json
        action_history.json
        ux_analysis.json
        token_usage.json
        screenshots/
        video/
      low_vision_user/
        result.json
        action_history.json
        ux_analysis.json
        screenshots/
        video/
```

The batch ID is also the shared parent run ID. Each persona folder contains
that persona's readable name and unique run ID in its JSON metadata and keeps
the screenshots, video, action history, result, and UX analysis together. The
batch manifest links persona IDs and names to their individual run IDs and
evidence URLs.

After browser execution, each run includes a persona-specific UX analysis with
evidence-cited strengths, improvement suggestions, and aspects not assessed.
Every recommendation cites recorded page text, controls, actions, or run status;
unsupported items are rejected rather than presented as findings. This is
evidence-grounded AI assistance, not a guarantee of perfect correctness or a
replacement for real participant research.

## Security and scope

- Azure credentials stay on the backend and must not be committed.
- Browser actions will use a validated, finite action schema, never generated
  executable code.
- The test case controls the allowed domain; private/reserved IP destinations
  and sign-in/checkout/purchase navigation are blocked.
- Every scenario ends at its configured completion criteria; checkout,
  purchases, and personal or payment information are out of scope.
- If a site requires a CAPTCHA, bot check, or sign-in, the run stops and records
  the obstacle rather than trying to bypass it.
- Browser interaction uses observed labels and controls rather than assumptions
  about a particular store or e-commerce platform. New site-specific fallbacks
  should only be added when generic observed controls are insufficient.
- Simulated personas are not substitutes for real usability studies.
- Automated accessibility scans do not replace manual accessibility testing.

## Environment notes

The current environment has Python 3.12.10, Node.js 24.22.1, npm 11.19.1, and
VS Code 1.143.1. Git and the recommended VS Code Python, Pylance, and ESLint
extensions were not detected.
