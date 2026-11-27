# Local setup

## Prerequisites

- Python 3.11 or 3.12 (the current environment has Python 3.12.10)
- Node.js LTS and npm
- Azure AI Foundry deployment credentials for LLM connectivity

Git and the recommended VS Code Python, Pylance, and ESLint extensions are
optional for running the initial foundation but are not currently installed.
Install them if desired:

```powershell
winget install --id Git.Git -e
code --install-extension ms-python.python
code --install-extension ms-python.vscode-pylance
code --install-extension dbaeumer.vscode-eslint
```

## Backend

From the workspace root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

The health endpoint is `http://127.0.0.1:8000/api/health`; API docs are at
`http://127.0.0.1:8000/docs`.

## Frontend

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Vite serves the frontend at `http://localhost:3000`.

## Azure AI Foundry

Copy `.env.example` to `.env` and set the endpoint, API key, and deployment name
for your Azure AI Foundry deployment. Select `AZURE_AI_API_MODE` to match its
API:

- `azure_openai`: Azure OpenAI deployment endpoint; set its API version.
- `openai_v1`: OpenAI-compatible `/openai/v1/` endpoint; API version is unused.
- `foundry_models`: Azure AI model-inference `/models` endpoint; set its API
  version.

Keep `.env` private. Check connectivity at
`http://127.0.0.1:8000/api/health/llm`. The check makes a small request using
the configured deployment's supported API endpoint.

## Google Cloud Storage artifact uploads

Each persona run uploads its result JSON, UX analysis, action history, token
usage, screenshots, and video to the `autotest-screenshot` bucket. Objects are
organized by test case, batch, and persona. Authenticate the machine running the
backend with Application Default Credentials:

```powershell
gcloud auth application-default login
```

The authenticated account needs permission to create objects in the bucket. A
cloud upload error is recorded in the local `result.json`; it does not change
the browser run's outcome or remove its local artifacts.

Alternatively, create the ignored root file `settings.local.json`:

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

Environment variables take precedence over JSON settings. Never commit tokens
or place them in VS Code's shared `settings.json`.

## Run a test scenario

Install Chromium once:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

From the workspace root, run:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-003
```

Use `--headed` to watch Chromium. Run `TC-002` for the Amazon iPhone search, or
`TC-003` for the Thayyil Sports Argentina jacket search. The CLI writes a result,
action history, screenshots, and a WebM video under a unique batch/run ID and
persona-specific folder: `artifacts\<test-case-id>\<batch-id>\<persona-id>\`.
The shared batch ID is the parent run ID; each persona's JSON retains its own
name and individual run ID.
Each scenario is defined by a separate JSON file
in `test_cases\`; add a new file named for its test-case id to add another
scenario. The command needs valid Azure configuration before it will launch
the browser.

Run the three accessibility personas in sequence for one test case, or select
just one persona:

```powershell
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --persona elderly_user low_vision_user non_native_english_speaker
.\.venv\Scripts\python.exe backend\run_test.py TC-003 --persona low_vision_user
```

Use `--all-personas` to include every configured persona. Add `--headed` to
show Chromium during each run. Each persona run creates its own artifact folder.

If a site requires sign-in or shows CAPTCHA/bot checks, the test stops and
records the obstacle; it does not bypass site controls or proceed to checkout.

## Backend API for test cases and runs

The backend exposes test case discovery and run management for future clients:

- `GET /api/test-cases` and `GET /api/test-cases/{id}` list and retrieve cases.
- `POST /api/test-cases/{id}/runs` starts background run(s). Send
  `{"persona_ids":["elderly_user","low_vision_user"]}` to select personas; omit
  the property to use the test case's configured persona.
- `GET /api/test-cases/{id}/runs` lists runs for that case.
- `GET /api/test-cases/{id}/runs/{run_id}` returns status and evidence URLs.
- `GET /api/test-cases/{id}/batches` and
  `GET /api/test-cases/{id}/batches/{batch_id}` list and retrieve grouped
  multi-persona runs.
- `GET /api/test-cases/{id}/runs/{run_id}/artifacts/{path}` downloads an
  individual result, action history, UX analysis, screenshot, or video.

Run artifacts are grouped at
`artifacts\<test-case-id>\<batch-id>\<persona-id>\`, including `result.json`,
`action_history.json`, `ux_analysis.json`, screenshots, and that persona's
video. The JSON metadata keeps the persona ID and individual run ID together.
The analysis contains persona-specific strengths, recommendations, and
unassessed areas. Executions also write a manifest to
`artifacts\<test-case-id>\batches\<batch-id>.json`, linking each persona's
individual run, analysis, screenshots, and video. The batch ID directory is
the shared parent; separate persona directories prevent evidence from being
mixed between personas.
Every finding must cite recorded evidence; this reduces unsupported claims but
cannot guarantee perfect correctness or replace real-user research.

## Tests

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests
```
