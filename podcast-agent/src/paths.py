"""Percorsi che arrivano da input: restano dentro la cartella prevista.

Con l'interfaccia web i nomi e i percorsi passati ai tool arrivano da utenti
(un --run-dir scelto dall'Archivio, il nome di un documento). Un valore come
`../../etc`, un percorso assoluto qualsiasi, un link simbolico dentro
output/ che punta altrove o un nome nudo come `.env` non devono far leggere o
scrivere fuori dalla cartella prevista. Qui c'e' l'unico controllo, usato da
tutti i tool.

Il controllo si fa sul percorso RISOLTO (link simbolici compresi): e' quello
che il sistema operativo aprira' davvero.
"""

from __future__ import annotations

from pathlib import Path

from .errors import DomainError


class PathNotAllowed(DomainError):
    """Un percorso da input esce dalla cartella in cui deve stare."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def confine_run_dir(value: str | Path, output_dir: Path, *, must_exist: bool = True) -> Path:
    """La cartella di un episodio: una sottocartella DIRETTA di output_dir.

    Le run nascono solo li' (run_init.py), quindi qualunque altro percorso e'
    un errore, anche se esiste. Il JSON di errore non ripete il percorso
    ricevuto: non serve a correggere e direbbe cosa esiste sul server.
    """
    root = Path(output_dir).expanduser().resolve()
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate if candidate.parent == Path(".") else candidate
    resolved = candidate.resolve()
    if resolved.parent != root:
        raise PathNotAllowed(
            "La cartella dell'episodio deve essere una delle cartelle dell'Archivio "
            f"(dentro {root.name}/): si creano con run_init.py.",
            details={"campo": "run_dir"},
        )
    if must_exist and not resolved.is_dir():
        raise FileNotFoundError(
            f"Cartella della run inesistente: {resolved.name}. Chiama prima run_init.py."
        )
    return resolved


def confine_to(path: str | Path, root: Path, *, field: str) -> Path:
    """Un percorso qualsiasi che deve stare (risolto) dentro `root`."""
    root_resolved = Path(root).expanduser().resolve()
    resolved = Path(str(path)).expanduser().resolve()
    if not _inside(resolved, root_resolved):
        raise PathNotAllowed(
            f"Il percorso indicato per '{field}' deve stare dentro {root_resolved.name}/.",
            details={"campo": field},
        )
    return resolved


__all__ = ["PathNotAllowed", "confine_run_dir", "confine_to"]
