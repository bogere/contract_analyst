"""FastAPI upload endpoint for the React ContractGuard analyst."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from openai import OpenAIError
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.concurrency import run_in_threadpool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from backend.auth import (  # noqa: E402
    Credentials,
    AdminUser,
    CurrentUser,
    DatabaseSession,
    DUMMY_PASSWORD_HASH,
    UserResponse,
    clear_session_cookie,
    get_current_user,
    is_admin_user,
    password_hash,
    set_session_cookie,
    validate_auth_configuration,
)
from backend.database import (  # noqa: E402
    AnalysisReport,
    User,
    WorkspaceSettings,
    close_database,
    get_database_session,
    initialize_database,
)
from backend.ocr import (
    IMAGE_EXTENSIONS,
    OCRPageLimitError,
    OCRUnavailableError,
    extract_image_text,
    extract_pdf_text,
)
from contractguard.analyzer import analyze_contract
from contractguard.checklist import run_checklist
from contractguard.models import AnalysisResult
from contractguard.parser import extract_text

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".rtf", *IMAGE_EXTENSIONS}
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
    allow_methods=["GET", "POST", "PUT"],
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


class AdminUserSummary(BaseModel):
    id: uuid.UUID
    email: EmailStr
    is_admin: bool
    created_at: datetime


class AdminSettingsUpdate(BaseModel):
    model: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=512, repr=False)
    clear_api_key: bool = False
    language: Literal["en", "zh"] = "en"
    jurisdiction: Literal["auto", "cn", "us-ca"] = "auto"

    @field_validator("model", "base_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else ""
        return normalized or None

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        normalized = value.get_secret_value().strip()
        return SecretStr(normalized) if normalized else None


class AdminSettingsResponse(BaseModel):
    model: str | None
    effective_model: str
    base_url: str | None
    has_saved_api_key: bool
    has_environment_api_key: bool
    language: Literal["en", "zh"]
    jurisdiction: Literal["auto", "cn", "us-ca"]


def require_trusted_origin(request: Request) -> None:
    if request.headers.get("origin") not in cors_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Report whether the API process is responding."""
    return {"status": "ok"}


def _user_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, is_admin=is_admin_user(user))


def _settings_cipher() -> Fernet:
    secret = os.environ["AUTH_SECRET_KEY"].encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def _encrypt_api_key(api_key: str) -> str:
    return _settings_cipher().encrypt(api_key.encode("utf-8")).decode("ascii")


def _decrypt_api_key(encrypted_api_key: str) -> str:
    try:
        return _settings_cipher().decrypt(encrypted_api_key.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError):
        raise HTTPException(
            status_code=500,
            detail="The saved AI provider key cannot be decrypted. Re-enter it in admin settings.",
        ) from None


def _admin_settings_response(settings: WorkspaceSettings | None) -> AdminSettingsResponse:
    environment_api_key = any(
        (os.getenv(name) or "").strip()
        for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
    )
    return AdminSettingsResponse(
        model=settings.model if settings is not None else None,
        effective_model=(settings.model if settings and settings.model else DEFAULT_MODEL),
        base_url=settings.base_url if settings is not None else None,
        has_saved_api_key=bool(settings and settings.encrypted_api_key),
        has_environment_api_key=environment_api_key,
        language=settings.language if settings is not None else "en",
        jurisdiction=settings.jurisdiction if settings is not None else "auto",
    )


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
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().casefold()
    if admin_email:
        admin_exists = await session.scalar(select(User.id).where(User.email == admin_email))
        if admin_exists is None:
            raise HTTPException(
                status_code=503,
                detail="Create the initial admin account before setting ADMIN_EMAIL.",
            )
        raise HTTPException(
            status_code=403,
            detail="New accounts must be created by an administrator.",
        )
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
    return _user_response(user)


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
    return _user_response(user)


@app.get("/api/auth/me", response_model=UserResponse)
async def current_user(user: CurrentUser) -> UserResponse:
    return _user_response(user)


@app.get("/api/admin/users", response_model=list[AdminUserSummary])
async def list_users(
    _: AdminUser,
    session: DatabaseSession,
) -> list[AdminUserSummary]:
    users = await session.scalars(select(User).order_by(User.created_at.desc()).limit(200))
    return [
        AdminUserSummary(
            id=user.id,
            email=user.email,
            is_admin=is_admin_user(user),
            created_at=user.created_at,
        )
        for user in users
    ]


@app.post(
    "/api/admin/users",
    response_model=AdminUserSummary,
    status_code=201,
    dependencies=[Depends(require_trusted_origin)],
)
async def create_user(
    credentials: Credentials,
    _: AdminUser,
    session: DatabaseSession,
) -> AdminUserSummary:
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
    return AdminUserSummary(
        id=user.id,
        email=user.email,
        is_admin=is_admin_user(user),
        created_at=user.created_at,
    )


@app.get("/api/admin/settings", response_model=AdminSettingsResponse)
async def get_admin_settings(
    _: AdminUser,
    session: DatabaseSession,
) -> AdminSettingsResponse:
    settings = await session.get(WorkspaceSettings, "global")
    return _admin_settings_response(settings)


@app.put(
    "/api/admin/settings",
    response_model=AdminSettingsResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def update_admin_settings(
    update: AdminSettingsUpdate,
    _: AdminUser,
    session: DatabaseSession,
) -> AdminSettingsResponse:
    current = await session.get(WorkspaceSettings, "global")
    if update.clear_api_key:
        encrypted_api_key = None
    elif update.api_key is not None:
        encrypted_api_key = _encrypt_api_key(update.api_key.get_secret_value())
    else:
        encrypted_api_key = current.encrypted_api_key if current is not None else None

    values = {
        "id": "global",
        "model": update.model,
        "base_url": update.base_url,
        "encrypted_api_key": encrypted_api_key,
        "language": update.language,
        "jurisdiction": update.jurisdiction,
    }
    statement = pg_insert(WorkspaceSettings).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[WorkspaceSettings.id],
        set_={
            "model": statement.excluded.model,
            "base_url": statement.excluded.base_url,
            "encrypted_api_key": statement.excluded.encrypted_api_key,
            "language": statement.excluded.language,
            "jurisdiction": statement.excluded.jurisdiction,
        },
    )
    await session.execute(statement)
    await session.commit()
    settings = await session.get(WorkspaceSettings, "global")
    return _admin_settings_response(settings)


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
                detail="Unsupported file type. Upload a PDF, DOCX, TXT, MD, RTF, BMP, JPEG, PNG, TIFF, or WebP document.",
            )

        settings = await session.get(WorkspaceSettings, "global")
        effective_api_key = (
            _decrypt_api_key(settings.encrypted_api_key)
            if settings is not None and settings.encrypted_api_key
            else None
        )
        if not effective_api_key and not any(
            (os.getenv(name) or "").strip()
            for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
        ):
            raise HTTPException(
                status_code=503,
                detail="No LLM API key configured. Set one in admin AI settings or configure OPENAI_API_KEY / OPENROUTER_API_KEY in .env.",
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

        if suffix in IMAGE_EXTENSIONS:
            try:
                text = await run_in_threadpool(extract_image_text, temp_path)
            except OCRUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except OCRPageLimitError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
        elif suffix == ".pdf":
            try:
                text = await run_in_threadpool(extract_pdf_text, temp_path)
            except OCRUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except OCRPageLimitError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
        else:
            text = await run_in_threadpool(extract_text, temp_path)
        if len(text.strip()) < 50:
            raise HTTPException(
                status_code=422,
                detail="Contract text is too short. Please upload a complete, readable document.",
            )

        try:
            result = await run_in_threadpool(
                analyze_contract,
                contract_text=text,
                model=settings.model if settings and settings.model else DEFAULT_MODEL,
                api_key=effective_api_key,
                base_url=(
                    settings.base_url if settings and settings.base_url
                    else (os.getenv("OPENAI_BASE_URL") or "").strip() or None
                ),
                lang=settings.language if settings is not None else "en",
            )
        except (OpenAIError, RuntimeError, AttributeError, IndexError) as exc:
            raise HTTPException(status_code=502, detail=f"Contract analysis failed: {exc}") from exc

        result.statute_checks = run_checklist(
            text,
            result.contract_type.value,
            settings.language if settings is not None else "en",
            jurisdiction=settings.jurisdiction if settings is not None else "auto",
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
