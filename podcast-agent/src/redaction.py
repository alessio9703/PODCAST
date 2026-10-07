"""Toglie dai messaggi i percorsi che rivelano il server.

Usato da tools/_common.py (JSON d'errore e tutto stderr) e da
src/production.py (eventi della produzione, che arrivano alla UI). Vedi
redact().
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

#: Cartelle configurate (output/, documenti/, ...) da accorciare al loro nome
#: nei messaggi: possono stare fuori dal progetto (es. /srv/podcast/output).
#: Le registra tools/_common.bootstrap() appena letto config.yaml.
KNOWN_ROOTS: dict[str, str] = {}

#: Cosa puo' seguire un percorso: un separatore, una virgoletta, uno spazio,
#: la punteggiatura di una frase o la fine. Serve a non toccare
#: /Users/mario2 quando la home e' /Users/mario.
_PATH_END = r"(?=$|[\\/'\"\s,;:)\]])"


def register_roots(config: Any) -> None:
    for attr in ("output_dir", "documents_dir", "voices_dir", "music_dir"):
        try:
            path = getattr(config, attr)
        except Exception:  # noqa: BLE001 - una cartella non configurata non va accorciata
            continue
        KNOWN_ROOTS[str(path)] = path.name


def _replacements() -> list[tuple[str, str]]:
    """(percorso assoluto, forma corta), dal piu' lungo al piu' corto."""
    pairs = dict(KNOWN_ROOTS)
    pairs[str(PROJECT_ROOT)] = "."
    home = str(Path.home())
    if home and home not in (os.sep, "/"):
        pairs.setdefault(home, "~")
    return sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)


def redact(value: Any) -> Any:
    """Toglie dai messaggi la parte del percorso che rivela il server.

    I messaggi arrivano fino al browser: `/Users/<nome>/.../podcast-agent/
    output/run/x.json` diventa `output/run/x.json`; una cartella configurata
    fuori dal progetto diventa il suo nome; il resto sotto la home diventa
    `~/...`. Vale anche per la forma `repr` dei percorsi Windows nei messaggi
    degli OSError (`'C:\\\\Users\\\\...'`, barre raddoppiate).

    I moduli in src/ usano gia' solo i nomi nei messaggi destinati alla UI:
    questo e' il controllo di riserva per quelli che non lo fanno (revisioni
    di sicurezza C0+C1, BASSA-2, e del diff delle correzioni, BASSA-4). Vale
    per gli errori e per tutti i messaggi su stderr (note()); le risposte di
    successo tengono le chiavi assolute che il subagent usa (vedi il
    contratto).
    """
    if isinstance(value, str):
        for absolute, short in _replacements():
            for variant in {absolute, absolute.replace("\\", "\\\\")}:
                prefix = "" if short == "." else short + "/"
                value = re.sub(re.escape(variant) + r"[\\/]+", lambda _m, p=prefix: p, value)
                value = re.sub(re.escape(variant) + _PATH_END, lambda _m, s=short: s, value)
        return value
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


__all__ = ["KNOWN_ROOTS", "redact", "register_roots"]
