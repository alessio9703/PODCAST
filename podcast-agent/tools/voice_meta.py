#!/usr/bin/env python3
"""Metadati di una voce della libreria: consenso alla clonazione.

    python tools/voice_meta.py --name "Mario Rossi"
    python tools/voice_meta.py --name "Mario Rossi" --consent --by "Utente A"
    python tools/voice_meta.py --name "Mario Rossi" --revoke-consent --by "Utente A"
    python tools/voice_meta.py --name "Mario Rossi" --confirm-clone-retry --by "Utente A"
    python tools/voice_meta.py --name "Voce Mario" --spoken-name "Mario Rossi" --by "Utente A"

Il consenso si registra SOLO dopo che la persona lo ha dato davvero: e' la
casella "Confermo che [Nome Cognome] ha dato il consenso..." del passo 5. Vale
per la registrazione attuale: se il file audio viene sostituito, va registrato
di nuovo (stato "superato").

--expect-sha256 lega il consenso alla registrazione che l'utente ha visto
(`consenso.audio_sha256` della stima o di questo comando): se il file e'
cambiato nel frattempo, exit 3 RecordingChanged e niente registrato. In
modalita' web e' obbligatorio con --consent.

--confirm-clone-retry: dopo una clonazione dall'esito incerto (exit 3
CloneOutcomeUnknown) l'utente ha controllato le voci sul provider e accetta
di clonare di nuovo.

--spoken-name: nome e cognome come vanno pronunciati (la "Nuova voce" della
libreria Voci). Senza cognome exit 3 SpokenNameIncomplete, a meno di
--accept-incomplete-name. Si puo' passare insieme a --consent.

Senza azioni mostra lo stato e basta. Il nome deve
corrispondere a una voce della libreria (maiuscole e accenti a parte): un nome
incerto da' exit 3 AmbiguousName con i candidati, mai una scelta al posto
dell'utente.
"""

from __future__ import annotations

import argparse

from _common import InvalidArguments, add_config_arg, bootstrap, emit, note, parse_args, require_by_in_web, run_tool, web_mode  # noqa: E402
from src.errors import DomainError  # noqa: E402
from src.registry import VoiceRegistry  # noqa: E402
from src.voice_meta import consent_view, load_meta, record_spoken_name, with_registry  # noqa: E402
from src.voice_prep import (  # noqa: E402
    confirm_clone_retry,
    grant_consent,
    pending_clone,
    resolve_library_voice,
    revoke_by_name,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Nome della voce nella libreria")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--consent", action="store_true", help="Registra il consenso della persona")
    action.add_argument("--revoke-consent", action="store_true", help="Revoca il consenso")
    action.add_argument(
        "--confirm-clone-retry",
        action="store_true",
        help="Dopo una clonazione dall'esito incerto: verificato sul provider, si puo' ritentare",
    )
    parser.add_argument("--by", default=None, help="Chi registra o revoca il consenso")
    parser.add_argument("--expect-sha256", default=None, help="sha256 della registrazione che l'utente ha visto")
    parser.add_argument(
        "--expect-revision", default=None, help="consenso.revisione mostrata all'utente: una revoca successiva vince"
    )
    parser.add_argument("--spoken-name", default=None, help="Nome e cognome come vanno pronunciati")
    parser.add_argument(
        "--accept-incomplete-name", action="store_true", help="Conferma un nome pronunciato senza cognome"
    )
    add_config_arg(parser)
    args = parse_args(parser)

    if args.spoken_name is not None:
        require_by_in_web(args.by, "la conferma del nome da pronunciare")
    config = bootstrap(args)
    if args.consent and web_mode() and not args.expect_sha256:
        raise InvalidArguments(
            "Dall'interfaccia il consenso si registra solo con --expect-sha256: lo sha256 della "
            "registrazione che l'utente ha visto.",
            details={"messaggio": "manca --expect-sha256"},
        )
    if args.consent and web_mode() and not args.expect_revision:
        raise InvalidArguments(
            "Dall'interfaccia il consenso si registra solo con --expect-revision: la consenso.revisione "
            "dello stato che l'utente ha visto (una revoca arrivata dopo vince).",
            details={"messaggio": "manca --expect-revision"},
        )
    if args.revoke_consent:
        if args.spoken_name is not None:
            # Una revoca non si combina con altro: il nome pronunciato verrebbe
            # ignorato (sicurezza C7, quinta revisione, BASSA-4).
            raise InvalidArguments(
                "--revoke-consent non si combina con --spoken-name: sono due richieste separate.",
                details={"messaggio": "--revoke-consent con --spoken-name"},
            )
        _revoke(config, args)
        return
    voice = resolve_library_voice(config, args.name)

    revoked = None
    cleared = None
    if args.spoken_name is not None:
        # Prima del consenso: un nome incompleto (exit 3) non lascia un consenso
        # registrato a meta' richiesta.
        record_spoken_name(
            voice.path, voice=voice.name, name=args.spoken_name, by=args.by,
            accept_incomplete=args.accept_incomplete_name,
        )
        note(f"Nome pronunciato di {voice.name}: {' '.join(args.spoken_name.split())}.")
    if args.consent:
        grant_consent(config, voice, by=args.by, expect_sha256=args.expect_sha256, expect_revision=args.expect_revision)
        note(f"Consenso registrato per {voice.name}.")
    elif args.confirm_clone_retry:
        if not args.by or not args.by.strip():
            raise DomainError("Serve --by: chi ha controllato sul provider.", error_type="ByRequired", details={"campo": "by"})
        cleared = confirm_clone_retry(config, voice.name)
        note(
            f"{voice.name}: la prossima produzione potra' clonare di nuovo (confermato da {args.by.strip()})."
            if cleared is not None
            else f"{voice.name}: nessuna clonazione dall'esito incerto."
        )

    meta = load_meta(voice.path)
    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    voice_id, _ = registry.lookup(voice.name, voice.path, config.active_provider)
    emit(
        {
            "voce": voice.name,
            "file": voice.path.name,
            "nome_pronunciato": meta.get("nome_pronunciato"),
            "consenso": with_registry(consent_view(voice.path, meta), registry, voice_id=voice_id, voice=voice.name, meta=meta),
            "consenso_revocato": revoked,
            "clonazione_incerta": pending_clone(config, voice.name),
            "clonazione_incerta_tolta": cleared,
        }
    )


def _revoke(config, args) -> None:  # type: ignore[no-untyped-def]
    """--revoke-consent: revoca per nome (src/voice_prep.revoke_by_name).

    Si revoca tutto cio' che porta quel nome: le registrazioni della libreria
    (doppioni compresi) e i nomi del registro che si normalizzano uguale, anche
    di voci gia' cancellate. Con una sola registrazione la risposta e' la vista
    solita della voce; altrimenti un riepilogo con `files`.
    """
    result = revoke_by_name(config, args.name, by=args.by)
    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    extra = {"files": result["files"], "revoche_registro": result["registry_items"], "nomi_registro": result["registry_names"]}
    if result["registry_names"]:
        note(f"Revocate anche le voci gia' cancellate con lo stesso nome: {', '.join(result['registry_names'])}.")
    if len(result["voices"]) == 1:
        voice = result["voices"][0]
        previous = result["previous"][voice.path.name]
        note(
            f"Consenso revocato per {voice.name}: la voce non verra' piu' clonata ne' riusata."
            if previous
            else f"Per {voice.name} non c'era un consenso registrato: segnata come revocata."
        )
        meta = load_meta(voice.path)
        voice_id, _ = registry.lookup(voice.name, voice.path, config.active_provider)
        emit({
            "voce": voice.name,
            "file": voice.path.name,
            "nome_pronunciato": meta.get("nome_pronunciato"),
            "consenso": with_registry(consent_view(voice.path, meta), registry, voice_id=voice_id, voice=voice.name, meta=meta),
            "consenso_revocato": previous,
            "clonazione_incerta": pending_clone(config, voice.name),
            "clonazione_incerta_tolta": None,
            **extra,
        })
        return
    note(f"{args.name}: revocato tutto cio' che porta questo nome ({', '.join(result['files']) or 'nessun file in libreria'}).")
    emit({
        "voce": args.name, "file": None, "nome_pronunciato": None,
        "consenso": {"stato": "revocato", "revoca_nel_registro": True, "da_confermare": False},
        "consenso_revocato": None, "clonazione_incerta": None, "clonazione_incerta_tolta": None,
        **extra,
    })


if __name__ == "__main__":
    run_tool(main)
