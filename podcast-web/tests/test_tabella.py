import os

import pytest

from app.agente.tabella import (
    OPZIONI_VIETATE, TABELLA, TOOL_VIETATI, ErroreArgomento, ErroreTabella, RunNonTrovata,
    costruisci_argomenti, verifica_tabella,
)


@pytest.fixture
def out(agente):
    return agente.radice / "output"


def test_tabella_coerente():
    verifica_tabella()
    for nome, cmd in TABELLA.items():
        assert cmd.tool not in TOOL_VIETATI
        assert not (set(cmd.fissi) & OPZIONI_VIETATE)
        assert all(a.param != "by" for a in cmd.argomenti), nome


def test_lavori_lunghi_e_timeout_del_contratto():
    lunghi = {n for n, c in TABELLA.items() if c.lungo}
    assert {"scrivi_copione", "revisiona_copione", "produci", "genera_retake"} <= lunghi
    assert TABELLA["produci"].timeout is None and TABELLA["scrivi_copione"].timeout == 600
    assert TABELLA["documento"].timeout == 240 and TABELLA["anteprima_documento"].timeout == 120
    assert TABELLA["ritenta_clonazione"].timeout == 660


def test_forma_opzione_uguale_valore(out):
    argv = costruisci_argomenti("modifica_battuta", {"run": "run-uno", "battuta": 4, "testo": "-Davvero?", "versione_attesa": 2},
                                output_dir=out, by="Mario Rossi")
    assert argv == ["--run-dir=run-uno", "--turn=4", "--text=-Davvero?", "--expected-version=2", "--by=Mario Rossi"]


def test_posizionali_dopo_doppio_trattino(out):
    argv = costruisci_argomenti("anteprima_documento", {"nome": "report.txt", "caratteri": 300}, output_dir=out)
    assert argv == ["--preview-chars=300", "--", "report.txt"]


def test_fissi_e_by(out):
    argv = costruisci_argomenti("ritira_approvazione", {"run": "run-uno"}, output_dir=out, by="Anna")
    assert argv == ["--run-dir=run-uno", "--revoke", "--by=Anna"]
    with pytest.raises(ErroreTabella):
        costruisci_argomenti("ritira_approvazione", {"run": "run-uno"}, output_dir=out)


def test_parametro_fuori_tabella(out):
    for extra in ({"by": "Altro"}, {"config": "x"}, {"trusted_paths": True}):
        with pytest.raises(ErroreTabella):
            costruisci_argomenti("copione", {"run": "run-uno", **extra}, output_dir=out)
    with pytest.raises(ErroreTabella):
        costruisci_argomenti("non_esiste", {}, output_dir=out)


def test_flag_di_conferma_solo_quelli_previsti(out):
    argv = costruisci_argomenti("produci", {"run": "run-uno"}, output_dir=out, flag_conferme=("--accept-changed-music",))
    assert argv == ["--run-dir=run-uno", "--events", "--accept-changed-music"]
    with pytest.raises(ErroreTabella):
        costruisci_argomenti("produci", {"run": "run-uno"}, output_dir=out, flag_conferme=("--trusted-paths",))
    with pytest.raises(ErroreTabella):
        costruisci_argomenti("copione", {"run": "run-uno"}, output_dir=out, flag_conferme=("--accept-changed-music",))


@pytest.mark.parametrize("nome", ["../x", "a/b", "a\\b", ".nascosto", "-opzione", "", " spazio", "a\x00b", "x" * 201, 5, None])
def test_nomi_rifiutati(out, nome):
    with pytest.raises((ErroreArgomento, TypeError)):
        costruisci_argomenti("documento", {"nome": nome}, output_dir=out)


def test_nomi_ammessi(out):
    assert costruisci_argomenti("documento", {"nome": "Report Q3 è.pdf"}, output_dir=out) == ["--name=Report Q3 è.pdf"]


@pytest.mark.parametrize("run", ["..", "../run-uno", "run-uno/..", "/tmp", ".nascosta", "-x", "a b"])
def test_run_malformate(out, run):
    with pytest.raises(ErroreArgomento):
        costruisci_argomenti("copione", {"run": run}, output_dir=out)


def test_run_inesistente_o_link(out, tmp_path):
    with pytest.raises(RunNonTrovata):
        costruisci_argomenti("copione", {"run": "manca"}, output_dir=out)
    (tmp_path / "fuori").mkdir()
    os.symlink(tmp_path / "fuori", out / "collegata")
    with pytest.raises(RunNonTrovata):
        costruisci_argomenti("copione", {"run": "collegata"}, output_dir=out)
    (out / "file").write_text("x")
    with pytest.raises(RunNonTrovata):
        costruisci_argomenti("copione", {"run": "file"}, output_dir=out)


def test_tipi_composti(out):
    argv = costruisci_argomenti("modifica_configurazione", {
        "run": "run-uno", "formato": "intervista", "minuti": 15,
        "voci": {"presentatore": "Anna Neri"}, "nomi": {"presentatore": "Anna Neri"}, "togli": ["ospite"],
    }, output_dir=out)
    assert argv == ["--run-dir=run-uno", "--formato=intervista", "--minuti=15", "--voce=presentatore=Anna Neri",
                    "--nome=presentatore=Anna Neri", "--togli=ospite"]
    argv = costruisci_argomenti("scegli_musiche", {"run": "run-uno", "intro": "Piano", "nessuna": ["outro", "sottofondo"]}, output_dir=out)
    assert argv == ["--run-dir=run-uno", "--intro=Piano", "--none", "outro", "sottofondo"]


@pytest.mark.parametrize("parametri", [
    {"formato": "monologo"}, {"minuti": 0}, {"minuti": 181}, {"minuti": True}, {"minuti": "15a"},
    {"voci": {"regista": "X"}}, {"voci": {"presentatore": "../x"}}, {"togli": ["-x"]}, {"nomi": "Anna"},
    {"titolo": "x" * 201}, {"titolo": "a\x07b"},
])
def test_valori_non_validi(out, parametri):
    with pytest.raises(ErroreArgomento):
        costruisci_argomenti("modifica_configurazione", {"run": "run-uno", **parametri}, output_dir=out)


def test_scelte_chiuse_non_diventano_opzioni(out):
    with pytest.raises(ErroreArgomento):
        costruisci_argomenti("scegli_musiche", {"run": "run-uno", "nessuna": ["--config"]}, output_dir=out)


def test_obbligatori(out):
    with pytest.raises(ErroreArgomento):
        costruisci_argomenti("approva_copione", {"run": "run-uno"}, output_dir=out, by="A")


def test_parametro_nascosto_solo_previsto(out):
    argv = costruisci_argomenti("lezioni_accetta", {"id": "L3"}, output_dir=out, by="A", parametri_nascosti={"remove_id": "L1"})
    assert argv == ["--accept=L3", "--remove-id=L1", "--by=A"]
    with pytest.raises(ErroreTabella):
        costruisci_argomenti("lezioni_accetta", {"id": "L3"}, output_dir=out, by="A", parametri_nascosti={"config": "x"})
