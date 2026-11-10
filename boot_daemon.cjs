/*
 * boot_daemon.cjs — avvio automatico della GPU Colab, senza chiedere click.
 *
 *   node boot_daemon.cjs
 *   STEERLAB_ROOT=... NOTEBOOK_PATH=... CONTROL_PATH=... node boot_daemon.cjs
 *
 * Usa un profilo Chrome PERSISTENTE dentro il workspace (_work/chrome_profile):
 * il login Google va fatto una sola volta e resta per sempre. Apre la home di
 * Colab, clicca direttamente "Upload notebook" (nessun menu), carica una copia
 * di START.ipynb con la chiave di sessione iniettata, seleziona T4, esegue la
 * cella, estrae LINK+CHIAVE e aggiorna control.json. Lascia la finestra aperta
 * per tenere viva la sessione. Stato: _work/colab_boot_status.json
 */
const PW = 'C:/Users/gio30/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright';
const { chromium } = require(PW);
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = process.env.STEERLAB_ROOT || __dirname;
const WORK = path.join(ROOT, '_work');
const UD = path.join(WORK, 'chrome_profile');
const NBPATH = process.env.NOTEBOOK_PATH || path.join(ROOT, 'START.ipynb');
const CTL = process.env.CONTROL_PATH || path.join(ROOT, 'control.json');
const STATUS = path.join(WORK, 'colab_boot_status.json');
const SHOT = path.join(WORK, 'colab_boot.png');
const RUNTIME_NB = path.join(WORK, 'START.runtime.ipynb');

fs.mkdirSync(WORK, { recursive: true });
function status(phase, extra) {
  fs.writeFileSync(STATUS, JSON.stringify({
    phase, at: new Date().toISOString(), ...(extra || {})
  }, null, 1));
  console.log('[' + phase + '] ' + JSON.stringify(extra || {}));
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
// Un timeout su un waitForEvent non deve mai far morire il demone.
process.on('unhandledRejection', e => console.log('[warn]', String(e).slice(0, 160)));

// ---- chiave di sessione, MAI nel repo ------------------------------------
let ctl = {};
try { ctl = JSON.parse(fs.readFileSync(CTL, 'utf8')); } catch (e) {}
const KEY = ctl.deploy_key || crypto.randomBytes(24).toString('hex');
ctl.repo = ctl.repo || 'https://github.com/gio3002/steerlab-colab';
ctl.deploy_key = KEY;
fs.writeFileSync(CTL, JSON.stringify(ctl, null, 1));

const nb = JSON.parse(fs.readFileSync(NBPATH, 'utf8'));
for (const c of nb.cells) {
  if (c.cell_type === 'code')
    c.source = c.source.join('').replace(/__DEPLOY_KEY__/g, KEY).split(/(?<=\n)/);
}
fs.writeFileSync(RUNTIME_NB, JSON.stringify(nb, null, 1));
status('chiave_sessione', { key: KEY.slice(0, 8) + '...' });

(async () => {
  const ctx = await chromium.launchPersistentContext(UD, {
    headless: false,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--no-first-run', '--no-default-browser-check',
           '--no-proxy-server', '--proxy-bypass-list=*',
           '--disable-blink-features=AutomationControlled'],
    viewport: { width: 1400, height: 1000 }
  });
  const p = ctx.pages()[0] || await ctx.newPage();
  p.setDefaultTimeout(20000);

  // Il form Google nasce in basso e su certi schermi non si raggiunge.
  // Zoom leggero + scroll in cima + card ancorata in alto, su OGNI scheda.
  const CENTER_CSS = `
    html, body { zoom: 0.82 !important; }
    body { min-height: 0 !important; }
    #initialView, .aCsJod, .wtf3ff, main, [role=main] {
      margin-top: 2vh !important; margin-bottom: 2vh !important;
    }
  `;
  async function center(pg) {
    try {
      await pg.evaluate(() => window.scrollTo(0, 0));
      await pg.addStyleTag({ content: CENTER_CSS });
    } catch (e) {}
  }
  ctx.on('page', pg => {
    pg.on('domcontentloaded', () => center(pg));
    center(pg);
  });
  status('avvio_browser');

  await p.goto('https://colab.research.google.com/',
               { waitUntil: 'domcontentloaded', timeout: 60000 });
  // Chrome apre da solo una scheda "Sign in to Chrome" che non c'entra: chiudila.
  for (const t of ctx.pages())
    if (t !== p && /accounts\.google\.com/.test(t.url())) await t.close().catch(() => {});
  await p.bringToFront();
  await center(p);

  // Scheda di accesso dedicata: il form resta in alto e centrato.
  // Dopo il login le cookie valgono anche per la scheda Colab.
  const login = await ctx.newPage();
  await login.goto(
    'https://accounts.google.com/signin/v2/identifier?hl=it&continue=' +
    encodeURIComponent('https://colab.research.google.com/'),
    { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await center(login);
  await login.bringToFront();
  status('richiesta_login', { msg: 'form centrato in alto nella scheda di accesso' });

  // ---- upload diretto, con retry se Colab chiede il login ----------------
  let uploaded = false;
  for (let attempt = 0; attempt < 60 && !uploaded; attempt++) {
    // Se un notebook e' gia' aperto (caricato dal demone O a mano dall'umano),
    // il menu "Runtime" esiste: esci dal loop e prosegui. E' la via di uscita
    // garantita, senza interpretare testi di login.
    const body = await p.evaluate(() => document.body.innerText.slice(0, 4000)).catch(() => '');
    if (/\bRuntime\b/.test(body) && /Connect|Ram|Disk|Ctrl|Edit|View|Insert/i.test(body)) {
      uploaded = true; status('notebook_aperto'); break;
    }
    // Strategia A: il pulsante apre un selettore file nativo.
    // Strategia B: apre un dialogo con <input type=file> nascosto.
    const fc = p.waitForEvent('filechooser', { timeout: 9000 }).catch(() => null);
    try {
      const btn = p.getByText(/Upload notebook|Carica notebook/i).first();
      if (await btn.count()) await btn.click({ timeout: 7000 });
    } catch (e) {}
    const chooser = await fc;
    try {
      if (chooser) {
        await chooser.setFiles(RUNTIME_NB);
        uploaded = true;
      } else {
        const inp = p.locator('input[type=file]').first();
        if (await inp.count()) {
          await inp.setInputFiles(RUNTIME_NB, { timeout: 8000 });
          uploaded = true;
        }
      }
    } catch (e) {}
    if (uploaded) { status('notebook_caricato'); break; }
    if (attempt === 0)
      status('attendo_login', { msg: 'login nella scheda di accesso, form in alto' });
    await center(login).catch(() => {});
    await login.bringToFront().catch(() => {});
    if (attempt % 4 === 3) await p.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
    await sleep(5000);
  }
  if (!uploaded) {
    status('upload_non_riuscito', { msg: 'controlla la finestra Chrome' });
    await p.screenshot({ path: SHOT });
    return;
  }

  await p.waitForTimeout(8000);

  // ---- T4 GPU (best effort; se il menu non c'e' si prosegue) -------------
  try {
    await p.getByText('Runtime', { exact: true }).first().click({ timeout: 8000 });
    await p.getByText(/Change runtime type/i).first().click({ timeout: 8000 });
    await sleep(1200);
    const gpu = p.getByText('T4 GPU', { exact: true }).first();
    if (await gpu.count()) await gpu.click({ timeout: 5000 });
    await p.getByText('Save', { exact: true }).first().click({ timeout: 8000 });
    status('t4_selezionato');
  } catch (e) {
    status('t4_da_impostare', { err: String(e).slice(0, 100) });
  }

  // ---- Connect + Run all --------------------------------------------------
  try {
    const conn = p.getByText(/^Connect$/i).first();
    if (await conn.count()) { await conn.click({ timeout: 5000 }); await sleep(5000); }
  } catch (e) {}
  await p.keyboard.press('Control+F9');
  status('cella_avviata');

  // ---- estrai LINK e CHIAVE ----------------------------------------------
  let link = null, key = null;
  for (let i = 0; i < 300; i++) {
    await sleep(5000);
    const t = await p.evaluate(() => document.body.innerText).catch(() => '');
    const ml = t.match(/LINK\s*:\s*(https:\/\/[a-z0-9-]+\.trycloudflare\.com)/);
    const mk = t.match(/CHIAVE\s*:\s*([0-9a-f]{32,})/);
    if (ml) link = ml[1];
    if (mk) key = mk[1];
    if (link && key) break;
    if (i % 12 === 0) status('attendo_pronto', { secondi: i * 5, link: link || null });
  }
  if (link && key) {
    ctl.app_url = link; ctl.deploy_key = key;
    ctl.started = new Date().toISOString();
    fs.writeFileSync(CTL, JSON.stringify(ctl, null, 1));
    status('pronto', { link, key: key.slice(0, 8) + '...' });
  } else {
    status('pronto_non_trovato', { link, key: key ? key.slice(0, 8) : null });
  }
  await p.screenshot({ path: SHOT });

  for (;;) {                       // tiene viva la sessione Colab
    await sleep(20000);
    try { await p.evaluate(() => window.dispatchEvent(new Event('focus'))); } catch (e) {}
  }
})().catch(e => {
  status('errore', { err: String(e && e.stack || e).slice(0, 600) });
  process.exit(1);
});
