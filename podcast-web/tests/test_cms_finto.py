"""A4: il CMS finto firma come chiede DEPLOY.md e il controller in AUTH_MODE=cms lo accetta.

Il CMS finto calcola la firma con il codice di DEPLOY.md, indipendente dal controller:
se specifica e controller divergessero, questi test fallirebbero.
"""

import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.config import carica_config
from app.main import crea_app as crea_controller
from strumenti.cms_finto import COOKIE, crea_app as crea_cms
from tests.conftest import SEGRETO


def esegui(coro):
    return asyncio.run(coro)


# --- cosa arriva al controller (app eco) ------------------------------------

async def _eco(request):
    corpo = await request.body()
    return JSONResponse({"path": request.url.path, "query": request.url.query,
                         "headers": {k: v for k, v in request.headers.items()}, "corpo": corpo.decode("latin-1")})

ECO = Starlette(routes=[Route("/{p:path}", _eco, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])])


async def _via_cms(metodo, url, profilo="autore", **kw):
    cms = crea_cms(SEGRETO.encode(), "http://controller", trasporto=httpx.ASGITransport(app=ECO))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cms), base_url="http://127.0.0.1:8780") as c:
        if profilo:
            c.cookies.set(COOKIE, profilo)
        return await c.request(metodo, url, **kw)


def test_senza_login_si_va_alla_pagina_dei_profili():
    r = esegui(_via_cms("GET", "/", profilo=None))
    assert r.status_code == 303 and r.headers["location"] == "/__cms/"


def test_header_del_browser_tolti_e_quelli_della_pagina_inoltrati():
    r = esegui(_via_cms("POST", "/api/x?a=1", headers={
        "X-LIL-Utente": "intruso", "X-LIL-Ruoli": "amministratore", "X-LIL-Firma": "v1=" + "0" * 64,
        "X-LIL-Richiesta": "1", "X-LIL-Nome-File": "a%20b.pdf", "X-LIL-Gettone": "g", "Range": "bytes=0-1",
        "Last-Event-ID": "4", "X-LIL-Altro": "x", "Content-Type": "application/octet-stream"}, content=b"dati"))
    h = r.json()["headers"]
    assert h["x-lil-utente"] == "aautore" and h["x-lil-ruoli"] == "autore"
    assert h["x-lil-richiesta"] == "1" and h["x-lil-nome-file"] == "a%20b.pdf" and h["x-lil-gettone"] == "g"
    assert h["range"] == "bytes=0-1" and h["last-event-id"] == "4"
    assert "x-lil-altro" not in h and "cookie" not in h
    assert r.json()["corpo"] == "dati" and r.json()["query"] == "a=1"


# --- con il controller vero in AUTH_MODE=cms ----------------------------------

async def _con_controller(ambiente_cms, passi):
    controller = crea_controller(carica_config(ambiente_cms))
    async with controller.router.lifespan_context(controller):
        cms = crea_cms(SEGRETO.encode(), "http://127.0.0.1:8765", trasporto=httpx.ASGITransport(app=controller))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cms), base_url="http://127.0.0.1:8780") as c:
            return await passi(c)


def test_il_controller_accetta_le_firme_del_cms_finto(ambiente_cms, agente):
    async def passi(c):
        esiti = {}
        for profilo in ("autore", "approvatore", "tutti"):
            c.cookies.set(COOKIE, profilo)
            esiti[profilo] = (await c.get("/api/io")).json()["dati"]
        r = await c.get("/api/episodi?gruppo=bozze&cerca=%C3%A8")  # query firmata
        esiti["archivio"] = r.status_code
        c.cookies.set(COOKIE, "autore")
        r = await c.post("/api/documenti", content=b"%PDF-1.7 ciao", headers={
            "Content-Type": "application/octet-stream", "X-LIL-Nome-File": "Relazione%20%C3%A8.pdf", "X-LIL-Richiesta": "1"})
        esiti["carica"] = (r.status_code, r.json())
        r = await c.post("/api/episodi/run-uno/copione/approvazione", json={"versione": 1}, headers={"X-LIL-Richiesta": "1"})
        esiti["approva_autore"] = r.status_code
        r = await c.get("/")
        esiti["pagina"] = (r.status_code, r.headers.get("content-security-policy", ""))
        return esiti

    esiti = esegui(_con_controller(ambiente_cms, passi))
    assert esiti["autore"]["utente"] == "aautore" and esiti["autore"]["ruoli"] == ["autore"]
    assert esiti["approvatore"]["nome"] == "Bruno Approvatore"
    assert esiti["tutti"]["ruoli"] == ["approvatore", "autore", "gestore_voci"]
    assert esiti["archivio"] == 200
    assert esiti["carica"][0] == 201 and esiti["carica"][1]["dati"]["nome"] == "Relazione è.pdf"
    assert esiti["approva_autore"] == 403  # i permessi valgono anche dietro il CMS
    assert esiti["pagina"][0] == 200 and "script-src 'self'" in esiti["pagina"][1]


def test_sse_attraverso_il_cms_finto(ambiente_cms, agente):
    async def passi(c):
        c.cookies.set(COOKIE, "tutti")
        async with c.stream("GET", "/api/episodi/run-uno/eventi") as r:
            tipo = r.headers["content-type"]
            testo = "".join([t async for t in r.aiter_text()])
        return tipo, testo
    tipo, testo = esegui(_con_controller(ambiente_cms, passi))
    assert tipo.startswith("text/event-stream") and "event: fine" in testo


def test_segreto_sbagliato_401(ambiente_cms, agente):
    async def passi_sbagliati():
        controller = crea_controller(carica_config(ambiente_cms))
        async with controller.router.lifespan_context(controller):
            cms = crea_cms(b"x" * 40, "http://c", trasporto=httpx.ASGITransport(app=controller))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cms), base_url="http://127.0.0.1:8780") as c:
                c.cookies.set(COOKIE, "autore")
                return (await c.get("/api/io")).status_code
    assert esegui(passi_sbagliati()) == 401


def test_firma_come_deploy_md():
    from strumenti.cms_finto import header_lil
    h = header_lil(b"0123456789abcdef0123456789abcdef", "GET", "/api/episodi?gruppo=bozze", "mrossi",
                   ["autore", "approvatore"], "Mario Rossi", timestamp=1760000000)
    assert h["X-LIL-Firma"] == "v1=80be18ebc1f3d9b1590c45c12c3899a9c8ac31ffc8c7cffeeb18479babbfd520"
    _ = json
