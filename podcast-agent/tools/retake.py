#!/usr/bin/env python3
"""Rifa' una battuta a parita' di testo, e lascia scegliere il take all'utente.

    # quanto costa (nessuna chiamata al provider)
    python tools/retake.py --run-dir output/<run> --turn 17 --takes 3 --dry-run
    # genera i take e le anteprime
    python tools/retake.py --run-dir output/<run> --turn 17 --takes 3
    # cosa c'e' gia'
    python tools/retake.py --run-dir output/<run> --list 17
    # quale usare nel montaggio
    python tools/retake.py --run-dir output/<run> --choose 17=3

E' il percorso per quando il TESTO va bene e la RESA no. Se il testo va
cambiato non si passa di qui: si corregge la bozza, si risalva con
script_save.py e si rifa' approvare.

Dopo `--choose`, synthesize.py rimonta l'episodio con zero chiamate TTS.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import wave
from pathlib import Path

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.audio_assembler import AudioAssembler  # noqa: E402
from src.music_mixer import preflight, render_bed_under  # noqa: E402
from src.segment_cache import RunCache  # noqa: E402
from src.retake import (  # noqa: E402
    choose_take,
    estimate_retake,
    generate_takes,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--turn", type=int, default=None, help="Numero del turno (da 1)")
    parser.add_argument("--takes", type=int, default=1, help="Quanti take generare")
    parser.add_argument("--list", type=int, default=None, help="Elenca i take di un turno")
    parser.add_argument("--choose", default=None, help="TURNO=TAKE, es. 17=3")
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
    args = parser.parse_args()

    modes = [args.turn is not None, args.list is not None, args.choose is not None]
    if sum(modes) != 1:
        raise ValueError(
            "Scegli una sola modalita': --turn N (genera), --list N (elenca) "
            "oppure --choose N=K (sceglie)."
        )

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    provider = config.build_provider()
    cache = RunCache.load(run_dir, provider)

    if args.list is not None:
        _do_list(cache, args.list)
        return
    if args.choose is not None:
        _do_choose(cache, args.choose)
        return
    _do_generate(cache, config, args)


# ----------------------------------------------------------------------


def _do_list(cache: RunCache, numero: int) -> None:
    turn = cache.turn(numero)
    takes = cache.takes(numero)
    chosen = cache.chosen(numero)
    note(f"Turno {numero} ({turn['speaker']}): take {takes or 'nessuno'}, scelto {chosen}")
    note(f"  {turn['testo']}")
    emit(
        {
            "turn": numero,
            "speaker": turn["speaker"],
            "testo": turn["testo"],
            "takes": takes,
            "chosen_take": chosen,
            "files": {str(t): str(cache.path(numero, t)) for t in takes},
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
        "Rimonta con synthesize.py: nessuna chiamata al provider."
    )
    emit({**result, "speaker": cache.turn(numero)["speaker"], "tts_calls": 0})


def _do_generate(cache: RunCache, config, args: argparse.Namespace) -> None:
    numero, count = args.turn, args.takes
    if count < 1:
        raise ValueError("--takes vuole almeno 1.")

    prediction = estimate_retake(cache, numero, count)

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

    # Un retake non cambia il copione, quindi l'approvazione esistente resta
    # valida — ma il gate si attraversa lo stesso: e' un passo che spende.
    gate = enforce(args)

    assembler = AudioAssembler.from_config(cache.provider, config.audio)
    note(f"Turno {numero} ({cache.turn(numero)['speaker']}): genero {count} take...")
    takes = generate_takes(cache, assembler, numero, count, note=note)

    warnings = [w for t in takes for w in t["warnings"]]
    for warning in warnings:
        note(f"ATTENZIONE: {warning}")

    if args.with_music:
        _add_previews_with_music(cache, config, takes, warnings)

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
            "new_takes": [t["take"] for t in takes],
            "takes": takes,
            "all_takes": cache.takes(numero),
            "chosen_take": cache.chosen(numero),
            "previews": previews,
            "previews_with_music": [t.get("preview_with_music") for t in takes if t.get("preview_with_music")],
            "tts_calls": len(takes),
            "chars": prediction["chars"],
            "cost_usd": prediction["cost_usd"],
            "cost_basis": prediction["cost_basis"],
            "warnings": warnings,
            **gate,
        }
    )


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
        shutil.copy2(out, target)
    return target


if __name__ == "__main__":
    run_tool(main)
