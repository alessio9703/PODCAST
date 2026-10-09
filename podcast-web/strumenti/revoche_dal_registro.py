"""Revoche del consenso da riapplicare dopo il ripristino di un backup (DEPLOY.md §7).

Un backup precedente a una revoca riporterebbe la voce come "consentita". Il registro
attivita' (tenuto anche fuori dal server) dice quali revoche sono avvenute dopo: questo
script le elenca e stampa i comandi per riapplicarle con l'agente, PRIMA di riavviare il
controller. Non esegue niente da solo.

    .venv/bin/python -m strumenti.revoche_dal_registro REGISTRO.jsonl --dopo 2026-10-09T02:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def revoche(registro: Path, dopo: datetime) -> Iterator[dict[str, Any]]:
    with registro.open(encoding="utf-8") as f:
        for numero, riga in enumerate(f, 1):
            try:
                voce = json.loads(riga)
                quando = datetime.fromisoformat(voce["at"])
            except (ValueError, KeyError, TypeError):
                print(f"riga {numero} illeggibile: controllala a mano", file=sys.stderr)
                continue
            if voce.get("azione") == "revoca_consenso" and voce.get("esito") in ("ok", "RevocationIncomplete") and quando > dopo:
                yield voce


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("registro", type=Path)
    parser.add_argument("--dopo", required=True, help="Istante del backup ripristinato (ISO 8601 con fuso)")
    args = parser.parse_args()
    dopo = datetime.fromisoformat(args.dopo)
    if dopo.tzinfo is None:
        print("--dopo deve avere il fuso orario (es. +00:00): il registro e' in UTC", file=sys.stderr)
        return 2
    trovate = list(revoche(args.registro, dopo))
    if not trovate:
        print("Nessuna revoca dopo il backup.")
        return 0
    print(f"# {len(trovate)} revoche da riapplicare, dalla cartella di podcast-agent:")
    for voce in trovate:
        nome = (voce.get("dettagli") or {}).get("nome")
        if not nome:
            print(f"# {voce['at']}: revoca senza nome della voce nel registro: controllala a mano", file=sys.stderr)
            continue
        chi = f"{voce.get('nome') or voce.get('utente')} (riapplicata dopo ripristino)"
        print(f"# {voce['at']} · revocata da {voce.get('nome')} ({voce.get('utente')})")
        print("PODCAST_AGENT_WEB=1 .venv/bin/python tools/voice_meta.py "
              f"{shlex.quote('--name=' + nome)} --revoke-consent {shlex.quote('--by=' + chi)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
