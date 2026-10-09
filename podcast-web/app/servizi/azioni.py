"""Un'azione della tabella dall'inizio alla fine, per le rotte.

permesso -> argomenti (tabella) -> esecutore -> esito (formato unico) ->
gettone se esito 3 -> registro attivita'. Le rotte non lanciano mai l'agente da
sole: chiamano `esegui_azione` (o, per i lavori lunghi, la coda di `app/lavori`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
    coda: Any = None  # app.lavori.coda.Coda
    sse_aperte: dict[str, int] = field(default_factory=dict)  # utente -> stream SSE aperti


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


def controlla_azione(ctx: Contesto, utente: Utente, azione: str, parametri: Mapping[str, Any], *,
                     da_gettone: bool = False, lungo: bool = False, flag_conferme: tuple[str, ...] = (),
                     parametri_nascosti: Mapping[str, str] | None = None) -> list[str]:
    """Permesso e argomenti PRIMA di lanciare o accodare: restituisce la riga di
    comando completa. Solleva ErroreController (403, 400, 404, 500)."""
    cmd = TABELLA.get(azione)
    if cmd is None:
        raise errore_da_argomenti(ErroreTabella(f"azione fuori tabella: {azione}"))
    if cmd.lungo != lungo:
        raise errore_da_argomenti(ErroreTabella(f"{azione}: lungo={cmd.lungo}, chiamata con lungo={lungo}"))
    if cmd.solo_da_gettone and not da_gettone:
        raise errore_da_argomenti(ErroreTabella(f"{azione} si lancia solo da una conferma"))
    if not ctx.permessi.puo(utente, cmd.permesso):
        if cmd.registra:
            ctx.registro.scrivi(utente=utente, azione=azione, run=parametri.get("run"), esito="NonPermesso", http=403)
        raise ErroreController("NonPermesso", details={"permesso": cmd.permesso})
    try:
        return esecutore.riga_di_comando(
            ctx.config, azione, parametri, by=utente.firma if cmd.by else None,
            flag_conferme=flag_conferme, parametri_nascosti=parametri_nascosti,
        )
    except (ErroreArgomento, RunNonTrovata, ErroreTabella) as exc:
        raise errore_da_argomenti(exc) from None


def chiudi_esito(
    ctx: Contesto, utente: Utente, azione: str, parametri: Mapping[str, Any], risultato: esecutore.Risultato, *,
    flag_conferme: tuple[str, ...] = (), risposta_a: str | None = None, lavoro: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Dal risultato dell'agente alla risposta: formato unico, gettone per gli
    esiti 3 (dell'utente che ha lanciato l'azione), riga nel registro."""
    cmd = TABELLA[azione]
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
            lavoro=lavoro,
            dettagli=_dettagli_registro(azione, parametri, flag_conferme, risultato.json, risposta_a),
        )
        if not scritto:
            corpo["avviso"] = AVVISO_REGISTRO
    return stato, corpo


# Riferimenti ai compiti protetti: un task senza riferimenti puo' essere raccolto.
_PROTETTI: set[asyncio.Task[Any]] = set()


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
    """Il cuore comune a rotte e conferme per le azioni BREVI.

    Le azioni che cambiano qualcosa (`registra`) sono protette dalla
    disconnessione del browser: se la richiesta viene cancellata, il comando
    dell'agente finisce comunque e la riga del registro si scrive (K23). Le
    letture invece si annullano (il processo riceve SIGTERM)."""
    controlla_azione(ctx, utente, azione, parametri, da_gettone=da_gettone,
                     flag_conferme=flag_conferme, parametri_nascosti=parametri_nascosti)
    cmd = TABELLA[azione]

    async def esegui_e_chiudi() -> tuple[int, dict[str, Any], esecutore.Risultato]:
        risultato = await esecutore.esegui(
            ctx.config, azione, parametri, by=utente.firma if cmd.by else None,
            flag_conferme=flag_conferme, parametri_nascosti=parametri_nascosti,
        )
        stato, corpo = chiudi_esito(ctx, utente, azione, parametri, risultato,
                                    flag_conferme=flag_conferme, risposta_a=risposta_a)
        return stato, corpo, risultato

    if not cmd.registra:
        return await esegui_e_chiudi()
    compito = asyncio.ensure_future(esegui_e_chiudi())
    _PROTETTI.add(compito)
    compito.add_done_callback(_fine_protetto)
    return await asyncio.shield(compito)


def _fine_protetto(compito: asyncio.Task[Any]) -> None:
    _PROTETTI.discard(compito)
    if not compito.cancelled() and compito.exception() is not None:
        log.error("azione protetta finita con un errore", exc_info=compito.exception())


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
