// Libreria Documenti (Documenti.dc.html): elenco con filtri e ricerca, caricamento
// (anche piu' file), anteprima del testo, sostituzione, "Crea episodio".

import { h, icona, sostituisci, giorno, numero } from "../dom.js";
import { leggi, seg } from "../api.js";
import { boxErrore, sezione } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, vuoto } from "../stati.js";
import { puo } from "../sessione.js";
import { caricaConSostituzione, sceltaFile } from "../caricamento.js";

const TIPI = ".pdf,.docx,.txt,.md,.markdown,.rst,.text";
const FILTRI = [["", "Tutti"], ["mai_usati", "Mai usati"], ["gia_usati", "Già usati"], ["con_problemi", "Con problemi"]];
const MOTIVI = { scansione: "sembra una scansione", protetto: "file protetto", vuoto: "file vuoto", illeggibile: "file illeggibile", troppo_grande: "documento troppo grande" };

export async function mostraDocumenti(pagina) {
  let filtro = "";
  let cerca = "";
  let attesa = null;
  let estraendo = false;
  const errore = boxErrore();
  const elenco = h("div", { class: "tabella-contenitore", "aria-live": "polite" });
  const anteprima = h("div", { "aria-live": "polite" });
  const filtri = h("div", { class: "filtri", role: "group", "aria-label": "Filtra i documenti" });
  const ricerca = h("input", { type: "search", id: "cerca-documenti", placeholder: "Cerca documenti", autocomplete: "off",
    oninput: (e) => { cerca = e.target.value; clearTimeout(attesa); attesa = setTimeout(carica, 300); } });
  const scelta = sceltaFile(h, { id: "file-libreria-documenti", accept: TIPI, testo: "Carica documento", multiplo: true, suFile: caricaFiles });

  const zona = h("div", { class: "zona-caricamento",
    ondragover: (e) => { e.preventDefault(); zona.classList.add("sopra"); },
    ondragleave: () => zona.classList.remove("sopra"),
    ondrop: (e) => { e.preventDefault(); zona.classList.remove("sopra"); caricaFiles([...e.dataTransfer.files]); } },
  icona("carica"), h("p", {}, "Trascina qui uno o più file · PDF, DOCX, TXT o MD"));

  sostituisci(pagina,
    h("header", { class: "intestazione-pagina" },
      h("div", {}, h("h1", { tabindex: "-1" }, "Documenti"),
        h("p", { class: "nota" }, "I documenti caricati, pronti da trasformare in episodi. Gli stessi che trovi al passo 1.")),
      puo("documento_carica") ? h("div", {}, scelta.etichetta, scelta.input) : null),
    puo("documento_carica") ? zona : null,
    h("div", { class: "barra-filtri" },
      h("label", { class: "campo-ricerca", for: "cerca-documenti" }, icona("cerca"), h("span", { class: "solo-lettori" }, "Cerca documenti"), ricerca),
      filtri),
    errore, anteprima, elenco);

  function disegnaFiltri() {
    sostituisci(filtri, FILTRI.map(([v, t]) => h("button", { type: "button", class: "filtro", "aria-pressed": String(v === filtro),
      onclick: () => { filtro = v; disegnaFiltri(); carica(); } }, t)));
  }

  async function caricaFiles(files) {
    pulisciErrore(errore);
    for (const file of files) {
      const corpo = await caricaConSostituzione("/api/documenti", file, { nota: "Il documento sostituito viene tolto e il nuovo si estrae di nuovo; gli episodi già creati restano come sono." });
      if (!corpo) continue;
      if (!corpo.ok) { mostraErrore(errore, corpo); continue; }
      conferma(`Documento caricato: ${corpo.dati.nome}`);
    }
    await carica();
  }

  async function carica() {
    const q = new URLSearchParams();
    if (filtro) q.set("filtro", filtro);
    if (cerca.trim()) q.set("cerca", cerca.trim());
    const { corpo } = await leggi(`/api/documenti?${q}`);
    if (!corpo.ok) { mostraErrore(errore, corpo, { riprova: carica }); return; }
    const documenti = corpo.dati.documenti || [];
    if (!documenti.length) {
      sostituisci(elenco, !filtro && !cerca.trim()
        ? vuoto({ titolo: "Nessun documento", testo: "Carica un PDF, un DOCX o un file di testo per crearne un episodio." })
        : h("p", { class: "nota" }, "Nessun documento corrisponde alla ricerca."));
      return;
    }
    sostituisci(elenco, h("table", { class: "tabella" },
      h("caption", { class: "solo-lettori" }, "Documenti"),
      h("thead", {}, h("tr", {}, ["Documento", "Contenuto", "Caricato", "Usato in", "Azioni"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, documenti.map(riga))));
    // PDF e DOCX non ancora estratti: uno alla volta, poi si ricarica l'elenco (contratto).
    const daEstrarre = documenti.find((d) => d.in_attesa);
    if (daEstrarre && !estraendo) {
      estraendo = true;
      await leggi(`/api/documenti/${seg(daEstrarre.nome)}`);
      estraendo = false;
      carica();
    }
  }

  function riga(d) {
    const contenuto = d.errore ? h("span", { class: "nota nota-errore" }, String(d.errore))
      : d.in_attesa ? h("span", { class: "nota" }, "Estrazione in corso…")
      : d.estraibile === false ? h("span", { class: "nota nota-errore" }, `Testo non estraibile: ${MOTIVI[d.motivo] || d.motivo || "motivo sconosciuto"}`)
      : [d.pagine ? `${numero(d.pagine)} pagine` : null, d.parole !== null && d.parole !== undefined ? `${numero(d.parole)} parole` : null].filter(Boolean).join(" · ");
    const usabile = d.estraibile !== false && !d.errore && !d.in_attesa;
    return h("tr", {},
      h("td", {}, h("span", { class: "badge badge-neutro" }, d.tipo || ""), " ", h("span", { class: "forte" }, d.nome)),
      h("td", {}, contenuto),
      h("td", {}, giorno(d.caricato_il)),
      h("td", {}, d.usato_in ? `${d.usato_in} ${d.usato_in === 1 ? "episodio" : "episodi"}` : "Mai usato"),
      h("td", {}, h("div", { class: "riga-azioni" },
        usabile ? h("button", { type: "button", class: "pulsante pulsante-piccolo", "aria-label": `Anteprima di ${d.nome}`, onclick: () => mostraAnteprima(d) }, "Anteprima") : null,
        usabile && puo("episodio_modifica") ? h("a", { class: "pulsante pulsante-piccolo", href: `#/nuovo/${seg(d.nome)}`, "aria-label": `Crea un episodio da ${d.nome}` }, "Crea episodio") : null,
        !usabile && puo("documento_carica") ? h("button", { type: "button", class: "pulsante pulsante-piccolo", onclick: () => scelta.input.click() }, "Sostituisci file") : null)));
  }

  async function mostraAnteprima(d) {
    sostituisci(anteprima, h("p", { class: "nota", role: "status" }, "Carico l'anteprima…"));
    const { corpo } = await leggi(`/api/documenti/${seg(d.nome)}/anteprima?caratteri=1500`);
    if (!corpo.ok) { sostituisci(anteprima); mostraErrore(errore, corpo); return; }
    const a = corpo.dati;
    sostituisci(anteprima, sezione(`Anteprima · ${d.nome}`,
      h("p", { class: "nota" }, [a.pages ? `${numero(a.pages)} pagine` : null, `${numero(a.words)} parole`, `${numero(a.chars)} caratteri`].filter(Boolean).join(" · ")),
      h("p", { class: "anteprima-testo" }, a.anteprima || "Anteprima non disponibile."),
      h("div", { class: "riga-azioni" },
        h("a", { class: "pulsante", href: `/api/documenti/${seg(d.nome)}/file`, target: "_blank", rel: "noopener" }, "Apri il file"),
        h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => sostituisci(anteprima) }, "Chiudi"))));
  }

  disegnaFiltri();
  await carica();
}
