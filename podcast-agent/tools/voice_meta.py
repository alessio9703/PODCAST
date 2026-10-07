#!/usr/bin/env python3
"""Metadati di una voce della libreria: consenso alla clonazione.

    python tools/voice_meta.py --name "Mario Rossi"
    python tools/voice_meta.py --name "Mario Rossi" --consent --by "Utente A"
    python tools/voice_meta.py --name "Mario Rossi" --revoke-consent --by "Utente A"

Il consenso si registra SOLO dopo che la persona lo ha dato davvero: e' la
casella "Confermo che [Nome Cognome] ha dato il consenso..." del passo 5. Vale
per la registrazione attuale: se il file audio viene sostituito, va registrato
di nuovo (stato "superato").

Senza --consent / --revoke-consent mostra lo stato e basta. Il nome deve
corrispondere a una voce della libreria (maiuscole e accenti a parte): un nome
incerto da' exit 3 AmbiguousName con i candidati, mai una scelta al posto
dell'utente.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, parse_args, run_tool  # noqa: E402
from src.voice_meta import consent_view, load_meta, record_consent, revoke_consent  # noqa: E402
from src.voice_prep import resolve_library_voice  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Nome della voce nella libreria")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--consent", action="store_true", help="Registra il consenso della persona")
    action.add_argument("--revoke-consent", action="store_true", help="Revoca il consenso")
    parser.add_argument("--by", default=None, help="Chi registra o revoca il consenso")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    voice = resolve_library_voice(config, args.name)

    revoked = None
    if args.consent:
        record_consent(voice.path, voice=voice.name, by=args.by)
        note(f"Consenso registrato per {voice.name}.")
    elif args.revoke_consent:
        _, revoked = revoke_consent(voice.path, voice=voice.name, by=args.by)
        note(
            f"Consenso revocato per {voice.name}: la voce non verra' piu' clonata ne' riusata."
            if revoked
            else f"Per {voice.name} non c'era un consenso registrato: segnata come revocata."
        )

    meta = load_meta(voice.path)
    emit(
        {
            "voce": voice.name,
            "file": voice.path.name,
            "consenso": consent_view(voice.path, meta),
            "consenso_revocato": revoked,
        }
    )


if __name__ == "__main__":
    run_tool(main)
