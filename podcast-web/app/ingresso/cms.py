"""AUTH_MODE=cms: identita' dagli header firmati dal CMS di LIFE.

Specifica per il dev del CMS (copiata in DEPLOY.md). Per cambiare i nomi degli
header basta cambiare le costanti qui sotto: nessun altro modulo li conosce.

Header di ogni richiesta inoltrata dal CMS:

    X-LIL-Utente     id stabile dell'utente, ^[A-Za-z0-9._@-]{1,100}$
    X-LIL-Nome       nome da mostrare (finisce in --by), UTF-8 percent-encoded
    X-LIL-Ruoli      ruoli separati da virgola, senza spazi: autore,approvatore
    X-LIL-Timestamp  secondi Unix, intero
    X-LIL-Firma      v1=<hex minuscolo di HMAC-SHA256(segreto, stringa canonica)>

Stringa canonica: queste righe unite da "\\n", in UTF-8, senza "\\n" finale:

    v1
    <timestamp>
    <metodo in maiuscolo>
    <percorso come arriva al controller, con "?query" se c'e'>
    <utente>
    <ruoli, esattamente come nell'header>
    <nome, esattamente come nell'header (percent-encoded)>

Il corpo non e' firmato: lo protegge l'HTTPS fra CMS e VPS.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from urllib.parse import quote, unquote

from fastapi import Request

from app.config import Config
from app.ingresso import Ingresso, Utente
from app.servizi.errori import ErroreController

log = logging.getLogger("podcast_web.ingresso")

HEADER_UTENTE = "X-LIL-Utente"
HEADER_NOME = "X-LIL-Nome"
HEADER_RUOLI = "X-LIL-Ruoli"
HEADER_TIMESTAMP = "X-LIL-Timestamp"
HEADER_FIRMA = "X-LIL-Firma"
HEADER_IDENTITA = (HEADER_UTENTE, HEADER_NOME, HEADER_RUOLI, HEADER_TIMESTAMP, HEADER_FIRMA)

VERSIONE_FIRMA = "v1"
_ID = re.compile(r"^[A-Za-z0-9._@-]{1,100}$")
_RUOLO = re.compile(r"^[a-z_]{1,40}$")
_FIRMA = re.compile(r"^v1=[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^[0-9]{1,12}$")
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f]")
NOME_MAX = 100


def codifica_header(testo: str) -> str:
    """Testo UTF-8 -> valore di header ASCII (percent-encoding, come X-LIL-Nome)."""
    return quote(testo, safe="")


def decodifica_header(valore: str) -> str:
    """Inverso di `codifica_header`. Solleva ValueError se non e' UTF-8 valido."""
    return unquote(valore, encoding="utf-8", errors="strict")


def stringa_canonica(timestamp: str, metodo: str, percorso: str, utente: str, ruoli: str, nome: str) -> bytes:
    righe = [VERSIONE_FIRMA, timestamp, metodo.upper(), percorso, utente, ruoli, nome]
    return "\n".join(righe).encode("utf-8")


def calcola_firma(segreto: bytes, timestamp: str, metodo: str, percorso: str, utente: str, ruoli: str, nome: str) -> str:
    digest = hmac.new(segreto, stringa_canonica(timestamp, metodo, percorso, utente, ruoli, nome), hashlib.sha256)
    return f"{VERSIONE_FIRMA}={digest.hexdigest()}"


def percorso_ricevuto(request: Request) -> str:
    """Il percorso esattamente come e' arrivato (non decodificato), con la query."""
    scope = request.scope
    grezzo = scope.get("raw_path") or scope["path"].encode("utf-8")
    percorso = grezzo.decode("latin-1")
    query = scope.get("query_string", b"").decode("latin-1")
    return f"{percorso}?{query}" if query else percorso


class IngressoCms(Ingresso):
    def __init__(self, config: Config, ruoli_noti: frozenset[str]) -> None:
        assert config.segreto is not None
        self._segreti = [s for s in (config.segreto, config.segreto_precedente) if s]
        self._tolleranza = config.firma_tolleranza_s
        self._ruoli_noti = ruoli_noti

    def _rifiuta(self, motivo: str) -> ErroreController:
        # Il motivo resta nel log del server; al browser nessun dettaglio.
        log.warning("cms: richiesta rifiutata: %s", motivo)
        return ErroreController("NonAutenticato")

    def identifica(self, request: Request) -> Utente:
        h = request.headers
        if any(len(h.getlist(nome)) > 1 for nome in HEADER_IDENTITA):
            raise self._rifiuta("header di identita' duplicati")
        utente = h.get(HEADER_UTENTE)
        nome = h.get(HEADER_NOME)
        ruoli = h.get(HEADER_RUOLI)
        timestamp = h.get(HEADER_TIMESTAMP)
        firma = h.get(HEADER_FIRMA)
        if None in (utente, nome, ruoli, timestamp, firma):
            raise self._rifiuta("header di identita' mancanti")
        if not _ID.match(utente) or not _TIMESTAMP.match(timestamp) or not _FIRMA.match(firma):
            raise self._rifiuta("header di identita' malformati")
        if abs(time.time() - int(timestamp)) > self._tolleranza:
            raise self._rifiuta("timestamp fuori tolleranza")
        percorso = percorso_ricevuto(request)
        valida = any(
            hmac.compare_digest(calcola_firma(s, timestamp, request.method, percorso, utente, ruoli, nome), firma)
            for s in self._segreti
        )
        if not valida:
            raise self._rifiuta("firma non valida")
        # Firma valida: i valori vengono dal CMS. Si controllano comunque.
        try:
            nome_chiaro = decodifica_header(nome).strip()
        except ValueError:
            raise self._rifiuta("X-LIL-Nome non e' UTF-8 valido") from None
        if len(nome_chiaro) > NOME_MAX or _CONTROLLO.search(nome_chiaro):
            raise self._rifiuta("X-LIL-Nome non valido")
        elenco = {r for r in ruoli.split(",") if r}
        if any(not _RUOLO.match(r) for r in elenco):
            raise self._rifiuta("X-LIL-Ruoli malformato")
        sconosciuti = elenco - self._ruoli_noti
        if sconosciuti:
            log.info("cms: ruoli sconosciuti ignorati: %s", sorted(sconosciuti))
        return Utente(id=utente, nome=nome_chiaro or utente, ruoli=frozenset(elenco & self._ruoli_noti))
