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
import re
import shutil
import subprocess
import wave
from pathlib import Path


class FfmpegNotFoundError(RuntimeError):
    """Ne' imageio-ffmpeg ne' un ffmpeg di sistema sono disponibili."""


#: Tempo massimo di una singola esecuzione di ffmpeg nel montaggio e nel mix.
#: Un ffmpeg appeso (file patologico, disco pieno) terrebbe altrimenti il
#: processo vivo per sempre, e con lui il lock di lavoro della run: l'episodio
#: resterebbe "in generazione" (revisione di sicurezza C2, MEDIA-4). 15 minuti
#: bastano per codificare ore di audio.
FFMPEG_TIMEOUT_SECONDS = 900


#: Formato (demuxer di ffmpeg) per estensione. Forzarlo vuol dire che un file
#: "brano.mp3" si legge SOLO come mp3: se in realta' e' una playlist (HLS,
#: ffconcat, ...) ffmpeg si rifiuta invece di aprire altri file o URL.
_DEMUXERS = {
    ".wav": "wav",
    ".mp3": "mp3",
    ".m4a": "mov",
    ".mp4": "mov",
    ".aac": "aac",
    ".flac": "flac",
    ".ogg": "ogg",
    ".opus": "ogg",
    ".webm": "matroska",
}


def input_args(path: str | Path) -> list[str]:
    """Gli argomenti con cui ffmpeg apre un file in ingresso.

    `-protocol_whitelist file`: solo file locali, mai rete o altri protocolli
    (nemmeno se un file caricato contiene riferimenti esterni); `-f`: il
    formato dall'estensione, quando e' noto. Revisioni di sicurezza C0+C1 e
    C5 (BASSA-4), fase C6.
    """
    args = ["-protocol_whitelist", "file"]
    demuxer = _DEMUXERS.get(Path(path).suffix.lower())
    if demuxer:
        args += ["-f", demuxer]
    return [*args, "-i", str(path)]


def probe_args(path: str | Path) -> list[str]:
    """Come input_args(), per ffprobe (che prende il file senza `-i`)."""
    args = ["-protocol_whitelist", "file"]
    demuxer = _DEMUXERS.get(Path(path).suffix.lower())
    if demuxer:
        args += ["-f", demuxer]
    return [*args, str(path)]


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


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def media_duration_seconds(path: str | Path) -> float | None:
    """Durata di un file audio, in secondi. None se non misurabile.

    Tre strade, in ordine:

      1. i .wav con il modulo standard `wave` (nessuna dipendenza);
      2. `ffprobe`, se e' nel PATH di sistema;
      3. `ffmpeg -i`, leggendo la riga "Duration:" che stampa su stderr.

    La terza e' quella che conta nell'installazione standard: imageio-ffmpeg
    porta ffmpeg ma NON ffprobe, quindi senza questa strada la durata degli
    mp3/m4a (musiche, campioni delle voci) restava sconosciuta.
    """
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                return handle.getnframes() / rate if rate else None
        except (wave.Error, OSError, EOFError):
            pass  # wav non standard (es. float, extensible): ci prova ffmpeg

    ffprobe = None if os.environ.get("PODCAST_AGENT_DISABLE_FFMPEG") else shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", *probe_args(path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    try:
        # Senza output ffmpeg esce con errore ("At least one output file must
        # be specified"), ma la riga Duration l'ha gia' stampata: e' voluto.
        result = subprocess.run(
            [ffmpeg, "-hide_banner", *input_args(path)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _DURATION_RE.search(result.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


__all__ = ["FfmpegNotFoundError", "find_ffmpeg", "media_duration_seconds", "require_ffmpeg"]
