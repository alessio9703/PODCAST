"""Fixture comuni: un agente finto su disco, la configurazione, l'app e i client.

Nessuna chiamata reale: l'agente finto e' una cartella con `.venv/bin/python`
(un link al python dei test) e `tools/*.py` che rispondono da `scenari.json`.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from app.agente.tabella import TABELLA
from app.config import carica_config
from app.ingresso import Utente, utente_corrente
from app.ingresso.cms import calcola_firma, codifica_header
from app.main import crea_app
from app.servizi.azioni import esegui_azione

QUI = Path(__file__).resolve().parent
SEGRETO = "s" * 40
SEGRETO_VECCHIO = "v" * 40
LOOPBACK = ("127.0.0.1", 50000)
BASE_URL = "http://127.0.0.1:8765"


class AgenteFinto:
    def __init__(self, radice: Path) -> None:
        self.radice = radice

    def scenario(self, tool: str, risposta: Any) -> None:
        file = self.radice / "scenari.json"
        dati = json.loads(file.read_text()) if file.exists() else {}
        dati[tool] = risposta
        file.write_text(json.dumps(dati))
        (self.radice / "contatori.json").unlink(missing_ok=True)

    def chiamate(self, tool: str | None = None) -> list[dict[str, Any]]:
        file = self.radice / "chiamate.jsonl"
        if not file.exists():
            return []
        righe = [json.loads(r) for r in file.read_text().splitlines() if r.strip()]
        return [r for r in righe if tool is None or r["tool"] == tool]

    def azzera_chiamate(self) -> None:
        (self.radice / "chiamate.jsonl").unlink(missing_ok=True)


def crea_agente_finto(radice: Path) -> AgenteFinto:
    (radice / ".venv" / "bin").mkdir(parents=True)
    (radice / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (radice / "tools").mkdir()
    for tool in {c.tool for c in TABELLA.values()}:
        shutil.copy(QUI / "agente_finto.py", radice / "tools" / tool)
    (radice / "output" / "run-uno").mkdir(parents=True)
    (radice / "documenti").mkdir()
    (radice / "voci_disponibili").mkdir()
    (radice / "musiche").mkdir()
    (radice / "VERSION").write_text("1.0-web\n")
    return AgenteFinto(radice)


@pytest.fixture
def agente(tmp_path: Path) -> AgenteFinto:
    return crea_agente_finto(tmp_path / "agente")


@pytest.fixture
def ambiente(tmp_path: Path, agente: AgenteFinto) -> dict[str, str]:
    return {
        "AUTH_MODE": "demo",
        "HOST": "127.0.0.1",
        "AGENT_DIR": str(agente.radice),
        "AGENT_VERSIONE_ATTESA": "1.0-web",
        "REGISTRO_FILE": str(tmp_path / "dati" / "registro.jsonl"),
        "DEMO_UTENTE": "demo",
        "DEMO_NOME": "Utente Demo",
    }


# Rotta solo per i test: lancia un'azione breve qualsiasi della tabella.
router_prova = APIRouter()


@router_prova.post("/api/prova/{azione}")
async def _prova(azione: str, request: Request, utente: Utente = Depends(utente_corrente)):
    parametri = await request.json() if await request.body() else {}
    return await esegui_azione(request, utente, azione, parametri)


def client_per(ambiente: dict[str, str], **extra: str) -> TestClient:
    config = carica_config({**ambiente, **extra})
    app = crea_app(config, router_extra=[router_prova])
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK, raise_server_exceptions=False)


@pytest.fixture
def client(ambiente: dict[str, str]):
    with client_per(ambiente) as c:
        c.headers.update({"X-LIL-Richiesta": "1"})
        yield c


@pytest.fixture
def ambiente_cms(ambiente: dict[str, str]) -> dict[str, str]:
    return {**ambiente, "AUTH_MODE": "cms", "LIL_SEGRETO": SEGRETO, "LIL_SEGRETO_PRECEDENTE": SEGRETO_VECCHIO}


def header_firmati(
    metodo: str, percorso: str, *, utente: str = "mrossi", nome: str = "Mario Rossi",
    ruoli: str = "autore", segreto: str = SEGRETO, timestamp: int | None = None,
) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    nome_h = codifica_header(nome)
    firma = calcola_firma(segreto.encode(), ts, metodo, percorso, utente, ruoli, nome_h)
    return {"X-LIL-Utente": utente, "X-LIL-Nome": nome_h, "X-LIL-Ruoli": ruoli,
            "X-LIL-Timestamp": ts, "X-LIL-Firma": firma}


def righe_registro(ambiente: dict[str, str]) -> list[dict[str, Any]]:
    file = Path(ambiente["REGISTRO_FILE"])
    if not file.exists():
        return []
    return [json.loads(r) for r in file.read_text().splitlines() if r.strip()]
