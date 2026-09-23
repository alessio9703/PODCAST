"""Caricamento di config.yaml e istanziazione dinamica del provider attivo."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .voice_provider import VoiceProvider, VoiceProviderError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """Configurazione mancante o incoerente."""


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path

    # -- accessi tipizzati ------------------------------------------------

    @property
    def active_provider(self) -> str:
        value = self.raw.get("active_provider")
        if not value:
            raise ConfigError(f"'active_provider' non definito in {self.path}")
        return str(value)

    @property
    def provider_config(self) -> dict[str, Any]:
        providers = self.raw.get("providers") or {}
        if self.active_provider not in providers:
            raise ConfigError(
                f"Provider attivo '{self.active_provider}' non presente nella sezione "
                f"'providers' di {self.path}. Definiti: {sorted(providers) or 'nessuno'}"
            )
        return dict(providers[self.active_provider])

    def path_setting(self, key: str) -> Path:
        paths = self.raw.get("paths") or {}
        if key not in paths:
            raise ConfigError(f"paths.{key} non definito in {self.path}")
        return (PROJECT_ROOT / str(paths[key])).resolve()

    @property
    def documents_dir(self) -> Path:
        return self.path_setting("documents_dir")

    @property
    def voices_dir(self) -> Path:
        return self.path_setting("voices_dir")

    @property
    def registry_file(self) -> Path:
        return self.path_setting("registry_file")

    @property
    def output_dir(self) -> Path:
        return self.path_setting("output_dir")

    @property
    def music_dir(self) -> Path:
        """Radice di intro/, outro/, sottofondo/. Default: ./musiche.

        A differenza degli altri path_setting(), qui manca 'paths.music_dir'
        in config.yaml non e' un errore: i progetti creati prima di questa
        funzione non ce l'hanno, e la musica resta semplicemente non
        disponibile (vedi src/music_library.py) invece di rompere l'avvio.
        """
        paths = self.raw.get("paths") or {}
        value = paths.get("music_dir", "./musiche")
        return (PROJECT_ROOT / str(value)).resolve()

    @property
    def audio(self) -> dict[str, Any]:
        return dict(self.raw.get("audio") or {})

    @property
    def script(self) -> dict[str, Any]:
        return dict(self.raw.get("script") or {})

    @property
    def music(self) -> dict[str, Any]:
        return dict(self.raw.get("music") or {})

    @property
    def lessons(self) -> dict[str, Any]:
        return dict(self.raw.get("lessons") or {})

    @property
    def accepted_extensions(self) -> list[str]:
        exts = self.audio.get("accepted_extensions") or [".wav", ".mp3"]
        return [str(e).lower() if str(e).startswith(".") else f".{e}".lower() for e in exts]

    # -- provider ---------------------------------------------------------

    def build_provider(self) -> VoiceProvider:
        """Istanzia il provider attivo senza che il chiamante sappia quale sia."""
        spec = self.provider_config
        target = spec.get("class")
        if not target or ":" not in str(target):
            raise ConfigError(
                f"providers.{self.active_provider}.class deve essere nella forma "
                "'modulo:Classe' (es. src.providers.fish_audio_provider:FishAudioProvider)"
            )
        module_name, class_name = str(target).split(":", 1)
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ConfigError(
                f"Impossibile importare il modulo provider '{module_name}': {exc}"
            ) from exc

        options = dict(spec.get("options") or {})

        builder = getattr(module, "build", None)
        if callable(builder):
            provider = builder(options)
        else:
            cls = getattr(module, class_name, None)
            if cls is None:
                raise ConfigError(
                    f"La classe '{class_name}' non esiste nel modulo '{module_name}'."
                )
            provider = cls(**options)

        if not isinstance(provider, VoiceProvider):
            raise ConfigError(
                f"{target} non implementa l'interfaccia VoiceProvider."
            )
        return provider

    # -- chiavi API -------------------------------------------------------

    def anthropic_api_key(self) -> str:
        env_name = str(self.script.get("api_key_env", "ANTHROPIC_API_KEY"))
        key = os.environ.get(env_name, "").strip()
        if not key:
            raise ConfigError(
                f"Variabile d'ambiente {env_name} non impostata: serve per far "
                "scrivere lo script del podcast a Claude.\n"
                f"Aggiungila al file .env:\n    {env_name}=sk-ant-..."
            )
        return key


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path).resolve() if path else DEFAULT_CONFIG
    if not config_path.is_file():
        raise ConfigError(f"File di configurazione non trovato: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} non contiene una mappa YAML valida.")
    return Config(raw=raw, path=config_path)


def load_dotenv(path: str | Path | None = None) -> None:
    """Carica un .env minimale senza dipendenze esterne (non sovrascrive l'ambiente)."""
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


__all__ = ["Config", "ConfigError", "load_config", "load_dotenv", "PROJECT_ROOT", "VoiceProviderError"]
