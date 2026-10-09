import pytest

from app.config import ErroreConfigurazione, carica_config


def test_configurazione_demo_valida(ambiente):
    config = carica_config(ambiente)
    assert config.auth_mode == "demo"
    assert config.agent_python.name == "python" and config.agent_python.parent.name == "bin"
    assert config.firma_tolleranza_s == 60


@pytest.mark.parametrize("modifica, frammento", [
    ({"AUTH_MODE": ""}, "AUTH_MODE"),
    ({"AUTH_MODE": "aperto"}, "AUTH_MODE"),
    ({"HOST": "0.0.0.0"}, "127.0.0.1"),
    ({"AGENT_DIR": ""}, "AGENT_DIR"),
    ({"AGENT_DIR": "relativo/agente"}, "assoluto"),
    ({"AGENT_VERSIONE_ATTESA": ""}, "AGENT_VERSIONE_ATTESA"),
    ({"PORT": "abc"}, "PORT"),
    ({"REGISTRO_FILE": "registro.jsonl"}, "REGISTRO_FILE"),
    ({"AGENT_PATH_EXTRA": "bin"}, "AGENT_PATH_EXTRA"),
])
def test_configurazione_rifiutata(ambiente, modifica, frammento):
    with pytest.raises(ErroreConfigurazione, match=frammento):
        carica_config({**ambiente, **modifica})


def test_cms_vuole_un_segreto_lungo(ambiente):
    with pytest.raises(ErroreConfigurazione, match="LIL_SEGRETO"):
        carica_config({**ambiente, "AUTH_MODE": "cms", "HOST": "0.0.0.0"})
    with pytest.raises(ErroreConfigurazione, match="32 byte"):
        carica_config({**ambiente, "AUTH_MODE": "cms", "LIL_SEGRETO": "corto"})
    config = carica_config({**ambiente, "AUTH_MODE": "cms", "HOST": "0.0.0.0", "LIL_SEGRETO": "x" * 32})
    assert config.host == "0.0.0.0"


def test_agente_senza_venv(ambiente, agente):
    (agente.radice / ".venv" / "bin" / "python").unlink()
    with pytest.raises(ErroreConfigurazione, match="venv"):
        carica_config(ambiente)
