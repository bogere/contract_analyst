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
- Choose a monthly Free, Pro, or Business analysis plan and subscribe or cancel through PayPal.
- Use the admin workspace to manage accounts and configure shared AI provider, model, language, and jurisdiction settings.
- Use Tesseract OCR for scanned PDFs and image uploads.

For the ContractGuard library's CLI and Python API, see the [library usage guide](docs/CONTRACTGUARD.md).

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

For the initial setup, leave `ADMIN_EMAIL` unset. Start the app and register the account that should become the administrator. Then set `ADMIN_EMAIL` in the root `.env` to that account's email address and restart the backend. Public registration remains available for customers; only the configured email address receives administrator access.

The administrator can also create accounts from the admin workspace. Admin status is assigned only to the configured email address.

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
| `PAYPAL_MODE` | `sandbox` for testing or `live` for real payments. Defaults to `sandbox`. |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | Server-side credentials for the matching PayPal app. Never expose the secret to the frontend. |
| `PAYPAL_WEBHOOK_ID` | Webhook ID used to verify events from PayPal. |
| `PAYPAL_PRO_PLAN_ID` / `PAYPAL_BUSINESS_PLAN_ID` | IDs for the corresponding active, monthly USD subscription plans in PayPal. |
| `APP_BASE_URL` | Public frontend URL used for PayPal's return and cancel redirects. Defaults to `http://localhost:5173`. |

### PayPal subscriptions

The built-in plans are Free (3 analyses per UTC calendar month), Pro ($19/month, 50 analyses), and Business ($49/month, 250 analyses). Analysis limits are enforced by the API. Paid subscriptions are individual to each account and are billed monthly by PayPal. A plan change requires cancelling the current subscription first; cancellation immediately returns the account to the Free plan.

To enable paid plans:

1. Create a PayPal developer app and set its client ID and secret in the backend `.env`. Start with sandbox credentials and `PAYPAL_MODE=sandbox`.
2. In PayPal, create active subscription plans in USD: Pro at $19 billed every month and Business at $49 billed every month. Put their plan IDs in `PAYPAL_PRO_PLAN_ID` and `PAYPAL_BUSINESS_PLAN_ID`.
3. Register a PayPal webhook pointing to `https://<your-api-host>/api/billing/webhook`, subscribe it to `BILLING.SUBSCRIPTION.ACTIVATED`, `BILLING.SUBSCRIPTION.CANCELLED`, `BILLING.SUBSCRIPTION.SUSPENDED`, `BILLING.SUBSCRIPTION.EXPIRED`, and `BILLING.SUBSCRIPTION.UPDATED`, and set its ID as `PAYPAL_WEBHOOK_ID`.
4. Set `APP_BASE_URL` to the frontend's public URL and configure `CONTRACTGUARD_CORS_ORIGINS` for that exact origin.
5. Test signup, checkout, renewal, and cancellation using sandbox accounts before launch. For live payments, use live PayPal app credentials and live plan and webhook IDs, set `PAYPAL_MODE=live`, enable HTTPS, and set `SESSION_COOKIE_SECURE=true`.

The backend checks that configured PayPal plans are active and charge the expected monthly USD price before starting checkout. When PayPal settings or plan IDs are missing, paid plans remain unavailable; the Free tier continues to work.

The admin workspace can override the model, provider URL and key, language, and jurisdiction for all users. Admin-saved provider keys are encrypted using a key derived from `AUTH_SECRET_KEY`; changing that secret means saved keys must be entered again. Leave workspace provider overrides blank to use backend environment settings.

For scanned PDFs and image files, install Tesseract and the language data on the backend host. On Debian or Ubuntu:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
```

Text-based PDFs do not require Tesseract. OCR uses the configured language codes, has a 100-page limit, and limits each image to 50 megapixels.

## Data and privacy

The API accepts uploads up to 20 MiB, extracts the contract text, analyzes it, and removes its temporary upload file afterward. Contract text is sent to the AI provider configured for the workspace. Use a local Ollama-compatible endpoint if contract text must remain on your machine.

PostgreSQL stores account email addresses, password hashes, structured analysis reports and filenames, workspace settings, and PayPal subscription IDs and statuses. It does not store uploaded files or extracted contract text. Passwords are hashed with Argon2, and each user's saved reports and subscription are accessible only to that user. Session cookies are HTTP-only and expire after 15 minutes of inactivity.

## Project layout

```text
backend/       FastAPI routes, authentication, PostgreSQL models, OCR
               PayPal subscription API and webhook verification
frontend/      React and TypeScript web application
docs/CONTRACTGUARD.md ContractGuard library CLI and Python API guide
```
