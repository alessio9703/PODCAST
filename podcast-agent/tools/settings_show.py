#!/usr/bin/env python3
"""Le impostazioni (config.yaml) in sola lettura, per lo schermo "Impostazioni".

    python tools/settings_show.py

Solo una whitelist di chiavi (src/settings_view.py): mai chiavi API, mai nomi
o valori di variabili d'ambiente, mai percorsi assoluti. Gratis.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_tool  # noqa: E402
from src.settings_view import settings_view  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_config_arg(parser)
    args = parse_args(parser)
    emit({"impostazioni": settings_view(bootstrap(args))})


if __name__ == "__main__":
    run_tool(main)
