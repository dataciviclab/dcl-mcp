# Design e note tecniche — dcl-mcp

Dettagli per chi mantiene il server. Per l'uso quotidiano vedi il
[README](README.md).

## Architettura in una riga

Un server MCP locale (stdio) che parla con tre sorgenti: gli artefatti di
contesto su GitHub (branch `context` di ACB), l'API GraphQL di GitHub per le
discussion, e i parquet pubblici su GCS via DuckDB.

## Contesto: artefatti remoti, zero checkout

`session_bootstrap`, `workspace_triage`, `lab_map`, `find`, `explore` e
`join_suggestions` leggono gli artefatti prodotti dalla CI di
[agent-context-builder](https://github.com/dataciviclab/agent-context-builder):
`session_bootstrap.md`, `workspace_triage.json`, `topic_index.json`, serviti da
`raw.githubusercontent.com` sul branch `context` (configurabile con
`ACB_REPO`/`ACB_BRANCH`).

- Cache TTL 120 secondi per processo.
- Nessun checkout del workspace richiesto: il server funziona anche su una
  macchina dove nessun repo del Lab è clonato.

## Card di `explore`

- **Una card = un layer**: `gcs_path` e `query_hint` puntano sempre al layer
  della card (clean per un dataset, mart per uno slug `dataset__table`).
- La card mart **non eredita** `clean_rows` né la lista `marts` del dataset
  padre; la lista `marts` sulla card dataset resta dato, non hint — per
  l'hint del mart si fa `explore(<mart_slug>)`.
- Il `name` di un mart viene dal registry del producer e, se il registry non
  lo committa, coincide con lo slug `dataset__table` (mai col nome del dataset
  padre, che rendeva indistinguibili i mart dello stesso dataset).

## `clean_rows` non è la serie completa

`clean_rows` è il conteggio dell'ultimo snapshot clean pubblicato dal
producer. Per numeri citabili usare `query_hint` (`count(*)`) via
`gcs_query`.

## `join_suggestions`: come deriva i join

- Fonte: `columns[].semantic_type` già risolto dal `registry build` e
  presente in `topic_index`. Il vocabolario (`semantic_types.yaml`) **non**
  viene caricato a runtime.
- Escluse le chiavi rumore (`year`, `month`, `country_code`); i nomi comune
  (`municipality_name`) solo con `include_names=true`.
- Ranking: chiavi specifiche (CIG/CUP/CF/codice ente) prima delle
  geografiche, poi chiavi condivise, overlap anni, stessa categoria.
- `type_mismatch` segnala coppie con tipi dichiarati diversi (es. BIGINT vs
  VARCHAR): è un match grossolano, la verifica dei valori resta all'agente.

## GCS: read-only con guardrail sull'AST

- Solo bucket `dataciviclab-clean` e `dataciviclab-mart`, una sola istruzione
  per chiamata (SELECT o `WITH…SELECT`, niente DML/PRAGMA), limite 500 righe
  (clamp dichiarato).
- La query deve leggere la tabella `_data` (view sul parquet).
- La validazione gira sull'**AST** che DuckDB produce con
  `json_serialize_sql`: ogni sorgente di righe (`BASE_TABLE`) deve essere
  `_data` o una CTE della query — **incluse le sorgenti scritte come stringa**
  (`FROM '/tmp/x.csv'`, sintassi DuckDB valida che legge il file) — e nessuna
  funzione di lettura (`read_*`, `*_scan`, `glob`, …) può comparire, né in
  `FROM` né come scalare in `WHERE`. Le sole table function ammesse sono
  quelle in memoria (`unnest`, `range`, `generate_series`).
- **È un guardrail, non un sandbox**: non eseguire query di terzi non fidati.
- Per i mart usare `layer=mart` e lo slug `dataset__table` (in `marts` della
  card, o dal registry del repo produttore).

## Ref tipizzati

In caso di collisione di slug (es. repo e analysis con lo stesso nome)
`explore("analysis:slug")` forza il tipo; senza prefisso l'ordine è dataset →
repo → domain → source → analysis.

## `find`: totali e affidabilità

- `find.total` = `total_catalog` + `total_external` (quest'ultimo solo con
  `include_github=true`).
- `total_catalog` conta tutti i match nel catalogo; `returned` dice quanti ne
  stai vedendo (`limit`).
- Con `include_github=true` la risposta ha anche `github_ok` e, se qualcosa
  fallisce, `github_errors`: un token assente/scaduto non viene confuso con
  "nessun risultato".
- `type=` sconosciuto → `ok:false` con `valid_types` (non zero risultati
  silenziosi).

## Envelope e trasporto

- Ogni risposta ha `ok` (anche gli errori, con `error`/`message`).
- Context, `find(include_github=true)` e Discussions passano dallo stesso
  `HttpClient` di lab-connectors (retry, circuit breaker, un solo pool) e
  dalla stessa risoluzione del token.
- La ricerca discussioni org-wide usa la query GraphQL di
  `discussions.client` — non esiste una seconda copia in `context/`.
- **Cache**: schema dei parquet mart (`DESCRIBE`) cachato 10 minuti come il
  registry — `explore` non ripete il round-trip GCS.

## Contratto

I nomi dei tool sono contratti cross-repo (skill, docs, config utente): non
vanno cambiati senza migrazione. I cambi di contratto sono registrati in
[CHANGELOG.md](CHANGELOG.md).

## Storia

Il server nasce dalla migrazione di `dataciviclab-context` (ACB) e
`github-discussions` (lab-connectors), più il nuovo `gcs_query`, modellato su
tre passate di audit con agenti newcomer. Il builder degli artefatti resta in
ACB; le librerie condivise in
[lab-connectors](https://github.com/dataciviclab/lab-connectors).
