"""Smoke test end-to-end senza toccare nessuna API a pagamento.

Esercita: caricamento dinamico del provider, scansione cartella voci,
risoluzione dei nomi, registro/cache, stima, sintesi, montaggio, output.

    python tests/test_smoke.py
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio_assembler import (
    AudioAssembler,
    MissingSpeakerVoice,
    _trim_edges,
    detect_dropped_audio,
    synthesize_segment,
    script_segment_keys,
    segment_key,
    slugify,
)
from src.config import Config, load_config
from src.cost_estimate import estimate
from src.pdf_reader import extract_text
from src.registry import VoiceRegistry, file_hash
from src.script_writer import PodcastScript
from src.voice_library import VoiceLibrary, VoiceLibraryError
from tests.fake_provider import FakeProvider

PASSED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        raise SystemExit(1)


def wav_bytes(pcm: bytes, rate: int = 8000) -> bytes:
    """Incarta dei byte PCM in un wav in memoria, come lo restituisce un provider."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def make_wav(path: Path, seconds: float = 1.0, freq: int = 220) -> None:
    rate = 44100
    frames = int(rate * seconds)
    data = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-agent-test-"))
    voices_dir = root / "voci"
    voices_dir.mkdir()
    make_wav(voices_dir / "Mario Rossi.wav", 1.0, 220)
    make_wav(voices_dir / "Giulia Bianchi.wav", 1.0, 330)
    (voices_dir / "note.txt").write_text("non e' audio", encoding="utf-8")

    print("\n[1] Documento")
    doc_path = root / "documento.md"
    doc_path.write_text(
        "# Report trimestrale\n\nI ricavi sono cresciuti del 12%.\n", encoding="utf-8"
    )
    document = extract_text(doc_path)
    check("estrazione testo", "ricavi" in document.text and document.word_count > 3)

    print("\n[2] Libreria voci")
    library = VoiceLibrary(voices_dir, [".wav", ".mp3"])
    library.scan()
    check("scansione trova 2 voci", library.names == ["Giulia Bianchi", "Mario Rossi"],
          str(library.names))
    check("file non audio segnalato", [p.name for p in library.rejected_files] == ["note.txt"])

    try:
        library.require(5)
        check("require(5) deve fallire", False)
    except VoiceLibraryError:
        check("require(5) blocca se mancano voci", True)

    match, candidates = library.resolve("Mario Rossi")
    check("match esatto", match is not None and match.name == "Mario Rossi")
    match, candidates = library.resolve("mario rosi")  # typo
    check("typo -> nessun match secco, ma candidati",
          match is None and [c.name for c in candidates] == ["Mario Rossi"])
    match, candidates = library.resolve("Sconosciuto")
    check("nome inesistente -> nessun candidato", match is None and candidates == [])
    match, _ = library.resolve("2")
    check("selezione per indice", match is not None and match.name == "Mario Rossi")

    print("\n[3] Registro / cache")
    registry = VoiceRegistry(root / "voices_registry.json", project_root=root)
    provider = FakeProvider()
    mario = library.voices[1]

    voice_id, reason = registry.lookup("Mario Rossi", mario.path, provider.name)
    check("prima volta: da clonare", voice_id is None and "non presente" in reason)

    new_id = provider.clone_voice("Mario Rossi", str(mario.path))
    registry.record("Mario Rossi", mario.path, provider.name, new_id)
    voice_id, reason = registry.lookup("Mario Rossi", mario.path, provider.name)
    check("seconda volta: riuso dalla cache", voice_id == new_id and "riuso" in reason)

    make_wav(mario.path, 1.2, 250)  # l'utente sostituisce la registrazione
    voice_id, reason = registry.lookup("Mario Rossi", mario.path, provider.name)
    check("hash cambiato -> riclona", voice_id is None and "cambiato" in reason)

    voice_id, reason = registry.lookup("Mario Rossi", mario.path, "altro_provider")
    check("provider diverso -> riclona", voice_id is None and "altro provider" in reason)

    saved = json.loads((root / "voices_registry.json").read_text())
    check("formato registro", set(saved["Mario Rossi"]) ==
          {"audio_file", "audio_hash", "provider", "voice_id", "cloned_at"})
    check("hash sha256", saved["Mario Rossi"]["audio_hash"].startswith("sha256:"))

    print("\n[4] Script e validazione")
    script = PodcastScript.from_dict({
        "titolo": "Il trimestre in tre minuti",
        "turni": [
            {"speaker": "Mario Rossi", "testo": "Ciao a tutti e benvenuti."},
            {"speaker": "Giulia Bianchi", "testo": "Oggi parliamo del report trimestrale."},
            {"speaker": "Mario Rossi", "testo": "I ricavi sono cresciuti del dodici per cento."},
        ],
    })
    check("speaker estratti", script.speakers == ["Mario Rossi", "Giulia Bianchi"])
    check("script valido", script.validate(["Mario Rossi", "Giulia Bianchi"]) == [])
    problems = script.validate(["Mario Rossi", "Luca Verdi"])
    check("speaker sconosciuto rilevato", any("non selezionati" in p for p in problems))
    check("speaker senza battute rilevato", any("nessuna battuta" in p for p in problems))

    print("\n[5] Stima")
    prediction = estimate(script, provider.pricing(), words_per_minute=150,
                          pause_between_turns_ms=450)
    check("caratteri contati", prediction.total_chars == script.total_chars)
    check("durata > 0", prediction.total_seconds > 0)
    check("costo calcolato", prediction.cost_usd is not None)
    print(prediction.render())

    print("\n[6] Blocco pre-sintesi se manca una voce")
    assembler = AudioAssembler(provider=provider, pause_between_turns_ms=450)
    try:
        assembler.check_voice_coverage(script, {"Mario Rossi": "fake-0001"})
        check("deve bloccare", False)
    except MissingSpeakerVoice as exc:
        check("blocca prima della sintesi", "Giulia Bianchi" in str(exc))

    print("\n[7] Sintesi e montaggio")
    voice_map = {
        "Mario Rossi": provider.clone_voice("Mario Rossi", str(mario.path)),
        "Giulia Bianchi": provider.clone_voice(
            "Giulia Bianchi", str(library.voices[0].path)
        ),
    }
    run_dir = root / "output" / "2026-09-13_10-30_il-trimestre-in-tre-minuti"
    synthesis = assembler.synthesize(script, voice_map, run_dir / "segmenti")
    segments = synthesis.paths
    check("un segmento per turno", len(segments) == 3)
    check("tutti sintetizzati la prima volta",
          synthesis.generated_turns == [1, 2, 3] and synthesis.reused_turns == [])
    check("segmenti su disco", all(p.is_file() and p.stat().st_size > 0 for p in segments))

    result = assembler.assemble(segments, run_dir / slugify(script.titolo))
    check("file finale creato", result.path.is_file() and result.path.stat().st_size > 0)
    check("durata calcolata", (result.duration_seconds or 0) > 0)

    expected_speech = sum(
        max(0.4, len(t["testo"]) / 100 * 4.0) for t in script.turni
    )
    expected_total = expected_speech + (2 * 0.45 + 0.3 + 0.6)
    check(
        f"durata con pause (~{expected_total:.2f}s, reale {result.duration_seconds:.2f}s)",
        abs((result.duration_seconds or 0) - expected_total) < 0.15,
    )
    print(f"       formato finale: {result.audio_format}")
    for warning in result.warnings:
        print(f"       avviso: {warning.splitlines()[0]}")

    print("\n[7-bis] La chiave dei segmenti: quando la cache vale e quando no")

    class Impronta(FakeProvider):
        """Un provider identico al finto, ma con un parametro di sintesi diverso."""

        def synthesis_fingerprint(self):
            return {"temperature": 0.9}

    base = segment_key("Ciao a tutti.", "fake-0001", provider)
    check("la chiave e' stabile a parita' di tutto",
          base == segment_key("Ciao a tutti.", "fake-0001", provider))
    check("testo diverso -> chiave diversa",
          base != segment_key("Ciao a tutti!", "fake-0001", provider))
    check("voce diversa -> chiave diversa",
          base != segment_key("Ciao a tutti.", "fake-0002", provider))
    check("impronta di sintesi diversa -> chiave diversa (la cache decade)",
          base != segment_key("Ciao a tutti.", "fake-0001", Impronta()))
    check("occorrenza diversa -> chiave diversa",
          base != segment_key("Ciao a tutti.", "fake-0001", provider, occurrence=2))

    # Le ripetizioni brevi ("Esatto.") sono la norma in un dialogo: devono
    # restare due segmenti distinti, altrimenti lo stesso audio ripetuto suona
    # registrato e un retake sull'uno cambierebbe anche l'altro.
    ripetuto = PodcastScript.from_dict({
        "titolo": "Ripetizioni",
        "turni": [
            {"speaker": "Mario Rossi", "testo": "Esatto."},
            {"speaker": "Giulia Bianchi", "testo": "I conti tornano."},
            {"speaker": "Mario Rossi", "testo": "Esatto."},
        ],
    })
    chiavi = script_segment_keys(ripetuto, voice_map, provider)
    check("due battute identiche -> due chiavi distinte",
          chiavi[0] != chiavi[2] and len(set(chiavi)) == 3, str(chiavi))

    # e inserire un turno DIVERSO non deve spostare le chiavi degli altri:
    # e' questo che fa reggere la cache alle revisioni del copione
    con_inserto = PodcastScript.from_dict({
        "titolo": "Ripetizioni",
        "turni": [
            ripetuto.turni[0],
            {"speaker": "Giulia Bianchi", "testo": "Una battuta nuova in mezzo."},
            ripetuto.turni[1],
            ripetuto.turni[2],
        ],
    })
    nuove = script_segment_keys(con_inserto, voice_map, provider)
    check("inserendo un turno le chiavi degli altri non cambiano",
          [nuove[0], nuove[2], nuove[3]] == chiavi, f"{nuove} vs {chiavi}")

    print("\n[8] Caricamento dinamico del provider da config.yaml")
    config = Config(
        raw={
            "active_provider": "fake",
            "providers": {
                "fake": {
                    "class": "tests.fake_provider:FakeProvider",
                    "options": {"seconds_per_100_chars": 2.0},
                }
            },
            "paths": {"voices_dir": ".", "registry_file": "r.json", "output_dir": "."},
        },
        path=Path("config-test.yaml"),
    )
    loaded = config.build_provider()
    check("provider istanziato dalla config", isinstance(loaded, FakeProvider))
    check("opzioni passate", loaded._seconds_per_100 == 2.0)

    real_config = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    check("config.yaml reale si carica", real_config.active_provider == "fish_audio")
    check("estensioni normalizzate", ".wav" in real_config.accepted_extensions)

    print("\n[9] Ritaglio del silenzio ai bordi dei segmenti")

    class P:  # i soli campi di wave.Params che servono a _trim_edges
        nchannels, sampwidth, framerate = 1, 2, 8000

    ms = 8  # campioni per millisecondo a 8 kHz
    def pcm(*blocchi):
        """blocchi = (durata_ms, ampiezza) -> bytes PCM 16 bit mono."""
        out = bytearray()
        for durata, amp in blocchi:
            for i in range(durata * ms):
                v = int(amp * math.sin(2 * math.pi * 200 * i / 8000))
                out += struct.pack("<h", v)
        return bytes(out)

    def durata_ms(data):
        return len(data) // 2 // ms

    # 500 ms di silenzio + 1000 di parlato + 900 di silenzio
    segmento = pcm((500, 0), (1000, 12000), (900, 0))
    check("segmento di prova lungo 2400 ms", durata_ms(segmento) == 2400)
    tagliato = _trim_edges(segmento, P, threshold_pct=1.5,
                           margin_head_ms=30, margin_tail_ms=80)
    check("il silenzio in eccesso e' sparito",
          abs(durata_ms(tagliato) - (1000 + 30 + 80)) <= 10, f"{durata_ms(tagliato)} ms")
    check("il parlato NON e' stato toccato", durata_ms(tagliato) >= 1000)

    # il margine deve restare davvero: serve a non troncare attacchi e rilasci
    testa = next(i for i in range(0, len(tagliato), 2)
                 if abs(struct.unpack("<h", tagliato[i:i+2])[0]) > 100) // 2 // ms
    check("il margine in testa e' stato lasciato", 20 <= testa <= 40, f"{testa} ms")

    # i margini sono asimmetrici di proposito
    stretto = _trim_edges(segmento, P, threshold_pct=1.5,
                          margin_head_ms=0, margin_tail_ms=0)
    check("senza margini si taglia di piu'", durata_ms(stretto) < durata_ms(tagliato))

    # casi in cui NON deve toccare niente
    check("segmento tutto silenzio -> invariato",
          _trim_edges(pcm((800, 0)), P) == pcm((800, 0)))
    check("segmento senza silenzio ai bordi -> invariato",
          _trim_edges(pcm((600, 12000)), P) == pcm((600, 12000)))

    class P8(P):
        sampwidth = 1
    check("formato non a 16 bit -> invariato (meglio non ritagliare che ritagliare male)",
          _trim_edges(segmento, P8) == segmento)

    # soglia relativa: un segmento sussurrato non deve sparire
    piano = pcm((500, 0), (1000, 400), (900, 0))
    tagliato_piano = _trim_edges(piano, P, threshold_pct=1.5,
                                 margin_head_ms=30, margin_tail_ms=80)
    check("voce piano: il parlato sopravvive",
          abs(durata_ms(tagliato_piano) - 1110) <= 10, f"{durata_ms(tagliato_piano)} ms")

    # L'effetto che conta sul montaggio: tre segmenti con code MOLTO diverse
    # (900, 200, 1400 ms) devono diventare lunghi uguali dopo il ritaglio.
    # E' questo che rende costante la pausa percepita, non la sua durata.
    code = (900, 200, 1400)
    grezzi = [pcm((100, 0), (700, 12000), (c, 0)) for c in code]
    rifiniti = [_trim_edges(g, P, threshold_pct=1.5, margin_head_ms=30,
                            margin_tail_ms=80) for g in grezzi]
    lunghezze = [durata_ms(r) for r in rifiniti]
    check("code diverse -> segmenti lunghi uguali dopo il ritaglio",
          max(lunghezze) - min(lunghezze) <= 10, f"{lunghezze} ms")
    check("la lunghezza e' parlato + i due margini",
          all(abs(x - (700 + 30 + 80)) <= 10 for x in lunghezze), f"{lunghezze} ms")
    grezze = [durata_ms(g) for g in grezzi]
    check("senza ritaglio erano diverse di oltre un secondo",
          max(grezze) - min(grezze) > 1000, f"{grezze} ms")

    # ------------------------------------------------------------------
    print("\n[10] Vuoto rumoroso in mezzo a un turno")
    # Riproduce il turno 10 dell'episodio "Il fotovoltaico dell'Alma Mater"
    # (18/09/2026): il provider dice il testo ma ci infila in mezzo decine di
    # secondi di RUMORE al posto della voce. Il parlato totale torna, quindi
    # il controllo sul rapporto non lo vede; il rumore sta sopra la soglia
    # assoluta del ritaglio, quindi nemmeno il montaggio lo accorcia.
    voce, rumore = 20000, 54  # ampiezze misurate sul segmento reale
    difettoso = pcm((3000, voce), (36000, rumore), (5000, voce))
    sano = pcm((3000, voce), (3000, rumore), (5000, voce))

    atteso = 8.0  # il parlato c'e' tutto: 3s + 5s
    check("il controllo sul rapporto NON vede il vuoto rumoroso",
          detect_dropped_audio(wav_bytes(difettoso), atteso, max_gap_seconds=0.0) is None,
          "se lo vedesse, il difetto reale non sarebbe mai sfuggito")

    difetto = detect_dropped_audio(wav_bytes(difettoso), atteso)
    check("il controllo sul vuoto lo rileva", difetto is not None and difetto.kind == "vuoto",
          f"{difetto}")
    check("e riporta quanto dura il vuoto",
          difetto is not None and 35 <= (difetto.gap_seconds or 0) <= 37,
          f"{difetto.gap_seconds if difetto else None} s")

    check("una pausa di 3s in un turno sano non viene segnalata",
          detect_dropped_audio(wav_bytes(sano), atteso) is None)
    check("un segmento davvero troncato resta 'troncato'",
          (lambda d: d is not None and d.kind == "troncato")(
              detect_dropped_audio(wav_bytes(pcm((1000, voce), (1000, 0))), 20.0)),
          "il controllo storico non deve essere stato sostituito")

    # Il rilevamento serve a poco se non fa scattare un altro tentativo: qui
    # il provider sbaglia la prima volta e va bene la seconda, come il glitch
    # reale, che e' probabilistico.
    class ProviderBallerino:
        name, audio_format, chars_per_second = "ballerino", "wav", 14.5

        def __init__(self):
            self.chiamate = 0

        def text_to_speech(self, text, voice_id):
            self.chiamate += 1
            guasto = self.chiamate == 1
            return wav_bytes(
                pcm((3000, voce), (36000 if guasto else 300, rumore), (5000, voce))
            )

    ballerino = ProviderBallerino()
    audio, difetto = synthesize_segment(ballerino, "una battuta qualsiasi", "v1")
    check("un segmento col vuoto fa scattare un secondo tentativo",
          ballerino.chiamate == 2, f"{ballerino.chiamate} chiamate")
    check("e il segmento accettato e' quello sano", difetto is None, f"{difetto}")

    sempre_guasto = ProviderBallerino()
    sempre_guasto.text_to_speech = lambda text, voice_id: (
        setattr(sempre_guasto, "chiamate", sempre_guasto.chiamate + 1)
        or wav_bytes(pcm((3000, voce), (36000, rumore), (5000, voce)))
    )
    _, difetto = synthesize_segment(sempre_guasto, "battuta", "v1", dropout_max_retries=2)
    check("se resta guasto si ferma dopo i tentativi previsti",
          sempre_guasto.chiamate == 3, f"{sempre_guasto.chiamate} chiamate")
    check("e il difetto viene riportato invece di essere accettato in silenzio",
          difetto is not None and "vuoto continuo" in difetto.describe(), f"{difetto}")

    print(f"\n{len(PASSED)} controlli superati.")
    print(f"Artefatti del test in: {root}")


if __name__ == "__main__":
    main()
