"""POST /api/conferme/{gettone}: la risposta dell'utente a un esito 3.

Il controller rilancia gli argomenti SALVATI nel gettone con cio' che la tabella
associa a quell'error_type (un flag, un'altra azione con dati presi da `details`,
o la scelta fra candidati). Il browser manda solo il gettone e, dove serve, la
scelta: non puo' cambiare argomenti, run o azione.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.agente.tabella import TABELLA
from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import contesto, esegui_e_valuta
from app.servizi.errori import ErroreController

router = APIRouter()


class RispostaConferma(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scelta: str | None = None


@router.post("/api/conferme/{gettone}")
async def conferma(gettone: str, request: Request, corpo: RispostaConferma | None = None,
                   utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    ctx = contesto(request)
    g = ctx.gettoni.consuma(gettone, utente.id)
    cmd = TABELLA.get(g.azione)
    if cmd is None:
        # Gettoni dei caricamenti (FileEsistente): si usano solo rimandando il file.
        raise ErroreController("GettoneNonValido")
    regola = cmd.conferme.get(g.error_type)
    if regola is None:
        raise ErroreController(
            "GettoneNonValido",
            "Questa domanda non si conferma: rispondi con una nuova richiesta (ad esempio scegliendo il nome esatto).",
        )
    if regola.flag:
        if cmd.lungo:
            if ctx.coda is None:
                raise ErroreController("NonPronto")
            return await ctx.coda.accoda(utente, g.azione, g.parametri, flag_conferme=(regola.flag,),
                                         risposta_a=g.error_type)
        stato, risposta, _ = await esegui_e_valuta(ctx, utente, g.azione, g.parametri, flag_conferme=(regola.flag,),
                                                  risposta_a=g.error_type)
        return JSONResponse(risposta, status_code=stato)
    if regola.azione:
        parametri: dict[str, Any] = {}
        for param, chiave in regola.da_details:
            valore = g.details.get(chiave)
            if not isinstance(valore, str) or not valore:
                raise ErroreController("GettoneNonValido")
            parametri[param] = valore
        stato, risposta, _ = await esegui_e_valuta(ctx, utente, regola.azione, parametri, da_gettone=True,
                                                  risposta_a=g.error_type)
        return JSONResponse(risposta, status_code=stato)
    assert regola.scelta is not None
    param, chiave, campo_id = regola.scelta
    candidati = [c.get(campo_id) for c in g.details.get(chiave, []) if isinstance(c, dict)]
    scelta = corpo.scelta if corpo else None
    if scelta is None or scelta not in candidati:
        # Il gettone e' gia' consumato: una scelta sbagliata si ripete dall'inizio.
        raise ErroreController("RichiestaNonValida", "Scegli una delle opzioni proposte.", {"campo": "scelta"})
    stato, risposta, _ = await esegui_e_valuta(ctx, utente, g.azione, g.parametri, parametri_nascosti={param: scelta},
                                              risposta_a=g.error_type)
    return JSONResponse(risposta, status_code=stato)
