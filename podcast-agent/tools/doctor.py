#!/usr/bin/env python3
"""Diagnosi dell'installazione: cosa c'e', cosa manca, da dove.

    python tools/doctor.py

Primo passo consigliato dopo `pip install -r requirements.txt` (vedi
README.md). Non chiama nessun provider e non spende: controlla solo che
l'ambiente sia pronto.

A differenza degli altri tool, l'exit code qui non segue la convenzione
0/1/2/3 di _common.py: e' un riassunto di controlli, non un'eccezione. `ok`
resta 0/1 secondo che TUTTI i controlli OBBLIGATORI siano passati; i
controlli facoltativi (musiche/, dipendenze opzionali) non influenzano
l'exit code, solo il JSON.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

#: (modulo, nome del pacchetto pip, obbligatorio?)
_DEPENDENCIES = [
    ("anthropic", "anthropic", True),
    ("requests", "requests", True),
    ("yaml", "PyYAML", True),
    ("pypdf", "pypdf", True),
    ("imageio_ffmpeg", "imageio-ffmpeg", True),
    ("docx", "python-docx", False),
    ("lameenc", "lameenc", False),
]

#: Filtri ffmpeg necessari al mix delle musiche (vedi src/music_mixer.py).
_REQUIRED_FILTERS = [
    "loudnorm", "sidechaincompress", "acrossfade", "aresample", "afade", "amix",
]


def _check(name: str, ok: bool, detail: str, required: bool) -> dict[str, Any]:
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def check_python() -> dict[str, Any]:
    version = sys.version.split()[0]
    ok = sys.version_info >= (3, 10)
    return _check(
        "python_version", ok, version if ok else f"{version} (serve almeno 3.10)", True
    )


def check_dependencies() -> list[dict[str, Any]]:
    results = []
    for module_name, pip_name, required in _DEPENDENCIES:
        try:
            importlib.import_module(module_name)
            try:
                version = importlib.metadata.version(pip_name)
            except importlib.metadata.PackageNotFoundError:
                version = "installato"
            results.append(_check(f"dependency:{pip_name}", True, version, required))
        except ImportError:
            detail = (
                "non installato — .venv/bin/pip install -r requirements.txt"
                if required
                else "non installato (opzionale)"
            )
            results.append(_check(f"dependency:{pip_name}", not required, detail, required))
    return results


def check_ffmpeg() -> tuple[list[dict[str, Any]], str | None]:
    from src.ffmpeg_tool import find_ffmpeg

    exe = find_ffmpeg()
    if exe is None:
        return [
            _check(
                "ffmpeg", False,
                "non trovato: ne' imageio-ffmpeg ne' un ffmpeg di sistema nel PATH",
                True,
            )
        ], None

    source = "imageio-ffmpeg" if "imageio_ffmpeg" in exe else "PATH di sistema"
    results = [_check("ffmpeg", True, f"{exe} ({source})", True)]

    import subprocess

    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
        )
        listing = proc.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        results.append(
            _check("ffmpeg_filters", False, f"impossibile interrogare ffmpeg: {exc}", True)
        )
        return results, exe

    for filt in _REQUIRED_FILTERS:
        present = any(
            line.split()[1] == filt
            for line in listing.splitlines()
            if len(line.split()) > 1 and not line.startswith("Filters:")
        )
        results.append(
            _check(
                f"ffmpeg_filter:{filt}", present,
                "presente" if present else "ASSENTE: questa build di ffmpeg non lo include",
                True,
            )
        )
    return results, exe


def check_paths() -> list[dict[str, Any]]:
    from src.config import ConfigError, load_config

    try:
        config = load_config()
    except ConfigError as exc:
        return [_check("config.yaml", False, str(exc), True)]

    results = [_check("config.yaml", True, str(config.path), True)]
    # documents_dir/voices_dir/output_dir sono di fatto obbligatori per un
    # episodio; music_dir e' opzionale per definizione (vedi README.md).
    for label, path, required in (
        ("paths.documents_dir", config.documents_dir, False),
        ("paths.voices_dir", config.voices_dir, True),
        ("paths.output_dir", config.output_dir, False),
        ("paths.music_dir", config.music_dir, False),
    ):
        exists = path.is_dir()
        detail = str(path) if exists else f"{path} (verra' creata/e' opzionale)"
        results.append(_check(label, exists or not required, detail, required))
    return results


def main() -> int:
    checks: list[dict[str, Any]] = [check_python()]
    checks.extend(check_dependencies())

    ffmpeg_checks, ffmpeg_exe = check_ffmpeg()
    checks.extend(ffmpeg_checks)
    checks.extend(check_paths())

    all_required_ok = all(c["ok"] for c in checks if c["required"])

    for c in checks:
        marker = "ok  " if c["ok"] else ("FAIL" if c["required"] else "warn")
        print(f"  {marker} {c['name']}: {c['detail']}", file=sys.stderr)

    payload = {
        "ok": all_required_ok,
        "checks": checks,
        "ffmpeg_path": ffmpeg_exe,
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if all_required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
