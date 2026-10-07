"""Preparazione delle voci di un episodio: riuso dal registro o clonazione.

Condivisa da tools/voices_prepare.py (subagent, CLI), da tools/estimate.py
(che deve sapere quali voci verranno clonate, senza clonarle) e da
tools/produce.py (interfaccia web).

Regole, le stesse di sempre piu' il consenso:

  - **nomi esatti**: uno speaker che non corrisponde a un file della libreria
    e' un errore, non si indovina;
  - **nessun retry sulla clonazione**: un fallimento del provider esce subito
    (exit 2). Un secondo tentativo creerebbe voci duplicate a pagamento;
  - **consenso prima della clonazione** (piano C2, decisione D5): una voce da
    clonare deve avere il consenso registrato per la registrazione attuale
    (src/voice_meta.py). Se ne manca anche uno solo, NON si clona niente e si
    esce con exit 3 `ConsentRequired`, l'elenco delle voci in `details`. Una
    voce gia' clonata si riusa senza chiedere di nuovo il consenso, a meno che
    sia stato revocato esplicitamente. L'unico modo di saltare il controllo e'
    `consent_gate=False` (flag --no-consent-gate, rifiutato in modalita' web);
  - **una clonazione per voce alla volta**: due produzioni che partono insieme
    con la stessa voce nuova non la clonano due volte. La clonazione avviene
    sotto un lock per voce, e dentro il lock il registro si rilegge: chi arriva
    secondo trova la voce gia' clonata e la riusa.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import AmbiguousName, NeedsUserDecision
from .fileio import file_lock, write_json_atomic
from .registry import VoiceRegistry
from .voice_library import AvailableVoice, VoiceLibrary, VoiceLibraryError
from .voice_meta import CONSENT_GRANTED, CONSENT_REVOKED, consent_state, consent_view

#: Quanto aspettare che un'altra operazione finisca di clonare la stessa voce.
CLONE_LOCK_TIMEOUT_SECONDS = 600

ACTION_REUSE = "reuse"
ACTION_CLONE = "clone"


class ConsentRequired(NeedsUserDecision):
    """Manca il consenso di almeno una voce da usare: nessuna clonazione fatta."""


class CloneOutcomeUnknown(NeedsUserDecision):
    """Una clonazione precedente della stessa voce non si sa come sia finita.

    Puo' aver creato la voce sul provider senza che finisse nel registro (errore
    dopo la creazione, processo ucciso). Ritentare da soli sarebbe un retry
    automatico, con il rischio di una voce duplicata a pagamento: serve che
    l'utente controlli sul provider e confermi (voice_meta.py
    --confirm-clone-retry). Revisione di sicurezza C2, MEDIA-1.
    """


@dataclass
class VoicePlan:
    """Cosa succedera' a una voce, deciso senza clonare niente."""

    speaker: str
    voice: AvailableVoice
    action: str  # ACTION_REUSE | ACTION_CLONE
    voice_id: str | None
    reason: str
    consent: dict[str, Any]
    cloned_at: str | None
    #: Marcatore di una clonazione precedente dall'esito incerto (vedi
    #: CloneOutcomeUnknown), o None.
    pending: dict[str, Any] | None = None

    @property
    def blocked_by_consent(self) -> bool:
        """Il consenso impedisce di usare questa voce."""
        state = self.consent["stato"]
        if state == CONSENT_REVOKED:
            return True
        return self.action == ACTION_CLONE and state != CONSENT_GRANTED

    def view(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "azione": self.action,
            "voice_id": self.voice_id,
            "motivo": self.reason,
            "consenso": self.consent,
            "clonata_il": self.cloned_at,
            "file": self.voice.path.name,
            "clonazione_incerta": self.pending is not None,
        }


@dataclass
class VoicePrepResult:
    voice_map: dict[str, str] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    audio_paths: dict[str, str] = field(default_factory=dict)

    @property
    def newly_cloned(self) -> list[str]:
        return [n for n, r in self.results.items() if r["action"] == "cloned"]

    @property
    def reused(self) -> list[str]:
        return [n for n, r in self.results.items() if r["action"] == "reused"]


def _library(config: Any) -> VoiceLibrary:
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    library.scan()
    return library


def resolve_library_voice(config: Any, query: str) -> AvailableVoice:
    """La voce della libreria con questo nome, senza indovinare.

    Vale la corrispondenza esatta a meno di maiuscole e accenti; un numero
    (indice) non vale. Qualunque altra somiglianza da' AmbiguousName (exit 3)
    con i candidati da proporre all'utente.
    """
    library = _library(config)
    match, candidates = library.resolve(query)
    if match is not None and not query.strip().isdigit():
        return match
    if candidates:
        raise AmbiguousName(
            f"'{query}' non e' un nome certo: candidati "
            + ", ".join(c.name for c in candidates)
            + ". Scegli quello giusto: le voci sono persone reali e non si indovinano.",
            details={"campo": "voce", "query": query, "candidates": [c.name for c in candidates]},
        )
    raise VoiceLibraryError(
        f"'{query}' non corrisponde a nessuna voce della libreria. "
        f"Disponibili: {', '.join(library.names) or 'nessuna'}."
    )


def _registry(config: Any) -> VoiceRegistry:
    return VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)


def plan_voices(config: Any, speakers: list[str], *, registry: VoiceRegistry | None = None) -> list[VoicePlan]:
    """Per ogni speaker: riuso o clonazione, e lo stato del consenso. Gratis."""
    library = _library(config)
    by_name = {v.name: v for v in library.voices}
    unknown = [s for s in speakers if s not in by_name]
    if unknown:
        raise VoiceLibraryError(
            "Questi speaker non corrispondono a nessun file nella cartella voci: "
            + ", ".join(unknown)
            + ".\nDisponibili: "
            + ", ".join(by_name)
            + ".\nUsa voices_list.py --resolve per trovare il nome giusto e "
            "fattelo confermare dall'utente."
        )
    registry = registry or _registry(config)
    plans = []
    for name in speakers:
        voice = by_name[name]
        voice_id, reason = registry.lookup(name, voice.path, config.active_provider)
        entry = registry.get(name) or {}
        plans.append(
            VoicePlan(
                speaker=name,
                voice=voice,
                action=ACTION_REUSE if voice_id else ACTION_CLONE,
                voice_id=voice_id,
                reason=reason,
                consent=consent_view(voice.path),
                cloned_at=entry.get("cloned_at") if voice_id else None,
                pending=None if voice_id else pending_clone(config, name),
            )
        )
    return plans


def check_consent(plans: list[VoicePlan]) -> None:
    """ConsentRequired (exit 3) se anche una sola voce non si puo' usare."""
    blocked = [p for p in plans if p.blocked_by_consent]
    if not blocked:
        return
    lines = []
    for plan in blocked:
        state = plan.consent["stato"]
        if state == CONSENT_REVOKED:
            lines.append(f"{plan.speaker}: consenso revocato")
        elif state == "superato":
            lines.append(f"{plan.speaker}: la registrazione e' cambiata dopo il consenso")
        else:
            lines.append(f"{plan.speaker}: consenso non registrato")
    raise ConsentRequired(
        "Prima di clonare (o riusare) queste voci serve il consenso delle persone: "
        + "; ".join(lines)
        + ". Nessuna voce e' stata clonata. Registra il consenso con "
        "tools/voice_meta.py --name NOME --consent --by CHI, poi riprova.",
        details={
            "voci": [
                {"speaker": p.speaker, "azione": p.action, "stato": p.consent["stato"], "file": p.voice.path.name}
                for p in blocked
            ]
        },
    )


@contextmanager
def _snapshot(audio_path: Path, data: bytes) -> Iterator[Path]:
    """Una copia privata dei byte da clonare, accanto alla registrazione.

    Il provider legge da un percorso: se leggesse il file della libreria,
    potrebbe ricevere un audio sostituito dopo il controllo del consenso.
    Il nome comincia con '.' (la libreria lo ignora) e tiene l'estensione,
    che il provider usa per riconoscere il formato.
    """
    fd, name = tempfile.mkstemp(prefix=f".{audio_path.stem}.clonazione.", suffix=audio_path.suffix, dir=str(audio_path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        yield Path(name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _clone_lock_target(config: Any, name: str) -> Path:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return config.registry_file.parent / f".clonazione-{digest}"


def _clone_marker(config: Any, name: str) -> Path:
    """Il marcatore "clonazione in corso" della voce, accanto al suo lock.

    Si scrive prima di chiamare il provider e si toglie solo dopo che la voce
    e' nel registro: se resta, l'esito di quella clonazione non e' noto.
    """
    target = _clone_lock_target(config, name)
    return target.with_name(target.name + ".json")


def pending_clone(config: Any, name: str) -> dict[str, Any] | None:
    """Il marcatore di una clonazione dall'esito incerto, o None."""
    marker = _clone_marker(config, name)
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def confirm_clone_retry(config: Any, name: str) -> dict[str, Any] | None:
    """L'utente ha controllato sul provider: si puo' clonare di nuovo.

    Toglie il marcatore sotto il lock per voce (non mentre un'altra
    clonazione e' in corso). Ritorna il marcatore tolto, o None.
    """
    with file_lock(_clone_lock_target(config, name), timeout=CLONE_LOCK_TIMEOUT_SECONDS):
        previous = pending_clone(config, name)
        _clone_marker(config, name).unlink(missing_ok=True)
    return previous


@contextmanager
def _signals_deferred() -> Iterator[None]:
    """SIGTERM e SIGINT rinviati alla fine del blocco (clonazione + registro).

    Un'interruzione fra la creazione della voce sul provider e la sua
    registrazione lascerebbe una voce pagata e sconosciuta. Il segnale non si
    perde: arriva appena il blocco finisce. Solo dove il sistema lo consente
    (non su Windows) e nel thread principale; il marcatore copre il resto
    (SIGKILL compreso).
    """
    mask = getattr(signal, "pthread_sigmask", None)
    if mask is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    blocked = {signal.SIGTERM, signal.SIGINT}
    previous = mask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        mask(signal.SIG_SETMASK, previous)


def prepare_voices(
    config: Any,
    speakers: list[str],
    *,
    dry_run: bool = False,
    consent_gate: bool = True,
    provider: Any = None,
    on_voice: Callable[[str, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    note: Callable[[str], None] | None = None,
) -> VoicePrepResult:
    """Riusa o clona le voci. Il consenso si controlla PRIMA di ogni clonazione.

    `on_voice(speaker, "riusata" | "clonata")` dopo ogni voce pronta;
    `should_stop()` si controlla fra una clonazione e l'altra (una clonazione
    gia' partita finisce: non si interrompe una chiamata a pagamento a meta').
    Ritorna con le voci pronte fino a quel punto se should_stop() diventa vero:
    il chiamante decide come interrompersi.
    """
    registry = _registry(config)
    plans = plan_voices(config, speakers, registry=registry)
    if consent_gate and not dry_run:
        check_consent(plans)

    result = VoicePrepResult(audio_paths={p.speaker: str(p.voice.path) for p in plans})
    for plan in plans:
        name = plan.speaker
        if plan.action == ACTION_REUSE:
            result.results[name] = {"voice_id": plan.voice_id, "action": "reused", "reason": plan.reason}
            result.voice_map[name] = plan.voice_id  # type: ignore[assignment]
            if note:
                note(f"{name}: {plan.reason}")
            if on_voice:
                on_voice(name, "riusata")
            continue
        if dry_run:
            result.results[name] = {
                "voice_id": None,
                "action": "would_clone",
                "reason": plan.reason,
                "consent": plan.consent["stato"],
            }
            if note:
                note(f"{name}: da clonare ({plan.reason})")
            continue
        if should_stop and should_stop():
            return result

        with file_lock(_clone_lock_target(config, name), timeout=CLONE_LOCK_TIMEOUT_SECONDS):
            # Un'altra operazione puo' averla clonata mentre si aspettava.
            registry.reload()
            voice_id, reason = registry.lookup(name, plan.voice.path, config.active_provider)
            if voice_id:
                result.results[name] = {
                    "voice_id": voice_id,
                    "action": "reused",
                    "reason": "clonata nel frattempo da un'altra operazione, riuso il voice_id",
                }
                result.voice_map[name] = voice_id
                if on_voice:
                    on_voice(name, "riusata")
                continue
            if provider is None:
                provider = config.build_provider()
            # I byte da clonare si leggono UNA volta, e su quei byte si
            # ricontrolla il consenso: fra il controllo iniziale e questo
            # momento possono passare minuti (le clonazioni prima di questa),
            # durante i quali il consenso puo' essere revocato o la
            # registrazione sostituita (revisione di sicurezza C2, ALTA-1).
            audio = plan.voice.path.read_bytes()
            audio_hash = "sha256:" + hashlib.sha256(audio).hexdigest()
            if consent_gate:
                state = consent_state(plan.voice.path, audio_sha256=audio_hash)
                if state != CONSENT_GRANTED:
                    plan.consent = {**plan.consent, "stato": state}
                    check_consent([plan])
            pending = pending_clone(config, name)
            if pending is not None:
                raise CloneOutcomeUnknown(
                    f"Una clonazione precedente di {name} (dal {pending.get('dal', '?')}) non si sa "
                    "come sia finita: la voce potrebbe esistere gia' sul provider. Controlla le voci "
                    "sul provider, poi conferma con tools/voice_meta.py --name NOME "
                    "--confirm-clone-retry --by CHI. Non ritento da solo.",
                    details={"speaker": name, "dal": pending.get("dal"), "provider": pending.get("provider")},
                )
            if note:
                note(f"{name}: clono la voce su '{provider.name}' ({reason})...")
            marker = _clone_marker(config, name)
            write_json_atomic(
                marker,
                {
                    "voce": name,
                    "audio_sha256": audio_hash,
                    "provider": provider.name,
                    "dal": datetime.now().replace(microsecond=0).isoformat(),
                },
            )
            with _signals_deferred():
                with _snapshot(plan.voice.path, audio) as snapshot:
                    # Nessun retry: un fallimento qui esce con codice 2 e il
                    # marcatore resta (esito incerto).
                    new_id = provider.clone_voice(name, str(snapshot))
                entry = registry.record(name, plan.voice.path, provider.name, new_id, audio_hash=audio_hash)
                marker.unlink(missing_ok=True)
        result.results[name] = {"voice_id": entry.voice_id, "action": "cloned", "reason": reason}
        result.voice_map[name] = entry.voice_id
        if on_voice:
            on_voice(name, "clonata")
    return result


__all__ = [
    "ACTION_CLONE",
    "ACTION_REUSE",
    "CloneOutcomeUnknown",
    "ConsentRequired",
    "confirm_clone_retry",
    "pending_clone",
    "VoicePlan",
    "VoicePrepResult",
    "check_consent",
    "plan_voices",
    "prepare_voices",
    "resolve_library_voice",
]
