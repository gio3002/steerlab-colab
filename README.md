# STEERLAB COLAB

Chat con **vector steering** su un modello che gira su GPU Colab. Nove assi in tre
famiglie (stile, emozioni, modalità), ognuno con una barra da un polo all'altro e un
equalizzatore per layer. La sessione si condivide con un link temporaneo.

Nessuna risorsa locale: il modello vive su Colab, il repo contiene solo codice e
calibrazione.

---

## 1. Avvio (UNA cella) e workflow automatico

- **`START.ipynb`** — **una sola cella**: clona il repo, installa le dipendenze,
  calibra solo se serve, avvia app + tunnel, carica il modello. Stampa `LINK` e
  `CHIAVE`.
- **`open.html`** — pagina di apertura: rileva da sola se l'app è online, offre
  il pulsante per aprirla o per riavviare la GPU.
- **`AGENT.md`** — manuale per un agente WorkBuddy. Un amico apre una nuova chat
  con questo repo come workspace e dice *«apri l'app e continua il progetto»*:
  l'agente avvia Colab, aggiorna `control.json`, poi modifica il codice ed
  esegue gli esperimenti da solo, senza altri click.
- **`remote_ctl.py`** — `R.sync()` carica il workspace sulla VM e ricarica i
  moduli a caldo (il link non cambia); `R.exec()` esegue Python sulla GPU;
  `R.background()` + `R.poll()` gestiscono i lavori lunghi.

Il canale di amministrazione è protetto da `deploy_key` in `control.json`.
**Il repo deve restare privato**: la chiave sta nel repo, quindi coincide con
l'elenco dei collaboratori. Se lo rendi pubblico, ruota la chiave e passala
tramite il Secret Colab `STEERLAB_DEPLOY_KEY`.

> Onestà: Colab free **si scollega** dopo un periodo di inattività. Non esiste
> una GPU gratuita sempre accesa senza una sessione browser attiva. Il riavvio
> è però una cella sola (o un'istruzione al tuo agente). Con un tunnel
> Cloudflare **nominato** (Secret `CLOUDFLARE_TUNNEL_TOKEN` + `public_url` in
> `config.json`) il link resta **fisso** fra riavvii.

---

## 2. Cosa serve e primo avvio

- account Google (Colab), account GitHub (repo privato), `GH_TOKEN` nei Secrets
  di Colab con `Contents: Read` e *Notebook access* attivo.
- Modello **Qwen3-1.7B**. Il 4-bit è **opzionale**: senza `bitsandbytes` il
  motore carica in fp16/bf16 e continua (~3.4 GB su una T4 da 16 GB). I pesi
  non sono nel repo: vengono scaricati al primo avvio.

1. Apri `START.ipynb` (`File → Upload notebook` dal workspace).
2. **Runtime → Change runtime type → T4 GPU → Save**.
3. `Ctrl+F9`. Attendi `PRONTO` (5-15 min se deve calibrare).
4. Apri il **LINK** stampato. `open.html` lo ricorda da solo.

---

## 3. Come si usa l'app

- **Barre**: 0 = nessuno steering. Il numero a destra è l'intensità α applicata
  (`barra × estremo calibrato`). Gli estremi non sono simmetrici: li ha misurati la
  calibrazione.
- **Simbolo ◈** accanto a ogni barra: apre le avanzate.
  - estremi modificabili (se vuoi spingere più o meno forte);
  - **equalizzatore**: una barra per layer, trascinabile. Il default è il profilo
    calibrato; "piatto" e "azzera" sono i due estremi.
- **Chat**: la risposta arriva token per token. Passa il mouse su una parola per
  evidenziarla, clicca per vedere i 5 token più probabili in quel punto.
- **KL** nella barra di stato: quanto la distribuzione si è mossa. Se il testo
  sembra identico, guarda lì.

---

## 4. Come si trova l'equalizzatore (in breve)

Non è a tentativi. Per ogni layer si misura:
- **guadagno** = quanto si muove il comportamento (margine sui probe) per unità di α;
- **costo** = quanta KL produce la stessa unità di α.

Poi si alloca un budget ai layer con il miglior rapporto guadagno/costo, con
rendimenti decrescenti, e si normalizza. Il profilo calibrato viene confrontato con
quello piatto: se non lo batte, si usa il piatto. Il metodo è documentato in
`builder.txt` §4.4 con i riferimenti (ITI, CAA, steering con vincolo di KL).

---

## 5. Struttura

```
builder.txt      specifica completa per altri LLM (leggere prima di modificare)
axes.py          i 9 assi: coppie minime, probe, famiglie
engine.py        caricamento 4-bit, hook, steering per layer, streaming
calibrate.py     direzioni per layer, estremi, equalizzatore
server.py        API FastAPI
app.py           avvio + tunnel Cloudflare
static/          interfaccia (barre, equalizzatore, chat)
calibration/     calibration.json + directions.npz (prodotti da calibrate.py)
colab.ipynb      notebook pronto
```

---

## 6. Domande frequenti

**Posso mettere i pesi nel repo?**
Sì, ma serve Git LFS e ~1 GB di quota: `git lfs track "*.safetensors"`, metti i pesi
in `models/` e imposta `model_dir` in `config.json`. Il codice lo supporta già.

**Quanto costa?**
Colab gratuito, GPU T4. Il modello in 4-bit occupa ~1.1 GB di VRAM.

**I miei amici vedono la chat?**
No: ogni browser ha la sua cronologia (vive nella pagina). Il server non salva nulla.
Il link è pubblico: chi lo ha può usare l'app finché il runtime è acceso.

**Posso usarlo senza GPU?**
Sì, con un modello piccolo e `load_4bit: false` in `config.json`. Su CPU è lento
(secondi per token), ma funziona: è così che è stato testato.

**Come ricalibro su un altro modello?**
`python calibrate.py` (o la cella dedicata del notebook). Scrive
`calibration/calibration.json` e `directions.npz`; poi committa quei due file.
