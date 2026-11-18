# migrations

## Overview

This is the Alembic environment for the typed Postgres schema (spec #44
Phase 5). `alembic.ini` at the repository root sets
`script_location = thestill/migrations`. `env.py` points Alembic at the same
database the application uses, and the revision chain lives in
[versions](./versions/EGM.md). The revisions use Postgres-only DDL, for
example `timestamptz`, `jsonb` and `pg_trgm`.

## Interface

- `env.py` - Alembic entry point. Alembic runs `run_migrations_offline()` when
  it is invoked with `--sql`, and `run_migrations_online()` otherwise.
- `env.py:_database_url()` - resolves the target DSN and rewrites
  `postgresql://` to `postgresql+psycopg://`.
- `script.py.mako` - template for new revisions: `revision`, `down_revision`,
  `upgrade()`, `downgrade()`.
- [versions](./versions/EGM.md) - linear revisions `0001` → `0028`. They add the
  base schema, briefings, platform links, entity-link decisions, analytics
  tables, abuse-prevention tables, MCP OAuth, the transcript editor op log
  and sync tracks.

## Invariants

- The DSN is resolved in this order:
  `config.attributes["thestill_dsn"]`, then `DATABASE_URL`, then
  `sqlalchemy.url`. If none is set, a `RuntimeError` is raised
  (`env.py:_database_url`).
- The programmatic DSN wins on purpose.
  `thestill/repositories/postgres_schema.py:run_alembic_upgrade` holds an
  advisory lock on that exact DSN, so an ambient `DATABASE_URL` must not
  redirect the upgrade.
- `env.py` loads only the `.env` found by walking up from the package
  (`thestill.utils.config._find_dotenv_from_package`) with a bare
  `load_dotenv` (`env.py:44-48`). `load_dotenv` does not override real
  environment variables, so those still win. When `run_alembic_upgrade` runs
  it inside the app, `load_config` has already loaded the app's `.env`, so
  `EMBEDDING_MODEL` (used by revision `0001`) and `DATABASE_URL` match the
  app.
- Known issue: a standalone `alembic` run does not follow
  `thestill/utils/config.py:load_env_file`, which checks an explicit path,
  then `THESTILL_ENV_FILE`, then the package walk, then the CWD walk. With
  `THESTILL_ENV_FILE` set, or with only a CWD `.env`, Alembic loads a
  different `.env` or none (`env.py:44-48`).
- `env.py` sets no `target_metadata`. Revisions are written by hand as raw
  SQL, and there is no autogenerate.
- A database that `postgres_schema.ensure_schema` bootstrapped can adopt Alembic
  with `alembic stamp head` (module docstring of `env.py`).

## Side Effects

- Reads `.env` into the process environment at import time
  (`env.py` module level, `load_dotenv`).
- Online mode opens a SQLAlchemy engine against Postgres and runs every pending
  revision inside one `context.begin_transaction()`. Offline mode writes the
  SQL to stdout instead.
- Callers that run it: the `alembic` CLI, and
  `thestill/repositories/postgres_schema.py:run_alembic_upgrade`.
  `thestill/web/app.py` calls the latter on startup when the app uses Postgres
  and `MIGRATE_ON_STARTUP` is on
  (`thestill/utils/config.py:is_migrate_on_startup_enabled`).

## Verification

- Run: `make test`
