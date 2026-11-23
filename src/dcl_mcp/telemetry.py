"""Search log opzionale: cosa chiedono gli agenti e cosa torna.

Perché esiste: i log del server (`guard_timed`) registrano solo nome tool e
durata. Per migliorare la ricerca serve sapere **quali query** arrivano, quante
tornano vuote e quali dataset finiscono in cima. Il client (OpenCode) ha queste
informazioni, ma sono per-client e spariscono con le sessioni.

Attivazione: `DCL_MCP_SEARCH_LOG=/percorso/chiamate.jsonl`. Spento se l'env non
c'è: nessun file, nessun costo. Ogni riga è un JSON con `pid` (raggruppa una
sessione), argomenti ed esito. Scrittura best-effort: un errore di I/O non deve
mai rompere un tool.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MAX_BYTES = 10 * 1024 * 1024
PATH_ENV = "DCL_MCP_SEARCH_LOG"


def enabled() -> bool:
    """True se il search log è attivo (env impostata e non vuota)."""
    return bool((os.environ.get(PATH_ENV) or "").strip())


def record(
    tool: str,
    args: dict[str, Any],
    outcome: dict[str, Any],
    elapsed_ms: int | None = None,
) -> None:
    """Appende una riga JSONL. Best-effort: non solleva mai eccezioni."""
    path = (os.environ.get(PATH_ENV) or "").strip()
    if not path:
        return
    line: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "tool": tool,
    }
    line.update({k: v for k, v in args.items() if v is not None})
    line.update({k: v for k, v in outcome.items() if v is not None})
    if elapsed_ms is not None:
        line["duration_ms"] = elapsed_ms
    try:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            if target.exists() and target.stat().st_size > _MAX_BYTES:
                target.replace(target.with_name(target.name + ".1"))
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # I/O del log non deve rompere la risposta
        return
