"""Forma d'onda del file finale, per il player del passo 7.

I picchi si calcolano sul FILE FINALE (intro e outro comprese, cosi' i punti
cadono dove li sente l'utente) decodificandolo con ffmpeg in mono a bassa
frequenza: per disegnare qualche centinaio di barre non serve di piu'.

Il risultato si tiene in cache in `_forma_onda_<N>.json`, legato allo sha256
del file: se l'episodio viene rimontato lo sha256 cambia e i picchi si
ricalcolano. La cache si scrive in modo atomico, quindi due richieste insieme
al massimo calcolano due volte la stessa cosa.
"""

from __future__ import annotations

import json
import subprocess
from array import array
from pathlib import Path
from typing import Any

from .errors import DomainError
from .ffmpeg_tool import FFMPEG_TIMEOUT_SECONDS, require_ffmpeg
from .fileio import write_json_atomic
from .registry import file_hash

MIN_POINTS = 10
MAX_POINTS = 4000
#: Frequenza a cui si decodifica: abbastanza per i picchi, poca memoria
#: (un'ora di episodio = ~14 MB di campioni).
_DECODE_RATE = 2000


class WaveformError(DomainError):
    """Forma d'onda non calcolabile (file assente, numero di punti fuori scala)."""


def cache_path(run_dir: Path, points: int) -> Path:
    return Path(run_dir) / f"_forma_onda_{points}.json"


def _decode(path: Path) -> array:
    command = [
        require_ffmpeg(), "-v", "error", "-nostdin",
        "-i", str(path), "-ac", "1", "-ar", str(_DECODE_RATE), "-f", "s16le", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise WaveformError(f"ffmpeg non ha finito entro {FFMPEG_TIMEOUT_SECONDS} secondi.") from exc
    if result.returncode != 0:
        raise WaveformError(
            f"Impossibile decodificare {path.name}: "
            + result.stderr.decode("utf-8", "replace").strip()[-300:]
        )
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) - len(result.stdout) % 2])
    return samples


def peaks(path: Path, points: int) -> tuple[list[float], float]:
    """(picchi normalizzati 0-1, durata in secondi)."""
    samples = _decode(path)
    total = len(samples)
    duration = total / _DECODE_RATE
    if total == 0:
        return [0.0] * points, 0.0
    out = []
    for index in range(points):
        start = index * total // points
        end = max(start + 1, (index + 1) * total // points)
        chunk = samples[start:end]
        peak = max(max(chunk), -min(chunk)) if chunk else 0
        out.append(round(min(1.0, peak / 32768), 3))
    return out, round(duration, 3)


def waveform(run_dir: Path, final: Path | None, points: int) -> dict[str, Any]:
    if not MIN_POINTS <= points <= MAX_POINTS:
        raise WaveformError(
            f"Numero di punti fuori scala: {points} (da {MIN_POINTS} a {MAX_POINTS}).",
            details={"campo": "points", "minimo": MIN_POINTS, "massimo": MAX_POINTS},
        )
    if final is None or not final.is_file():
        raise WaveformError(
            "L'episodio non ha ancora un file finale: producilo prima.", error_type="EpisodeNotProduced"
        )
    digest = file_hash(final)
    target = cache_path(run_dir, points)
    if target.is_file():
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached.get("sha256") == digest and cached.get("file") == final.name:
                return {**cached, "cached": True}
        except (OSError, json.JSONDecodeError):
            pass
    values, duration = peaks(final, points)
    payload = {"file": final.name, "sha256": digest, "points": points, "duration_seconds": duration, "peaks": values}
    write_json_atomic(target, payload)
    return {**payload, "cached": False}


__all__ = ["MAX_POINTS", "MIN_POINTS", "WaveformError", "cache_path", "peaks", "waveform"]
