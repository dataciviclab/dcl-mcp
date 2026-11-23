# Changelog

Cambi di contratto e comportamento del server MCP `dataciviclab`.
Storia completa: `git log`.

## [Unreleased]

### Changed — ricerca

- `find` somma le componenti di punteggio invece di prendere il massimo: un
  substring accidentale non pareggia più un match coerente.
- Substring solo ai confini di parola: `cup` non matcha `occupazione`.
- Credito parziale sui token (2 su 3 non è zero) e peso **IDF** dal catalogo:
  `italiano` (3 dataset) non obbliga più la ricerca quanto `cup` (16).
- Alias curati (`disabilità`→`invalidi`, `europei`→`eu`, `fondi`→`finanziamenti`…).
- Soglia minima (8) e `hint` quando non c'è match forte: meglio zero risultati
  dichiarati che dieci casuali.
- Ogni risultato dataset porta `matched` (quali campi hanno fatto match).
- Stopword articolate mancanti (`della`, `nella`, `sulla`, `dalla`, `alla`…):
  una preposizione azzerava la query.

### Changed

- `workspace_triage(section="registry")` è **compatto per default**: niente
  `signals_detail` (~186 KB → 10 KB). Il dettaglio per dataset è già in
  `find`/`explore`/`lab_map(view="datasets")`; per averlo:
  `detail=true`. **Breaking** per chi leggeva `signals_detail` senza chiedere.

### Added

- `lab_map(view="datasets", domain=)` — enumerazione del catalogo: una riga per
  dataset con `years`, `gcs_available`, `snapshot_year`, `n_columns`,
  `last_run`, `run_year` (~121 KB per 547; 2–38 KB per dominio, media ~18 KB). Risponde a
  domande di catalogo ("quali sono obsoleti?") senza un `explore` per dataset:
  nell'audit la stessa domanda costava 1.516–2.606 chiamate con esito
  non deterministico.
- Provenienza strutturata: `as_of` (quando è stato costruito il catalogo) e
  `source` in `find`, `explore`, `lab_map`, `join_suggestions`, `gcs_query`,
  `workspace_triage`, `session_bootstrap`; `snapshot_year` (anno del file
  pubblicato su GCS) nelle card dataset/mart e in `gcs_query`.
  Motivo: senza data l'agente non può citare un risultato né dire se un dataset
  è aggiornato (audit usabilità 2026-11-23, F8/F10; R02: 1.772 chiamate e
  timeout su una domanda senza criterio di stop).
- `find`: ogni risultato dataset porta `snippet`, `years`, `n_columns`,
  `n_marts`, `snapshot_year`, `gcs_available` e l'aggiornamento della pipeline
  (`last_run`, `run_year`, `repo_updated_at`) — bastano a scegliere e a
  giudicare la freschezza senza un `explore` per candidato (audit F5).
- `explore`: card dataset con `source` (etichetta della fonte), `url` (pagina
  Explorer), `gcs_available`, `clean_rows_scope` (`single_file` |
  `last_year_of_multi_file`), `joins` (2 dataset collegabili per chiave);
  mart con `n_columns`, `primary_key` (se nel registry) e `columns_note`
  quando `role`/`semantic_type` non sono deducibili; analysis con `year_min`/
  `year_max` strutturati; repo/domain/source con `datasets_total` e
  `truncated` (prima la lista era tagliata a 50 in silenzio).

### Removed

- `stage` da ogni risposta (dataset, mart, `find`, `lab_map`). È un campo
  editoriale che resta stale: `published` copriva 33 dataset su 547, mentre
  536 hanno un parquet leggibile. L'accessibilità reale è `gcs_available`.
  **Breaking** per i consumer che leggevano `stage`.
- `n_published` (repo) e `totals.published` (`lab_map`): il valore del builder
  diceva 0 su repo con 15 dataset su GCS. Sostituiti da `n_with_gcs` /
  `totals.with_gcs`, calcolati dalle `location` reali.

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
