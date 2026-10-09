"""Librerie usate dal flusso principale: documenti (elenco, estrazione, anteprima,
file, caricamento), voci (elenco, consenso), musiche (elenco).

Caricamenti e download seguono A2-piano.md K1-K12 (`app/file/`).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from app.agente.tabella import controlla_nome, ErroreArgomento
from app.file import caricamenti, download
from app.file.nomi import ESTENSIONI_DOCUMENTI, apri_cartella, cartella_libreria, chiave_nome, nome_caricato
from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import contesto, esegui_azione, esegui_e_valuta
from app.servizi.errori import ErroreController

router = APIRouter(prefix="/api")

HEADER_NOME_FILE = "X-LIL-Nome-File"
HEADER_GETTONE = "X-LIL-Gettone"


def _senza_none(**valori: Any) -> dict[str, Any]:
    return {k: v for k, v in valori.items() if v is not None}


def _nome_da_percorso(nome: str) -> str:
    try:
        return controlla_nome("nome", nome)
    except ErroreArgomento as exc:
        raise ErroreController("RichiestaNonValida", exc.messaggio, {"campo": exc.campo}) from None


# --- Documenti -------------------------------------------------------------

@router.get("/documenti")
async def documenti(request: Request, filtro: str | None = None, cerca: str | None = None,
                    utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "documenti", _senza_none(filtro=filtro, cerca=cerca))


@router.get("/documenti/{nome}")
async def documento(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "documento", {"nome": nome})


@router.get("/documenti/{nome}/anteprima")
async def anteprima(nome: str, request: Request, caratteri: int | None = None,
                    utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "anteprima_documento", _senza_none(nome=nome, caratteri=caratteri))


def _cartella_documenti(request: Request):
    ctx = contesto(request)
    nome = (ctx.impostazioni.get("cartelle") or {}).get("documenti") or "documenti"
    return cartella_libreria(ctx.config.agent_dir, nome)


@router.get("/documenti/{nome}/file")
async def file_documento(nome: str, request: Request, scarica: int = 0,
                         utente: Utente = Depends(utente_corrente)) -> Response:
    """Solo un nome dell'elenco di documents_list (K9), dentro documenti/ (K10)."""
    nome = _nome_da_percorso(nome)
    ctx = contesto(request)
    stato, corpo, _ = await esegui_e_valuta(ctx, utente, "documenti", {})
    if stato != 200:
        return JSONResponse(corpo, status_code=stato)
    elencati = {d.get("nome") for d in corpo["dati"].get("documenti", []) if isinstance(d, dict)}
    if nome not in elencati:
        raise ErroreController("NonTrovato")
    dir_fd = apri_cartella(_cartella_documenti(request))
    try:
        return download.servi(dir_fd, nome, range_header=request.headers.get("range"), scarica=bool(scarica))
    finally:
        os.close(dir_fd)


@router.post("/documenti")
async def carica_documento(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Corpo = byte del file (application/octet-stream), nome in X-LIL-Nome-File.
    Un nome gia' presente -> 409 FileEsistente con gettone; si rimanda il file
    con X-LIL-Gettone per sostituirlo (K1-K7)."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, "documento_carica")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        raise ErroreController("FileNonAmmesso", "Il file va inviato come application/octet-stream.")
    nome = nome_caricato(request.headers.get(HEADER_NOME_FILE), ESTENSIONI_DOCUMENTI)
    chiave = chiave_nome(nome)
    sostituzione = False
    valore_gettone = request.headers.get(HEADER_GETTONE)
    if valore_gettone:
        g = ctx.gettoni.consuma(valore_gettone, utente.id)
        if g.azione != "sostituisci_documento" or g.details.get("chiave") != chiave:
            raise ErroreController("GettoneNonValido")
        sostituzione = True
    if sostituzione and ctx.coda is not None and ctx.coda.lavoro_in_corso() is not None:
        raise ErroreController("LavoroGiaInCorso", "C'e' un lavoro in corso: sostituisci il file quando e' finito.")
    massimo = int((ctx.impostazioni.get("documenti") or {}).get("max_bytes") or 20 * 1024 * 1024)
    dir_fd = apri_cartella(_cartella_documenti(request))
    try:
        try:
            caricato = await caricamenti.salva(
                dir_fd=dir_fd, nome=nome, flusso=request.stream(), massimo=massimo,
                lunghezza_dichiarata=request.headers.get("content-length"),
                firma_ok=caricamenti.firma_documento, sostituzione_ammessa=sostituzione,
            )
        except ErroreController as exc:
            if exc.error_type == "FileEsistente":
                g = ctx.gettoni.crea(utente_id=utente.id, azione="sostituisci_documento", run=None,
                                     error_type="FileEsistente", details={"libreria": "documenti", "chiave": chiave},
                                     parametri={})
                exc.gettone, exc.scade_il = g.valore, g.scade_il
            elif exc.error_type in ("FileTroppoGrande", "FileNonAmmesso"):
                ctx.registro.scrivi(utente=utente, azione="carica_documento", esito=exc.error_type,
                                    dettagli={"nome": nome})
            raise
    finally:
        os.close(dir_fd)
    azione = "sostituisci_documento" if sostituzione else "carica_documento"
    scritto = ctx.registro.scrivi(utente=utente, azione=azione, esito="ok", http=201, dettagli={
        "nome": caricato.nome, "byte": caricato.dimensione, "sha256": caricato.sha256, "sostituiti": caricato.sostituiti})
    corpo: dict[str, Any] = {"ok": True, "dati": {"nome": caricato.nome, "dimensione_bytes": caricato.dimensione,
                                                  "sha256": caricato.sha256, "sostituiti": caricato.sostituiti}}
    if not scritto:
        from app.servizi.azioni import AVVISO_REGISTRO
        corpo["avviso"] = AVVISO_REGISTRO
    return JSONResponse(corpo, status_code=201)


# --- Voci ------------------------------------------------------------------

@router.get("/voci")
async def voci(request: Request, filtro: str | None = None, cerca: str | None = None,
               utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "voci", _senza_none(filtro=filtro, cerca=cerca))


@router.get("/voci/{nome}/consenso")
async def stato_voce(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "stato_voce", {"nome": nome})


class Consenso(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_sha256: str
    revisione: str


@router.post("/voci/{nome}/consenso")
async def registra_consenso(nome: str, corpo: Consenso, request: Request,
                            utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """"Confermo che ... ha dato il consenso": sha256 e revisione sono quelli che la
    pagina ha mostrato (stima o stato della voce)."""
    return await esegui_azione(request, utente, "registra_consenso",
                               {"nome": nome, "audio_sha256": corpo.audio_sha256, "revisione": corpo.revisione})


@router.delete("/voci/{nome}/consenso")
async def revoca_consenso(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "revoca_consenso", {"nome": nome})


# --- Musiche ---------------------------------------------------------------

@router.get("/musiche")
async def musiche(request: Request, filtro: str | None = None, cerca: str | None = None,
                  utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "musiche", _senza_none(filtro=filtro, cerca=cerca))
