"""GET /api/io e GET /salute."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.file.caricamenti import estensioni_audio
from app.file.nomi import ESTENSIONI_DOCUMENTI
from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import contesto

router = APIRouter()


@router.get("/api/io")
async def io(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    ctx = contesto(request)
    return JSONResponse({"ok": True, "dati": {
        "utente": utente.id,
        "nome": utente.nome,
        "ruoli": sorted(utente.ruoli),
        "permessi": sorted(ctx.permessi.di(utente)),
        # Formati che il controller accetta nei caricamenti (per l'attributo accept).
        "formati": {"documenti": list(ESTENSIONI_DOCUMENTI), "audio": list(estensioni_audio(ctx.impostazioni))},
    }})


@router.get("/salute")
async def salute(request: Request) -> JSONResponse:
    """Per il CMS, senza firma e senza dettagli: 200 se l'avvio e' passato e
    la versione dell'agente e' ancora quella attesa, altrimenti 503."""
    stato = request.app.state.salute
    ok = stato.verifica()
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)
