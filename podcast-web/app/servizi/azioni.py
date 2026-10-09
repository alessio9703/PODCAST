"""Un'azione della tabella dall'inizio alla fine, per le rotte.

permesso -> argomenti (tabella) -> esecutore -> esito (formato unico) ->
gettone se esito 3 -> registro attivita'. Le rotte non lanciano mai l'agente da
sole: chiamano `esegui_azione` (o, per i lavori lunghi, la coda di `app/lavori`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse

from app.agente import esecutore
from app.agente.tabella import TABELLA, ErroreArgomento, ErroreTabella, RunNonTrovata
from app.config import Config
from app.ingresso import Ingresso, Utente
from app.registro import Registro
from app.servizi.errori import ErroreController
from app.servizi.esiti import Pulitore, valuta
from app.servizi.gettoni import Gettoni
from app.servizi.permessi import Permessi

log = logging.getLogger("podcast_web.azioni")

AVVISO_REGISTRO = ("L'operazione e' stata eseguita, ma non e' stato possibile scriverla nel registro attivita'. "
                   "L'errore e' stato segnalato all'amministratore.")


@dataclass
class Contesto:
    """Tutto cio' che serve alle rotte; sta in `app.state.ctx`."""

    config: Config
    ingresso: Ingresso
    permessi: Permessi
    registro: Registro
    gettoni: Gettoni
    pulitore: Pulitore
    impostazioni: dict[str, Any]
    coda: Any = None  # app.lavori.coda.Coda (A2)


def contesto(request: Request) -> Contesto:
    return request.app.state.ctx


def _costi(dati: Mapping[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(dati, Mapping):
        return None, None
    usd, eur = dati.get("cost_usd"), dati.get("cost_eur")
    usd = float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else None
    eur = float(eur) if isinstance(eur, (int, float)) and not isinstance(eur, bool) else None
    return usd, eur


def errore_da_argomenti(exc: Exception) -> ErroreController:
    if isinstance(exc, ErroreArgomento):
        return ErroreController("RichiestaNonValida", exc.messaggio, {"campo": exc.campo})
    if isinstance(exc, RunNonTrovata):
        return ErroreController("NonTrovato", "Episodio non trovato.", {"campo": "run"})
    log.error("errore di programmazione nella tabella: %s", exc)
    return ErroreController("ErroreInterno")


async def esegui_e_valuta(
    ctx: Contesto,
    utente: Utente,
    azione: str,
    parametri: Mapping[str, Any],
    *,
    flag_conferme: tuple[str, ...] = (),
    parametri_nascosti: Mapping[str, str] | None = None,
    da_gettone: bool = False,
    risposta_a: str | None = None,
) -> tuple[int, dict[str, Any], esecutore.Risultato]:
    """Il cuore comune a rotte e conferme. Solleva ErroreController per gli
    errori del controller (permesso, argomenti)."""
    cmd = TABELLA.get(azione)
    if cmd is None:
        raise errore_da_argomenti(ErroreTabella(f"azione fuori tabella: {azione}"))
    if cmd.lungo:
        raise errore_da_argomenti(ErroreTabella(f"{azione} e' un lavoro lungo: va in coda"))
    if cmd.solo_da_gettone and not da_gettone:
        raise errore_da_argomenti(ErroreTabella(f"{azione} si lancia solo da una conferma"))
    if not ctx.permessi.puo(utente, cmd.permesso):
        if cmd.registra:
            ctx.registro.scrivi(utente=utente, azione=azione, run=parametri.get("run"), esito="NonPermesso", http=403)
        raise ErroreController("NonPermesso", details={"permesso": cmd.permesso})
    try:
        risultato = await esecutore.esegui(
            ctx.config, azione, parametri, by=utente.firma if cmd.by else None,
            flag_conferme=flag_conferme, parametri_nascosti=parametri_nascosti,
        )
    except (ErroreArgomento, RunNonTrovata, ErroreTabella) as exc:
        raise errore_da_argomenti(exc) from None
    stato, corpo = valuta(risultato, ctx.pulitore)
    run = parametri.get("run")
    if corpo.get("esito") == "decisione":
        gettone = ctx.gettoni.crea(
            utente_id=utente.id, azione=azione, run=run, error_type=corpo["error_type"],
            details=(risultato.json or {}).get("details") or {}, parametri=dict(parametri),
        )
        corpo["gettone"] = gettone.valore
        corpo["scade_il"] = gettone.scade_il
    if cmd.registra:
        usd, eur = _costi(risultato.json)
        scritto = ctx.registro.scrivi(
            utente=utente, azione=azione, run=run,
            esito="ok" if corpo["ok"] else corpo["error_type"],
            exit_code=risultato.exit_code, http=stato, costo_stimato_usd=usd, costo_stimato_eur=eur,
            dettagli=_dettagli_registro(azione, parametri, flag_conferme, risultato.json, risposta_a),
        )
        if not scritto:
            corpo["avviso"] = AVVISO_REGISTRO
    return stato, corpo, risultato


def _dettagli_registro(azione: str, parametri: Mapping[str, Any], flag: tuple[str, ...], dati: Any,
                       domanda: str | None = None) -> dict[str, Any]:
    """Pochi dati utili e senza percorsi: versioni, battute, voci, flag di conferma."""
    dettagli: dict[str, Any] = {}
    for chiave in ("versione", "battuta", "nome", "documento", "titolo", "versione_attesa"):
        if chiave in parametri:
            dettagli[chiave] = parametri[chiave]
    if flag:
        dettagli["conferme"] = list(flag)
    if domanda:
        dettagli["risposta_a"] = domanda
    if isinstance(dati, dict):
        for chiave in ("version", "sha256", "approved_version"):
            if chiave in dati and isinstance(dati[chiave], (int, str)):
                dettagli[f"agente_{chiave}"] = dati[chiave]
    return dettagli


async def esegui_azione(request: Request, utente: Utente, azione: str, parametri: Mapping[str, Any]) -> JSONResponse:
    stato, corpo, _ = await esegui_e_valuta(contesto(request), utente, azione, parametri)
    return JSONResponse(corpo, status_code=stato)
