"""FastAPI upload endpoint for the React ContractGuard analyst."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from openai import OpenAIError
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import func, select
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
    Subscription,
    User,
    WorkspaceSettings,
    close_database,
    get_database_session,
    initialize_database,
)
from backend.paypal import (
    PayPalError,
    cancel_subscription as paypal_cancel_subscription,
    create_subscription as paypal_create_subscription,
    get_plan as paypal_get_plan,
    get_subscription as paypal_get_subscription,
    validate_monthly_plan,
    verify_webhook as paypal_verify_webhook,
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
PLAN_DEFINITIONS: dict[str, dict[str, object]] = {
    "free": {
        "name": "Free",
        "price": "$0",
        "monthly_limit": 3,
        "description": "Try Contract Analyst with three analyses each month.",
    },
    "pro": {
        "name": "Pro",
        "price": "$19/month",
        "monthly_price": "19.00",
        "monthly_limit": 50,
        "description": "For individuals reviewing contracts regularly.",
    },
    "business": {
        "name": "Business",
        "price": "$49/month",
        "monthly_price": "49.00",
        "monthly_limit": 250,
        "description": "Higher monthly capacity for professional use.",
    },
}
PAYPAL_PLAN_ENV = {
    "pro": "PAYPAL_PRO_PLAN_ID",
    "business": "PAYPAL_BUSINESS_PLAN_ID",
}

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


class BillingPlan(BaseModel):
    key: Literal["free", "pro", "business"]
    name: str
    price: str
    monthly_limit: int
    description: str
    available: bool


class BillingResponse(BaseModel):
    current_plan_key: Literal["free", "pro", "business"]
    current_plan_name: str
    subscription_status: str
    pending_plan_key: Literal["pro", "business"] | None
    monthly_usage: int
    monthly_limit: int
    next_billing_time: datetime | None
    paypal_enabled: bool
    plans: list[BillingPlan]


class CheckoutRequest(BaseModel):
    plan_key: Literal["pro", "business"]


class CheckoutResponse(BaseModel):
    approval_url: str


class ConfirmSubscriptionRequest(BaseModel):
    subscription_id: str = Field(min_length=6, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def require_trusted_origin(request: Request) -> None:
    if request.headers.get("origin") not in cors_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


async def _get_active_subscription(
    user_id: uuid.UUID,
    session: AsyncSession,
) -> Subscription | None:
    return await session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == "ACTIVE")
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )


async def _monthly_usage(user_id: uuid.UUID, session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = month_start.replace(year=now.year + 1, month=1)
    else:
        next_month = month_start.replace(month=now.month + 1)
    usage = await session.scalar(
        select(func.count())
        .select_from(AnalysisReport)
        .where(
            AnalysisReport.user_id == user_id,
            AnalysisReport.created_at >= month_start,
            AnalysisReport.created_at < next_month,
        )
    )
    return int(usage or 0)


async def _billing_response(user_id: uuid.UUID, session: AsyncSession) -> BillingResponse:
    subscription = await _get_active_subscription(user_id, session)
    latest_subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    plan_key = subscription.plan_key if subscription is not None else "free"
    plan = PLAN_DEFINITIONS[plan_key]
    usage = await _monthly_usage(user_id, session)
    paypal_enabled = bool(
        (os.getenv("PAYPAL_CLIENT_ID") or "").strip()
        and (os.getenv("PAYPAL_CLIENT_SECRET") or "").strip()
    )
    plans = [
        BillingPlan(
            key=key,
            name=str(definition["name"]),
            price=str(definition["price"]),
            monthly_limit=int(definition["monthly_limit"]),
            description=str(definition["description"]),
            available=(
                key == "free"
                or (
                    paypal_enabled
                    and bool((os.getenv(PAYPAL_PLAN_ENV[key]) or "").strip())
                )
            ),
        )
        for key, definition in PLAN_DEFINITIONS.items()
    ]
    return BillingResponse(
        current_plan_key=plan_key,
        current_plan_name=str(plan["name"]),
        subscription_status=(
            subscription.status if subscription is not None
            else latest_subscription.status if latest_subscription is not None
            else "FREE"
        ),
        pending_plan_key=(
            latest_subscription.plan_key
            if latest_subscription is not None
            and latest_subscription.status in {"APPROVAL_PENDING", "APPROVED"}
            else None
        ),
        monthly_usage=usage,
        monthly_limit=int(plan["monthly_limit"]),
        next_billing_time=subscription.next_billing_time if subscription is not None else None,
        paypal_enabled=paypal_enabled,
        plans=plans,
    )


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


def _approval_url(payload: dict[str, object]) -> str | None:
    links = payload.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if (
            isinstance(link, dict)
            and link.get("rel") == "approve"
            and isinstance(link.get("href"), str)
        ):
            return link["href"]
    return None


def _paypal_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


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


@app.get("/api/billing", response_model=BillingResponse)
async def get_billing(user: CurrentUser, session: DatabaseSession) -> BillingResponse:
    return await _billing_response(user.id, session)


@app.post(
    "/api/billing/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def start_subscription_checkout(
    request: CheckoutRequest,
    user: CurrentUser,
    session: DatabaseSession,
) -> CheckoutResponse:
    plan_id = (os.getenv(PAYPAL_PLAN_ENV[request.plan_key]) or "").strip()
    if not plan_id:
        raise HTTPException(
            status_code=503,
            detail=f"PayPal is not configured for the {request.plan_key} plan.",
        )

    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise HTTPException(status_code=401, detail="Your account no longer exists.")
    if await _get_active_subscription(user.id, session) is not None:
        raise HTTPException(status_code=409, detail="Cancel your current subscription before changing plans.")

    pending = await session.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status.in_(("APPROVAL_PENDING", "APPROVED")),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    if pending is not None:
        if pending.plan_key != request.plan_key:
            raise HTTPException(
                status_code=409,
                detail="Finish or cancel your existing PayPal checkout before choosing another plan.",
            )
        if pending.approval_url:
            return CheckoutResponse(approval_url=pending.approval_url)
        raise HTTPException(
            status_code=409,
            detail="A PayPal checkout is already pending. Complete it or contact support.",
        )

    app_base_url = (os.getenv("APP_BASE_URL") or "http://localhost:5173").rstrip("/")
    app_origin = urlsplit(app_base_url)
    if (os.getenv("PAYPAL_MODE") or "sandbox").strip().lower() == "live" and (
        app_origin.scheme != "https"
        or (os.getenv("SESSION_COOKIE_SECURE") or "").lower() not in {"1", "true", "yes"}
    ):
        raise HTTPException(
            status_code=503,
            detail="Live PayPal billing requires an HTTPS APP_BASE_URL and SESSION_COOKIE_SECURE=true.",
        )
    try:
        paypal_plan = await paypal_get_plan(plan_id)
        validate_monthly_plan(paypal_plan, str(PLAN_DEFINITIONS[request.plan_key]["monthly_price"]))
        paypal_subscription = await paypal_create_subscription(
            plan_id=plan_id,
            user_id=str(user.id),
            return_url=f"{app_base_url}/?billing=success",
            cancel_url=f"{app_base_url}/?billing=cancelled",
        )
    except PayPalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    subscription_id = paypal_subscription.get("id")
    approval_url = _approval_url(paypal_subscription)
    if not isinstance(subscription_id, str) or not approval_url:
        raise HTTPException(status_code=502, detail="PayPal did not provide a valid subscription checkout link.")
    approval_target = urlsplit(approval_url)
    if (
        approval_target.scheme != "https"
        or approval_target.hostname not in {"www.paypal.com", "www.sandbox.paypal.com"}
    ):
        raise HTTPException(status_code=502, detail="PayPal returned an unsafe checkout URL.")
    session.add(
        Subscription(
            user_id=user.id,
            plan_key=request.plan_key,
            paypal_subscription_id=subscription_id,
            approval_url=approval_url,
            status="APPROVAL_PENDING",
        )
    )
    await session.commit()
    return CheckoutResponse(approval_url=approval_url)


@app.post(
    "/api/billing/confirm",
    response_model=BillingResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def confirm_subscription(
    request: ConfirmSubscriptionRequest,
    user: CurrentUser,
    session: DatabaseSession,
) -> BillingResponse:
    try:
        paypal_subscription = await paypal_get_subscription(request.subscription_id)
    except PayPalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if paypal_subscription.get("custom_id") != str(user.id):
        raise HTTPException(status_code=403, detail="This PayPal subscription does not belong to your account.")
    if paypal_subscription.get("status") != "ACTIVE":
        raise HTTPException(
            status_code=409,
            detail="PayPal has not activated this subscription yet. Please try again shortly.",
        )
    plan_id = paypal_subscription.get("plan_id")
    matching_plan = next(
        (
            key
            for key, env_name in PAYPAL_PLAN_ENV.items()
            if isinstance(plan_id, str) and plan_id == (os.getenv(env_name) or "").strip()
        ),
        None,
    )
    if matching_plan is None:
        raise HTTPException(status_code=400, detail="This PayPal subscription uses an unrecognized plan.")
    try:
        plan_details = await paypal_get_plan(plan_id)
        validate_monthly_plan(
            plan_details,
            str(PLAN_DEFINITIONS[matching_plan]["monthly_price"]),
        )
    except PayPalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise HTTPException(status_code=401, detail="Your account no longer exists.")
    existing = await session.scalar(
        select(Subscription).where(
            Subscription.paypal_subscription_id == request.subscription_id,
            Subscription.user_id == user.id,
        )
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="No checkout was found for this PayPal subscription.")
    if existing.plan_key != matching_plan:
        raise HTTPException(status_code=400, detail="The PayPal subscription plan does not match your checkout.")
    active_subscription = await _get_active_subscription(user.id, session)
    if active_subscription is not None and active_subscription.id != existing.id:
        raise HTTPException(status_code=409, detail="Another subscription is already active on your account.")

    billing_info = paypal_subscription.get("billing_info")
    next_billing_time = (
        _paypal_datetime(billing_info.get("next_billing_time"))
        if isinstance(billing_info, dict)
        else None
    )
    existing.status = "ACTIVE"
    existing.approval_url = None
    existing.next_billing_time = next_billing_time
    await session.commit()
    return await _billing_response(user.id, session)


@app.post(
    "/api/billing/cancel",
    response_model=BillingResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def cancel_current_subscription(
    user: CurrentUser,
    session: DatabaseSession,
) -> BillingResponse:
    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise HTTPException(status_code=401, detail="Your account no longer exists.")
    subscription = await _get_active_subscription(user.id, session)
    if subscription is None:
        raise HTTPException(status_code=404, detail="There is no active subscription to cancel.")
    try:
        await paypal_cancel_subscription(
            subscription.paypal_subscription_id,
            "Cancellation requested by the account holder.",
        )
        paypal_subscription = await paypal_get_subscription(subscription.paypal_subscription_id)
    except PayPalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    status = paypal_subscription.get("status")
    if not isinstance(status, str) or status not in {"CANCELLED", "SUSPENDED", "EXPIRED"}:
        raise HTTPException(status_code=502, detail="PayPal did not confirm the subscription cancellation.")
    subscription.status = status
    subscription.next_billing_time = None
    subscription.last_webhook_event_time = datetime.now(timezone.utc)
    await session.commit()
    return await _billing_response(user.id, session)


@app.post("/api/billing/webhook")
async def paypal_webhook(request: Request, session: DatabaseSession) -> dict[str, bool]:
    try:
        event = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON.") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook event.")
    try:
        verified = await paypal_verify_webhook(dict(request.headers), event)
    except PayPalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not verified:
        raise HTTPException(status_code=400, detail="PayPal webhook signature could not be verified.")

    event_type = event.get("event_type")
    resource = event.get("resource")
    if not isinstance(event_type, str) or not isinstance(resource, dict):
        return {"received": True}
    subscription_id = resource.get("id")
    if not isinstance(subscription_id, str):
        return {"received": True}
    status_by_event = {
        "BILLING.SUBSCRIPTION.ACTIVATED": "ACTIVE",
        "BILLING.SUBSCRIPTION.CANCELLED": "CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED": "SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED": "EXPIRED",
    }
    status = status_by_event.get(event_type)
    if event_type == "BILLING.SUBSCRIPTION.UPDATED":
        resource_status = resource.get("status")
        status = resource_status if isinstance(resource_status, str) else None
    if status not in {"ACTIVE", "CANCELLED", "SUSPENDED", "EXPIRED"}:
        return {"received": True}
    event_time = _paypal_datetime(event.get("create_time"))
    if event_time is None:
        raise HTTPException(status_code=400, detail="PayPal subscription event has no valid creation time.")

    subscription = await session.scalar(
        select(Subscription).where(Subscription.paypal_subscription_id == subscription_id)
    )
    if (
        subscription is not None
        and (
            subscription.last_webhook_event_time is None
            or event_time > subscription.last_webhook_event_time
        )
    ):
        subscription.status = status
        subscription.last_webhook_event_time = event_time
        billing_info = resource.get("billing_info")
        if isinstance(billing_info, dict):
            subscription.next_billing_time = _paypal_datetime(billing_info.get("next_billing_time"))
        await session.commit()
    return {"received": True}


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
        locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        if locked_user is None:
            raise HTTPException(status_code=401, detail="Your account no longer exists.")
        subscription = await _get_active_subscription(user.id, session)
        plan_key = subscription.plan_key if subscription is not None else "free"
        monthly_limit = int(PLAN_DEFINITIONS[plan_key]["monthly_limit"])
        usage = await _monthly_usage(user.id, session)
        if usage >= monthly_limit:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"You have used all {monthly_limit} analyses included in your "
                    f"{PLAN_DEFINITIONS[plan_key]['name']} plan this month. Upgrade your plan to continue."
                ),
            )

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
