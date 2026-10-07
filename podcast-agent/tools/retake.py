#!/usr/bin/env python3
"""Rifa' una battuta a parita' di testo, e lascia scegliere il take all'utente.

    # quanto costa (nessuna chiamata al provider)
    python tools/retake.py --run-dir output/<run> --turn 17 --takes 3 --direction "Più calmo" --dry-run
    # genera i take e le anteprime
    python tools/retake.py --run-dir output/<run> --turn 17 --takes 3 --direction "Più calmo"
    # i take che ci sono, con l'anteprima in contesto (nessuna chiamata)
    python tools/retake.py --run-dir output/<run> --list 17 [--with-music]
    # quale usare nel montaggio (poi tools/mix.py rimonta)
    python tools/retake.py --run-dir output/<run> --choose 17=3

E' il percorso per quando il TESTO va bene e la RESA no ("Come e' detta").
Se il testo va cambiato non si passa di qui: script_edit.py, nuova
approvazione, produce.py (che paga solo la battuta cambiata).

`--direction` (ripetibile) sono le indicazioni "Come deve cambiare?": solo
quelle di config.yaml (retake.indicazioni), mai testo libero. Le loro tag
vanno SOLO nel testo inviato per i take nuovi: il copione approvato non
cambia. "Pronuncia diversa" da' DirectionNeedsTextEdit: si corregge il testo.

Generazione e scelta tengono il lock di lavoro della run. Una voce il cui
consenso e' stato revocato non si usa nemmeno per un retake.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import wave
from pathlib import Path

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, parse_args, require_by_in_web, run_dir_arg, run_tool  # noqa: E402
from src.audio_assembler import AudioAssembler  # noqa: E402
from src.errors import DomainError  # noqa: E402
from src.fileio import atomic_output  # noqa: E402
from src.music_mixer import preflight, render_bed_under  # noqa: E402
from src.production import ui_path  # noqa: E402
from src.retake import (  # noqa: E402
    choose_take,
    estimate_retake,
    generate_takes,
    list_takes,
    max_takes,
    resolve_directions,
)
from src.run_lock import work_lock  # noqa: E402
from src.segment_cache import RunCache  # noqa: E402
from src.redaction import redact  # noqa: E402
from src.voice_prep import check_not_revoked  # noqa: E402
from src.web_mode import web_mode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--turn", type=int, default=None, help="Numero del turno (da 1)")
    parser.add_argument("--takes", type=int, default=1, help="Quanti take generare")
    parser.add_argument(
        "--direction", action="append", default=[], metavar="INDICAZIONE",
        help="Come deve cambiare (ripetibile), es. \"Più calmo\": solo quelle di config.yaml",
    )
    parser.add_argument("--list", type=int, default=None, help="Elenca i take di un turno")
    parser.add_argument("--choose", default=None, help="TURNO=TAKE, es. 17=3")
    parser.add_argument("--by", default=None, help="Chi chiede i take (registrato nei metadati)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dice quanto costerebbe senza generare niente (nessun costo)",
    )
    parser.add_argument(
        "--with-music",
        action="store_true",
        help="Se la run ha un sottofondo scelto, monta anche un'anteprima con "
        "il sottofondo sotto (stessi parametri del mix vero). Le anteprime "
        "restano SENZA intro/outro.",
    )
    add_gate_args(parser, run_dir_required=True)
    add_config_arg(parser)
    args = parse_args(parser)

    modes = [args.turn is not None, args.list is not None, args.choose is not None]
    if sum(modes) != 1:
        raise ValueError(
            "Scegli una sola modalita': --turn N (genera), --list N (elenca) "
            "oppure --choose N=K (sceglie)."
        )
    if args.turn is not None and not args.dry_run:
        require_by_in_web(args.by, "la generazione di un retake")

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    # Il gate di approvazione legge args.run_dir: deve essere il percorso gia'
    # confinato, non il valore ricevuto (che puo' essere il solo nome).
    args.run_dir = str(run_dir)
    provider = config.build_provider()
    cache = RunCache.load(run_dir, provider)

    if args.list is not None:
        _do_list(cache, config, args)
        return
    if args.choose is not None:
        with work_lock(run_dir, "scelta_take"):
            _do_choose(cache, args.choose)
        return
    _do_generate(cache, config, args)


# ----------------------------------------------------------------------


def _do_list(cache: RunCache, config, args: argparse.Namespace) -> None:
    numero = args.list
    turn = cache.turn(numero)
    # Le anteprime montano la battuta e le due vicine: nessuna voce revocata
    # (revisione di sicurezza C3, MEDIA-2).
    neighbours = range(max(1, numero - 1), min(len(cache.script.turni), numero + 1) + 1)
    check_not_revoked(config, sorted({cache.turn(n)["speaker"] for n in neighbours}), voice_map=cache.voice_map)
    assembler = AudioAssembler.from_config(cache.provider, config.audio)
    takes = list_takes(cache, assembler, numero)
    warnings: list[str] = []
    if args.with_music:
        _add_previews_with_music(cache, config, takes, warnings)
    _relative_paths(takes, config)
    note(f"Turno {numero} ({turn['speaker']}): take {[t['take'] for t in takes] or 'nessuno'}, scelto {cache.chosen(numero)}")
    emit(
        {
            "turn": numero,
            "speaker": turn["speaker"],
            "testo": turn["testo"],
            "takes": [t["take"] for t in takes],
            "chosen_take": cache.chosen(numero),
            "files": {str(t["take"]): t["file"] for t in takes},
            "take_list": takes,
            "warnings": redact(warnings),
            "tts_calls": 0,
        }
    )


def _do_choose(cache: RunCache, spec: str) -> None:
    if "=" not in spec:
        raise ValueError(f"--choose vuole TURNO=TAKE (es. 17=3), non '{spec}'.")
    left, _, right = spec.partition("=")
    try:
        numero, take = int(left.strip()), int(right.strip())
    except ValueError as exc:
        raise ValueError(f"--choose vuole due numeri interi, non '{spec}'.") from exc

    result = choose_take(cache, numero, take)
    note(
        f"Turno {numero}: uso il take {take} nel montaggio. "
        "Rimonta con tools/mix.py: nessuna chiamata al provider."
    )
    emit({**result, "speaker": cache.turn(numero)["speaker"], "tts_calls": 0})


def _do_generate(cache: RunCache, config, args: argparse.Namespace) -> None:
    numero, count = args.turn, args.takes
    if count < 1:
        raise ValueError("--takes vuole almeno 1.")
    limit = max_takes(config)
    if count > limit:
        raise DomainError(
            f"Al massimo {limit} take per volta (retake.max_takes in config.yaml).",
            error_type="TooManyTakes",
            details={"massimo": limit, "richiesti": count},
        )
    directions = resolve_directions(config, args.direction)
    prediction = estimate_retake(cache, numero, count, directions)
    rate = config.eur_rate
    prediction["cost_eur"] = (
        round(prediction["cost_usd"] * rate[0], 4) if (rate and prediction["cost_usd"] is not None) else None
    )

    # Il dry-run non spende, quindi non ha bisogno dell'approvazione: stessa
    # regola di voices_prepare.py.
    if args.dry_run:
        note(
            f"Rifare il turno {numero} in {count} take: {prediction['chars']} caratteri"
            + (
                f", ~${prediction['cost_usd']:.4f} ({prediction['cost_basis']})"
                if prediction["cost_usd"] is not None
                else ", costo non disponibile per questo provider"
            )
        )
        emit({**prediction, "dry_run": True, "approval_gate": "not_needed"})
        return

    with work_lock(cache.run_dir, "retake", turno=numero):
        # Il copione si rilegge dentro il lock e il gate si attraversa su
        # QUELLO: si sintetizza esattamente il testo verificato (revisione di
        # sicurezza C3, MEDIA-1). Un retake non cambia il copione, ma spende.
        cache = RunCache.load(cache.run_dir, cache.provider)
        gate = enforce(args, cache.script.to_dict())
        # Una voce con il consenso revocato non si usa, nemmeno per un take; dal
        # web serve il consenso registrato, perche' il take e' un riuso della
        # voce (revisione finale, N1).
        speaker = cache.turn(numero)["speaker"]
        check_not_revoked(config, [speaker], voice_map=cache.voice_map, require_consent=web_mode())

        assembler = AudioAssembler.from_config(cache.provider, config.audio)
        note(f"Turno {numero} ({speaker}): genero {count} take...")
        takes = generate_takes(cache, assembler, numero, count, note=note, directions=directions, by=args.by)

        warnings = [w for t in takes for w in t["warnings"]]
        for warning in warnings:
            note(f"ATTENZIONE: {warning}")

        if args.with_music:
            _add_previews_with_music(cache, config, takes, warnings)

    _relative_paths(takes, config)
    previews = [t["preview"] for t in takes if t["preview"]]
    if previews:
        note("Anteprime da far ascoltare all'utente (turno precedente, take, successivo):")
        for path in previews:
            note(f"  {path}")
    note(
        f"Il take resta da scegliere: retake.py --choose {numero}=<take>. "
        "Non sceglierlo al posto dell'utente."
    )

    emit(
        {
            "turn": numero,
            "speaker": cache.turn(numero)["speaker"],
            "testo": cache.turn(numero)["testo"],
            "indicazioni": prediction["indicazioni"],
            "new_takes": [t["take"] for t in takes],
            "takes": takes,
            "all_takes": cache.takes(numero),
            "chosen_take": cache.chosen(numero),
            "previews": previews,
            "previews_with_music": [t.get("preview_with_music") for t in takes if t.get("preview_with_music")],
            "tts_calls": len(takes),
            "chars": prediction["chars"],
            "cost_usd": prediction["cost_usd"],
            "cost_eur": prediction["cost_eur"],
            "cost_basis": prediction["cost_basis"],
            "warnings": redact(warnings),
            **gate,
        }
    )


def _relative_paths(takes: list[dict], config) -> None:
    """Percorsi dei take e delle anteprime nella forma output/<run>/...

    Mai assoluti nel JSON (revisione di sicurezza C3, BASSA-1). `preview_ui`
    resta come sinonimo di `preview`, per il contratto.
    """
    for take in takes:
        for key in ("file", "preview", "preview_with_music"):
            if take.get(key):
                take[key] = ui_path(Path(take[key]), config)
        take["preview_ui"] = take.get("preview")
        if take.get("preview_with_music"):
            take["preview_with_music_ui"] = take["preview_with_music"]


def _add_previews_with_music(cache: RunCache, config, takes: list[dict], warnings: list[str]) -> None:
    """Aggiunge takes[i]['preview_with_music'], se la run ha un sottofondo.

    Il sottofondo si costruisce con lo STESSO take candidato appena generato
    (gia' dentro il wav di takes[i]['preview']: prev + take candidato +
    successivo), non con quello attualmente scelto — cosi' si giudica il
    take nuovo nel suo vero contesto sonoro.
    """
    music_choice, _ = preflight(cache.run_dir, config)
    if music_choice is None or not music_choice.sottofondo:
        note("Nessun sottofondo scelto per questa run: --with-music non aggiunge niente.")
        return

    for take in takes:
        preview_path = take.get("preview")
        take["preview_with_music"] = None
        if not preview_path:
            continue
        try:
            take["preview_with_music"] = str(
                _build_preview_with_music(Path(preview_path), music_choice.sottofondo, config)
            )
        except Exception as exc:  # noqa: BLE001 - un'anteprima in piu' non deve far fallire il retake
            warnings.append(f"Anteprima con musica del take {take['take']} non riuscita: {exc}")


def _build_preview_with_music(preview_wav: Path, sottofondo, config) -> Path:
    with wave.open(str(preview_wav), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
    target = preview_wav.with_name(preview_wav.stem + "_con_musica.wav")
    with tempfile.TemporaryDirectory(prefix="lil-preview-") as tmp_name:
        out = render_bed_under(preview_wav, sottofondo, rate, channels, config.music, Path(tmp_name))
        # Atomico: chi serve l'anteprima non la legge mai a meta' (C3, BASSA-6).
        with atomic_output(target) as partial:
            shutil.copyfile(out, partial)
    return target


if __name__ == "__main__":
    run_tool(main)
