# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Alembic environment (spec #44 Phase 5).

Resolves the target database from ``DATABASE_URL`` — the same variable the
application's repository factory switches on — so ``alembic upgrade head``
migrates exactly the database the app will use.

Relationship to ``postgres_schema.ensure_schema``: the initial revision
executes the SAME ``SCHEMA_SQL`` (single source of truth, all DDL is
``IF NOT EXISTS``), so either bootstrap path converges on the same schema.
Dev/test environments may keep using ``ensure_schema``; deployments should
run ``alembic upgrade head`` so future schema changes are versioned. A
database bootstrapped via ``ensure_schema`` can adopt alembic with
``alembic stamp head``.
"""

from __future__ import annotations

import os

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load the SAME .env the application loads (utils/config.py does this inside
# load_config): a deployment configured entirely via .env must migrate the
# same database — and revision 0001 must see the same EMBEDDING_MODEL for
# the pgvector column width — that the app will use. Reuses the app's
# discovery helper rather than duplicating the walk (FM-6); real environment
# variables still take precedence (load_dotenv does not override).
from thestill.utils.config import _find_dotenv_from_package

_dotenv = _find_dotenv_from_package()
if _dotenv:
    load_dotenv(_dotenv)

config = context.config


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("Set DATABASE_URL (or sqlalchemy.url) before running alembic.")
    # SQLAlchemy needs an explicit psycopg3 driver marker.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing (``alembic upgrade --sql``)."""
    context.configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
