"""Registro attivita': file JSONL append-only.

Una riga per azione che cambia qualcosa: chi, quando, azione, run, esito, costo
stimato se c'e'. Il file si apre sempre in append (`O_APPEND`), una scrittura
per riga, `fsync`, permessi 0600; il controller non lo riscrive e non lo legge.
Obbligatorio per approvazioni, consensi, revoche, produzioni (e per tutte le
altre azioni che cambiano qualcosa).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingresso import Utente

log = logging.getLogger("podcast_web.registro")

CAMPI_DETTAGLI_MAX = 2000


class ErroreRegistro(Exception):
    """Il registro non e' scrivibile: il controller non parte."""


def adesso_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registro:
    def __init__(self, percorso: Path) -> None:
        self._percorso = percorso
        self._lock = threading.Lock()

    @property
    def percorso(self) -> Path:
        return self._percorso

    def verifica(self) -> None:
        """All'avvio: la cartella esiste (o si crea, 0700) e il file e' scrivibile.
        File e cartella devono essere del processo e chiusi agli altri (niente
        permessi per gruppo o altri): altrimenti il controller non parte."""
        try:
            cartella = self._percorso.parent
            cartella.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._percorso.is_symlink() or cartella.is_symlink():
                raise ErroreRegistro("il registro attivita' (o la sua cartella) non puo' essere un link simbolico")
            fd = os.open(self._percorso, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info_file = os.fstat(fd)
            finally:
                os.close(fd)
            info_cartella = os.stat(cartella)
        except OSError as exc:
            raise ErroreRegistro(f"registro attivita' non scrivibile ({exc.__class__.__name__})") from None
        uid = os.geteuid()
        if info_file.st_uid != uid or info_cartella.st_uid != uid:
            raise ErroreRegistro("il registro attivita' e la sua cartella devono appartenere all'utente del servizio")
        if stat.S_IMODE(info_file.st_mode) & 0o077:
            raise ErroreRegistro(f"permessi del registro troppo larghi ({oct(stat.S_IMODE(info_file.st_mode))}): servono 0600")
        if stat.S_IMODE(info_cartella.st_mode) & 0o077:
            raise ErroreRegistro(
                f"permessi della cartella del registro troppo larghi ({oct(stat.S_IMODE(info_cartella.st_mode))}): servono 0700")

    def scrivi(
        self,
        *,
        utente: Utente,
        azione: str,
        esito: str,
        run: str | None = None,
        exit_code: int | None = None,
        http: int | None = None,
        costo_stimato_usd: float | None = None,
        costo_stimato_eur: float | None = None,
        lavoro: str | None = None,
        dettagli: dict[str, Any] | None = None,
    ) -> bool:
        """Scrive una riga. Restituisce False se non ci e' riuscito (riga nel log
        critico): chi chiama risponde con l'esito vero dell'azione piu' un avviso."""
        riga: dict[str, Any] = {
            "at": adesso_iso(),
            "utente": utente.id,
            "nome": utente.nome,
            "azione": azione,
            "run": run,
            "esito": esito,
        }
        if exit_code is not None:
            riga["exit_code"] = exit_code
        if http is not None:
            riga["http"] = http
        if costo_stimato_usd is not None:
            riga["costo_stimato_usd"] = costo_stimato_usd
        if costo_stimato_eur is not None:
            riga["costo_stimato_eur"] = costo_stimato_eur
        if lavoro is not None:
            riga["lavoro"] = lavoro
        if dettagli:
            testo = json.dumps(dettagli, ensure_ascii=False)
            riga["dettagli"] = dettagli if len(testo) <= CAMPI_DETTAGLI_MAX else {"troncati": testo[:CAMPI_DETTAGLI_MAX]}
        testo = json.dumps(riga, ensure_ascii=False)
        dati = (testo + "\n").encode("utf-8")
        try:
            with self._lock:
                fd = os.open(self._percorso, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                try:
                    scritti = os.write(fd, dati)
                    if scritti != len(dati):
                        raise OSError("scrittura parziale")
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            # L'azione e' gia' avvenuta: la riga non deve perdersi. Resta nel log del
            # servizio (journald) per intero, da riportare a mano nel registro.
            log.critical("REGISTRO ATTIVITA' NON SCRITTO (%s): %s", exc.__class__.__name__, testo)
            return False
        return True
