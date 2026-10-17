"""FastAPI upload endpoint for the React ContractGuard analyst."""

from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from openai import OpenAIError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from backend.auth import (  # noqa: E402
    Credentials,
    CurrentUser,
    DatabaseSession,
    DUMMY_PASSWORD_HASH,
    UserResponse,
    clear_session_cookie,
    get_current_user,
    password_hash,
    set_session_cookie,
    validate_auth_configuration,
)
from backend.database import (  # noqa: E402
    AnalysisReport,
    User,
    close_database,
    get_database_session,
    initialize_database,
)
from contractguard.analyzer import analyze_contract
from contractguard.checklist import run_checklist
from contractguard.models import AnalysisResult
from contractguard.parser import extract_text

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".rtf"}
DEFAULT_MODEL = (os.getenv("CONTRACTGUARD_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"

cors_origins = [
    origin.strip()
    for origin in os.getenv("CONTRACTGUARD_CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_auth_configuration()
    try:
        await initialize_database()
        yield
    finally:
        await close_database()


app = FastAPI(title="ContractGuard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)


class ReportSummary(BaseModel):
    id: uuid.UUID
    filename: str
    contract_type: str
    fairness_score: int
    fairness_grade: str
    created_at: datetime


class SavedReport(BaseModel):
    id: uuid.UUID
    filename: str
    result: AnalysisResult
    created_at: datetime


def require_trusted_origin(request: Request) -> None:
    if request.headers.get("origin") not in cors_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {"status": "ok"}


@app.post(
    "/api/auth/register",
    response_model=UserResponse,
    status_code=201,
    dependencies=[Depends(require_trusted_origin)],
)
async def register(
    credentials: Credentials,
    response: Response,
    session: DatabaseSession,
) -> UserResponse:
    email = str(credentials.email).casefold()
    existing_user = await session.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(email=email, password_hash=password_hash.hash(credentials.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from None
    await session.refresh(user)
    set_session_cookie(response, user.id)
    return UserResponse(id=user.id, email=user.email)


@app.post("/api/auth/login", response_model=UserResponse, dependencies=[Depends(require_trusted_origin)])
async def login(
    credentials: Credentials,
    response: Response,
    session: DatabaseSession,
) -> UserResponse:
    email = str(credentials.email).casefold()
    user = await session.scalar(select(User).where(User.email == email))
    valid_password = password_hash.verify(
        credentials.password,
        user.password_hash if user is not None else DUMMY_PASSWORD_HASH,
    )
    if user is None or not valid_password:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    set_session_cookie(response, user.id)
    return UserResponse(id=user.id, email=user.email)


@app.get("/api/auth/me", response_model=UserResponse)
async def current_user(user: CurrentUser) -> UserResponse:
    return UserResponse(id=user.id, email=user.email)


@app.post("/api/auth/logout", status_code=204, dependencies=[Depends(require_trusted_origin)])
async def logout(response: Response) -> Response:
    clear_session_cookie(response)
    response.status_code = 204
    return response


@app.get("/api/reports", response_model=list[ReportSummary])
async def list_reports(user: CurrentUser, session: DatabaseSession) -> list[ReportSummary]:
    reports = await session.scalars(
        select(AnalysisReport)
        .where(AnalysisReport.user_id == user.id)
        .order_by(AnalysisReport.created_at.desc())
        .limit(100)
    )
    summaries = []
    for report in reports:
        contract_type = report.result.get("contract_type")
        fairness_score = report.result.get("fairness_score")
        fairness_grade = report.result.get("fairness_grade")
        summaries.append(
            ReportSummary(
                id=report.id,
                filename=report.filename,
                contract_type=contract_type if isinstance(contract_type, str) else "unknown",
                fairness_score=fairness_score if isinstance(fairness_score, int) else 0,
                fairness_grade=fairness_grade if isinstance(fairness_grade, str) else "?",
                created_at=report.created_at,
            )
        )
    return summaries


@app.get("/api/reports/{report_id}", response_model=SavedReport)
async def get_report(
    report_id: uuid.UUID,
    user: CurrentUser,
    session: DatabaseSession,
) -> SavedReport:
    report = await session.scalar(
        select(AnalysisReport).where(
            AnalysisReport.id == report_id,
            AnalysisReport.user_id == user.id,
        )
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return SavedReport(
        id=report.id,
        filename=report.filename,
        result=AnalysisResult.model_validate(report.result),
        created_at=report.created_at,
    )


@app.post(
    "/api/analyze",
    response_model=AnalysisResult,
    dependencies=[Depends(require_trusted_origin)],
)
async def analyze_upload(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    api_key: str | None = Form(None),
    base_url: str | None = Form(None),
    lang: Literal["en", "zh"] = Form("en"),
    jurisdiction: Literal["auto", "cn", "us-ca"] = Form("auto"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_database_session),
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
        session.add(
            AnalysisReport(
                user_id=user.id,
                filename=Path(file.filename or "contract").name[:255],
                result=result.model_dump(mode="json"),
            )
        )
        await session.commit()
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
