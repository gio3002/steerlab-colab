# GIT — guida per chi non l'ha mai usato

Questa guida è per te, non per un LLM. Tutto si fa con 6 comandi. Se qualcosa non
torna, l'errore è quasi sempre nel punto 4 (autenticazione).

---

## 1. Installa git (una volta sola)

Windows: scarica <https://git-scm.com/download/win>, installa con le opzioni di
default. Verifica aprendo il terminale:

```bash
git --version
```

## 2. Presentati a git (una volta sola)

```bash
git config --global user.name "Il Tuo Nome"
git config --global user.email "tua@email.com"
```

## 3. Crea il repository in locale

La cartella `steerlab-colab` è già pronta. Da dentro quella cartella:

```bash
cd "C:/Users/gio30/Desktop/NN research/project 3/steerlab-colab"
git init
git add .
git commit -m "prima versione: assi, calibrazione, chat con steering"
```

`git init` crea la cartella nascosta `.git` (la memoria del progetto).
`git add .` prepara tutti i file. `git commit` li salva con un messaggio.

## 4. Crea il repository su GitHub (privato)

1. Vai su <https://github.com/new>.
2. Nome: `steerlab-colab`. **Private** selezionato.
3. NON spuntare "Add a README" (lo abbiamo già).
4. Crea. GitHub ti mostra un indirizzo tipo
   `https://github.com/TUONOME/steerlab-colab.git`.

Collega e carica:

```bash
git remote add origin https://github.com/TUONOME/steerlab-colab.git
git branch -M main
git push -u origin main
```

Al `push` ti chiede username e password. **La password normale non funziona più**:
serve un *token*.

### Come si crea il token (PAT)

1. GitHub → foto profilo → **Settings** → **Developer settings**
   (in fondo) → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Nome: `colab-steerlab`. Scadenza: 90 giorni.
3. **Repository access**: *Only select repositories* → `steerlab-colab`.
4. **Permissions** → *Repository permissions* → **Contents: Read and write**.
5. Genera e **copia subito** il token (inizia con `github_pat_...`): non lo rivedrai più.

Al posto della password incolla il token. Per non riscriverlo ogni volta:

```bash
git config --global credential.helper manager
```

## 5. Salvare le modifiche (il comando che userai sempre)

```bash
git status                 # cosa è cambiato
git add .
git commit -m "descrizione di cosa hai cambiato"
git push
```

## 6. Clonare su Colab

Nel notebook c'è già la cella. In sintesi:

```python
from google.colab import userdata
token = userdata.get('GH_TOKEN')          # il PAT salvato nei Secrets
!git clone https://{token}@github.com/TUONOME/steerlab-colab.git
```

Se il repo è privato e il token manca, il clone chiede password e fallisce:
è l'errore più comune.

---

## Errori frequenti

| messaggio | causa | rimedio |
|---|---|---|
| `Authentication failed` | password al posto del token | usa il PAT |
| `remote origin already exists` | hai già collegato un remote | `git remote set-url origin <url>` |
| `failed to push some refs` | il remoto ha commit che non hai | `git pull --rebase` poi `git push` |
| `src refspec main does not match any` | non hai ancora fatto commit | `git add . && git commit -m "..."` |
| `Support for password authentication was removed` | password normale | PAT, come sopra |
| il notebook non trova il repo | token assente nei Secrets | aggiungi `GH_TOKEN` nei Secrets di Colab |

## Cosa NON mettere nel repo

`.gitignore` esclude già cache, `__pycache__`, modelli e log. Non committare mai:
token, `.env`, pesi del modello (a meno di usare Git LFS).
