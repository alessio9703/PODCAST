"""Modalita' web: l'adattatore imposta SEMPRE PODCAST_AGENT_WEB=1.

Vive in src/ (e non solo in tools/_common.py) perche' alcune regole di dominio
cambiano con la modalita': dal web anche il RIUSO di una voce gia' clonata
richiede un consenso registrato (revisione finale, N1).
"""

from __future__ import annotations

import os

#: Variabile d'ambiente che l'adattatore web imposta SEMPRE (a 1).
WEB_MODE_ENV = "PODCAST_AGENT_WEB"


def web_mode() -> bool:
    """True se PODCAST_AGENT_WEB e' impostata (qualunque valore che non sia 0/false/no).

    Nel dubbio vale come accesa: un valore scritto male dall'adattatore deve
    togliere poteri, non darli.
    """
    value = os.environ.get(WEB_MODE_ENV, "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


__all__ = ["WEB_MODE_ENV", "web_mode"]
