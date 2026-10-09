"""Librerie: documenti (elenco, estrazione, anteprima, file, caricamento), voci
(elenco, caricamento, campione, nome pronunciato, consenso e revoca), musiche
(elenco, caricamento, anteprima, licenza, parlato e trascrizione).

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
from app.file.nomi import ESTENSIONI_DOCUMENTI, ESTENSIONI_VIETATE, apri_cartella, cartella_libreria, chiave_nome, nome_caricato
from app.ingresso import Utente, utente_corrente
from app.servizi.azioni import AVVISO_REGISTRO, contesto, esegui_azione, esegui_e_valuta
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
    """Il testo dell'anteprima arriva come `preview`, che il pulitore toglie sempre (nei
    retake e' un percorso): qui e' testo estratto, e torna come `anteprima`."""
    ctx = contesto(request)
    stato, corpo, risultato = await esegui_e_valuta(ctx, utente, "anteprima_documento", _senza_none(nome=nome, caratteri=caratteri))
    testo = (risultato.json or {}).get("preview") if corpo.get("ok") else None
    if isinstance(testo, str) and not ctx.pulitore.stringa_sospetta(testo):
        corpo["dati"]["anteprima"] = testo
    return JSONResponse(corpo, status_code=stato)


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
    massimo = int((ctx.impostazioni.get("documenti") or {}).get("max_bytes") or 20 * 1024 * 1024)
    return await _carica(request, utente, libreria="documenti", cartella=_cartella_documenti(request),
                         estensioni=ESTENSIONI_DOCUMENTI, firma_ok=caricamenti.firma_documento, massimo=massimo,
                         permesso="documento_carica", azione="carica_documento", azione_sostituisci="sostituisci_documento")


async def _carica(request: Request, utente: Utente, *, libreria: str, cartella, estensioni, firma_ok, massimo: int,
                  permesso: str, azione: str, azione_sostituisci: str) -> JSONResponse:
    """Un caricamento in una libreria (documenti, voci, musiche/<categoria>), con le
    regole di `app/file/caricamenti.py`, il gettone `FileEsistente` per sostituire e
    la riga nel registro attivita'."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, permesso)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        raise ErroreController("FileNonAmmesso", "Il file va inviato come application/octet-stream.")
    if not estensioni:
        raise ErroreController("FileNonAmmesso", "Nessun formato ammesso per questa libreria.")
    nome = nome_caricato(request.headers.get(HEADER_NOME_FILE), estensioni)
    chiave = chiave_nome(nome)
    sostituzione = False
    valore_gettone = request.headers.get(HEADER_GETTONE)

    def gettone_valido(g) -> None:
        if g.azione != azione_sostituisci or g.details.get("libreria") != libreria or g.details.get("chiave") != chiave:
            raise ErroreController("GettoneNonValido")

    def nessun_lavoro() -> None:
        if ctx.coda is not None and ctx.coda.lavoro_in_corso() is not None:
            raise ErroreController("LavoroGiaInCorso", "C'e' un lavoro in corso: sostituisci il file quando e' finito.")

    if valore_gettone:
        # Si controlla senza consumarlo: il consumo avviene solo subito prima del rename.
        gettone_valido(ctx.gettoni.leggi(valore_gettone, utente.id))
        nessun_lavoro()
        sostituzione = True

    def prima_del_rename(esistenti: list[str]) -> None:
        if esistenti:
            # Controlli nel momento della sostituzione (A2 BASSA-2), poi il gettone si consuma.
            nessun_lavoro()
            if not valore_gettone:
                raise ErroreController("FileEsistente")
            gettone_valido(ctx.gettoni.consuma(valore_gettone, utente.id))
    dir_fd = apri_cartella(cartella)
    try:
        try:
            caricato = await caricamenti.salva(
                dir_fd=dir_fd, nome=nome, flusso=request.stream(), massimo=massimo,
                lunghezza_dichiarata=request.headers.get("content-length"),
                firma_ok=firma_ok, estensioni=estensioni,
                sostituzione_ammessa=sostituzione, prima_del_rename=prima_del_rename,
            )
        except ErroreController as exc:
            if exc.error_type == "FileEsistente":
                g = ctx.gettoni.crea(utente_id=utente.id, azione=azione_sostituisci, run=None,
                                     error_type="FileEsistente", details={"libreria": libreria, "chiave": chiave},
                                     parametri={})
                exc.gettone, exc.scade_il = g.valore, g.scade_il
            elif exc.error_type in ("FileTroppoGrande", "FileNonAmmesso"):
                ctx.registro.scrivi(utente=utente, azione=azione, esito=exc.error_type,
                                    dettagli={"nome": nome, "libreria": libreria})
            raise
    finally:
        os.close(dir_fd)
    scritto = ctx.registro.scrivi(utente=utente, azione=azione_sostituisci if sostituzione else azione, esito="ok",
                                  http=201, dettagli={"nome": caricato.nome, "libreria": libreria, "byte": caricato.dimensione,
                                                      "sha256": caricato.sha256, "sostituiti": caricato.sostituiti})
    corpo: dict[str, Any] = {"ok": True, "dati": {"nome": caricato.nome, "dimensione_bytes": caricato.dimensione,
                                                  "sha256": caricato.sha256, "sostituiti": caricato.sostituiti}}
    if not scritto:
        corpo["avviso"] = AVVISO_REGISTRO
    return JSONResponse(corpo, status_code=201)


def _cartella(request: Request, chiave: str, *sotto: str):
    ctx = contesto(request)
    nome = (ctx.impostazioni.get("cartelle") or {}).get(chiave) or {"voci": "voci_disponibili"}.get(chiave, chiave)
    cartella = cartella_libreria(ctx.config.agent_dir, nome)
    return cartella.joinpath(*sotto)


def _servi_da_libreria(request: Request, cartella, nome_file: Any) -> Response:
    """Un file di una libreria, per nome di file preso da un elenco dell'agente (contratto,
    "Download"): niente separatori, punto iniziale, link; mai i `.json` dei metadati."""
    if not isinstance(nome_file, str) or os.path.splitext(nome_file)[1].lower() in ESTENSIONI_VIETATE:
        raise ErroreController("NonTrovato")
    try:
        nome_file = controlla_nome("nome", nome_file)
    except ErroreArgomento:
        raise ErroreController("NonTrovato") from None
    dir_fd = apri_cartella(cartella)
    try:
        return download.servi(dir_fd, nome_file, range_header=request.headers.get("range"), scarica=False)
    finally:
        os.close(dir_fd)


# --- Voci ------------------------------------------------------------------

@router.get("/voci")
async def voci(request: Request, filtro: str | None = None, cerca: str | None = None,
               utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "voci", _senza_none(filtro=filtro, cerca=cerca))


# Limiti dei caricamenti audio (contratto: registrazione di 10-30 s letta in memoria alla
# clonazione; brani "ragionevoli").
MAX_VOCE = 20 * 1024 * 1024
MAX_BRANO = 50 * 1024 * 1024


@router.post("/voci")
async def carica_voce(request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Registrazione di una voce. Dopo il caricamento la pagina chiede nome pronunciato e
    consenso (con lo sha256 che l'agente mostra). Una sostituzione rende il consenso
    `superato` e la voce va clonata di nuovo."""
    ctx = contesto(request)
    return await _carica(request, utente, libreria="voci", cartella=_cartella(request, "voci"),
                         estensioni=caricamenti.estensioni_audio(ctx.impostazioni), firma_ok=caricamenti.firma_audio,
                         massimo=MAX_VOCE, permesso="voce_carica", azione="carica_voce", azione_sostituisci="sostituisci_voce")


async def _voce(request: Request, utente: Utente, nome: str) -> dict[str, Any]:
    nome = _nome_da_percorso(nome)
    stato, corpo, _ = await esegui_e_valuta(contesto(request), utente, "voci", {})
    if stato != 200:
        raise ErroreController("NonTrovato")
    for v in corpo["dati"].get("voices") or []:
        if isinstance(v, dict) and v.get("name") == nome:
            return v
    raise ErroreController("NonTrovato")


@router.get("/voci/{nome}/campione")
async def campione_voce(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> Response:
    """"Ascolta il campione": il `file` di voices_list per quella voce, solo da utenti
    autenticati (e' un dato personale)."""
    voce = await _voce(request, utente, nome)
    return _servi_da_libreria(request, _cartella(request, "voci"), voce.get("file"))


class NomePronunciato(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome_pronunciato: str


@router.patch("/voci/{nome}")
async def nome_voce(nome: str, corpo: NomePronunciato, request: Request,
                    utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Il nome che il presentatore pronuncia. Senza cognome: esito 3 con gettone."""
    return await esegui_azione(request, utente, "nome_voce", {"nome": nome, "nome_pronunciato": corpo.nome_pronunciato})


@router.get("/voci/{nome}/consenso")
async def stato_voce(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "stato_voce", {"nome": nome})


class Consenso(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_sha256: str
    revisione: str
    # Conferma esplicita e separata quando la registrazione era stata revocata (A5 MEDIA-1,
    # C7-sicurezza-3 BASSA-1): un nuovo consenso toglie la revoca di quella registrazione
    # e dei suoi voice_id, quindi non deve mai partire "di passaggio".
    dopo_revoca: bool = False


def _revocata(consenso: Any) -> bool:
    return isinstance(consenso, dict) and (consenso.get("stato") == "revocato" or consenso.get("revoca_nel_registro") is True)


@router.post("/voci/{nome}/consenso")
async def registra_consenso(nome: str, corpo: Consenso, request: Request,
                            utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """"Confermo che ... ha dato il consenso": sha256 e revisione sono quelli che la
    pagina ha mostrato (stima o stato della voce). Se la registrazione risulta revocata
    serve `dopo_revoca: true`, che la pagina manda solo dopo aver mostrato la revoca e
    avuto una conferma a parte; altrimenti 409 `ConsensoDopoRevoca`, niente registrato."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, "voce_consenso")
    stato, corpo_stato, _ = await esegui_e_valuta(ctx, utente, "stato_voce", {"nome": nome})
    if stato != 200:
        return JSONResponse(corpo_stato, status_code=stato)
    consenso = corpo_stato["dati"].get("consenso")
    revocata = _revocata(consenso)
    if revocata and not corpo.dopo_revoca:
        ctx.registro.scrivi(utente=utente, azione="registra_consenso", esito="ConsensoDopoRevoca", http=409,
                            dettagli={"nome": nome, "motivo": "registrazione revocata: serve una conferma esplicita"})
        raise ErroreController("ConsensoDopoRevoca", details={
            "campo": "dopo_revoca", "revocato_da": consenso.get("revocato_da"), "revocato_il": consenso.get("revocato_il")})
    stato, risposta, _ = await esegui_e_valuta(ctx, utente, "registra_consenso",
                                               {"nome": nome, "audio_sha256": corpo.audio_sha256, "revisione": corpo.revisione},
                                               risposta_a="ConsensoDopoRevoca" if revocata else None)
    return JSONResponse(risposta, status_code=stato)


@router.delete("/voci/{nome}/consenso")
async def revoca_consenso(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "revoca_consenso", {"nome": nome})


@router.post("/voci/{nome}/clonazione-verificata")
async def clonazione_verificata(nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Risposta a un `CloneOutcomeUnknown` da parte di chi gestisce le voci, anche se
    la produzione l'ha lanciata un altro (A2 NOTA-4).

    La domanda dell'esito 3 la conserva l'agente (`clonazione_incerta` della voce):
    si mette in coda `--confirm-clone-retry` SOLO se l'agente dice che per quella voce
    c'e' davvero una clonazione dall'esito incerto. Permesso `voce_clonazione`;
    registro con `risposta_a: CloneOutcomeUnknown`. Poi si rilancia la produzione."""
    ctx = contesto(request)
    ctx.permessi.richiedi(utente, "voce_clonazione")
    stato, corpo, _ = await esegui_e_valuta(ctx, utente, "stato_voce", {"nome": nome})
    if stato != 200:
        return JSONResponse(corpo, status_code=stato)
    dati = corpo["dati"]
    if dati.get("clonazione_incerta") is not True:
        raise ErroreController("RichiestaNonValida", "Per questa voce non c'e' una clonazione da verificare.",
                               {"campo": "nome"})
    nome_esatto = dati.get("voce") if isinstance(dati.get("voce"), str) else nome
    if ctx.coda is None:
        raise ErroreController("NonPronto")
    return await ctx.coda.accoda(utente, "ritenta_clonazione", {"nome": nome_esatto}, da_gettone=True,
                                 risposta_a="CloneOutcomeUnknown")


# --- Musiche ---------------------------------------------------------------

@router.get("/musiche")
async def musiche(request: Request, filtro: str | None = None, cerca: str | None = None,
                  utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    return await esegui_azione(request, utente, "musiche", _senza_none(filtro=filtro, cerca=cerca))


CATEGORIE = ("intro", "outro", "sottofondo")


def _categoria(categoria: str) -> str:
    if categoria not in CATEGORIE:
        raise ErroreController("NonTrovato")
    return categoria


@router.post("/musiche/{categoria}")
async def carica_brano(categoria: str, request: Request, utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    categoria = _categoria(categoria)
    ctx = contesto(request)
    return await _carica(request, utente, libreria=f"musiche/{categoria}", cartella=_cartella(request, "musiche", categoria),
                         estensioni=caricamenti.estensioni_audio(ctx.impostazioni), firma_ok=caricamenti.firma_audio,
                         massimo=MAX_BRANO, permesso="brano_carica", azione="carica_brano", azione_sostituisci="sostituisci_brano")


@router.get("/musiche/{categoria}/{nome}/file")
async def file_brano(categoria: str, nome: str, request: Request, utente: Utente = Depends(utente_corrente)) -> Response:
    """"Ascolta l'anteprima": `name` + `extension` di un brano di music_list nella sua categoria."""
    categoria = _categoria(categoria)
    nome = _nome_da_percorso(nome)
    stato, corpo, _ = await esegui_e_valuta(contesto(request), utente, "musiche", {})
    if stato != 200:
        raise ErroreController("NonTrovato")
    for b in corpo["dati"].get(categoria) or []:
        if isinstance(b, dict) and b.get("name") == nome and isinstance(b.get("extension"), str):
            return _servi_da_libreria(request, _cartella(request, "musiche", categoria), nome + b["extension"])
    raise ErroreController("NonTrovato")


class MetadatiBrano(BaseModel):
    model_config = ConfigDict(extra="forbid")
    licenza: str | None = None
    senza_licenza: bool | None = None
    parlato: bool | None = None
    trascrizione: str | None = None


@router.patch("/musiche/{categoria}/{nome}")
async def metadati_brano(categoria: str, nome: str, corpo: MetadatiBrano, request: Request,
                         utente: Utente = Depends(utente_corrente)) -> JSONResponse:
    """Licenza dichiarata (o tolta), parlato e trascrizione. La licenza non si verifica:
    si registra chi l'ha dichiarata e quando."""
    categoria = _categoria(categoria)
    if corpo.licenza is not None and corpo.senza_licenza:
        raise ErroreController("RichiestaNonValida", "Licenza e «senza licenza» insieme.", {"campo": "licenza"})
    parametri = _senza_none(categoria=categoria, nome=nome, licenza=corpo.licenza,
                            senza_licenza=True if corpo.senza_licenza else None,
                            parlato=None if corpo.parlato is None else ("true" if corpo.parlato else "false"),
                            trascrizione=corpo.trascrizione)
    if len(parametri) == 2:
        raise ErroreController("RichiestaNonValida", "Nessuna modifica indicata.")
    return await esegui_azione(request, utente, "metadati_brano", parametri)
