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
import os
import re
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
from src.errors import DomainError, NeedsUserDecision, error_details  # noqa: E402
from src.ffmpeg_tool import FfmpegNotFoundError  # noqa: E402
from src.lessons import LessonsError  # noqa: E402
from src.music_library import MusicLibraryError  # noqa: E402
from src.music_mixer import MusicChangedError, MusicMixerError  # noqa: E402
from src.paths import confine_run_dir, confine_to  # noqa: E402
from src.redaction import redact as _redact  # noqa: E402
from src.redaction import register_roots as _register_roots  # noqa: E402
from src.web_mode import WEB_MODE_ENV, web_mode  # noqa: E402,F401 - riesportati per i tool
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


# ----------------------------------------------------------------------
# Modalita' web
# ----------------------------------------------------------------------

#: Argomenti che in modalita' web vengono rifiutati, qualunque tool li
#: riceva. Sono quelli che la revisione di sicurezza C0+C1 ha elencato come
#: "da non esporre mai all'utente web":
#:   --config            script.client_factory importa ed esegue un modulo qualsiasi;
#:   --trusted-paths     unico modo di uscire da documenti/ e output/;
#:   --no-approval-gate  salta il gate sullo sha256 del copione approvato;
#:   --no-consent-gate   salta il consenso prima della clonazione (C2);
#:   --save-to, --text-file, --session, --script-json, --script, --out,
#:   --voices, --audio-paths
#:                       file passati per percorso: l'adattatore lavora per
#:                       --run-dir, e i nomi dei file li fissa il tool. Sono
#:                       confinati a output/, non alla run: da fuori si
#:                       potrebbe scrivere nei file di un'altra run.
WEB_FORBIDDEN_ARGS = (
    "--config",
    "--trusted-paths",
    "--no-approval-gate",
    "--no-consent-gate",
    "--save-to",
    "--text-file",
    "--session",
    "--script-json",
    "--script",
    "--out",
    "--voices",
    "--audio-paths",
)


class NotAllowedInWebMode(DomainError):
    """Un argomento riservato all'uso locale, ricevuto in modalita' web (exit 1)."""


class InvalidArguments(DomainError):
    """Argomenti non validi per argparse (mancanti, sconosciuti, malformati).

    Senza, argparse esce con 2 e stdout vuoto: per il contratto 2 e' "errore
    del provider, nessun Riprova", e manca il JSON. Caso tipico: un valore che
    comincia con un trattino (`--text "- Si', certo"`), che argparse prende
    per un'opzione; la forma `--text=- Si', certo` lo evita.
    """


def parse_args(
    parser: argparse.ArgumentParser, *, web_forbidden: tuple[str, ...] = (), web_allowed: bool = True
) -> argparse.Namespace:
    """parser.parse_args() + il controllo della modalita' web.

    `web_forbidden`: argomenti da rifiutare in modalita' web solo in QUESTO
    tool, oltre a WEB_FORBIDDEN_ARGS (es. --document di script_write.py).
    `web_allowed=False`: il tool intero non si usa dall'interfaccia (es.
    voices_prepare.py: le voci le prepara produce.py, legato al copione
    approvato).

    Il controllo si fa due volte:
      - PRIMA di argparse, sul testo della riga di comando (revisione finale,
        N19): un argomento vietato o un tool non ammesso danno
        NotAllowedInWebMode anche se mancano argomenti obbligatori, invece di
        un InvalidArguments che dipende dall'ordine dei controlli. Valgono il
        nome esatto, la forma `--opzione=valore` e le abbreviazioni delle
        opzioni del tool (`--trusted`);
      - DOPO, sui valori gia' interpretati da argparse (un argomento conta come
        usato se il suo valore e' diverso dal default): la rete di sicurezza.
    """
    def _error(message: str) -> None:
        raise InvalidArguments(
            f"Argomenti non validi: {message}. Un valore che comincia con '-' si passa "
            "nella forma --opzione=valore.",
            details={"messaggio": message},
        )

    parser.error = _error  # type: ignore[method-assign]
    if web_mode():
        _web_precheck(parser, set(WEB_FORBIDDEN_ARGS) | set(web_forbidden), web_allowed)
    args = parser.parse_args()
    if web_mode():
        forbidden = set(WEB_FORBIDDEN_ARGS) | set(web_forbidden)
        used = []
        for action in parser._actions:  # noqa: SLF001 - API stabile di fatto
            names = [name for name in action.option_strings if name in forbidden]
            if names and getattr(args, action.dest, None) != action.default:
                used.append(names[0])
        if used:
            raise NotAllowedInWebMode(
                "Argomenti riservati all'uso locale, non ammessi dall'interfaccia web: "
                + ", ".join(used)
                + ".",
                details={"argomenti": used},
            )
        if not web_allowed:
            command = Path(sys.argv[0]).name
            raise NotAllowedInWebMode(
                f"{command} non si usa dall'interfaccia web.",
                details={"argomenti": [], "comando": command},
            )
    args._web_checked = True  # noqa: SLF001 - vedi bootstrap()
    return args


def _web_precheck(parser: argparse.ArgumentParser, forbidden: set[str], web_allowed: bool) -> None:
    """Il controllo della modalita' web sul testo della riga di comando (vedi parse_args)."""
    defined = {name for action in parser._actions for name in action.option_strings if name.startswith("--")}  # noqa: SLF001
    used: list[str] = []
    for token in sys.argv[1:]:
        if token == "--":
            break
        if not token.startswith("--"):
            continue
        name = token.split("=", 1)[0]
        if name in defined or name in forbidden:
            hit = name if name in forbidden else None
        else:
            # Abbreviazione: vietata se puo' indicare un'opzione vietata.
            hit = next((d for d in sorted(defined) if d.startswith(name) and d in forbidden), None)
        if hit and hit not in used:
            used.append(hit)
    if used:
        raise NotAllowedInWebMode(
            "Argomenti riservati all'uso locale, non ammessi dall'interfaccia web: " + ", ".join(used) + ".",
            details={"argomenti": used},
        )
    if not web_allowed:
        command = Path(sys.argv[0]).name
        raise NotAllowedInWebMode(
            f"{command} non si usa dall'interfaccia web.",
            details={"argomenti": [], "comando": command},
        )


def require_by_in_web(by: str | None, what: str) -> None:
    """Dall'interfaccia le azioni che lasciano una firma vogliono --by.

    Approvazioni, ritiri, ripristini, modifiche, stesure e retake registrano
    chi li ha fatti ("Approvato da ..."): l'adattatore sa sempre chi e'
    l'utente collegato, quindi un --by mancante e' un suo errore
    (InvalidArguments, `details.campo = "by"`). Revisione finale, N27.
    """
    if web_mode() and not (by and by.strip()):
        raise InvalidArguments(
            f"Dall'interfaccia {what} richiede --by: chi la fa (l'utente collegato).",
            details={"campo": "by", "messaggio": "manca --by"},
        )


def bootstrap(args: argparse.Namespace) -> Config:
    """Carica .env e config.yaml. Le API key NON vengono mai stampate.

    In modalita' web si rifiuta di partire se gli argomenti non sono passati
    da parse_args(): un tool nuovo che dimentica il controllo deve fallire,
    non accettare in silenzio --trusted-paths.
    """
    if web_mode() and not getattr(args, "_web_checked", False):
        raise NotAllowedInWebMode(
            "Questo comando non controlla gli argomenti della modalita' web "
            "(tools/_common.parse_args): non e' utilizzabile dall'interfaccia.",
            details={"argomenti": []},
        )
    load_dotenv()
    config = load_config(getattr(args, "config", None))
    _register_roots(config)
    return config


def emit(payload: dict[str, Any]) -> None:
    """Scrive la risposta JSON su stdout."""
    data = {"ok": True, **payload}
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def note(message: str) -> None:
    """Messaggio per l'umano: va su stderr, non inquina il JSON.

    Passa da _redact(): stderr si puo' mostrare in un pannello "dettagli"
    della UI, quindi niente percorsi assoluti del server.
    """
    print(_redact(message), file=sys.stderr, flush=True)


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
    payload: dict[str, Any] = {"ok": False, "error_type": kind, "error": _redact(message), "exit_code": code}
    if details:
        payload["details"] = _redact(details)
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
    "WEB_MODE_ENV",
    "require_by_in_web",
    "WEB_FORBIDDEN_ARGS",
    "NotAllowedInWebMode",
    "InvalidArguments",
    "web_mode",
    "parse_args",
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
