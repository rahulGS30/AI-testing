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
action history, screenshots, and a WebM video to a unique
`artifacts\<run-id>\` folder. Each scenario is defined by a separate JSON file
in `test_cases\`; add a new file named for its test-case id to add another
scenario. The command needs valid Azure configuration before it will launch
the browser.

If a site requires sign-in or shows CAPTCHA/bot checks, the test stops and
records the obstacle; it does not bypass site controls or proceed to checkout.

## Tests

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests
```
