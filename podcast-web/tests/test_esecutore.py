import asyncio
import os

from app.agente import esecutore
from app.config import carica_config


def test_ambiente_minimo_e_cartella(ambiente, agente, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-segreta")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "segreta")
    config = carica_config({**ambiente, "AGENT_PATH_EXTRA": "/opt/homebrew/bin"})
    risultato = asyncio.run(esecutore.esegui(config, "copione", {"run": "run-uno"}))
    assert risultato.ok
    chiamata = agente.chiamate("script_show.py")[0]
    assert chiamata["argv"] == ["--run-dir=run-uno"]
    assert chiamata["cwd"] == str(agente.radice)
    env = chiamata["env"]
    assert env["PODCAST_AGENT_WEB"] == "1"
    assert "ANTHROPIC_API_KEY" not in env and "FISH_AUDIO_API_KEY" not in env
    assert env["PATH"].startswith("/opt/homebrew/bin:")
    ammesse = {"PODCAST_AGENT_WEB", "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONDONTWRITEBYTECODE",
               "PYTHONIOENCODING", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert set(env) <= ammesse, set(env) - ammesse


def test_riga_di_comando_usa_il_python_della_venv(ambiente, agente):
    config = carica_config(ambiente)
    argv = esecutore.riga_di_comando(config, "copione", {"run": "run-uno"})
    assert argv == [str(agente.radice / ".venv" / "bin" / "python"), "tools/script_show.py", "--run-dir=run-uno"]


def test_timeout(ambiente, agente):
    agente.scenario("script_show.py", {"sleep": 5, "json": {"ok": True}})
    config = carica_config(ambiente)
    risultato = asyncio.run(esecutore.esegui(config, "copione", {"run": "run-uno"}, timeout=0.5))
    assert risultato.timeout and risultato.exit_code is None


def test_json_rotto(ambiente, agente):
    agente.scenario("script_show.py", {"stdout": "non json", "exit": 0})
    risultato = asyncio.run(esecutore.esegui(carica_config(ambiente), "copione", {"run": "run-uno"}))
    assert risultato.json is None and not risultato.ok
