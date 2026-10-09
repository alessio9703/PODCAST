"""CMS finto: un piccolo proxy locale che firma le richieste come fara' il CMS di LIFE.

Serve per provare AUTH_MODE=cms sul Mac:

    browser -> http://127.0.0.1:8780 (CMS finto) -> http://127.0.0.1:8765 (controller, AUTH_MODE=cms)

Fa esattamente cio' che `DEPLOY.md` chiede al CMS:
- toglie dalle richieste del browser gli header `X-LIL-*` di identita' (inoltra intatti
  solo `X-LIL-Richiesta`, `X-LIL-Nome-File`, `X-LIL-Gettone`, oltre a `Range` e
  `Last-Event-ID`) e non inoltra i cookie della propria sessione;
- aggiunge `X-LIL-Utente`, `X-LIL-Nome`, `X-LIL-Ruoli`, `X-LIL-Timestamp`, `X-LIL-Firma`
  calcolati sul percorso inoltrato (con la query), con il segreto condiviso;
- inoltra il corpo grezzo dei caricamenti senza trattenerlo e restituisce le risposte in
  streaming (lo SSE arriva subito, niente buffering).

La firma e' calcolata qui in modo INDIPENDENTE dal controller (stesso codice di esempio
di DEPLOY.md): se la specifica e il controller divergessero, i test lo vedrebbero.

Il "login" e' una pagina di scelta del profilo (`/__cms/`): quattro utenti, uno per ruolo,
piu' uno con tutti i ruoli. Ascolta solo su 127.0.0.1.

    LIL_SEGRETO=... .venv/bin/python -m strumenti.cms_finto   [--porta 8780] [--controller http://127.0.0.1:8765]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import os
import sys
import time
from typing import AsyncIterator
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

PROFILI = {
    "autore": ("aautore", "Alessia Autore", ["autore"]),
    "approvatore": ("bapprova", "Bruno Approvatore", ["approvatore"]),
    "gestore_voci": ("cvoci", "Carla Gestore Voci", ["gestore_voci"]),
    "amministratore": ("damm", "Dario Amministratore", ["amministratore"]),
    "tutti": ("eredazione", "Elena Redazione", ["autore", "approvatore", "gestore_voci"]),
}
COOKIE = "cms_finto_profilo"

# Header della pagina che il CMS inoltra intatti (DEPLOY.md §3). Tutti gli altri X-LIL-* si tolgono.
X_LIL_DELLA_PAGINA = {"x-lil-richiesta", "x-lil-nome-file", "x-lil-gettone"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade", "host", "content-length", "cookie"}
RISPOSTA_DA_NON_COPIARE = {"connection", "keep-alive", "transfer-encoding", "content-length", "set-cookie"}


def header_lil(segreto: bytes, metodo: str, percorso: str, utente: str, ruoli: list[str], nome: str,
               timestamp: int | None = None) -> dict[str, str]:
    """Il codice di riferimento di DEPLOY.md §1."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    nome_h = quote(nome, safe="")
    ruoli_h = ",".join(ruoli)
    canonica = "\n".join(["v1", ts, metodo.upper(), percorso, utente, ruoli_h, nome_h]).encode("utf-8")
    firma = "v1=" + hmac.new(segreto, canonica, hashlib.sha256).hexdigest()
    return {"X-LIL-Utente": utente, "X-LIL-Nome": nome_h, "X-LIL-Ruoli": ruoli_h,
            "X-LIL-Timestamp": ts, "X-LIL-Firma": firma}


def header_da_inoltrare(request: Request) -> list[tuple[str, str]]:
    uscita = []
    for nome, valore in request.headers.items():
        basso = nome.lower()
        if basso in HOP_BY_HOP:
            continue
        if basso.startswith("x-lil-") and basso not in X_LIL_DELLA_PAGINA:
            continue  # identita' mai dal browser
        uscita.append((nome, valore))
    return uscita


def crea_app(segreto: bytes, controller: str, *, trasporto: httpx.AsyncBaseTransport | None = None) -> Starlette:
    client = httpx.AsyncClient(base_url=controller, transport=trasporto,
                               timeout=httpx.Timeout(connect=5.0, read=None, write=120.0, pool=5.0))

    async def pagina_profili(request: Request) -> Response:
        attuale = request.cookies.get(COOKIE, "")
        voci = "".join(
            f'<li><a href="/__cms/entra?profilo={k}">{html.escape(n)}</a> · {", ".join(r)}'
            f'{" <strong>(attuale)</strong>" if k == attuale else ""}</li>'
            for k, (_, n, r) in PROFILI.items())
        return HTMLResponse(
            "<!doctype html><html lang='it'><meta charset='utf-8'><title>CMS finto</title>"
            "<body><h1>CMS finto · scegli chi sei</h1>"
            "<p>Simula il login del CMS di LIFE: le richieste verso il controller vengono firmate "
            "con l'utente scelto.</p>"
            f"<ul>{voci}</ul><p><a href='/'>Vai a LIL Podcast</a> · <a href='/__cms/esci'>Esci</a></p></body></html>")

    async def entra(request: Request) -> Response:
        profilo = request.query_params.get("profilo", "")
        if profilo not in PROFILI:
            return PlainTextResponse("profilo sconosciuto", status_code=400)
        risposta = RedirectResponse("/", status_code=303)
        risposta.set_cookie(COOKIE, profilo, httponly=True, samesite="strict")
        return risposta

    async def esci(request: Request) -> Response:
        risposta = RedirectResponse("/__cms/", status_code=303)
        risposta.delete_cookie(COOKIE)
        return risposta

    async def inoltra(request: Request) -> Response:
        profilo = PROFILI.get(request.cookies.get(COOKIE, ""))
        if profilo is None:
            return RedirectResponse("/__cms/", status_code=303)
        utente, nome, ruoli = profilo
        grezzo = request.scope.get("raw_path") or request.url.path.encode()
        percorso = grezzo.decode("latin-1")
        query = request.scope.get("query_string", b"").decode("latin-1")
        if query:
            percorso = f"{percorso}?{query}"
        intestazioni = header_da_inoltrare(request)
        intestazioni += list(header_lil(segreto, request.method, percorso, utente, ruoli, nome).items())

        async def corpo() -> AsyncIterator[bytes]:
            async for pezzo in request.stream():
                yield pezzo

        con_corpo = request.method in ("POST", "PUT", "PATCH", "DELETE")
        richiesta = client.build_request(request.method, percorso, headers=intestazioni,
                                         content=corpo() if con_corpo else None)
        try:
            risposta = await client.send(richiesta, stream=True)
        except httpx.HTTPError:
            return PlainTextResponse("Il controller non risponde.", status_code=502)
        uscita = {k: v for k, v in risposta.headers.items() if k.lower() not in RISPOSTA_DA_NON_COPIARE}
        return StreamingResponse(risposta.aiter_raw(), status_code=risposta.status_code, headers=uscita,
                                 background=BackgroundTask(risposta.aclose))

    metodi = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    app = Starlette(routes=[
        Route("/__cms/", pagina_profili),
        Route("/__cms/entra", entra),
        Route("/__cms/esci", esci),
        Route("/{percorso:path}", inoltra, methods=metodi),
    ])
    app.state.client = client
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--porta", type=int, default=8780)
    parser.add_argument("--controller", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    segreto = os.environ.get("LIL_SEGRETO", "")
    if len(segreto.encode()) < 32:
        print("Serve LIL_SEGRETO (almeno 32 byte), lo stesso del controller.", file=sys.stderr)
        return 2
    import uvicorn

    print(f"CMS finto su http://127.0.0.1:{args.porta}/__cms/ -> {args.controller}")
    uvicorn.run(crea_app(segreto.encode(), args.controller), host="127.0.0.1", port=args.porta,
                log_level="info", server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
