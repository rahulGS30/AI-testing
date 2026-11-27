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

**Phase 1 and browser-runner foundation:** FastAPI health/persona endpoints,
Azure AI Foundry configuration, structured action/persona models, a
JSON-defined Amazon and Thayyil Sports test case, terminal runner, Playwright
observation/action loop, per-run video/screenshots/JSON evidence, tests, and the
React dashboard.

**Not implemented yet:** UX/accessibility analysis, axe-core, reports, API
test-run orchestration, dashboard execution controls, and human review.

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

Use `--headed` to watch the browser while it runs:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-002 --headed
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --headed
```

Test cases are independent JSON files in [test_cases/](test_cases/). Add a
uniquely named JSON file whose `test_case_id` matches its filename to add a
scenario. Runs are saved under `artifacts/<run-id>/`, with `result.json`,
`action_history.json`, screenshots, and a `video/journey.webm` recording.
The CLI exits with code 0 only when the cart goal is confirmed. Other outcomes
(blocked, incomplete, step limit, or error) produce a non-zero exit and a
result file.

## API endpoints

- `GET /api/health` — backend status and non-secret configuration readiness
- `GET /api/health/llm` — sends a small request to verify Azure connectivity
- `GET /api/personas` — returns the predefined personas

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
