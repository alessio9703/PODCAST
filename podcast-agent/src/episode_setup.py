"""Passi 1-2 del wireframe: applicare le scelte dell'utente a episodio.json.

src/episode_config.py sa COSA e' valido; qui si collega quel modello al resto
del progetto: le voci si risolvono contro voci_disponibili/ (senza indovinare:
un nome incerto e' un AmbiguousName, exit 3), il documento si estrae davvero
(una scansione e' un DocumentNotExtractable, exit 1), e la vista restituita
alla UI dice per ogni ruolo se la voce e' gia' clonata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .episode_config import (
    EXTRACTED_TEXT_FILENAME,
    ROLE_LABELS,
    EpisodeConfig,
    document_record,
    locked_update,
)
from .errors import AmbiguousName
from .fileio import write_text_atomic
from .music_context import music_durations
from .pdf_reader import Document, extract_text
from .registry import VoiceRegistry
from .run_lock import session_lock
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession
from .voice_library import VoiceLibrary, VoiceLibraryError, spoken_name_guess
from .voice_meta import consent_view


def resolve_voice(library: VoiceLibrary, query: str, role: str) -> str:
    """Il nome ESATTO della voce, oppure un errore che chiede di scegliere."""
    match, candidates = library.resolve(query)
    if match is not None and match.name == query.strip():
        return match.name
    if match is not None and not query.strip().isdigit():
        # Corrispondenza esatta a meno di maiuscole/accenti: e' la stessa voce.
        return match.name
    if candidates:
        raise AmbiguousName(
            f"'{query}' ({ROLE_LABELS.get(role, role)}) non e' un nome certo: candidati "
            + ", ".join(c.name for c in candidates)
            + ". Scegli quello giusto: le voci sono persone reali e non si indovinano.",
            details={"campo": "voce", "ruolo": role, "query": query, "candidates": [c.name for c in candidates]},
        )
    raise VoiceLibraryError(
        f"'{query}' non corrisponde a nessuna voce in {library.dir.name}/. "
        f"Disponibili: {', '.join(library.names) or 'nessuna'}."
    )


def load_document(document_arg: str, config: Any, *, trusted_paths: bool = False) -> Document:
    """Estrae il documento, con i limiti di config.yaml (`documents:`).

    Il documento deve stare nella libreria documenti/ (src/pdf_reader,
    modalita' confinata), salvo `trusted_paths` per l'uso locale. Si chiama
    FUORI dal lock di episodio.json: l'estrazione di un PDF lungo non deve
    far aspettare chi lavora sulla stessa bozza.
    """
    return extract_text(
        document_arg,
        search_dir=config.documents_dir,
        confined=not trusted_paths,
        limits=config.document_limits,
    )


def set_document(episode: EpisodeConfig, run_dir: Path, record: dict[str, Any], text: str) -> dict[str, Any]:
    """Registra un documento GIA' estratto e ne salva il testo nella run.

    `record` e' document_record(document), calcolato anch'esso fuori dal lock
    (legge il file per lo sha256): qui dentro si scrive soltanto.
    """
    episode.documento = record
    write_text_atomic(Path(run_dir) / EXTRACTED_TEXT_FILENAME, text)
    return episode.documento


def _cast_snapshot(episode: EpisodeConfig) -> tuple:
    """Cio' che, se cambia, rende vecchia un'approvazione: formato e ruoli.

    Titolo e durata NO: non cambiano chi parla ne' come viene presentato.
    """
    return (
        episode.formato,
        tuple(
            sorted(
                (role, data.get("voce"), data.get("nome_pronunciato"), data.get("descrizione"))
                for role, data in episode.ruoli.items()
            )
        ),
    )


def revoke_approval_if_any(run_dir: Path) -> dict[str, Any] | None:
    """Ritira l'approvazione del copione, se c'e'. Ritorna quella ritirata."""
    session_path = Path(run_dir) / SESSION_FILENAME
    if not session_path.is_file():
        return None
    with session_lock(run_dir):
        session = ScriptSession.load(session_path)
        if not session.approved:
            return None
        revoked = session.approved
        session.approved = None
        session.save()
    return revoked


def apply_changes(
    run_dir: Path,
    config: Any,
    *,
    title: str | None = None,
    document: str | Document | None = None,
    script_format: str | None = None,
    minutes: int | None = None,
    voices: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
    descriptions: dict[str, str] | None = None,
    remove: list[str] | None = None,
    accept_incomplete_names: bool = False,
    trusted_paths: bool = False,
) -> tuple[EpisodeConfig, list[str], dict[str, Any] | None]:
    """Applica le modifiche sotto lock, tutte o nessuna.

    Ritorna (config, avvisi, approvazione ritirata o None). Se cambiano
    formato o ruoli (voce, nome pronunciato, descrizione) e il copione era
    approvato, l'approvazione viene ritirata: il copione e' stato approvato
    per un altro cast, anche se il suo testo (e quindi lo sha256) e' rimasto
    uguale. Titolo e durata non la toccano.

    L'ordine conta ed e' quello dello schermo: formato prima dei ruoli (i ruoli
    ammessi dipendono dal formato), voce prima del nome (assegnare una voce
    nuova azzera il nome della persona precedente).

    `document` e' il nome nella libreria (si estrae qui) oppure un Document
    gia' estratto da chi chiama (run_init.py, per non estrarlo due volte).
    In entrambi i casi l'estrazione avviene PRIMA di prendere il lock.
    """
    run_dir = Path(run_dir)
    warnings: list[str] = []
    library = None
    if voices:
        library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
        library.scan()
    document_data = None
    if document is not None:
        if not isinstance(document, Document):
            document = load_document(document, config, trusted_paths=trusted_paths)
        document_data = (document_record(document), document.text)

    with locked_update(run_dir):
        episode = EpisodeConfig.load(run_dir) or EpisodeConfig()
        before = _cast_snapshot(episode)
        if title is not None:
            episode.set_title(title)
        if document_data is not None:
            set_document(episode, run_dir, *document_data)
        if script_format is not None:
            warnings.extend(episode.set_format(script_format))
        if minutes is not None:
            episode.set_duration(minutes)
        for role in remove or []:
            episode.remove_role(role)
        for role, query in (voices or {}).items():
            episode.assign_voice(role, resolve_voice(library, query, role))  # type: ignore[arg-type]
        for role, name in (names or {}).items():
            episode.set_spoken_name(role, name, accept_incomplete=accept_incomplete_names)
        for role, text in (descriptions or {}).items():
            episode.set_description(role, text)
        episode.save(run_dir)
        cast_changed = _cast_snapshot(episode) != before

    revoked = revoke_approval_if_any(run_dir) if cast_changed else None
    if revoked:
        warnings.append(
            f"Il cast e' cambiato dopo l'approvazione della versione {revoked.get('version')}: "
            "l'approvazione e' stata ritirata, il copione va riapprovato."
        )
    warnings.extend(_script_mismatch(run_dir, episode))
    return episode, warnings, revoked


def _script_mismatch(run_dir: Path, episode: EpisodeConfig) -> list[str]:
    """Avviso se esiste gia' un copione scritto con un cast diverso."""
    session_path = run_dir / SESSION_FILENAME
    if not session_path.is_file() or not episode.cast_complete:
        return []
    try:
        session = ScriptSession.load(session_path)
    except Exception:  # noqa: BLE001 - e' solo un avviso
        return []
    if session.current_script is None:
        return []
    args = episode.script_arguments() if episode.documento else None
    if not args:
        return []
    same = (
        sorted(session.speakers) == sorted(args["speakers"])
        and session.script_format == args["script_format"]
        and session.interviewer == args["interviewer"]
        and session.presenter == args["presenter"]
        and sorted(map(str, session.participants)) == sorted(map(str, args["participants"]))
    )
    if same:
        return []
    return [
        "Esiste gia' un copione scritto con un cast diverso da questo: per usare "
        "il cast nuovo va riscritto (tools/script_write.py)."
    ]


def episode_view(run_dir: Path, config: Any, episode: EpisodeConfig | None = None) -> dict[str, Any]:
    """Tutto quello che i passi 1-3 mostrano, derivato al momento."""
    run_dir = Path(run_dir)
    if episode is None:
        episode = EpisodeConfig.load(run_dir) or EpisodeConfig()

    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    try:
        library.scan()
        by_name = {v.name: v for v in library.voices}
    except VoiceLibraryError:
        by_name = {}

    roles = []
    for row in episode.roles_view():
        voice = by_name.get(row["voce"]) if row["voce"] else None
        cloned = None
        if voice is not None:
            voice_id, _ = registry.lookup(voice.name, voice.path, config.active_provider)
            cloned = voice_id is not None
        proposed = spoken_name_guess(row["voce"]) if row["voce"] else None
        roles.append(
            {
                **row,
                "voce_presente": voice is not None if row["voce"] else None,
                "clonata": cloned,
                # Stato del consenso alla clonazione (src/voice_meta.py):
                # registrato, mancante, superato, revocato. None = nessuna voce.
                "consenso": consent_view(voice.path) if voice is not None else None,
                # Solo una PROPOSTA ricavata dal nome del file, mai salvata da
                # sola: il nome lo conferma l'utente (name_complete=false vuol
                # dire che manca nome o cognome e va chiesto).
                "nome_proposto": proposed[0] if proposed else None,
                "nome_proposto_completo": proposed[1] if proposed else None,
            }
        )

    music = music_durations(run_dir, config)
    music.pop("_note_parts", None)
    extra = float(music.get("music_extra_seconds", 0.0) or 0.0)
    target_seconds = (episode.durata_minuti or 0) * 60

    return {
        "run": run_dir.name,
        "run_dir": str(run_dir),
        "episodio_file": str(EpisodeConfig.path_for(run_dir)),
        "titolo": episode.titolo,
        "documento": episode.documento,
        "formato": episode.formato,
        "durata_minuti": episode.durata_minuti,
        "ruoli": roles,
        "speakers": episode.speakers,
        "presenter": episode.presenter,
        "interviewer": episode.interviewer,
        "participants": episode.participants,
        "mancanti": episode.missing(),
        "cast_completo": episode.cast_complete,
        # Struttura del passo 3: parlato (durata obiettivo) + intro/outro misurate.
        "durata_indicativa": {
            "parlato_seconds": target_seconds or None,
            **music,
            "totale_seconds": round(target_seconds + extra, 1) if target_seconds else None,
        },
    }


__all__ = [
    "apply_changes",
    "episode_view",
    "load_document",
    "resolve_voice",
    "revoke_approval_if_any",
    "set_document",
]
