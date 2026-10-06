"""Le scelte dell'utente per un episodio, salvate PRIMA del copione.

Perche' esiste: nell'interfaccia web un episodio nasce come bozza e si
costruisce in piu' passi (documento, cast e formato, musiche), anche in giorni
diversi, e dall'Archivio si riprende da dove ci si era fermati. Le scelte di
quei passi vanno quindi salvate nella run subito, non solo quando il copione
viene scritto, che e' quello che fa oggi la sessione (_script_session.json).

`episodio.json` contiene SOLO scelte dell'utente, nessuno stato:

    {
      "titolo": "Il costo della pioggia",
      "documento": {"nome", "percorso", "sha256", "tipo", "pagine", "parole", "caratteri"},
      "formato": "intervista",
      "durata_minuti": 15,
      "ruoli": {
        "presentatore":   {"voce": "Voce Alberto Tivoli", "nome_pronunciato": "Alberto Tivoli", "descrizione": "..."},
        "intervistatore": {"voce": "...", "nome_pronunciato": "...", "descrizione": "..."},
        "ospite":         {"voce": "...", "nome_pronunciato": "...", "descrizione": "..."}
      }
    }

Lo stato (bozza, copione da approvare, pronto...) si DERIVA dai file della run
(vedi la fase Archivio), non si scrive qui.

Da qui si ricavano gli argomenti che il resto dell'agente conosce gia':
`speakers`, `interviewer`, `presenter`, `participants`, cioe' gli stessi di
tools/script_save.py. Le regole di coerenza sono quelle di src/script_rules.py.

Il web supporta solo `conversazione` e `intervista`, sempre con un
presentatore e due voci (decisione del piano, punto aperto 4): monologo e
tavola rotonda restano possibili con run.py e con script_write.py senza
configurazione della run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import DomainError, NeedsUserDecision
from .fileio import file_lock, write_json_atomic
from .script_rules import FORMATS

FILENAME = "episodio.json"

#: Ruoli per formato, nell'ordine in cui parlano/compaiono.
ROLES_BY_FORMAT: dict[str, tuple[str, ...]] = {
    "conversazione": ("presentatore", "voce_a", "voce_b"),
    "intervista": ("presentatore", "intervistatore", "ospite"),
}
ALL_ROLES = ("presentatore", "voce_a", "voce_b", "intervistatore", "ospite")
ROLE_LABELS = {
    "presentatore": "Presentatore",
    "voce_a": "Voce A",
    "voce_b": "Voce B",
    "intervistatore": "Intervistatore",
    "ospite": "Ospite",
}
#: Cambiando formato le voci restano al loro posto: chi era "voce A" diventa
#: l'intervistatore, chi era "voce B" l'ospite (come nel wireframe, dove la
#: stessa voce cambia solo etichetta).
_EQUIVALENT_ROLE = {
    "voce_a": "intervistatore",
    "voce_b": "ospite",
    "intervistatore": "voce_a",
    "ospite": "voce_b",
}

MIN_MINUTES = 1
MAX_MINUTES = 180
MAX_TITLE_CHARS = 200


class EpisodeConfigError(DomainError):
    """Una scelta non valida per la configurazione dell'episodio (exit 1)."""


class SpokenNameIncomplete(NeedsUserDecision):
    """Il nome da pronunciare non ha nome E cognome (exit 3).

    Non si indovina il cognome: o l'utente lo aggiunge, o conferma
    esplicitamente che va detto cosi' (`accept_incomplete`).
    """


def spoken_name_is_complete(name: str) -> bool:
    """Nome e cognome: almeno due parole."""
    return len(name.split()) >= 2


def parse_duration(value: str) -> int:
    """"1:30" (ore:minuti) oppure "90" (minuti) -> minuti interi."""
    text = str(value).strip()
    try:
        if ":" in text:
            hours, _, minutes = text.partition(":")
            total = int(hours or 0) * 60 + int(minutes or 0)
        else:
            total = int(text)
    except ValueError as exc:
        raise EpisodeConfigError(
            f"Durata '{value}' non riconosciuta: usa ore:minuti (es. 0:15) o i minuti (es. 15).",
            details={"campo": "durata"},
        ) from exc
    if not MIN_MINUTES <= total <= MAX_MINUTES:
        raise EpisodeConfigError(
            f"Durata di {total} minuti fuori dall'intervallo ammesso "
            f"({MIN_MINUTES}-{MAX_MINUTES} minuti).",
            details={"campo": "durata", "minimo": MIN_MINUTES, "massimo": MAX_MINUTES},
        )
    return total


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass
class EpisodeConfig:
    titolo: str | None = None
    documento: dict[str, Any] | None = None
    formato: str | None = None
    durata_minuti: int | None = None
    ruoli: dict[str, dict[str, str]] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # -- IO ---------------------------------------------------------------

    @classmethod
    def path_for(cls, run_dir: Path | str) -> Path:
        return Path(run_dir) / FILENAME

    @classmethod
    def load(cls, run_dir: Path | str) -> "EpisodeConfig | None":
        """La configurazione della run, o None se la run non ne ha (run vecchie)."""
        target = cls.path_for(run_dir)
        if not target.is_file():
            return None
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EpisodeConfigError(f"{target} non e' un JSON leggibile: {exc}") from exc
        if not isinstance(raw, dict):
            raise EpisodeConfigError(f"{target} deve contenere un oggetto JSON.")
        return cls(
            titolo=raw.get("titolo"),
            documento=raw.get("documento"),
            formato=raw.get("formato"),
            durata_minuti=raw.get("durata_minuti"),
            ruoli={k: dict(v) for k, v in (raw.get("ruoli") or {}).items() if isinstance(v, dict)},
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "titolo": self.titolo,
            "documento": self.documento,
            "formato": self.formato,
            "durata_minuti": self.durata_minuti,
            "ruoli": self.ruoli,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def save(self, run_dir: Path | str) -> Path:
        now = _now()
        self.created_at = self.created_at or now
        self.updated_at = now
        return write_json_atomic(self.path_for(run_dir), self.to_dict())

    # -- modifiche --------------------------------------------------------

    def set_title(self, title: str) -> None:
        clean = " ".join(str(title).split())
        if not clean:
            raise EpisodeConfigError("Il titolo e' vuoto.", details={"campo": "titolo"})
        if len(clean) > MAX_TITLE_CHARS:
            raise EpisodeConfigError(
                f"Il titolo supera i {MAX_TITLE_CHARS} caratteri.", details={"campo": "titolo"}
            )
        self.titolo = clean

    def set_format(self, formato: str) -> list[str]:
        """Cambia formato tenendo le voci al loro posto. Ritorna gli avvisi."""
        if formato not in FORMATS:
            raise EpisodeConfigError(
                f"Formato '{formato}' non disponibile. Ammessi: {', '.join(FORMATS)}.",
                details={"campo": "formato", "ammessi": list(FORMATS)},
            )
        warnings: list[str] = []
        if self.formato and self.formato != formato:
            remapped: dict[str, dict[str, str]] = {}
            for role, data in self.ruoli.items():
                new_role = role if role in ROLES_BY_FORMAT[formato] else _EQUIVALENT_ROLE.get(role)
                if new_role:
                    remapped[new_role] = data
            if any(
                role in ("intervistatore", "ospite", "voce_a", "voce_b") and data.get("descrizione")
                for role, data in remapped.items()
            ):
                warnings.append(
                    "Formato cambiato: le voci restano assegnate, ma rileggi la "
                    "descrizione dei ruoli (es. chi fa le domande)."
                )
            self.ruoli = remapped
        self.formato = formato
        return warnings

    def set_duration(self, minutes: int) -> None:
        if not MIN_MINUTES <= int(minutes) <= MAX_MINUTES:
            raise EpisodeConfigError(
                f"Durata di {minutes} minuti fuori dall'intervallo ammesso "
                f"({MIN_MINUTES}-{MAX_MINUTES} minuti).",
                details={"campo": "durata", "minimo": MIN_MINUTES, "massimo": MAX_MINUTES},
            )
        self.durata_minuti = int(minutes)

    def _check_role(self, role: str) -> None:
        if not self.formato:
            raise EpisodeConfigError(
                "Scegli prima il formato (conversazione o intervista): i ruoli dipendono da quello.",
                details={"campo": "formato"},
            )
        allowed = ROLES_BY_FORMAT[self.formato]
        if role not in allowed:
            raise EpisodeConfigError(
                f"Il ruolo '{role}' non esiste nel formato '{self.formato}'. "
                f"Ruoli ammessi: {', '.join(allowed)}.",
                details={"campo": "ruolo", "ruolo": role, "ammessi": list(allowed)},
            )

    def assign_voice(self, role: str, voice: str) -> None:
        """Assegna una voce ESATTA (nome del file, gia' risolto) a un ruolo."""
        self._check_role(role)
        for other, data in self.ruoli.items():
            if other != role and data.get("voce") == voice:
                raise EpisodeConfigError(
                    f"'{voice}' e' gia' assegnata al ruolo {ROLE_LABELS[other]}: ogni "
                    "ruolo ha una voce diversa.",
                    details={"campo": "voce", "ruolo": role, "voce": voice, "gia_in": other},
                )
        entry = self.ruoli.setdefault(role, {})
        if entry.get("voce") != voice:
            # Voce nuova: nome e descrizione erano della persona precedente.
            entry.pop("nome_pronunciato", None)
            entry.pop("descrizione", None)
        entry["voce"] = voice

    def set_spoken_name(self, role: str, name: str, *, accept_incomplete: bool = False) -> None:
        self._check_role(role)
        clean = " ".join(str(name).split())
        if not clean:
            raise EpisodeConfigError(
                "Il nome da pronunciare e' vuoto.", details={"campo": "nome_pronunciato", "ruolo": role}
            )
        if not accept_incomplete and not spoken_name_is_complete(clean):
            raise SpokenNameIncomplete(
                f"'{clean}' ({ROLE_LABELS[role]}): manca il cognome. Aggiungilo, "
                "non lo indoviniamo; se va detto proprio cosi', confermalo esplicitamente.",
                details={"campo": "nome_pronunciato", "ruolo": role, "nome": clean},
            )
        self.ruoli.setdefault(role, {})["nome_pronunciato"] = clean

    def set_description(self, role: str, text: str) -> None:
        self._check_role(role)
        clean = " ".join(str(text).split())
        if not clean:
            raise EpisodeConfigError(
                "La descrizione del ruolo e' vuota.", details={"campo": "descrizione", "ruolo": role}
            )
        self.ruoli.setdefault(role, {})["descrizione"] = clean

    def remove_role(self, role: str) -> None:
        self._check_role(role)
        self.ruoli.pop(role, None)

    # -- viste derivate ---------------------------------------------------

    @property
    def roles(self) -> tuple[str, ...]:
        return ROLES_BY_FORMAT.get(self.formato or "", ())

    def voice_of(self, role: str) -> str | None:
        return (self.ruoli.get(role) or {}).get("voce")

    @property
    def presenter(self) -> str | None:
        return self.voice_of("presentatore")

    @property
    def interviewer(self) -> str | None:
        return self.voice_of("intervistatore") if self.formato == "intervista" else None

    @property
    def dialogue_roles(self) -> tuple[str, ...]:
        return tuple(r for r in self.roles if r != "presentatore")

    @property
    def speakers(self) -> list[str]:
        """Gli speaker del copione: presentatore per primo, poi il dialogo."""
        return [v for v in (self.voice_of(r) for r in self.roles) if v]

    @property
    def participants(self) -> list[dict[str, str]]:
        """Nella forma di script_save.py: {"speaker", "nome", "ruolo"}."""
        out = []
        for role in self.dialogue_roles:
            data = self.ruoli.get(role) or {}
            if data.get("voce") and data.get("nome_pronunciato") and data.get("descrizione"):
                out.append(
                    {"speaker": data["voce"], "nome": data["nome_pronunciato"], "ruolo": data["descrizione"]}
                )
        return out

    def role_of(self, speaker: str) -> str | None:
        for role in self.roles:
            if self.voice_of(role) == speaker:
                return role
        return None

    def missing(self) -> list[dict[str, str]]:
        """Cosa manca perche' il cast sia completo, in forma strutturata."""
        out: list[dict[str, str]] = []
        if not self.titolo:
            out.append({"campo": "titolo"})
        if not self.documento:
            out.append({"campo": "documento"})
        if not self.formato:
            out.append({"campo": "formato"})
            return out
        if not self.durata_minuti:
            out.append({"campo": "durata"})
        for role in self.roles:
            data = self.ruoli.get(role) or {}
            if not data.get("voce"):
                out.append({"campo": "voce", "ruolo": role})
            if role != "presentatore":
                if not data.get("nome_pronunciato"):
                    out.append({"campo": "nome_pronunciato", "ruolo": role})
                if not data.get("descrizione"):
                    out.append({"campo": "descrizione", "ruolo": role})
        return out

    @property
    def cast_complete(self) -> bool:
        """Formato, durata e tutti i ruoli pronti (titolo e documento a parte)."""
        return not [m for m in self.missing() if m["campo"] not in ("titolo", "documento")]

    def script_arguments(self) -> dict[str, Any]:
        """Gli argomenti per scrivere il copione. Solleva se il cast e' incompleto."""
        gaps = [m for m in self.missing() if m["campo"] not in ("titolo",)]
        if gaps:
            raise EpisodeConfigError(
                "La configurazione dell'episodio non e' completa: "
                + ", ".join(
                    m["campo"] + (f" ({ROLE_LABELS[m['ruolo']]})" if m.get("ruolo") else "") for m in gaps
                )
                + ". Completala con tools/episode_config.py prima di scrivere il copione.",
                details={"mancanti": gaps},
            )
        return {
            "speakers": self.speakers,
            "script_format": self.formato,
            "interviewer": self.interviewer,
            "presenter": self.presenter,
            "participants": self.participants,
            "target_minutes": self.durata_minuti,
        }

    def roles_view(self) -> list[dict[str, Any]]:
        """I ruoli del formato corrente, nell'ordine, anche quelli vuoti."""
        return [
            {
                "ruolo": role,
                "etichetta": ROLE_LABELS[role],
                "voce": (self.ruoli.get(role) or {}).get("voce"),
                "nome_pronunciato": (self.ruoli.get(role) or {}).get("nome_pronunciato"),
                "descrizione": (self.ruoli.get(role) or {}).get("descrizione"),
            }
            for role in self.roles
        ]


def locked_update(run_dir: Path | str):
    """Lock per leggi-modifica-scrivi di episodio.json (due utenti sulla stessa bozza)."""
    return file_lock(EpisodeConfig.path_for(run_dir))




def document_record(document: Any) -> dict[str, Any]:
    """Cio' che episodio.json ricorda del documento (src.pdf_reader.Document)."""
    from .registry import file_hash

    path = Path(document.path)
    return {
        "nome": path.name,
        "percorso": str(path),
        "sha256": file_hash(path),
        "tipo": document.kind,
        "pagine": document.pages,
        "parole": document.word_count,
        "caratteri": document.char_count,
    }


#: Il testo estratto, salvato nella run: la scrittura del copione lo legge da
#: qui, cosi' funziona anche se il file originale viene poi tolto da documenti/.
EXTRACTED_TEXT_FILENAME = "documento_estratto.txt"


__all__ = [
    "ALL_ROLES",
    "EpisodeConfig",
    "EpisodeConfigError",
    "FILENAME",
    "ROLES_BY_FORMAT",
    "ROLE_LABELS",
    "EXTRACTED_TEXT_FILENAME",
    "SpokenNameIncomplete",
    "document_record",
    "locked_update",
    "parse_duration",
    "spoken_name_is_complete",
]
