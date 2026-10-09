"""Gettoni di conferma per gli esiti 3.

Un gettone e' casuale (`secrets`), vale pochi minuti, si usa UNA volta ed e'
legato a utente, azione, run e domanda (l'error_type e i `details` dell'esito 3).
Conserva gli argomenti della richiesta originale: confermando, il controller
rilancia QUEGLI argomenti con cio' che la tabella associa all'error_type, e il
browser non puo' cambiare niente. I `details` servono per le conferme che ne usano
i dati (la voce di `CloneOutcomeUnknown`, i candidati di `LessonRemovalRequired`).
La domanda non si ricontrolla alla conferma: se nel frattempo la situazione e'
cambiata di nuovo (es. un brano sostituito una seconda volta), vale la semantica
del flag dell'agente, entro la durata del gettone.

Stanno in memoria (un solo processo uvicorn): un riavvio li perde, e l'utente
ripete l'operazione.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.servizi.errori import ErroreController

MAX_PER_UTENTE = 50


@dataclass
class Gettone:
    valore: str
    utente_id: str
    azione: str
    run: str | None
    error_type: str
    parametri: dict[str, Any]
    details: dict[str, Any]
    scadenza: float               # time.monotonic()
    scade_il: str                 # per il browser
    creato: float = field(default_factory=time.monotonic)


class Gettoni:
    def __init__(self, durata_s: int) -> None:
        self._durata = durata_s
        self._attivi: dict[str, Gettone] = {}
        self._lock = threading.Lock()

    def _pulisci(self, ora: float) -> None:
        for valore in [v for v, g in self._attivi.items() if g.scadenza <= ora]:
            del self._attivi[valore]

    def crea(
        self, *, utente_id: str, azione: str, run: str | None, error_type: str,
        details: dict[str, Any] | None, parametri: dict[str, Any],
    ) -> Gettone:
        ora = time.monotonic()
        scade_il = (datetime.now(timezone.utc) + timedelta(seconds=self._durata)).isoformat(timespec="seconds")
        gettone = Gettone(
            valore=secrets.token_urlsafe(32), utente_id=utente_id, azione=azione, run=run,
            error_type=error_type,
            parametri=json.loads(json.dumps(parametri)), details=json.loads(json.dumps(details or {})),
            scadenza=ora + self._durata, scade_il=scade_il,
        )
        with self._lock:
            self._pulisci(ora)
            propri = sorted((g for g in self._attivi.values() if g.utente_id == utente_id), key=lambda g: g.creato)
            for vecchio in propri[: max(0, len(propri) - MAX_PER_UTENTE + 1)]:
                del self._attivi[vecchio.valore]
            self._attivi[gettone.valore] = gettone
        return gettone

    def consuma(self, valore: str, utente_id: str) -> Gettone:
        """Restituisce il gettone e lo toglie (monouso). Un gettone inesistente,
        scaduto o di un altro utente da' sempre lo stesso errore."""
        if not isinstance(valore, str) or not 20 <= len(valore) <= 100:
            raise ErroreController("GettoneNonValido")
        with self._lock:
            ora = time.monotonic()
            self._pulisci(ora)
            gettone = None
            for v, g in self._attivi.items():
                if secrets.compare_digest(v, valore):
                    gettone = g
                    break
            if gettone is None or gettone.utente_id != utente_id:
                raise ErroreController("GettoneNonValido")
            del self._attivi[gettone.valore]
        return gettone

    def __len__(self) -> int:
        return len(self._attivi)
