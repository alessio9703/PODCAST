// Passo 7 · Episodio (07-Episodio.dc.html): intestazione, lettore con le sezioni,
// trascrizione cliccabile, file da scaricare, correzioni di una battuta: "Cosa dice"
// (correggi il testo: nuova versione da approvare, poi si rigenera solo quella battuta).

import { h, sostituisci, durata, giorno, nuovoId } from "../dom.js";
import { leggi, modifica, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, sezione, badgeStato, piede } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma } from "../stati.js";
import { puo } from "../sessione.js";

const FORMATI = { conversazione: "Conversazione", intervista: "Intervista" };
const SEZIONI = { intro: "Intro", parlato: "Parlato", outro: "Outro" };
const MOTIVI = { copione: "il copione è cambiato", take: "è stato scelto un take diverso", musiche: "le musiche sono cambiate", non_approvato: "il copione attuale non è approvato" };
const RUOLI = { presentatore: "Presentatore", intervistatore: "Intervistatore", ospite: "Ospite", voce: "Voce" };

export async function mostraEpisodio(pagina, { run }) {
  const [e, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/episodio`), statoRun(run)]);
  const errore = boxErrore();
  if (!e.corpo.ok) {
    sostituisci(pagina, intestazionePasso({ run, passo: 7, titolo: s?.titolo, stato: s?.etichetta, raggiunto: s?.passo || 7 }), errore);
    mostraErrore(errore, e.corpo);
    return;
  }
  const ep = e.corpo.dati;
  const url = (nome, scarica) => `/api/episodi/${seg(run)}/file/${seg(nome)}${scarica ? "?scarica=1" : ""}`;
  const finale = ep.file_finale;
  const audio = finale ? h("audio", { controls: true, preload: "metadata", src: url(finale.nome), class: "lettore" }) : null;
  const righe = [];
  const pannello = h("div", { id: "correzioni", "aria-live": "polite" });

  const trascrizione = h("ol", { class: "trascrizione" }, (ep.trascrizione || []).map((t) => {
    const testo = [h("span", { class: "nota" }, `${t.inizio_label || "—"} · ${RUOLI[t.ruolo] || t.speaker} · `), t.testo_parlato || t.testo];
    const voce = h("button", { type: "button", class: "battuta-ascolta", "aria-label": `Battuta ${t.n}, ${t.speaker}: ascolta e correggi`,
      onclick: () => {
        seleziona(t);
        if (audio && typeof t.inizio_finale === "number") { audio.currentTime = t.inizio_finale; audio.play().catch(() => {}); }
      } }, testo);
    const li = h("li", { class: "riga-trascrizione", dataset: { n: String(t.n), inizio: t.inizio_finale ?? "", fine: t.fine_finale ?? "" } }, voce);
    righe.push(li);
    return li;
  }));

  // --- Correzioni della battuta scelta ---------------------------------------
  function seleziona(t) {
    for (const li of righe) li.classList.toggle("scelta-correzione", li.dataset.n === String(t.n));
    const tempi = typeof t.inizio_finale === "number" ? ` · ${orario(t.inizio_finale)}–${orario(t.fine_finale)}` : "";
    const corpo = h("div", {});
    const scelte = h("div", { class: "scelte", role: "group", "aria-label": "Cosa non va in questa battuta?" });
    const scegli = (quale) => {
      sostituisci(scelte,
        h("button", { type: "button", class: "scelta scelta-estesa", "aria-pressed": String(quale === "come"), onclick: () => scegli("come") },
          h("span", { class: "forte" }, "Come è detta"), h("span", { class: "nota" }, "Stesso testo, nuova resa")),
        h("button", { type: "button", class: "scelta scelta-estesa", "aria-pressed": String(quale === "cosa"), onclick: () => scegli("cosa") },
          h("span", { class: "forte" }, "Cosa dice"), h("span", { class: "nota" }, "Correggi il testo · nuova approvazione")));
      if (quale === "cosa") cosaDice(t, corpo);
      else if (quale === "come") comeEDetta(t, corpo);
      else sostituisci(corpo);
    };
    scegli(null);
    sostituisci(pannello, sezione(`Battuta ${t.n} · ${RUOLI[t.ruolo] || t.speaker}${tempi}`,
      h("p", { class: "forte" }, "Cosa non va in questa battuta?"), scelte, corpo));
  }

  async function cosaDice(t, corpo) {
    if (!puo("copione_modifica")) { sostituisci(corpo, h("p", { class: "nota" }, "Serve il permesso di modificare il copione.")); return; }
    sostituisci(corpo, h("p", { class: "nota", role: "status" }, "Carico il copione…"));
    const box = boxErrore();
    const { corpo: c } = await leggi(`/api/episodi/${seg(run)}/copione`);
    if (!c.ok) { sostituisci(corpo, box); mostraErrore(box, c); return; }
    const copione = c.dati;
    const attuale = (copione.turni_strutturati || []).find((x) => x.n === t.n);
    if (!attuale) { sostituisci(corpo, h("p", { class: "nota" }, "Questa battuta non c'è più nel copione attuale.")); return; }
    const id = nuovoId("cosa-dice");
    const area = h("textarea", { id, rows: "4", maxlength: "5000" }, attuale.testo);
    sostituisci(corpo,
      h("label", { for: id, class: "etichetta" }, "Testo della battuta"),
      area,
      h("p", { class: "nota" }, "Le indicazioni fra [quadre] cambiano la resa, ad esempio [pausa] o [ride]."),
      attuale.testo !== t.testo ? h("p", { class: "nota" }, "Il copione è cambiato dopo il montaggio: qui vedi il testo attuale.") : null,
      h("h3", {}, "Cosa succede dopo"),
      h("p", {}, `Il copione passa alla versione ${copione.version + 1} e va riapprovato. Poi si rigenera solo questa battuta (la stima mostra il costo prima di confermare) e si rimonta l'episodio. Le altre restano identiche.`),
      h("div", { class: "riga-azioni" },
        h("button", { type: "button", class: "pulsante pulsante-primario", onclick: async () => {
          pulisciErrore(box);
          const { corpo: r } = await modifica(`/api/episodi/${seg(run)}/copione/battute/${t.n}`, { testo: area.value, versione_attesa: copione.version });
          if (r.ok) {
            location.hash = `#/episodi/${seg(run)}/4`;
            conferma(`Copione salvato · versione ${r.dati.version}. Approvalo per rigenerare la battuta ${t.n}.`);
          } else mostraErrore(box, r);
        } }, "Salva e vai all'approvazione")),
      box);
  }

  // "Come è detta" (retake): A5, blocco successivo.
  function comeEDetta(t, corpo) {
    sostituisci(corpo, h("p", { class: "nota" }, "I nuovi take arrivano a breve."));
  }

  if (audio) {
    // Evidenzia la battuta in ascolto.
    audio.addEventListener("timeupdate", () => {
      const ora = audio.currentTime;
      for (const li of righe) {
        const inizio = Number(li.dataset.inizio), fine = Number(li.dataset.fine);
        li.classList.toggle("in-ascolto", li.dataset.inizio !== "" && ora >= inizio && ora < fine);
      }
    });
  }

  const totale = ep.durata_seconds || 0;
  const barraSezioni = totale && (ep.sezioni || []).length ? h("ol", { class: "barra-sezioni", "aria-label": "Sezioni dell'episodio" },
    ep.sezioni.map((x) => {
      const li = h("li", { class: `sezione-${x.tipo}` }, h("button", { type: "button", disabled: !audio,
        onclick: () => { if (audio) { audio.currentTime = x.inizio; audio.play().catch(() => {}); } } },
      `${SEZIONI[x.tipo] || x.tipo}${x.battute ? ` · ${x.battute} battute` : ""}`));
      li.style.flexGrow = String(Math.max(0.05, (x.fine - x.inizio) / totale)); // CSSOM: ammesso dalla CSP
      return li;
    })) : null;

  const altri = (ep.file || []).filter((f) => f.interno === false && f.nome !== finale?.nome);

  sostituisci(pagina,
    intestazionePasso({ run, passo: 7, titolo: ep.titolo, stato: s?.etichetta || ep.stato, raggiunto: s?.passo || 7 }),
    errore,
    h("div", { class: "testa-episodio" },
      s ? badgeStato(s) : null,
      h("p", { class: "nota" }, [FORMATI[ep.formato] || "—", ep.durata_label || durata(ep.durata_seconds),
        `${ep.numero_voci ?? "—"} voci`, ep.montato_il ? `montato il ${giorno(ep.montato_il)}` : null].filter(Boolean).join(" · "))),
    ep.stato === "da_aggiornare" ? h("div", { class: "messaggio messaggio-attenzione" },
      h("p", { class: "messaggio-titolo" }, "L'episodio va aggiornato"),
      h("p", {}, `Dopo il montaggio ${(ep.motivi || []).map((m) => MOTIVI[m] || m).join(", ")}.`),
      (ep.motivi || []).includes("non_approvato") || (ep.motivi || []).includes("copione")
        ? h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/4` }, "Vai al copione") : null) : null,
    ep.stato === "non_prodotto" ? h("div", { class: "messaggio messaggio-attenzione" },
      h("p", { class: "messaggio-titolo" }, "L'episodio non è ancora stato prodotto"),
      h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/5` }, "Vai alla stima")) : null,
    finale ? sezione("Ascolta", barraSezioni, audio,
      h("div", { class: "riga-azioni" }, h("a", { class: "pulsante pulsante-primario", href: url(finale.nome, true), download: finale.nome }, `Scarica ${(finale.formato || "mp3").toUpperCase()}`))) : null,
    ep.tempi_non_disponibili ? h("p", { class: "nota" }, ep.tempi_non_disponibili.messaggio) : null,
    h("div", { class: "colonne" },
      h("div", { class: "colonna-principale" }, sezione("Trascrizione", h("p", { class: "nota" }, "Scegli una battuta per ascoltarla e correggerla."), trascrizione)),
      h("aside", { class: "colonna-laterale" }, pannello)),
    altri.length ? sezione("File dell'episodio", h("ul", { class: "file" }, altri.map((f) => h("li", {},
      h("a", { href: url(f.nome, true), download: f.nome }, f.nome), h("span", { class: "nota" }, ` · ${Math.max(1, Math.round((f.dimensione_bytes || 0) / 1024))} KB`))))) : null,
    piede(h("a", { class: "pulsante", href: "#/archivio" }, "Torna all'archivio")));
}

function orario(secondi) {
  const s = Math.max(0, Math.round(Number(secondi) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
