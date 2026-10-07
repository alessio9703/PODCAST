"""Base condivisa dagli entry-point CLI usati dal subagent.

Contratto che ogni tool rispetta:

  stdout  -> UN SOLO oggetto JSON, la risposta machine-readable
  stderr  -> messaggi per l'umano (avvisi, avanzamento)
  exit    -> 0 ok | 1 errore di dominio | 2 errore del provider | 3 serve l'utente

I tre codici diversi non sono decorativi: `2` significa "fermati e riporta"
(tipicamente una clonazione fallita, che NON va ritentata perche' creerebbe voci
duplicate a pagamento), `3` significa "chiedi all'utente e richiamami".

In caso di errore il JSON e':

  {"ok": false, "error_type": "...", "error": "...", "exit_code": N,
   "details": {...}}          <- solo se l'errore porta dati strutturati

`error_type` e' un nome stabile (vedi src/errors.py): un'interfaccia decide
cosa mostrare da quello, non dal testo di `error`.

Anche un'interruzione (Ctrl-C, SIGTERM) produce il JSON, con
`error_type: "Interrupted"` ed exit 1: chi chiama il tool come sottoprocesso
deve sempre trovare UN oggetto JSON su stdout.

Questi file non contengono logica: importano da `src/` ed espongono.
"""

from __future__ import annotations

import argparse
import json
import signal
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
from src.errors import NeedsUserDecision, error_details  # noqa: E402
from src.ffmpeg_tool import FfmpegNotFoundError  # noqa: E402
from src.lessons import LessonsError  # noqa: E402
from src.music_library import MusicLibraryError  # noqa: E402
from src.music_mixer import MusicChangedError, MusicMixerError  # noqa: E402
from src.paths import confine_run_dir, confine_to  # noqa: E402
from src.pdf_reader import DocumentError  # noqa: E402
from src.script_session import ScriptSessionError  # noqa: E402
from src.script_writer import ModelAccessError, ScriptWriterError  # noqa: E402
from src.voice_library import VoiceLibraryError  # noqa: E402
from src.voice_provider import VoiceProviderError  # noqa: E402

EXIT_OK = 0
EXIT_DOMAIN = 1
EXIT_PROVIDER = 2
EXIT_NEEDS_USER = 3


class NeedsUserInput(NeedsUserDecision):
    """L'agente deve chiedere qualcosa all'utente e richiamare il tool.

    Resta con il suo `error_type` storico ("needs_user_input"); gli esiti 3
    nuovi usano una sottoclasse di src.errors.NeedsUserDecision con un nome
    proprio (es. AmbiguousName, ConsentRequired).
    """

    error_type = "needs_user_input"


#: Esito di un'interruzione (Ctrl-C o SIGTERM). Exit 1 e non un codice nuovo:
#: il contratto ha quattro esiti e un'interruzione e' un "non e' andato a buon
#: fine, si puo' rilanciare", cioe' un esito 1 con un error_type riconoscibile.
INTERRUPTED = "Interrupted"


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
    if isinstance(exc, NeedsUserDecision):
        return EXIT_NEEDS_USER, exc.kind
    # MusicChangedError e' una MusicMixerError: va controllata PRIMA, altrimenti
    # ricadrebbe nel ramo generico qui sotto con lo stesso exit code di un
    # qualunque altro errore di dominio, perdendo il segnale "serve una
    # risposta esplicita" (--accept-changed-music).
    if isinstance(exc, MusicChangedError):
        return EXIT_NEEDS_USER, type(exc).__name__
    if isinstance(exc, VoiceProviderError):
        return EXIT_PROVIDER, type(exc).__name__
    # Chiave Anthropic non valida o credito esaurito: come un errore del
    # provider vocale, non si risolve riprovando (exit 2). Rate limit,
    # sovraccarico e rete restano exit 1 (ModelTemporarilyUnavailable).
    if isinstance(exc, ModelAccessError):
        return EXIT_PROVIDER, exc.kind
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
        return EXIT_DOMAIN, _kind(exc)
    return EXIT_DOMAIN, _kind(exc)


def _kind(exc: Exception) -> str:
    """error_type esplicito se l'errore lo dichiara, altrimenti il nome della classe."""
    return getattr(exc, "kind", None) or type(exc).__name__


def _emit_error(kind: str, message: str, code: int, details: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {"ok": False, "error_type": kind, "error": message, "exit_code": code}
    if details:
        payload["details"] = details
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _sigterm_as_interrupt(signum, frame) -> None:  # noqa: ANN001
    raise KeyboardInterrupt


def run_tool(main: Callable[[], None]) -> None:
    """Esegue il tool traducendo le eccezioni in JSON + exit code.

    SIGTERM viene trattato come Ctrl-C: e' il segnale con cui un adattatore
    ferma un sottoprocesso, e senza questo il processo morirebbe senza
    scrivere il JSON. Un tool che vuole fermarsi in modo piu' ordinato (finire
    il segmento in corso) installa un proprio handler dentro `main`.
    """
    try:
        signal.signal(signal.SIGTERM, _sigterm_as_interrupt)
    except (ValueError, OSError):  # non nel thread principale: si va avanti senza
        pass
    try:
        main()
    except KeyboardInterrupt:
        message = "Interrotto prima della fine. Nessun file e' rimasto scritto a meta': il comando si puo' rilanciare."
        _emit_error(INTERRUPTED, message, EXIT_DOMAIN, None)
        note(f"ERRORE [{INTERRUPTED}] {message}")
        raise SystemExit(EXIT_DOMAIN)
    except Exception as exc:  # noqa: BLE001 - tradotte deliberatamente
        code, kind = _classify(exc)
        _emit_error(kind, str(exc), code, error_details(exc))
        note(f"ERRORE [{kind}] {exc}")
        raise SystemExit(code)


def resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


# ----------------------------------------------------------------------
# Percorsi da input (vedi src/paths.py)
# ----------------------------------------------------------------------


def add_trusted_paths_arg(parser: argparse.ArgumentParser) -> None:
    """--trusted-paths: percorsi liberi, SOLO per uso locale (subagent, CLI).

    Senza, documenti e file passati per percorso devono stare nelle cartelle
    del progetto (documenti/, output/). L'adattatore web non deve MAI
    passarlo: e' l'unico modo per uscire da quelle cartelle.
    """
    parser.add_argument(
        "--trusted-paths",
        action="store_true",
        help="Uso locale: consente documenti e file fuori da documenti/ e output/. "
        "Mai dall'interfaccia web.",
    )


def run_dir_arg(value: str, config: Config, *, must_exist: bool = True) -> Path:
    """--run-dir: sempre una cartella dell'Archivio (sottocartella di output/)."""
    return confine_run_dir(value, config.output_dir, must_exist=must_exist)


def file_arg(value: str, config: Config, args: argparse.Namespace, field: str) -> Path:
    """Un file passato per percorso (copione, voci, bozza...): dentro output/,
    salvo --trusted-paths."""
    if getattr(args, "trusted_paths", False):
        return resolve_path(value)
    return confine_to(value, config.output_dir, field=field)


def documents_confined(args: argparse.Namespace) -> bool:
    return not getattr(args, "trusted_paths", False)


__all__ = [
    "PROJECT_ROOT",
    "EXIT_OK",
    "EXIT_DOMAIN",
    "EXIT_PROVIDER",
    "EXIT_NEEDS_USER",
    "NeedsUserInput",
    "INTERRUPTED",
    "add_config_arg",
    "bootstrap",
    "emit",
    "note",
    "run_tool",
    "resolve_path",
    "add_trusted_paths_arg",
    "run_dir_arg",
    "file_arg",
    "documents_confined",
]
