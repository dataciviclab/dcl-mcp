# dcl-mcp — il MCP del DataCivicLab

Server MCP unico per partecipare all'ecosistema del Lab: contesto
org-wide, GitHub Discussions e query read-only sui dati pubblici.

```
pip install "lab-connectors[mcp,duckdb] @ git+https://github.com/dataciviclab/lab-connectors.git"
pip install "dcl-mcp @ git+https://github.com/dataciviclab/dcl-mcp.git"
```

## Cosa espone

Un solo server, nome `dataciviclab` — 14 tool in tre famiglie:

| Famiglia | Tool | Cosa fa |
|---|---|---|
| **Context** | `session_bootstrap` | Orientamento rapido (markdown) |
| | `lab_map(domain=)` | Albero domains → repos → sources |
| | `find(query, type=, domain=)` | Ricerca ranked cross-entity |
| | `explore(ref)` | Deep-dive di dataset/repo/domain/source/analysis |
| | `workspace_triage(section=)` | Stato Lab: radar, PR, issues, discussions… |
| | `refresh_context()` | Trigger rebuild CI degli artefatti (serve `GITHUB_TOKEN`) |
| **Discussions** | `github_list_discussions` | Elenca discussioni (cursor pagination) |
| | `github_get_discussion` / `_summary` / `_comments` | Leggi una discussion e i commenti |
| | `github_search_discussions` | Full-text search nel repo |
| | `github_create_discussion` / `github_add_discussion_comment` | Partecipa (scrittura) |
| **GCS** | `gcs_query(slug, sql, layer=, limit=)` | SELECT read-only sui parquet pubblici |

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
| `GITHUB_TOKEN` | Discussions (GraphQL richiede auth anche in lettura) + `refresh_context` | sì, per discussions/write |
| `DCL_MCP_ENV_FILE` | Path a un file `.env` (fallback: `.env` cercato verso l'alto dal cwd) | no |
| `ACB_REPO` / `ACB_BRANCH` | Sorgente artefatti contesto (default: ACB, branch `context`) | no |

## Note di design

- **Context** legge da remoto gli artefatti prodotti dalla CI di
  [agent-context-builder](https://github.com/dataciviclab/agent-context-builder)
  sul branch `context` (raw.githubusercontent, cache TTL 120s). Nessuna
  checkout locale richiesta.
- **GCS** è read-only: solo bucket `dataciviclab-clean` e
  `dataciviclab-mart`, una sola SELECT per chiamata, limite 500 righe.
  La query deve referenziare la tabella `_data` (view sul parquet).
  Per i mart usa `layer=mart` e lo slug `dataset__table` (da `find`/`explore`
  o dal registry del repo produttore).
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
(ACB) e `github-discussions` (lab-connectors) + il nuovo `gcs_query`.
Il builder degli artefatti resta in ACB; le librerie condivise in
[lab-connectors](https://github.com/dataciviclab/lab-connectors).

## Licenza

MIT — vedi [LICENSE](LICENSE).
