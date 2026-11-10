/*
 * colab_boot.cjs — bootstrap automatico della GPU Colab.
 *
 * Avvio:  node boot_daemon.cjs   (dalla radice del repo)
 * Per questa macchina: STEERLAB_ROOT='.../project 3' e il notebook
 * va indicato con NOTEBOOK_PATH / CONTROL_PATH se il repo e' in una sottocartella.
 *
 * Usa un profilo Chrome PERSISTENTE dentro il workspace (_work/chrome_profile),
 * cosi' il login Google va fatto UNA SOLA VOLTA e resta valido per sempre.
 * Apre una finestra visibile: se non sei loggato, entra in attesa.
 * Appena il login compare, carica START.ipynb, avvia la cella, estrae
 * LINK + CHIAVE, aggiorna steerlab-colab/control.json e lascia la finestra
 * aperta per tenere viva la sessione Colab.
 *
 * Stato leggibile in _work/colab_boot_status.json.
 */
const PW = 'C:/Users/gio30/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright';
const { chromium } = require(PW);
const fs = require('fs');
const path = require('path');

// Per l'uso locale: STEERLAB_ROOT=<radice del progetto>.
// Se il file sta nella radice del repo, __dirname è già il workspace.
const ROOT = process.env.STEERLAB_ROOT || __dirname;
const UD = path.join(ROOT, '_work', 'chrome_profile');
const NOTEBOOK = path.join(ROOT, 'START.ipynb');
const CONTROL = path.join(ROOT, 'control.json');
const STATUS = path.join(ROOT, '_work', 'colab_boot_status.json');
const NBPATH = process.env.NOTEBOOK_PATH || NOTEBOOK;
const CTL = process.env.CONTROL_PATH || CONTROL;
const crypto = require('crypto');
const RUNTIME_NB = path.join(ROOT, '_work', 'START.runtime.ipynb');
fs.mkdirSync(path.dirname(RUNTIME_NB), { recursive: true });

// La chiave NON sta nel repo: ogni sessione ha la propria. Se manca, la crea.
let ctl0 = {};
try { ctl0 = JSON.parse(fs.readFileSync(CTL, 'utf8')); } catch (e) {}
const SESSION_KEY = ctl0.deploy_key || crypto.randomBytes(24).toString('hex');
ctl0.repo = ctl0.repo || 'https://github.com/gio3002/steerlab-colab';
ctl0.deploy_key = SESSION_KEY;
fs.writeFileSync(CTL, JSON.stringify(ctl0, null, 1));
status('chiave_sessione', { key: SESSION_KEY.slice(0, 8) + '...' });

// Il notebook nel repo ha un placeholder: si carica una copia con la chiave.
const nbRaw = JSON.parse(fs.readFileSync(NBPATH, 'utf8'));
for (const c of nbRaw.cells) {
  if (c.cell_type === 'code')
    c.source = c.source.join('').replace(/__DEPLOY_KEY__/g, SESSION_KEY).split(/(?<=\n)/);
}
fs.writeFileSync(RUNTIME_NB, JSON.stringify(nbRaw, null, 1));
const UPLOAD_NB = RUNTIME_NB;
const SHOT = path.join(ROOT, '_work', 'colab_boot.png');

function status(phase, extra) {
  fs.writeFileSync(STATUS, JSON.stringify({
    phase, at: new Date().toISOString(), ...(extra || {})
  }, null, 1));
  console.log('[' + phase + '] ' + JSON.stringify(extra || {}));
}

(async () => {
  fs.mkdirSync(UD, { recursive: true });
  status('avvio_browser');
  const ctx = await chromium.launchPersistentContext(UD, {
    headless: false,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    // Google rifiuta il login nei browser con --enable-automation.
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--no-first-run', '--no-default-browser-check',
           '--no-proxy-server', '--proxy-bypass-list=*',
           '--disable-blink-features=AutomationControlled',
           '--disable-features=ChromeWhatsNewUI'],
    viewport: { width: 1320, height: 900 }
  });
  const p = ctx.pages()[0] || await ctx.newPage();
  p.setDefaultTimeout(20000);

  // Chrome, con un profilo nuovo, apre da solo una scheda di "Sign in to Chrome"
  // (accounts.google.com/.../chrome/sync). Non c'entra con Colab e Google la
  // rifiuta: chiudiamola e restiamo solo sulla pagina web di Colab.
  await p.goto('https://colab.research.google.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  for (const t of ctx.pages()) {
    if (t !== p && /accounts\.google\.com/.test(t.url())) {
      await t.close().catch(() => {});
    }
  }
  await p.bringToFront();

  // ---- attesa login (max 45 min) -----------------------------------------
  let logged = false;
  for (let i = 0; i < 900; i++) {
    await p.waitForTimeout(3000);
    const t = await p.evaluate(() => document.body.innerText.slice(0, 2000)).catch(() => '');
    if (!/sign in/i.test(t) && /notebook|recent|welcome|new notebook/i.test(t)) {
      logged = true; break;
    }
    if (i === 0) status('attendo_login', { msg: 'fai login Google nella finestra Chrome' });
  }
  if (!logged) { status('timeout_login'); await p.screenshot({ path: SHOT }); return; }
  status('loggato');

  // ---- cambia runtime in T4 GPU (best effort) ----------------------------
  try {
    await p.getByText('Runtime', { exact: true }).first().click({ timeout: 8000 });
    await p.getByText(/Change runtime type/i).first().click({ timeout: 8000 });
    await p.waitForTimeout(1200);
    const gpu = p.getByText('T4 GPU', { exact: true }).first();
    if (await gpu.count()) await gpu.click({ timeout: 5000 });
    await p.getByText('Save', { exact: true }).first().click({ timeout: 8000 });
    status('t4_selezionato');
  } catch (e) {
    status('t4_manuale', { msg: 'menu non automatizzabile, provo comunque', err: String(e).slice(0, 120) });
  }

  // ---- carica START.ipynb -------------------------------------------------
  let uploaded = false;
  try {
    const fc = p.waitForEvent('filechooser', { timeout: 15000 });
    await p.getByText('File', { exact: true }).first().click({ timeout: 8000 });
    await p.getByText(/Upload notebook/i).first().click({ timeout: 8000 });
    const chooser = await fc;
    await chooser.setFiles(UPLOAD_NB);
    uploaded = true;
    status('notebook_caricato');
  } catch (e) {
    status('upload_fallito', { err: String(e).slice(0, 180) });
  }

  if (uploaded) {
    await p.waitForTimeout(6000);
    await p.keyboard.press('Control+F9');           // Run all (una cella sola)
    status('cella_avviata');
  } else {
    // fallback: inietta il codice della cella nel primo notebook disponibile
    const nb = JSON.parse(fs.readFileSync(UPLOAD_NB, 'utf8'));
    const code = nb.cells.filter(c => c.cell_type === 'code')
                         .map(c => c.source.join('')).join('\n');
    const one = 'import base64; exec(base64.b64decode("' +
                Buffer.from(code, 'utf8').toString('base64') + '").decode())';
    await p.keyboard.press('Escape');
    await p.waitForTimeout(500);
    await p.keyboard.insertText(one);
    await p.keyboard.press('Control+Enter');
    status('codice_iniettato');
  }

  // ---- attendi PRONTO ed estrai LINK + CHIAVE ----------------------------
  let link = null, key = null;
  for (let i = 0; i < 300; i++) {          // fino a 25 min (ricalibrazione)
    await p.waitForTimeout(5000);
    const t = await p.evaluate(() => document.body.innerText).catch(() => '');
    const ml = t.match(/LINK\s*:\s*(https:\/\/[a-z0-9-]+\.trycloudflare\.com)/);
    const mk = t.match(/CHIAVE\s*:\s*([0-9a-f]{32,})/);
    if (ml) link = ml[1];
    if (mk) key = mk[1];
    if (link && key) break;
    if (i % 12 === 0) status('attendo_pronto', { secondi: i * 5, link: link || null });
  }

  if (link && key) {
    let ctl = {};
    try { ctl = JSON.parse(fs.readFileSync(CTL, 'utf8')); } catch (e) {}
    ctl.repo = ctl.repo || 'https://github.com/gio3002/steerlab-colab';
    ctl.app_url = link; ctl.deploy_key = key;
    ctl.started = new Date().toISOString();
    fs.writeFileSync(CTL, JSON.stringify(ctl, null, 1));
    status('pronto', { link, key: key.slice(0, 8) + '...' });
  } else {
    status('pronto_non_trovato', { link, key: key ? key.slice(0, 8) : null });
  }
  await p.screenshot({ path: SHOT });

  // ---- tieni viva la sessione --------------------------------------------
  for (;;) {
    await p.waitForTimeout(20000);
    try { await p.evaluate(() => { window.dispatchEvent(new Event('focus')); }); } catch (e) {}
  }
})().catch(e => { status('errore', { err: String(e && e.stack || e).slice(0, 500) }); process.exit(1); });
