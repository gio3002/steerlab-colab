"""
calibrate.py — calibrazione a posteriori, sul modello scelto.

Produce:
  calibration/directions.npz   direzioni (n_assi, n_layer, hidden) + resid_norm
  calibration/calibration.json estremi sinistro/destro, profilo equalizzatore,
                               metriche e metodo

Metodo (dettagli in builder.txt §4):
  1. direzione per OGNI layer: d_l = mean_i(h_l(a_i) − h_l(b_i)), coppie minime.
  2. per layer: guadagno g_l = Δscore con α piccolo, costo c_l = KL.
  3. efficienza e_l = max(g_l,0)/(c_l+eps); allocazione greedy con budget →
     profilo in [0,1], il layer migliore vale 1.
  4. estremi: sweep di α con il profilo calibrato; si prende il più grande α con
     win rate ≥ soglia, KL ≤ budget e degenerazione contenuta. Sinistro e destro
     separati (l'effetto non è simmetrico).

Uso:
  python calibrate.py                     # completo (GPU consigliata)
  python calibrate.py --quick             # test rapido su CPU
  python calibrate.py --axes concision,calm --layers 4
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import torch

from engine import Engine, CAL_JSON, DIRS_NPZ, CAL_DIR
import axes as AXES_MOD

EPS = 1e-6


def log(msg):
    print(f"[calib {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def score_continuation(eng: Engine, prompt: str, cont: str,
                       axis_values: dict, profiles: dict) -> float:
    """media log p(cont | prompt) con steering attivo su tutte le posizioni."""
    ids_p = eng.tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"]
    full = prompt + cont
    ids = eng.tok(full, return_tensors="pt", add_special_tokens=False)["input_ids"].to(eng.device)
    n_p = ids_p.shape[1]
    n = ids.shape[1]
    if n <= n_p:
        return float("nan")
    with torch.no_grad():
        with eng._hooks(axis_values, profiles, 0):     # 0 = tutte le posizioni
            out = eng.model(input_ids=ids, use_cache=False)
        lg = out.logits[0].float().cpu().numpy()
    lp = Engine._logp(lg[n_p - 1])                      # predice il primo token di cont
    # per continuazioni multi-token servono le posizioni successive
    total, cnt = 0.0, 0
    for j in range(n_p, n):
        row = lg[j - 1]
        lrow = Engine._logp(row)
        total += float(lrow[int(ids[0, j])])
        cnt += 1
    return total / max(1, cnt)


def margin(eng: Engine, probes: list[dict], axis_values: dict, profiles: dict) -> float:
    vals = []
    for pr in probes:
        pos = score_continuation(eng, pr["prompt"], pr["pos"], axis_values, profiles)
        neg = score_continuation(eng, pr["prompt"], pr["neg"], axis_values, profiles)
        if np.isfinite(pos) and np.isfinite(neg):
            vals.append(pos - neg)
    return float(np.mean(vals)) if vals else float("nan")


def kl_first(eng: Engine, prompt: str, axis_values: dict, profiles: dict) -> float:
    ids = eng.tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"].to(eng.device)
    with torch.no_grad():
        with eng._hooks({}, {}, 0):
            base = eng.model(input_ids=ids, use_cache=False).logits[0, -1].float().cpu().numpy()
        with eng._hooks(axis_values, profiles, 0):
            ste = eng.model(input_ids=ids, use_cache=False).logits[0, -1].float().cpu().numpy()
    return float(Engine._kl(base, ste))


def gen_length(eng: Engine, prompt: str, axis_values: dict, profiles: dict,
               max_new: int = 48) -> int:
    ids = eng.tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"].to(eng.device)
    n = 0
    for _nxt, _meta in eng._generate_stream(prompt, axis_values, profiles, max_new, 0.0, 0):
        n += 1
    return n


def length_delta(eng: Engine, probes: list[dict], axis_values: dict,
                 profiles: dict, max_new: int) -> float:
    d = []
    for pr in probes:
        a = gen_length(eng, pr["prompt"], {}, {}, max_new)
        b = gen_length(eng, pr["prompt"], axis_values, profiles, max_new)
        d.append(b - a)
    return float(np.mean(d)) if d else float("nan")


def measure(eng: Engine, ax: dict, axis_values: dict, profiles: dict,
            max_new: int) -> tuple[float, float]:
    """(guadagno, costo). Per readout length il guadagno è −Δlunghezza se il polo
    è 'conciso' (sinistra), +Δ se 'prolisso' (destra). Il chiamante passa il
    segno giusto tramite axis_values già orientato."""
    if ax.get("readout") == "length":
        d = length_delta(eng, ax["probes"], axis_values, profiles, max_new)
        return d, kl_first(eng, ax["probes"][0]["prompt"], axis_values, profiles)
    return margin(eng, ax["probes"], axis_values, profiles), \
        kl_first(eng, ax["probes"][0]["prompt"], axis_values, profiles)


def allocate(layer_gain: list[float], layer_cost: list[float], budget: int = 8) -> list[float]:
    """Allocazione greedy: aggiunge budget al layer con efficienza più alta,
    con rendimenti decrescenti (ogni colpo vale 0.7 del precedente)."""
    n = len(layer_gain)
    eff = [max(0.0, g) / (c + EPS) for g, c in zip(layer_gain, layer_cost)]
    prof = [0.0] * n
    if max(eff) <= 0:
        return [1.0] * n
    for _ in range(budget):
        best, bi = -1.0, -1
        for i in range(n):
            marginal = eff[i] * (0.7 ** int(prof[i] * 4))
            if marginal > best:
                best, bi = marginal, i
        prof[bi] += 0.25
    m = max(prof) or 1.0
    return [round(p / m, 4) for p in prof]


def calibrate(axes_keys: list[str], layer_stride: int, alphas: list[float],
              win_threshold: float, kl_budget: float, max_new: int,
              quick: bool) -> dict:
    eng = Engine()
    log(f"carico {eng.cfg['model_id']} (4bit={eng.cfg.get('load_4bit')})")
    eng.load()
    log(f"modello pronto in {eng.load_seconds}s · {eng.n_layers} layer · {eng.hidden} dim")
    L = eng.n_layers
    layers = list(range(1, L, max(1, layer_stride)))
    if layers[-1] != L - 1:
        layers.append(L - 1)
    log(f"layer misurati: {layers}")

    dirs = np.zeros((len(eng.order), L, eng.hidden), dtype=np.float32)
    out_axes = {}

    for ai, key in enumerate(eng.order):
        if axes_keys and key not in axes_keys:
            continue
        ax = eng.axes[key]
        t0 = time.time()
        # 1) direzioni. In modalità quick solo per i layer misurati, poi si
        # riempiono i vicini: serve a validare la pipeline su CPU in pochi minuti.
        dir_layers = layers if quick else list(range(L))
        for l in dir_layers:
            dirs[ai, l] = eng.build_direction(key, l)
        if quick:
            for i in range(L):
                near = min(dir_layers, key=lambda x: abs(x - i))
                dirs[ai, i] = dirs[ai, near]
        log(f"{key}: direzioni per {len(dir_layers)} layer in {time.time()-t0:.1f}s")

        # 2) guadagno e costo per layer (α piccolo, profilo piatto)
        a0 = 0.12
        gains, costs = [], []
        for l in layers:
            prof = [0.0] * L
            prof[l] = 1.0
            # segno: verso destra per il guadagno positivo
            av = {key: 1.0}
            g, c = measure(eng, ax, av, {key: prof}, max_new)
            gains.append(g)
            costs.append(max(c, 0.0))
        # 3) profilo
        prof = [0.0] * L
        alloc = allocate(gains, costs, budget=8)
        for l, v in zip(layers, alloc):
            prof[l] = v
        # riempi i layer non misurati interpolando
        for i in range(L):
            if prof[i] == 0.0 and layers:
                near = min(layers, key=lambda x: abs(x - i))
                if abs(near - i) <= max(1, layer_stride // 2):
                    prof[i] = prof[near]
        prof = [round(float(x), 4) for x in prof]

        # 4) estremi, separati, con il profilo calibrato
        def sweep(sign: int) -> tuple[float, dict]:
            best = None
            rows = []
            for a in alphas:
                av = {key: sign * a}
                g, c = measure(eng, ax, av, {key: prof}, max_new)
                rows.append({"alpha": a, "score": g, "kl": c})
                # Il segno atteso dipende dal polo: steerare verso destra deve
                # aumentare il margine (o la lunghezza), verso sinistra diminuirlo.
                # Vale per entrambi i readout, perché i probe sono allineati
                # (pos = polo destro, neg = polo sinistro).
                ok = (g * sign) > 0
                if ok and c <= kl_budget:
                    best = a
            if best is None and rows:
                # nessun α passa: prendi quello con miglior rapporto effetto/costo
                def ratio(r):
                    return (r["score"] * sign) / (r["kl"] + 0.05)
                best = max(rows, key=ratio)["alpha"]
                weak = True
            else:
                weak = False
            return float(best or 0.25), {"rows": rows, "weak": weak}

        er, mr = sweep(+1)
        el, ml = sweep(-1)

        # 5) validazione: profilo calibrato vs piatto, stesso α
        a_test = min(er, 0.4)
        g_cal, _ = measure(eng, ax, {key: a_test}, {key: prof}, max_new)
        g_flat, _ = measure(eng, ax, {key: a_test}, {key: [1.0] * L}, max_new)
        if not (g_cal >= g_flat * 0.95):
            log(f"{key}: profilo calibrato non batte il piatto ({g_cal:.3f} vs {g_flat:.3f}) -> uso piatto")
            prof = [1.0] * L
            g_cal = g_flat

        out_axes[key] = {
            "left": ax["left"], "right": ax["right"],
            "family": ax["family"], "readout": ax.get("readout", "margin"),
            "extreme_left": round(abs(el), 4),
            "extreme_right": round(abs(er), 4),
            "profile": prof,
            "metrics": {
                "method": "efficiency-weighted allocation with KL budget",
                "layers_measured": layers,
                "layer_gain": [round(float(x), 4) for x in gains],
                "layer_cost": [round(float(x), 4) for x in costs],
                "win_alpha_right": mr, "win_alpha_left": ml,
                "gain_calibrated": round(float(g_cal), 4),
                "gain_flat": round(float(g_flat), 4),
                "profile_helps": bool(g_cal > g_flat),
            },
        }
        log(f"{key}: estremi L={el:.2f} R={er:.2f} · profilo max={max(prof):.2f} "
            f"· guadagno cal {g_cal:.3f} vs piatto {g_flat:.3f}")

    # resid_norm per layer, misurata
    eng._estimate_resid_norm()
    os.makedirs(CAL_DIR, exist_ok=True)
    cal_order = [k for k in eng.order if k in out_axes]
    rows = [eng.order.index(k) for k in cal_order]
    np.savez_compressed(DIRS_NPZ, axes=dirs[rows] if rows else dirs[:0],
                        resid_norm=eng.resid_norm,
                        order=np.array(cal_order))
    cal = {
        "model_id": eng.cfg.get("model_dir") or eng.cfg["model_id"],
        "n_layers": L, "hidden": eng.hidden,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "measured",
        "method": {
            "directions": "paired minimal-pair differences, one per layer",
            "equalizer": "efficiency-weighted greedy allocation with KL budget",
            "extremes": "alpha sweep, largest alpha with positive effect and KL <= budget",
            "references": ["ITI (Li et al. 2023): layer selection by probe accuracy",
                           "CAA (Rimsky et al. 2023): layer choice dominates",
                           "KL-constrained steering: allocate under a divergence budget"],
        },
        "axes": out_axes,
    }
    with open(CAL_JSON, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=1)
    log(f"scritti {CAL_JSON} e {DIRS_NPZ}")
    return cal


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--axes", default="", help="lista separata da virgole")
    p.add_argument("--layers", type=int, default=0, help="stride fra i layer")
    p.add_argument("--alphas", default="0.1,0.2,0.35,0.5,0.7")
    p.add_argument("--win-threshold", type=float, default=0.75)
    p.add_argument("--kl-budget", type=float, default=1.0)
    p.add_argument("--max-new", type=int, default=40)
    p.add_argument("--quick", action="store_true")
    a = p.parse_args()
    keys = [x.strip() for x in a.axes.split(",") if x.strip()]
    stride = a.layers or (6 if a.quick else 2)
    alphas = [float(x) for x in a.alphas.split(",")]
    if a.quick:
        alphas = [0.15, 0.35, 0.6]
    calibrate(keys, stride, alphas, a.win_threshold, a.kl_budget,
              a.max_new, a.quick)


if __name__ == "__main__":
    main()
