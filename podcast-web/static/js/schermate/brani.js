// Libreria Musiche (Musiche.dc.html): brani per intro, sottofondo e outro, ascolto,
// licenza dichiarata (non verificata: si registra chi e quando), parlato e trascrizione
// (entra nei prompt del copione degli altri), caricamento e sostituzione.

import { h, icona, sostituisci, dataOra, durata, nuovoId } from "../dom.js";
import { leggi, modifica, seg } from "../api.js";
import { boxErrore, sezione } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, vuoto } from "../stati.js";
import { puo, sessione } from "../sessione.js";
import { caricaConSostituzione } from "../caricamento.js";

const CATEGORIE = [["intro", "Intro"], ["sottofondo", "Sottofondo"], ["outro", "Outro"]];
const FILTRI = [["", "Tutti"], ["strumentali", "Solo strumentali"], ["con_parlato", "Con parlato"], ["licenza_mancante", "Licenza mancante"]];
const NOTA_SOSTITUZIONE = "Gli episodi già montati con questo brano chiederanno conferma prima di usare quello nuovo.";

export async function mostraBrani(pagina) {
  let filtro = "";
  let cerca = "";
  let attesa = null;
  const errore = boxErrore();
  const elenco = h("div", { "aria-live": "polite" });
  const filtri = h("div", { class: "filtri", role: "group", "aria-label": "Filtra i brani" });
  const ricerca = h("input", { type: "search", id: "cerca-brani", placeholder: "Cerca brani", autocomplete: "off",
    oninput: (e) => { cerca = e.target.value; clearTimeout(attesa); attesa = setTimeout(carica, 300); } });
  const estensioni = (sessione.formati.audio || []).join(",");

  // Caricamento: categoria + file.
  const idCat = nuovoId("categoria"), idFile = nuovoId("file-brano");
  const categoria = h("select", { id: idCat }, CATEGORIE.map(([v, t]) => h("option", { value: v }, t)));
  const boxCarica = boxErrore();
  const campoFile = h("input", { type: "file", id: idFile, accept: estensioni, class: "solo-lettori",
    onchange: async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      pulisciErrore(boxCarica);
      const corpo = await caricaConSostituzione(`/api/musiche/${seg(categoria.value)}`, f, { nota: NOTA_SOSTITUZIONE });
      if (!corpo) return;
      if (!corpo.ok) { mostraErrore(boxCarica, corpo); return; }
      conferma(`Brano caricato: ${corpo.dati.nome}. Dichiara la licenza prima di usarlo.`);
      carica();
    } });

  sostituisci(pagina,
    h("header", { class: "intestazione-pagina" },
      h("div", {}, h("h1", { tabindex: "-1" }, "Musiche"),
        h("p", { class: "nota" }, "I brani da usare come intro, sottofondo o outro. Gli stessi che trovi al passo 3."))),
    puo("brano_carica") ? sezione("Carica brano",
      h("div", { class: "riga-campi" },
        h("div", {}, h("label", { for: idCat, class: "etichetta" }, "Categoria"), categoria),
        h("div", {}, h("span", { class: "etichetta" }, "File"), h("label", { class: "pulsante", for: idFile }, icona("carica"), h("span", {}, "Scegli il brano")), campoFile)),
      h("p", { class: "nota" }, estensioni.replaceAll(".", "").replaceAll(",", ", ")), boxCarica) : null,
    h("div", { class: "barra-filtri" },
      h("label", { class: "campo-ricerca", for: "cerca-brani" }, icona("cerca"), h("span", { class: "solo-lettori" }, "Cerca brani"), ricerca),
      filtri),
    errore, elenco);

  function disegnaFiltri() {
    sostituisci(filtri, FILTRI.map(([v, t]) => h("button", { type: "button", class: "filtro", "aria-pressed": String(v === filtro),
      onclick: () => { filtro = v; disegnaFiltri(); carica(); } }, t)));
  }

  async function carica() {
    const q = new URLSearchParams();
    if (filtro) q.set("filtro", filtro);
    if (cerca.trim()) q.set("cerca", cerca.trim());
    const { corpo } = await leggi(`/api/musiche?${q}`);
    if (!corpo.ok) { mostraErrore(errore, corpo, { riprova: carica }); return; }
    const d = corpo.dati;
    const totale = CATEGORIE.reduce((n, [c]) => n + (d[c] || []).length, 0);
    if (!totale) {
      sostituisci(elenco, !filtro && !cerca.trim()
        ? vuoto({ titolo: "Nessun brano", testo: "Carica un brano per l'intro, il sottofondo o l'outro." })
        : h("p", { class: "nota" }, "Nessun brano corrisponde alla ricerca."));
      return;
    }
    sostituisci(elenco, CATEGORIE.map(([c, t]) => (d[c] || []).length ? sezione(t,
      h("table", { class: "tabella" },
        h("caption", { class: "solo-lettori" }, `Brani ${t}`),
        h("thead", {}, h("tr", {}, ["Brano", "Durata", "Contenuto", "Licenza", "Usato in", "Azioni"].map((x) => h("th", { scope: "col" }, x)))),
        h("tbody", {}, d[c].flatMap((b) => righe(c, b)))),
      (d[`${c}_rejected`] || []).length ? h("p", { class: "nota" }, `File non usati: ${d[`${c}_rejected`].map((s) => s.file || s.name || s).join(", ")}.`) : null) : null));
  }

  function righe(cat, b) {
    const box = boxErrore();
    const dettaglio = h("tr", { class: "riga-dettaglio", hidden: true }, h("td", { colspan: "6" }));
    const apri = () => {
      dettaglio.hidden = !dettaglio.hidden;
      if (!dettaglio.hidden) sostituisci(dettaglio.firstChild, pannello(cat, b, box));
    };
    const idAudio = nuovoId("brano");
    return [h("tr", {},
      h("td", {}, h("span", { class: "forte" }, b.name),
        b.error ? h("p", { class: "nota nota-errore" }, b.error === "Doppione" ? "Due file con lo stesso nome: il brano non si usa." : String(b.error)) : null),
      h("td", {}, durata(b.duration_seconds)),
      h("td", {}, b.parlato ? "Contiene parlato" : "Solo strumentale"),
      h("td", {}, b.has_licenza ? h("span", { class: "badge badge-ok" }, "Licenza dichiarata") : h("span", { class: "badge badge-attenzione" }, "Licenza mancante")),
      h("td", {}, b.usato_in ? `${b.usato_in} ${b.usato_in === 1 ? "episodio" : "episodi"}` : "Mai usato"),
      h("td", {}, h("div", { class: "riga-azioni" },
        h("audio", { id: idAudio, controls: true, preload: "none", src: `/api/musiche/${seg(cat)}/${seg(b.name)}/file`, class: "lettore-piccolo", "aria-label": `Ascolta ${b.name}` }),
        h("button", { type: "button", class: "pulsante pulsante-piccolo", "aria-expanded": "false", onclick: (e) => { apri(); e.currentTarget.setAttribute("aria-expanded", String(!dettaglio.hidden)); } }, "Dettagli")))),
    dettaglio];
  }

  function pannello(cat, b, box) {
    const puoModificare = puo("brano_metadati");
    const idLic = nuovoId("licenza"), idParl = nuovoId("parlato"), idTrasc = nuovoId("trascrizione");
    const licenza = h("textarea", { id: idLic, rows: "2", maxlength: "2000", disabled: !puoModificare }, b.licenza || "");
    const parlato = h("input", { type: "checkbox", id: idParl, checked: !!b.parlato, disabled: !puoModificare || cat === "sottofondo" });
    const trascrizione = h("textarea", { id: idTrasc, rows: "2", maxlength: "500", disabled: !puoModificare || !b.parlato }, b.trascrizione || "");
    parlato.addEventListener("change", () => { trascrizione.disabled = !parlato.checked; });
    const salva = async (dati, messaggio) => {
      pulisciErrore(box);
      const { corpo: r } = await modifica(`/api/musiche/${seg(cat)}/${seg(b.name)}`, dati);
      if (r.ok) { conferma(messaggio); carica(); } else mostraErrore(box, r);
    };
    return h("div", { class: "dettaglio-brano" },
      h("div", {},
        h("h3", {}, "Parlato"),
        cat === "sottofondo" ? h("p", { class: "nota" }, "Un sottofondo non può contenere parlato.") : h("div", { class: "consenso" }, parlato, h("label", { for: idParl }, "Il brano contiene parlato")),
        h("label", { for: idTrasc, class: "etichetta" }, "Trascrizione del parlato"), trascrizione,
        h("p", { class: "nota" }, "Utile per evitare che si sovrapponga alle voci dell'episodio. Entra nelle istruzioni per scrivere i copioni."),
        puoModificare && cat !== "sottofondo" ? h("button", { type: "button", class: "pulsante", onclick: () =>
          salva(parlato.checked ? { parlato: true, trascrizione: trascrizione.value.trim() || undefined } : { parlato: false }, "Parlato salvato") }, "Salva il parlato") : null),
      h("div", {},
        h("h3", {}, "Licenza"),
        b.has_licenza ? h("p", { class: "nota" }, `Dichiarata da ${b.licenza_da || "—"} il ${dataOra(b.licenza_il)}`) : null,
        h("label", { for: idLic, class: "etichetta" }, "Ho la licenza per questo brano: quale?"), licenza,
        h("p", { class: "nota" }, "La licenza non viene verificata: si registra chi l'ha dichiarata e quando."),
        puoModificare ? h("div", { class: "riga-azioni" },
          h("button", { type: "button", class: "pulsante", onclick: () => {
            if (!licenza.value.trim()) { licenza.focus(); return; }
            salva({ licenza: licenza.value.trim() }, "Licenza dichiarata");
          } }, "Dichiara la licenza"),
          b.has_licenza ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => salva({ senza_licenza: true }, "Licenza tolta") }, "Togli la licenza") : null) : null,
        !puoModificare ? h("p", { class: "nota" }, "Serve il permesso per i metadati dei brani.") : null),
      box);
  }

  disegnaFiltri();
  await carica();
}
