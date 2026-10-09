"""Correzioni dopo la revisione di A4 (medie 1, 2, 3)."""

import json
import subprocess
import sys
from pathlib import Path

from app.servizi.stima import impronta_stima
from tests.conftest import righe_registro
from tests.test_lavori import PRODUCE, PRODUCI, STIMA


def test_produzione_con_una_stima_diversa_non_parte(client, agente, ambiente):
    """MEDIA-1: la produzione e' legata alla stima vista."""
    (agente.radice / "tools" / "produce.py").write_text(PRODUCE)
    vista = {**STIMA["json"]}
    agente.scenario("estimate.py", {"exit": 0, "json": vista})
    r = client.get("/api/episodi/run-uno/stima")
    assert r.json()["dati"]["impronta"] == impronta_stima(vista)
    # nel frattempo un altro approva una versione piu' lunga
    agente.scenario("estimate.py", {"exit": 0, "json": {**vista, "approved_version": 3, "cost_usd": 9.0, "turns": 12}})
    r = client.post("/api/episodi/run-uno/produzione", json={"impronta": impronta_stima(vista)})
    assert r.status_code == 409 and r.json()["error_type"] == "StimaCambiata"
    assert agente.chiamate("produce.py") == []
    assert righe_registro(ambiente)[-1]["esito"] == "StimaCambiata"


def test_produzione_senza_impronta(client, agente):
    agente.scenario("estimate.py", STIMA)
    assert client.post("/api/episodi/run-uno/produzione", json={}).status_code == 400
    assert agente.chiamate("produce.py") == []


def test_impronta_cambia_con_le_voci_da_clonare():
    base = {"approved_version": 2, "cost_usd": 1.0, "voices_to_clone": ["A"], "consent_missing": []}
    assert impronta_stima(base) != impronta_stima({**base, "voices_to_clone": ["A", "B"]})
    assert impronta_stima(base) == impronta_stima({**base, "rendered": "testo diverso"})


def test_interfaccia_non_incorniciabile(client):
    """MEDIA-2."""
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_revoche_da_riapplicare(tmp_path):
    """MEDIA-3: dopo un ripristino, le revoche successive al backup si ricavano dal registro."""
    registro = tmp_path / "r.jsonl"
    righe = [
        {"at": "2026-10-08T10:00:00+00:00", "utente": "g", "nome": "Gestore", "azione": "revoca_consenso", "esito": "ok", "dettagli": {"nome": "Vecchia"}},
        {"at": "2026-10-09T10:00:00+00:00", "utente": "g", "nome": "Gestore", "azione": "revoca_consenso", "esito": "ok", "dettagli": {"nome": "Mario Rossi"}},
        {"at": "2026-10-09T11:00:00+00:00", "utente": "g", "nome": "Gestore", "azione": "registra_consenso", "esito": "ok", "dettagli": {"nome": "X"}},
        {"at": "2026-10-09T12:00:00+00:00", "utente": "g", "nome": "Gestore", "azione": "revoca_consenso", "esito": "NonPermesso"},
    ]
    registro.write_text("\n".join(json.dumps(r) for r in righe) + "\nriga rotta\n")
    r = subprocess.run([sys.executable, "-m", "strumenti.revoche_dal_registro", str(registro), "--dopo", "2026-10-09T02:00:00+00:00"],
                       capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0
    assert "--name=Mario Rossi" in r.stdout and "--revoke-consent" in r.stdout and "Vecchia" not in r.stdout
    assert "'--name=Mario Rossi'" in r.stdout  # quotato per la shell
    assert "illeggibile" in r.stderr
