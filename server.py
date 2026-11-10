"""
server.py — API di STEERLAB COLAB.

Solo 127.0.0.1. Nessuna chiamata in uscita (il tunnel è un processo separato).
Ogni endpoint ritorna {"ok": bool, ...}; gli errori non sono mai 500 muti.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import secrets
import sys
import time
import traceback
import hmac

from fastapi import Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import ENGINE, EngineError  # noqa: E402
import axes as AXES_MOD  # noqa: E402

STATIC = os.path.join(HERE, "static")
LOG = os.path.join(HERE, "server.log")
APP_VERSION = "1.0"

# Chiave di amministrazione: generata a ogni avvio, NON sta nel repo.
# Viene stampata in app.log e nella cella 4 del notebook. Chi la possiede puo'
# aggiornare il codice ed eseguire esperimenti senza alcun click in Colab.
DEPLOY_KEY = os.environ.get("STEERLAB_DEPLOY_KEY") or secrets.token_hex(24)
print(f"[admin] DEPLOY_KEY={DEPLOY_KEY}", flush=True)
ADMIN_ON = os.environ.get("STEERLAB_ADMIN", "1") not in ("0", "false", "")

app = FastAPI(title="STEERLAB COLAB", docs_url=None, redoc_url=None)
# open.html puo' essere aperto come file:// o da GitHub Pages: senza CORS il
# browser bloccherebbe il controllo /api/health. Solo lettura, nessun cookie.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


def _log(where: str, exc: BaseException):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {type(exc).__name__}: {exc}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))[-1200:] + "\n")
    except OSError:
        pass
    print(line.rstrip(), flush=True)


def ok(**kw):
    return {"ok": True, **kw}


def safe(where, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except EngineError as e:
        _log(where, e)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        _log(where, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/health")
def health():
    return ok(version=APP_VERSION, uptime_s=round(time.monotonic(), 1))


@app.get("/api/state")
def state():
    s = ENGINE.state()
    eff = ENGINE.cfg.get("model_dir") or ENGINE.cfg.get("model_id")
    s["effective_model"] = eff
    s["calibration_matches"] = (s.get("calibration_model") in (None, eff)
                                or str(eff).endswith(str(s.get("calibration_model") or "")))
    s["ok"] = True
    return s


@app.post("/api/model/load")
def model_load():
    return safe("model/load", lambda: ok(state=ENGINE.load()))


@app.get("/api/axes")
def get_axes():
    def run():
        out = []
        for a in AXES_MOD.AXES:
            cal = ENGINE.cal.get("axes", {}).get(a["key"], {})
            out.append({
                "key": a["key"], "family": a["family"],
                "left": a["left"], "right": a["right"],
                "readout": a.get("readout", "margin"),
                "extreme_left": cal.get("extreme_left", ENGINE.cfg.get("default_extreme", 0.35)),
                "extreme_right": cal.get("extreme_right", ENGINE.cfg.get("default_extreme", 0.35)),
                "profile": ENGINE.profiles.get(a["key"], [1.0] * max(1, ENGINE.n_layers)),
                "calibrated": bool(cal),
                "metrics": cal.get("metrics", {}),
            })
        return ok(axes=out, families=list(AXES_MOD.by_family().keys()),
                  n_layers=ENGINE.n_layers, calibration=ENGINE.cal.get("source", "none"))
    return safe("axes", run)


@app.post("/api/axes/profile")
def set_profile(payload: dict = Body(default={})):
    def run():
        key = str(payload.get("key") or "")
        prof = payload.get("profile")
        if key not in ENGINE.axes:
            raise EngineError(f"asse sconosciuto: {key}")
        if not isinstance(prof, list) or not prof:
            raise EngineError("profilo non valido")
        ENGINE.profiles[key] = [max(0.0, min(1.0, float(x))) for x in prof]
        return ok(key=key, profile=ENGINE.profiles[key])
    return safe("axes/profile", run)


@app.post("/api/axes/extremes")
def set_extremes(payload: dict = Body(default={})):
    def run():
        key = str(payload.get("key") or "")
        if key not in ENGINE.axes:
            raise EngineError(f"asse sconosciuto: {key}")
        el = abs(float(payload.get("extreme_left", 0.35)))
        er = abs(float(payload.get("extreme_right", 0.35)))
        ENGINE.cal.setdefault("axes", {}).setdefault(key, {})
        ENGINE.cal["axes"][key]["extreme_left"] = el
        ENGINE.cal["axes"][key]["extreme_right"] = er
        return ok(key=key, extreme_left=el, extreme_right=er)
    return safe("axes/extremes", run)


@app.post("/api/chat/stream")
def chat_stream(payload: dict = Body(default={})):
    def gen():
        try:
            for ev in ENGINE.stream_chat(
                    list(payload.get("history") or []),
                    str(payload.get("message") or ""),
                    dict(payload.get("axes") or {}),
                    payload.get("profiles") or None,
                    int(payload.get("max_new") or ENGINE.cfg.get("max_new_default", 96)),
                    float(payload.get("temperature") or 0.0),
                    int(payload.get("seed") or 0)):
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        except Exception as e:
            _log("chat/stream", e)
            yield "data: " + json.dumps({"event": "error", "ok": False,
                                         "error": str(e)}, ensure_ascii=False) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/chat")
def chat(payload: dict = Body(default={})):
    return safe("chat", lambda: ok(**ENGINE.chat(
        list(payload.get("history") or []),
        str(payload.get("message") or ""),
        dict(payload.get("axes") or {}),
        payload.get("profiles") or None,
        int(payload.get("max_new") or ENGINE.cfg.get("max_new_default", 96)),
        float(payload.get("temperature") or 0.0),
        int(payload.get("seed") or 0))))


@app.post("/api/calibrate")
def calibrate(payload: dict = Body(default={})):
    """Ricalcola la calibrazione. Lungo: pensato per il notebook Colab."""
    def run():
        import calibrate as CAL
        keys = list(payload.get("axes") or [])
        CAL.calibrate(keys, int(payload.get("layer_stride") or 2),
                      [0.1, 0.2, 0.35, 0.5, 0.7],
                      float(payload.get("win_threshold") or 0.75),
                      float(payload.get("kl_budget") or 1.0),
                      int(payload.get("max_new") or 40), False)
        ENGINE._load_calibration()
        return ok(calibration=ENGINE.cal.get("source"))
    return safe("calibrate", run)


@app.get("/api/log")
def get_log():
    try:
        with open(LOG, encoding="utf-8") as f:
            return ok(lines=f.readlines()[-60:])
    except OSError:
        return ok(lines=[])



# ---------------------------------------------------------------------------
# AMMINISTRAZIONE REMOTA (zero-click)
# ---------------------------------------------------------------------------
def _admin_ok(req: Request) -> bool:
    return ADMIN_ON and hmac.compare_digest(
        req.headers.get("X-Deploy-Key", ""), DEPLOY_KEY)


@app.get("/api/admin/ping")
def admin_ping():
    """Senza chiave: dice solo se il canale di amministrazione e' vivo."""
    return ok(version=APP_VERSION, admin=ADMIN_ON,
              loaded=ENGINE.model is not None, device=ENGINE.device,
              model=ENGINE.cfg.get("model_id"),
              note="usa X-Deploy-Key per deploy/exec/state")


@app.post("/api/admin/deploy")
async def admin_deploy(request: Request):
    """Scrive file di testo e ricarica i moduli IN PROCESSO.

    Il tunnel e' un processo separato: non si riavvia, quindi il link pubblico
    resta identico. Nessun push git, nessun "Run all".
    """
    if not _admin_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, 403)
    try:
        payload = await request.json()
        exts = (".py", ".js", ".html", ".css", ".json", ".txt", ".md", ".ipynb")
        written = []
        for rel, content in (payload.get("files") or {}).items():
            rel = str(rel).replace("\\", "/").lstrip("./")
            parts = rel.split("/")
            if not rel or ".." in parts or rel.startswith(".git/") or not rel.endswith(exts):
                continue
            path = os.path.join(HERE, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content.replace("\r\n", "\n"))
            written.append(rel)
        reloaded = False
        if payload.get("reload", True):
            import engine as ENGINE_MOD
            import axes as AXES_MOD_LOCAL
            try:
                ENGINE.unload()
            except Exception:
                pass
            importlib.reload(AXES_MOD_LOCAL)
            importlib.reload(ENGINE_MOD)
            globals()["ENGINE"] = ENGINE_MOD.ENGINE
            globals()["EngineError"] = ENGINE_MOD.EngineError
            reloaded = True
        return ok(written=written, reloaded=reloaded,
                  note="chiama /api/model/load per ricaricare i pesi")
    except Exception as e:
        _log("admin/deploy", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/admin/exec")
async def admin_exec(request: Request):
    """Esegue Python nella VM e restituisce stdout/stderr. Serve a guidare gli
    esperimenti (calibrazione, diagnostica GPU, cambio modello) da remoto."""
    if not _admin_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, 403)
    try:
        payload = await request.json()
        g = globals()
        ns = {"_g": g, "os": os, "sys": sys, "json": json,
              "time": time, "np": __import__("numpy")}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                exec(str(payload.get("code", "")), ns)
            except Exception:
                traceback.print_exc()
        return ok(output=buf.getvalue()[-20000:])
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/api/admin/state")
def admin_state(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, 403)
    return ok(state=ENGINE.state(), version=APP_VERSION, here=HERE)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    _log(f"unhandled {request.url.path}", exc)
    return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 200)
