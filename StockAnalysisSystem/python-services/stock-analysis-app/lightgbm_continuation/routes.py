"""Authenticated continuation requests. Model training runs only in the worker."""
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from api.auth.dependencies import require_admin, require_csrf
from tasks.contracts import Conflict, idempotency
from tasks.routes import query
from workflows.routes import body
from .contracts import KIND, normalize


def _submit(service, actor, request, key):
    try:
        return service.create(actor, request, key)
    except PermissionError:
        raise HTTPException(403, "Access denied") from None
    except Conflict:
        raise HTTPException(409, "Task conflict or comparison window already reserved") from None
    except (LookupError, ValueError):
        raise HTTPException(422, "Parent or verified continuation samples not ready") from None
    except Exception:
        raise HTTPException(503, "Continuation dependencies unavailable") from None


def register_continuation_routes(app):
    async def guard(request, *, write=False):
        if app.state.auth_settings.mode != "protected":
            raise HTTPException(503, "Protected mode required")
        actor = await run_in_threadpool(require_admin, request)
        if write:
            await run_in_threadpool(require_csrf, request)
        query(request, set())
        service = app.state.task_service
        if service is None:
            raise HTTPException(503, "Continuation dependencies unavailable")
        return service, actor

    @app.get("/api/admin/lightgbm-continuations")
    async def parents(request: Request):
        service, _ = await guard(request)
        return await run_in_threadpool(_parents, service)

    @app.post("/api/admin/lightgbm-continuations", status_code=201)
    async def create(request: Request):
        service, actor = await guard(request, write=True)
        value = await body(request)
        try:
            keys = request.headers.getlist("idempotency-key")
            if len(keys) != 1 or "kind" in value:
                raise ValueError("Invalid continuation request")
            key = idempotency(keys[0])
            value = normalize(dict(value, kind=KIND))
        except ValueError:
            raise HTTPException(400, "Invalid continuation request") from None
        return await run_in_threadpool(_submit, service, actor["id"], value, key)


def _parents(service):
    from sqlalchemy import select
    from prediction_portal.repository import releases
    from .source import ContinuationSource
    try:
        with service.repo.engine.connect() as connection:
            ids = connection.execute(select(releases.c.model_id).where(
                releases.c.enabled.is_(True)).order_by(releases.c.model_id).limit(20)).scalars().all()
        source = ContinuationSource(service.repo.engine, service.source.calendar_path)
        items = []
        for id in ids:
            try:
                row, meta, _, _ = source.parent(id)
            except (LookupError, ValueError, TypeError, PermissionError, FileNotFoundError):
                continue
            native = meta["native"]
            items.append(dict(id=id, title=row["title"], seen_through=native["seen_through"],
                              pool_count=native["pool_count"], feature_count=len(native["feature_names"])))
        return dict(items=items)
    except Exception:
        raise HTTPException(503, "Continuation dependencies unavailable") from None
