"""Errori con un esito strutturato, leggibile da una macchina.

Il contratto dei tool (tools/_common.py) traduce ogni eccezione in
`{"ok": false, "error_type", "error", "exit_code"}`. Per un agente che legge il
messaggio basta; per un'interfaccia no: deve poter mostrare i candidati di un
nome ambiguo come pulsanti, o le voci senza consenso come caselle da spuntare,
senza fare il parsing di una frase in italiano.

Per questo un errore puo' portare:

  - `error_type`: un nome STABILE, che non cambia se cambia il testo. Di
    default e' il nome della classe;
  - `details`: un dizionario serializzabile in JSON con i dati dell'esito.

Vivono in src/ e non in tools/ perche' li sollevano i moduli di dominio: e'
src/ a sapere che una voce non ha il consenso, non il tool che lo chiama.
"""

from __future__ import annotations

from typing import Any


class _Structured:
    """Comportamento comune: `error_type` stabile + `details` opzionali."""

    #: None = usa il nome della classe.
    error_type: str | None = None

    def _init_structured(self, error_type: str | None, details: dict[str, Any] | None) -> None:
        if error_type:
            self.error_type = error_type
        self.details: dict[str, Any] | None = details

    @property
    def kind(self) -> str:
        return self.error_type or type(self).__name__


class DomainError(_Structured, RuntimeError):
    """Errore correggibile dall'utente (exit 1), con dati strutturati."""

    def __init__(
        self, message: str, *, error_type: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self._init_structured(error_type, details)


class NeedsUserDecision(_Structured, RuntimeError):
    """Serve una scelta dell'utente prima di poter proseguire (exit 3).

    Il comando non ha fatto niente di irreversibile: va richiamato con la
    risposta (un nome esatto, un flag esplicito). `details` contiene quello che
    serve all'interfaccia per porre la domanda.
    """

    def __init__(
        self, message: str, *, error_type: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self._init_structured(error_type, details)


class AmbiguousName(NeedsUserDecision):
    """Un nome digitato corrisponde a piu' candidati: non si indovina."""


def error_details(exc: BaseException) -> dict[str, Any] | None:
    """I `details` di un'eccezione, se ne ha (anche se non deriva da qui)."""
    details = getattr(exc, "details", None)
    return details if isinstance(details, dict) else None


__all__ = ["DomainError", "NeedsUserDecision", "AmbiguousName", "error_details"]
