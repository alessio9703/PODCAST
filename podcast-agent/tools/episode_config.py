#!/usr/bin/env python3
"""Legge o modifica le scelte dell'episodio (passi 1-3): episodio.json.

    # cosa c'e' (e cosa manca)
    python tools/episode_config.py --run-dir output/<run>

    # passo 1: titolo e documento
    python tools/episode_config.py --run-dir output/<run> --titolo "Il costo della pioggia" \
        --documento report.pdf

    # passo 2: formato, durata, voci per ruolo, nomi da pronunciare, ruoli
    python tools/episode_config.py --run-dir output/<run> --formato intervista --durata 0:15 \
        --voce presentatore="Voce Alberto Tivoli" --nome presentatore="Alberto Tivoli" \
        --voce intervistatore="Voce Sarah Fornito" --nome intervistatore="Sarah Fornito" \
        --descrizione intervistatore="giornalista di LIL"

Ruoli: `presentatore` piu' `intervistatore` e `ospite` (intervista) oppure
`voce_a` e `voce_b` (conversazione). Le modifiche passate in una chiamata si
applicano tutte o nessuna. Operazione locale e gratuita.

Esiti:
  - voce scritta in modo incerto -> exit 3 AmbiguousName (candidati in details);
  - nome da pronunciare senza cognome -> exit 3 SpokenNameIncomplete: si
    aggiunge il cognome, oppure si conferma con --accetta-nome-incompleto;
  - documento senza testo (scansione, protetto) -> exit 1 DocumentNotExtractable.

Se il copione era approvato, cambiare formato, voci, nomi pronunciati o
descrizioni dei ruoli ritira l'approvazione (`approval_revoked` nel JSON):
il copione era stato approvato per un altro cast. Titolo e durata no.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.episode_config import ALL_ROLES, parse_duration  # noqa: E402
from src.episode_setup import apply_changes, episode_view  # noqa: E402


def _pairs(values: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        role, sep, value = item.partition("=")
        role = role.strip()
        if not sep or role not in ALL_ROLES:
            raise ValueError(
                f"{flag} vuole RUOLO=VALORE con RUOLO fra {', '.join(ALL_ROLES)}; ricevuto '{item}'."
            )
        out[role] = value
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--titolo", default=None)
    parser.add_argument("--documento", default=None, help="Nome in documenti/ o percorso")
    parser.add_argument("--formato", default=None, help="conversazione | intervista")
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--durata", default=None, help="Ore:minuti, es. 0:15")
    duration.add_argument("--minuti", type=int, default=None)
    parser.add_argument("--voce", action="append", default=[], metavar="RUOLO=VOCE")
    parser.add_argument("--nome", action="append", default=[], metavar="RUOLO=NOME COGNOME")
    parser.add_argument("--descrizione", action="append", default=[], metavar="RUOLO=CHI E'")
    parser.add_argument("--togli", action="append", default=[], metavar="RUOLO", help="Libera un ruolo")
    parser.add_argument(
        "--accetta-nome-incompleto",
        action="store_true",
        help="Conferma esplicita: il nome va pronunciato cosi', senza cognome",
    )
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Cartella della run inesistente: {run_dir}. Chiama prima run_init.py.")

    minutes = parse_duration(args.durata) if args.durata is not None else args.minuti
    changes = dict(
        title=args.titolo,
        document=args.documento,
        script_format=args.formato,
        minutes=minutes,
        voices=_pairs(args.voce, "--voce"),
        names=_pairs(args.nome, "--nome"),
        descriptions=_pairs(args.descrizione, "--descrizione"),
        remove=[r for r in args.togli],
        accept_incomplete_names=args.accetta_nome_incompleto,
    )
    for role in changes["remove"]:
        if role not in ALL_ROLES:
            raise ValueError(f"--togli: ruolo '{role}' sconosciuto. Ammessi: {', '.join(ALL_ROLES)}.")

    warnings: list[str] = []
    episode = None
    revoked = None
    if any(v not in (None, {}, [], False) for k, v in changes.items() if k != "accept_incomplete_names"):
        episode, warnings, revoked = apply_changes(run_dir, config, **changes)
        note("Episodio aggiornato.")
    for warning in warnings:
        note(f"avviso: {warning}")

    view = episode_view(run_dir, config, episode)
    if view["mancanti"]:
        note("Manca ancora: " + ", ".join(m["campo"] + (f" ({m['ruolo']})" if m.get("ruolo") else "") for m in view["mancanti"]))
    emit({**view, "warnings": warnings, "approval_revoked": revoked})


if __name__ == "__main__":
    run_tool(main)
