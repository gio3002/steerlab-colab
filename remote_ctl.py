"""
remote_ctl.py — ponte fra il workspace (locale o di un altro agente) e la VM Colab.

Uso minimale:

    from remote_ctl import Remote
    R = Remote.from_control()          # legge control.json (link + chiave)
    print(R.ping())
    print(R.exec("print('ciao dalla GPU')"))
    R.sync()                           # carica TUTTO il workspace sulla VM
    print(R.state())

Nessun click, nessun push: il codice viaggia dentro il tunnel tramite
/api/admin/deploy e i moduli vengono ricaricati a caldo. Il link non cambia.

NOTA sui tempi: Cloudflare chiude le richieste oltre ~100 s. I lavori lunghi
vanno lanciati in background con `background()` e poi seguiti con `poll()`.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT_EXT = (".py", ".js", ".html", ".css", ".json", ".txt", ".md", ".ipynb")
SKIP_DIRS = {".git", "__pycache__", "_dev", "node_modules", "calibration"}
# control.json contiene il link della sessione CORRENTE: non va sovrascritto.
SKIP_FILES = {"control.json", "app.log", "server.log"}


class Remote:
    def __init__(self, base: str, key: str, timeout: int = 90):
        self.base = base.rstrip("/")
        self.key = key
        self.timeout = timeout
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def from_control(cls, path: str | None = None):
        p = path or os.path.join(HERE, "control.json")
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        url, key = c.get("app_url", ""), c.get("deploy_key", "")
        if not url or not key:
            raise RuntimeError("control.json non ha app_url/deploy_key: avvia START.ipynb")
        return cls(url, key)

    def _req(self, path, data=None, auth=True, timeout=None):
        h = {"Content-Type": "application/json"}
        if auth:
            h["X-Deploy-Key"] = self.key
        r = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=h, method="POST" if data is not None else "GET")
        try:
            with self.op.open(r, timeout=timeout or self.timeout) as f:
                return json.load(f)
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:400]}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---- canale di amministrazione ---------------------------------------
    def ping(self):    return self._req("/api/admin/ping", auth=False)
    def state(self):   return self._req("/api/admin/state")
    def deploy(self, files, reload=True):
        return self._req("/api/admin/deploy", {"files": files, "reload": reload})
    def exec(self, code, timeout=None):
        return self._req("/api/admin/exec", {"code": code}, timeout=timeout)

    # ---- app normale -----------------------------------------------------
    def api(self, path, data=None, timeout=None):
        return self._req(path, data, auth=False, timeout=timeout)

    def chat(self, message, axes=None, max_new=64):
        return self.api("/api/chat", {"history": [], "message": message,
                                      "axes": axes or {}, "max_new": max_new})

    # ---- sincronizzazione del workspace ---------------------------------
    def collect(self, root: str | None = None, include_calibration: bool = False):
        root = root or HERE
        files = {}
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS or
                       (include_calibration and d == "calibration")]
            for n in names:
                if (not n.endswith(TEXT_EXT) or n.startswith(".")
                        or n in SKIP_FILES):
                    continue
                fp = os.path.join(base, n)
                rel = os.path.relpath(fp, root).replace("\\", "/")
                try:
                    with open(fp, encoding="utf-8") as f:
                        files[rel] = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
        return files

    def sync(self, reload: bool = True, include_calibration: bool = False):
        """Porta tutto il workspace sulla VM e ricarica i moduli."""
        files = self.collect(include_calibration=include_calibration)
        return self.deploy(files, reload=reload)

    def send_calibration(self, npz_path: str | None = None):
        """Invia directions.npz (binario) tramite exec + base64."""
        npz_path = npz_path or os.path.join(HERE, "calibration", "directions.npz")
        with open(npz_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        code = (
            "import base64, os\n"
            "os.makedirs('calibration', exist_ok=True)\n"
            f"open('calibration/directions.npz','wb').write(base64.b64decode('{b64}'))\n"
            "print('npz scritto', os.path.getsize('calibration/directions.npz'), 'byte')\n"
        )
        return self.exec(code)

    # ---- lavori lunghi ---------------------------------------------------
    def background(self, command: str, log: str = "job.log"):
        """Avvia un comando sulla VM e ritorna subito (evita il timeout 524)."""
        code = (
            "import subprocess\n"
            f"open({log!r},'w').close()\n"
            f"subprocess.Popen({command!r}, shell=True, "
            f"stdout=open({log!r},'w'), stderr=subprocess.STDOUT)\n"
            f"print('avviato:', {command!r})\n"
        )
        return self.exec(code)

    def poll(self, log: str = "job.log", tail: int = 40):
        return self.exec(
            "import os\n"
            f"p={log!r}\n"
            "t=open(p,encoding='utf-8',errors='replace').read() if os.path.exists(p) else ''\n"
            f"print(''.join(t.splitlines(True)[-{tail}:]))\n")
