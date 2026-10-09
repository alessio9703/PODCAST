// Passo 1 · Documento (01-Documento.dc.html).
// Nuovo episodio: niente run finche' non si preme "Avanti" (run_init con titolo e
// documento); "Annulla" torna all'Archivio senza lasciare bozze. Episodio esistente:
// cambia titolo o documento (episode_config).

import { h, icona, sostituisci, numero, data } from "../dom.js";
import { leggi, invia, modifica, sostituisci as put, caricaFile, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, erroreCorreggibile, titolo as titoloEsito } from "../stati.js";
import { puo } from "../sessione.js";

const TIPI = ".pdf,.docx,.txt,.md,.markdown,.rst,.text";
const VISIBILI = 3;

export async function mostraDocumento(pagina, { run, documento = null }) {
  let configurazione = null;
  let stato = null;
  if (run) {
    const [c, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/configurazione`), statoRun(run)]);
    if (!c.corpo.ok) { const box = boxErrore(); sostituisci(pagina, box); mostraErrore(box, c.corpo); return; }
    configurazione = c.corpo.dati;
    stato = s;
  }
  let scelto = configurazione?.documento?.nome || documento || null;
  let tuttiVisibili = false;

  const erroreFile = boxErrore();
  const erroreTitolo = boxErrore();
  const elencoDocumenti = h("div", { class: "elenco-documenti" });
  const estratto = h("div", { class: "estratto", "aria-live": "polite" });
  const inputFile = h("input", { type: "file", id: "file-documento", accept: TIPI, class: "solo-lettori", onchange: (e) => { if (e.target.files[0]) carica(e.target.files[0]); e.target.value = ""; } });
  const titolo = h("input", { type: "text", id: "titolo-episodio", maxlength: "200", value: configurazione?.titolo || "", autocomplete: "off",
    onchange: run ? salvaTitolo : null, "aria-describedby": "aiuto-titolo" });
  const avanti = h("button", { type: "button", class: "pulsante pulsante-primario", onclick: prosegui }, h("span", {}, "Avanti: cast e formato"), icona("avanti"));

  const zona = h("div", { class: "zona-caricamento", tabindex: "-1",
    ondragover: (e) => { e.preventDefault(); zona.classList.add("sopra"); },
    ondragleave: () => zona.classList.remove("sopra"),
    ondrop: (e) => { e.preventDefault(); zona.classList.remove("sopra"); const f = e.dataTransfer.files[0]; if (f) carica(f); } },
    icona("carica"),
    h("p", { class: "forte" }, "Trascina qui il documento"),
    h("p", { class: "nota" }, "PDF, DOCX, TXT o MD"),
    puo("documento_carica")
      ? h("label", { class: "pulsante", for: "file-documento" }, "Scegli un file")
      : h("p", { class: "nota" }, "Non hai il permesso di caricare documenti: scegline uno fra quelli caricati."),
    inputFile);

  sostituisci(pagina,
    intestazionePasso({ run, passo: 1, titolo: configurazione?.titolo, stato: stato?.etichetta || (run ? "Bozza" : "Nuovo episodio"), raggiunto: stato?.passo || 1 }),
    h("h2", { class: "domanda-passo" }, "Da quale documento parte l'episodio?"),
    h("p", { class: "nota" }, "Il testo viene estratto e usato per scrivere il copione."),
    sezione(null, zona, erroreFile,
      h("h3", {}, "Oppure scegli tra i documenti caricati"), elencoDocumenti),
    estratto,
    sezione(null,
      h("label", { for: "titolo-episodio", class: "etichetta" }, "Titolo dell'episodio"),
      h("p", { id: "aiuto-titolo", class: "nota" }, "Lo usiamo per l'archivio e per il nome del file mp3. Puoi cambiarlo quando vuoi."),
      titolo, erroreTitolo),
    piede(
      h("a", { class: "pulsante", href: "#/archivio" }, run ? "Torna all'archivio" : "Annulla"),
      avanti));

  async function caricaElenco() {
    const { corpo } = await leggi("/api/documenti");
    if (!corpo.ok) { mostraErrore(erroreFile, corpo); return; }
    const documenti = (corpo.dati.documenti || []).slice().sort((a, b) => String(b.caricato_il).localeCompare(String(a.caricato_il)));
    const mostrati = tuttiVisibili ? documenti : documenti.slice(0, VISIBILI);
    sostituisci(elencoDocumenti,
      documenti.length ? h("ul", { class: "scelte" }, mostrati.map((d) => h("li", {},
        h("button", { type: "button", class: "scelta", "aria-pressed": String(d.nome === scelto), onclick: () => scegli(d.nome) },
          h("span", { class: "forte" }, d.nome),
          h("span", { class: "nota" }, d.estraibile === false ? "Testo non estraibile" : data(d.caricato_il)))))) : h("p", { class: "nota" }, "Nessun documento caricato."),
      documenti.length > VISIBILI ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => { tuttiVisibili = !tuttiVisibili; caricaElenco(); } },
        tuttiVisibili ? "Mostra meno" : `Vedi tutti (${documenti.length})`) : null);
  }

  async function carica(file, gettone) {
    pulisciErrore(erroreFile);
    sostituisci(estratto, h("p", { class: "nota", role: "status" }, `Caricamento di ${file.name}…`));
    const { stato: s, corpo } = await caricaFile("/api/documenti", file, gettone);
    if (s === 409 && corpo.error_type === "FileEsistente" && corpo.gettone) {
      const esistenti = (corpo.details?.esistenti || []).join(", ");
      const risposta = await domanda({
        titolo: titoloEsito(corpo),
        testo: `Nella libreria c'è già «${esistenti}». Vuoi sostituirlo con «${file.name}»?`,
        nota: "Il documento sostituito viene tolto; gli episodi che lo usavano restano come sono.",
        opzioni: [{ testo: "Annulla", valore: false }, { testo: "Sostituisci", valore: true, primaria: true }],
      });
      if (risposta) return carica(file, corpo.gettone);
      sostituisci(estratto);
      return;
    }
    if (!corpo.ok) { sostituisci(estratto); mostraErrore(erroreFile, corpo, { azione: { testo: "Carica un altro file", fai: () => inputFile.click() } }); return; }
    conferma(`Documento caricato: ${corpo.dati.nome}`);
    await caricaElenco();
    await scegli(corpo.dati.nome);
  }

  async function scegli(nome) {
    pulisciErrore(erroreFile);
    scelto = nome;
    sostituisci(estratto, h("p", { class: "nota", role: "status" }, "Estrazione del testo…"));
    caricaElenco();
    const { corpo } = await leggi(`/api/documenti/${seg(nome)}`);
    if (!corpo.ok) { sostituisci(estratto); mostraErrore(erroreFile, corpo); scelto = null; return; }
    const d = (corpo.dati.documenti || [])[0];
    if (!d || d.estraibile === false) {
      scelto = null;
      sostituisci(estratto);
      erroreCorreggibile(erroreFile, {
        error_type: d?.motivo === "troppo_grande" ? "DocumentTooLarge" : "DocumentNotExtractable",
        messaggio: d?.motivo === "scansione" ? "Sembra una scansione. Carica un PDF con testo selezionabile, oppure un DOCX." : `Testo non estraibile (${d?.motivo || "motivo sconosciuto"}).`,
      }, { azione: puo("documento_carica") ? { testo: "Carica un altro file", fai: () => inputFile.click() } : null });
      return;
    }
    sostituisci(estratto, h("div", { class: "scheda scheda-documento" },
      h("span", { class: "badge badge-neutro" }, d.tipo),
      h("div", {}, h("p", { class: "forte" }, d.nome),
        h("p", { class: "nota" }, [d.pagine ? `${numero(d.pagine)} pagine` : null, `${numero(d.parole)} parole`].filter(Boolean).join(" · "))),
      h("span", { class: "badge badge-ok" }, icona("spunta"), " Testo estratto")));
    if (!titolo.value.trim() && !run) titolo.value = nome.replace(/\.[^.]+$/, "");
    if (run && nome !== configurazione?.documento?.nome) {
      const r = await put(`/api/episodi/${seg(run)}/documento`, { documento: nome });
      if (!r.corpo.ok) { mostraErrore(erroreFile, r.corpo); return; }
      configurazione = r.corpo.dati;
      conferma("Documento dell'episodio sostituito");
    }
  }

  async function salvaTitolo() {
    pulisciErrore(erroreTitolo);
    if (!titolo.value.trim()) return;
    const { corpo } = await modifica(`/api/episodi/${seg(run)}/configurazione`, { titolo: titolo.value.trim() });
    if (!corpo.ok) { mostraErrore(erroreTitolo, corpo); return; }
    conferma("Titolo salvato");
  }

  async function prosegui() {
    pulisciErrore(erroreTitolo);
    pulisciErrore(erroreFile);
    if (!scelto) { erroreCorreggibile(erroreFile, { error_type: "EpisodeConfigError", messaggio: "Scegli o carica il documento di partenza." }); return; }
    if (!titolo.value.trim()) { erroreCorreggibile(erroreTitolo, { error_type: "EpisodeConfigError", messaggio: "Scrivi il titolo dell'episodio." }); titolo.focus(); return; }
    if (run) { location.hash = `#/episodi/${seg(run)}/2`; return; }
    avanti.disabled = true;
    const { corpo } = await invia("/api/episodi", { titolo: titolo.value.trim(), documento: scelto });
    avanti.disabled = false;
    if (!corpo.ok) { mostraErrore(corpo.details?.campo === "titolo" ? erroreTitolo : erroreFile, corpo); return; }
    location.hash = `#/episodi/${seg(corpo.dati.run)}/2`;
  }

  await caricaElenco();
  if (scelto) await scegli(scelto);
  if (!puo("episodio_modifica")) { avanti.disabled = true; titolo.disabled = true; }
}
