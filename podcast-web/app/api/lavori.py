"""Lavori lunghi: stesura e revisione del copione, produzione; coda, interruzione,
avanzamento (SSE con polling come ripiego). Punti K13-K22 di A2-piano.md.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.agente.tabella import ErroreArgomento, RunNonTrovata, controlla_run
from app.ingresso import Utente, utente_corrente
from app.lavori.eventi import Lettore
from app.servizi.azioni import contesto, errore_da_argomenti
from app.servizi.errori import ErroreController

router = APIRouter(prefix="/api")

SSE_PER_UTENTE = 4
SSE_DURATA_MAX_S = 2 * 3600
SSE_HEARTBEAT_S = 15
SSE_INTERVALLO_S = 0.5
_ID_EVENTO = re.compile(r"[0-9]{1,6}")


def _run_valida(request: Request, run: str) -> str:
    try:
        return controlla_run(run, contesto(request).config.output_dir)
    except (ErroreArgomento, RunNonTrovata) as exc:
        raise errore_da_argomenti(exc) from None


def _coda(request: Request):
    coda = contesto(request).coda
    if coda is None:
        raise ErroreController("NonPronto")
    return coda


# --- Avvio dei lavori ------------------------------------------------------

@router.post("/episodi/{run}/copione/stesura")
async def stesura(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await _coda(request).accoda(utente, "scrivi_copione", {"run": run})


class Revisione(BaseModel):
    model_config = ConfigDict(extra="forbid")
    istruzioni: str


@router.post("/episodi/{run}/copione/revisione")
async def revisione(run: str, corpo: Revisione, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await _coda(request).accoda(utente, "revisiona_copione", {"run": run, "istruzioni": corpo.istruzioni})


@router.post("/episodi/{run}/produzione")
async def produzione(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Stima (costo nel registro) e produzione in coda. Esiti 3 (consenso, clonazione
    incerta, brano sostituito) arrivano con il gettone nell'esito del lavoro."""
    return await _coda(request).accoda(utente, "produci", {"run": run})


@router.post("/episodi/{run}/interruzione")
async def interruzione(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    run = _run_valida(request, run)
    return JSONResponse({"ok": True, "dati": await _coda(request).interrompi(run, utente)})


@router.get("/lavori")
async def lavori(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    contesto(request).permessi.richiedi(utente, "leggere")
    return JSONResponse({"ok": True, "dati": _coda(request).elenco(utente)})


@router.get("/lavori/{id_lavoro}")
async def lavoro(id_lavoro: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    contesto(request).permessi.richiedi(utente, "leggere")
    trovato = _coda(request).trova(id_lavoro)
    if trovato is None:
        raise ErroreController("NonTrovato", "Lavoro non trovato (forse il servizio e' stato riavviato).")
    return JSONResponse({"ok": True, "dati": trovato.vista(utente)})


# --- Avanzamento: polling --------------------------------------------------

def _intero_evento(valore: str | None, campo: str) -> int:
    if valore is None or valore == "":
        return 0
    if not _ID_EVENTO.fullmatch(valore):
        raise ErroreController("RichiestaNonValida", "Numero di evento non valido.", {"campo": campo})
    return int(valore)


@router.get("/episodi/{run}/eventi/elenco")
async def eventi_polling(run: str, request: Request, dopo: str | None = None,
                         utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Ripiego dello SSE: gli eventi con id > `dopo` e lo stato del lavoro."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, "leggere")
    run = _run_valida(request, run)
    da = _intero_evento(dopo, "dopo")
    lettore = Lettore(ctx.config.output_dir / run, ctx.pulitore)
    eventi = lettore.salta_fino_a(da)
    return JSONResponse({"ok": True, "dati": {
        "eventi": [{"id": n, **e} for n, e in eventi],
        "ultimo_id": lettore.riga,
        "ripartito": da > lettore.riga,
        "lavoro": _coda(request).vista_run(run, utente),
        "finito": lettore.finito,
    }})


# --- Avanzamento: SSE ------------------------------------------------------

def _sse(evento: str, dati: Any, id_evento: int | None = None) -> bytes:
    righe = []
    if id_evento is not None:
        righe.append(f"id: {id_evento}")
    righe.append(f"event: {evento}")
    righe.append("data: " + json.dumps(dati, ensure_ascii=False))
    return ("\n".join(righe) + "\n\n").encode("utf-8")


@router.get("/episodi/{run}/eventi")
async def eventi_sse(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> StreamingResponse:
    """Server-Sent Events: `agente` (una riga di _eventi.jsonl, id = numero di riga),
    `lavoro` (stato del lavoro della run), `azzerato` (nuova produzione: si riparte),
    `fine` (niente piu' da seguire: lo stream si chiude). Heartbeat ogni 15 s."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, "leggere")
    run = _run_valida(request, run)
    da = _intero_evento(request.headers.get("last-event-id"), "Last-Event-ID")
    coda = _coda(request)
    aperte = ctx.sse_aperte
    if aperte.get(utente.id, 0) >= SSE_PER_UTENTE:
        raise ErroreController("TroppeConnessioni")

    async def flusso() -> AsyncIterator[bytes]:
        # Il contatore sale dentro il generatore: uno stream mai avviato non lo tocca.
        aperte[utente.id] = aperte.get(utente.id, 0) + 1
        try:
            lettore = Lettore(ctx.config.output_dir / run, ctx.pulitore)
            for n, e in lettore.salta_fino_a(da):
                yield _sse("agente", e, n)
            ultimo_lavoro = None
            inizio = ultimo_battito = time.monotonic()
            while True:
                vista = coda.vista_run(run, utente)
                if vista != ultimo_lavoro:
                    ultimo_lavoro = vista
                    yield _sse("lavoro", vista)
                for n, e in lettore.leggi_nuovi():
                    if lettore.azzerato:
                        lettore.azzerato = False
                        yield _sse("azzerato", {})
                    yield _sse("agente", e, n)
                attivo = coda.lavoro_della_run(run)
                if attivo is None and (lettore.finito or vista is None or vista.get("stato") in ("finito", "annullato")):
                    # Rilegge un'ultima volta: gli eventi finali si scrivono prima della fine del processo.
                    for n, e in lettore.leggi_nuovi():
                        yield _sse("agente", e, n)
                    yield _sse("fine", {"lavoro": coda.vista_run(run, utente)})
                    return
                if await request.is_disconnected():
                    return
                ora = time.monotonic()
                if ora - inizio > SSE_DURATA_MAX_S:
                    yield _sse("fine", {"motivo": "durata massima"})
                    return
                if ora - ultimo_battito >= SSE_HEARTBEAT_S:
                    ultimo_battito = ora
                    yield b": battito\n\n"
                await coda.aspetta_cambio(SSE_INTERVALLO_S)
        finally:
            aperte[utente.id] -= 1
            if aperte[utente.id] <= 0:
                aperte.pop(utente.id, None)

    return StreamingResponse(flusso(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

