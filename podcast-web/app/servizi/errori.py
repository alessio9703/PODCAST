"""Errori del controller (non dell'agente).

Hanno `esito: "controller"` e un elenco chiuso di nomi, in italiano per distinguerli
dagli `error_type` dell'agente (§3 del contratto). Il formato verso il browser e'
lo stesso di ogni altro esito (`servizi/esiti.py`).
"""

from __future__ import annotations

from typing import Any

# nome -> stato HTTP
ERRORI_CONTROLLER: dict[str, int] = {
    "RichiestaNonValida": 400,
    "NonAutenticato": 401,
    "NonPermesso": 403,
    "NonTrovato": 404,
    "MetodoNonAmmesso": 405,
    "FileEsistente": 409,
    "GettoneNonValido": 409,
    "LavoroGiaInCorso": 409,
    "LavoroAnnullato": 409,
    "StimaCambiata": 409,
    "ConsensoDopoRevoca": 409,
    "TroppeConnessioni": 429,
    "FileTroppoGrande": 413,
    "FileNonAmmesso": 415,
    "ErroreInterno": 500,
    "CodaPiena": 503,
    "NonPronto": 503,
    "Timeout": 504,
}

MESSAGGI_DEFAULT: dict[str, str] = {
    "RichiestaNonValida": "Richiesta non valida.",
    "NonAutenticato": "Accesso non autorizzato.",
    "NonPermesso": "Non hai il permesso per questa azione.",
    "NonTrovato": "Elemento non trovato.",
    "MetodoNonAmmesso": "Operazione non ammessa.",
    "GettoneNonValido": "La conferma non e' piu' valida: ripeti l'operazione.",
    "ErroreInterno": "Errore interno del controller. L'errore e' stato registrato.",
    "CodaPiena": "Troppi lavori in attesa: riprova fra qualche minuto.",
    "TroppeConnessioni": "Troppe pagine aperte che seguono l'avanzamento.",
    "StimaCambiata": "La stima è cambiata da quando l'hai vista: ricontrolla costo e voci prima di generare.",
    "ConsensoDopoRevoca": ("Per questa registrazione il consenso era stato revocato. Registrarlo di nuovo toglie la revoca: "
                           "serve una conferma esplicita."),
    "NonPronto": "Servizio non pronto.",
    "Timeout": "L'operazione ha impiegato troppo tempo ed e' stata fermata.",
}


class ErroreController(Exception):
    def __init__(
        self,
        error_type: str,
        messaggio: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        gettone: str | None = None,
        scade_il: str | None = None,
    ) -> None:
        if error_type not in ERRORI_CONTROLLER:
            raise ValueError(f"errore del controller sconosciuto: {error_type}")
        super().__init__(error_type)
        self.error_type = error_type
        self.status = ERRORI_CONTROLLER[error_type]
        self.messaggio = messaggio or MESSAGGI_DEFAULT.get(error_type, error_type)
        self.details = details
        self.gettone = gettone
        self.scade_il = scade_il

    def corpo(self) -> dict[str, Any]:
        corpo: dict[str, Any] = {
            "ok": False,
            "esito": "controller",
            "error_type": self.error_type,
            "messaggio": self.messaggio,
            "riprovabile": self.error_type in ("CodaPiena", "Timeout", "LavoroGiaInCorso", "TroppeConnessioni"),
        }
        if self.details:
            corpo["details"] = self.details
        if self.gettone:
            corpo["gettone"] = self.gettone
            corpo["scade_il"] = self.scade_il
        return corpo
