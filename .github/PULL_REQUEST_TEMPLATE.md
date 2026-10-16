## Sintesi

Descrivi in poche righe cosa cambia e perché.

## Contesto collegato

Closes #

## Cosa cambia

- [ ] Tool context (session_bootstrap / triage / lab_map / find / explore / refresh)
- [ ] Tool discussions (github_*)
- [ ] Tool GCS (gcs_query / resolver)
- [ ] Migrazione o refactor interno
- [ ] Bug fix
- [ ] CI / dipendenze
- [ ] Documentazione

## Contratti pubblici

I nomi dei tool e dei server MCP sono contratti cross-repo (skill, config utenti, docs).

- [ ] Nessuna modifica a nomi tool / firme / formati di risposta
- [ ] Modifica contratto pubblico → motivata e documentata nella PR

## Verifica

```bash
pytest tests/ -q -m "not smoke"
ruff check src/ tests/
mypy src/dcl_mcp/
```

- [ ] `pytest` passa
- [ ] `ruff check` passa
- [ ] `mypy` passa

## Checklist PR

- [ ] Perimetro stretto: una PR = un tema o un fix
- [ ] Issue collegata o motivazione dell'assenza

## Note per chi revisiona

Rischi, limiti, punti da controllare con attenzione.
