"""AUTH_MODE=demo: utente fisso da configurazione, con tutti i ruoli.

Solo per l'uso in locale: la configurazione rifiuta un HOST diverso da
127.0.0.1, e qui si rifiuta ogni richiesta che non arrivi dal loopback (difesa
se qualcuno lancia uvicorn a mano su 0.0.0.0). Contro il DNS rebinding (una pagina
di un altro sito che si fa risolvere a 127.0.0.1) si accetta solo un header `Host`
di loopback con la porta configurata e, sulle richieste che cambiano qualcosa, un
`Origin` (se c'e') con lo stesso host.
"""

from __future__ import annotations

import logging

from fastapi import Request

from app.config import Config
from app.ingresso import Ingresso, Utente
from app.servizi.errori import ErroreController

log = logging.getLogger("podcast_web.ingresso")

LOOPBACK = ("127.0.0.1", "::1")
NOMI_LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
METODI_CHE_CAMBIANO = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class IngressoDemo(Ingresso):
    def __init__(self, config: Config, ruoli_noti: frozenset[str]) -> None:
        self._utente = Utente(id=config.demo_utente, nome=config.demo_nome, ruoli=ruoli_noti)
        self._host_ammessi = frozenset(f"{n}:{config.port}" for n in NOMI_LOOPBACK)
        self._origin_ammessi = frozenset(f"http://{h}" for h in self._host_ammessi)

    def identifica(self, request: Request) -> Utente:
        client = request.client.host if request.client else None
        if client not in LOOPBACK:
            log.warning("demo: richiesta rifiutata da un indirizzo non di loopback (%s)", client)
            raise ErroreController("NonAutenticato")
        host = request.headers.get("host", "").lower()
        if host not in self._host_ammessi:
            log.warning("demo: header Host non di loopback (%r): possibile DNS rebinding", host)
            raise ErroreController("NonAutenticato")
        origin = request.headers.get("origin")
        if request.method in METODI_CHE_CAMBIANO and origin is not None and origin.lower() not in self._origin_ammessi:
            log.warning("demo: Origin non ammesso (%r)", origin)
            raise ErroreController("NonAutenticato")
        return self._utente
