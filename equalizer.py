"""
equalizer.py — allocazione dell'intensità di steering tra i layer.

PERCHÉ QUESTO MODULO ESISTE
---------------------------
La versione precedente allocava il budget con un greedy "chi ha l'efficienza
più alta prende un colpo, con rendimenti decrescenti". È un'euristica difesa
solo dal buon senso, e assume che i layer siano INDIPENDENTI. Non lo sono:
il residual stream è una somma cumulativa, quindi iniettare al layer l cambia
lo stato che i layer l+1...L ricevono. Inoltre le norme delle attivazioni
crescono con la profondità, per cui un confronto fra magnitudini grezze è
strutturalmente sbilanciato verso il fondo della rete.

BASE DI LETTERATURA (vedi EQUALIZER_RESEARCH.md per le citazioni)
-----------------------------------------------------------------
1. Góral, Winkels & Basart (2025), "Depth-Wise Activation Steering for Honest
   Language Models", arXiv:2512.07667. Formulano lo steering come ALLOCAZIONE
   DI UN BUDGET FRA LAYER e mostrano, con ablazioni a budget costante, che la
   FORMA del profilo (gaussiana centrata nei layer medio-tardi) batte random,
   uniforme e box-filter. È il risultato centrale: a parità di energia totale,
   conta dove la metti.
2. Azizi et al. (2025), arXiv:2507.04742. Bound KL in forma chiusa,
   curvature-aware, SPECIFICO DEL LAYER: giustifica pesare ogni layer per il
   suo rapporto segnale/costo-KL locale, non per la sola efficacia.
3. Fine-grained activation steering / SAE-StatSteer: usare una statistica di
   COERENZA DI SEGNO fra esempi, non la magnitudine, perché la magnitudine
   cresce con la profondità e produce un bias sistematico.
4. ITI (Li et al. 2023): selezione per-head su accuratezza di probe in
   validazione; CLaS-Bench: i layer iniziali richiedono α basso e degradano
   prima, i finali tollerano α più alto. Da qui la maschera 0.2L–0.85L.
5. Dyn / Dynamic Activation Composition: lo steering è "front-loaded" sui
   primi token (asse temporale, ortogonale alla profondità). Non implementato
   qui, documentato come estensione.

COSA FA QUESTA IMPLEMENTAZIONE
------------------------------
- normalizza il guadagno per la norma del residual del layer (toglie il bias
  di profondità);
- moltiplica per la qualità della direzione (coerenza di segno delle coppie);
- calcola l'efficienza guadagno/KL per layer;
- maschera le zone notoriamente inefficaci (0–20% e 85–100%);
- adatta una GAUSSIANA all'efficienza misurata (forma liscia + centro e
  larghezza interpretabili), mescolata con la curva misurata;
- normalizza a SOMMA 1: α della barra è il budget totale, non un
  moltiplicatore per layer;
- genera i profili di controllo (uniforme, box, single-layer, greedy, random)
  e lascia che sia una MISURA a decidere, con l'ablazione a budget costante
  prescritta da Góral et al. Se la gaussiana non batte i controlli, non viene
  usata: nessun dogma.

LIMITE DICHIARATO: l'accoppiamento del secondo ordine fra layer resta
approssimato (l'ablazione lo mitiga ma non lo modella). La stima esplicita
della matrice di interazione è il passo successivo, descritto in
EQUALIZER_RESEARCH.md §6.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-6


def gaussian(L: int, mu: float, sigma: float) -> np.ndarray:
    """Schedule gaussiana di Góral et al., normalizzata a somma 1."""
    x = np.arange(max(1, int(L)), dtype=np.float64)
    w = np.exp(-0.5 * ((x - float(mu)) / max(0.5, float(sigma))) ** 2)
    s = w.sum()
    return w / s if s > 0 else np.ones_like(w) / len(w)


def fit_gaussian(curve: np.ndarray, L: int,
                 lo: float = 0.20, hi: float = 0.85) -> tuple[float, float]:
    """Adatta (mu, sigma) a una curva di efficienza non negativa per layer.

    Usa i momenti pesati della curva, con un floor al 25° percentile per non
    farsi trascinare dal rumore di fondo. Il centro viene confinato alla banda
    [lo, hi] della profondità, dove la letteratura colloca le feature
    semantiche linearmente separabili con ancora "computational runway" a valle.
    """
    c = np.asarray(curve, dtype=np.float64)
    if c.size == 0:
        return 0.60 * (L - 1), max(1.0, 0.12 * L)
    c = np.maximum(c - np.percentile(c, 25), 0.0)
    x = np.arange(len(c), dtype=np.float64)
    # riporta x sulla scala dei layer reali se la curva è sottocampionata
    if len(c) != L:
        x = x * (L - 1) / max(1, len(c) - 1)
    tot = c.sum()
    if tot <= EPS:
        return 0.60 * (L - 1), max(1.0, 0.12 * L)
    mu = float((x * c).sum() / tot)
    var = float((c * (x - mu) ** 2).sum() / tot)
    sigma = float(np.clip(np.sqrt(var), 1.0, 0.25 * L))
    mu = float(np.clip(mu, lo * (L - 1), hi * (L - 1)))
    return mu, sigma


def _expand(values, measured_layers, L):
    """Interpola i valori misurati su soli `measured_layers` a tutti gli L layer."""
    v = np.asarray(values, dtype=np.float64)
    ml = np.asarray(measured_layers, dtype=int)
    full = np.zeros(L, dtype=np.float64)
    if v.size == L:
        return v
    if ml.size == 0:
        return full
    full = np.interp(np.arange(L), ml, v)
    return full


def _smooth(w: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Convoluzione gaussiana breve: vieta profili a pettine."""
    if sigma <= 0 or len(w) < 3:
        return w
    r = int(max(1, round(3 * sigma)))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(w, (r, r), mode="edge"), k, mode="valid")


def efficiency(gains, costs, measured_layers, L,
               resid_norm=None, quality=None,
               lo: float = 0.20, hi: float = 0.85) -> np.ndarray:
    """Efficienza causale per layer, corretta per norma e coerenza di segno."""
    g = _expand(gains, measured_layers, L)
    c = _expand(costs, measured_layers, L)
    if resid_norm is not None:
        rn = np.asarray(resid_norm, dtype=np.float64)
        if rn.size == L:
            # La magnitudine grezza cresce con la profondità: senza questa
            # normalizzazione qualsiasi confronto cross-layer è spurio.
            g = g / (rn / max(float(np.median(rn)), EPS))
    if quality is not None:
        q = _expand(quality, measured_layers, L)
        g = g * np.clip(q, 0.0, 1.0)
    e = np.maximum(g, 0.0) / (np.maximum(c, 0.0) + 0.02)
    lo_i, hi_i = int(lo * L), int(hi * L)
    mask = np.zeros(L)
    mask[lo_i:hi_i] = 1.0
    e = e * mask
    return e / (e.sum() or 1.0)


def allocate_v2(gains, costs, measured_layers, L,
                resid_norm=None, quality=None, blend: float = 0.6) -> list[float]:
    """Profilo calibrato, normalizzato a SOMMA 1 (α = budget totale)."""
    e = efficiency(gains, costs, measured_layers, L, resid_norm, quality)
    mu, sigma = fit_gaussian(e, L)
    shape = gaussian(L, mu, sigma)
    w = blend * shape + (1.0 - blend) * e
    w = np.maximum(_smooth(w, 1.0), 0.0)
    s = w.sum()
    w = w / s if s > EPS else np.ones(L) / L
    return [round(float(x), 6) for x in w]


def greedy_profile(gains, costs, measured_layers, L, budget: int = 8) -> list[float]:
    """L'allocatore della versione precedente, tenuto come CONTROLLO."""
    e = efficiency(gains, costs, measured_layers, L)
    prof = np.zeros(L)
    for _ in range(int(budget)):
        i = int(np.argmax(e * (0.7 ** prof)))
        prof[i] += 0.25
    s = prof.sum()
    return list(prof / s) if s > EPS else [1.0 / L] * L


def candidate_profiles(gains, costs, measured_layers, L,
                       resid_norm=None, quality=None, seed: int = 7) -> dict:
    """I profili dell'ablazione A BUDGET COSTANTE (Góral et al., Fig. 5).

    Tutti sono normalizzati a somma 1: cambia solo la forma, non l'energia.
    """
    out = {
        "gaussian": allocate_v2(gains, costs, measured_layers, L,
                                resid_norm, quality),
        "uniform": [1.0 / L] * L,
        "greedy": greedy_profile(gains, costs, measured_layers, L),
    }
    e = efficiency(gains, costs, measured_layers, L, resid_norm, quality)
    best = int(np.argmax(e))
    # box-filter: banda contigua centrata sul miglior layer singolo
    half = max(1, int(0.10 * L))
    box = np.zeros(L)
    box[max(0, best - half):min(L, best + half + 1)] = 1.0
    out["box"] = list(box / box.sum())
    # point-mass: il classico CAA / ActAdd a layer singolo
    single = np.zeros(L)
    single[best] = 1.0
    out["single"] = list(single)
    # random: redistribuisce lo stesso budget a caso
    rng = np.random.default_rng(seed)
    r = rng.random(L)
    out["random"] = list(r / r.sum())
    return out


def choose(candidates: dict, scores: dict, kl: dict,
           kl_budget: float = 1.0, min_margin: float = 0.02) -> dict:
    """Sceglie il profilo vincente fra quelli misurati.

    Regola onesta: la gaussiana vince solo se supera IL MIGLIOR CONTROLLO di
    almeno `min_margin` e rispetta il budget KL. Altrimenti si usa il controllo
    migliore: la letteratura stessa riporta casi (Mistral-7B) in cui il
    single-layer batte la schedule.
    """
    valid = {k: v for k, v in scores.items() if kl.get(k, 0.0) <= kl_budget}
    if not valid:
        valid = dict(scores)
    best_ctrl = max((k for k in valid if k != "gaussian"),
                    key=lambda k: valid[k], default=None)
    g = valid.get("gaussian")
    if best_ctrl is None or g is None or g >= valid[best_ctrl] + min_margin:
        winner = "gaussian" if g is not None else best_ctrl
        reason = "gaussian batte i controlli a budget costante"
    else:
        winner = best_ctrl
        reason = f"gaussian non supera {best_ctrl} di {min_margin}: uso il controllo"
    return {"winner": winner, "reason": reason,
            "scores": {k: round(float(v), 4) for k, v in scores.items()},
            "kl": {k: round(float(v), 4) for k, v in kl.items()},
            "ablation_margin": round(float(g - valid[best_ctrl]), 4)
            if (g is not None and best_ctrl is not None) else None}
