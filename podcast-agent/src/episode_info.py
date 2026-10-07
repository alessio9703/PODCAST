"""Passo 7 del wireframe: tutto quello che lo schermo "Episodio" mostra.

Intestazione (titolo, formato, durata, voci, data di montaggio), barra delle
sezioni del file finale (intro, parlato, outro), trascrizione con il punto
esatto di ogni battuta NEL FILE FINALE, e i file della run.

Lo stato si DERIVA, non si legge da un campo:

  - "in_corso":      il lock di lavoro della run e' tenuto (produzione,
                     montaggio, retake...);
  - "non_prodotto":  nessun file finale;
  - "pronto":        il file finale c'e' ed e' coerente con quello che c'e'
                     adesso nella run;
  - "da_aggiornare": il file c'e', ma dopo il montaggio sono cambiati il
                     copione, i take scelti o le musiche (`motivi`).

La coerenza si giudica confrontando _montaggio.json (scritto dal montaggio
insieme al file) con lo stato attuale: sha256 del copione, scelte dei take,
musiche scelte. Le run montate prima di C3 non hanno quel file: se il file
finale c'e' risultano "pronto", con `verificabile: false`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio_assembler import load_choices, slugify
from .episode_config import EpisodeConfig
from .episode_timeline import TimelineError, attach_texts, load_timeline
from .music_library import load_music_choice
from .music_mixer import MUSICHE_USATE_FILENAME
from .production import load_montage, load_run_script, ui_path
from .run_lock import current_work
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession, script_hash
from .script_view import _add_presenter_name, cast_view, session_cast, structured_turns

#: File della run che non sono "file dell'episodio" per l'utente.
_HIDDEN_SUFFIXES = (".lock", ".tmp")
#: File che restano sul server: _lavoro.json ha il pid del processo (C0+C1,
#: BASSA-2); gli altri hanno percorsi assoluti delle registrazioni o i
#: voice_id del provider (C3, BASSA-3).
_HIDDEN_NAMES = (
    "_lavoro.json", "_audio_paths.json", "voci.json", "voci_usate.json",
    # con percorsi assoluti del server (revisione di sicurezza C5, BASSA-1)
    "episodio.json", "musiche_usate.json",
)
#: Cache della forma d'onda (C3, BASSA-5) e riferimenti a percorsi del server.
_HIDDEN_PREFIXES = ("_forma_onda_",)
_HIDDEN_NAME_SUFFIXES = (".percorso.txt",)


def final_file(run_dir: Path, script: Any) -> Path | None:
    """Il file finale dell'episodio, se esiste."""
    record = load_montage(run_dir)
    if record and record.get("file"):
        candidate = Path(run_dir) / Path(str(record["file"])).name
        return candidate if candidate.is_file() else None
    # Senza registro: il nome dal titolo del copione (run di prima) o da quello
    # dell'episodio (G7, se il registro e' andato perso).
    stems = [slugify(script.titolo)]
    episode = EpisodeConfig.load(run_dir)
    if episode is not None and episode.titolo:
        stems.append(slugify(episode.titolo))
    for stem in stems:
        for ext in (".mp3", ".wav"):
            candidate = Path(run_dir) / f"{stem}{ext}"
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
    return None


def _session(run_dir: Path) -> ScriptSession | None:
    path = Path(run_dir) / SESSION_FILENAME
    if not path.is_file():
        return None
    return ScriptSession.load(path)


def _music_names_now(run_dir: Path) -> dict[str, str | None]:
    try:
        choice = load_music_choice(run_dir)
    except Exception:  # noqa: BLE001 - un musiche.json rotto non deve impedire di vedere l'episodio
        return {}
    return {category: choice.get(category) for category in ("intro", "sottofondo", "outro")}


def _staleness(run_dir: Path, record: dict[str, Any], script: Any) -> list[str]:
    """Cosa e' cambiato dopo il montaggio: copione, take, musiche."""
    reasons = []
    if record.get("copione_sha256") != script_hash(script.to_dict()):
        reasons.append("copione")
    if record.get("scelte_take") is not None and record.get("scelte_take") != load_choices(Path(run_dir) / "segmenti"):
        reasons.append("take")
    montage_music = {
        category: (entry or {}).get("nome") for category, entry in (record.get("musiche") or {}).items()
    }
    now = {category: name for category, name in _music_names_now(run_dir).items() if name}
    if {k: v for k, v in montage_music.items() if v} != now:
        reasons.append("musiche")
    return reasons


def _legacy_sections(run_dir: Path, duration: float | None) -> tuple[list[dict[str, Any]], float]:
    """Sezioni di una run montata prima di C3, da musiche_usate.json se c'e'."""
    target = Path(run_dir) / MUSICHE_USATE_FILENAME
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            offset = float(raw.get("voice_offset_seconds") or 0.0)
            voice = float(raw.get("voice_duration_seconds") or 0.0)
            return [{"tipo": "parlato", "inizio": offset, "fine": round(offset + voice, 3)}], offset
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return [{"tipo": "parlato", "inizio": 0.0, "fine": duration}], 0.0


def _run_files(run_dir: Path, config: Any) -> list[dict[str, Any]]:
    files = []
    for entry in sorted(Path(run_dir).iterdir()):
        if (
            entry.is_symlink()
            or not entry.is_file()
            or entry.name.startswith(".")
            or entry.name.endswith(_HIDDEN_SUFFIXES + _HIDDEN_NAME_SUFFIXES)
            or entry.name.startswith(_HIDDEN_PREFIXES)
            or entry.name in _HIDDEN_NAMES
        ):
            continue
        stat = entry.stat()
        files.append(
            {
                "nome": entry.name,
                "percorso": ui_path(entry, config),
                "dimensione_bytes": stat.st_size,
                "modificato_il": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
                # I file con "_" davanti sono di lavoro (sessione, tempi,
                # cache): la UI puo' tenerli in un elenco a parte.
                "interno": entry.name.startswith("_"),
            }
        )
    return files


def _label(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}:{rest:02d}"


def episode_info(run_dir: Path, config: Any) -> dict[str, Any]:
    run_dir = Path(run_dir)
    script = load_run_script(run_dir)
    episode = EpisodeConfig.load(run_dir)
    session = _session(run_dir)
    record = load_montage(run_dir)
    final = final_file(run_dir, script)
    in_progress = current_work(run_dir)

    # -- stato derivato -------------------------------------------------------
    reasons: list[str] = []
    if in_progress:
        state = "in_corso"
    elif final is None:
        state = "non_prodotto"
    else:
        reasons = _staleness(run_dir, record, script) if record else []
        # Un file coerente con un copione NON approvato non e' "pronto"
        # (revisione di sicurezza C3, MEDIA-1).
        if session is not None and session.approval_problem(script.to_dict()) is not None:
            reasons.append("non_approvato")
        state = "da_aggiornare" if reasons else "pronto"

    # -- chi parla ------------------------------------------------------------
    speakers = list(script.speakers)
    if session is not None:
        cast = session_cast(session, speakers)
        _add_presenter_name(cast, session, run_dir)
        script_format = session.script_format
    else:
        cast = cast_view(script_format=None, interviewer=None, presenter=None, participants=None, speakers=speakers)
        script_format = None
    if episode is not None and episode.formato:
        script_format = episode.formato
    voices = [
        {
            "speaker": speaker,
            "ruolo": (cast.get(speaker) or {}).get("ruolo"),
            "nome_pronunciato": (cast.get(speaker) or {}).get("nome_pronunciato"),
        }
        for speaker in speakers
    ]

    # -- tempi nel file finale -----------------------------------------------
    duration = (record or {}).get("duration_seconds") if record else None
    if record:
        sections = [dict(s) for s in record.get("sezioni") or []]
        offset = float(record.get("voice_offset_seconds") or 0.0)
    else:
        sections, offset = _legacy_sections(run_dir, duration)
    for section in sections:
        if section.get("tipo") == "parlato":
            section["battute"] = len(script.turni)

    turns = structured_turns(script.turni, cast)
    timing_note = None
    if final is not None:
        try:
            timeline = attach_texts(load_timeline(run_dir), [t["testo"] for t in script.turni])
        except TimelineError as exc:
            timeline = None
            timing_note = {
                "codice": exc.code,
                "messaggio": (
                    "Tempi non disponibili: il file e' stato montato da un copione diverso. "
                    "Produci di nuovo l'episodio."
                    if exc.code == "timeline_diversa"
                    else "Tempi non disponibili: rimonta l'episodio."
                ),
            }
    else:
        timeline = None
    for turn, timed in zip(turns, timeline or [{} for _ in turns]):
        start = timed.get("inizio")
        end = timed.get("fine")
        turn["inizio_finale"] = round(float(start) + offset, 3) if start is not None else None
        turn["fine_finale"] = round(float(end) + offset, 3) if end is not None else None
        turn["inizio_label"] = _label(turn["inizio_finale"])
        turn.pop("parti", None)

    final_view = None
    if final is not None:
        final_view = {
            "nome": final.name,
            "percorso": ui_path(final, config),
            "formato": final.suffix.lstrip("."),
            "dimensione_bytes": final.stat().st_size,
        }
    montato_il = (record or {}).get("montato_il")
    if montato_il is None and final is not None:
        montato_il = datetime.fromtimestamp(final.stat().st_mtime).replace(microsecond=0).isoformat()

    approved = None
    if session is not None:
        approved = session.approval_problem(script.to_dict()) is None

    return {
        "run": run_dir.name,
        "titolo": (episode.titolo if episode and episode.titolo else None) or script.titolo,
        "stato": state,
        "motivi": reasons,
        "verificabile": record is not None,
        "in_corso": in_progress,
        "copione_approvato": approved,
        "formato": script_format,
        "durata_seconds": duration,
        "durata_label": _label(duration),
        "voci": voices,
        "numero_voci": len(voices),
        "montato_il": montato_il,
        "file_finale": final_view,
        "sezioni": sections,
        "trascrizione": turns,
        "tempi_non_disponibili": timing_note,
        "file": _run_files(run_dir, config),
    }


__all__ = ["episode_info", "final_file"]
