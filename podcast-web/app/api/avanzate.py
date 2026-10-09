"""Avanzate (A5): lezioni, impostazioni, diagnostica.

- Una lezione accettata entra nel prompt di TUTTI gli episodi successivi: permesso
  `lezioni` (di base solo l'amministratore), `--by` dall'ingresso, riga nel registro.
- Proporre lezioni COSTA (modello): `GET .../proposta/stima` da' la stima e
  l'`impronta`, `POST .../proposta` la rimanda e il controller rifa' la stima prima di
  accodare. Se `config.yaml` non ha i prezzi, il costo e' dichiarato "non noto".
- `LessonRemovalRequired` (esito 3): la lezione da togliere si sceglie fra i candidati
  con `POST /api/conferme/{gettone}` `{"scelta": "<id>"}`.
- Impostazioni e diagnostica: sola lettura (`settings_show.py`, `doctor.py --web`).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import contesto, esegui_azione, esegui_e_valuta
from app.servizi.errori import ErroreController
from app.servizi.stima import impronta_lezioni

router = APIRouter(prefix="/api")


class Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("/lezioni")
async def lezioni(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "lezioni_elenco", {})


@router.get("/lezioni/evidenze")
async def evidenze(request: Request, ultimi: int | None = None, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "lezioni_evidenze", {"ultimi": ultimi} if ultimi is not None else {})


@router.get("/lezioni/revisione")
async def revisione(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "lezioni_revisione", {})


@router.get("/lezioni/andamento")
async def andamento(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "lezioni_andamento", {})


@router.get("/lezioni/proposta/stima")
async def stima_proposta(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    stato, corpo, risultato = await esegui_e_valuta(contesto(request), utente, "lezioni_proponi_stima", {})
    if corpo.get("ok") and risultato.json:
        corpo["dati"]["impronta"] = impronta_lezioni(risultato.json)
    return JSONResponse(corpo, status_code=stato)


class Proposta(Corpo):
    impronta: str


@router.post("/lezioni/proposta")
async def proponi(corpo: Proposta, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    coda = contesto(request).coda
    if coda is None:
        raise ErroreController("NonPronto")
    return await coda.accoda(utente, "lezioni_proponi", {}, impronta=corpo.impronta)


class Accettazione(Corpo):
    testo: str | None = None


@router.post("/lezioni/{id_lezione}/accettazione")
async def accetta(id_lezione: str, corpo: Accettazione, request: Request,
                  utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    parametri = {"id": id_lezione}
    if corpo.testo is not None:
        parametri["testo"] = corpo.testo
    return await esegui_azione(request, utente, "lezioni_accetta", parametri)


class Rifiuto(Corpo):
    motivo: str


@router.post("/lezioni/{id_lezione}/rifiuto")
async def rifiuta(id_lezione: str, corpo: Rifiuto, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "lezioni_rifiuta", {"id": id_lezione, "motivo": corpo.motivo})


@router.get("/impostazioni")
async def impostazioni(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "impostazioni", {})


# Un percorso assoluto dentro una frase ("/opt/x/ffmpeg non trovato"): il pulitore toglie
# solo le stringhe che SONO un percorso, qui si maschera anche dentro il testo.
_PERCORSO_NEL_TESTO = re.compile(r"(?<![\w.])(?:~|/)(?:[^\s/]+/)*[^\s/]+")


def _senza_percorsi(controlli: Any) -> list[dict[str, Any]]:
    puliti = []
    for c in controlli if isinstance(controlli, list) else []:
        if isinstance(c, dict):
            c = dict(c)
            if isinstance(c.get("detail"), str):
                c["detail"] = _PERCORSO_NEL_TESTO.sub("…", c["detail"])
            puliti.append(c)
    return puliti


@router.get("/diagnostica")
async def diagnostica(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """`doctor.py --web` esce con 1 se un controllo obbligatorio non passa, ma sempre con
    il JSON dei controlli: qui l'elenco si mostra in entrambi i casi."""
    ctx = contesto(request)
    stato, corpo, risultato = await esegui_e_valuta(ctx, utente, "diagnostica", {})
    dati = risultato.json if isinstance(risultato.json, dict) else None
    if not corpo.get("ok") and dati and isinstance(dati.get("checks"), list) and risultato.exit_code == 1:
        corpo = {"ok": True, "dati": ctx.pulitore.pulisci({"ok": False, "checks": dati["checks"]})}
        stato = 200
    if corpo.get("ok"):
        corpo["dati"]["checks"] = _senza_percorsi(corpo["dati"].get("checks"))
    return JSONResponse(corpo, status_code=stato)
