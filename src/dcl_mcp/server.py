"""Server MCP unico del DataCivicLab — entry point ``dcl-mcp``.

Registra i tool dei tre moduli (context, discussions, gcs) sull'istanza
FastMCP condivisa e avvia il server stdio.
"""

from __future__ import annotations

from dcl_mcp.app import SERVER_NAME, github_token, load_env, mcp

# Import dei moduli tools: la registrazione dei tool è un side-effect
# dell'import (ogni modulo definisce @mcp.tool su dcl_mcp.app.mcp).
from dcl_mcp.context import tools as _context_tools  # noqa: F401
from dcl_mcp.discussions import tools as _discussions_tools  # noqa: F401
from dcl_mcp.gcs import tools as _gcs_tools  # noqa: F401

__all__ = ["mcp", "main"]


def main() -> None:
    load_env()
    from lab_connectors.mcp import get_mcp_logger

    log = get_mcp_logger(SERVER_NAME)
    # `github_token()` accetta le tre env var: il log non deve dire "assente"
    # quando i tool github_* funzionano con GITHUB_PERSONAL_ACCESS_TOKEN.
    log.info("main", "starting", github_token=bool(github_token()))
    mcp.run()


if __name__ == "__main__":
    main()
