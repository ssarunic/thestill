# thestill.search

## Overview

Corpus search and the "Related episodes" rail. The package does four jobs:

- Defines the `SearchBackend` protocol and its two implementations:
  `SqliteVecBackend`, which uses SQLite FTS5 and sqlite-vec, and
  `PgVectorBackend`, which uses Postgres full-text search and pgvector.
- Translates user query syntax into a safe full-text expression.
- Shapes hits into citation rows for MCP, REST, and the CLI.
- Precomputes related-episode pairs into the `episode_related` table.

`__init__.py` exports `build_citation_row` and `build_citation_rows`.

## Interface

- `base.py:SearchMode`: `lexical`, `semantic`, and `hybrid`.
- `base.py:SearchBackend`, a Protocol with two methods:
  `search(query, *, mode, limit, filters) -> List[ResolvedHit]` and
  `count_lexical_matches(query, episode_ids, filters) -> Dict[str, int]`.
- `base.py:SearchFilters`, a frozen dataclass with the fields `podcast_id`,
  `date_from`, `date_to`, `has_entity` (a tuple), `speaker`, `episode_id`,
  and `max_per_episode`.
- `base.py:ResolvedHit`, a frozen dataclass. `as_citation(...)` returns the
  citation wire dict: `deeplink` is `thestill://episode/<id>?t=<s>`, plus the
  `episode_links` fields. `origin` is used only for diagnostics.
- `base.py:cap_per_episode`, `base.py:embedding_dim_for`, and
  `base.py:EMBEDDING_MODEL_DIMS`. The dimension table maps
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` to 384 and
  `BAAI/bge-small-en-v1.5` to 384. `base.py:DEFAULT_EMBEDDING_MODEL` is the
  MiniLM model.
- `sqlite_vec_client.py:SqliteVecBackend(db_path=..., embedding_model=...,
  reranker=None, rerank_pool=20, rerank_min_score=0.01,
  rerank_semantic_min_score=0.05, entity_leg=False)`.
- `pgvector_client.py:PgVectorBackend(dsn=..., ...)`, which takes the same
  arguments with `dsn` in place of `db_path`. Its helper `_fts5_to_websearch`
  maps an FTS5 MATCH string onto `websearch_to_tsquery` syntax.
- `query_translator.py:translate_lexical_query(raw) -> TranslatedQuery`.
  `TranslatedQuery` has the fields `fts_match`, `embedding_text`, and
  `speaker`. The supported syntax is `"phrase"`, `-term`, `AND`, `OR`,
  `NOT`, and `speaker:VALUE`. The uppercase tokens `AND`, `OR`, and `NOT`
  pass straight through as FTS5 operators
  [`query_translator.py:_FTS5_OPERATORS`]. Any other `column:VALUE` is
  quoted as a literal.
- `reranker.py:Reranker(model_name, max_length=256, batch_size=32)` loads a
  cross-encoder lazily and provides `warmup()` and `score(query, passages)`.
- `reranker.py:pool_candidates`, `reranker.py:rerank`, and
  `reranker.py:LITERAL_ORIGINS` (`lexical`, `entity`).
- `citation.py:build_citation_row` and `build_citation_rows` turn a
  `MentionContext` into a `CitationRow` with `match_type=entity`.
  `citation.py:search_citations(hits, repository=..., base_url=...)` does
  the same for search hits.
- `index_verify.py:verify_index(config, embedding_model, sample=50) ->
  VerifyReport` backs `thestill chunks verify`. `VerifyReport` provides
  `healthy`, `verdict`, `runtime_disagrees`, and `index_disagrees`.
  `DISAGREEMENT_THRESHOLD` is 0.05.
- `related_builder.py:build_related_episodes(db_path, *,
  embedding_model_name, top_n=5, ...)` and
  `related_builder.py:update_related_for_episodes(db_path, *,
  embedding_model_name, episode_ids, ...)` build the rail on SQLite.
- `related_builder.py:run_incremental(backend, episode_ids, pool_k=150)`,
  `related_builder.py:IncrementalBackend` (a Protocol), and
  `related_builder.py:IncrementalStats`.
- Related-rail defaults in `related_builder.py`:

  | Constant | Value |
  | --- | --- |
  | `DEFAULT_TFIDF_FLOOR` | 0.12 |
  | `DEFAULT_W_TFIDF` | 0.55 |
  | `DEFAULT_W_VECTOR` | 0.30 |
  | `DEFAULT_W_ENTITY` | 0.15 |
  | `DEFAULT_CANDIDATE_CAP` | 2000 |
  | `DEFAULT_LEXICAL_TERMS` | 25 |
  | `DEFAULT_INCREMENTAL_POOL_K` | 150 |

- `pg_related_builder.py:build_related_episodes(dsn, ...)` and
  `pg_related_builder.py:update_related_for_episodes(dsn, ...)` are the
  Postgres versions, with the same signatures.

## Invariants

- Backends return at most `limit` hits, best first. They push filters into
  SQL rather than filtering fetched rows in Python, and they must be safe
  to call from several threads [`base.py:SearchBackend`].
- Score meanings differ by match type, so never compare scores across
  types [`base.py:ResolvedHit`]:
  - `lexical`: `-bm25`, or `ts_rank_cd` on Postgres. Higher is better.
  - `semantic`: cosine distance. Lower is better.
  - `hybrid`: the RRF score, or the reranker's sigmoid score. Higher is
    better.
- `has_entity` uses AND logic: the episode's `entity_mentions` must cover
  every listed id. `speaker` is a case-insensitive substring match
  [`sqlite_vec_client.py:SqliteVecBackend._filter_clauses`].
- The two backends must stay in step: they repeat the same constants
  rather than import them [`sqlite_vec_client.py`, `pgvector_client.py`]:

  | Constant | Value | Meaning |
  | --- | --- | --- |
  | `_RRF_K` | 60 | RRF constant |
  | `_HYBRID_FETCH` | 50 | Rows fetched per leg |
  | `_HYBRID_WEIGHT_LEX`, `_HYBRID_WEIGHT_SEM` | 0.5 each | Leg weights |
  | `_SEMANTIC_MAX_DISTANCE` | 0.55 | Largest distance kept in the semantic leg |
  | `_HYBRID_SEMANTIC_ONLY_MAX_DISTANCE` | 0.5 | Largest distance for a hybrid row only the semantic leg found |
  | `_MIN_SEMANTIC_TEXT_CHARS` | 30 | Shortest chunk text the semantic leg considers |
  | `_SHORT_QUERY_MAX_WORDS` | 2 | Queries this short get no semantic-only hybrid rows |

  The SQLite backend also over-fetches k-NN results by
  `_SEMANTIC_FILTER_OVERFETCH` (10).
- Without a reranker, a hybrid query of at most 2 words admits no
  semantic-only rows: a keyword with no literal match returns nothing. Rows
  that both legs found are never gated
  [`sqlite_vec_client.py:SqliteVecBackend._hybrid`].
- With a reranker, candidates are pooled from the lexical, optional entity,
  and semantic legs. Rows from the literal legs survive at
  `rerank_min_score`, and semantic-only rows need
  `rerank_semantic_min_score`. Chunks below the writer's word minimum are
  dropped [`reranker.py:pool_candidates`, `reranker.py:rerank`].
- `Reranker` loads the model and runs inference under one lock, on the CPU
  [`reranker.py:Reranker.score`].
- Every query filters on `embedding_model`, so rows embedded by another
  model are never mixed in [`sqlite_vec_client.py:SqliteVecBackend._lexical`,
  `_semantic`].
- Query translation never emits a bare `-term` or `col:` into FTS5, and the
  last `speaker:` wins. Empty input gives an empty `fts_match`, and lexical
  mode then returns `[]` [`query_translator.py:translate_lexical_query`,
  `sqlite_vec_client.py:SqliteVecBackend.search`].
- Exclusions become a `NOT` tail after the positive terms: one exclusion
  renders as `a NOT b`, and several as `a NOT (b OR c)`
  [`query_translator.py:_assemble`].
- An operator (`AND`, `OR`, `NOT`) is placed between the positives on either
  side of it, and adjacent positives are joined with `AND`. An operator with
  no positive before it or after it is dropped [`query_translator.py:_assemble`].
- Exclusions apply to every leg. The translator returns them as
  `exclude_match` (an FTS5 `OR` expression), the backend copies it into
  `SearchFilters.exclude_match`, and `_filter_clauses` drops matching chunks
  in the lexical, semantic and entity legs: SQLite with
  `c.id NOT IN (SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?)`,
  Postgres with `NOT (c.text_tsv @@ websearch_to_tsquery(...))`
  [`query_translator.py:translate_lexical_query`,
  `sqlite_vec_client.py:SqliteVecBackend._filter_clauses`,
  `pgvector_client.py:PgVectorBackend._filter_clauses`].
- Excluded words are never encoded into `embedding_text`.
- A query with only exclusions (`-nvidia`) has nothing to search for: the
  translator returns empty `fts_match` and `embedding_text`, and `search`
  returns `[]` in every mode [`sqlite_vec_client.py:SqliteVecBackend.search`,
  `pgvector_client.py:PgVectorBackend.search`].
- `embedding_dim_for` raises `KeyError` for an unknown model rather than
  guessing a dimension [`base.py:embedding_dim_for`].
- Related rail [`related_builder.py:build_related_episodes`,
  `related_builder.py:run_incremental`, `related_builder.py:_write_pairs`,
  `related_builder.py:_write_pairs_scoped`]:
  - A candidate must reach the TF-IDF floor to be eligible at all.
  - The score blends TF-IDF, vector, and entity-Jaccard signals, each
    min-max normalised per source episode.
  - A full rebuild replaces `episode_related` in one transaction.
  - An incremental update rewrites only the rails it scored.
  - Each seed episode costs one candidate query, independent of corpus
    size.
- `pg_related_builder` imports its scoring math from `related_builder`, so
  the two cannot drift numerically [`pg_related_builder.py` module
  docstring].
- An index is healthy when at least one row was sampled and the stored
  vectors and the batch encoding are each within 0.05 cosine distance of the
  query encoding. An empty sample (`sampled == 0`) reports unhealthy
  [`index_verify.py:VerifyReport.healthy`].

## Side Effects

- Reads the database. SQLite search queries `chunks`, `chunks_fts`,
  `chunks_vec`, `episodes`, `podcasts`, `entity_mentions`, and `entities`,
  and each call opens a connection with WAL, `foreign_keys`, and
  `busy_timeout=5000` [`sqlite_vec_client.py:SqliteVecBackend._get_connection`].
  Postgres search queries `chunks.text_tsv` and `chunks.embedding`, and sets
  `SET LOCAL hnsw.iterative_scan` [`pgvector_client.py:PgVectorBackend._semantic`].
- Writes the database in the related-rail builders: `episode_related` and
  `related_idf` are replaced with a delete followed by an insert, and
  `episode_vectors` centroids are upserted
  [`related_builder.py:_persist_idf`, `_write_pairs`, `_ensure_episode_vectors`,
  and the matching functions in `pg_related_builder.py`].
- Loads ML models: sentence-transformers `CrossEncoder` in
  `reranker.py:Reranker._get_model`, and scikit-learn `TfidfVectorizer` in
  `related_builder.py:build_related_episodes`.
- `index_verify.py:_sample_rows` reads a random sample of `chunks` rows and
  re-embeds them.
- `citation.py:search_citations` makes one
  `PodcastRepository.get_episodes_by_ids` lookup per call.
- Log events include `reranker_loading`, `reranker_loaded`,
  `related_build_complete`, and `related_build_skipped_small_corpus`.

## Verification

- Run: `./venv/bin/pytest tests/unit/search`
