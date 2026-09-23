"""Unico punto del progetto che cerca l'eseguibile ffmpeg.

Il progetto deve installarsi con il solo `pip install -r requirements.txt`,
su macOS, Windows e Linux, senza pacchetti di sistema (niente "prima installa
ffmpeg col tuo package manager"). Per questo `imageio-ffmpeg` e' una
dipendenza OBBLIGATORIA (vedi requirements.txt): porta con se' un binario
ffmpeg statico gia' pronto per la piattaforma corrente.

Due strade, in ordine:

  1. `imageio_ffmpeg.get_ffmpeg_exe()` — il binario del pacchetto pip;
  2. `ffmpeg` nel PATH di sistema — ripiego per chi ne ha gia' uno (es. una
     installazione di sistema preesistente) e preferisce usare quello.

Nessun altro modulo chiama `shutil.which("ffmpeg")` o importa
`imageio_ffmpeg` per conto proprio: `src/audio_assembler.py` e
`src/music_mixer.py` passano sempre da qui, cosi' "dove si trova ffmpeg" e
"cosa succede se non c'e'" restano decisi in un solo posto.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class FfmpegNotFoundError(RuntimeError):
    """Ne' imageio-ffmpeg ne' un ffmpeg di sistema sono disponibili."""


def find_ffmpeg() -> str | None:
    """Percorso dell'eseguibile ffmpeg, o None se non trovato.

    La variabile d'ambiente PODCAST_AGENT_DISABLE_FFMPEG forza il risultato a
    None anche se ffmpeg e' presente: serve SOLO ai test, per simulare "ffmpeg
    assente" senza doverlo disinstallare davvero (vedi tests/test_music.py).
    """
    if os.environ.get("PODCAST_AGENT_DISABLE_FFMPEG"):
        return None

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = None
    if exe and Path(exe).is_file():
        return exe

    return shutil.which("ffmpeg")


def require_ffmpeg() -> str:
    """Come find_ffmpeg(), ma solleva un errore leggibile se manca."""
    exe = find_ffmpeg()
    if exe is None:
        raise FfmpegNotFoundError(
            "ffmpeg non trovato. Due strade:\n"
            "  1. .venv/bin/pip install -r requirements.txt — include "
            "imageio-ffmpeg, che porta un ffmpeg pronto all'uso, senza "
            "installazioni di sistema;\n"
            "  2. in alternativa, assicurati che un ffmpeg di sistema sia nel PATH.\n"
            "Esegui tools/doctor.py per una diagnosi completa."
        )
    return exe


__all__ = ["FfmpegNotFoundError", "find_ffmpeg", "require_ffmpeg"]
