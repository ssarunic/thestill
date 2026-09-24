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

"""Spec #85 — LIKE wildcard escaping."""

import sqlite3

import pytest

from thestill.utils.sql_like import LIKE_ESCAPE_CLAUSE, escape_like_wildcards, substring_pattern


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("plain", "plain"),
        ("50%", "50\\%"),
        ("a_b", "a\\_b"),
        ("c\\d", "c\\\\d"),
        # Escape char first: the backslash introduced for % must not be re-escaped.
        ("\\%", "\\\\\\%"),
        ("", ""),
    ],
)
def test_escape_like_wildcards(raw, escaped):
    assert escape_like_wildcards(raw) == escaped


def test_substring_pattern_wraps_and_escapes():
    assert substring_pattern("50%") == "%50\\%%"


def test_escape_clause_is_a_single_backslash():
    assert LIKE_ESCAPE_CLAUSE == "ESCAPE '\\'"


@pytest.mark.parametrize(
    ("needle", "haystacks", "expected"),
    [
        ("50%", ["50% off", "50 shades", "100%"], ["50% off"]),
        ("a_b", ["a_b", "aXb", "ab"], ["a_b"]),
        # A single literal backslash; the double-backslash row is not a substring match.
        ("c\\d", ["c\\d", "cd", "c\\\\d"], ["c\\d"]),
    ],
)
def test_round_trips_through_sqlite_like(needle, haystacks, expected):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(h,) for h in haystacks])
    rows = conn.execute(
        f"SELECT v FROM t WHERE v LIKE ? {LIKE_ESCAPE_CLAUSE} ORDER BY rowid", (substring_pattern(needle),)
    ).fetchall()
    assert [r[0] for r in rows] == expected
