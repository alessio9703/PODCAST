"""Modulo di ingresso: l'UNICO punto che stabilisce chi e' l'utente.

Nessun altro modulo legge header di identita'. Le rotte ricevono l'utente con la
dipendenza `utente_corrente`; `--by` e il registro attivita' usano solo quello.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Config


@dataclass(frozen=True)
class Utente:
    id: str
    nome: str
    ruoli: frozenset[str]

    @property
    def firma(self) -> str:
        """Il valore di `--by`: il nome da mostrare ("Approvato da ...")."""
        return self.nome or self.id


class Ingresso:
    def identifica(self, request: Request) -> Utente:  # pragma: no cover - interfaccia
        raise NotImplementedError


def crea_ingresso(config: Config, ruoli_noti: frozenset[str]) -> Ingresso:
    if config.auth_mode == "demo":
        from app.ingresso.demo import IngressoDemo

        return IngressoDemo(config, ruoli_noti)
    from app.ingresso.cms import IngressoCms

    return IngressoCms(config, ruoli_noti)


async def utente_corrente(request: Request) -> Utente:
    """Dipendenza FastAPI. L'ingresso e' gia' stato verificato dal middleware:
    qui si rilegge il risultato, senza ricalcolarlo."""
    utente = getattr(request.state, "utente", None)
    if utente is None:
        utente = request.app.state.ingresso.identifica(request)
        request.state.utente = utente
    return utente
