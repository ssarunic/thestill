"""Spec #28 §O2 — query translator unit tests.

Locks in the contract that ``-term`` and ``speaker:foo`` no longer
raise FTS5 ``OperationalError`` when fed straight into ``MATCH``.
"""

from __future__ import annotations

import sqlite3

import pytest

from thestill.search.query_translator import translate_lexical_query


class TestTranslate:
    def test_empty_query(self):
        t = translate_lexical_query("")
        assert t.fts_match == ""
        assert t.embedding_text == ""
        assert t.speaker is None

    def test_plain_terms_get_implicit_and(self):
        t = translate_lexical_query("agentic engineering")
        assert t.fts_match == "agentic AND engineering"

    def test_quoted_phrase_preserved(self):
        t = translate_lexical_query('"sovereign AI" capex')
        assert t.fts_match == '"sovereign AI" AND capex'

    def test_negation_translated_to_not(self):
        # Spec O2 example: ``"sovereign AI" AND -nvidia``.
        t = translate_lexical_query('"sovereign AI" AND -nvidia')
        assert t.fts_match == '"sovereign AI" NOT nvidia'

    def test_multiple_negatives_grouped(self):
        t = translate_lexical_query("foo -bar -baz")
        assert t.fts_match == "foo NOT (bar OR baz)"

    def test_explicit_or_kept(self):
        t = translate_lexical_query("foo OR bar")
        assert t.fts_match == "foo OR bar"

    def test_speaker_filter_extracted(self):
        # Spec O2 example: ``speaker:friedberg AI hyperscalers``.
        t = translate_lexical_query("speaker:friedberg AI hyperscalers")
        assert t.fts_match == "AI AND hyperscalers"
        assert t.speaker == "friedberg"

    def test_speaker_filter_with_quoted_value(self):
        t = translate_lexical_query('speaker:"David Friedberg" capex')
        assert t.speaker == "David Friedberg"
        assert t.fts_match == "capex"

    def test_speaker_only_query(self):
        # No positive terms left — fts_match is empty so callers can
        # short-circuit. embedding_text falls back to the raw input
        # so the semantic leg can still encode something.
        t = translate_lexical_query("speaker:friedberg")
        assert t.fts_match == ""
        assert t.speaker == "friedberg"
        assert t.embedding_text == "speaker:friedberg"

    def test_unknown_column_prefix_quoted_for_fts(self):
        # ``Host:`` isn't a real column on chunks_fts — quote the
        # token so FTS5 treats it as a literal phrase rather than
        # raising "no such column: Host". The embedding model still
        # sees the raw form, which is critical because chunk text
        # is stored as ``"<speaker>: <text>"`` and the encoder needs
        # the colon to find a semantic neighbour.
        t = translate_lexical_query("Host: alpha text")
        assert t.embedding_text == "Host: alpha text"
        assert t.speaker is None
        # FTS5 must not raise on this expression.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE doc USING fts5(text)")
        conn.execute("INSERT INTO doc(text) VALUES ('Host: alpha text')")
        rows = conn.execute("SELECT text FROM doc WHERE doc MATCH ?", (t.fts_match,)).fetchall()
        assert rows == [("Host: alpha text",)]

    def test_negative_only_falls_back_to_positive(self):
        t = translate_lexical_query("-nvidia")
        assert t.fts_match == "nvidia"


class TestFts5Compatibility:
    """Whatever the translator emits must parse against real FTS5."""

    @pytest.fixture
    def fts(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE VIRTUAL TABLE doc USING fts5(text);
            INSERT INTO doc(text) VALUES ('sovereign AI rules everything');
            INSERT INTO doc(text) VALUES ('nvidia chips dominate');
            INSERT INTO doc(text) VALUES ('sovereign AI nvidia future');
            INSERT INTO doc(text) VALUES ('AI hyperscalers compete');
            """
        )
        return conn

    @pytest.mark.parametrize(
        "raw",
        [
            '"sovereign AI" AND -nvidia',
            "speaker:friedberg AI hyperscalers",
            "AI hyperscalers",
            "foo -bar",
            "foo OR bar",
            "plain query",
            "-nvidia",
        ],
    )
    def test_translated_query_does_not_raise(self, fts, raw):
        expr = translate_lexical_query(raw).fts_match
        if not expr:
            # Nothing to MATCH against — caller's responsibility to skip.
            return
        # Must not raise OperationalError.
        fts.execute("SELECT text FROM doc WHERE doc MATCH ?", (expr,)).fetchall()

    def test_negation_actually_excludes(self, fts):
        # The behaviour we're locking in: spec example excludes
        # nvidia-bearing rows.
        expr = translate_lexical_query('"sovereign AI" AND -nvidia').fts_match
        rows = [r[0] for r in fts.execute("SELECT text FROM doc WHERE doc MATCH ? ORDER BY rank", (expr,))]
        assert rows == ["sovereign AI rules everything"]
