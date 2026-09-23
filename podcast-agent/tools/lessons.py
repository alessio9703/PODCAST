#!/usr/bin/env python3
"""Aggrega i segnali degli episodi, propone lezioni, le approva.

    # schemi ricorrenti, deterministico, nessun costo
    python tools/lessons.py --evidence [--last N]
    # chiede al modello fino a due lezioni dalle prove aggregate (dichiara il costo prima)
    python tools/lessons.py --propose [--dry-run]
    # cosa c'e' gia'
    python tools/lessons.py --list
    # SOLO l'utente approva: promuove una proposta in prompts/lezioni.md
    python tools/lessons.py --accept ID [--text "..."] [--remove-id ID]
    python tools/lessons.py --reject ID [--reason "..."]
    # una lezione attiva il cui schema si ripresenta ancora
    python tools/lessons.py --review
    # andamento degli indicatori per episodio (non e' una misura di qualita')
    python tools/lessons.py --trend

Nessun percorso di questo comando scrive in prompts/lezioni.md se non
`--accept`: vedi src/lessons.py.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from _common import NeedsUserInput, PROJECT_ROOT, add_config_arg, bootstrap, emit, note, run_tool  # noqa: E402
from src import lessons as lessons_mod  # noqa: E402
from src.episode_signals import SIGNALS_FILENAME  # noqa: E402

PROMPTS_DIR = PROJECT_ROOT / "prompts"
COPIONE_PATH = PROMPTS_DIR / "copione.md"


def _load_all_signals(output_dir: Path) -> list[dict]:
    out: list[dict] = []
    if not output_dir.is_dir():
        return out
    for run_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        target = run_dir / SIGNALS_FILENAME
        if not target.is_file():
            continue
        try:
            out.append(json.loads(target.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _thresholds(lessons_config: dict) -> dict[str, int]:
    return {
        "min_occurrences": int(lessons_config.get("recurrence_min_occurrences", 3)),
        "min_episodes": int(lessons_config.get("recurrence_min_episodes", 2)),
        "max_examples_per_pattern": int(lessons_config.get("max_examples_per_pattern", 5)),
        "max_example_chars": int(lessons_config.get("max_example_chars", 200)),
    }


def _recent_signals(config, lessons_config: dict, last: int | None) -> list[dict]:
    n = last or int(lessons_config.get("evidence_lookback_episodes", 6))
    all_signals = _load_all_signals(config.output_dir)
    all_signals.sort(key=lambda s: str(s.get("generato_il", "")), reverse=True)
    return all_signals[:n]


def _do_evidence(config, lessons_config: dict, last: int | None) -> None:
    recent = _recent_signals(config, lessons_config, last)
    aggregated = lessons_mod.aggregate_evidence(recent, **_thresholds(lessons_config))
    note(
        f"{aggregated['episodi_analizzati']} episodi analizzati (soglia "
        f"{aggregated['soglia_occorrenze']} occorrenze / {aggregated['soglia_episodi']} episodi): "
        f"{len(aggregated['schemi'])} schemi sopra soglia."
    )
    emit({"evidenza": aggregated})


def _do_propose(config, lessons_config: dict, last: int | None, dry_run: bool) -> None:
    recent = _recent_signals(config, lessons_config, last)
    aggregated = lessons_mod.aggregate_evidence(recent, **_thresholds(lessons_config))

    if not aggregated["schemi"]:
        note("Nessuno schema sopra soglia: niente da proporre.")
        emit({"proposte": [], "scartate": [], "motivo_se_vuoto": "nessuno schema sopra soglia.", "evidenza": aggregated})
        return

    thresholds = _thresholds(lessons_config)
    active_text = "\n\n".join(
        f"{e['id']}: {e.get('testo', '')}" for e in lessons_mod.load_db(PROMPTS_DIR) if e.get("stato") == "attiva"
    )
    copione_text = COPIONE_PATH.read_text(encoding="utf-8") if COPIONE_PATH.is_file() else ""
    prompt = lessons_mod.build_propose_prompt(
        aggregated,
        copione_text=copione_text,
        active_lessons_text=active_text,
        max_examples_per_pattern=thresholds["max_examples_per_pattern"],
        max_example_chars=thresholds["max_example_chars"],
    )

    model = str(lessons_config.get("model") or config.script.get("model", "claude-opus-5"))
    stima = lessons_mod.estimate_propose_cost(prompt, lessons_config)
    costo = (
        f"~${stima['cost_usd']:.4f} ({stima['cost_basis']})"
        if stima["cost_usd"] is not None
        else stima["cost_basis"]
    )
    note(
        f"Chiamata a {model}: ~{stima['input_tokens_stimati']} token in ingresso, "
        f"fino a {stima['output_tokens_stimati_max']} in uscita, {costo}."
    )

    if dry_run:
        emit({"dry_run": True, "evidenza": aggregated, "stima": stima})
        return

    api_key_env = str(lessons_config.get("api_key_env") or config.script.get("api_key_env", "ANTHROPIC_API_KEY"))
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(
            f"Variabile d'ambiente {api_key_env} non impostata: serve per far proporre le lezioni a Claude.\n"
            f"Aggiungila al file .env:\n    {api_key_env}=sk-ant-..."
        )

    result = lessons_mod.propose(
        prompts_dir=PROMPTS_DIR,
        copione_path=COPIONE_PATH,
        aggregated=aggregated,
        api_key=api_key,
        model=model,
        max_examples_per_pattern=thresholds["max_examples_per_pattern"],
        max_example_chars=thresholds["max_example_chars"],
    )
    for s in result["scartate"]:
        note(f"Proposta scartata dal tool: {s['motivo']}")
    for p in result["proposte"]:
        note(f"Proposta {p['id']} (in prompts/_lezioni.json, non ancora attiva): {p['testo']}")
    if not result["proposte"] and result.get("motivo_se_vuoto"):
        note(f"Il modello non propone niente: {result['motivo_se_vuoto']}")
    emit({**result, "stima": stima})


def _do_accept(lessons_config: dict, lesson_id: str, text_override: str | None, remove_id: str | None) -> None:
    lezioni_md_path = PROMPTS_DIR / lessons_mod.MD_FILENAME
    try:
        result = lessons_mod.accept(
            PROMPTS_DIR,
            lezioni_md_path,
            lesson_id,
            text_override=text_override,
            remove_id=remove_id,
            max_lines=int(lessons_config.get("max_lines", 20)),
        )
    except lessons_mod.NeedsRemoval as exc:
        candidati = "; ".join(f"{c['id']} ({c['motivo']}): {c['testo']}" for c in exc.candidates)
        note(f"{exc} Candidati: {candidati or 'nessuno'}.")
        raise NeedsUserInput(f"{exc} Candidati: {candidati or 'nessuno'}.") from exc

    note(f"Lezione {lesson_id} attiva in {result['lezioni_md']}.")
    if result.get("rimossa"):
        note(f"Rimossa {result['rimossa']} per far posto a {lesson_id}.")
    emit(result)


def _do_reject(lesson_id: str, reason: str | None) -> None:
    result = lessons_mod.reject(PROMPTS_DIR, lesson_id, reason=reason)
    note(f"Lezione {lesson_id} rifiutata.")
    emit(result)


def _do_review(config, lessons_config: dict) -> None:
    all_signals = _load_all_signals(config.output_dir)
    result = lessons_mod.review(
        PROMPTS_DIR, all_signals, after_episodes=int(lessons_config.get("review_after_episodes", 3))
    )
    for item in result["lezioni_non_funzionanti"]:
        note(
            f"ATTENZIONE: {item['id']} non sta funzionando: lo schema si e' ripresentato "
            f"{item['occorrenze_dopo']} volte in {item['episodi_controllati']} episodi da quando e' attiva."
        )
    if not result["lezioni_non_funzionanti"]:
        note("Nessuna lezione segnalata (fra quelle gia' controllabili).")
    emit(result)


def _do_trend(config) -> None:
    all_signals = _load_all_signals(config.output_dir)
    result = lessons_mod.trend(all_signals)
    note(result["avvertenza"])
    emit(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--evidence", action="store_true", help="Schemi ricorrenti, deterministico, nessun costo")
    mode.add_argument("--propose", action="store_true", help="Chiede al modello fino a due lezioni dalle prove")
    mode.add_argument("--list", action="store_true", help="Elenca le lezioni attive, proposte, rifiutate, rimosse")
    mode.add_argument("--accept", metavar="ID", help="Promuove una proposta in prompts/lezioni.md")
    mode.add_argument("--reject", metavar="ID", help="Scarta una proposta")
    mode.add_argument("--review", action="store_true", help="Segnala le lezioni attive il cui schema si ripresenta ancora")
    mode.add_argument("--trend", action="store_true", help="Andamento degli indicatori per episodio")

    parser.add_argument("--last", type=int, default=None, help="Quanti episodi guardare (default: config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Con --propose: mostra solo le prove e la stima, non chiama il modello")
    parser.add_argument("--text", default=None, help="Con --accept: riscrive il testo della proposta")
    parser.add_argument("--remove-id", default=None, help="Con --accept: quale lezione attiva togliere se serve spazio")
    parser.add_argument("--reason", default=None, help="Con --reject: perche' si scarta")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    lessons_config = config.lessons

    if args.evidence:
        _do_evidence(config, lessons_config, args.last)
    elif args.propose:
        _do_propose(config, lessons_config, args.last, args.dry_run)
    elif args.list:
        emit({"lezioni": lessons_mod.list_lessons(PROMPTS_DIR)})
    elif args.accept:
        _do_accept(lessons_config, args.accept, args.text, args.remove_id)
    elif args.reject:
        _do_reject(args.reject, args.reason)
    elif args.review:
        _do_review(config, lessons_config)
    elif args.trend:
        _do_trend(config)


if __name__ == "__main__":
    run_tool(main)
