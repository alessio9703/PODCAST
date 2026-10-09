"""Passo 7 · correzioni (A5): forma d'onda, battuta a un tempo, retake ("Come e' detta"),
anteprime in contesto, scelta del take, rimontaggio.

- Il retake COSTA: `GET .../retake/stima` da' costo e `impronta`; `POST .../retake` la
  rimanda e il controller rifa' la stima prima di accodare (come la produzione).
- Le anteprime dei take sono percorsi dell'agente (`output/<run>/segmenti/anteprime/…`):
  al browser arriva solo un indirizzo del controller; il file si serve solo se l'agente lo
  elenca in quel momento, risolto dentro la cartella della run senza seguire link.
- Il testo libero ("Altre indicazioni, con parole tue") non e' supportato (decisione D1):
  le indicazioni sono un elenco chiuso (`INDICAZIONI_RETAKE`).
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from app.file import download
from app.file.nomi import apri_percorso_relativo
from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import contesto, esegui_azione, esegui_e_valuta
from app.servizi.errori import ErroreController
from app.servizi.stima import impronta_retake

router = APIRouter(prefix="/api")

_TEMPO = re.compile(r"[0-9]{1,2}(:[0-9]{1,2}){0,2}(\.[0-9]{1,3})?")


class Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("/episodi/{run}/forma-onda")
async def forma_onda(run: str, request: Request, punti: str = "400", utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "forma_onda", {"run": run, "punti": punti})


@router.get("/episodi/{run}/battuta")
async def battuta_al_tempo(run: str, request: Request, al: str, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    if not _TEMPO.fullmatch(al):
        raise ErroreController("RichiestaNonValida", "Tempo non valido (es. 4:12).", {"campo": "al"})
    return await esegui_azione(request, utente, "battuta_al_tempo", {"run": run, "al": al})


def _parametri_retake(run: str, battuta: int, take: int, indicazioni: list[str] | None) -> dict[str, Any]:
    parametri: dict[str, Any] = {"run": run, "battuta": battuta, "take": take}
    if indicazioni:
        parametri["indicazioni"] = indicazioni
    return parametri


@router.get("/episodi/{run}/retake/stima")
async def stima_retake(run: str, request: Request, battuta: int, take: int,
                       indicazioni: list[str] | None = Query(None), utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Costo dei nuovi take, con l'`impronta` da rimandare per generarli."""
    stato, corpo, risultato = await esegui_e_valuta(contesto(request), utente, "stima_retake",
                                                    _parametri_retake(run, battuta, take, indicazioni))
    if corpo.get("ok") and risultato.json:
        corpo["dati"]["impronta"] = impronta_retake(risultato.json)
    return JSONResponse(corpo, status_code=stato)


class Retake(Corpo):
    battuta: int
    take: int
    indicazioni: list[str] | None = None
    impronta: str


@router.post("/episodi/{run}/retake")
async def genera_retake(run: str, corpo: Retake, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    coda = contesto(request).coda
    if coda is None:
        raise ErroreController("NonPronto")
    return await coda.accoda(utente, "genera_retake", _parametri_retake(run, corpo.battuta, corpo.take, corpo.indicazioni),
                             impronta=corpo.impronta)


async def _take(request: Request, utente: Utente, run: str, n: int, musica: bool) -> tuple[int, dict[str, Any], Any]:
    parametri: dict[str, Any] = {"run": run, "battuta": n}
    if musica:
        parametri["con_musica"] = True
    return await esegui_e_valuta(contesto(request), utente, "ascolta_take", parametri)


@router.get("/episodi/{run}/retake/{n}")
async def ascolta_take(run: str, n: int, request: Request, musica: int = 0,
                       utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """I take della battuta (originale compreso). Al posto dei percorsi delle anteprime,
    gli indirizzi del controller che le servono."""
    stato, corpo, risultato = await _take(request, utente, run, n, bool(musica))
    if corpo.get("ok"):
        grezzi = {t.get("take"): t for t in (risultato.json or {}).get("take_list") or [] if isinstance(t, dict)}
        for t in corpo["dati"].get("take_list") or []:
            g = grezzi.get(t.get("take")) or {}
            base = f"/api/episodi/{run}/retake/{n}/take/{t.get('take')}/anteprima"
            if isinstance(g.get("preview"), str):
                t["anteprima"] = base
            if isinstance(g.get("preview_with_music"), str):
                t["anteprima_musica"] = base + "?musica=1"
    return JSONResponse(corpo, status_code=stato)


@router.get("/episodi/{run}/retake/{n}/take/{k}/anteprima")
async def anteprima_take(run: str, n: int, k: int, request: Request, musica: int = 0,
                         utente: Utente = Depends(utente_corrente)) -> Response:
    stato, corpo, risultato = await _take(request, utente, run, n, bool(musica))
    if stato != 200:
        return JSONResponse(corpo, status_code=stato)
    chiave = "preview_with_music" if musica else "preview"
    for t in (risultato.json or {}).get("take_list") or []:
        if isinstance(t, dict) and t.get("take") == k and isinstance(t.get(chiave), str):
            ctx = contesto(request)
            dir_fd, nome = apri_percorso_relativo(ctx.config.output_dir / run, t[chiave], f"output/{run}/")
            try:
                return download.servi(dir_fd, nome, range_header=request.headers.get("range"), scarica=False)
            finally:
                os.close(dir_fd)
    raise ErroreController("NonTrovato")


class SceltaTake(Corpo):
    battuta: int
    take: int


@router.post("/episodi/{run}/retake/scelta")
async def scegli_take(run: str, corpo: SceltaTake, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """"Usa il take": `--choose N=K` (poi si rimonta con /rimontaggio)."""
    if not (1 <= corpo.battuta <= 100000 and 1 <= corpo.take <= 100):
        raise ErroreController("RichiestaNonValida", "Battuta o take fuori intervallo.", {"campo": "take"})
    return await esegui_azione(request, utente, "scegli_take", {"run": run, "scelta": f"{corpo.battuta}={corpo.take}"})


@router.post("/episodi/{run}/rimontaggio")
async def rimonta(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Rimonta dalla cache (take scelti, musiche cambiate): nessuna sintesi, non costa.
    Un brano sostituito dopo il montaggio: esito 3 `MusicChanged` con gettone."""
    return await esegui_azione(request, utente, "rimonta", {"run": run})
