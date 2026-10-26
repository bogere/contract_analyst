# Contract Analyst

Contract Analyst is a web app for reviewing contracts with [ContractGuard](https://pypi.org/project/contractguardian/). Upload a document to see an AI-generated summary, fairness score, red flags, warnings, protections, and jurisdiction-aware statute checks. The app includes user accounts, private report history, and an administrator workspace for shared AI settings and account management.

The application consists of a React and TypeScript frontend and a FastAPI backend. The backend uses the ContractGuard Python library to extract and analyze contract text, and PostgreSQL to store accounts, report results, and workspace settings.

> Contract analysis is informational and is not legal advice. Have a qualified attorney review important agreements.

## Why Contract Analyst?

Every year, millions of people sign contracts they don't fully understand — apartment leases with hidden penalties, employment agreements with overly broad non-competes, and NDAs that silently strip away their rights. Hiring a lawyer can cost hundreds of dollars an hour, so many people sign and hope for the best.

**Contract Analyst** makes ContractGuard's contract review available through a web app. Upload a contract to get potential problems explained in plain language, along with suggestions for what to negotiate, a fairness score, and jurisdiction-aware statute checks. Accounts and private report history make it easier to review your contracts over time, while an administrator can manage shared AI settings and team access.

## Features

- Upload PDF, DOCX, TXT, Markdown, RTF, BMP, JPEG, PNG, TIFF, or WebP files up to 20 MiB.
- Review contract findings, fairness score, and statute checks in the browser.
- Register and sign in with an email address and password; passwords must be at least 12 characters.
- Save and revisit your analysis reports. Reports are private to the account that created them.
- Use the admin workspace to manage accounts and configure shared AI provider, model, language, and jurisdiction settings.
- Use Tesseract OCR for scanned PDFs and image uploads.

For the ContractGuard library's CLI and Python API, see the [library usage guide](docs/README.md).

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer and npm
- PostgreSQL
- An OpenAI-compatible LLM provider, such as OpenAI, OpenRouter, or a local Ollama server
- Tesseract OCR and the desired language data if you need to analyze scanned PDFs or images

## Local setup

### 1. Create a PostgreSQL database

Install and start PostgreSQL, then create a database for the app. For example, from `psql`:

```sql
CREATE DATABASE contractguard;
```

Use a PostgreSQL connection URL for the app, such as:

```text
postgresql://USER:PASSWORD@localhost:5432/contractguard
```

The backend creates its application tables when it starts. The database and the PostgreSQL user must already exist and be accessible.

### 2. Install the backend

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\activate` instead.

Edit `.env` and set at least:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/contractguard
AUTH_SECRET_KEY=<a-random-secret-at-least-32-characters-long>
```

Generate a suitable auth secret with:

```bash
openssl rand -hex 32
```

Configure an AI provider in `.env`. For OpenAI, set `OPENAI_API_KEY` and keep or set `OPENAI_BASE_URL=https://api.openai.com/v1`. For OpenRouter, set `OPENROUTER_API_KEY` and configure its OpenAI-compatible base URL. For local Ollama, use its OpenAI-compatible URL and an appropriate model. `CONTRACTGUARD_MODEL` selects the model; the default is `gpt-4o-mini`, so change it if your provider uses a different model ID.

The backend reads `.env` from the repository root. Process environment variables take precedence. Never commit `.env` or put provider keys in frontend variables.

### 3. Install the frontend

In a separate terminal:

```bash
cd frontend
npm ci
```

### 4. Start the app

In the first terminal, from the repository root with the virtual environment active:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

In the second terminal:

```bash
cd frontend
npm run dev
```

Open <http://localhost:5173>. The frontend connects to `http://localhost:8000` by default. If the backend runs elsewhere, set `VITE_API_URL` in `frontend/.env.local` to its origin, and set `CONTRACTGUARD_CORS_ORIGINS` in the backend environment to the exact frontend origin or comma-separated allowed origins.

Check API availability at <http://localhost:8000/api/health>. To create a production frontend build, run `npm run build` from `frontend/`.

## First administrator account

For the initial setup, leave `ADMIN_EMAIL` unset. Start the app and register the account that should become the administrator. Then set `ADMIN_EMAIL` in the root `.env` to that account's email address and restart the backend.

Once that configured account exists, public registration is disabled. The administrator can create additional accounts from the admin workspace. Admin status is assigned only to the configured email address.

## Configuration

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL. Required. |
| `AUTH_SECRET_KEY` | Signs session cookies and encrypts saved provider keys. Required; use at least 32 characters and keep it stable. |
| `ADMIN_EMAIL` | Email address of the administrator. Leave unset until the initial account has been registered. |
| `SESSION_COOKIE_SECURE` | Set to `true` when serving the app over HTTPS. Keep `false` for local HTTP development. |
| `OPENAI_API_KEY` | Provider key for an OpenAI-compatible endpoint. |
| `OPENROUTER_API_KEY` | Provider key for OpenRouter. |
| `OPENAI_BASE_URL` | OpenAI-compatible provider endpoint. |
| `CONTRACTGUARD_MODEL` | Model ID supported by the configured provider. Defaults to `gpt-4o-mini`. |
| `CONTRACTGUARD_OCR_LANG` | Installed Tesseract language codes, defaulting to `eng+chi_sim`. |
| `CONTRACTGUARD_CORS_ORIGINS` | Comma-separated frontend origins allowed by the backend. Defaults to `http://localhost:5173`. |
| `VITE_API_URL` | Backend origin used by the frontend; set in `frontend/.env.local` if it differs from `http://localhost:8000`. |

The admin workspace can override the model, provider URL and key, language, and jurisdiction for all users. Admin-saved provider keys are encrypted using a key derived from `AUTH_SECRET_KEY`; changing that secret means saved keys must be entered again. Leave workspace provider overrides blank to use backend environment settings.

For scanned PDFs and image files, install Tesseract and the language data on the backend host. On Debian or Ubuntu:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
```

Text-based PDFs do not require Tesseract. OCR uses the configured language codes, has a 100-page limit, and limits each image to 50 megapixels.

## Data and privacy

The API accepts uploads up to 20 MiB, extracts the contract text, analyzes it, and removes its temporary upload file afterward. Contract text is sent to the AI provider configured for the workspace. Use a local Ollama-compatible endpoint if contract text must remain on your machine.

PostgreSQL stores account email addresses, password hashes, structured analysis reports and filenames, and workspace settings. It does not store uploaded files or extracted contract text. Passwords are hashed with Argon2, and each user's saved reports are accessible only to that user. Session cookies are HTTP-only and expire after 15 minutes of inactivity.

## Project layout

```text
backend/       FastAPI routes, authentication, PostgreSQL models, OCR
frontend/      React and TypeScript web application
docs/README.md ContractGuard library CLI and Python API guide
```
