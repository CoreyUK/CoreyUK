"""FastAPI application: JSON API plus the static single-page UI."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .cache import ResultCache
from .config import Settings
from .fetcher import Fetcher
from .models import RetailerInfo, SearchResponse
from .ratelimit import MinIntervalGate, SlidingWindowLimiter
from .scrapers import RETAILERS, enabled_retailers
from .search import SearchService, normalise_query

STATIC_DIR = Path(__file__).parent / "static"

# Convenience categories surfaced in the UI as one-click searches.
CATEGORIES = [
    {"label": "CPUs", "query": "ryzen 7 9800x3d"},
    {"label": "Graphics cards", "query": "rtx 5070"},
    {"label": "NVMe SSDs", "query": "2tb nvme ssd"},
    {"label": "DDR5 RAM", "query": "32gb ddr5 6000"},
    {"label": "Motherboards", "query": "b850 motherboard"},
    {"label": "Power supplies", "query": "850w psu"},
    {"label": "CPU coolers", "query": "360mm aio"},
    {"label": "Cases", "query": "atx case"},
]


def create_app(settings: Settings | None = None, *, fetcher: Fetcher | None = None, cache: ResultCache | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.fetcher = fetcher or Fetcher(settings)
        app.state.cache = cache or ResultCache(settings.cache_db_path, settings.cache_ttl_seconds)
        app.state.service = SearchService(settings, app.state.fetcher, app.state.cache, enabled_retailers(settings))
        app.state.limiter = SlidingWindowLimiter(settings.api_rate_limit_per_minute, 60.0)
        app.state.refresh_gate = MinIntervalGate(settings.force_refresh_min_interval_seconds)
        app.state.cache.purge_sync(max_age_seconds=7 * 24 * 3600)
        try:
            yield
        finally:
            await app.state.fetcher.close()
            app.state.cache.close()

    app = FastAPI(title="UK PC Parts Price Search", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def api_rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/search"):
            allowed, remaining, retry_after = request.app.state.limiter.check(client_key(request))
            if not allowed:
                return JSONResponse(
                    {"detail": "Too many searches, slow down a little.", "retry_after": round(retry_after, 1)},
                    status_code=429,
                    headers={"Retry-After": str(int(retry_after) + 1), "X-RateLimit-Remaining": "0"},
                )
            response: Response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(request.app.state.limiter.limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        return {"status": "ok", "time": time.time(), "retailers": len(request.app.state.service.retailers)}

    @app.get("/api/retailers", response_model=list[RetailerInfo])
    async def retailers(request: Request) -> list[RetailerInfo]:
        cfg: Settings = request.app.state.settings
        return [RetailerInfo(id=r.id, name=r.name, homepage=r.homepage, enabled=cfg.retailer_enabled(r.id)) for r in RETAILERS]

    @app.get("/api/categories")
    async def categories() -> list[dict[str, str]]:
        return CATEGORIES

    @app.get("/api/search", response_model=SearchResponse)
    async def search(
        request: Request,
        q: str = Query(..., min_length=1),
        retailers: str | None = Query(None, description="Comma-separated retailer ids"),
        refresh: bool = Query(False, description="Bypass the cache (rate limited per query)"),
    ) -> SearchResponse:
        cfg: Settings = request.app.state.settings
        query = normalise_query(q)
        if not query:
            raise HTTPException(status_code=400, detail="Enter something to search for.")
        if len(query) > cfg.max_query_length:
            raise HTTPException(status_code=400, detail=f"Query too long (max {cfg.max_query_length} characters).")
        service: SearchService = request.app.state.service
        known = {r.id for r in service.retailers}
        ids: list[str] | None = None
        if retailers:
            ids = [r.strip().lower() for r in retailers.split(",") if r.strip()]
            unknown = [r for r in ids if r not in known]
            if unknown:
                raise HTTPException(status_code=400, detail=f"Unknown retailer(s): {', '.join(unknown)}")
        force = False
        if refresh:
            force, _wait = request.app.state.refresh_gate.allow(query)
        return await service.search(query, ids, force=force)

    @app.get("/api/history")
    async def history(request: Request, retailer: str = Query(...), url: str = Query(..., max_length=2048)) -> dict[str, object]:
        cache: ResultCache = request.app.state.cache
        return {"retailer": retailer, "url": url, "points": await cache.history(retailer, url)}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
