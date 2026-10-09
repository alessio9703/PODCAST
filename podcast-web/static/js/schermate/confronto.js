// Pannello Confronto versioni (Confronto.dc.html): una versione del copione contro
// un'altra (di base la corrente), tutto il copione o solo le modifiche, e "Ripristina",
// che crea una NUOVA versione da approvare di nuovo. Non costa niente.

import { h, sostituisci, dataOra, nuovoId } from "../dom.js";
import { leggi, invia, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, sezione, piede, pulsanteIndietro } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda } from "../stati.js";
import { puo } from "../sessione.js";

const RUOLI = { presentatore: "Presentatore", intervistatore: "Intervistatore", ospite: "Ospite", voce: "Voce" };
const STATI = { modificata: ["Modificata", "badge-attenzione"], aggiunta: ["Aggiunta", "badge-ok"], rimossa: ["Rimossa", "badge-errore"] };
const TIPI = { prima_stesura: "prima stesura", riscrittura: "riscrittura", revisione: "revisione", modifica_manuale: "modifica manuale", ripristino: "ripristino" };

export async function mostraConfronto(pagina, { run, da }) {
  const errore = boxErrore();
  const corpo = h("div", {});
  let copione = null;
  let partenza = Number(da) || null;
  let arrivo = null;          // null = versione corrente
  let soloModifiche = true;

  const [c, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/copione`), statoRun(run)]);
  const testa = intestazionePasso({ run, passo: 4, titolo: c.corpo.dati?.titolo || s?.titolo, stato: s?.etichetta, raggiunto: s?.passo || 4,
    sottotitolo: "Confronto versioni del copione" });
  if (!c.corpo.ok) { sostituisci(pagina, testa, errore); mostraErrore(errore, c.corpo); return; }
  copione = c.corpo.dati;
  const versioni = copione.versions || [];
  if (!copione.has_script || versioni.length < 2) {
    sostituisci(pagina, testa, sezione("Confronto versioni",
      h("p", {}, "Il copione ha una sola versione: non c'è niente da confrontare."),
      h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/4` }, "Torna al copione")));
    return;
  }
  if (!partenza || !versioni.some((v) => v.version === partenza) || partenza === copione.version) {
    partenza = versioni.filter((v) => v.version !== copione.version).at(-1).version;
  }
  const ruoloDi = (speaker) => {
    const x = copione.cast?.[speaker];
    return x ? (x.presentatore ? "Presentatore" : RUOLI[x.ruolo] || "Partecipante") : null;
  };

  function selettore(etichetta, valore, conCorrente, cambia) {
    const id = nuovoId("versione");
    return h("div", {},
      h("label", { for: id, class: "etichetta" }, etichetta),
      h("select", { id, onchange: (e) => cambia(e.target.value === "" ? null : Number(e.target.value)) },
        versioni.slice().reverse().map((v) => {
          const corrente = v.version === copione.version;
          const testo = `v${v.version}${corrente ? " · corrente" : ""} · ${TIPI[v.kind] || v.kind || ""} · ${dataOra(v.at)}`;
          const val = corrente && conCorrente ? "" : String(v.version);
          return h("option", { value: val, selected: corrente && conCorrente ? valore === null : valore === v.version }, testo);
        })));
  }

  async function carica() {
    pulisciErrore(errore);
    sostituisci(corpo, h("p", { class: "nota", role: "status" }, "Confronto in corso…"));
    const q = new URLSearchParams({ da: String(partenza) });
    if (arrivo !== null) q.set("a", String(arrivo));
    if (soloModifiche) q.set("solo_modifiche", "1");
    const { corpo: r } = await leggi(`/api/episodi/${seg(run)}/copione/confronto?${q}`);
    if (!r.ok) { sostituisci(corpo); mostraErrore(errore, r, { riprova: carica }); return; }
    disegna(r.dati);
  }

  function segmenti(riga) {
    if (!(riga.segmenti || []).length) return riga.testo_dopo ?? riga.testo_prima ?? "";
    return riga.segmenti.map((x) => x.tipo === "aggiunto" ? h("ins", { class: "diff-aggiunto" }, x.testo)
      : x.tipo === "rimosso" ? h("del", { class: "diff-rimosso" }, x.testo) : x.testo);
  }

  function riga(r) {
    if (r.stato === "invariate") {
      return h("li", { class: "diff-invariate" },
        h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => { soloModifiche = false; disegnaGruppo(); carica(); } },
          `${r.conteggio} ${r.conteggio === 1 ? "battuta invariata" : "battute invariate"} · mostra`));
    }
    const [etichetta, classe] = STATI[r.stato] || [null, null];
    const n = r.n_dopo ?? null;
    const testo = r.stato === "aggiunta" ? h("ins", { class: "diff-aggiunto" }, r.testo_dopo)
      : r.stato === "rimossa" ? h("del", { class: "diff-rimosso" }, r.testo_prima)
      : r.stato === "modificata" ? segmenti(r) : r.testo_dopo;
    return h("li", { class: ["battuta", `diff-${r.stato}`] },
      h("div", { class: "battuta-testa" },
        h("span", { class: "battuta-n" }, n === null ? "—" : String(n).padStart(2, "0")),
        h("span", { class: "forte" }, r.speaker || r.speaker_prima || ""),
        ruoloDi(r.speaker || r.speaker_prima) ? h("span", { class: "badge badge-neutro" }, ruoloDi(r.speaker || r.speaker_prima)) : null,
        etichetta ? h("span", { class: `badge ${classe}` }, etichetta) : null),
      r.speaker_prima && r.speaker && r.speaker_prima !== r.speaker ? h("p", { class: "nota" }, `Prima: ${r.speaker_prima}`) : null,
      h("p", { class: "battuta-testo" }, testo));
  }

  function disegna(d) {
    const k = d.conteggi || {};
    const cifre = [
      k.modificata ? `${k.modificata} ${k.modificata === 1 ? "modificata" : "modificate"}` : null,
      k.aggiunta ? `${k.aggiunta} ${k.aggiunta === 1 ? "aggiunta" : "aggiunte"}` : null,
      k.rimossa ? `${k.rimossa} ${k.rimossa === 1 ? "rimossa" : "rimosse"}` : null,
    ].filter(Boolean);
    const box = boxErrore();
    const prossima = Math.max(...versioni.map((v) => v.version)) + 1;
    sostituisci(corpo,
      d.to?.instruction ? h("p", {}, `Richiesta che ha creato la v${d.to.version}: `, h("span", { class: "forte" }, `«${d.to.instruction}»`)) : null,
      h("p", { class: "nota", role: "status" }, cifre.length ? cifre.join(" · ") : "Nessuna differenza fra le battute."),
      d.titolo_cambiato ? h("p", {}, `Titolo: «${d.titolo_prima}» → «${d.titolo_dopo}»`) : null,
      h("ol", { class: "battute diff" }, (d.righe || []).map(riga)),
      puo("copione_modifica") && partenza !== copione.version ? sezione(null,
        h("button", { type: "button", class: "pulsante pulsante-primario", onclick: async () => {
          pulisciErrore(box);
          const ok = await domanda({ titolo: `Ripristinare la v${partenza}?`,
            testo: `Viene creata la versione ${prossima} con il contenuto della v${partenza}. La storia resta intatta e il copione va approvato di nuovo.`,
            opzioni: [{ testo: "Annulla", valore: false }, { testo: `Ripristina v${partenza}`, valore: true, primaria: true }] });
          if (!ok) return;
          const { corpo: r } = await invia(`/api/episodi/${seg(run)}/copione/ripristino`, { versione: partenza, versione_attesa: copione.version });
          if (r.ok) { location.hash = `#/episodi/${seg(run)}/4`; conferma(`Ripristinata la v${partenza} · versione ${r.dati.version}, da approvare`); }
          else mostraErrore(box, r);
        } }, `Ripristina v${partenza}`),
        h("p", { class: "nota" }, `Crea la versione ${prossima}, da approvare di nuovo.`), box) : null);
  }

  const gruppo = h("div", { class: "scelte", role: "group", "aria-label": "Cosa mostrare" });
  function disegnaGruppo() {
    sostituisci(gruppo,
      h("button", { type: "button", class: "scelta", "aria-pressed": String(soloModifiche), onclick: () => { soloModifiche = true; disegnaGruppo(); carica(); } }, "Solo modifiche"),
      h("button", { type: "button", class: "scelta", "aria-pressed": String(!soloModifiche), onclick: () => { soloModifiche = false; disegnaGruppo(); carica(); } }, "Tutto il copione"));
  }
  disegnaGruppo();
  sostituisci(pagina, testa, errore,
    sezione("Confronto",
      h("div", { class: "riga-campi" },
        selettore("Versione di partenza", partenza, false, (v) => { partenza = v; carica(); }),
        selettore("Confrontata con", arrivo, true, (v) => { arrivo = v; carica(); })),
      gruppo),
    corpo,
    piede(pulsanteIndietro(`#/episodi/${seg(run)}/4`, "Torna al copione")));
  await carica();
}
