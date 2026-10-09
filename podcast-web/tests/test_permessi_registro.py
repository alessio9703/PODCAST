import json
import os
import stat

import pytest

from app.ingresso import Utente
from app.registro import ErroreRegistro, Registro
from app.servizi.permessi import ErrorePermessi, Permessi
from tests.conftest import client_per, header_firmati, righe_registro


def u(*ruoli):
    return Utente("x", "X", frozenset(ruoli))


def test_permessi_di_default():
    p = Permessi.da_file(None)
    assert p.ruoli == {"autore", "approvatore", "gestore_voci", "amministratore"}
    assert p.puo(u("autore"), "copione_scrivi") and not p.puo(u("autore"), "copione_approva")
    assert p.puo(u("approvatore"), "produrre") and not p.puo(u("approvatore"), "voce_consenso")
    assert p.puo(u("gestore_voci"), "voce_clonazione") and not p.puo(u("gestore_voci"), "produrre")
    assert p.puo(u("amministratore"), "lezioni") and p.puo(u("amministratore"), "brano_carica")
    assert not p.puo(u(), "leggere")
    with pytest.raises(ValueError):
        p.puo(u("autore"), "inventato")


def test_file_dei_permessi_non_valido(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"ruoli": {"autore": ["volare"]}}))
    with pytest.raises(ErrorePermessi):
        Permessi.da_file(f)
    f.write_text("{")
    with pytest.raises(ErrorePermessi):
        Permessi.da_file(f)


def test_permesso_negato_e_registrato(ambiente_cms, agente):
    with client_per(ambiente_cms) as c:
        p = "/api/prova/approva_copione"
        h = {**header_firmati("POST", p, ruoli="autore"), "X-LIL-Richiesta": "1"}
        r = c.post(p, json={"run": "run-uno", "versione": 2}, headers=h)
        assert r.status_code == 403 and r.json()["error_type"] == "NonPermesso"
        assert agente.chiamate("script_approve.py") == []
        riga = righe_registro(ambiente_cms)[-1]
        assert riga["esito"] == "NonPermesso" and riga["utente"] == "mrossi" and riga["azione"] == "approva_copione"

        h = {**header_firmati("POST", p, ruoli="approvatore", nome="Anna Neri"), "X-LIL-Richiesta": "1"}
        assert c.post(p, json={"run": "run-uno", "versione": 2}, headers=h).status_code == 200
        # --by e' sempre l'utente dell'ingresso
        assert agente.chiamate("script_approve.py")[-1]["argv"] == ["--run-dir=run-uno", "--version=2", "--by=Anna Neri"]


def test_registro_append_only(tmp_path):
    reg = Registro(tmp_path / "d" / "reg.jsonl")
    reg.verifica()
    reg.scrivi(utente=u("autore"), azione="approva_copione", esito="ok", run="r", exit_code=0, http=200,
               costo_stimato_usd=0.5, dettagli={"versione": 3})
    reg.scrivi(utente=u("autore"), azione="produci", esito="RunBusy", run="r")
    righe = [json.loads(r) for r in (tmp_path / "d" / "reg.jsonl").read_text().splitlines()]
    assert [r["azione"] for r in righe] == ["approva_copione", "produci"]
    assert righe[0]["costo_stimato_usd"] == 0.5 and righe[0]["dettagli"] == {"versione": 3}
    assert {"at", "utente", "nome", "azione", "run", "esito"} <= set(righe[1])
    assert stat.S_IMODE(os.stat(tmp_path / "d" / "reg.jsonl").st_mode) == 0o600


def test_registro_link_simbolico_rifiutato(tmp_path):
    (tmp_path / "vero").write_text("")
    os.symlink(tmp_path / "vero", tmp_path / "reg.jsonl")
    with pytest.raises(ErroreRegistro):
        Registro(tmp_path / "reg.jsonl").verifica()


def test_azioni_di_lettura_non_registrate(client, ambiente):
    client.post("/api/prova/copione", json={"run": "run-uno"})
    assert righe_registro(ambiente) == []


def test_costo_nel_registro(client, agente, ambiente):
    agente.scenario("music_set.py", {"exit": 0, "json": {"ok": True, "cost_usd": 1.25, "cost_eur": None}})
    client.post("/api/prova/scegli_musiche", json={"run": "run-uno", "intro": "Piano"})
    riga = righe_registro(ambiente)[-1]
    assert riga["costo_stimato_usd"] == 1.25 and "costo_stimato_eur" not in riga
