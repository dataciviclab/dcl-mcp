# Contributing a dcl-mcp

Questa guida vale per la repo `dcl-mcp`.

Per le regole GitHub condivise dell'organizzazione, parti prima da
[`.github`](https://github.com/dataciviclab/.github).

## A cosa serve questa repo

`dcl-mcp` è il server MCP unico del DataCivicLab (`dataciviclab`):
contesto org-wide (artefatti ACB), GitHub Discussions e query read-only
sui parquet pubblici GCS.

Qui stanno:

- `src/dcl_mcp/context/` — fetch artefatti branch `context` + navigazione (search/lab_map/explore)
- `src/dcl_mcp/discussions/` — client GraphQL GitHub Discussions + tool
- `src/dcl_mcp/gcs/` — resolver slug → parquet + `gcs_query`
- `tests/` — test contratti tool, logica pura, adapter mockati

Qui non stanno:

- il builder degli artefatti contesto — resta in `agent-context-builder`
- librerie condivise (HTTP, DuckDB, GCS paths, registry) — restano in `lab-connectors`
- pipeline e contratti dataset — restano in `toolkit` e nei repo dati

## Contratti pubblici

I nomi dei tool e del server (`dataciviclab`) sono contratti cross-repo:
skill, `.mcp.json` degli utenti e docs del Lab li referenziano. Cambiarli
richiede una migrazione esplicita, non un rename.

## Verifica

```bash
pytest tests/ -q -m "not smoke"
ruff check src/ tests/
mypy src/dcl_mcp/
```

## Note

- `GITHUB_TOKEN` è opzionale per context e `gcs_query`, obbligatorio per
  le discussions (GraphQL autenticato).
- I test `smoke` usano la rete: sono esclusi dalla CI (`-m "not smoke"`).
