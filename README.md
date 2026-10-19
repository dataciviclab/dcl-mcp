# dcl-mcp — il MCP del DataCivicLab

Server MCP unico per partecipare all'ecosistema del Lab: contesto
org-wide, GitHub Discussions e query read-only sui dati pubblici.

```
pip install "lab-connectors[mcp,duckdb] @ git+https://github.com/dataciviclab/lab-connectors.git"
pip install "dcl-mcp @ git+https://github.com/dataciviclab/dcl-mcp.git"
```

## Cosa espone

Un solo server, nome `dataciviclab` — 11 tool in tre famiglie:

| Famiglia | Tool | Cosa fa |
|---|---|---|
| **Context** | `session_bootstrap` | Orientamento rapido (markdown) |
| | `lab_map(domain=)` | Albero domains → repos → sources (dominio sconosciuto → elenco domini validi) |
| | `find(query, type=, domain=)` | Ricerca ranked cross-entity; `total_catalog` + `total_external` (+ `github_errors` se `include_github` fallisce) |
| | `explore(ref)` | Deep-dive di dataset/repo/domain/source/analysis — ref tipizzabili (`analysis:slug`, `repo:slug`, …) |
| | `workspace_triage(section=)` | Stato Lab: radar, PR, issues, discussions… |
| **Discussions** | `github_list_discussions(query=, category_name=)` | Elenca (cursor) o cerca full-text/categoria server-side; `repo_full_name` default hub |
| | `github_list_discussion_categories` | Categorie valide per la creazione (nome + slug) |
| | `github_get_discussion(include_comments=)` | Legge una discussion; con `include_comments=true` aggiunge commenti e reply in una sola chiamata |
| | `github_create_discussion` / `github_add_discussion_comment` | Partecipa (scrittura) |
| **GCS** | `gcs_query(slug, sql, layer=, limit=)` | SELECT/CTE read-only sui parquet pubblici |

### Flusso tipico (onboarding)

```
find("rifiuti urbani")            # scopri dataset/analisi/repo
  → explore("ispra_ru_base")      # card: description, anni, colonne,
                                  # clean_rows, marts, gcs_path, query_hint
  → gcs_query(slug=…, sql=…)      # SELECT o WITH…SELECT su `_data`,
                                  # layer=mart per le tabelle mart
  → find(…, include_github=true)  # issue/PR aperte + discussioni open del tema
  → github_get_discussion(number=…, include_comments=True)
  → github_list_discussion_categories → github_create_discussion
```

### Routing tra MCP del Lab

- **dcl-mcp** (questo) — org-wide: dove cerco, cosa è successo, come partecipo, dati pubblicati
- **toolkit** — repo locale: pipeline, contratti, SQL su raw/clean/mart del workspace
- **source-observatory** — scouting fonti e inventory

## Configurazione

```json
{
  "mcpServers": {
    "dataciviclab": {
      "command": "dcl-mcp"
    }
  }
}
```

| Env var | Ruolo | Obbligatoria |
|---|---|---|
| `GITHUB_TOKEN` | Discussions (GraphQL richiede auth anche in lettura) | sì, per discussions/write |
| `DCL_MCP_ENV_FILE` | Path a un file `.env` (fallback: `.env` cercato verso l'alto dal cwd) | no |
| `ACB_REPO` / `ACB_BRANCH` | Sorgente artefatti contesto (default: ACB, branch `context`) | no |

## Note di design

- **Context** legge da remoto gli artefatti prodotti dalla CI di
  [agent-context-builder](https://github.com/dataciviclab/agent-context-builder)
  sul branch `context` (raw.githubusercontent, cache TTL 120s). Nessuna
  checkout locale richiesta. `explore` sulle card dataset espone anche
  description, periodo (`year_min`/`year_max`), `clean_rows`, lista `marts`
  (da `mart_refs`), colonne compatte (name/type/role), `gcs_path` e
  `query_hint` pronto.
- **`clean_rows`** è il conteggio dell'ultimo snapshot clean pubblicato dal
  producer: **non è garantito** sia la serie completa. Per numeri citabili
  usa `query_hint` (`count(*)`) via `gcs_query`.
- **GCS** è read-only: solo bucket `dataciviclab-clean` e
  `dataciviclab-mart`, una sola istruzione per chiamata (SELECT o
  WITH…SELECT, niente DML/PRAGMA), limite 500 righe (clamp dichiarato).
  La query deve referenziare la tabella `_data` (view sul parquet).
  Per i mart usa `layer=mart` e lo slug `dataset__table` (in `marts`/
  `marts_hint` della card, o dal registry del repo produttore).
- **Ref tipizzati**: in caso di collisione di slug (es. repo e analysis
  con lo stesso nome) `explore("analysis:slug")` forza il tipo; senza
  prefisso l'ordine è dataset → repo → domain → source → analysis.
- **Rimozioni (v0.2)**: `github_get_discussion_summary` →
  `github_get_discussion(mode="summary")`;
  `github_get_discussion_comments` →
  `github_get_discussion(include_comments=True)`;
  `github_search_discussions` → `github_list_discussions(query=…)`.
  `refresh_context` è stato rimosso: il rebuild degli artefatti è
  un'azione operativa da CLI/CI di ACB, non da un MCP per esterni.
- **`find.total`** = `total_catalog` + `total_external` (quest'ultimo solo
  con `include_github=true`). Con `include_github=true` la risposta ha
  anche `github_ok` e, se qualcosa fallisce, `github_errors`: un token
  assente/scaduto non viene più confuso con "nessun risultato".
- **Envelope `ok`**: ogni risposta ha `ok` (anche gli errori, con
  `error`/`message`). I 5 tool Discussions hanno `repo_full_name` con
  default `dataciviclab/dataciviclab`.
- **Cache**: schema dei parquet mart (`DESCRIBE`) cachato 10 min come il
  registry — `explore` non ripete il round-trip GCS.
- I nomi dei tool sono contratti cross-repo (skill, docs, config utente):
  non vanno cambiati senza migrazione.

## Sviluppo

```bash
pip install -e ".[dev]"
pytest tests/ -q -m "not smoke"
ruff check src/ tests/
mypy src/dcl_mcp/
```

Storia: questo server nasce dalla migrazione di `dataciviclab-context`
(ACB) e `github-discussions` (lab-connectors) + il nuovo `gcs_query`,
modellato su tre passate di audit con agenti newcomer. Il builder degli
artefatti resta in ACB; le librerie condivise in
[lab-connectors](https://github.com/dataciviclab/lab-connectors).

## Licenza

MIT — vedi [LICENSE](LICENSE).
