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
    esce con exit 3 `ConsentRequired`, l'elenco delle voci in `details`;
  - **il consenso e' una proprieta' della voce** (D11, revisione finale N1):
    in modalita' web anche il RIUSO di una voce gia' clonata richiede il
    consenso registrato per la registrazione del registro. Fuori dal web
    (subagent, CLI) il riuso blocca solo con il consenso revocato. Una revoca
    vale sempre, anche se registrata solo nel registro (`__revoche__`, che
    resta quando si cancellano i file). L'unico modo di saltare il controllo
    e' `consent_gate=False` (flag --no-consent-gate, rifiutato in modalita'
    web);
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
from .registry import VoiceRegistry, file_hash
from .voice_library import AvailableVoice, VoiceLibrary, VoiceLibraryError
from .voice_meta import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    consent_state,
    consent_view,
    VoiceMetaError,
    load_meta,
    record_consent,
    revocation_token,
    revoke_consent,
    with_registry,
)
from .web_mode import web_mode

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


class RevokedMeanwhile(NeedsUserDecision):
    """Mentre si registrava il consenso, la stessa voce e' stata revocata.

    Consenso e revoca concorrenti: vince sempre la revoca (decisione
    dell'utente, sicurezza C7 BASSA-1). Il consenso non e' registrato; la UI
    ricarica lo stato e, se serve, lo si chiede di nuovo.
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
    #: Anche il riuso richiede il consenso registrato (modalita' web, N1).
    reuse_requires_consent: bool = False

    @property
    def blocked_by_consent(self) -> bool:
        """Il consenso impedisce di usare questa voce."""
        state = self.consent["stato"]
        if state == CONSENT_REVOKED:
            return True
        if self.action == ACTION_CLONE or self.reuse_requires_consent:
            return state != CONSENT_GRANTED
        return False

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


def plan_voices(
    config: Any,
    speakers: list[str],
    *,
    registry: VoiceRegistry | None = None,
    reuse_requires_consent: bool | None = None,
) -> list[VoicePlan]:
    """Per ogni speaker: riuso o clonazione, e lo stato del consenso. Gratis.

    `reuse_requires_consent`: None = in modalita' web si', altrimenti no.
    """
    if reuse_requires_consent is None:
        reuse_requires_consent = web_mode()
    library = _library(config)
    by_name = {v.name: v for v in library.voices}
    doubled = {s: library.duplicate_files(s) for s in speakers if library.duplicate_files(s)}
    if doubled:
        raise VoiceLibraryError(
            "Ci sono piu' registrazioni con lo stesso nome: "
            + "; ".join(f"{name} ({', '.join(files)})" for name, files in doubled.items())
            + ". Tienine una sola per persona: non scelgo io quale usare."
        )
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
        meta = load_meta(voice.path)
        voice_id, reason = registry.lookup(name, voice.path, config.active_provider)
        entry = registry.get(name) or {}
        plans.append(
            VoicePlan(
                speaker=name,
                voice=voice,
                action=ACTION_REUSE if voice_id else ACTION_CLONE,
                voice_id=voice_id,
                reason=reason,
                consent=with_registry(consent_view(voice.path, meta), registry, voice_id=voice_id, voice=name, meta=meta),
                cloned_at=entry.get("cloned_at") if voice_id else None,
                pending=None if voice_id else pending_clone(config, name),
                reuse_requires_consent=reuse_requires_consent,
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
        elif plan.action == ACTION_REUSE:
            lines.append(f"{plan.speaker}: gia' clonata, ma il consenso non e' registrato (da confermare)")
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
                {
                    "speaker": p.speaker,
                    "azione": p.action,
                    "stato": p.consent["stato"],
                    "da_confermare": bool(p.consent.get("da_confermare")),
                    "file": p.voice.path.name,
                }
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


def check_not_revoked(
    config: Any,
    speakers: list[str],
    *,
    voice_map: dict[str, str] | None = None,
    require_consent: bool = False,
) -> None:
    """ConsentRequired (exit 3) se una delle voci non si puo' usare.

    Per i passi che riusano voci gia' clonate o audio gia' sintetizzato
    (rimontaggio, retake, anteprime): non clonano, ma un consenso revocato
    vale anche li' (revisione di sicurezza C3, MEDIA-2).

    `voice_map`: i voice_id della run (voci.json). Una revoca registrata nel
    registro per uno di quei voice_id blocca anche se la registrazione nella
    libreria e' stata cancellata e ricaricata, o la riga del registro tolta
    (revisione finale, N1).
    `require_consent`: blocca anche un consenso non registrato (il retake dal
    web, che sintetizza con la voce: e' un riuso).
    """
    plans = plan_voices(config, list(speakers), reuse_requires_consent=require_consent)
    blocked = [p for p in plans if p.consent["stato"] == CONSENT_REVOKED or (require_consent and p.blocked_by_consent)]
    if blocked:
        check_consent(blocked)
    if voice_map:
        registry = _registry(config)
        revoked = [
            (speaker, voice_id)
            for speaker, voice_id in sorted(voice_map.items())
            if speaker in speakers and registry.revocation_for(voice_id=voice_id)
        ]
        if revoked:
            raise ConsentRequired(
                "Il consenso di queste voci e' stato revocato: "
                + ", ".join(speaker for speaker, _ in revoked)
                + ". La voce clonata usata da questo episodio non si riusa e non si rimonta.",
                details={
                    "voci": [
                        {"speaker": speaker, "azione": ACTION_REUSE, "stato": CONSENT_REVOKED, "revoca_nel_registro": True}
                        for speaker, _ in revoked
                    ]
                },
            )


def _consent_lock_target(config: Any, name: str) -> Path:
    """Lock per voce comune a consenso e revoca (metadati + registro insieme)."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return config.registry_file.parent / f".consenso-{digest}"


def _current_token(config: Any, voice: AvailableVoice) -> str:
    """L'impronta delle revoche della voce ora (vedi voice_meta.revocation_token)."""
    try:
        meta = load_meta(voice.path)
    except VoiceMetaError:
        meta = {"consenso_revocato": "illeggibile"}
    return revocation_token(_registry(config), voice.name, meta)


def grant_consent(
    config: Any,
    voice: AvailableVoice,
    *,
    by: str | None,
    expect_sha256: str | None = None,
    expect_revision: str | None = None,
) -> dict[str, Any]:
    """Registra il consenso (metadati accanto all'audio) e toglie dal registro
    la revoca di QUESTA registrazione: la persona ha dato di nuovo il consenso.

    Vince sempre la revoca (decisione dell'utente). Il consenso vale solo sullo
    stato delle revoche che l'utente ha visto: `expect_revision` e' la
    `consenso.revisione` mostrata (la UI la ripassa con --expect-revision,
    run.py la prende prima di chiedere). Se nel frattempo la voce e' stata
    revocata, anche un istante prima di questa chiamata, exit 3
    RevokedMeanwhile e niente registrato. Senza `expect_revision` (uso locale da
    riga di comando) vale lo stato all'ingresso. Consenso e revoca della stessa
    voce passano dallo stesso lock (sicurezza C7, BASSA-1; seconda tornata,
    MEDIA-1).
    """
    expected = expect_revision or _current_token(config, voice)
    with file_lock(_consent_lock_target(config, voice.name)):
        if _current_token(config, voice) != expected:
            raise RevokedMeanwhile(
                f"Il consenso di {voice.name} e' stato revocato dopo che lo stato e' stato mostrato: "
                "vale la revoca, il consenso non e' stato registrato. Ricarica e, se la persona lo "
                "conferma di nuovo, registralo ancora.",
                details={"voce": voice.name},
            )
        meta = record_consent(voice.path, voice=voice.name, by=by, expect_sha256=expect_sha256)
        _registry(config).lift_revocations(voice.name, meta["consenso"]["audio_sha256"])
    return meta


def _clones_in_runs(config: Any, voice: str) -> list[tuple[str | None, str]]:
    """(sha256, voice_id) della voce in voci_usate.json delle run: anche le
    clonazioni precedenti al registro con lo storico (C7, MEDIA-1)."""
    pairs: list[tuple[str | None, str]] = []
    output = Path(config.output_dir)
    if not output.is_dir():
        return pairs
    for run in sorted(output.iterdir()):
        target = run / "voci_usate.json"
        if run.name.startswith(".") or run.is_symlink() or not run.is_dir() or target.is_symlink() or not target.is_file():
            continue
        try:
            speakers = json.loads(target.read_text(encoding="utf-8")).get("speakers") or {}
        except (OSError, ValueError, AttributeError):
            continue
        row = speakers.get(voice) if isinstance(speakers, dict) else None
        if isinstance(row, dict) and isinstance(row.get("voice_id"), str):
            pairs.append((row.get("audio_hash") if isinstance(row.get("audio_hash"), str) else None, row["voice_id"]))
    return pairs


def _record_revocation(config: Any, registry: VoiceRegistry, voice: str, hashes: list[str | None], by: str, when: str) -> list[dict[str, Any]]:
    """Una revoca per registrazione, ognuna con i voice_id clonati da lei."""
    clones: dict[str | None, list[str]] = {h: [] for h in hashes if h}
    for digest, voice_id in [*registry.clones_of(voice), *_clones_in_runs(config, voice)]:
        clones.setdefault(digest, []).append(voice_id)
    entry = registry.get(voice) or {}
    return registry.record_revocation(voice, clones=clones, provider=entry.get("provider"), by=by, when=when)


def revoke_voice_consent(config: Any, voice: AvailableVoice, *, by: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Revoca il consenso nei metadati E nel registro delle voci.

    Nel registro si scrive una revoca per ogni registrazione nota della voce
    (l'attuale, quella del consenso revocato, quelle clonate in passato, dal
    registro, dal suo storico e da voci_usate.json delle run), ognuna con i
    voice_id clonati da lei. Cosi' la revoca vale anche dopo aver cancellato il
    file audio e il suo .json, e un retake o un mix di una run vecchia non usa
    piu' nessuno di quei voice_id (revisione finale N1; sicurezza C7, ALTA-1 e
    MEDIA-1).
    """
    with file_lock(_consent_lock_target(config, voice.name)):
        meta, previous = revoke_consent(voice.path, voice=voice.name, by=by)
        revocation = meta["consenso_revocato"]
        _record_revocation(
            config, _registry(config), voice.name, [file_hash(voice.path), (previous or {}).get("audio_sha256")],
            revocation["da"], revocation["il"],
        )
    return meta, previous


def revoke_missing_voice(config: Any, name: str, *, by: str | None) -> list[dict[str, Any]]:
    """Revoca una voce la cui registrazione non e' piu' nella libreria.

    Vale solo per un nome ESATTO che il registro conosce (riga o storico): non si
    indovina. Senza file non ci sono metadati da aggiornare, ma la revoca nel
    registro blocca i voice_id clonati (sicurezza C7, MEDIA-1).
    """
    from .voice_meta import _now, _require_by

    who = _require_by(by)
    registry = _registry(config)
    if not registry.knows(name):
        raise VoiceLibraryError(
            f"'{name}' non e' nella libreria e il registro delle voci non lo conosce: niente da revocare.",
        )
    with file_lock(_consent_lock_target(config, name)):
        return _record_revocation(config, _registry(config), name, [], who, _now())


def _read_sample(path: Path, config: Any) -> bytes:
    """I byte del campione da clonare, letti senza seguire link (sicurezza C7, BASSA-3).

    La libreria scarta gia' i link alla scansione, ma fra scansione e lettura il
    file puo' essere sostituito da un link: O_NOFOLLOW lo rifiuta all'apertura.
    Il file deve stare direttamente nella cartella delle voci (niente cartelle
    collegate) e non avere altri nomi (hardlink: un file fuori dalla libreria
    con un secondo nome dentro).
    """
    folder = Path(config.voices_dir).resolve()
    if path.parent.resolve() != folder or path.parent.is_symlink():
        raise VoiceLibraryError(f"{path.name}: non e' direttamente nella cartella delle voci, non lo clono.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise VoiceLibraryError(f"{path.name}: non si apre come file normale (link simbolico?), non lo clono.") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if info.st_nlink > 1:
            raise VoiceLibraryError(
                f"{path.name} ha altri nomi sul disco (hardlink): potrebbe essere un file fuori dalla "
                "libreria. Caricalo come copia, non come collegamento."
            )
        return handle.read()


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
    reuse_requires_consent: bool | None = None,
) -> VoicePrepResult:
    """Riusa o clona le voci. Il consenso si controlla PRIMA di ogni clonazione.

    `reuse_requires_consent`: anche il riuso vuole il consenso registrato (None =
    solo in modalita' web; run.py lo chiede sempre).

    `on_voice(speaker, "riusata" | "clonata")` dopo ogni voce pronta;
    `should_stop()` si controlla fra una clonazione e l'altra (una clonazione
    gia' partita finisce: non si interrompe una chiamata a pagamento a meta').
    Ritorna con le voci pronte fino a quel punto se should_stop() diventa vero:
    il chiamante decide come interrompersi.
    """
    registry = _registry(config)
    plans = plan_voices(config, speakers, registry=registry, reuse_requires_consent=reuse_requires_consent)
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
            # L'attesa sul lock puo' durare minuti: nel frattempo l'approvazione
            # puo' essere stata ritirata, o chiesta l'interruzione (sicurezza
            # C7, BASSA-4). Si ricontrolla prima di spendere.
            if should_stop and should_stop():
                return result
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
            audio = _read_sample(plan.voice.path, config)
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
    "RevokedMeanwhile",
    "confirm_clone_retry",
    "pending_clone",
    "VoicePlan",
    "VoicePrepResult",
    "check_consent",
    "check_not_revoked",
    "grant_consent",
    "revoke_missing_voice",
    "revoke_voice_consent",
    "plan_voices",
    "prepare_voices",
    "resolve_library_voice",
]
