"""Base condivisa dagli entry-point CLI usati dal subagent.

Contratto che ogni tool rispetta:

  stdout  -> UN SOLO oggetto JSON, la risposta machine-readable
  stderr  -> messaggi per l'umano (avvisi, avanzamento)
  exit    -> 0 ok | 1 errore di dominio | 2 errore del provider | 3 serve l'utente

I tre codici diversi non sono decorativi: `2` significa "fermati e riporta"
(tipicamente una clonazione fallita, che NON va ritentata perche' creerebbe voci
duplicate a pagamento), `3` significa "chiedi all'utente e richiamami".

Questi file non contengono logica: importano da `src/` ed espongono.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Su Windows lo stdout/stderr di un processo lanciato da un altro puo' finire
# su una code page che non e' UTF-8 (cp1252 e simili): i nomi dei brani e gli
# accenti nei messaggi andrebbero a male in silenzio, o peggio bloccherebbero
# emit() con un UnicodeEncodeError. Forzare UTF-8 qui, una volta sola, e' piu'
# robusto di ricordarselo in ogni tool.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # stream non riconfigurabile (es. rediretto su qualcosa di insolito): ok, si va avanti

from src.audio_assembler import AssemblyError  # noqa: E402
from src.config import Config, ConfigError, load_config, load_dotenv  # noqa: E402
from src.episode_signals import SignalsError  # noqa: E402
from src.ffmpeg_tool import FfmpegNotFoundError  # noqa: E402
from src.lessons import LessonsError  # noqa: E402
from src.music_library import MusicLibraryError  # noqa: E402
from src.music_mixer import MusicChangedError, MusicMixerError  # noqa: E402
from src.pdf_reader import DocumentError  # noqa: E402
from src.script_session import ScriptSessionError  # noqa: E402
from src.script_writer import ScriptWriterError  # noqa: E402
from src.voice_library import VoiceLibraryError  # noqa: E402
from src.voice_provider import VoiceProviderError  # noqa: E402

EXIT_OK = 0
EXIT_DOMAIN = 1
EXIT_PROVIDER = 2
EXIT_NEEDS_USER = 3


class NeedsUserInput(RuntimeError):
    """L'agente deve chiedere qualcosa all'utente e richiamare il tool."""


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Percorso di config.yaml")


def bootstrap(args: argparse.Namespace) -> Config:
    """Carica .env e config.yaml. Le API key NON vengono mai stampate."""
    load_dotenv()
    return load_config(getattr(args, "config", None))


def emit(payload: dict[str, Any]) -> None:
    """Scrive la risposta JSON su stdout."""
    data = {"ok": True, **payload}
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def note(message: str) -> None:
    """Messaggio per l'umano: va su stderr, non inquina il JSON."""
    print(message, file=sys.stderr, flush=True)


def _classify(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, NeedsUserInput):
        return EXIT_NEEDS_USER, "needs_user_input"
    # MusicChangedError e' una MusicMixerError: va controllata PRIMA, altrimenti
    # ricadrebbe nel ramo generico qui sotto con lo stesso exit code di un
    # qualunque altro errore di dominio, perdendo il segnale "serve una
    # risposta esplicita" (--accept-changed-music).
    if isinstance(exc, MusicChangedError):
        return EXIT_NEEDS_USER, type(exc).__name__
    if isinstance(exc, VoiceProviderError):
        return EXIT_PROVIDER, type(exc).__name__
    if isinstance(
        exc,
        (
            ConfigError,
            DocumentError,
            VoiceLibraryError,
            ScriptWriterError,
            ScriptSessionError,
            AssemblyError,
            MusicLibraryError,
            MusicMixerError,
            FfmpegNotFoundError,
            FileNotFoundError,
            ValueError,
            SignalsError,
            LessonsError,
        ),
    ):
        return EXIT_DOMAIN, type(exc).__name__
    return EXIT_DOMAIN, type(exc).__name__


def run_tool(main: Callable[[], None]) -> None:
    """Esegue il tool traducendo le eccezioni in JSON + exit code."""
    try:
        main()
    except KeyboardInterrupt:
        note("Interrotto.")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - tradotte deliberatamente
        code, kind = _classify(exc)
        json.dump(
            {"ok": False, "error_type": kind, "error": str(exc), "exit_code": code},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        note(f"ERRORE [{kind}] {exc}")
        raise SystemExit(code)


def resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


__all__ = [
    "PROJECT_ROOT",
    "EXIT_OK",
    "EXIT_DOMAIN",
    "EXIT_PROVIDER",
    "EXIT_NEEDS_USER",
    "NeedsUserInput",
    "add_config_arg",
    "bootstrap",
    "emit",
    "note",
    "run_tool",
    "resolve_path",
]
