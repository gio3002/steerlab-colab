# AGENT.md — istruzioni per un agente WorkBuddy che apre questo repo

> **Questo file è per un LLM, non per un umano.** Se hai appena aperto una nuova
> chat con `steerlab-colab` come workspace, esegui la procedura qui sotto.
> L'obiettivo: l'umano ti ha dato **solo il link del repo**. Tutto il resto —
> avviare la GPU, aprire l'app, modificare il codice, eseguire esperimenti,
> leggere i risultati — **lo fai tu**. Non chiedere click all'umano.

---

## 0. Contesto in 10 righe

- Repo: `https://github.com/gio3002/steerlab-colab` (privato, accesso ai soli
  collaboratori con ruolo Write).
- È un'app di **activation steering**: 9 assi in 3 famiglie (stile, emozioni,
  modalità), barre A↔B, equalizzatore per layer, chat con streaming e ispezione
  dei token. Gira su **GPU Colab T4**, non sul PC dell'umano.
- Il codice gira in una VM Colab raggiungibile tramite un tunnel HTTPS.
- Dentro l'app esiste un **canale di amministrazione** (`/api/admin/*`) protetto
  da `deploy_key`: permette di scrivere file, ricaricare i moduli a caldo ed
  eseguire Python nella VM. È il meccanismo che elimina ogni interazione umana.
- `control.json` contiene `app_url` (link corrente) e `deploy_key`.
- `START.ipynb` è un notebook **a una cella** che avvia tutto da zero.
- `remote_ctl.py` è il client che usi tu dal workspace.
- Documenti da leggere prima di toccare il codice: `builder.txt` (cronologia
  completa e ragioni delle scelte), `EQUALIZER_RESEARCH.md` (letteratura e
  derivazione dell'equalizzatore), `COLLAB.md` (permessi), `README.md`.

## 1. Bootstrap: porta l'app online

```
1. Leggi control.json.
2. Prova: python -c "from remote_ctl import Remote; print(Remote.from_control().ping())"
   - Se risponde {"ok": true, ...}: SEI GIÀ ONLINE -> vai al §2.
   - Se risponde errore/timeout (es. Cloudflare 1033): la sessione Colab è
     morta. È normale: Colab free si scollega. Devi riavviarla.
```

### Riavvio automatico con demone (procedura preferita)

Il repo contiene **`boot_daemon.cjs`**. Avvialo in background:

```bash
node boot_daemon.cjs                    # se il repo e' la radice del workspace
STEERLAB_ROOT=/percorso NOTEBOOK_PATH=/percorso/START.ipynb \
  CONTROL_PATH=/percorso/control.json node boot_daemon.cjs
```

Fa tutto lui: apre una finestra Chrome con un **profilo persistente dentro il
workspace**, attende il login Google (una sola volta nella vita del profilo),
seleziona T4, carica `START.ipynb`, esegue la cella, estrae `LINK` e `CHIAVE`,
aggiorna `control.json` e **lascia la finestra aperta** per tenere viva la
sessione. Stato leggibile in `_work/colab_boot_status.json`.

Se il profilo non è ancora loggato, l'unica azione umana è il login in quella
finestra. Dopo, ogni riavvio è completamente automatico — anche per te.

### Riavvio automatico (procedura preferita)

Usa le tue capacità di automazione browser (skill `agent-browser` oppure
`playwright-cli`; su Windows c'è Chrome). L'umano è già loggato a Google nel
profilo del browser.

1. Apri `https://colab.research.google.com/`.
2. **File → Upload notebook** e carica `START.ipynb` **dalla cartella del
   workspace** (hai il file in locale; con Playwright usa `set_input_files` sul
   file chooser).
3. **Runtime → Change runtime type → T4 GPU → Save**.
4. Esegui l'unica cella (`Ctrl+F9`, oppure clic sul pulsante Run).
5. Attendi che compaia `PRONTO` nell'output (5-15 minuti se deve ricalibrare;
   pochi secondi se la calibrazione è già buona). Non usare un timeout breve.
6. Dall'output estrai le due righe `LINK :` e `CHIAVE:`.
7. Scrivile in `control.json`:
   ```json
   {"repo": "https://github.com/gio3002/steerlab-colab",
    "app_url": "<LINK>", "deploy_key": "<CHIAVE>"}
   ```
8. Verifica con `Remote.from_control().ping()`.

**Se il repo è privato**, la cella ha bisogno di `GH_TOKEN` nei Secrets di Colab
(icona chiave → `GH_TOKEN`, permesso `Contents: Read`, Notebook access attivo).
Se manca, la cella lo chiede con `getpass`: in quel caso puoi incollarlo
programmaticamente nel prompt del browser, oppure — solo se non hai automazione
browser — chiedere all'umano **una sola volta** di incollarti LINK e CHIAVE.

### Fallback senza automazione browser

È l'unico caso in cui è ammesso chiedere qualcosa all'umano. Digli: *«apri
`START.ipynb` in Colab con T4, premi Ctrl+F9, incollami le due righe LINK e
CHIAVE»*. Poi aggiorna `control.json` e proseguire in autonomia.

## 2. Ciclo di lavoro normale (zero interazioni)

```python
from remote_ctl import Remote
R = Remote.from_control()

R.ping()                        # stato del tunnel
R.state()                       # stato completo del motore
R.sync()                        # carica TUTTO il workspace sulla VM e ricarica i moduli
R.exec("print('ciao dalla GPU')")   # Python arbitrario nella VM
R.api("/api/axes")              # una qualunque API dell'app
R.chat("Describe the lighthouse.", {"concision": 0.5})
```

Regole pratiche:

- **Modifichi un file Python** (`engine.py`, `server.py`, `axes.py`,
  `equalizer.py`, `calibrate.py`): `R.sync(reload=True)` e poi
  `R.api("/api/model/load", {})`, perché il reload a caldo scarica il modello.
- **Modifichi solo UI** (`static/*`): `R.sync(reload=False)` basta; il browser
  degli utenti deve solo ricaricare la pagina.
- **Lavori > 90 secondi** (calibrazione, download modelli, sweep): **non** farli
  in una sola `exec` — Cloudflare chiude la richiesta (HTTP 524). Usa
  ```python
  R.background("python -u calibrate.py > cal.log 2>&1", "cal.log")
  # poi, ogni 20-30 s:
  print(R.poll("cal.log")["output"])
  ```
- `/api/admin/deploy` accetta solo file di testo con estensione
  `.py .js .html .css .json .txt .md .ipynb`. Per `directions.npz` (binario)
  usa `R.send_calibration()`.
- Il tunnel è un processo separato: il **link non cambia** dopo un deploy.
  Cambia solo se la sessione Colab muore e la riavvii → aggiorna `control.json`.

## 3. Esperimenti di steering: come si fa sul serio

Non giudicare dall'argmax: con generazione deterministica il **testo può restare
identico** mentre la distribuzione cambia. Usa sempre un readout quantitativo.

- `engine.evaluate()` / `POST /api/evaluate`: margine
  `log p(target+) − log p(target−)` teacher-forced su probe tenuti fuori, con
  direzioni casuali come controllo nullo.
- `engine.minimal_pairs()`: direzione da **differenze appaiate**
  `d = mean_i(h(a_i) − h(b_i))`. Regola imparata a caro prezzo: **il token finale
  delle due frasi deve essere identico**, altrimenti l'ultimo hidden state
  codifica cosa *segue* la parola e la direzione si inverte.
- L'effetto è **non monotono in α**: più intenso non è più efficace. Fai sempre
  uno sweep e cerca l'optimum, non il massimo.
- `equalizer.py`: profilo per layer = gaussiana adattata + ablazione a budget
  costante contro 5 controlli. Se un controllo vince, **si usa il controllo**.
- Composizione A/B vincolata: `w = v + C(CᵀGC)⁻¹(t − CᵀGv)`, con `G = I`
  (euclidea) o `G = Σ⁻¹` (whitened, Woodbury). Serve a steerare A preservando B.
- Domande aperte (materiale per ricerca vera): matrice di accoppiamento `H_ij`
  fra layer, partizionamento dei layer fra più assi attivi, decadimento
  temporale dell'intensità, pullback di Fisher ricorsivo. Vedi
  `EQUALIZER_RESEARCH.md §5`.

Esempio di esperimento completo, eseguito tutto da te:

```python
code = """
import json, numpy as np
e = _g['ENGINE']
print('device', e.device, '| modello', e.cfg.get('model_id'))
for alpha in (0.1, 0.2, 0.35, 0.5):
    r = e.chat([], 'Describe the lighthouse in one sentence.',
               {'concision': alpha} if alpha else {}, None, max_new=24)
    print(alpha, 'KL', round(r['kl'], 4), '|', repr(r['reply'][:60]))
"""
print(R.exec(code)["output"])
```

## 4. Convenzioni del progetto (non contraddirle)

- **Non aggiungere funzioni a caso.** L'app deve restare un chatbot con steering,
  non un pannello di controllo generale. Ogni aggiunta deve servire a una
  misura o a un esperimento.
- **Niente paper di risultati negativi.** Se un ramo produce solo "questa idea
  non funziona", cambia bersaglio: consegna un metodo che funziona o un risultato
  utilizzabile. Un negativo si riporta come riga secondaria, mai come tesi.
- **Tutto si scrive dentro il workspace.** Niente file su Desktop, in `%TEMP%`,
  in `~/.cache` o fuori dal repo. Se un modello va scaricato, usa una cartella
  dentro il workspace o la cache della VM Colab (che è effimera e va bene).
- **Prima di spendere ore su un ramo, chiediti:** se va come previsto, cosa ho in
  mano che qualcuno userebbe? Se la risposta è "una confutazione", cambia ramo.
- **Documenta**: ogni modifica sostanziale va aggiunta in fondo a `builder.txt`
  con data, motivazione, numeri misurati e correzioni da non ripetere.

## 5. Commit e permessi

- Lavori in un clone del repo. `git add -A && git commit -m "..."` e `git push`.
  GitHub verifica l'identità: solo i collaboratori con ruolo Write possono
  pushare. Non serve e non si deve chiedere la password del titolare.
- **Non committare segreti**: `control.json` contiene `deploy_key`. Se il repo
  diventa pubblico, ruota la chiave, metti `control.json` in `.gitignore` e
  passa la chiave tramite il Secret `STEERLAB_DEPLOY_KEY` di Colab.
- Un push **non modifica l'app in esecuzione**: per applicarlo usa `R.sync()`
  (hot reload) oppure riavvia `START.ipynb`.

## 6. Diagnostica rapida

| Sintomo | Causa | Azione |
|---|---|---|
| Cloudflare `1033` | sessione Colab morta / `cloudflared` spento | §1, riavvio |
| `403 unauthorized` | chiave errata o ruotata | aggiorna `control.json` |
| HTTP `524` / timeout | richiesta > ~100 s | `background()` + `poll()` |
| `ImportError bitsandbytes` | 4-bit opzionale mancante | **non è un errore**: il motore ripiega su fp16/bf16 |
| `0.0s` nella calibrazione per-layer | cache delle direzioni inquinata | usa `build_direction(..., force=True)` (già fixato) |
| testo identico con steering attivo | argmax invariato | misura KL / margine, non le parole |
| modello non caricato dopo `sync` | reload scarica i pesi | `R.api("/api/model/load", {})` |
| `calibration MISMATCH` | calibrazione di un altro modello | ricalibra: `R.background("python -u calibrate.py")` |

## 7. Checklist di presa in carico

- [ ] Letti `builder.txt`, `EQUALIZER_RESEARCH.md`, `COLLAB.md`
- [ ] `control.json` con `app_url` + `deploy_key` validi
- [ ] `R.ping()` → `ok: true`
- [ ] `R.state()` → modello caricato, 9 assi, 28 layer
- [ ] `R.sync()` eseguito una volta per allineare il workspace alla VM
- [ ] Un esperimento di controllo (barra a 0 → KL 0; barra > 0 → KL > 0)
- [ ] Da qui in poi: modifica, misura, documenta, committa. **Zero click umani.**
