"""Guard con envelope ``ok`` per i tool dcl-mcp.

``guard_timed`` di lab-connectors logga e converte le eccezioni in
``{"error", "message"}``, ma non aggiunge ``ok``. I tool di successo
invece lo espongono: senza ``ok: false`` un client non può controllare
un solo campo per capire se la chiamata è fallita.

Questo wrapper è locale a dcl-mcp per non cambiare lab-connectors
(condiviso con toolkit e source-observatory).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lab_connectors.mcp import guard_timed

Fn = Callable[..., Any]


def guard_ok(
    fn: Fn,
    tool_name: str,
    *args: Any,
    logger_name: str | None = None,
    slow_ms: int = 5000,
    **kwargs: Any,
) -> Any:
    """Esegue *fn* con logging e garantisce ``ok`` nella risposta."""
    result = guard_timed(
        fn,
        tool_name,
        *args,
        logger_name=logger_name,
        slow_ms=slow_ms,
        **kwargs,
    )
    if isinstance(result, dict):
        result.setdefault("ok", "error" not in result)
    return result
