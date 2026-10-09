"""Applicazione FastAPI del controller.

`crea_app(config)` costruisce l'app; i controlli di avvio girano nel lifespan:
se uno non passa l'app non parte (uvicorn esce con errore). Ogni richiesta, tranne
`/salute`, passa dal modulo di ingresso; le richieste che cambiano qualcosa devono
avere `X-LIL-Richiesta: 1` e un Content-Type JSON o binario (difesa CSRF).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agente.tabella import verifica_tabella
from app.api import conferme, sessione
from app.config import Config, carica_config
from app.ingresso import crea_ingresso
from app.registro import Registro
from app.servizi.avvio import StatoSalute, verifica_avvio
from app.servizi.azioni import Contesto
from app.servizi.errori import ErroreController
from app.servizi.esiti import Pulitore
from app.servizi.gettoni import Gettoni
from app.servizi.permessi import RUOLI_INIZIALI, Permessi

log = logging.getLogger("podcast_web")

METODI_CHE_CAMBIANO = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HEADER_RICHIESTA = "x-lil-richiesta"
TIPI_AMMESSI = ("application/json", "application/octet-stream")
SENZA_INGRESSO = frozenset({"/salute"})

HEADER_SICUREZZA = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _errore(exc: ErroreController) -> JSONResponse:
    return JSONResponse(exc.corpo(), status_code=exc.status)


def crea_app(config: Config | None = None, *, router_extra: list[Any] | None = None) -> FastAPI:
    config = config or carica_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        verifica_tabella()
        permessi = Permessi.da_file(config.permessi_file)
        mancanti = RUOLI_INIZIALI - permessi.ruoli
        if mancanti:
            log.warning("ruoli iniziali assenti dal file dei permessi: %s", sorted(mancanti))
        registro = Registro(config.registro_file)
        registro.verifica()
        impostazioni = await verifica_avvio(config)
        archivio = (impostazioni.get("cartelle") or {}).get("archivio") or "output"
        app.state.ctx = Contesto(
            config=config,
            ingresso=crea_ingresso(config, permessi.ruoli),
            permessi=permessi,
            registro=registro,
            gettoni=Gettoni(config.gettone_durata_s),
            pulitore=Pulitore(config.agent_dir, os.environ.get("HOME"), archivio),
            impostazioni=impostazioni,
        )
        app.state.ingresso = app.state.ctx.ingresso
        app.state.salute.segna_pronto()
        log.info("controller pronto (AUTH_MODE=%s)", config.auth_mode)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.salute = StatoSalute(config)

    @app.middleware("http")
    async def ingresso_e_sicurezza(request: Request, call_next: Any) -> Any:
        try:
            percorso = request.url.path
            if percorso not in SENZA_INGRESSO:
                ctx = getattr(request.app.state, "ctx", None)
                if ctx is None:
                    raise ErroreController("NonPronto")
                request.state.utente = ctx.ingresso.identifica(request)
                if request.method in METODI_CHE_CAMBIANO and percorso.startswith("/api/"):
                    if request.headers.get(HEADER_RICHIESTA) != "1":
                        raise ErroreController("RichiestaNonValida", "Richiesta senza l'intestazione del controller.")
                    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
                    lunghezza = request.headers.get("content-length")
                    ha_corpo = tipo or (lunghezza not in (None, "0")) or request.headers.get("transfer-encoding")
                    if ha_corpo and tipo not in TIPI_AMMESSI:
                        raise ErroreController("FileNonAmmesso", "Tipo di contenuto non ammesso.")
            risposta = await call_next(request)
        except ErroreController as exc:
            risposta = _errore(exc)
        for nome, valore in HEADER_SICUREZZA.items():
            risposta.headers.setdefault(nome, valore)
        if request.url.path.startswith("/api/") or request.url.path in SENZA_INGRESSO:
            risposta.headers.setdefault("Cache-Control", "no-store")
        return risposta

    @app.exception_handler(ErroreController)
    async def _gestisci_controller(request: Request, exc: ErroreController) -> JSONResponse:
        return _errore(exc)

    @app.exception_handler(RequestValidationError)
    async def _gestisci_validazione(request: Request, exc: RequestValidationError) -> JSONResponse:
        campi = [".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()]
        return _errore(ErroreController("RichiestaNonValida", details={"campo": campi[0] if campi else None}))

    @app.exception_handler(StarletteHTTPException)
    async def _gestisci_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _errore(ErroreController("NonTrovato"))
        if exc.status_code == 405:
            return _errore(ErroreController("MetodoNonAmmesso"))
        if 400 <= exc.status_code < 500:
            return _errore(ErroreController("RichiestaNonValida"))
        log.error("errore HTTP %s: %s", exc.status_code, exc.detail)
        return _errore(ErroreController("ErroreInterno"))

    @app.exception_handler(Exception)
    async def _gestisci_imprevisto(request: Request, exc: Exception) -> JSONResponse:
        log.exception("errore imprevisto su %s %s", request.method, request.url.path)
        return _errore(ErroreController("ErroreInterno"))

    app.include_router(sessione.router)
    app.include_router(conferme.router)
    for router in router_extra or []:
        app.include_router(router)
    return app
