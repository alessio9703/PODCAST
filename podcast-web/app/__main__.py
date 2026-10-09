"""Avvio del controller: `python -m app` (anche da systemd).

Un solo processo uvicorn (la coda dei lavori e i gettoni sono in memoria), senza
reload. Se la configurazione o i controlli di avvio non passano, esce con un
codice diverso da zero e il motivo nel log.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from app.config import ErroreConfigurazione, carica_config
from app.main import crea_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = carica_config()
    except ErroreConfigurazione as exc:
        logging.getLogger("podcast_web").error("configurazione non valida: %s", exc)
        return 2
    logging.getLogger().setLevel(config.log_livello)
    uvicorn.run(
        crea_app(config),
        host=config.host,
        port=config.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
        log_level=config.log_livello.lower(),
        timeout_graceful_shutdown=30,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
