# Changelog

Cambi di contratto e comportamento del server MCP `dataciviclab`.
Storia completa: `git log`.

## [Unreleased]

### Added

- `join_suggestions(slug, limit, min_years, include_names)`: dataset joinabili
  derivati dai `semantic_type` dell'artifact. Coppie di colonne, overlap anni,
  `type_mismatch`; chiavi specifiche prima delle geografiche.

## [0.4.0] — 2026-11-01

Cambi di contratto rispetto alla v0.3. **I nomi dei tool non cambiano.**

### Changed

- `gcs_query` e la card mart di `explore` espongono `gcs_path` (era `gs_path`):
  un solo nome per il path in tutto il server.
- `find` espone `returned`; `total_catalog` è il totale dei match e non più
  `len(results)` (era limitato da `limit`).
- `lab_map(domain=…)` riporta `totals` del dominio, non globali.
- `gcs_query` valida la SQL sull'AST di DuckDB: sorgenti `FROM`/`JOIN`
  limitate a `_data`/CTE (anche quando scritte come stringa), funzioni di
  lettura rifiutate ovunque compaiano.
- Un solo client HTTP (lab-connectors) e una sola risoluzione del token per
  context, `find(include_github)` e Discussions.

### Removed

- `marts_hint` (card dataset) e `marts_query_hint` (card mart): resta
  `query_hint`, sempre del layer della card.
- La card mart non eredita più `clean_rows`, `clean_rows_note` e `marts` dal
  dataset padre; il `name` è quello del mart, non del padre.
- Dipendenze `httpx` e `python-dotenv`.

### Fixed

- `find(type=…)` con un tipo sconosciuto risponde `ok:false` con
  `valid_types` (prima: zero risultati silenziosi).

## [0.2.0]

### Changed (breaking)

- `github_get_discussion_summary` → `github_get_discussion(mode="summary")`
- `github_get_discussion_comments` → `github_get_discussion(include_comments=True)`
- `github_search_discussions` → `github_list_discussions(query=…)`

### Removed

- `refresh_context`: il rebuild degli artefatti è un'azione operativa da
  CLI/CI di ACB, non da un MCP per esterni.
