"""Stato della scrittura del copione, persistito su disco.

Perche' esiste: con `run.py` la conversazione con Claude vive in memoria per
tutta la sessione, quindi `ScriptWriter.revise()` ha sempre il contesto sotto
mano. Il subagent invece chiama scrittura e revisione come **processi
separati**: fra una chiamata e l'altra la memoria non c'e' piu'.

La sessione NON e' un dump della conversazione. Tiene:

  - il prompt iniziale (documento + speaker), cioe' il "compito";
  - SOLO l'ultimo copione prodotto, non tutte le bozze intermedie;
  - l'elenco delle istruzioni di revisione gia' date.

Da questi tre pezzi `seed_messages()` ricostruisce la conversazione a ogni
chiamata. Cosi' il contesto resta (il modello sa cosa gli hai gia' chiesto di
cambiare) ma il costo in token non cresce a ogni giro, perche' le bozze
scartate non rientrano nel contesto.

Il file finisce nella cartella dell'episodio: e' anche la storia leggibile di
come ci sei arrivato.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .script_writer import PodcastScript

FILENAME = "_script_session.json"


class ScriptSessionError(RuntimeError):
    """File di sessione mancante, illeggibile o incoerente."""


@dataclass
class ScriptSession:
    document_path: str
    document_text: str
    speakers: list[str]
    prompt: str
    #: Formato del copione: "conversazione" (default) o "intervista". In
    #: intervista `interviewer` dice quale speaker fa le domande. Restano nella
    #: sessione perche' il formato e' una scelta dell'utente come le voci: va
    #: ritrovata alla revisione, non richiesta a ogni salvataggio.
    script_format: str = "conversazione"
    interviewer: str | None = None
    #: {"intro": {"name", "sha256"}, "outro": {...}} — solo le categorie con
    #: parlato: true al momento della prima stesura. Serve SOLO al confronto
    #: sha256 di mix_episode() ("il copione era stato scritto per un'altra
    #: intro/outro"): non rientra nel prompt ricostruito da seed_messages(),
    #: che usa gia' il contesto musicale perche' e' stato scritto dentro
    #: `prompt` da ScriptWriter.build_prompt() quando il copione e' nato.
    music_context: dict[str, Any] | None = None
    current_script: dict[str, Any] | None = None
    #: Le lezioni ATTIVE ({"id", "data"}) al momento in cui questo episodio e'
    #: stato scritto, catturate una volta sola alla creazione della sessione
    #: (stesso principio di music_context: e' una fotografia del contesto con
    #: cui il copione e' nato, non uno stato da tenere sincronizzato). Serve a
    #: sapere, a distanza di mesi, con quali regole e' stato scritto un
    #: episodio — vedi src/lessons.py.
    active_lessons: list[dict[str, str]] = field(default_factory=list)
    revisions: list[dict[str, str]] = field(default_factory=list)
    #: storico completo delle bozze. Sta su disco, non in contesto: vedi
    #: l'avvertenza su seed_messages().
    versions: list[dict[str, Any]] = field(default_factory=list)
    #: {version, sha256, at} del copione approvato dall'utente, oppure None
    approved: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
    path: Path | None = None

    # -- ciclo di vita ----------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        document_path: str | Path,
        document_text: str,
        speakers: list[str],
        prompt: str,
        path: Path,
        script_format: str = "conversazione",
        interviewer: str | None = None,
        music_context: dict[str, Any] | None = None,
        active_lessons: list[dict[str, str]] | None = None,
    ) -> "ScriptSession":
        now = _now()
        return cls(
            document_path=str(document_path),
            document_text=document_text,
            speakers=list(speakers),
            prompt=prompt,
            script_format=script_format,
            interviewer=interviewer,
            music_context=music_context,
            active_lessons=list(active_lessons or []),
            created_at=now,
            updated_at=now,
            path=Path(path),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ScriptSession":
        session_path = Path(path)
        if not session_path.is_file():
            raise ScriptSessionError(
                f"Sessione non trovata: {session_path}\n"
                "Serve il file creato dalla scrittura del copione. Se non c'e', "
                "riparti dalla scrittura invece che dalla revisione."
            )
        try:
            raw = json.loads(session_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ScriptSessionError(f"{session_path} non e' un JSON valido: {exc}") from exc

        missing = {"document_path", "speakers", "prompt"} - set(raw)
        if missing:
            raise ScriptSessionError(
                f"{session_path} non contiene i campi {sorted(missing)}: "
                "non e' un file di sessione valido."
            )
        return cls(
            document_path=raw["document_path"],
            document_text=raw.get("document_text", ""),
            speakers=list(raw["speakers"]),
            prompt=raw["prompt"],
            current_script=raw.get("current_script"),
            active_lessons=list(raw.get("active_lessons", [])),
            revisions=list(raw.get("revisions", [])),
            versions=list(raw.get("versions", [])),
            approved=raw.get("approved"),
            script_format=raw.get("script_format", "conversazione"),
            interviewer=raw.get("interviewer"),
            music_context=raw.get("music_context"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            path=session_path,
        )

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ScriptSessionError("Nessun percorso indicato per salvare la sessione.")
        self.path = target
        self.updated_at = _now()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(target)
        return target

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_path": self.document_path,
            "document_text": self.document_text,
            "speakers": self.speakers,
            "prompt": self.prompt,
            "script_format": self.script_format,
            "interviewer": self.interviewer,
            "music_context": self.music_context,
            "current_script": self.current_script,
            "active_lessons": self.active_lessons,
            "revisions": self.revisions,
            "versions": self.versions,
            "approved": self.approved,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # -- aggiornamento ----------------------------------------------------

    def record(self, script: PodcastScript, instruction: str | None = None) -> None:
        """Registra una nuova versione del copione.

        Ogni salvataggio crea una voce in `versions[]`. L'approvazione NON
        viene cancellata qui: resta registrata con lo sha256 del copione che
        l'utente ha visto, e il gate confronta quell'hash con il copione che
        sta per essere sintetizzato. Cosi' un "si'" vale sul contenuto
        approvato, non sul file — e vale anche se il file viene riscritto
        aggirando questo modulo.
        """
        payload = script.to_dict()
        if instruction:
            self.revisions.append({"at": _now(), "instruction": instruction.strip()})
        self.versions.append(
            {
                "version": len(self.versions) + 1,
                "at": _now(),
                "instruction": instruction.strip() if instruction else None,
                "sha256": script_hash(payload),
                "script": payload,
            }
        )
        self.current_script = payload

    @property
    def revision_count(self) -> int:
        return len(self.revisions)

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def current_hash(self) -> str | None:
        return script_hash(self.current_script) if self.current_script else None

    # -- approvazione -----------------------------------------------------

    def approve(self) -> dict[str, Any]:
        """Registra l'approvazione dell'utente sul copione CORRENTE."""
        if self.current_script is None:
            raise ScriptSessionError("Non c'e' nessun copione da approvare.")
        self.approved = {
            "version": self.version_count,
            "sha256": self.current_hash,
            "at": _now(),
        }
        return self.approved

    def approval_problem(self, script: dict[str, Any] | None = None) -> str | None:
        """Perche' la sintesi non puo' partire. None = via libera.

        `script` e' il copione che si sta per sintetizzare, letto dal disco.
        Il confronto e' sul suo sha256, non su un flag: e' questo che impedisce
        sia di far valere un "si'" dato su una bozza precedente, sia di
        aggirare il checkpoint riscrivendo conversazione.json a mano.
        """
        target = script if script is not None else self.current_script
        if target is None:
            return "La sessione non contiene nessun copione."
        if not self.approved:
            return (
                "Il copione non e' stato approvato dall'utente.\n"
                "Mostraglielo per intero, chiedi conferma esplicita, poi registra "
                "l'approvazione con tools/script_approve.py."
            )
        if self.approved.get("sha256") != script_hash(target):
            approved_version = self.approved.get("version")
            return (
                "Il copione e' cambiato DOPO l'approvazione: l'utente ha "
                f"approvato la versione {approved_version}, quello che stai per "
                "sintetizzare e' diverso.\n"
                "Rimostraglielo e fatti dare una nuova conferma: un si' vale sul "
                "copione che ha letto, non sul file."
            )
        return None

    # -- ricostruzione della conversazione --------------------------------

    def seed_messages(self, next_instruction: str) -> list[dict[str, str]]:
        """Ricostruisce i `messages` per una revisione.

        user(compito) -> assistant(copione attuale) -> user(istruzione nuova,
        preceduta dall'elenco di quelle gia' applicate).

        ATTENZIONE: usa SOLO `current_script`, mai `versions[]`. Lo storico
        delle bozze sta su disco perche' li' e' gratis; rimetterlo in
        contesto farebbe ripagare ogni bozza scartata a ogni revisione
        successiva del percorso API.
        """
        if self.current_script is None:
            raise ScriptSessionError(
                "La sessione non contiene ancora nessun copione: non c'e' niente "
                "da revisionare."
            )

        already = ""
        if self.revisions:
            listed = "\n".join(
                f"{i}. {r['instruction']}" for i, r in enumerate(self.revisions, start=1)
            )
            already = (
                "Revisioni gia' applicate a questo copione, da non annullare:\n"
                f"{listed}\n\n"
            )

        return [
            {"role": "user", "content": self.prompt},
            {
                "role": "assistant",
                "content": json.dumps(self.current_script, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": (
                    f"{already}Rivedi il copione secondo questa indicazione e "
                    "restituiscilo per intero, non solo le parti modificate:\n\n"
                    f"{next_instruction.strip()}"
                ),
            },
        ]

    def script(self) -> PodcastScript:
        if self.current_script is None:
            raise ScriptSessionError("La sessione non contiene ancora un copione.")
        return PodcastScript.from_dict(self.current_script)


def script_hash(script: dict[str, Any]) -> str:
    """sha256 del copione, calcolato su una serializzazione canonica."""
    canonical = json.dumps(script, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


__all__ = ["ScriptSession", "ScriptSessionError", "script_hash", "FILENAME"]
