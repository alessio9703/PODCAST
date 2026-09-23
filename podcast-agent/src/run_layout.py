"""Struttura della cartella di output di un episodio.

Estratto da run.py per essere condiviso fra la CLI classica (`run.py`) e gli
entry-point in `tools/` usati dal subagent: la stessa convenzione di nomi e lo
stesso contenuto della cartella, scritti in un posto solo.

Ogni run produce:

    output/<data>_<slug-titolo>/
    ├── <slug-titolo>.mp3          episodio finale
    ├── conversazione.json         script usato
    ├── voci_usate.json            speaker -> voice_id + hash + data di clonazione
    ├── documento_originale.<ext>  copia del documento di partenza
    └── _script_session.json       storia delle revisioni (solo dal subagent)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .audio_assembler import slugify
from .registry import VoiceRegistry


def make_run_dir(output_root: Path, title: str) -> Path:
    """Crea output/<data>_<slug>/ evitando collisioni con un suffisso numerico."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    run_dir = output_root / f"{stamp}_{slugify(title)}"
    suffix = 2
    while run_dir.exists():
        run_dir = output_root / f"{stamp}_{slugify(title)}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def write_voices_used(
    run_dir: Path,
    provider_name: str,
    voice_map: Mapping[str, str],
    audio_paths: Mapping[str, str | Path | None],
    registry: VoiceRegistry,
) -> Path:
    """Scrive voci_usate.json: quale voce ha detto cosa, in questa run."""
    payload = {
        "provider": provider_name,
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "speakers": {
            name: {
                "voice_id": voice_id,
                "audio_file": str(audio_paths[name])
                if audio_paths.get(name) is not None
                else None,
                "audio_hash": (registry.get(name) or {}).get("audio_hash"),
                "cloned_at": (registry.get(name) or {}).get("cloned_at"),
            }
            for name, voice_id in voice_map.items()
        },
    }
    target = run_dir / "voci_usate.json"
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def archive_source_document(source: Path, run_dir: Path) -> str:
    """Copia il documento nella cartella della run; se non riesce, ne salva il percorso."""
    target = run_dir / f"documento_originale{source.suffix}"
    try:
        shutil.copy2(source, target)
        return target.name
    except OSError as exc:
        fallback = run_dir / "documento_originale.percorso.txt"
        fallback.write_text(
            f"{source}\n\n(copia non riuscita: {exc})\n", encoding="utf-8"
        )
        return f"{fallback.name} (riferimento al percorso, copia non riuscita)"


__all__ = ["make_run_dir", "write_voices_used", "archive_source_document"]
