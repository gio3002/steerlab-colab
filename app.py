"""
app.py — avvio di STEERLAB COLAB.

  python app.py                 # server locale su http://127.0.0.1:7860
  python app.py --tunnel        # + URL pubblico temporaneo (cloudflared)
  python app.py --port 8000

Il tunnel serve per condividere la sessione Colab con altre persone: chi ha il
link entra, e alla chiusura del runtime il link smette di funzionare.
Nessun dato viene salvato: la cronologia vive solo nella pagina del browser.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def find_cloudflared() -> str | None:
    p = shutil.which("cloudflared")
    if p:
        return p
    for c in (os.path.join(HERE, "cloudflared"),
              "/usr/local/bin/cloudflared", "/usr/bin/cloudflared"):
        if os.path.exists(c):
            return c
    return None


def start_tunnel(port: int):
    exe = find_cloudflared()
    if not exe:
        print("\n[!] cloudflared non trovato. Su Colab:")
        print("    !wget -q https://github.com/cloudflare/cloudflared/releases/latest/"
              "download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared")
        return None

    # Con un tunnel NOMINATO (Cloudflare Zero Trust, gratuito) il link è FISSO:
    # impostare il Secret Colab CLOUDFLARE_TUNNEL_TOKEN e public_url in
    # config.json. Senza, si usa il quick tunnel (URL casuale che cambia ad ogni
    # riavvio della sessione Colab).
    named = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip()
    if named:
        cmd = [exe, "tunnel", "--no-autoupdate", "run", "--token", named]
        try:
            with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
                fixed = json.load(f).get("public_url", "")
        except OSError:
            fixed = ""
        if fixed:
            print("\n" + "=" * 72)
            print("  LINK PUBBLICO FISSO:", fixed)
            print("=" * 72 + "\n", flush=True)
    else:
        cmd = [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
        fixed = ""

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    url_holder = {"url": fixed or None}

    def reader():
        pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        for line in proc.stdout:
            m = pat.search(line)
            if m and not url_holder["url"]:
                url_holder["url"] = m.group(0)
                print("\n" + "=" * 72)
                print("  LINK PUBBLICO (condividilo con chi vuoi):")
                print("  " + url_holder["url"])
                print("=" * 72 + "\n", flush=True)
    threading.Thread(target=reader, daemon=True).start()

    def watchdog():
        nonlocal proc
        while True:
            time.sleep(5)
            if proc.poll() is not None:
                print("[tunnel] cloudflared caduto, riavvio...", flush=True)
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1)
                    if not fixed:
                        threading.Thread(target=lambda: [None for _ in proc.stdout],
                                         daemon=True).start()
                except Exception as e:
                    print("[tunnel] riavvio fallito:", e, flush=True)
            time.sleep(10)

    threading.Thread(target=watchdog, daemon=True).start()
    for _ in range(40):
        if url_holder["url"]:
            break
        time.sleep(0.5)
    return proc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--tunnel", action="store_true")
    a = ap.parse_args()

    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    port = a.port or int(cfg.get("port", 7860))

    print("=" * 72)
    print("  STEERLAB COLAB")
    print(f"  modello: {cfg['model_id']}   4bit: {cfg.get('load_4bit')}")
    print(f"  locale:  http://127.0.0.1:{port}")
    print("  Il modello viene caricato alla prima richiesta (o da /api/model/load).")
    print("=" * 72, flush=True)

    tun = start_tunnel(port) if a.tunnel else None

    import uvicorn
    try:
        uvicorn.run("server:app", host=a.host, port=port,
                    log_level="warning", access_log=False)
    finally:
        if tun:
            tun.terminate()


if __name__ == "__main__":
    main()
