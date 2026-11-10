# Condivisione del progetto con altri (permessi reali)

## Requisito

> «Giro un link ai miei amici; loro collegano WorkBuddy con il proprio account
> in una nuova chat alla cartella GitHub e possono non solo eseguire ma anche
> andare avanti col progetto, cambiare il codice. Ma solo chi ha il permesso.»

## La risposta breve

**Sì, e non serve nessun plugin.** I permessi li gestisce GitHub, non WorkBuddy.
WorkBuddy lavora su una **cartella locale**: se quella cartella è un clone del
repo, l'agente legge, modifica, fa commit e push esattamente come faresti tu.
Non esiste (nel catalogo connettori di questa sessione) un connettore GitHub da
installare, e non serve: Git è già sufficiente.

Il repo **deve essere privato**: è l'unico modo per restringere anche la lettura.
Su un repo pubblico chiunque può leggere e fare fork (ma **non** pushare: GitHub
limita la scrittura ai collaboratori anche nei repo pubblici).

## Impostazione, una volta sola (titolare)

1. GitHub → repo → **Settings → General → Danger zone → Change visibility →
   Make private**. Conferma digitando `gio3002/steerlab-colab`.
2. GitHub → repo → **Settings → Collaborators** (a sinistra) → **Add people** →
   aggiungi l'username o l'email GitHub di ogni amico → ruolo **Write**
   (`Admin` solo a chi deve poter cambiare i permessi).
3. L'amico riceve un invito via email e deve **accettarlo**.

Da quel momento:

| Chi | Legge | Pusha codice | Cambia permessi |
|---|---|---|---|
| Titolare | sì | sì | sì |
| Collaboratore `Write` | sì | sì | no |
| Chiunque altro | **404: non vede nemmeno che esiste** | no | no |

## Cosa fa l'amico (una volta sola)

1. Si crea un account GitHub (email + password; GitHub non ha il login Google).
2. Installa **GitHub Desktop** (`desktop.github.com`), fa login col **proprio**
   account.
3. **File → Clone repository → URL** → `https://github.com/gio3002/steerlab-colab.git`
   → sceglie una cartella sul suo PC. Funziona solo se l'invito è stato accettato.
4. Apre **WorkBuddy in una nuova chat con QUELLA cartella come workspace**.
5. Da lì l'agente può leggere `builder.txt`, `EQUALIZER_RESEARCH.md`, modificare
   `engine.py` / `static/*`, fare commit e push. GitHub verifica l'identità a ogni
   push: se non è collaboratore, riceve `403`.

Non c'è bisogno di passare token o password all'agente: GitHub Desktop conserva
le credenziali dell'amico in modo sicuro sul suo PC.

## Separazione importante: repo ≠ app in esecuzione

Il repo è il **codice**. L'app che gira su Colab è un **processo separato** con un
link `trycloudflare.com`. Un push di un amico **non modifica** l'app già in
esecuzione. Per applicare le modifiche ci sono tre strade:

- `/api/admin/deploy` (chiave di controllo): l'agente installa il codice a caldo,
  senza riavviare e senza cambiare link;
- riavviare il notebook Colab (la cella 1 fa `git pull`);
- aprire una nuova sessione Colab.

Questo è voluto: evita che un push rompa l'app mentre qualcuno la sta usando.

## Due conseguenze del rendere privato il repo

1. **Il notebook non si apre più con l'URL pubblico**
   `colab.research.google.com/github/gio3002/steerlab-colab/blob/main/colab.ipynb`.
   Soluzioni: tenere il `colab.ipynb` già caricato in Colab, oppure usare
   **File → Open notebook → GitHub** (Colab chiede l'autorizzazione al proprio
   account GitHub), oppire scaricare il file dal repo e farne l'upload.
2. **Il clone dentro il notebook richiede il token** `GH_TOKEN` nei Secrets, con
   permesso `Contents: Read`. Era già previsto: è il motivo per cui il token è
   stato creato. Se il repo resta pubblico, il token è facoltativo.

## L'accesso all'APP è un'altra cosa

Il link `trycloudflare.com` è pubblico per chiunque lo possieda, **indipendentemente
dal repo**. I permessi GitHub proteggono il codice, non l'interfaccia. Se serve
limitare anche l'ingresso all'app, si aggiunge un codice di accesso lato server
(`APP_ACCESS_CODE` in `config.json`, verificato su ogni richiesta `/api/*` e sulla
pagina). Non è ancora implementato: va chiesto esplicitamente.

## Sicurezza del canale di amministrazione

`/api/admin/deploy` e `/api/admin/exec` sono protetti da `DEPLOY_KEY`, generata
a ogni avvio e **non presente nel repo**. Chi non ce l'ha riceve `403`. Il
titolare può disattivare tutto con `STEERLAB_ADMIN=0`. Questa chiave va
condivisa solo con chi deve poter modificare il codice *in esecuzione* — non con
tutti i collaboratori, se non si vuole dare loro il controllo della VM Colab.
