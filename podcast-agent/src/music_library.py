"""Scansione di musiche/ e risoluzione dei nomi di intro, outro e sottofondo.

Sulla falsariga di voice_library.py, con una differenza di fondo: la cartella
delle voci e' obbligatoria (senza non si puo' fare un episodio), quella delle
musiche no. Se `musiche/` o una sua sottocartella non esiste, la funzione
corrispondente e' semplicemente non disponibile — non e' un errore, e
`scan()` non solleva mai per questo motivo.

I nomi si risolvono con la STESSA regola delle voci: nessuna corrispondenza
parziale indovinata, solo candidati (vedi voice_library.py per il perche').
"""

from __future__ import annotations

import difflib
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ffmpeg_tool import find_ffmpeg, media_duration_seconds
from .fileio import write_json_atomic
from .registry import file_hash
from .web_mode import web_mode

CATEGORIES = ("intro", "outro", "sottofondo")


class MusicLibraryError(RuntimeError):
    """Problema nella cartella delle musiche o nei suoi metadati."""


def _normalize(value: str) -> str:
    """Minuscole, senza accenti e senza spazi multipli: solo per il matching.

    Stessa funzione di voice_library._normalize(): duplicata invece di
    importata perche' sono due cataloghi concettualmente indipendenti (voci
    di persone contro brani musicali) che per ora condividono solo la
    tecnica di confronto, non lo stato.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())


@dataclass(frozen=True)
class TrackMetadata:
    licenza: str | None = None
    parlato: bool = False
    trascrizione: str | None = None


@dataclass(frozen=True)
class MusicTrack:
    category: str
    name: str
    path: Path
    metadata: TrackMetadata
    #: None se i metadati sono validi. Altrimenti il motivo per cui questo
    #: brano non e' utilizzabile (es. sottofondo con parlato: true, oppure
    #: il .json accanto al file non e' leggibile). Compare comunque nella
    #: lista di music_list.py — si vede, non sparisce in silenzio — ma
    #: resolve()/require_usable() lo bloccano.
    metadata_error: str | None = None

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size

    def sha256(self) -> str:
        return file_hash(self.path)

    def readability_error(self) -> str | None:
        """Il motivo per cui il file non si legge come dice la sua estensione, o None.

        ffmpeg apre ogni file con il formato forzato dall'estensione (C6): un AAC
        chiamato .mp3, o un .wav che contiene un mp3, non si legge. Meglio dirlo
        negli elenchi e prima di spendere che con un errore di ffmpeg al mix
        (revisione di sicurezza C6, BASSA-1). Senza ffmpeg non si giudica.
        """
        if self.extension == ".wav" or find_ffmpeg() is None:
            return None
        if media_duration_seconds(self.path) is None:
            return (
                f"il file non si legge come {self.extension}: l'estensione non corrisponde al "
                "contenuto, oppure il file e' rovinato. Ricaricalo nel formato giusto."
            )
        return None

    def duration_seconds(self) -> float | None:
        """Durata del brano. None se non misurabile.

        I .wav si misurano senza dipendenze; gli altri formati con ffprobe o,
        nell'installazione standard, con l'ffmpeg di imageio-ffmpeg (vedi
        src/ffmpeg_tool.media_duration_seconds). Una durata sconosciuta non
        blocca l'elenco: la si conoscera' al momento del mix.
        """
        return media_duration_seconds(self.path)


def _load_metadata(track_path: Path, category: str) -> tuple[TrackMetadata, str | None]:
    """Legge <nome>.json accanto al brano. Assente = metadati vuoti, non errore."""
    meta_path = track_path.with_suffix(".json")
    if meta_path.is_symlink():
        return TrackMetadata(), f"{meta_path.name} e' un link simbolico: non lo leggo."
    if not meta_path.is_file():
        return TrackMetadata(), None

    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return TrackMetadata(), f"{meta_path.name} non e' un JSON leggibile: {exc}"
    if not isinstance(raw, dict):
        return TrackMetadata(), f"{meta_path.name} deve contenere un oggetto JSON."

    licenza = raw.get("licenza")
    if licenza is not None and not isinstance(licenza, str):
        return TrackMetadata(), f"{meta_path.name}: 'licenza' deve essere una stringa."

    parlato = raw.get("parlato", False)
    if not isinstance(parlato, bool):
        return TrackMetadata(), f"{meta_path.name}: 'parlato' deve essere true/false."

    trascrizione = raw.get("trascrizione")
    if trascrizione is not None and not isinstance(trascrizione, str):
        return TrackMetadata(), f"{meta_path.name}: 'trascrizione' deve essere una stringa."

    metadata = TrackMetadata(
        licenza=licenza, parlato=parlato, trascrizione=trascrizione
    )

    # Errore di dominio specifico del sottofondo: deve essere solo musica.
    # Si rileva qui, non a monte, cosi' il brano resta visibile in elenco
    # con il motivo scritto sopra, invece di sparire.
    if category == "sottofondo" and parlato:
        return metadata, (
            "il sottofondo ha 'parlato: true' nei metadati: il sottofondo deve "
            "essere solo musica. Correggi il file JSON, oppure usa questo brano "
            "come intro o outro."
        )

    return metadata, None


class MusicLibrary:
    def __init__(self, music_dir: Path, accepted_extensions: list[str]) -> None:
        self.dir = Path(music_dir)
        self.accepted_extensions = [e.lower() for e in accepted_extensions]
        self._tracks: dict[str, list[MusicTrack]] = {c: [] for c in CATEGORIES}
        self._rejected: dict[str, list[Path]] = {c: [] for c in CATEGORIES}

    # -- scansione ----------------------------------------------------------

    def scan(self) -> dict[str, list[MusicTrack]]:
        """Scansiona le tre sottocartelle. Assenti = categoria vuota, non errore."""
        for category in CATEGORIES:
            tracks: list[MusicTrack] = []
            rejected: list[Path] = []
            category_dir = self.dir / category
            if category_dir.is_symlink():
                # Una cartella di categoria collegata altrove non si legge
                # (sicurezza C7, BASSA-3): i brani starebbero fuori da musiche/.
                rejected.append(category_dir)
            elif category_dir.is_dir():
                for entry in sorted(category_dir.iterdir()):
                    if entry.name.startswith("."):
                        continue
                    if entry.is_symlink():
                        # Come nella libreria Voci: un link non si segue,
                        # finisce fra i file scartati (revisione finale, N4).
                        rejected.append(entry)
                        continue
                    if not entry.is_file():
                        continue
                    if entry.suffix.lower() in (".json", ".lock", ".tmp"):
                        continue  # metadati accanto al brano (e loro lock), non un brano
                    if entry.suffix.lower() not in self.accepted_extensions:
                        rejected.append(entry)
                        continue
                    metadata, error = _load_metadata(entry, category)
                    tracks.append(
                        MusicTrack(
                            category=category,
                            name=entry.stem.strip(),
                            path=entry,
                            metadata=metadata,
                            metadata_error=error,
                        )
                    )
            # Due file con lo stesso nome (a meno dell'estensione) si leggerebbero
            # lo stesso <nome>.json e sarebbero ambigui: nessuno dei due si usa
            # finche' uno non viene tolto (revisione di sicurezza C5, MEDIA-1).
            groups: dict[str, list[MusicTrack]] = {}
            for track in tracks:
                groups.setdefault(_normalize(track.name), []).append(track)
            for group in groups.values():
                if len(group) > 1:
                    files = ", ".join(t.path.name for t in group)
                    for track in group:
                        index = tracks.index(track)
                        tracks[index] = MusicTrack(
                            category=track.category,
                            name=track.name,
                            path=track.path,
                            metadata=track.metadata,
                            metadata_error=f"piu' brani con lo stesso nome in {category}/ ({files}): tienine uno solo.",
                        )
            self._tracks[category] = tracks
            self._rejected[category] = rejected
        return dict(self._tracks)

    def available(self) -> bool:
        """True se ALMENO UNA delle tre categorie ha almeno un brano."""
        return any(self._tracks[c] for c in CATEGORIES)

    def category_available(self, category: str) -> bool:
        self._check_category(category)
        return bool(self._tracks[category])

    def tracks(self, category: str) -> list[MusicTrack]:
        self._check_category(category)
        return list(self._tracks[category])

    def rejected_files(self, category: str) -> list[Path]:
        self._check_category(category)
        return list(self._rejected[category])

    @staticmethod
    def _check_category(category: str) -> None:
        if category not in CATEGORIES:
            raise MusicLibraryError(
                f"Categoria '{category}' sconosciuta. Ammesse: {', '.join(CATEGORIES)}."
            )

    # -- risoluzione dei nomi -------------------------------------------------

    def resolve(
        self, category: str, query: str
    ) -> tuple[MusicTrack | None, list[MusicTrack]]:
        """Risolve un nome digitato dall'utente dentro UNA categoria.

        Stessa semantica di VoiceLibrary.resolve(): ritorna (match_esatto,
        candidati). Se match_esatto e' None, il chiamante deve chiedere
        conferma, mai indovinare.
        """
        self._check_category(category)
        raw = query.strip()
        pool = self._tracks[category]
        if not raw or not pool:
            return None, []

        if raw.isdigit():
            # Dall'interfaccia un numero non vale come nome (come per le voci):
            # l'indice resta solo per run.py, che mostra l'elenco numerato
            # (decisione dell'utente dopo C7).
            if web_mode():
                return None, []
            index = int(raw)
            if 1 <= index <= len(pool):
                return pool[index - 1], []
            return None, []

        normalized = _normalize(raw)
        exact = [t for t in pool if _normalize(t.name) == normalized]
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact

        substring = [t for t in pool if normalized in _normalize(t.name)]
        if substring:
            return None, substring

        close = difflib.get_close_matches(
            normalized, [_normalize(t.name) for t in pool], n=3, cutoff=0.6
        )
        candidates = [t for t in pool if _normalize(t.name) in close]
        return None, candidates

    def get_usable(self, category: str, name: str) -> MusicTrack:
        """Il brano ESATTO per nome, gia' validato. Solleva se manca o e' invalido.

        A differenza di resolve() (che serve all'interazione con l'utente),
        questo serve al mix: li' il nome e' gia' stato scelto e salvato in
        musiche.json, quindi una mancata corrispondenza e' un errore di
        dominio (il file e' sparito o e' stato rinominato dopo la scelta),
        non un'ambiguita' da chiedere.
        """
        self._check_category(category)
        for track in self._tracks[category]:
            if track.name == name:
                if track.metadata_error:
                    raise MusicLibraryError(
                        f"'{name}' ({category}) non e' utilizzabile: {track.metadata_error}"
                    )
                unreadable = track.readability_error()
                if unreadable:
                    raise MusicLibraryError(f"'{name}' ({category}) non e' utilizzabile: {unreadable}")
                return track
        raise MusicLibraryError(
            f"'{name}' non e' piu' fra i brani di {category}/ in {self.dir}: "
            "e' stato spostato o rinominato dopo la scelta. Usa "
            "tools/music_set.py per sceglierne un altro."
        )


MUSIC_CHOICE_FILENAME = "musiche.json"


def load_music_choice(run_dir: Path | str) -> dict[str, str | None]:
    """Legge musiche.json della run. Assente = nessuna musica scelta (non errore)."""
    target = Path(run_dir) / MUSIC_CHOICE_FILENAME
    if not target.is_file():
        return {c: None for c in CATEGORIES}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise MusicLibraryError(f"{target} non e' un JSON leggibile: {exc}") from exc
    if not isinstance(raw, dict):
        raise MusicLibraryError(f"{target} deve contenere un oggetto JSON.")
    return {c: raw.get(c) for c in CATEGORIES}


def write_music_choice(run_dir: Path | str, choice: dict[str, str | None]) -> Path:
    """Scrive musiche.json con i nomi gia' risolti. Operazione gratuita: non spende."""
    target = Path(run_dir) / MUSIC_CHOICE_FILENAME
    payload = {c: choice.get(c) for c in CATEGORIES}
    return write_json_atomic(target, payload)


def has_any_music(choice: dict[str, str | None]) -> bool:
    return any(choice.get(c) for c in CATEGORIES)


__all__ = [
    "CATEGORIES",
    "MUSIC_CHOICE_FILENAME",
    "MusicLibrary",
    "MusicLibraryError",
    "MusicTrack",
    "TrackMetadata",
    "load_music_choice",
    "write_music_choice",
    "has_any_music",
]
