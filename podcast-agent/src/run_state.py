"""Archivio: lo stato di ogni episodio, DERIVATO dai file della run e dai lock.

Nessun campo "stato" salvato da qualche parte: un campo puo' andare fuori
sincrono (un processo morto lascerebbe "in generazione" per sempre), i file e
i lock no. Le regole, dalla prima che vale:

  1. lock di lavoro tenuto              -> "in_corso" (produzione: i/N dagli eventi)
  2. file finale coerente con la run    -> "pronto"
  3. file finale, copione approvato, nessuna battuta da generare, ma take o
     musiche cambiati dopo il montaggio -> "da_aggiornare" (si rimonta)
  4. approvato + voci.json + battute mancanti -> "interrotta"
  5. approvato                          -> "stima_e_consenso"
  6. copione (sessione)                 -> "copione_da_approvare"
  7. musiche.json                       -> "bozza_copione"
  8. episodio.json con il cast completo -> "bozza_musiche"
  9. altrimenti                         -> "bozza_cast" (o "bozza_documento")

Regge le run create prima dell'interfaccia web: senza episodio.json, senza
sessione, senza _montaggio.json.

Le battute mancanti si calcolano dalle chiavi dei segmenti, che dipendono
dall'impronta di sintesi del provider: se il provider non si puo' costruire
(chiave assente) il numero resta `null`, lo stato no.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .episode_config import EpisodeConfig
from .episode_info import final_file
from .production import EVENTS_FILENAME, load_montage, missing_turns
from .run_lock import current_work
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession
from .script_writer import PodcastScript
from .segment_cache import SCRIPT_FILENAME, VOICES_FILENAME

#: Etichetta, gruppo del filtro dell'Archivio, prossima azione e passo del
#: wireframe da cui si riprende.
STATES: dict[str, dict[str, Any]] = {
    "in_corso": {"etichetta": "In generazione", "gruppo": "in_produzione", "azione": "Segui l'avanzamento", "passo": 6},
    "pronto": {"etichetta": "Pronto", "gruppo": "pronti", "azione": "Ascolta", "passo": 7},
    "da_aggiornare": {"etichetta": "Da rimontare", "gruppo": "pronti", "azione": "Rimonta", "passo": 7},
    "interrotta": {"etichetta": "Interrotta", "gruppo": "in_produzione", "azione": "Riprendi", "passo": 6},
    "stima_e_consenso": {"etichetta": "Stima e consenso", "gruppo": "in_produzione", "azione": "Vai alla stima", "passo": 5},
    "copione_da_approvare": {"etichetta": "Copione da approvare", "gruppo": "da_approvare", "azione": "Rivedi e approva", "passo": 4},
    "bozza_copione": {"etichetta": "Bozza · Copione", "gruppo": "bozze", "azione": "Riprendi", "passo": 4},
    "bozza_musiche": {"etichetta": "Bozza · Musiche", "gruppo": "bozze", "azione": "Riprendi", "passo": 3},
    "bozza_cast": {"etichetta": "Bozza · Cast e formato", "gruppo": "bozze", "azione": "Riprendi", "passo": 2},
    "bozza_documento": {"etichetta": "Bozza · Documento", "gruppo": "bozze", "azione": "Riprendi", "passo": 1},
    "illeggibile": {"etichetta": "Illeggibile", "gruppo": "problemi", "azione": None, "passo": None},
}

GROUPS = ("bozze", "da_approvare", "in_produzione", "pronti")


def _load_script(run_dir: Path) -> PodcastScript | None:
    path = run_dir / SCRIPT_FILENAME
    if not path.is_file():
        return None
    return PodcastScript.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _session(run_dir: Path) -> ScriptSession | None:
    path = run_dir / SESSION_FILENAME
    return ScriptSession.load(path) if path.is_file() else None


def _last_progress(run_dir: Path) -> tuple[int | None, int | None]:
    """(battuta, totale) dall'ultimo evento 'battuta' di una produzione in corso."""
    path = run_dir / EVENTS_FILENAME
    if not path.is_file():
        return None, None
    done = total = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("evento") == "iniziata":
                total = event.get("totale")
            elif event.get("evento") == "battuta":
                done, total = event.get("n"), event.get("totale")
    except OSError:
        return None, None
    return done, total


def _updated_at(run_dir: Path) -> str | None:
    latest = 0.0
    for entry in run_dir.iterdir():
        if entry.name.startswith(".") or entry.name.endswith(".lock") or entry.is_symlink():
            continue
        try:
            latest = max(latest, entry.stat().st_mtime)
        except OSError:
            continue
    return datetime.fromtimestamp(latest).replace(microsecond=0).isoformat() if latest else None


def _music_chosen(run_dir: Path) -> bool:
    path = run_dir / "musiche.json"
    return path.is_file()


def _missing(run_dir: Path, script: PodcastScript, provider: Any) -> list[int] | None:
    voices = run_dir / VOICES_FILENAME
    if provider is None or not voices.is_file():
        return None
    try:
        voice_map = json.loads(voices.read_text(encoding="utf-8"))
        return missing_turns(run_dir, script, voice_map, provider)
    except Exception:  # noqa: BLE001 - un voci.json rotto non deve rompere l'Archivio
        return None


def run_state(run_dir: Path, config: Any, provider: Any = None) -> dict[str, Any]:
    """Lo stato derivato di una run, con cio' che l'Archivio mostra."""
    run_dir = Path(run_dir)
    episode = EpisodeConfig.load(run_dir)
    session = _session(run_dir)
    script = _load_script(run_dir)
    work = current_work(run_dir)
    extra: dict[str, Any] = {}

    approved = False
    if session is not None and script is not None:
        approved = session.approval_problem(script.to_dict()) is None

    final = final_file(run_dir, script) if script is not None else None
    if work:
        state = "in_corso"
        extra["operazione"] = work.get("operazione")
        if work.get("operazione") == "produzione":
            done, total = _last_progress(run_dir)
            extra["battuta"] = done
            extra["battute_totali"] = total
    elif final is not None and script is not None and _coherent(run_dir, script, approved):
        state = "pronto"
    else:
        missing = _missing(run_dir, script, provider) if (script is not None and approved) else None
        if final is not None and approved and missing == [] and (run_dir / VOICES_FILENAME).is_file():
            state = "da_aggiornare"
        elif approved and (run_dir / VOICES_FILENAME).is_file() and (missing is None or missing):
            state = "interrotta"
            extra["battute_mancanti"] = len(missing) if missing is not None else None
        elif approved:
            state = "stima_e_consenso"
        elif session is not None and script is not None:
            state = "copione_da_approvare"
        elif _music_chosen(run_dir):
            state = "bozza_copione"
        elif episode is not None and episode.cast_complete:
            state = "bozza_musiche"
        elif episode is not None and not episode.documento:
            state = "bozza_documento"
        else:
            state = "bozza_cast"

    title = (episode.titolo if episode and episode.titolo else None) or (script.titolo if script else None) or run_dir.name
    document = None
    if episode is not None and episode.documento:
        document = episode.documento.get("nome")
    elif session is not None and session.document_path:
        document = Path(session.document_path).name
    script_format = (episode.formato if episode else None) or (session.script_format if session else None)
    voices = len(script.speakers) if script is not None else (
        len([r for r in episode.roles_view() if r.get("voce")]) if episode is not None else 0
    )

    return {
        "run": run_dir.name,
        "titolo": title,
        "documento": document,
        "formato": script_format if (script is not None or (episode and episode.formato)) else None,
        "voci": voices or None,
        "stato": state,
        **STATES[state],
        **extra,
        "aggiornato_il": _updated_at(run_dir),
    }


def _coherent(run_dir: Path, script: PodcastScript, approved: bool) -> bool:
    """Il file finale corrisponde alla run di adesso (vedi src/episode_info.py).

    Run montate prima di C3 (nessun _montaggio.json): basta che il file ci sia.
    """
    record = load_montage(run_dir)
    if record is None:
        return True
    from .episode_info import _staleness  # import locale: evita un ciclo

    return approved and not _staleness(run_dir, record, script)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def list_runs(config: Any, provider: Any = None, *, group: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
    """Tutte le run dell'Archivio: le sottocartelle dirette di output/.

    Solo cartelle vere (niente link simbolici, niente nascoste): sono le stesse
    che run_dir_arg accetta. Una run che non si riesce a leggere compare come
    "illeggibile" invece di far fallire l'elenco intero.
    """
    root = Path(config.output_dir)
    if not root.is_dir():
        return []
    rows = []
    for entry in root.iterdir():
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            row = run_state(entry, config, provider)
        except Exception as exc:  # noqa: BLE001 - una run rotta non nasconde le altre
            row = {
                "run": entry.name,
                "titolo": entry.name,
                "stato": "illeggibile",
                **STATES["illeggibile"],
                "errore": type(exc).__name__,
                "aggiornato_il": _updated_at(entry),
            }
        rows.append(row)
    if group:
        rows = [r for r in rows if r["gruppo"] == group]
    if search:
        needle = _normalize(search.strip())
        rows = [r for r in rows if needle in _normalize(r.get("titolo") or "") or needle in _normalize(r.get("documento") or "")]
    rows.sort(key=lambda r: r.get("aggiornato_il") or "", reverse=True)
    return rows


__all__ = ["GROUPS", "STATES", "list_runs", "run_state"]
