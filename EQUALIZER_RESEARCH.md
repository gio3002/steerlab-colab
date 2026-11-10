# L'EQUALIZZATORE — ricerca, derivazione, implementazione

> Domanda di partenza: *«i primi layer riguardano la sintassi, quelli al centro la
> semantica: non mi sembra scontato, né teoricamente né praticamente, trovare la
> configurazione ottimale»*. **L'intuizione è corretta**, e la letteratura
> recente la rende molto più precisa di quanto sospettassi. Questo documento
> sostituisce il §4.4 di `builder.txt`, che era un'euristica presentata come
> metodo.

---

## 1. Perché il problema è davvero difficile (e non è un equalizzatore audio)

In un equalizzatore audio le bande sono **indipendenti**: alzare i 4 kHz non
cambia ciò che fa il filtro a 200 Hz. In un transformer **no**. Il residual
stream è una somma cumulativa:

$$h_L = h_0 + \sum_{\ell=0}^{L-1} f_\ell(h_\ell), \qquad
h_{\ell+1} = h_\ell + f_\ell(h_\ell)$$

Iniettare $w_\ell d_\ell$ al layer $\ell$ altera **l'input di tutti i layer
successivi**. L'effetto non è additivo: attenzione, MLP e (RMS)LayerNorm a valle
sono non lineari. Quindi la funzione obiettivo è

$$J(w) = \text{effetto comportamentale}\big(\{w_\ell\}\big), \qquad
\text{vincolo: } \mathrm{KL}\big(p_{\text{base}} \| p_w\big) \le \varepsilon$$

e **non è separabile**. Ogni misura "a un layer alla volta" stima solo il
gradiente $\partial J/\partial w_\ell$ in un punto, ignorando i termini misti
$\partial^2 J/\partial w_i \partial w_j$.

Cinque insidie concrete, tutte documentate:

1. **Bias di profondità.** Le norme delle attivazioni crescono con $\ell$.
   Confrontare magnitudini grezze fra layer premia sistematicamente gli ultimi
   layer, anche quando sono causalmente meno utili.
2. **"Leggibile" ≠ "steerabile".** Un probe può leggere una proprietà al layer 4
   senza che iniettare lì produca un output coerente. ITI seleziona le teste in
   base all'accuratezza di validazione, non alla separabilità in training.
3. **Non monotonicità in $\alpha$.** Aumentare l'intensità può *ridurre* o
   invertire l'effetto (osservato anche nei nostri dati: `refusal` 7/8 a
   $\alpha=0.20$, 0/8 a $0.35$). Un allocatore che assume monotonicità locale
   può sbagliare in silenzio.
4. **LayerNorm / normalizzazione.** Un intervento precoce viene in parte
   ri-normalizzato; uno tardivo impatta i logit quasi direttamente ma ha
   pochissima "pista di rullaggio" per essere integrato.
5. **Il costo giusto è il KL sulle distribuzioni di output**, non la distanza
   euclidea fra attivazioni. La metrica rilevante è il pullback della metrica
   di Fisher dello strato softmax, che è fortemente non euclidea e il cui
   condizionamento **peggiora con la profondità**.

---

## 2. Cosa dice la letteratura

### 2.1 Il paper centrale: l'allocazione in profondità come problema a sé

**Góral, Winkels & Basart (2025), *Depth-Wise Activation Steering for Honest
Language Models*, arXiv:2512.07667.** È esattamente la domanda posta. Gli
autori osservano che gli steering esistenti sono *degeneri*: o **punto di massa**
su un solo layer (CAA, ActAdd), o peso **quasi uniforme** su tutti i layer.
Propongono una *Gaussian depth schedule*

$$\alpha_\ell = A \exp\!\left(-\frac{(\ell-\mu)^2}{2\sigma^2}\right)$$

con ampiezza $A$, centro $\mu$, larghezza $\sigma$, **a parità di budget
totale**. Risultati su 7 modelli (LLaMA/Qwen/Mistral, benchmark MASK):
migliora in 6/7 vs single-layer, guadagni a due cifre in 4/7; dove il
single-layer *peggiorava* (Qwen), la schedule recupera.

**Il risultato decisivo per noi**: le **ablazioni a budget costante** (LLaMA-3.1-8B
e Qwen-2.5-7B) mostrano che la gaussiana batte allocazioni **random, uniform e
box-filter**. A parità di energia totale, *solo la forma* cambia l'esito.
Controesempio onesto: su Mistral-7B-Instruct-v0.2 il single-layer ha un picco
più alto (40.7 vs 32.9). Quindi la schedule non è un dogma: **va validata**.

Meccanismo: layer iniziali = sintassi/feature locali (il segnale si dissolve);
layer medi (≈40–80%) = concetti astratti linearmente separabili con ancora
layer non lineari a valle per amplificarli (*computational runway*); layer
finali = commit sul vocabolario, sostituzione grezza di token.

### 2.2 Vincolo KL per layer, in forma chiusa

**Azizi, Baghaei Potraghloo & Pedram (2025), arXiv:2507.04742.** Formalizzano

$$\mathrm{KL}(\mathrm{softmax}(z) \| \mathrm{softmax}(\tilde z)) \le \varepsilon$$

e scompongono lo shift dei logit in $\gamma\, \mathbf{J}_\ell \mathbf{v}$ più un
resto di curvatura $L_\ell$. Ottengono una scala massima $\gamma^{(\ell)}_{\max}$
in forma chiusa (equazione cubica adimensionale), con $\mathbf{J}_\ell$
Jacobiano locale. Stima pratica: una JVP per $\|\mathbf{J}_\ell \mathbf{v}\|$ e
una HVP per la curvatura, su ~50 hidden states, pochi secondi.

**Perché conta**: $\gamma^{(\ell)}_{\max}$ dipende dal layer. Fornisce la base
formale per pesare $\alpha_\ell \propto \gamma^{(\ell)}_{\max}$, cioè più
intensità dove il rapporto segnale/costo-KL locale è migliore.

### 2.3 La geometria giusta non è euclidea

**FishBack** (Wang et al., arXiv:2605.17231 — preprint 2026, da verificare):
la perturbazione di minima distorsione off-target è
$\delta h^* = \rho\, G^+ q / (q^\top G^+ q)$ con $G$ metrica di Fisher tirata
indietro tramite lo Jacobiano dei layer successivi. Riportano deviazione >
97% in norma spettrale dalla metrica euclidea, dimensionalità effettiva
2–17%, condizionamento $>10^7$ crescente con la profondità. Spiega
formalmente perché il costo KL per unità di effetto **non è uniforme in
profondità**. (Il nostro motore usa già una metrica di Mahalanobis con
shrinkage + Woodbury per la composizione A/B; qui servirebbe il pullback
ricorsivo, che è il passo successivo.)

### 2.4 Altri risultati che cambiano il disegno

| Fonte | Risultato | Conseguenza |
|---|---|---|
| ITI, Li et al. 2023, arXiv:2306.03341 | probe per attention head, selezione top-K su accuratezza di validazione | selezionare per **causalità in validazione**, non per separabilità |
| *Extending Activation Steering to Broad Skills*, arXiv:2403.05767 | combinare vettori **allo stesso layer** degrada o inverte; layer **disgiunti** danno controllo quasi indipendente | con 9 assi attivi insieme serve un partizionamento del budget, non un profilo unico |
| Fine-grained steering / SAE-StatSteer | usare la **proporzione di esempi con segno coerente**, non la magnitudine (che cresce con la profondità) | normalizzare per `resid_norm` e pesare per qualità della direzione |
| Dyn / Dynamic Activation Composition | lo steering è *front-loaded*: quasi tutto l'effetto serve nei primi token, poi il prefisso fa da contesto | asse **temporale**, ortogonale alla profondità: da comporre, non sostituire |
| Statistically Grounded Sparse-Feature Interventions (arXiv:2607.19364, preprint) | su 344 configurazioni, correlazione profondità↔effetto $\rho=-0.005$; il layer ottimo cambia per dominio e per modello | nessuna regola del pollice universale: $\mu$ va **stimato per asse e per modello** |
| CLaS-Bench, ACL 2026 findings | interazione layer×forza: i layer iniziali richiedono $\alpha$ basso e degradano prima; i finali tollerano di più | maschera di profondità e limiti di intensità per layer |

---

## 3. Formulazione corretta del problema

Sia $w \in \mathbb{R}^L_{\ge 0}$ il profilo, normalizzato $\sum_\ell w_\ell = 1$
(così l'α della barra è il **budget totale**, non un moltiplicatore per layer —
questo l'avevamo già corretto, ed era essenziale).

$$\max_{w}\; g^\top w - \tfrac{1}{2} w^\top H w
\quad \text{s.t.}\quad c^\top w \le \varepsilon,\; w \ge 0,\; \sum_\ell w_\ell = 1$$

dove $g_\ell = \partial J/\partial w_\ell$ (sensibilità causale),
$c_\ell$ il costo KL marginale, $H_{ij}$ l'**accoppiamento** fra layer.
$H$ è quello che tutti i metodi citati trascurano. Con $H=0$ e senza il
vincolo di somma si ottiene il nostro vecchio greedy; con $H \neq 0$ è un
programma quadratico. **Nessun paper risolve il caso multi-layer con
accoppiamento in forma chiusa**: Góral danno una forma parametrica, Azizi un
bound per un layer alla volta, FishBack l'ottimo per un layer solo. È una
lacuna reale, non un dettaglio.

Stimare $H$ richiederebbe $\mathcal{O}(L^2)$ misure accoppiate ($\approx 400$
per $L=28$): fattibile su GPU ma costoso. Approcci pratici:

- **SPSA**: 2 valutazioni per iterazione indipendentemente da $L$, stima del
  gradiente di $J(w)$ con perturbazioni simultanee ±;
- **base a bassa dimensionalità**: $w = B\theta$ con $B$ colonne di gaussiane
  / DCT / spline, $\theta \in \mathbb{R}^{4\text{–}6}$ → ottimizzazione
  bayesiana o Nelder-Mead con poche decine di valutazioni;
- **regolarizzazione di smoothness**: $\lambda \|D_2 w\|^2$ penalizza profili
  a pettine (ed è ciò che la gaussiana realizza implicitamente).

---

## 4. Cosa abbiamo implementato (`equalizer.py`)

Rispetto al greedy precedente, cinque cambiamenti sostanziali:

1. **Normalizzazione per `resid_norm[layer]`** — rimuove il bias di profondità
   dalle magnitudini.
2. **Statistica di coerenza di segno** `q_{k,\ell}` — media del coseno fra ogni
   differenza appaiata $h(a_i)-h(b_i)$ e la direzione media. Calcolata in
   `build_direction` **senza forward aggiuntivi** (le attivazioni erano già
   lì). Pesa la qualità della direzione, non la sua ampiezza.
3. **Efficienza** $e_\ell = \max(g_\ell,0)/(c_\ell + 0.02)$ con maschera
   $0.2L \le \ell \le 0.85L$ (le zone in cui la letteratura concorda che lo
   steering è inefficace o dannoso).
4. **Gaussian depth schedule adattata**: $\mu,\sigma$ dai momenti pesati di
   $e_\ell$ (con floor al 25° percentile e centro confinato alla banda),
   mescolata 60/40 con la curva misurata e lisciata con un kernel gaussiano
   corto. Forma liscia + centro e larghezza **interpretabili e mostrabili
   nell'interfaccia**.
5. **Ablazione a budget costante**: per ogni asse si misurano sei profili —
   `gaussian`, `uniform`, `box`, `single`, `greedy`, `random` — tutti a
   $\sum w_\ell = 1$ e stesso $\alpha$. Vince la gaussiana **solo se supera il
   miglior controllo di una soglia esplicita** e rispetta il budget KL;
   altrimenti si usa il controllo. È il protocollo di Góral et al. applicato
   al nostro modello, non una citazione decorativa.

Il risultato dell'ablazione (`scores`, `kl`, `reason`, `ablation_margin`) viene
salvato in `calibration.json` e può essere mostrato nel pannello avanzate.

---

## 5. Cosa resta aperto (lavoro successivo, prioritario)

1. **Matrice di accoppiamento $H$** sui layer top-k: misurare
   $J(i{+}j) - J(i) - J(j) + J(0)$ per ~30 coppie, adattare un QP e verificare
   se il profilo cambia. È il contributo più originale e nessun paper lo fa.
2. **Partizionamento per asse**: con più barre attive, iniettare assi diversi
   in layer disgiunti (arXiv:2403.05767) e controllare l'ortogonalità dei
   vettori nel residual di quel layer.
3. **Asse temporale (Dyn)**: decadere $\alpha$ dopo i primi token, quando il
   prefisso già riflette la proprietà. Compone con la schedule di profondità.
4. **Pullback di Fisher ricorsivo** per il costo KL, invece della nostra
   Mahalanobis globale.
5. **Validazione su held-out**: oggi l'ablazione usa gli stessi probe della
   misura di guadagno. Servono probe tenuti fuori (la batteria locale li ha già)
   per evitare overfitting del profilo.

---

## 6. Onestà bibliografica

Verificati su pagine ufficiali arXiv: **2512.07667**, **2507.04742**,
**2306.03341**, **2403.05767**. I preprint con numerazione 2026
(2605.17231 FishBack, 2607.19364, 2611.01493 TopoSteer, 2603.06745 DIRECTER)
sono stati recuperati tramite aggregatori: contenuti coerenti fra le fonti, ma
**vanno ricontrollati sulla versione corrente prima di citarli in un paper**.
Le conclusioni portanti di questo documento (forma del profilo, bias di
profondità, non monotonicità, costo KL sulle distribuzioni) non dipendono da
quei preprint: reggono sui quattro lavori verificati e sui nostri dati.
