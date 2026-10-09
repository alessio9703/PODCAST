"""Permessi: tabella ruolo -> azioni, da configurazione.

Dalla sezione "Autorizzazioni" del contratto. Il controllo vero e' sempre sul
server; `GET /api/io` da' alla UI l'elenco solo per nascondere i pulsanti.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ingresso import Utente
from app.servizi.errori import ErroreController

PERMESSI: dict[str, str] = {
    "leggere": "Archivio, viste, ascolto e download",
    "documento_carica": "caricare o sostituire documenti",
    "episodio_modifica": "creare episodi, passi 1-3, scelta dei take, rimontaggio",
    "copione_scrivi": "stesura e revisione del copione (costano)",
    "copione_modifica": "modificare una battuta, ripristinare una versione",
    "copione_approva": "approvare il copione o ritirare l'approvazione",
    "produrre": "produzione e retake (costano), conferma di un brano sostituito",
    "voce_consenso": "registrare o revocare il consenso di una voce",
    "voce_clonazione": "confermare una nuova clonazione dopo un esito incerto (spesa)",
    "voce_carica": "caricare o sostituire registrazioni di voci",
    "voce_nome": "cambiare il nome pronunciato di una voce",
    "brano_carica": "caricare o sostituire brani",
    "brano_metadati": "licenza, parlato e trascrizione dei brani",
    "lezioni": "proporre, accettare, rifiutare lezioni",
    "diagnostica": "diagnostica e impostazioni",
}

RUOLI_INIZIALI = frozenset({"autore", "approvatore", "gestore_voci", "amministratore"})
DEFAULT = Path(__file__).with_name("permessi_default.json")


class ErrorePermessi(Exception):
    """File dei permessi non valido: il controller non parte."""


class Permessi:
    def __init__(self, tabella: dict[str, frozenset[str]]) -> None:
        self._tabella = tabella

    @classmethod
    def da_file(cls, percorso: Path | None) -> "Permessi":
        percorso = percorso or DEFAULT
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ErrorePermessi(f"file dei permessi illeggibile: {percorso.name} ({exc.__class__.__name__})") from None
        ruoli = dati.get("ruoli") if isinstance(dati, dict) else None
        if not isinstance(ruoli, dict) or not ruoli:
            raise ErrorePermessi("il file dei permessi deve avere 'ruoli': {ruolo: [permessi]}")
        tabella: dict[str, frozenset[str]] = {}
        for ruolo, elenco in ruoli.items():
            if not isinstance(elenco, list) or not all(isinstance(p, str) for p in elenco):
                raise ErrorePermessi(f"permessi del ruolo {ruolo!r}: serve una lista di nomi")
            if "*" in elenco:
                tabella[ruolo] = frozenset(PERMESSI)
                continue
            ignoti = set(elenco) - set(PERMESSI)
            if ignoti:
                raise ErrorePermessi(f"permessi sconosciuti per {ruolo!r}: {sorted(ignoti)}")
            tabella[ruolo] = frozenset(elenco)
        return cls(tabella)

    @property
    def ruoli(self) -> frozenset[str]:
        return frozenset(self._tabella)

    def di(self, utente: Utente) -> frozenset[str]:
        concessi: set[str] = set()
        for ruolo in utente.ruoli:
            concessi |= self._tabella.get(ruolo, frozenset())
        return frozenset(concessi)

    def puo(self, utente: Utente, permesso: str) -> bool:
        if permesso not in PERMESSI:
            raise ValueError(f"permesso sconosciuto: {permesso}")
        return permesso in self.di(utente)

    def richiedi(self, utente: Utente, permesso: str) -> None:
        if not self.puo(utente, permesso):
            raise ErroreController("NonPermesso", details={"permesso": permesso})
