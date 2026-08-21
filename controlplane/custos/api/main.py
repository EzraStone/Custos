"""ASGI entry point.

    uvicorn custos.api.main:app --host 0.0.0.0 --port 8080

Configuration is environmental so the same image runs in every environment:

    CUSTOS_DB       path to the SQLite database (default: custos.db)
    CUSTOS_TOKENS   account:token pairs, comma separated
"""

from __future__ import annotations

import os

from ..store.db import open_database
from .app import create_app
from .auth import TokenStore

app = create_app(
    conn=open_database(os.getenv("CUSTOS_DB", "custos.db")),
    tokens=TokenStore.from_env(),
)
