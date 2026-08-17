"""FastAPI app factory + lifespan.

`create_app()` builds the app; `app = create_app()` at module scope is what
`uvicorn app.main:app` runs. Both `checkpointer` and `load_env` are consumed
inside the lifespan, not at construction, so *importing* this module stays
side-effect-free (no filesystem writes, no open connections - matches
invoice_agent/graph.py's get_graph() convention). Tests build their own app
via `create_app(checkpointer=..., load_env=False)` without ever touching the
real .env file - see tests/test_api_routes.py.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.errors import register_exception_handlers
from app.service import DEFAULT_MAX_CONCURRENCY, GraphService
from invoice_agent import tracing
from invoice_agent.graph import build_graph

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT_DB_PATH = REPO_ROOT / "checkpoints" / "graph.sqlite"
DEFAULT_UPLOAD_DIR = REPO_ROOT / "uploads"


def _checkpoint_db_path() -> Path:
    raw = os.environ.get("CHECKPOINT_DB_PATH")
    return Path(raw) if raw else DEFAULT_CHECKPOINT_DB_PATH


def _upload_dir() -> Path:
    raw = os.environ.get("UPLOAD_DIR")
    return Path(raw) if raw else DEFAULT_UPLOAD_DIR


def _max_concurrency() -> int:
    raw = os.environ.get("GRAPH_MAX_CONCURRENCY")
    if not raw:
        return DEFAULT_MAX_CONCURRENCY
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY


def create_app(*, checkpointer: Optional[BaseCheckpointSaver] = None, load_env: bool = True) -> FastAPI:
    """Build the FastAPI app.

    `checkpointer`: inject a test double (e.g. `SqliteSaver(sqlite3.connect(
    ":memory:", check_same_thread=False))`) to skip opening the real on-disk
    DB. When None (the default, and what `uvicorn app.main:app` uses), the
    lifespan opens `checkpoints/graph.sqlite` (or `$CHECKPOINT_DB_PATH`)
    itself - NEVER `invoice_agent.graph.get_graph()`'s module singleton (see
    app/service.py's `GraphService` docstring for why: two SqliteSaver
    instances over two connections have two different internal locks).

    `load_env`: whether the lifespan calls `load_dotenv()`. False for tests
    - `TestClient(app)` runs the lifespan as a context manager, and this
    repo's `.env` holds real credentials; a test that doesn't need them must
    not pull them into `os.environ`.
    """
    owns_connection = checkpointer is None
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if load_env:
            from dotenv import load_dotenv

            load_dotenv()

        saver = checkpointer
        if saver is None:
            db_path = _checkpoint_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            state["conn"] = conn
            saver = SqliteSaver(conn)

        upload_dir = _upload_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)

        graph = build_graph(saver)
        app.state.graph_service = GraphService(graph, max_concurrency=_max_concurrency())
        app.state.upload_dir = upload_dir

        try:
            yield
        finally:
            # Long-running process - deliberately no per-request flush()
            # (see invoice_agent/tracing.py / docs/observability.md); only
            # here, on shutdown, so buffered spans aren't lost on SIGTERM.
            tracing.flush()
            if owns_connection:
                conn = state.get("conn")
                if conn is not None:
                    conn.close()

    app = FastAPI(title="Invoice Agent API", lifespan=lifespan)
    register_exception_handlers(app)

    from app.routes import router

    app.include_router(router)

    return app


app = create_app()
