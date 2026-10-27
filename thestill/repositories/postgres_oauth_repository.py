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

"""PostgreSQL implementation of ``OAuthRepository`` (spec #109).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids (read
back through ``as_str``), ``timestamptz`` datetimes, ``jsonb`` metadata,
``%s`` placeholders. Schema in ``postgres_schema.py`` and alembic
``0025_mcp_oauth``. Each ``with connect(...)`` block is one transaction;
compare-and-set updates take the row lock, so a concurrent second attempt
re-reads the row and matches nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

import psycopg
from psycopg.types.json import Jsonb
from structlog import get_logger

from ..models.oauth import OAuthClientRecord, OAuthCode, OAuthGrant, OAuthPending, OAuthTokenRecord, RevokeReason
from ..utils.postgres_ext import as_str, connect
from .oauth_repository import OAuthRepository

logger = get_logger(__name__)

# Grant columns are aliased ``g_*`` everywhere: joined with a code or token
# row, ``scopes``/``created_at``/``revoked_at`` would otherwise collide.
_GRANT_FIELDS = (
    "id",
    "user_id",
    "client_id",
    "client_name",
    "redirect_host",
    "scopes",
    "created_at",
    "last_used_at",
    "last_used_ip",
    "revoked_at",
    "revoked_reason",
)
_GRANT_COLS = ", ".join(f"g.{f} AS g_{f}" for f in _GRANT_FIELDS)
_CODE_COLS = (
    "c.code_hash, c.grant_id, c.client_id, c.scopes, c.redirect_uri, c.redirect_uri_explicit,"
    " c.code_challenge, c.resource, c.expires_at, c.used_at"
)
_TOKEN_COLS = (
    "t.token_hash, t.grant_id, t.client_id, t.kind, t.scopes, t.resource, t.created_at,"
    " t.expires_at, t.replaced_by_hash, t.revoked_at"
)
_PENDING_COLS = (
    "id_hash, client_id, redirect_uri, redirect_uri_explicit, state, scopes, code_challenge,"
    " resource, created_at, expires_at"
)


def _scopes(value: str) -> Tuple[str, ...]:
    return tuple(s for s in value.split(",") if s)


def _grant(row) -> OAuthGrant:
    return OAuthGrant(
        id=as_str(row["g_id"]),
        user_id=as_str(row["g_user_id"]),
        client_id=row["g_client_id"],
        client_name=row["g_client_name"],
        redirect_host=row["g_redirect_host"],
        scopes=_scopes(row["g_scopes"]),
        created_at=row["g_created_at"],
        last_used_at=row["g_last_used_at"],
        last_used_ip=row["g_last_used_ip"],
        revoked_at=row["g_revoked_at"],
        revoked_reason=row["g_revoked_reason"],
    )


def _code(row) -> OAuthCode:
    return OAuthCode(
        code_hash=row["code_hash"],
        grant_id=as_str(row["grant_id"]),
        client_id=row["client_id"],
        scopes=_scopes(row["scopes"]),
        redirect_uri=row["redirect_uri"],
        redirect_uri_explicit=row["redirect_uri_explicit"],
        code_challenge=row["code_challenge"],
        resource=row["resource"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
    )


def _token(row) -> OAuthTokenRecord:
    return OAuthTokenRecord(
        token_hash=row["token_hash"],
        grant_id=as_str(row["grant_id"]),
        client_id=row["client_id"],
        kind=row["kind"],
        scopes=_scopes(row["scopes"]),
        resource=row["resource"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        replaced_by_hash=row["replaced_by_hash"],
        revoked_at=row["revoked_at"],
    )


def _pending(row) -> OAuthPending:
    return OAuthPending(
        id_hash=row["id_hash"],
        client_id=row["client_id"],
        redirect_uri=row["redirect_uri"],
        redirect_uri_explicit=row["redirect_uri_explicit"],
        state=row["state"],
        scopes=_scopes(row["scopes"]),
        code_challenge=row["code_challenge"],
        resource=row["resource"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _insert_tokens(conn: psycopg.Connection, tokens: Sequence[OAuthTokenRecord]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO oauth_tokens
                (token_hash, grant_id, client_id, kind, scopes, resource, created_at,
                 expires_at, replaced_by_hash, revoked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    t.token_hash,
                    t.grant_id,
                    t.client_id,
                    t.kind,
                    ",".join(t.scopes),
                    t.resource,
                    t.created_at,
                    t.expires_at,
                    t.replaced_by_hash,
                    t.revoked_at,
                )
                for t in tokens
            ],
        )


class PostgresOAuthRepository(OAuthRepository):
    """PostgreSQL-backed OAuth authorization server repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres OAuth repository")

    # -- clients -------------------------------------------------------------

    def get_client(self, client_id: str) -> Optional[OAuthClientRecord]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT client_id, source, client_name, metadata, created_at, fetched_at, fresh_until"
                " FROM oauth_clients WHERE client_id = %s",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClientRecord(
            client_id=row["client_id"],
            source=row["source"],
            client_name=row["client_name"],
            metadata=row["metadata"],
            created_at=row["created_at"],
            fetched_at=row["fetched_at"],
            fresh_until=row["fresh_until"],
        )

    def save_client(self, client: OAuthClientRecord) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO oauth_clients
                    (client_id, source, client_name, metadata, created_at, fetched_at, fresh_until)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_id) DO UPDATE SET
                    source      = excluded.source,
                    client_name = excluded.client_name,
                    metadata    = excluded.metadata,
                    fetched_at  = excluded.fetched_at,
                    fresh_until = excluded.fresh_until
                """,
                (
                    client.client_id,
                    client.source,
                    client.client_name,
                    Jsonb(client.metadata),
                    client.created_at,
                    client.fetched_at,
                    client.fresh_until,
                ),
            )

    # -- pending -------------------------------------------------------------

    def save_pending(self, pending: OAuthPending) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO oauth_pending ({_PENDING_COLS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    pending.id_hash,
                    pending.client_id,
                    pending.redirect_uri,
                    pending.redirect_uri_explicit,
                    pending.state,
                    ",".join(pending.scopes),
                    pending.code_challenge,
                    pending.resource,
                    pending.created_at,
                    pending.expires_at,
                ),
            )

    def get_pending(self, id_hash: str, *, now: datetime) -> Optional[OAuthPending]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_PENDING_COLS} FROM oauth_pending WHERE id_hash = %s AND expires_at > %s",
                (id_hash, now),
            ).fetchone()
        return _pending(row) if row else None

    def delete_pending(self, id_hash: str) -> bool:
        with connect(self.dsn) as conn:
            return conn.execute("DELETE FROM oauth_pending WHERE id_hash = %s", (id_hash,)).rowcount == 1

    def approve(
        self,
        id_hash: str,
        *,
        now: datetime,
        user_id: str,
        new_grant_id: str,
        client_name: Optional[str],
        redirect_host: str,
        scopes: Sequence[str],
        code_hash: str,
        code_expires_at: datetime,
    ) -> Optional[Tuple[OAuthGrant, OAuthCode]]:
        joined = ",".join(scopes)
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"DELETE FROM oauth_pending WHERE id_hash = %s AND expires_at > %s RETURNING {_PENDING_COLS}",
                (id_hash, now),
            ).fetchone()
            if row is None:
                return None
            pending = _pending(row)
            # Serialise approvals of one (user, client) so the keep-or-replace
            # decision and the partial unique index never race.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"oauth_grant:{user_id}:{pending.client_id}",),
            )
            current = conn.execute(
                f"SELECT {_GRANT_COLS} FROM oauth_grants g WHERE g.user_id = %s AND g.client_id = %s"
                " AND g.revoked_at IS NULL",
                (user_id, pending.client_id),
            ).fetchone()
            if current is not None and current["g_scopes"] == joined:
                grant_id = as_str(current["g_id"])
                conn.execute(
                    "UPDATE oauth_grants SET client_name = %s, redirect_host = %s WHERE id = %s",
                    (client_name, redirect_host, grant_id),
                )
            else:
                if current is not None:
                    conn.execute(
                        "UPDATE oauth_grants SET revoked_at = %s, revoked_reason = 'replaced' WHERE id = %s",
                        (now, current["g_id"]),
                    )
                grant_id = new_grant_id
                conn.execute(
                    """
                    INSERT INTO oauth_grants
                        (id, user_id, client_id, client_name, redirect_host, scopes, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (grant_id, user_id, pending.client_id, client_name, redirect_host, joined, now),
                )
            code = OAuthCode(
                code_hash=code_hash,
                grant_id=grant_id,
                client_id=pending.client_id,
                scopes=tuple(scopes),
                redirect_uri=pending.redirect_uri,
                redirect_uri_explicit=pending.redirect_uri_explicit,
                code_challenge=pending.code_challenge,
                resource=pending.resource,
                expires_at=code_expires_at,
            )
            conn.execute(
                """
                INSERT INTO oauth_codes
                    (code_hash, grant_id, client_id, scopes, redirect_uri, redirect_uri_explicit,
                     code_challenge, resource, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    code.code_hash,
                    code.grant_id,
                    code.client_id,
                    joined,
                    code.redirect_uri,
                    code.redirect_uri_explicit,
                    code.code_challenge,
                    code.resource,
                    code.expires_at,
                ),
            )
            grant_row = conn.execute(
                f"SELECT {_GRANT_COLS} FROM oauth_grants g WHERE g.id = %s", (grant_id,)
            ).fetchone()
            return _grant(grant_row), code

    # -- codes ---------------------------------------------------------------

    def get_code(self, code_hash: str) -> Optional[Tuple[OAuthCode, OAuthGrant]]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_CODE_COLS}, {_GRANT_COLS} FROM oauth_codes c"
                " JOIN oauth_grants g ON g.id = c.grant_id WHERE c.code_hash = %s",
                (code_hash,),
            ).fetchone()
        return (_code(row), _grant(row)) if row else None

    def redeem_code(self, code_hash: str, *, now: datetime, tokens: Sequence[OAuthTokenRecord]) -> bool:
        with connect(self.dsn) as conn:
            updated = conn.execute(
                """
                UPDATE oauth_codes SET used_at = %s
                 WHERE code_hash = %s AND used_at IS NULL AND expires_at > %s
                   AND EXISTS (SELECT 1 FROM oauth_grants g
                                WHERE g.id = oauth_codes.grant_id AND g.revoked_at IS NULL)
                """,
                (now, code_hash, now),
            ).rowcount
            if updated != 1:
                return False
            _insert_tokens(conn, tokens)
            return True

    # -- tokens --------------------------------------------------------------

    def find_active_access(self, token_hash: str, *, now: datetime) -> Optional[Tuple[OAuthTokenRecord, OAuthGrant]]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_TOKEN_COLS}, {_GRANT_COLS} FROM oauth_tokens t
                  JOIN oauth_grants g ON g.id = t.grant_id
                 WHERE t.token_hash = %s AND t.kind = 'access' AND t.revoked_at IS NULL
                   AND (t.expires_at IS NULL OR t.expires_at > %s)
                   AND g.revoked_at IS NULL
                """,
                (token_hash, now),
            ).fetchone()
        return (_token(row), _grant(row)) if row else None

    def get_token(self, token_hash: str) -> Optional[Tuple[OAuthTokenRecord, OAuthGrant]]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_TOKEN_COLS}, {_GRANT_COLS} FROM oauth_tokens t"
                " JOIN oauth_grants g ON g.id = t.grant_id WHERE t.token_hash = %s",
                (token_hash,),
            ).fetchone()
        return (_token(row), _grant(row)) if row else None

    def rotate_refresh(self, old_hash: str, *, now: datetime, tokens: Sequence[OAuthTokenRecord]) -> bool:
        successor = next(t.token_hash for t in tokens if t.kind == "refresh")
        with connect(self.dsn) as conn:
            updated = conn.execute(
                """
                UPDATE oauth_tokens SET replaced_by_hash = %s
                 WHERE token_hash = %s AND kind = 'refresh'
                   AND replaced_by_hash IS NULL AND revoked_at IS NULL
                   AND (expires_at IS NULL OR expires_at > %s)
                   AND EXISTS (SELECT 1 FROM oauth_grants g
                                WHERE g.id = oauth_tokens.grant_id AND g.revoked_at IS NULL)
                """,
                (successor, old_hash, now),
            ).rowcount
            if updated != 1:
                return False
            _insert_tokens(conn, tokens)
            return True

    def revoke_token(self, token_hash: str, *, now: datetime) -> bool:
        with connect(self.dsn) as conn:
            return (
                conn.execute(
                    "UPDATE oauth_tokens SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL",
                    (now, token_hash),
                ).rowcount
                == 1
            )

    # -- grants --------------------------------------------------------------

    def get_grant(self, grant_id: str) -> Optional[OAuthGrant]:
        with connect(self.dsn) as conn:
            row = conn.execute(f"SELECT {_GRANT_COLS} FROM oauth_grants g WHERE g.id = %s", (grant_id,)).fetchone()
        return _grant(row) if row else None

    def list_active_grants(self, user_id: str) -> List[OAuthGrant]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT {_GRANT_COLS} FROM oauth_grants g WHERE g.user_id = %s AND g.revoked_at IS NULL"
                " ORDER BY g.created_at DESC",
                (user_id,),
            ).fetchall()
        return [_grant(r) for r in rows]

    def revoke_grant(
        self, grant_id: str, *, now: datetime, reason: RevokeReason, user_id: Optional[str] = None
    ) -> bool:
        sql = "UPDATE oauth_grants SET revoked_at = %s, revoked_reason = %s WHERE id = %s AND revoked_at IS NULL"
        params: list = [now, reason, grant_id]
        if user_id is not None:
            sql += " AND user_id = %s"
            params.append(user_id)
        with connect(self.dsn) as conn:
            return conn.execute(sql, params).rowcount == 1

    def revoke_user_grants(self, user_id: str, *, now: datetime, reason: RevokeReason) -> int:
        with connect(self.dsn) as conn:
            return conn.execute(
                "UPDATE oauth_grants SET revoked_at = %s, revoked_reason = %s"
                " WHERE user_id = %s AND revoked_at IS NULL",
                (now, reason, user_id),
            ).rowcount

    def touch_grant(self, grant_id: str, *, now: datetime, ip: Optional[str], min_interval_seconds: int) -> bool:
        cutoff = now - timedelta(seconds=min_interval_seconds)
        with connect(self.dsn) as conn:
            return (
                conn.execute(
                    """
                    UPDATE oauth_grants SET last_used_at = %s, last_used_ip = %s
                     WHERE id = %s AND (last_used_at IS NULL OR last_used_at <= %s)
                    """,
                    (now, ip, grant_id, cutoff),
                ).rowcount
                == 1
            )

    # -- housekeeping --------------------------------------------------------

    def prune(self, *, now: datetime, retired_before: datetime, unused_clients_before: datetime) -> int:
        with connect(self.dsn) as conn:
            deleted = conn.execute("DELETE FROM oauth_pending WHERE expires_at <= %s", (now,)).rowcount
            # Used codes stay until they expire, so a replay inside the
            # code's lifetime is still recognised (and revokes the grant).
            deleted += conn.execute("DELETE FROM oauth_codes WHERE expires_at <= %s", (now,)).rowcount
            deleted += conn.execute(
                """
                DELETE FROM oauth_tokens
                 WHERE (expires_at IS NOT NULL AND expires_at <= %s)
                    OR (kind = 'refresh' AND (replaced_by_hash IS NOT NULL OR revoked_at IS NOT NULL)
                        AND created_at <= %s)
                """,
                (now, retired_before),
            ).rowcount
            deleted += conn.execute(
                "DELETE FROM oauth_grants WHERE revoked_at IS NOT NULL AND revoked_at <= %s", (retired_before,)
            ).rowcount
            deleted += conn.execute(
                """
                DELETE FROM oauth_clients
                 WHERE source = 'dcr' AND created_at <= %s
                   AND NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_id = oauth_clients.client_id)
                """,
                (unused_clients_before,),
            ).rowcount
        return deleted
