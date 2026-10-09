"""A3: l'interfaccia rispetta la CSP (niente script o stili inline) e passa dall'ingresso."""

import re
from pathlib import Path

import pytest

from tests.conftest import client_per, header_firmati

STATICI = Path(__file__).resolve().parents[1] / "static"
HTML = list(STATICI.rglob("*.html"))
JS = list(STATICI.rglob("*.js"))
CSS = list(STATICI.rglob("*.css"))


def test_ci_sono_i_file():
    assert (STATICI / "index.html").exists() and len(JS) >= 10 and len(CSS) == 2


@pytest.mark.parametrize("file", HTML, ids=lambda p: p.name)
def test_html_senza_inline(file):
    testo = file.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", testo), "script inline"
    assert not re.search(r"\sstyle\s*=", testo, re.I), "attributo style"
    assert "<style" not in testo.lower(), "elemento style"
    assert not re.search(r"\son[a-z]+\s*=", testo, re.I), "gestore di evento inline"
    assert not re.search(r"(src|href)\s*=\s*\"(https?:)?//", testo), "risorse esterne"


@pytest.mark.parametrize("file", JS, ids=lambda p: p.name)
def test_js_senza_costrutti_pericolosi(file):
    testo = file.read_text(encoding="utf-8")
    for vietato in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                    'setAttribute("style"', "setAttribute('style'", "javascript:"):
        assert vietato not in testo, f"{file.name}: {vietato}"
    assert not re.search(r"fetch\(\s*['\"]https?://", testo)


@pytest.mark.parametrize("file", JS, ids=lambda p: p.name)
def test_import_risolti(file):
    for modulo in re.findall(r"from\s+\"(\.{1,2}/[^\"]+)\"", file.read_text(encoding="utf-8")):
        assert (file.parent / modulo).resolve().exists(), f"{file.name}: {modulo}"


def test_html_riferimenti_esistono():
    testo = (STATICI / "index.html").read_text(encoding="utf-8")
    for rif in re.findall(r"(?:src|href)=\"/static/([^\"]+)\"", testo):
        assert (STATICI / rif).exists(), rif


def test_colori_solo_nel_tema():
    """Il marchio LIFE si applica cambiando le variabili: app.css non ha colori scritti a mano."""
    app = (STATICI / "css" / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", app)
    assert "font-family: var(--carattere)" in app


def test_pagina_servita_con_csp(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp
    r = client.get("/static/js/app.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert client.get("/static/../app/config.py").status_code == 404
    assert client.get("/static/js/non-esiste.js").status_code == 404


def test_pagina_dietro_l_ingresso(ambiente_cms):
    with client_per(ambiente_cms) as c:
        assert c.get("/").status_code == 401
        assert c.get("/static/js/app.js").status_code == 401
        assert c.get("/", headers=header_firmati("GET", "/")).status_code == 200
