"""Gate di approvazione condiviso dai due passi che costano.

Sia la clonazione delle voci sia la sintesi spendono. Entrambe passano di qui:
o la sessione dice che l'utente ha approvato ESATTAMENTE quel copione, oppure
il comando deve portare `--no-approval-gate` in chiaro.

Il gate non e' un flag nella sessione ma un confronto di sha256 col copione che
sta per essere usato: cosi' vale anche se conversazione.json viene riscritto
aggirando script_save.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Importato per il suo effetto collaterale: _common mette la radice del
# progetto in sys.path. Senza, `src` non e' importabile quando questo modulo
# viene caricato per primo.
from _common import PROJECT_ROOT  # noqa: F401
from src.script_session import FILENAME as SESSION_FILENAME
from src.script_session import ScriptSession


def add_gate_args(parser: argparse.ArgumentParser, *, run_dir_required: bool) -> None:
    if not run_dir_required:
        parser.add_argument(
            "--run-dir", default=None, help="Cartella dell'episodio (per il gate)"
        )
    parser.add_argument(
        "--no-approval-gate",
        action="store_true",
        help="Salta il controllo di approvazione. Solo per uso manuale e CI: "
        "in una sessione con l'utente questo flag non va usato.",
    )


def enforce(args: argparse.Namespace, script: dict | None = None) -> dict:
    """Blocca se manca l'approvazione. Ritorna lo stato per il JSON di risposta."""
    if getattr(args, "no_approval_gate", False):
        return {"approval_gate": "bypassed"}

    run_dir = getattr(args, "run_dir", None)
    if not run_dir:
        raise ValueError(
            "Manca --run-dir: senza non posso verificare che l'utente abbia "
            "approvato il copione.\n"
            "Passa --run-dir output/<run>, oppure --no-approval-gate se stai "
            "lavorando a mano fuori da una sessione con l'utente."
        )

    session_path = Path(run_dir).expanduser().resolve() / SESSION_FILENAME
    if not session_path.is_file():
        raise ValueError(
            f"Nessuna sessione in {session_path.parent}: non risulta nessun copione "
            "approvato.\n"
            "Il copione va salvato con script_save.py, mostrato all'utente e "
            "approvato con script_approve.py prima di spendere."
        )

    session = ScriptSession.load(session_path)

    if script is None:
        script_file = session_path.parent / "conversazione.json"
        if script_file.is_file():
            script = json.loads(script_file.read_text(encoding="utf-8"))

    problem = session.approval_problem(script)
    if problem:
        raise ValueError(f"Passo bloccato: {problem}")

    return {
        "approval_gate": "passed",
        "approved_version": (session.approved or {}).get("version"),
        "approved_at": (session.approved or {}).get("at"),
    }


__all__ = ["add_gate_args", "enforce"]
