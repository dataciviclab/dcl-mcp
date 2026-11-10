# dcl-mcp — il MCP del DataCivicLab

Il modo più rapido per **consultare il Lab e partecipare alle sue discussioni**
da un agente AI, senza clonare nessun repository.

Un solo server, nome `dataciviclab`, 12 tool.

## Cosa ti dà

| Puoi… | Con | Cosa ottieni |
|---|---|---|
| capire com'è organizzato il Lab | `session_bootstrap`, `lab_map` | domini, repo, fonti, stato recente |
| trovare un dataset o un'analisi | `find`, `explore` | slug, anni, colonne, marts, dove sta il parquet |
| leggere i dati pubblicati | `gcs_query` | SELECT su clean e mart, read-only (max 500 righe) |
| capire se due dataset si possono unire | `join_suggestions` | coppie di colonne, overlap anni, mismatch di tipo |
| vedere cosa è aperto | `workspace_triage`, `find(include_github=true)` | PR, issue, discussioni del tema |
| partecipare | `github_get_discussion`, `github_create_discussion`, `github_add_discussion_comment` | leggi, apri, commenta |

## Cosa non puoi fare

Il server è **read-only sui dati** e **non esegue pipeline**:

- non scarica né trasforma dati → usa `toolkit`, sul repo locale
- non modifica file nei repo né apre PR → usa il server `github`
- non pubblica dataset né rigenera il catalogo → lo fa la CI di
  [agent-context-builder](https://github.com/dataciviclab/agent-context-builder)

## Tre percorsi

### 1. Mi oriento

```
session_bootstrap()          # cosa è successo nel Lab
lab_map()                    # domini → repo → fonti
find("rifiuti urbani")       # cerca dataset, analisi, repo, fonti
explore("ispra_ru_base")     # card: anni, colonne, marts, gcs_path
```

### 2. Mi serve un numero verificabile

```
find("irpef comunale")        # trova lo slug
explore("irpef_comunale")     # controlla colonne e periodo
gcs_query(slug="irpef_comunale", sql="SELECT count(*) FROM _data")
```

`clean_rows` nella card è il conteggio dell'ultimo snapshot pubblicato, **non
la serie completa**: per un numero citabile usa sempre `count(*)` via
`gcs_query`.

### 3. Voglio partecipare

```
find("appalti", include_github=true)      # cosa è già aperto
github_get_discussion(number=123, include_comments=true)
github_list_discussion_categories()       # prima di creare
github_create_discussion(category_name="…", title=…, body=…)
github_add_discussion_comment(number=123, body=…)
```

## Installazione

```bash
pip install "lab-connectors[mcp,duckdb] @ git+https://github.com/dataciviclab/lab-connectors.git"
pip install "dcl-mcp @ git+https://github.com/dataciviclab/dcl-mcp.git"
```

Config OpenCode (V2):

```jsonc
{
  "mcp": {
    "servers": {
      "dataciviclab": {
        "type": "local",
        "command": ["dcl-mcp"],
        "environment": { "DCL_MCP_ENV_FILE": "/percorso/.env" }
      }
    }
  }
}
```

Per altri client che usano il formato `mcpServers`, l'entry è
`{"command": "dcl-mcp", "env": {...}}`.

| Env var | Ruolo | Obbligatoria |
|---|---|---|
| `GITHUB_TOKEN` (o `GITHUB_PERSONAL_ACCESS_TOKEN` / `GH_TOKEN`) | Discussions e `find(include_github=true)` — GraphQL richiede auth anche in lettura | sì, per discussions/write |
| `DCL_MCP_ENV_FILE` | Path a un file `.env` (fallback: `.env` cercato verso l'alto dal cwd) | no |
| `ACB_REPO` / `ACB_BRANCH` | Sorgente artefatti contesto (default: ACB, branch `context`) | no |

## Riferimento tool

| Famiglia | Tool | Cosa fa |
|---|---|---|
| **Context** | `session_bootstrap` | Orientamento rapido (markdown) |
| | `lab_map(domain=)` | Domini → repo → fonti; con `domain=` i totali sono quelli del dominio |
| | `find(query, type=, domain=, include_github=)` | Ricerca ranked cross-entity, con `total_catalog` e `returned` |
| | `explore(ref)` | Deep-dive di dataset/repo/domain/source/analysis |
| | `join_suggestions(slug, limit=, min_years=, include_names=)` | Dataset joinabili dai semantic_type: colonne, overlap anni, `type_mismatch` |
| | `workspace_triage(section=)` | Stato Lab: radar, PR, issue, discussioni |
| **Discussions** | `github_list_discussions(query=, category_name=)` | Elenca o cerca full-text/categoria |
| | `github_list_discussion_categories` | Categorie valide per la creazione |
| | `github_get_discussion(include_comments=)` | Legge una discussion, con commenti e reply |
| | `github_create_discussion` | Apre una discussion |
| | `github_add_discussion_comment` | Commenta una discussion |
| **GCS** | `gcs_query(slug, sql, layer=, limit=)` | SELECT/CTE read-only sui parquet pubblici |

Ogni risposta ha `ok` (anche gli errori, con `error`/`message`). I tool
Discussions usano `repo_full_name` con default `dataciviclab/dataciviclab`.

## Limiti e sicurezza

- **GCS read-only**: solo i bucket `dataciviclab-clean` e `dataciviclab-mart`,
  una sola istruzione per chiamata, max 500 righe, tabella `_data` (view sul
  parquet). Nessun DML/PRAGMA.
- **È un guardrail, non un sandbox**: non eseguire query di terzi non fidati.
  I dettagli della validazione AST sono in [docs/DESIGN.md](docs/DESIGN.md).
- **Nomi dei tool = contratto cross-repo** (skill, config utente, docs): non
  cambiano senza migrazione.

## Altri server del Lab

| Server | A cosa serve |
|---|---|
| **dcl-mcp** (questo) | org-wide: dove cerco, cosa è successo, come partecipo, dati pubblicati |
| **toolkit** | repo locale: pipeline, contratti, SQL su raw/clean/mart del workspace |
| **source-observatory** | scouting fonti e inventory |
| **github** | leggere/scrivere un issue o una PR specifica |

`find(include_github=true)` qui è **scoperta Lab-scoped** (issue/PR aperte i
cui titoli contengono i token della query), non sostituisce il server `github`.

## Sviluppo

```bash
pip install -e ".[dev]"
pytest tests/ -q -m "not smoke"
pytest tests/test_smoke.py -q -m smoke   # golden path, rete richiesta
ruff check src/ tests/
mypy src/dcl_mcp/
```

Storia e scelte di design: [docs/DESIGN.md](docs/DESIGN.md).
Cambi di contratto: [CHANGELOG.md](CHANGELOG.md).

## Licenza

MIT — vedi [LICENSE](LICENSE).
