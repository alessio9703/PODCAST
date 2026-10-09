"""Archivio e passi 1-5, 7 del flusso principale (rotte -> tabella).

Le rotte traducono la richiesta HTTP nei parametri di un'azione della tabella e
chiamano `esegui_azione`: permesso, argomenti, esito, registro stanno li'.
I lavori lunghi (stesura, revisione, produzione) sono in `app/api/lavori.py`.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from app.ingresso import Utente, utente_corrente
from app.file import download
from app.file.nomi import apri_cartella
from app.servizi.azioni import contesto, esegui_azione, esegui_e_valuta
from app.servizi.stima import impronta_stima
from app.servizi.errori import ErroreController

router = APIRouter(prefix="/api")


class Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _senza_none(**valori: Any) -> dict[str, Any]:
    return {k: v for k, v in valori.items() if v is not None}


# --- Archivio --------------------------------------------------------------

@router.get("/episodi")
async def archivio(request: Request, gruppo: str | None = None, cerca: str | None = None,
                   utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "archivio", _senza_none(gruppo=gruppo, cerca=cerca))


class NuovoEpisodio(Corpo):
    titolo: str
    documento: str | None = None


@router.post("/episodi")
async def crea_episodio(corpo: NuovoEpisodio, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "crea_episodio", _senza_none(titolo=corpo.titolo, documento=corpo.documento))


@router.get("/episodi/{run}/stato")
async def stato_run(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "stato_run", {"run": run})


# --- Passi 1-2 · Documento, cast e formato ---------------------------------

@router.get("/episodi/{run}/configurazione")
async def leggi_configurazione(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "leggi_configurazione", {"run": run})


class Configurazione(Corpo):
    titolo: str | None = None
    formato: str | None = None
    durata: str | None = None
    minuti: int | None = None
    voci: dict[str, str] | None = None
    nomi: dict[str, str] | None = None
    descrizioni: dict[str, str] | None = None
    togli: list[str] | None = None


@router.patch("/episodi/{run}/configurazione")
async def modifica_configurazione(run: str, corpo: Configurazione, request: Request,
                                  utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    parametri = _senza_none(run=run, **corpo.model_dump())
    if len(parametri) == 1:
        raise ErroreController("RichiestaNonValida", "Nessuna modifica indicata.")
    return await esegui_azione(request, utente, "modifica_configurazione", parametri)


class CambioDocumento(Corpo):
    documento: str


@router.put("/episodi/{run}/documento")
async def cambia_documento(run: str, corpo: CambioDocumento, request: Request,
                           utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "cambia_documento", {"run": run, "documento": corpo.documento})


# --- Passo 3 · Musiche -----------------------------------------------------

@router.put("/episodi/{run}/musiche")
async def scegli_musiche(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Corpo: {"intro": "Nome" | null, "outro": ..., "sottofondo": ...}. Una chiave
    assente resta com'e'; `null` vuol dire "nessuna musica" (`--none`)."""
    try:
        dati = await request.json()
    except ValueError:
        raise ErroreController("RichiestaNonValida") from None
    if not isinstance(dati, dict) or not dati or set(dati) - {"intro", "outro", "sottofondo"}:
        raise ErroreController("RichiestaNonValida", "Indica intro, outro o sottofondo.")
    parametri: dict[str, Any] = {"run": run}
    nessuna = []
    for categoria, valore in dati.items():
        if valore is None:
            nessuna.append(categoria)
        else:
            parametri[categoria] = valore
    if nessuna:
        parametri["nessuna"] = nessuna
    return await esegui_azione(request, utente, "scegli_musiche", parametri)


# --- Passo 4 · Copione (azioni brevi) --------------------------------------

@router.get("/episodi/{run}/copione")
async def copione(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "copione", {"run": run})


class Battuta(Corpo):
    testo: str
    versione_attesa: int


@router.patch("/episodi/{run}/copione/battute/{n}")
async def modifica_battuta(run: str, n: int, corpo: Battuta, request: Request,
                           utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "modifica_battuta",
                               {"run": run, "battuta": n, "testo": corpo.testo, "versione_attesa": corpo.versione_attesa})


class Approvazione(Corpo):
    versione: int


@router.post("/episodi/{run}/copione/approvazione")
async def approva(run: str, corpo: Approvazione, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "approva_copione", {"run": run, "versione": corpo.versione})


@router.delete("/episodi/{run}/copione/approvazione")
async def ritira_approvazione(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "ritira_approvazione", {"run": run})


@router.get("/episodi/{run}/copione/versioni")
async def versioni(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "versioni", {"run": run})


class Ripristino(Corpo):
    versione: int
    versione_attesa: int


@router.post("/episodi/{run}/copione/ripristino")
async def ripristina(run: str, corpo: Ripristino, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "ripristina_versione",
                               {"run": run, "versione": corpo.versione, "versione_attesa": corpo.versione_attesa})


# --- Passo 5 · Stima -------------------------------------------------------

@router.get("/episodi/{run}/stima")
async def stima(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """La stima con la sua `impronta`, da rimandare per produrre (A4 MEDIA-1)."""
    stato, corpo, risultato = await esegui_e_valuta(contesto(request), utente, "stima", {"run": run})
    if corpo.get("ok") and risultato.json:
        corpo["dati"]["impronta"] = impronta_stima(risultato.json)
    return JSONResponse(corpo, status_code=stato)


# --- Passo 7 · Episodio ----------------------------------------------------

@router.get("/episodi/{run}/episodio")
async def episodio(run: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """I file si scaricano da /api/episodi/{run}/file/{nome} (`file_finale.nome`,
    `file[].nome` con `interno: false`): nessun percorso del server nella risposta."""
    return await esegui_azione(request, utente, "episodio", {"run": run})



# File con percorsi o dati del server: mai serviti, anche se un elenco li segnasse
# come non interni (contratto, "Download").
FILE_MAI_SERVITI = frozenset({"episodio.json", "musiche_usate.json", "voci.json", "voci_usate.json",
                              "_lavoro.json", "_audio_paths.json"})


@router.get("/episodi/{run}/file/{nome}")
async def file_episodio(run: str, nome: str, request: Request, scarica: int = 0,
                        utente: Utente = Depends(utente_corrente)) -> Response:
    """Solo `file_finale.nome` e i `file[]` con `interno: false` di episode_info (K9),
    aperti dentro output/<run> senza seguire link (K10), con Range (K11)."""
    ctx = contesto(request)
    stato, corpo, _ = await esegui_e_valuta(ctx, utente, "episodio", {"run": run})
    if stato != 200:
        return JSONResponse(corpo, status_code=stato)
    dati = corpo["dati"]
    ammessi = set()
    finale = dati.get("file_finale")
    if isinstance(finale, dict) and isinstance(finale.get("nome"), str):
        ammessi.add(finale["nome"])
    for f in dati.get("file") or []:
        if isinstance(f, dict) and f.get("interno") is False and isinstance(f.get("nome"), str):
            ammessi.add(f["nome"])
    if nome not in ammessi or nome in FILE_MAI_SERVITI or nome.endswith(".percorso.txt"):
        raise ErroreController("NonTrovato")
    dir_fd = apri_cartella(ctx.config.output_dir / run)
    try:
        return download.servi(dir_fd, nome, range_header=request.headers.get("range"), scarica=bool(scarica))
    finally:
        os.close(dir_fd)
