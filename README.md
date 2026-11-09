# STEERLAB COLAB

Chat con **vector steering** su un modello che gira su GPU Colab. Nove assi in tre
famiglie (stile, emozioni, modalità), ognuno con una barra da un polo all'altro e un
equalizzatore per layer. La sessione si condivide con un link temporaneo.

Nessuna risorsa locale: il modello vive su Colab, il repo contiene solo codice e
calibrazione.

---

## 1. Cosa serve

- un account Google (per Colab);
- un account GitHub (per il repo privato);
- nient'altro: la GPU è quella gratuita di Colab.

Il modello è **Qwen3-1.7B** in 4-bit (fallback `Qwen2.5-1.5B-Instruct`). Non è nel
repo: GitHub blocca i file oltre 100 MB e un 1.5B in 4-bit pesa ~1 GB. Viene
scaricato da HuggingFace al primo avvio su Colab (~1 minuto). Se vuoi i pesi nel
repo, vedi §6.

---

## 2. Primo avvio su Colab (5 minuti)

1. Apri `colab.ipynb` (dal repo, oppure `File → Upload notebook`).
2. In Colab: **Runtime → Change runtime type → T4 GPU**.
3. Nel pannello a sinistra, icona della chiave (**Secrets**), aggiungi:
   - `GH_TOKEN` = un *personal access token* di GitHub con scope `repo`
     (GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
     tokens → *Repository access: only select repositories* → permesso
     *Contents: Read*). Serve perché il repo è privato.
   - `HF_TOKEN` (facoltativo): evita i limiti di download di HuggingFace.
4. Esegui le celle in ordine. L'ultima stampa un link tipo
   `https://qualcosa.trycloudflare.com`: quello è l'indirizzo da dare agli amici.
5. Alla chiusura del runtime il link muore. Per riaprirlo, riesegui l'ultima cella.

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
