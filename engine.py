"""
engine.py — runtime di STEERLAB COLAB.

Responsabilità: caricare il modello (4-bit su GPU, fp32 su CPU), costruire o
leggere le direzioni per layer, applicare le barre con l'equalizzatore, generare
in streaming con i metadati per token.

Nessuna dipendenza dal progetto locale: questo file è autonomo.
"""

from __future__ import annotations

import contextlib
import gc
import hashlib
import json
import math
import re
import os
import threading
import time

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
CAL_DIR = os.path.join(HERE, "calibration")
CAL_JSON = os.path.join(CAL_DIR, "calibration.json")
DIRS_NPZ = os.path.join(CAL_DIR, "directions.npz")

import axes as AXES_MOD


def _key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _finite(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


class EngineError(RuntimeError):
    pass


class Engine:
    def __init__(self, config: dict | None = None):
        with open(CONFIG, encoding="utf-8") as f:
            self.cfg = config or json.load(f)
        # override da ambiente: utile per test locali e per Colab
        if os.environ.get("STEERLAB_MODEL_ID"):
            self.cfg["model_id"] = os.environ["STEERLAB_MODEL_ID"]
        if os.environ.get("STEERLAB_LOAD_4BIT") is not None:
            self.cfg["load_4bit"] = os.environ["STEERLAB_LOAD_4BIT"] not in ("0", "false", "")
        if os.environ.get("STEERLAB_DEVICE"):
            self.cfg["device"] = os.environ["STEERLAB_DEVICE"]
        if os.environ.get("STEERLAB_MODEL_DIR"):
            self.cfg["model_dir"] = os.environ["STEERLAB_MODEL_DIR"]
        self.model = None
        self.tok = None
        self.blocks = None
        self.n_layers = 0
        self.hidden = 0
        self.device = "cpu"
        self.quantized = False
        want = [x.strip() for x in os.environ.get("STEERLAB_AXES", "").split(",") if x.strip()]
        src = [a for a in AXES_MOD.AXES if (not want or a["key"] in want)]
        self.axes = {a["key"]: a for a in src}
        self.order = [a["key"] for a in src]
        self.dirs: np.ndarray | None = None          # (A, L, H) unitarie
        self.resid_norm = np.ones(1, dtype=np.float32)
        self.cal: dict = {"axes": {}}
        self.profiles: dict[str, list[float]] = {}
        self.lock = threading.RLock()
        self._dir_cache: dict[tuple[str, int], np.ndarray] = {}
        # Replica usata SOLO come ripiego a runtime (assi senza calibrazione).
        # NON va nella _dir_cache: altrimenti la calibrazione crederebbe di avere
        # gia' la direzione di ogni layer e salterebbe il calcolo (bug 2026-11-10).
        self._dir_replica: dict[str, np.ndarray] = {}
        self.load_seconds: float | None = None
        self.last_error: str | None = None
        self._load_calibration()

    # ------------------------------------------------------------- stato
    def state(self) -> dict:
        return {
            "model_id": self.cfg.get("model_id"),
            "model_dir": self.cfg.get("model_dir"),
            "device": self.device,
            "quantized": self.quantized,
            "load_note": getattr(self, "load_note", None),
            "load_warning": getattr(self, "last_error", None),
            "n_layers": self.n_layers,
            "hidden": self.hidden,
            "loaded": self.model is not None,
            "calibration": self.cal.get("source", "none"),
            "calibration_model": self.cal.get("model_id"),
            "axes": self.order,
        }

    def _default_profile(self) -> list[float]:
        """Senza calibrazione: banda gaussiana centrata al 60% della profondità.

        Un profilo piatto su tutti i layer somma troppi interventi e degenera il
        testo (misurato: 'staystaystay...'). La banda è un prior ragionevole:
        CAA e ITI trovano l'effetto migliore a metà/seconda metà della rete.
        """
        L = max(1, self.n_layers)
        c = 0.60 * (L - 1)
        sig = max(1.0, 0.12 * L)
        prof = []
        for l in range(L):
            v = math.exp(-0.5 * ((l - c) / sig) ** 2)
            prof.append(0.0 if v < 0.15 else round(v, 4))
        m = max(prof) or 1.0
        return [round(x / m, 4) for x in prof]

    def _load_calibration(self):
        try:
            with open(CAL_JSON, encoding="utf-8") as f:
                self.cal = json.load(f)
            self.cal["source"] = "measured"
        except (OSError, ValueError):
            self.cal = {"axes": {}, "source": "none"}
        for k in self.order:
            c = self.cal.get("axes", {}).get(k)
            if c and isinstance(c.get("profile"), list):
                self.profiles[k] = [float(x) for x in c["profile"]]
        self.dir_order: list[str] = []
        try:
            z = np.load(DIRS_NPZ)
            d = z["axes"]
            if d.ndim == 3:
                self.dirs = d.astype(np.float32)
                if "order" in z:
                    self.dir_order = [str(x) for x in z["order"]]
                else:
                    self.dir_order = list(self.order)[:d.shape[0]]
            rn = z.get("resid_norm")
            if rn is not None:
                self.resid_norm = rn.astype(np.float32)
        except (OSError, ValueError, KeyError):
            self.dirs = None
            self.dir_order = []

    # ---------------------------------------------------------- caricamento
    @staticmethod
    def _bnb_ok() -> bool:
        """True solo se bitsandbytes esiste ed e' >= 0.46.1 (minimo transformers)."""
        try:
            import bitsandbytes as bnb
        except Exception:
            return False
        nums = [int(x) for x in re.findall(r"\d+", str(getattr(bnb, "__version__", "0")))[:3]]
        nums += [0] * (3 - len(nums))
        return tuple(nums) >= (0, 46, 1)

    def load(self) -> dict:
        if self.model is not None:
            return self.state()
        # `model_dir` (percorso locale) ha precedenza su `model_id` (HuggingFace).
        # Serve per test offline e per chi vuole i pesi nel repo.
        mid = self.cfg.get("model_dir") or self.cfg["model_id"]
        dev = self.cfg.get("device", "auto")
        if dev == "auto":
            if torch.cuda.is_available():
                self.device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        else:
            self.device = dev
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import transformers
            transformers.utils.logging.set_verbosity_error()
            transformers.utils.logging.disable_progress_bar()
            t0 = time.time()
            tok = AutoTokenizer.from_pretrained(mid, trust_remote_code=True)
            if tok.pad_token_id is None:
                tok.pad_token = tok.eos_token
            # dtype: fp32 CPU, bf16 Ampere+, fp16 Turing (T4). Il 4-bit e'
            # OPZIONALE: su T4 un 1.7B in fp16 occupa ~3.4 GB su 16 GB di VRAM.
            if self.device == "cpu":
                dtype = torch.float32
            else:
                cap = torch.cuda.get_device_capability(0)[0] if self.device == "cuda" else 0
                dtype = torch.bfloat16 if cap >= 8 else torch.float16
            self.quantized = False
            self.load_note = f"{str(dtype).replace('torch.', '')} su {self.device}"
            kw = {"trust_remote_code": True, "torch_dtype": dtype}
            model = None
            want4 = bool(self.cfg.get("load_4bit")) and self.device == "cuda"
            if want4 and self._bnb_ok():
                try:
                    from transformers import BitsAndBytesConfig
                    kw4 = dict(kw)
                    kw4["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True, bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=dtype,
                        bnb_4bit_use_double_quant=True)
                    kw4["device_map"] = "auto"
                    model = AutoModelForCausalLM.from_pretrained(mid, **kw4)
                    self.quantized = True
                    self.load_note = "4bit NF4 (bitsandbytes)"
                except Exception as e:
                    self.last_error = f"4bit non riuscito ({e}); ripiego su {dtype}"
                    self.quantized = False
                    model = None
            elif want4:
                self.last_error = ("bitsandbytes assente o < 0.46.1: "
                                   "uso fp16/bf16, nessun problema")
            if model is None:
                model = AutoModelForCausalLM.from_pretrained(mid, **kw)
            if not self.quantized:
                model.to(self.device)
            model.eval()
            self.model, self.tok = model, tok
            self._find_blocks()
            self._find_head()
            self.load_seconds = round(time.time() - t0, 2)
            print(f"[engine] modalita: {self.load_note}", flush=True)
            if self.dirs is None:
                self._build_all_directions()
            # Per gli assi senza direzioni nel file di calibrazione: una direzione
            # al layer centrale, replicata a tutti i layer. Evita 32 build al primo
            # uso (che su CPU sarebbero minuti) e rende prevedibile il caricamento.
            for k in self.order:
                if k not in self.dir_order:
                    Lc = self.n_layers // 2
                    # una sola direzione, tenuta come ripiego: NON riempie la cache
                    self._dir_replica[k] = self.build_direction(k, Lc, force=True)
            if self.resid_norm.shape[0] != self.n_layers:
                self._estimate_resid_norm()
            base = self._default_profile()
            for k in self.order:
                self.profiles.setdefault(k, list(base))
            return self.state()
        except Exception as e:
            self.model = None
            raise EngineError(f"caricamento fallito: {type(e).__name__}: {e}") from e

    def _find_blocks(self):
        for path in ("model.layers", "transformer.h", "gpt_neox.layers",
                     "model.decoder.layers"):
            obj = self.model
            ok = True
            for part in path.split("."):
                obj = getattr(obj, part, None)
                if obj is None:
                    ok = False
                    break
            if ok and hasattr(obj, "__len__"):
                self.blocks = obj
                break
        if self.blocks is None:
            raise EngineError("blocchi decoder non trovati")
        self.n_layers = len(self.blocks)
        cfg = getattr(self.model, "config", None)
        self.hidden = int(getattr(cfg, "hidden_size", getattr(cfg, "n_embd", 0)))
        if not self.hidden:
            raise EngineError("hidden size non determinabile")

    def _find_head(self):
        self.final_norm = None
        for path in ("model.norm", "transformer.ln_f", "model.final_layernorm"):
            obj = self.model
            for part in path.split("."):
                obj = getattr(obj, part, None)
                if obj is None:
                    break
            if obj is not None:
                self.final_norm = obj
                break
        try:
            self.lm_head = self.model.get_output_embeddings().weight.detach().float().cpu()
        except Exception:
            self.lm_head = None

    # ------------------------------------------------------------ encoding
    def _apply_template(self, msgs: list[dict]) -> str:
        try:
            return self.tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
                enable_thinking=False)
        except TypeError:
            return self.tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
        except Exception:
            return "\n".join(f"{m['role']}: {m['content']}" for m in msgs) + "\nassistant:"

    def _encode(self, text: str, raw: bool = False):
        if not raw:
            text = self._apply_template([{"role": "user", "content": text}])
        ids = self.tok(text, return_tensors="pt", add_special_tokens=True)["input_ids"]
        return ids.to(self.device)

    # ------------------------------------------------------------- direzioni
    def _acts(self, texts: list[str], layer: int) -> np.ndarray:
        out = []
        for t in texts:
            ids = self._encode(t)
            got = {}

            def hk(module, inp, o, L=layer):
                h = o[0] if isinstance(o, (tuple, list)) else o
                got["v"] = h[0, -1, :].detach().float().cpu().numpy()
                return None

            hd = self.blocks[layer].register_forward_hook(hk)
            try:
                with torch.no_grad():
                    self.model(input_ids=ids, use_cache=False)
            finally:
                hd.remove()
            out.append(got["v"])
        return np.stack(out).astype(np.float32)

    def build_direction(self, axis_key: str, layer: int,
                        force: bool = False) -> np.ndarray:
        """d = mean_i(h(a_i) - h(b_i)) al layer dato. Cache in memoria.

        force=True ignora cache e replica: lo usa la calibrazione, che deve
        misurare la direzione VERA di ogni layer.
        """
        ck = (axis_key, int(layer))
        if not force and ck in self._dir_cache:
            return self._dir_cache[ck]
        if not force and axis_key in self._dir_replica:
            return self._dir_replica[axis_key]
        ax = self.axes[axis_key]
        A = [p["a"] for p in ax["pairs"]]
        B = [p["b"] for p in ax["pairs"]]
        HA = self._acts(A, layer)
        HB = self._acts(B, layer)
        d = (HA - HB).mean(0)
        n = float(np.linalg.norm(d))
        if n < 1e-9:
            raise EngineError(f"direzione nulla per {axis_key} al layer {layer}")
        d = (d / n).astype(np.float32)
        self._dir_cache[ck] = d
        return d

    def _build_all_directions(self):
        """Senza directions.npz: costruisce al layer centrale e replica.
        È un fallback: la calibrazione vera produce una direzione per layer."""
        L = self.n_layers // 2
        arr = np.zeros((len(self.order), self.n_layers, self.hidden), dtype=np.float32)
        for i, k in enumerate(self.order):
            d = self.build_direction(k, L)
            arr[i, :, :] = d
        self.dirs = arr
        self._estimate_resid_norm()

    def _estimate_resid_norm(self):
        """Norma media del residual per layer con UN forward per prompt.

        La versione precedente faceva un forward per layer (32×6 = 192 forward):
        su CPU sono minuti. Con gli hook su tutti i blocchi basta un forward per
        prompt e si leggono tutte le profondità insieme.
        """
        probes = ["The lighthouse stands on the coast.",
                  "A short note about the weather.",
                  "The meeting is scheduled for noon.",
                  "Explain why the sky is blue.",
                  "Once upon a time there was a village.",
                  "The train leaves at six."]
        n = self.n_layers
        vals = np.zeros(n, dtype=np.float64)
        got: dict[int, np.ndarray] = {}

        def make(i):
            def hk(module, inp, out):
                h = out[0] if isinstance(out, (tuple, list)) else out
                got[i] = h[0, -1, :].detach().float().cpu().numpy()
                return None
            return hk

        handles = [self.blocks[i].register_forward_hook(make(i)) for i in range(n)]
        try:
            for t in probes:
                ids = self._encode(t)
                with torch.no_grad():
                    self.model(input_ids=ids, use_cache=False)
                for i in range(n):
                    vals[i] += float(np.linalg.norm(got[i]))
        finally:
            for h in handles:
                try:
                    h.remove()
                except Exception:
                    pass
        self.resid_norm = (vals / len(probes)).astype(np.float32)

    # --------------------------------------------------------------- hooks
    @contextlib.contextmanager
    def _hooks(self, axis_values: dict[str, float], profiles: dict[str, list[float]],
               prompt_len: int):
        """Costruisce un vettore per layer: somma dei contributi degli assi,
        ciascuno con il proprio α (barra × estremo) e il proprio profilo."""
        by_layer: dict[int, torch.Tensor] = {}
        for k, v in (axis_values or {}).items():
            if k not in self.axes or abs(float(v)) < 1e-6:
                continue
            cal = self.cal.get("axes", {}).get(k, {})
            ext = float(cal.get("extreme_right" if v >= 0 else "extreme_left",
                                self.cfg.get("default_extreme", 0.35)))
            alpha = float(v) * abs(ext)
            prof = (profiles or {}).get(k) or self.profiles.get(k) or [1.0] * self.n_layers
            prof = list(prof) + [0.0] * max(0, self.n_layers - len(prof))
            # Il profilo è un insieme di PESI, non un moltiplicatore per layer:
            # normalizziamo a somma 1 così α resta il budget TOTALE di intervento.
            # Senza questo, un profilo con 8 layer attivi moltiplica l'intensità
            # per 8 e il testo degenera (misurato: "LminsAXAXAXA").
            tot = float(sum(max(0.0, float(x)) for x in prof)) or 1.0
            idx = self.dir_order.index(k) if k in self.dir_order else -1
            for l in range(self.n_layers):
                p = max(0.0, float(prof[l])) / tot
                if p <= 1e-6:
                    continue
                d = (self.dirs[idx, l] if (self.dirs is not None and idx >= 0)
                     else self.build_direction(k, l))
                vec = torch.tensor(d * (alpha * p * float(self.resid_norm[l])),
                                   dtype=torch.float32)
                by_layer[l] = by_layer.get(l, torch.zeros(self.hidden)) + vec

        handles = []

        def make(idx):
            def hook(module, inp, out):
                if torch.is_tensor(out):
                    h, tup, rest = out, False, None
                elif isinstance(out, (tuple, list)) and len(out) and torch.is_tensor(out[0]):
                    h, rest, tup = out[0], out[1:], True
                else:
                    return out
                if idx in by_layer:
                    T = h.shape[1]
                    h = h.clone()
                    if T > 1:
                        h[0, max(0, prompt_len - 1):, :] += by_layer[idx].to(h.device, h.dtype)
                    else:
                        h[0, 0, :] += by_layer[idx].to(h.device, h.dtype)
                return (h,) + tuple(rest) if tup else h
            return hook

        try:
            for i, b in enumerate(self.blocks):
                handles.append(b.register_forward_hook(make(i)))
            yield
        finally:
            for h in handles:
                try:
                    h.remove()
                except Exception:
                    pass

    # ----------------------------------------------------------- generazione
    def _generate_stream(self, text: str, axis_values: dict, profiles: dict,
                         max_new: int, temperature: float, seed: int):
        ids = self._encode(text, raw=True)
        prompt_len = ids.shape[1]
        torch.manual_seed(int(seed))
        eos = self.tok.eos_token_id
        specials = set(getattr(self.tok, "all_special_ids", []) or [])
        with torch.no_grad():
            with self._hooks(axis_values, profiles, prompt_len):
                past = None
                cur = ids
                for step in range(int(max_new)):
                    use_c = past is not None
                    inp = cur[:, -1:] if use_c else cur
                    out = self.model(input_ids=inp, past_key_values=past if use_c else None,
                                     use_cache=True)
                    past = out.past_key_values
                    lg = out.logits[0, -1, :].float()
                    probs = torch.softmax(lg, -1)
                    if temperature and temperature > 0:
                        g = torch.Generator(device="cpu").manual_seed(int(seed) + step)
                        nxt = int(torch.multinomial(probs.float().cpu(), 1, generator=g))
                    else:
                        nxt = int(lg.argmax())
                    p = float(probs[nxt])
                    rank = int((probs > probs[nxt]).sum().item()) + 1
                    tv, ti = torch.topk(probs, 5)
                    yield nxt, {
                        "i": step, "id": nxt,
                        "text": self.tok.decode([nxt]),
                        "special": bool(nxt in specials),
                        "p": round(p, 6), "rank": rank,
                        "top": [{"token": self.tok.decode([int(i)]), "id": int(i),
                                 "p": round(float(v), 6)}
                                for v, i in zip(tv.tolist(), ti.tolist())],
                        "first_logits": lg.cpu().numpy() if step == 0 else None,
                    }
                    cur = torch.cat([cur, torch.tensor([[nxt]], device=cur.device)], dim=1)
                    if eos is not None and nxt == eos and step > 0:
                        break

    def stream_chat(self, history: list[dict], message: str, axis_values: dict,
                    profiles: dict | None = None, max_new: int = 96,
                    temperature: float = 0.0, seed: int = 0):
        with self.lock:
            if self.model is None:
                raise EngineError("modello non caricato")
            t0 = time.time()
            msgs = [{"role": m["role"], "content": str(m.get("content", ""))}
                    for m in history if m.get("role") in ("user", "assistant")]
            msgs.append({"role": "user", "content": message})
            full = self._apply_template(msgs)
            yield {"event": "start", "n_prompt_tokens": len(self._encode(full, raw=True)[0]),
                   "formatted_prompt": full}
            metas, ids = [], []
            first = None
            for nxt, meta in self._generate_stream(full, axis_values, profiles or {},
                                                   max_new, temperature, seed):
                if meta["i"] == 0:
                    first = meta.pop("first_logits", None)
                else:
                    meta.pop("first_logits", None)
                metas.append(meta)
                ids.append(nxt)
                yield {"event": "token", **meta}
            reply = "".join(m["text"] for m in metas if not m.get("special")).strip()
            if not reply:
                reply = self.tok.decode(ids, skip_special_tokens=False).strip()
            kl = 0.0
            if first is not None:
                base = self._baseline_logits(msgs)
                kl = _finite(self._kl(base, first)) or 0.0
            yield {"event": "done", "ok": True, "reply": reply,
                   "history": msgs + [{"role": "assistant", "content": reply}],
                   "tokens_meta": metas, "kl": kl,
                   "compute_ms": int((time.time() - t0) * 1000)}

    def _baseline_logits(self, msgs: list[dict]) -> np.ndarray:
        full = self._apply_template(msgs)
        ids = self._encode(full, raw=True)
        with torch.no_grad():
            with self._hooks({}, {}, ids.shape[1]):
                out = self.model(input_ids=ids, use_cache=False)
        return out.logits[0, -1, :].float().cpu().numpy()

    @staticmethod
    def _logp(logits):
        z = np.asarray(logits, dtype=np.float64)
        z = z - z.max()
        return z - np.log(np.exp(z).sum())

    @classmethod
    def _kl(cls, p, q):
        lp, lq = cls._logp(p), cls._logp(q)
        return float(np.sum(np.exp(lp) * (lp - lq)))

    def chat(self, history, message, axis_values, profiles=None,
             max_new=96, temperature=0.0, seed=0):
        events = list(self.stream_chat(history, message, axis_values, profiles,
                                       max_new, temperature, seed))
        done = [e for e in events if e.get("event") == "done"]
        if not done:
            raise EngineError("generazione non completata")
        return done[-1]

    def unload(self):
        self.model = None
        self.tok = None
        self.blocks = None
        self.dirs = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


ENGINE = Engine()