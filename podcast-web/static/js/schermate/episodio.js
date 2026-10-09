// Passo 7 · Episodio (07-Episodio.dc.html): intestazione, lettore con le sezioni,
// trascrizione cliccabile, file da scaricare. Le correzioni (retake, "Cosa dice",
// cambio musiche) e la forma d'onda arrivano in A5.

import { h, sostituisci, durata, giorno } from "../dom.js";
import { leggi, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, sezione, badgeStato, piede } from "../componenti.js";
import { mostraErrore } from "../stati.js";

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

  const trascrizione = h("ol", { class: "trascrizione" }, (ep.trascrizione || []).map((t) => {
    const testo = [h("span", { class: "nota" }, `${t.inizio_label || "—"} · ${RUOLI[t.ruolo] || t.speaker} · `), t.testo_parlato || t.testo];
    const voce = audio && typeof t.inizio_finale === "number"
      ? h("button", { type: "button", class: "battuta-ascolta", "aria-label": `Ascolta dalla battuta ${t.n}, ${t.speaker}`,
        onclick: () => { audio.currentTime = t.inizio_finale; audio.play().catch(() => {}); } }, testo)
      : h("p", {}, testo);
    const li = h("li", { class: "riga-trascrizione", dataset: { inizio: t.inizio_finale ?? "", fine: t.fine_finale ?? "" } }, voce);
    righe.push(li);
    return li;
  }));

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
    sezione("Trascrizione", trascrizione),
    altri.length ? sezione("File dell'episodio", h("ul", { class: "file" }, altri.map((f) => h("li", {},
      h("a", { href: url(f.nome, true), download: f.nome }, f.nome), h("span", { class: "nota" }, ` · ${Math.max(1, Math.round((f.dimensione_bytes || 0) / 1024))} KB`))))) : null,
    sezione("Correzioni", h("p", { class: "nota" }, "«Come è detta» (nuovi take), «Cosa dice» (correzione del testo) e «Cambia musiche» arrivano in una fase successiva. Per correggere il testo oggi: modifica la battuta al passo 4, riapprova e rigenera (si pagano solo le battute cambiate).")),
    piede(h("a", { class: "pulsante", href: "#/archivio" }, "Torna all'archivio")));
}
