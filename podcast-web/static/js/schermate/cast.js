// Passo 2 · Cast e formato (02-Cast.dc.html). Ogni modifica si salva subito con
// episode_config (tutto o niente). Le voci si scelgono dalla libreria, mai indovinate;
// il nome proposto dal file e' solo un suggerimento, non si salva da solo.

import { h, sostituisci, durata as fmtDurata } from "../dom.js";
import { leggi, modifica, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione, pulsanteIndietro, pulsanteAvanti } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, confermaGettone, domanda, titolo as titoloEsito } from "../stati.js";
import { puo } from "../sessione.js";

const FORMATI = [
  ["conversazione", "Conversazione", "Due voci alla pari discutono il documento."],
  ["intervista", "Intervista", "Una voce fa le domande, l'altra risponde."],
];
const AIUTO_RUOLI = {
  presentatore: "Apre e chiude l'episodio",
  intervistatore: "Fa le domande",
  ospite: "Risponde alle domande",
  voce_a: "Prima voce della conversazione",
  voce_b: "Seconda voce della conversazione",
};

export async function mostraCast(pagina, { run }) {
  const [c, v, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/configurazione`), leggi("/api/voci"), statoRun(run)]);
  if (!c.corpo.ok) { const box = boxErrore(); sostituisci(pagina, box); mostraErrore(box, c.corpo); return; }
  let conf = c.corpo.dati;
  const voci = v.corpo.ok ? v.corpo.dati.voices || [] : [];
  const modificabile = puo("episodio_modifica");
  const erroreGenerale = boxErrore();
  const corpo = h("div", {});

  sostituisci(pagina,
    intestazionePasso({ run, passo: 2, titolo: conf.titolo, stato: s?.etichetta || "Bozza", raggiunto: s?.passo || 2 }),
    h("h2", { class: "domanda-passo" }, "Chi parla e in che forma?"),
    h("p", { class: "nota" }, "Scegli il formato, poi assegna una voce a ogni ruolo. Le voci sono persone reali: scegline una dalla libreria, non la indoviniamo mai."),
    erroreGenerale, corpo);

  // Salva una modifica; gestisce gli esiti 3 (nome senza cognome, nome ambiguo).
  async function salva(modifiche, boxCampo) {
    pulisciErrore(erroreGenerale);
    if (boxCampo) pulisciErrore(boxCampo);
    const { corpo: r } = await modifica(`/api/episodi/${seg(run)}/configurazione`, modifiche);
    if (r.ok) return aggiorna(r.dati);
    if (r.esito === "decisione" && r.error_type === "SpokenNameIncomplete") {
      const nome = r.details?.nome || "";
      const ok = await confermaGettone(r, { testo: `«${nome}» non ha il cognome. Va pronunciato proprio così?`, testoConferma: "Sì, va bene così" });
      if (ok?.corpo?.ok) return aggiorna(ok.corpo.dati);
      if (ok && !ok.corpo.ok) mostraErrore(boxCampo || erroreGenerale, ok.corpo);
      return;
    }
    if (r.esito === "decisione" && r.error_type === "AmbiguousName") {
      const scelta = await domanda({ titolo: titoloEsito(r), testo: r.messaggio,
        opzioni: [...(r.details?.candidates || []).map((n) => ({ testo: n, valore: n })), { testo: "Annulla", valore: null }] });
      if (scelta && r.details?.ruolo) return salva({ voci: { [r.details.ruolo]: scelta } }, boxCampo);
      return;
    }
    mostraErrore(boxCampo || erroreGenerale, r);
  }

  function aggiorna(dati) {
    conf = dati;
    if (dati.approval_revoked) conferma(`Approvazione del copione ritirata (versione ${dati.approval_revoked.version}): il cast è cambiato.`);
    else conferma("Salvato");
    disegna();
  }

  function disegna() {
    const conFocus = document.activeElement?.id;
    disegnaSezioni();
    if (conFocus) document.getElementById(conFocus)?.focus();
  }

  function disegnaSezioni() {
    const mancanti = new Set((conf.mancanti || []).map((m) => `${m.campo}:${m.ruolo || ""}`));
    const erroreFormato = boxErrore();
    const erroreDurata = boxErrore();
    const minutiTot = conf.durata_minuti ?? null;
    const ore = h("input", { type: "number", id: "durata-ore", min: "0", max: "3", inputmode: "numeric", value: minutiTot !== null ? Math.floor(minutiTot / 60) : "", disabled: !modificabile });
    const minuti = h("input", { type: "number", id: "durata-minuti", min: "0", max: "59", inputmode: "numeric", value: minutiTot !== null ? minutiTot % 60 : "", disabled: !modificabile });
    const salvaDurata = () => {
      const o = Number(ore.value || 0), m = Number(minuti.value || 0);
      if (!Number.isInteger(o) || !Number.isInteger(m) || o * 60 + m < 1) return;
      salva({ durata: `${o}:${String(m).padStart(2, "0")}` }, erroreDurata);
    };
    ore.addEventListener("change", salvaDurata);
    minuti.addEventListener("change", salvaDurata);

    const nomeGruppo = "formato";
    sostituisci(corpo,
      sezione("Formato",
        h("div", { class: "scelte-formato", role: "radiogroup", "aria-label": "Formato" }, FORMATI.map(([valore, nome, descrizione]) => h("label", { class: "scelta-formato" },
          h("input", { type: "radio", id: `formato-${valore}`, name: nomeGruppo, value: valore, checked: conf.formato === valore, disabled: !modificabile,
            onchange: () => salva({ formato: valore }, erroreFormato) }),
          h("span", { class: "forte" }, nome), h("span", { class: "nota" }, descrizione)))),
        mancanti.has("formato:") ? h("p", { class: "nota nota-errore" }, "Scegli il formato.") : null,
        erroreFormato),
      sezione("Durata indicativa",
        h("div", { class: "riga-campi" },
          h("label", { for: "durata-ore" }, "Ore"), ore, h("span", { "aria-hidden": "true" }, ":"), h("label", { for: "durata-minuti" }, "Minuti"), minuti),
        h("p", { class: "nota" }, "Il copione viene scritto per avvicinarsi a questa durata."),
        mancanti.has("durata:") ? h("p", { class: "nota nota-errore" }, "Indica la durata.") : null,
        erroreDurata),
      sezione("Ruoli", (conf.ruoli || []).length
        ? h("ul", { class: "ruoli" }, conf.ruoli.map((r) => ruolo(r, mancanti)))
        : h("p", { class: "nota" }, "Scegli prima il formato.")),
      piede(pulsanteIndietro(`#/episodi/${seg(run)}/1`),
        pulsanteAvanti("Avanti: musiche", `#/episodi/${seg(run)}/3`, !!conf.cast_completo)),
    );
  }

  function ruolo(r, mancanti) {
    const box = boxErrore();
    // Id stabili: dopo ogni salvataggio la sezione si ridisegna e il focus torna qui.
    const idVoce = `voce-${r.ruolo}`, idNome = `nome-${r.ruolo}`, idDescr = `descr-${r.ruolo}`;
    const voce = voci.find((x) => x.name === r.voce);
    const selezione = h("select", { id: idVoce, disabled: !modificabile,
      onchange: (e) => e.target.value ? salva({ voci: { [r.ruolo]: e.target.value } }, box) : salva({ togli: [r.ruolo] }, box) },
      h("option", { value: "" }, "Nessuna voce assegnata"),
      voci.filter((x) => !x.errore).map((x) => h("option", { value: x.name, selected: x.name === r.voce },
        `${x.name} · ${x.stato === "Già clonata" ? "Già clonata" : "Da clonare · consenso richiesto"}`)));
    const nome = h("input", { type: "text", id: idNome, maxlength: "100", value: r.nome_pronunciato || "", placeholder: r.nome_proposto || "Nome Cognome",
      disabled: !modificabile || !r.voce, onchange: (e) => e.target.value.trim() && salva({ nomi: { [r.ruolo]: e.target.value.trim() } }, box) });
    const descrizione = r.ruolo === "presentatore" ? null : h("input", { type: "text", id: idDescr, maxlength: "300", value: r.descrizione || "",
      placeholder: "Chi è, di cosa si occupa", disabled: !modificabile || !r.voce,
      onchange: (e) => e.target.value.trim() && salva({ descrizioni: { [r.ruolo]: e.target.value.trim() } }, box) });
    const manca = (campo) => mancanti.has(`${campo}:${r.ruolo}`);
    return h("li", { class: ["ruolo", [...mancanti].some((m) => m.endsWith(`:${r.ruolo}`)) ? "ruolo-incompleto" : ""] },
      h("div", { class: "ruolo-testa" },
        h("h3", {}, r.etichetta || r.ruolo), h("p", { class: "nota" }, AIUTO_RUOLI[r.ruolo] || ""),
        r.voce ? h("span", { class: ["badge", r.clonata ? "badge-ok" : "badge-attenzione"] }, r.clonata ? "Già clonata" : "Da clonare · consenso richiesto") : null),
      h("label", { for: idVoce, class: "etichetta" }, "Voce"), selezione,
      manca("voce") ? h("p", { class: "nota nota-errore" }, "Scegli una voce dalla libreria.") : null,
      h("label", { for: idNome, class: "etichetta" }, "Nome e cognome da pronunciare"), nome,
      r.voce && !r.nome_pronunciato && r.nome_proposto
        ? h("button", { type: "button", class: "pulsante pulsante-link", disabled: !modificabile, onclick: () => salva({ nomi: { [r.ruolo]: r.nome_proposto } }, box) },
          `Usa «${r.nome_proposto}»`) : null,
      r.voce && r.nome_proposto_completo === false && !r.nome_pronunciato ? h("p", { class: "nota" }, "Il nome proposto è senza cognome: aggiungilo, non lo indoviniamo.") : null,
      manca("nome_pronunciato") ? h("p", { class: "nota nota-errore" }, "Manca il nome da pronunciare.") : null,
      descrizione ? [h("label", { for: idDescr, class: "etichetta" }, "Ruolo"), descrizione] : null,
      manca("descrizione") ? h("p", { class: "nota nota-errore" }, "Descrivi il ruolo.") : null,
      voce && voce.durata_campione_seconds && (voce.durata_campione_seconds < 10 || voce.durata_campione_seconds > 30)
        ? h("p", { class: "nota" }, `Il campione dura ${fmtDurata(voce.durata_campione_seconds)}: la durata consigliata è fra 10 e 30 secondi.`) : null,
      box);
  }

  disegna();
}
