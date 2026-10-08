#!/usr/bin/env python3
"""Chiude la run: voci_usate.json + copia del documento + riepilogo.

    python tools/run_finalize.py --run-dir output/<run> --document documento.pdf \
        --voices output/<run>/voci.json --audio-paths output/<run>/_audio_paths.json

Ritorna il riepilogo che l'agente deve riportare all'utente.
"""

from __future__ import annotations

import argparse
import json

from _common import add_config_arg, add_trusted_paths_arg, bootstrap, documents_confined, emit, file_arg, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.pdf_reader import locate_document  # noqa: E402
from src.registry import VoiceRegistry  # noqa: E402
from src.run_layout import archive_source_document, write_voices_used  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--document", required=True, help="Documento di partenza")
    parser.add_argument("--voices", required=True, help="JSON speaker -> voice_id")
    parser.add_argument(
        "--audio-paths",
        default=None,
        help="JSON speaker -> file audio di riferimento (da voices_prepare.py)",
    )
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    voice_map = json.loads(file_arg(args.voices, config, args, "voices").read_text(encoding="utf-8"))
    audio_paths = (
        json.loads(file_arg(args.audio_paths, config, args, "audio_paths").read_text(encoding="utf-8"))
        if args.audio_paths
        else {}
    )

    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    voices_file = write_voices_used(
        run_dir, config.active_provider, voice_map, audio_paths, registry
    )
    # Stessa ricerca di doc_extract.py: un nome nudo va cercato in documents_dir,
    # non risolto contro la working directory.
    source = locate_document(
        args.document, search_dir=config.documents_dir, confined=documents_confined(args)
    )
    source_note = archive_source_document(source, run_dir)
    note(f"Run chiusa: {run_dir}")

    emit(
        {
            "run": run_dir.name,
            "run_dir": str(run_dir),
            "voci_usate": str(voices_file),
            "documento": source_note,
            "files": sorted(p.name for p in run_dir.iterdir()),
        }
    )


if __name__ == "__main__":
    run_tool(main)
