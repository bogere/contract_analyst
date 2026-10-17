"""FastAPI upload endpoint for the React ContractGuard analyst."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from openai import OpenAIError
from starlette.concurrency import run_in_threadpool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from contractguard.analyzer import analyze_contract
from contractguard.checklist import run_checklist
from contractguard.models import AnalysisResult
from contractguard.parser import extract_text

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".rtf"}
DEFAULT_MODEL = (os.getenv("CONTRACTGUARD_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"

app = FastAPI(title="ContractGuard API", version="1.0.0")
cors_origins = [
    origin.strip()
    for origin in os.getenv("CONTRACTGUARD_CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalysisResult)
async def analyze_upload(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    api_key: str | None = Form(None),
    base_url: str | None = Form(None),
    lang: Literal["en", "zh"] = Form("en"),
    jurisdiction: Literal["auto", "cn", "us-ca"] = Form("auto"),
) -> AnalysisResult:
    """Extract and analyze one supported contract document."""
    temp_path: Path | None = None
    try:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Upload a PDF, DOCX, TXT, MD, or RTF document.",
            )

        effective_api_key = (api_key or "").strip() or None
        if not effective_api_key and not any(
            (os.getenv(name) or "").strip()
            for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
        ):
            raise HTTPException(
                status_code=503,
                detail="No LLM API key configured. Enter one above or set OPENAI_API_KEY / OPENROUTER_API_KEY.",
            )

        with tempfile.NamedTemporaryFile(
            prefix="contractguard-upload-",
            suffix=suffix,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File is larger than the 20 MiB upload limit.")
                temp_file.write(chunk)

        text = await run_in_threadpool(extract_text, temp_path)
        if len(text.strip()) < 50:
            raise HTTPException(
                status_code=422,
                detail="Contract text is too short. Please upload a complete, text-readable document.",
            )

        try:
            result = await run_in_threadpool(
                analyze_contract,
                contract_text=text,
                model=model.strip() or DEFAULT_MODEL,
                api_key=effective_api_key,
                base_url=(base_url or "").strip() or None,
                lang=lang,
            )
        except (OpenAIError, RuntimeError, AttributeError, IndexError) as exc:
            raise HTTPException(status_code=502, detail=f"Contract analysis failed: {exc}") from exc

        result.statute_checks = run_checklist(
            text,
            result.contract_type.value,
            lang,
            jurisdiction=jurisdiction,
        )
        return result
    except HTTPException:
        raise
    except (ValueError, ImportError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> None:
    """Launch the API for local development."""
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
