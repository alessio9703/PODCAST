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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agente.tabella import verifica_tabella
from app.api import avanzate, conferme, correzioni, episodi, lavori, librerie, sessione
from app.lavori.coda import Coda
from app.config import RADICE_WEB, Config, carica_config
from app.ingresso import crea_ingresso
from app.registro import Registro
from app.servizi.avvio import StatoSalute, verifica_avvio
from app.servizi.azioni import Contesto
from app.servizi.errori import ErroreController
from app.servizi.esiti import Pulitore
from app.servizi.gettoni import Gettoni
from app.servizi.permessi import RUOLI_INIZIALI, Permessi

log = logging.getLogger("podcast_web")

STATICI = RADICE_WEB / "static"
METODI_CHE_CAMBIANO = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HEADER_RICHIESTA = "x-lil-richiesta"
TIPO_JSON = "application/json"
TIPO_BINARIO = "application/octet-stream"
# Le sole rotte che ricevono un corpo binario (caricamenti, con il loro limite in
# streaming). Altrove si accetta solo JSON, con il limite di CORPO_JSON_MAX.
CARICAMENTI = frozenset({("POST", "/api/documenti"), ("POST", "/api/voci"), ("POST", "/api/musiche/intro"),
                         ("POST", "/api/musiche/outro"), ("POST", "/api/musiche/sottofondo")})
SENZA_INGRESSO = frozenset({"/salute"})
# Corpo massimo delle richieste non binarie (JSON): i caricamenti hanno il loro limite.
CORPO_JSON_MAX = 256 * 1024

HEADER_SICUREZZA = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


def _errore(exc: ErroreController) -> JSONResponse:
    return JSONResponse(exc.corpo(), status_code=exc.status)


class LimiteCorpo:
    """Middleware ASGI: fuori dalle rotte di caricamento, un corpo oltre CORPO_JSON_MAX -> 413, contato
    mentre arriva (vale anche senza Content-Length o con un valore falso), prima
    che FastAPI lo tenga tutto in memoria."""

    def __init__(self, app: Any, massimo: int = CORPO_JSON_MAX) -> None:
        self.app = app
        self.massimo = massimo

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if (scope.get("method"), scope.get("path")) in CARICAMENTI:
            await self.app(scope, receive, send)
            return
        intestazioni = {k.lower(): v for k, v in scope.get("headers", [])}
        lunghezza = intestazioni.get(b"content-length", b"")
        if lunghezza.isdigit() and int(lunghezza) > self.massimo:
            await _errore(ErroreController("FileTroppoGrande", "Richiesta troppo grande."))(scope, receive, send)
            return
        letti = 0
        superato = False
        massimo = self.massimo

        async def receive_contato() -> Any:
            # Oltre il limite l'app vede una disconnessione: smette di leggere e
            # non esegue niente; la risposta 413 la manda questo middleware.
            nonlocal letti, superato
            if superato:
                return {"type": "http.disconnect"}
            messaggio = await receive()
            if messaggio["type"] == "http.request":
                letti += len(messaggio.get("body", b""))
                if letti > massimo:
                    superato = True
                    return {"type": "http.disconnect"}
            return messaggio

        async def send_filtrato(messaggio: Any) -> None:
            if not superato:
                await send(messaggio)

        try:
            await self.app(scope, receive_contato, send_filtrato)
        except Exception:
            if not superato:
                raise
        if superato:
            await _errore(ErroreController("FileTroppoGrande", "Richiesta troppo grande."))(scope, receive, send)


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
        coda = Coda(app.state.ctx)
        app.state.ctx.coda = coda
        coda.avvia()
        app.state.salute.segna_pronto()
        log.info("controller pronto (AUTH_MODE=%s)", config.auth_mode)
        try:
            yield
        finally:
            await coda.chiudi()

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
                    ammesso = TIPO_BINARIO if (request.method, percorso) in CARICAMENTI else TIPO_JSON
                    if ha_corpo and tipo != ammesso:
                        raise ErroreController("FileNonAmmesso", "Tipo di contenuto non ammesso.")
            risposta = await call_next(request)
        except ErroreController as exc:
            risposta = _errore(exc)
        except Exception:
            # Anche un errore imprevisto esce nel formato unico e con gli header di
            # sicurezza (prima finiva nel ServerErrorMiddleware, senza header).
            log.exception("errore imprevisto su %s %s", request.method, request.url.path)
            risposta = _errore(ErroreController("ErroreInterno"))
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

    app.add_middleware(LimiteCorpo)
    app.include_router(sessione.router)
    app.include_router(conferme.router)
    app.include_router(episodi.router)
    app.include_router(librerie.router)
    app.include_router(lavori.router)
    app.include_router(correzioni.router)
    app.include_router(avanzate.router)
    for router in router_extra or []:
        app.include_router(router)

    # Interfaccia: pagina unica e file statici, dietro lo stesso ingresso delle API.
    @app.get("/", include_in_schema=False)
    async def pagina() -> FileResponse:
        return FileResponse(STATICI / "index.html", media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATICI, follow_symlink=False), name="static")
    return app
