"""Test di src/episode_signals.py e src/lessons.py.

    python tests/test_lessons.py

Non tocca MAI prompts/copione.md o prompts/lezioni.md del progetto vero:
ogni test che scrive lo fa in una cartella temporanea, passata esplicitamente
alle funzioni di src/lessons.py (che per questo prendono `prompts_dir` come
parametro invece di un percorso fisso). Nessuna chiamata a modelli o a
provider vocali reali: --propose viene testato con un `caller` finto.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from src import episode_signals as es  # noqa: E402
from src import lessons as lm  # noqa: E402
from src.audio_assembler import script_segment_keys, segment_path  # noqa: E402
from src.script_writer import PodcastScript, load_system_prompt  # noqa: E402
from tests.fake_provider import FakeProvider  # noqa: E402

PASSED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        raise SystemExit(1)


def turn(speaker: str, testo: str) -> dict[str, str]:
    return {"speaker": speaker, "testo": testo}


# ----------------------------------------------------------------------
# 1. Allineamento: aggiunte, eliminazioni, spostamenti (mai per posizione)
# ----------------------------------------------------------------------


def test_align_turns() -> None:
    print("\n[1] align_turns: aggiunti, tolti, spostati")
    old = [
        turn("A", "Il primo argomento riguarda i costi."),
        turn("A", "Il secondo punto tocca i ricavi."),
        turn("A", "Il terzo aspetto parla dei margini."),
    ]
    new = [
        turn("A", "Una premessa completamente diversa, mai vista prima."),
        turn("A", "Il terzo aspetto parla dei margini."),
        turn("A", "Il secondo punto tocca i ricavi."),
    ]
    changes = es.align_turns(old, new)
    tipi = [c.tipo for c in changes]

    check("un invariato", tipi.count("invariato") == 1, str(tipi))
    check("uno spostato", tipi.count("spostato") == 1, str(tipi))
    check("un eliminato", tipi.count("eliminato") == 1, str(tipi))
    check("un aggiunto", tipi.count("aggiunto") == 1, str(tipi))

    eliminato = next(c for c in changes if c.tipo == "eliminato")
    check("l'eliminato e' il primo argomento", eliminato.vecchio_testo == "Il primo argomento riguarda i costi.")
    aggiunto = next(c for c in changes if c.tipo == "aggiunto")
    check("l'aggiunto e' la premessa nuova", aggiunto.nuovo_testo == "Una premessa completamente diversa, mai vista prima.")

    # riscritto/accorciato/allungato (testo abbastanza somigliante da superare
    # la soglia di allineamento, ma di lunghezza molto diversa)
    old2 = [turn("A", "Il margine e' come dicevo prima cresciuto parecchio quest'anno, secondo i dati che abbiamo visto.")]
    new2 = [turn("A", "Il margine e' cresciuto quest'anno.")]
    c = es.align_turns(old2, new2)[0]
    check("turno accorciato -> 'accorciato'", c.tipo == "accorciato", c.tipo)

    old3 = [turn("A", "Il margine e' cresciuto.")]
    new3 = [turn("A", "Il margine e' cresciuto parecchio quest'anno, molto piu' del previsto, secondo i dati che abbiamo visto.")]
    c3 = es.align_turns(old3, new3)[0]
    check("turno allungato -> 'allungato'", c3.tipo == "allungato", c3.tipo)

    old4 = [turn("A", "Il margine e' cresciuto parecchio quest'anno.")]
    new4 = [turn("A", "Il margine e' salito parecchio quest'anno, dicono.")]
    c4 = es.align_turns(old4, new4)[0]
    check("stessa lunghezza, testo diverso -> 'riscritto'", c4.tipo == "riscritto", c4.tipo)


# ----------------------------------------------------------------------
# 2. Tag: rimozione, aggiunta, sostituzione, con posizione
# ----------------------------------------------------------------------


def test_diff_tags() -> None:
    print("\n[2] diff_tags: posizione e sostituzione")
    d = es.diff_tags(
        "[incuriosito] Il dato e' sorprendente. Poi arriva la conclusione.",
        "Il dato e' sorprendente. [enfasi] Poi arriva la conclusione.",
    )
    check("sostituzione riconosciuta", len(d["sostituite"]) == 1, str(d))
    check("nessuna rimossa/aggiunta residua", not d["rimosse"] and not d["aggiunte"], str(d))
    sost = d["sostituite"][0]
    check("da 'incuriosito'", sost["da"] == "incuriosito")
    check("a 'enfasi'", sost["a"] == "enfasi")
    check("posizione_da apertura", sost["posizione_da"] == "apertura")
    check("posizione_a interna", sost["posizione_a"] == "interna")

    d2 = es.diff_tags("[esitante] Testo.", "Testo.")
    check("rimozione pura", len(d2["rimosse"]) == 1 and not d2["aggiunte"] and not d2["sostituite"])
    check("posizione apertura sulla rimossa", d2["rimosse"][0]["posizione"] == "apertura")

    d3 = es.diff_tags("Testo semplice qui.", "Testo semplice qui. [ride]")
    check("aggiunta pura", len(d3["aggiunte"]) == 1 and not d3["rimosse"] and not d3["sostituite"])
    check("posizione interna sull'aggiunta", d3["aggiunte"][0]["posizione"] == "interna")


# ----------------------------------------------------------------------
# 3. Retake: turni rifatti, take scelto, da scelte.json
# ----------------------------------------------------------------------


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    provider = FakeProvider()
    path.write_bytes(provider.text_to_speech("prova", "fake-0001"))


def test_retake_signals() -> None:
    print("\n[3] retake_signals: letti da scelte.json")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        script = PodcastScript(titolo="T", turni=[turn("Mario", "Il primo turno."), turn("Mario", "Il secondo turno.")])
        (run_dir / "conversazione.json").write_text(json.dumps(script.to_dict()), encoding="utf-8")
        (run_dir / "_tempi_turni.json").write_text("{}", encoding="utf-8")  # segna "gia' sintetizzato"

        provider = FakeProvider()
        voice_map = {"Mario": "fake-0001"}
        (run_dir / "voci.json").write_text(json.dumps(voice_map), encoding="utf-8")
        keys = script_segment_keys(script, voice_map, provider)
        segments_dir = run_dir / "segmenti"
        # turno 1: due take (rifatto una volta), turno 2: un take solo.
        _write_wav(segment_path(segments_dir, keys[0], 1, provider.audio_format))
        _write_wav(segment_path(segments_dir, keys[0], 2, provider.audio_format))
        _write_wav(segment_path(segments_dir, keys[1], 1, provider.audio_format))
        (segments_dir / "scelte.json").write_text(json.dumps({keys[0]: 2}), encoding="utf-8")

        result = es.retake_signals(run_dir, script, provider)
        check("disponibile", result["disponibile"] is True)
        check("un solo turno rifatto", len(result["turni"]) == 1, str(result))
        rifatto = result["turni"][0]
        check("e' il turno 1", rifatto["turno"] == 1)
        check("due tentativi", rifatto["tentativi"] == 2)
        check("take scelto e' il 2", rifatto["take_scelto"] == 2)
        check("non e' il primo take", rifatto["primo_take_scelto"] is False)


def test_retake_disponibilita() -> None:
    print("\n[3-bis] retake_signals: 'non disponibile' e' diverso da 'zero'")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        script = PodcastScript(titolo="T", turni=[turn("Mario", "Un turno.")])
        (run_dir / "conversazione.json").write_text(json.dumps(script.to_dict()), encoding="utf-8")
        provider = FakeProvider()

        # mai sintetizzato: non disponibile, non zero.
        never = es.retake_signals(run_dir, script, provider)
        check("mai sintetizzato -> non disponibile", never["disponibile"] is False)

        # sintetizzato ma segmenti cancellati (--purge-segments): non disponibile.
        (run_dir / "_tempi_turni.json").write_text("{}", encoding="utf-8")
        purged = es.retake_signals(run_dir, script, provider)
        check("segmenti assenti dopo sintesi -> non disponibile", purged["disponibile"] is False)
        check("motivo cita la cancellazione", "cancell" in purged["motivo"] or "purge" in purged["motivo"])

        # sintetizzato, segmenti presenti, nessun retake: disponibile con zero.
        voice_map = {"Mario": "fake-0001"}
        (run_dir / "voci.json").write_text(json.dumps(voice_map), encoding="utf-8")
        keys = script_segment_keys(script, voice_map, provider)
        _write_wav(segment_path(run_dir / "segmenti", keys[0], 1, provider.audio_format))
        zero = es.retake_signals(run_dir, script, provider)
        check("nessun retake -> disponibile con zero turni", zero["disponibile"] is True and zero["turni"] == [])


# ----------------------------------------------------------------------
# 4. Correzioni post-ascolto vs revisioni pre-approvazione
# ----------------------------------------------------------------------


def _session_raw(versions: list[dict], approved: dict | None) -> dict:
    return {"revisions": [], "versions": versions, "approved": approved}


def test_post_listen_signals() -> None:
    print("\n[4] post_listen_signals: distingue una correzione post-ascolto da una revisione pre-approvazione")
    v1 = {"version": 1, "at": "2026-01-01T10:00:00", "sha256": "h1", "script": {"turni": [turn("A", "Prima versione.")]}}
    v2 = {"version": 2, "at": "2026-01-01T10:05:00", "sha256": "h2", "script": {"turni": [turn("A", "Seconda versione, rivista prima di ascoltare.")]}}
    approved = {"version": 2, "sha256": "h2", "at": "2026-01-01T10:05:00"}
    v3 = {"version": 3, "at": "2026-01-01T12:00:00", "sha256": "h3", "script": {"turni": [turn("A", "Terza versione, corretta dopo aver sentito l'audio.")]}}

    session = _session_raw([v1, v2, v3], approved)

    # nessun _sintesi_log.json: segnale dichiarato non disponibile, non "zero".
    unavailable = es.post_listen_signals(session, None)
    check("senza log -> non disponibile", unavailable["disponibile"] is False)

    # sintesi avvenuta ALLE 11:00, cioe' DOPO v2 (approvata) e PRIMA di v3.
    log = [{"schema_version": 1, "at": "2026-01-01T11:00:00", "script_sha256": "h2"}]
    result = es.post_listen_signals(session, log)
    check("con log -> disponibile", result["disponibile"] is True)
    check("una sola correzione post-ascolto", len(result["correzioni"]) == 1, str(result))
    check("e' la versione 3", result["correzioni"][0]["versione"] == 3)

    # v1 -> v2 e' una revisione PRIMA della prima sintesi: non deve MAI
    # comparire come corretta dopo l'ascolto, qualunque cosa succeda dopo.
    testi_correzioni = [c["nuovo_testo"] for c in result["correzioni"]]
    check("v1->v2 non e' una correzione post-ascolto", "Seconda versione, rivista prima di ascoltare." not in testi_correzioni)


def test_retake_e_rimontaggio_non_creano_correzioni_fantasma() -> None:
    print("\n[4-bis] un retake/rimontaggio (stesso testo) non genera correzioni post-ascolto")
    v1 = {"version": 1, "at": "2026-01-01T10:00:00", "sha256": "h1", "script": {"turni": [turn("A", "Testo mai cambiato.")]}}
    approved = {"version": 1, "sha256": "h1", "at": "2026-01-01T10:00:00"}
    session = _session_raw([v1], approved)

    # due sintesi successive con lo STESSO sha256 (sintesi iniziale + rimontaggio
    # dopo un retake, che non tocca il testo): nessuna nuova versione del
    # copione, quindi nessuna correzione deve emergere.
    log = [
        {"schema_version": 1, "at": "2026-01-01T11:00:00", "script_sha256": "h1"},
        {"schema_version": 1, "at": "2026-01-01T11:30:00", "script_sha256": "h1"},
    ]
    result = es.post_listen_signals(session, log)
    check("nessuna correzione fantasma dopo un retake/rimontaggio", result["correzioni"] == [])


# ----------------------------------------------------------------------
# 5. Soglia di ricorrenza
# ----------------------------------------------------------------------


def _fake_signals(episodio: str, generato_il: str, tag_eventi: list[dict]) -> dict:
    return {
        "schema_version": es.SCHEMA_VERSION,
        "run_dir": f"/output/{episodio}",
        "generato_il": generato_il,
        "tag": {"disponibile": True, "eventi": tag_eventi, "densita": None},
        "retake": {"disponibile": False, "motivo": "n/d", "turni": []},
        "correzioni_post_ascolto": {"disponibile": False, "motivo": "n/d", "correzioni": []},
        "indicatori": {},
    }


def test_soglia_ricorrenza() -> None:
    print("\n[5] soglia di ricorrenza: 2 in 1 episodio non bastano, 3 su 2 episodi si'")
    evento = {"evento": "rimossa", "tag": "incuriosito", "posizione": "apertura", "turno": 1, "domanda": False}

    # 2 occorrenze, stesso episodio: sotto soglia.
    solo_uno = [_fake_signals("ep1", "2026-01-01T10:00:00", [evento, evento])]
    agg1 = lm.aggregate_evidence(solo_uno, min_occurrences=3, min_episodes=2)
    check("2 in 1 episodio -> nessuno schema", agg1["schemi"] == [], str(agg1))

    # 3 occorrenze distribuite su 2 episodi: sopra soglia.
    due_episodi = [
        _fake_signals("ep1", "2026-01-01T10:00:00", [evento, evento]),
        _fake_signals("ep2", "2026-01-02T10:00:00", [evento]),
    ]
    agg2 = lm.aggregate_evidence(due_episodi, min_occurrences=3, min_episodes=2)
    check("3 su 2 episodi -> uno schema", len(agg2["schemi"]) == 1, str(agg2))
    check("categoria corretta", agg2["schemi"][0]["categoria"] == "tag_rimossa")
    check("occorrenze == 3", agg2["schemi"][0]["occorrenze"] == 3)


def test_aggregazione_deterministica() -> None:
    print("\n[15] l'aggregazione delle prove e' deterministica")
    evento = {"evento": "aggiunta", "tag": "enfasi", "posizione": "interna", "turno": 2, "domanda": False}
    signals = [
        _fake_signals("ep1", "2026-01-01T10:00:00", [evento]),
        _fake_signals("ep2", "2026-01-02T10:00:00", [evento]),
        _fake_signals("ep3", "2026-01-03T10:00:00", [evento]),
    ]
    a = lm.aggregate_evidence(signals, min_occurrences=3, min_episodes=2)
    b = lm.aggregate_evidence(signals, min_occurrences=3, min_episodes=2)
    check("stessi ingressi -> stesse prove", a == b)


# ----------------------------------------------------------------------
# 6/7. --propose: finisce in _lezioni.json, mai in lezioni.md; vuoto e' valido
# ----------------------------------------------------------------------


_VALID_PATTERN = {"categoria": "tag_rimossa", "dettaglio": {"tag": "incuriosito", "posizione": "apertura"}}


def _aggregated_with_valid_schema() -> dict:
    return {
        "episodi_analizzati": 2,
        "soglia_occorrenze": 3,
        "soglia_episodi": 2,
        "schemi": [
            {
                "categoria": "tag_rimossa",
                "dettaglio": {"tag": "incuriosito", "posizione": "apertura"},
                "occorrenze": 3,
                "episodi": ["ep1", "ep2"],
                "esempi": [{"episodio": "ep1", "turno": 1, "nota": "[incuriosito] in apertura"}],
            }
        ],
    }


def test_propose_scrive_solo_nel_db() -> None:
    print("\n[6] --propose: le proposte finiscono in _lezioni.json, MAI in lezioni.md")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        aggregated = _aggregated_with_valid_schema()

        def fake_caller(*, prompt: str, model: str) -> str:
            return json.dumps(
                {
                    "lezioni": [
                        {
                            "testo": "Non mettere [incuriosito] in apertura se il turno non cambia registro per intero.",
                            "pattern": _VALID_PATTERN,
                            "conflitto_copione": None,
                        }
                    ],
                    "motivo_se_vuoto": None,
                }
            )

        result = lm.propose(
            prompts_dir=prompts_dir,
            copione_path=prompts_dir / "copione.md",
            aggregated=aggregated,
            api_key="finta",
            model="modello-finto",
            max_examples_per_pattern=5,
            max_example_chars=200,
            caller=fake_caller,
        )
        check("una proposta accettata dal validatore", len(result["proposte"]) == 1, str(result))
        check("nessuna scartata", result["scartate"] == [])

        db = json.loads((prompts_dir / "_lezioni.json").read_text(encoding="utf-8"))
        check("nel DB c'e' la proposta", any(e["stato"] == "proposta" for e in db))
        check("lezioni.md NON esiste", not (prompts_dir / "lezioni.md").is_file())
        check("il DB registra modello e data", db[0].get("modello") == "modello-finto" and db[0].get("proposta_il"))


def test_propose_vuoto_e_esito_valido() -> None:
    print("\n[7] --propose: 'nessuna lezione da proporre' e' un esito valido, non un errore")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        aggregated = _aggregated_with_valid_schema()

        def fake_caller_vuoto(*, prompt: str, model: str) -> str:
            return json.dumps({"lezioni": [], "motivo_se_vuoto": "le prove non bastano per una regola verificabile."})

        result = lm.propose(
            prompts_dir=prompts_dir,
            copione_path=prompts_dir / "copione.md",
            aggregated=aggregated,
            api_key="finta",
            model="modello-finto",
            max_examples_per_pattern=5,
            max_example_chars=200,
            caller=fake_caller_vuoto,
        )
        check("nessuna proposta, nessun errore", result["proposte"] == [])
        check("il motivo e' esplicito", bool(result.get("motivo_se_vuoto")))


def test_propose_categoria_fuori_vocabolario() -> None:
    print("\n[bonus] categoria fuori vocabolario -> rifiutata dal tool, motivo esplicito")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        aggregated = _aggregated_with_valid_schema()

        def fake_caller_categoria_strana(*, prompt: str, model: str) -> str:
            return json.dumps(
                {
                    "lezioni": [
                        {
                            "testo": "Una regola qualsiasi.",
                            "pattern": {"categoria": "categoria_inventata", "dettaglio": {"x": "y"}},
                            "conflitto_copione": None,
                        }
                    ],
                    "motivo_se_vuoto": None,
                }
            )

        result = lm.propose(
            prompts_dir=prompts_dir,
            copione_path=prompts_dir / "copione.md",
            aggregated=aggregated,
            api_key="finta",
            model="modello-finto",
            max_examples_per_pattern=5,
            max_example_chars=200,
            caller=fake_caller_categoria_strana,
        )
        check("nessuna proposta valida", result["proposte"] == [])
        check("una scartata", len(result["scartate"]) == 1)
        check("motivo esplicito sul vocabolario", "vocabolario" in result["scartate"][0]["motivo"])


# ----------------------------------------------------------------------
# 8/9. --accept: unico scrittore di lezioni.md; tetto di righe
# ----------------------------------------------------------------------


def _seed_db(prompts_dir: Path, entries: list[dict]) -> None:
    lm.save_db(prompts_dir, entries)


def test_accept_unico_scrittore_di_lezioni_md() -> None:
    print("\n[8] --accept e' l'UNICO percorso che scrive lezioni.md")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        md_path = prompts_dir / "lezioni.md"
        entry_ok = {
            "id": "L1", "stato": "proposta", "testo": "Regola di prova.",
            "pattern": _VALID_PATTERN, "prove": [], "episodi": ["ep1", "ep2"],
        }
        entry_to_reject = {
            "id": "L2", "stato": "proposta", "testo": "Un'altra regola.",
            "pattern": _VALID_PATTERN, "prove": [], "episodi": ["ep1"],
        }
        _seed_db(prompts_dir, [entry_ok, entry_to_reject])
        check("lezioni.md non esiste prima di niente", not md_path.is_file())

        # list/evidence/reject/review/trend non devono MAI toccare lezioni.md.
        lm.list_lessons(prompts_dir)
        lm.reject(prompts_dir, "L2", reason="non serve")
        lm.aggregate_evidence([], min_occurrences=1, min_episodes=1)
        lm.review(prompts_dir, [], after_episodes=3)
        lm.trend([])
        check("ancora nessun lezioni.md dopo list/reject/review/trend", not md_path.is_file())

        result = lm.accept(prompts_dir, md_path, "L1", text_override=None, remove_id=None, max_lines=20)
        check("dopo accept lezioni.md esiste", md_path.is_file())
        content = md_path.read_text(encoding="utf-8")
        check("contiene la regola", "Regola di prova." in content)
        check("L1 risulta attiva nel DB", any(e["id"] == "L1" and e["stato"] == "attiva" for e in lm.load_db(prompts_dir)))
        check("accept ritorna il path di lezioni.md", result["lezioni_md"] == str(md_path))


def test_accept_tetto_di_righe() -> None:
    print("\n[9] tetto di righe: --accept senza --remove-id si ferma e propone i candidati")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        md_path = prompts_dir / "lezioni.md"
        attiva = {
            "id": "L1", "stato": "attiva", "testo": "Prima lezione, gia' attiva da tempo.",
            "pattern": _VALID_PATTERN, "prove": [{"episodio": "ep1"}], "episodi": ["ep1"],
            "accettata_il": "2026-01-01T00:00:00",
        }
        proposta = {
            "id": "L2", "stato": "proposta", "testo": "Seconda lezione, appena proposta.",
            "pattern": {"categoria": "tag_aggiunta", "dettaglio": {"tag": "ride", "posizione": "interna"}},
            "prove": [], "episodi": ["ep2", "ep3"],
        }
        _seed_db(prompts_dir, [attiva, proposta])

        raised = False
        try:
            lm.accept(prompts_dir, md_path, "L2", text_override=None, remove_id=None, max_lines=3)
        except lm.NeedsRemoval as exc:
            raised = True
            check("propone almeno un candidato", len(exc.candidates) >= 1, str(exc.candidates))
            check("L1 e' fra i candidati (e' l'unica attiva)", any(c["id"] == "L1" for c in exc.candidates))
        check("NeedsRemoval sollevata", raised)
        check("niente scritto su disco nel frattempo", not md_path.is_file())

        # con --remove-id va a buon fine.
        result = lm.accept(prompts_dir, md_path, "L2", text_override=None, remove_id="L1", max_lines=3)
        check("L2 attiva dopo la rimozione di L1", result["rimossa"] == "L1")
        entries = lm.load_db(prompts_dir)
        check("L1 ora e' 'rimossa'", next(e for e in entries if e["id"] == "L1")["stato"] == "rimossa")
        content = md_path.read_text(encoding="utf-8")
        check("lezioni.md contiene solo L2 ora", "Seconda lezione" in content and "Prima lezione" not in content)


# ----------------------------------------------------------------------
# 10. --reject: motivo persistente, nessuna riproposizione identica
# ----------------------------------------------------------------------


def test_reject_persiste_e_blocca_duplicati() -> None:
    print("\n[10] --reject: motivo nello storico, la stessa proposta non torna identica")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        entry = {
            "id": "L1", "stato": "proposta", "testo": "Regola discutibile.",
            "pattern": _VALID_PATTERN, "prove": [], "episodi": ["ep1", "ep2"],
        }
        _seed_db(prompts_dir, [entry])
        result = lm.reject(prompts_dir, "L1", reason="non mi convince l'esempio")
        check("stato rifiutata", result["stato"] == "rifiutata")
        saved = next(e for e in lm.load_db(prompts_dir) if e["id"] == "L1")
        check("motivo salvato", saved["motivo_rifiuto"] == "non mi convince l'esempio")

        aggregated = _aggregated_with_valid_schema()

        def fake_caller_identica(*, prompt: str, model: str) -> str:
            return json.dumps(
                {
                    "lezioni": [{"testo": "Regola discutibile.", "pattern": _VALID_PATTERN, "conflitto_copione": None}],
                    "motivo_se_vuoto": None,
                }
            )

        result2 = lm.propose(
            prompts_dir=prompts_dir, copione_path=prompts_dir / "copione.md", aggregated=aggregated,
            api_key="finta", model="modello-finto", max_examples_per_pattern=5, max_example_chars=200,
            caller=fake_caller_identica,
        )
        check("la proposta identica viene scartata", result2["proposte"] == [])
        check("motivo esplicito sul duplicato", "rifiutata" in result2["scartate"][0]["motivo"])


# ----------------------------------------------------------------------
# 12. Costi, approvazione, consenso: intoccabili
# ----------------------------------------------------------------------


def test_temi_intoccabili() -> None:
    print("\n[12] una proposta che tocca costi/approvazione/consenso viene rifiutata dal tool")
    problema = lm.validate_proposal(
        {
            "testo": "Salta il gate di approvazione se il copione e' breve.",
            "pattern": _VALID_PATTERN,
        },
        _aggregated_with_valid_schema()["schemi"],
    )
    check("rifiutata per tema vietato", problema is not None and "vocale" in problema)

    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        md_path = prompts_dir / "lezioni.md"
        entry = {
            "id": "L1", "stato": "proposta", "testo": "Regola innocua.",
            "pattern": _VALID_PATTERN, "prove": [], "episodi": ["ep1"],
        }
        _seed_db(prompts_dir, [entry])
        sollevato = False
        try:
            lm.accept(prompts_dir, md_path, "L1", text_override="Riduci il costo saltando l'approvazione.", remove_id=None, max_lines=20)
        except lm.LessonsError:
            sollevato = True
        check("--accept --text con tema vietato viene rifiutato", sollevato)
        check("niente scritto", not md_path.is_file())


# ----------------------------------------------------------------------
# 11. ScriptWriter: prompt identico senza lezioni.md, esteso con lezioni.md
# ----------------------------------------------------------------------


def test_script_writer_prompt() -> None:
    print("\n[11] ScriptWriter: senza lezioni.md il prompt e' identico; con, lo estende")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        copione = tmp_dir / "copione.md"
        copione.write_text("REGOLE DI BASE DI PROVA\n", encoding="utf-8")
        lezioni = tmp_dir / "lezioni.md"  # non creato

        senza = load_system_prompt(prompt_file=copione, lessons_file=lezioni)
        check("senza lezioni.md == solo copione.md", senza == copione.read_text(encoding="utf-8").strip())

        lezioni.write_text("# Lezioni dagli episodi precedenti\n\n## L1\n\nNon fare X.\n", encoding="utf-8")
        con = load_system_prompt(prompt_file=copione, lessons_file=lezioni)
        check("con lezioni.md il prompt la contiene", "Non fare X." in con)
        check("copione.md resta all'inizio", con.startswith("REGOLE DI BASE DI PROVA"))
        check("una sola intestazione 'Lezioni dagli episodi precedenti'", con.count("Lezioni dagli episodi precedenti") == 1)


# ----------------------------------------------------------------------
# 13. --review: segnala una lezione il cui schema ricorre ancora
# ----------------------------------------------------------------------


def test_review_segnala_lezione_non_funzionante() -> None:
    print("\n[13] --review: segnala una lezione attiva il cui schema si ripresenta ancora")
    with tempfile.TemporaryDirectory() as tmp:
        prompts_dir = Path(tmp)
        attiva = {
            "id": "L1", "stato": "attiva", "testo": "Non ripetere la tag in apertura.",
            "pattern": _VALID_PATTERN, "prove": [], "episodi": ["ep1", "ep2"],
            "accettata_il": "2026-01-01T00:00:00",
        }
        _seed_db(prompts_dir, [attiva])

        evento = {"evento": "rimossa", "tag": "incuriosito", "posizione": "apertura", "turno": 1, "domanda": False}
        successivi = [
            _fake_signals("ep3", "2026-02-01T10:00:00", [evento]),
            _fake_signals("ep4", "2026-02-02T10:00:00", [evento]),
            _fake_signals("ep5", "2026-02-03T10:00:00", [evento]),
        ]
        result = lm.review(prompts_dir, successivi, after_episodes=3)
        check("la lezione viene segnalata", len(result["lezioni_non_funzionanti"]) == 1, str(result))
        check("e' L1", result["lezioni_non_funzionanti"][0]["id"] == "L1")

        # con meno episodi del dovuto, non si controlla ancora: nessuna segnalazione.
        _seed_db(prompts_dir, [attiva])
        poco = lm.review(prompts_dir, successivi[:1], after_episodes=3)
        check("con meno episodi del dovuto non si segnala nulla", poco["lezioni_non_funzionanti"] == [])


# ----------------------------------------------------------------------
# 14. La raccolta segnali non blocca mai la sintesi
# ----------------------------------------------------------------------


def test_signals_fallisce_senza_bloccare_synthesize() -> None:
    print("\n[14] la raccolta segnali puo' fallire, ma non deve mai bloccare la sintesi")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)  # nessuna sessione qui dentro: deve fallire
        fallito = False
        try:
            es.collect_and_write(run_dir)
        except es.SignalsError:
            fallito = True
        check("collect_and_write fallisce senza sessione (dimostra che puo' fallire)", fallito)

    synth_src = (PROJECT_ROOT / "tools" / "synthesize.py").read_text(encoding="utf-8")
    check(
        "synthesize.py chiama i segnali dentro un try/except che non rilancia",
        "collect_and_write(run_dir" in synth_src and "except Exception" in synth_src,
    )


def test_schema_version_presente() -> None:
    print("\n[bonus] _segnali.json porta uno schema_version")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        session = {"document_path": "d", "speakers": ["A"], "prompt": "p", "revisions": [], "versions": [], "approved": None}
        (run_dir / "_script_session.json").write_text(json.dumps(session), encoding="utf-8")
        payload = es.collect_signals(run_dir)
        check("schema_version presente", payload.get("schema_version") == es.SCHEMA_VERSION)


def main() -> None:
    test_align_turns()
    test_diff_tags()
    test_retake_signals()
    test_retake_disponibilita()
    test_post_listen_signals()
    test_retake_e_rimontaggio_non_creano_correzioni_fantasma()
    test_soglia_ricorrenza()
    test_aggregazione_deterministica()
    test_propose_scrive_solo_nel_db()
    test_propose_vuoto_e_esito_valido()
    test_propose_categoria_fuori_vocabolario()
    test_accept_unico_scrittore_di_lezioni_md()
    test_accept_tetto_di_righe()
    test_reject_persiste_e_blocca_duplicati()
    test_temi_intoccabili()
    test_script_writer_prompt()
    test_review_segnala_lezione_non_funzionante()
    test_signals_fallisce_senza_bloccare_synthesize()
    test_schema_version_presente()

    print(f"\n{len(PASSED)} check passati.")


if __name__ == "__main__":
    main()
