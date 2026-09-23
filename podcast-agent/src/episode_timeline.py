"""Dal minutaggio dell'episodio alla battuta che si sente in quel punto.

Perche' esiste: l'utente non dice "il turno 17", dice "al tre e quaranta suona
male". Tradurre l'uno nell'altro richiede di sapere dove cade ogni turno dentro
il master, che e' una proprieta' del MONTAGGIO (pause comprese) e viene
calcolata da `AudioAssembler._timeline()`. Qui si legge quello che il montaggio
ha scritto in `_tempi_turni.json`; nessun tempo viene ricalcolato, altrimenti
ci sarebbero due verita' sulle stesse pause.

Gli episodi prodotti prima di questa funzione hanno il file ma non la timeline:
il caso e' previsto e produce un errore che dice cosa fare (rimontare), non un
KeyError.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIMINGS_FILENAME = "_tempi_turni.json"


class TimelineError(RuntimeError):
    """Timeline mancante, illeggibile o senza gli offset dei turni."""


@dataclass(frozen=True)
class TurnLocation:
    """Il turno che si sente (o che si e' appena sentito) a un dato istante."""

    numero: int
    speaker: str
    testo: str
    inizio: float
    fine: float
    #: True quando l'istante chiesto cade FUORI da ogni turno: in una pausa,
    #: nel lead-in o nel lead-out. Il turno restituito e' il piu' vicino.
    in_pausa: bool
    #: Distanza in secondi dall'istante chiesto al turno; 0 se ci cade dentro.
    distanza: float


def parse_timecode(value: str) -> float:
    """Accetta "3:42", "1:02:07" oppure i secondi ("222", "222.5")."""
    text = str(value).strip()
    if not text:
        raise ValueError("Minutaggio vuoto: usa 3:42 oppure i secondi.")

    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(
            f"Minutaggio '{value}' non riconosciuto: usa secondi, m:ss oppure h:mm:ss."
        )
    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(
            f"Minutaggio '{value}' non riconosciuto: usa secondi, m:ss oppure h:mm:ss."
        ) from exc

    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds < 0:
        raise ValueError(f"Minutaggio negativo: {value}")
    return seconds


def load_timeline(run_dir: Path) -> list[dict[str, Any]]:
    """Legge i turni con inizio/fine da _tempi_turni.json."""
    target = Path(run_dir) / TIMINGS_FILENAME
    if not target.is_file():
        raise TimelineError(
            f"Nessuna timeline in {target}.\n"
            "Il file viene scritto dal montaggio: rilancia tools/synthesize.py "
            "sulla cartella dell'episodio. Se i segmenti sono ancora in cache "
            "non costa niente, perche' non viene risintetizzato nessun turno."
        )
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise TimelineError(f"{target} non e' leggibile: {exc}") from exc

    turni = raw.get("turni") if isinstance(raw, dict) else None
    if not turni:
        raise TimelineError(f"{target} non contiene nessun turno.")
    if any("inizio" not in t or "fine" not in t for t in turni):
        raise TimelineError(
            f"{target} e' stato scritto da una versione precedente e non contiene "
            "gli offset dei turni.\n"
            "Rimonta l'episodio con tools/synthesize.py: con i segmenti in cache "
            "non c'e' nessuna chiamata al provider, quindi non costa niente."
        )
    return list(turni)


def attach_texts(turni: list[dict[str, Any]], testi: list[str]) -> list[dict[str, Any]]:
    """Accoppia la timeline al testo dei turni del copione.

    Il testo NON viene salvato dentro _tempi_turni.json di proposito: sarebbe
    una seconda copia del copione, che puo' divergere da conversazione.json
    senza che nessuno se ne accorga. Qui invece la divergenza si vede subito,
    perche' i conteggi non tornano.
    """
    if len(turni) != len(testi):
        raise TimelineError(
            f"La timeline ha {len(turni)} turni ma il copione ne ha {len(testi)}: "
            "l'episodio e' stato montato da un copione diverso da quello attuale.\n"
            "Rimonta con tools/synthesize.py, poi richiedi il minutaggio."
        )
    return [{**turn, "testo": testo} for turn, testo in zip(turni, testi)]


def turn_at(turni: list[dict[str, Any]], seconds: float) -> TurnLocation:
    """Il turno che si sente a `seconds`, o il piu' vicino se e' in una pausa."""
    best: tuple[float, int] | None = None
    for position, turn in enumerate(turni):
        inizio = float(turn["inizio"])
        fine = float(turn["fine"])
        if inizio <= seconds <= fine:
            distance = 0.0
        elif seconds < inizio:
            distance = inizio - seconds
        else:
            distance = seconds - fine
        if best is None or distance < best[0]:
            best = (distance, position)

    assert best is not None  # la lista non e' mai vuota: lo garantisce load_timeline
    distance, position = best
    turn = turni[position]
    return TurnLocation(
        numero=position + 1,
        speaker=str(turn.get("speaker", "")),
        testo=str(turn.get("testo", "")),
        inizio=float(turn["inizio"]),
        fine=float(turn["fine"]),
        in_pausa=distance > 0,
        distanza=round(distance, 3),
    )


def label(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}:{rest:02d}"


__all__ = [
    "TIMINGS_FILENAME",
    "TimelineError",
    "TurnLocation",
    "parse_timecode",
    "load_timeline",
    "attach_texts",
    "turn_at",
    "label",
]
