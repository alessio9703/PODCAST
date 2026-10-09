import logging
import re
from pathlib import Path

import pytest

from app.agente.esecutore import Risultato
from app.servizi.esiti import ERROR_TYPES, Pulitore, valuta

AGENTE = Path("/srv/lil/podcast-agent")
P = Pulitore(AGENTE, "/home/lil")


def r(exit_code, json=None, timeout=False):
    return Risultato(azione="prova", exit_code=exit_code, json=json, stderr="dettaglio su stderr", durata_s=0.1, timeout=timeout)


def errore(nome, exit_code, details=None):
    j = {"ok": False, "error_type": nome, "error": f"messaggio {nome}", "exit_code": exit_code}
    if details is not None:
        j["details"] = details
    return j


def test_successo_ripulito():
    stato, corpo = valuta(r(0, {"ok": True, "run": "r1", "run_dir": "/srv/x", "documento": {"nome": "a.pdf", "percorso": "/srv/a.pdf"},
                                "voices": [{"speaker": "A", "voice_id": "abc"}], "nota": "/srv/lil/podcast-agent/output/x",
                                "rel": "output/r1/file.mp3", "testo": "Ciao / a tutti"}), P)
    assert stato == 200
    assert corpo == {"ok": True, "dati": {"run": "r1", "documento": {"nome": "a.pdf"}, "voices": [{"speaker": "A"}], "testo": "Ciao / a tutti"}}


def test_percorsi_di_home_e_assoluti_tolti():
    _, corpo = valuta(r(0, {"ok": True, "a": "/home/lil/x", "b": "~/segreti/file", "c": "/etc/passwd", "d": ["ok", "/tmp/x"]}), P)
    assert corpo["dati"] == {"d": ["ok"]}


@pytest.mark.parametrize("nome, stato, esito, riprovabile", [
    ("EpisodeConfigError", 422, "correggibile", False),
    ("RunBusy", 409, "correggibile", True),
    ("VersionConflict", 409, "correggibile", False),
    ("LockTimeout", 409, "correggibile", True),
    ("ApprovalRequired", 409, "correggibile", False),
    ("ModelRateLimited", 422, "correggibile", True),
])
def test_esito_1(nome, stato, esito, riprovabile):
    s, corpo = valuta(r(1, errore(nome, 1, {"campo": "durata"})), P)
    assert (s, corpo["esito"], corpo["riprovabile"], corpo["error_type"]) == (stato, esito, riprovabile, nome)
    assert corpo["details"] == {"campo": "durata"} and corpo["messaggio"] == f"messaggio {nome}"


@pytest.mark.parametrize("nome", ["InvalidArguments", "NotAllowedInWebMode", "ByRequired", "InternalError"])
def test_errori_adattatore_500_e_nel_log(nome, caplog):
    with caplog.at_level(logging.ERROR, logger="podcast_web.esiti"):
        s, corpo = valuta(r(1, errore(nome, 1, {"argomenti": ["--config"]})), P)
    assert s == 500 and corpo["error_type"] == nome and "details" not in corpo
    assert nome in caplog.text and "--config" in caplog.text and "dettaglio su stderr" in caplog.text


def test_esito_2_senza_riprova():
    s, corpo = valuta(r(2, errore("ProviderQuotaError", 2)), P)
    assert (s, corpo["esito"], corpo["riprovabile"]) == (502, "servizio_esterno", False)


def test_esito_3():
    s, corpo = valuta(r(3, errore("AmbiguousName", 3, {"query": "piano", "candidates": ["Piano calmo", "Piano forte"]})), P)
    assert s == 409 and corpo["esito"] == "decisione" and corpo["details"]["candidates"] == ["Piano calmo", "Piano forte"]


def test_nome_fuori_elenco_conserva_l_exit():
    assert valuta(r(2, errore("ValueError", 2)), P)[1]["error_type"] == "ExternalServiceError"
    assert valuta(r(3, errore("Strano", 3)), P)[1]["error_type"] == "UserDecisionRequired"
    s, corpo = valuta(r(1, errore("KeyError", 1)), P)
    assert s == 500 and corpo["error_type"] == "InternalError"
    # nome noto ma con exit diverso dal contratto: generico dell'exit
    assert valuta(r(1, errore("ConsentRequired", 1)), P)[1]["error_type"] == "InternalError"


def test_timeout_e_incoerenze():
    assert valuta(r(None, timeout=True), P)[0] == 504
    assert valuta(r(0, None), P)[0] == 500
    assert valuta(r(0, {"ok": False}), P)[0] == 500
    assert valuta(r(1, {"ok": True}), P)[0] == 500
    assert valuta(r(7, errore("X", 7)), P)[0] == 500


def test_elenco_completo_del_contratto():
    """Lo stesso elenco di §3 del contratto, exit code compresi (si legge il file, non si importa)."""
    contratto = Path(__file__).resolve().parents[2] / "podcast-agent" / "docs" / "CONTRATTO_INTERFACCIA.md"
    testo = contratto.read_text(encoding="utf-8").split("## 3.", 1)[1].split("## 4.", 1)[0]
    righe = re.findall(r"^\| `(\w+)` \| (?:\*\*)?([123])(?:\*\*)? \|", testo, re.M)
    assert dict((n, int(e)) for n, e in righe) == ERROR_TYPES
    assert {n for n, e in ERROR_TYPES.items() if e == 3} == {
        "UserDecisionRequired", "SpokenNameIncomplete", "AmbiguousName", "ConsentRequired", "CloneOutcomeUnknown",
        "RecordingChanged", "RevokedMeanwhile", "MusicChanged", "LessonRemovalRequired"}
