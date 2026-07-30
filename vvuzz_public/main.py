from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import time
from collections import OrderedDict, deque
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import connection, table_exists
from .ingest import ingest_v2
from .repository import (
    LookupCache,
    archive_overview,
    campaign_for_year,
    campaigns,
    competition_details,
    lookup,
)
from .security import (
    audit,
    authenticate,
    create_session,
    digest,
    get_session,
    valid_csrf,
)


PACKAGE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=PACKAGE / "templates")


class SlidingRateLimiter:
    def __init__(self, *, key: bytes, requests: int, window_seconds: int) -> None:
        self.key = key
        self.requests = requests
        self.window_seconds = window_seconds
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        hashed = digest(identity, self.key)
        with self._lock:
            events = self._events.setdefault(hashed, deque())
            self._events.move_to_end(hashed)
            while events and events[0] <= now - self.window_seconds:
                events.popleft()
            if len(events) >= self.requests:
                return False
            events.append(now)
            while len(self._events) > 10_000:
                self._events.popitem(last=False)
            return True


def client_identity(request: Request) -> str:
    if request.client and request.client.host in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-real-ip", "").strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def valid_applicant_id(value: object) -> str:
    applicant_id = str(value or "").strip()
    if not applicant_id.isascii() or not applicant_id.isdigit() or not 4 <= len(applicant_id) <= 20:
        raise HTTPException(status_code=422, detail="ID должен содержать от 4 до 20 цифр")
    return applicant_id


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Vvuzz Public",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.lookup_cache = LookupCache(settings.cache_hmac_key)
    app.state.rate_limiter = SlidingRateLimiter(
        key=settings.cache_hmac_key,
        requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/public/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if request.url.path.startswith("/manage"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def ensure_ready() -> None:
        if not settings.db_path.exists():
            raise HTTPException(status_code=503, detail="База данных недоступна")
        with connection(settings.db_path, readonly=True) as conn:
            if not table_exists(conn, "campaigns"):
                raise HTTPException(status_code=503, detail="Миграция Vvuzz Public ещё не применена")

    def public_context() -> dict:
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            available = campaigns(conn)
        return {
            "campaigns": available,
            "active_campaign": next((row for row in available if row["status"] == "active"), available[0] if available else None),
            "base_path": settings.base_path,
        }

    def require_rate_limit(request: Request) -> None:
        if not app.state.rate_limiter.allow(client_identity(request)):
            raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")

    def current_session(request: Request):
        token = request.cookies.get(settings.session_cookie, "")
        with connection(settings.db_path) as conn:
            row = get_session(
                conn,
                token=token,
                hmac_key=settings.cache_hmac_key,
                idle_seconds=settings.session_idle_seconds,
            )
            conn.commit()
            return row

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        context = public_context()
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            {**context, "page": "home"},
        )

    @app.get("/archive", response_class=HTMLResponse)
    async def archive_page(request: Request):
        context = public_context()
        return TEMPLATES.TemplateResponse(
            request,
            "archive.html",
            {**context, "page": "archive"},
        )

    @app.post("/lookup", response_class=HTMLResponse)
    async def lookup_page(
        request: Request,
        year: int = Form(...),
        applicant_id: str = Form(...),
    ):
        started = time.perf_counter()
        require_rate_limit(request)
        applicant_id = valid_applicant_id(applicant_id)
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            campaign = campaign_for_year(conn, year)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Кампания не найдена")
            result = lookup(
                conn,
                campaign_id=campaign["id"],
                year=year,
                applicant_id=applicant_id,
                freshness=campaign["freshness_at"],
                limit=settings.lookup_limit,
                offset=0,
            )
        elapsed = (time.perf_counter() - started) * 1000
        remaining = settings.lookup_timing_floor_ms - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining / 1000)
        return TEMPLATES.TemplateResponse(
            request,
            "results.html",
            {"base_path": settings.base_path, "page": "home", "result": result},
            headers={
                "Cache-Control": "no-store",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )

    @app.get("/api/public/campaigns")
    async def campaigns_api():
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            return {"campaigns": campaigns(conn)}

    @app.post("/api/public/lookup")
    async def public_lookup(request: Request):
        started = time.perf_counter()
        require_rate_limit(request)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректный JSON") from exc
        applicant_id = valid_applicant_id(payload.get("applicant_id"))
        try:
            year = int(payload.get("year"))
            offset = int(payload.get("offset") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Некорректная кампания") from exc
        if not 0 <= offset <= 5000:
            raise HTTPException(status_code=422, detail="Некорректное смещение")
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            campaign = campaign_for_year(conn, year)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Кампания не найдена")
            marker = str(campaign["snapshot_id"] or campaign["freshness_at"] or "none")
            cache_key = app.state.lookup_cache.make_key(
                year, applicant_id, f"{marker}:{offset}"
            )
            result = app.state.lookup_cache.get(cache_key)
            if result is None:
                result = lookup(
                    conn,
                    campaign_id=campaign["id"],
                    year=year,
                    applicant_id=applicant_id,
                    freshness=campaign["freshness_at"],
                    limit=settings.lookup_limit,
                    offset=offset,
                )
                app.state.lookup_cache.put(cache_key, result)
        elapsed = (time.perf_counter() - started) * 1000
        remaining = settings.lookup_timing_floor_ms - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining / 1000)
        return result

    @app.post("/api/public/competition")
    async def public_competition(request: Request):
        require_rate_limit(request)
        payload = await request.json()
        applicant_id = valid_applicant_id(payload.get("applicant_id"))
        try:
            year = int(payload.get("year"))
            raw_ids = payload.get("competition_ids") or []
            competition_ids = list(dict.fromkeys(int(item) for item in raw_ids))[:5]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Некорректные параметры") from exc
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            campaign = campaign_for_year(conn, year)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Кампания не найдена")
            details = competition_details(
                conn,
                campaign_id=campaign["id"],
                applicant_id=applicant_id,
                competition_ids=competition_ids,
            )
        return {"campaign_year": year, "competitions": details}

    @app.get("/api/public/archive/{year}/overview")
    async def archive_api(year: int):
        ensure_ready()
        with connection(settings.db_path, readonly=True) as conn:
            overview = archive_overview(conn, year)
        if overview is None:
            raise HTTPException(status_code=404, detail="Кампания не найдена")
        return overview

    @app.get("/manage", response_class=HTMLResponse)
    async def manage(request: Request):
        ensure_ready()
        session = current_session(request)
        if session is None:
            return TEMPLATES.TemplateResponse(
                request,
                "manage_login.html",
                {"base_path": settings.base_path, "error": "", "page": "manage"},
            )
        csrf_token = request.cookies.get("vvuzz_csrf", "")
        if not valid_csrf(session, csrf_token, settings.cache_hmac_key):
            response = RedirectResponse(f"{settings.base_path}/manage", status_code=303)
            response.delete_cookie(settings.session_cookie, path=settings.base_path or "/")
            response.delete_cookie("vvuzz_csrf", path=settings.base_path or "/")
            return response
        with connection(settings.db_path, readonly=True) as conn:
            stats = campaigns(conn)
            recent_audit = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT event_type, outcome, created_at
                    FROM admin_audit
                    ORDER BY id DESC
                    LIMIT 20
                    """
                )
            ]
        return TEMPLATES.TemplateResponse(
            request,
            "manage_dashboard.html",
            {
                "base_path": settings.base_path,
                "page": "manage",
                "session": session,
                "csrf_token": csrf_token,
                "campaigns": stats,
                "audit": recent_audit,
            },
        )

    @app.post("/manage/login")
    async def manage_login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        ensure_ready()
        with connection(settings.db_path) as conn:
            user = authenticate(conn, username=username.strip(), password=password)
            if user is None:
                conn.commit()
                return TEMPLATES.TemplateResponse(
                    request,
                    "manage_login.html",
                    {
                        "base_path": settings.base_path,
                        "error": "Неверные данные или вход временно заблокирован.",
                        "page": "manage",
                    },
                    status_code=401,
                )
            session = create_session(
                conn,
                user_id=user["id"],
                hmac_key=settings.cache_hmac_key,
                ttl_seconds=settings.session_ttl_seconds,
                user_agent=request.headers.get("user-agent", ""),
                ip_prefix=client_identity(request),
            )
            conn.commit()
        path = settings.base_path or "/"
        secure = settings.environment == "production"
        response = RedirectResponse(f"{settings.base_path}/manage", status_code=303)
        response.set_cookie(
            settings.session_cookie,
            session.token,
            max_age=settings.session_ttl_seconds,
            path=path,
            secure=secure,
            httponly=True,
            samesite="strict",
        )
        response.set_cookie(
            "vvuzz_csrf",
            session.csrf_token,
            max_age=settings.session_ttl_seconds,
            path=path,
            secure=secure,
            httponly=False,
            samesite="strict",
        )
        return response

    @app.post("/manage/logout")
    async def manage_logout(request: Request, csrf_token: str = Form(...)):
        ensure_ready()
        token = request.cookies.get(settings.session_cookie, "")
        with connection(settings.db_path) as conn:
            session = get_session(
                conn,
                token=token,
                hmac_key=settings.cache_hmac_key,
                idle_seconds=settings.session_idle_seconds,
            )
            if session is None or not valid_csrf(session, csrf_token, settings.cache_hmac_key):
                raise HTTPException(status_code=403, detail="CSRF-проверка не пройдена")
            conn.execute("DELETE FROM auth_sessions WHERE id_hash = ?", (session["id_hash"],))
            audit(conn, event_type="logout", outcome="success", user_id=session["user_id"])
            conn.commit()
        path = settings.base_path or "/"
        response = RedirectResponse(f"{settings.base_path}/manage", status_code=303)
        response.delete_cookie(settings.session_cookie, path=path)
        response.delete_cookie("vvuzz_csrf", path=path)
        return response

    @app.post("/api/ingest/v2/batch")
    async def ingest_batch_v2(request: Request):
        if not settings.ingest_token:
            raise HTTPException(status_code=503, detail="Ingest v2 не настроен")
        supplied = request.headers.get("x-vvuzz-ingest-token", "")
        if not hmac.compare_digest(supplied, settings.ingest_token):
            raise HTTPException(status_code=401, detail="Неверный ingest token")
        payload = await request.json()
        ensure_ready()
        try:
            with connection(settings.db_path) as conn:
                response_status, result = ingest_v2(conn, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(result, status_code=response_status)

    @app.get("/healthz")
    async def healthz():
        ensure_ready()
        return {"status": "ok", "time": now_iso()}

    return app


app = create_app()
