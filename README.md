# Bidoo bot 🤖

Bot che gira **su GitHub** (GitHub Actions, senza tenere il PC acceso):

1. Si collega alla tua **Gmail** e cerca le email **non lette di Bidoo**.
2. Mostra l'HTML dell'email in un browser e **clicca davvero il link** dentro l'email.
3. Sulla pagina Bidoo che si apre, se serve, **fa login** (username + password + ENTRA).
4. Segna l'email come **letta** (così non la riprocessa).

---

## ⚠️ Prima di iniziare — leggi

- **Termini di servizio Bidoo**: automatizzare login e click con un bot può violare i
  termini del sito e, nel peggiore dei casi, portare alla **sospensione dell'account**.
  Decidi tu se e come usarlo: il rischio è tuo.
- **Password Gmail**: NON sta nel codice, va nei **GitHub Secrets** (o in un `.env`
  locale solo per i test, già ignorato da git). Apre tutta la tua casella email:
  va protetta.
- **Password Bidoo**: su tua richiesta è scritta come default nel codice
  (`bidoo_bot.py`), perché è un account usa-e-getta con sole monete virtuali.
  ➜ Proprio per questo **tieni il repository su "Private"**: se è pubblico, quella
  password è visibile a chiunque. Puoi comunque sovrascriverla con un Secret
  `BIDOO_PASS` senza toccare il codice.

---

## 1) Password per le app di Gmail

Gmail non accetta la password normale via IMAP. Serve una **"Password per le app"**:

1. Attiva la **verifica in due passaggi** sul tuo account Google.
2. Vai su **myaccount.google.com → Sicurezza → Password per le app**.
3. Crea una password per le app (16 caratteri). Questa è `GMAIL_APP_PASSWORD`.
4. Verifica che l'**IMAP sia attivo**: Gmail → Impostazioni → Inoltro e POP/IMAP → *Attiva IMAP*.

> `GMAIL_USER` è il tuo indirizzo completo, es. `tuonome@gmail.com`.

---

## 2) Imposta i Secrets su GitHub

Nel repo, vai su **Settings → Secrets and variables → Actions → New repository secret**
e crea **solo questi due** secret (Gmail):

| Nome                  | Valore                               |
|-----------------------|--------------------------------------|
| `GMAIL_USER`          | il tuo indirizzo Gmail               |
| `GMAIL_APP_PASSWORD`  | la password per le app (16 caratteri)|

Fatto questo, le credenziali Gmail restano cifrate su GitHub e **non compaiono nei log**.

> `BIDOO_USER` e `BIDOO_PASS` sono già impostati come default nel codice, quindi
> **non devi configurarli**. Se un giorno cambi la password Bidoo, puoi aggiornarla
> aggiungendo i secret `BIDOO_USER` / `BIDOO_PASS` (sovrascrivono il default) oppure
> modificando le due righe in `bidoo_bot.py`.

---

## 3) Come si avvia

- **A mano**: tab **Actions → Bidoo bot → Run workflow**.
- **In automatico**: parte da solo alle **19:35 ora italiana** (vedi il cron in
  `.github/workflows/bidoo.yml`).
  Il cron di GitHub è in **UTC** e può ritardare di qualche minuto — non è preciso
  al secondo. Le due righe `cron` coprono ora legale e ora solare; puoi modificarle
  o cancellarne una.

Dopo ogni esecuzione, nella pagina del run trovi gli **screenshot** (artifact
`screenshots`): utili per vedere cosa ha fatto il bot (atterraggio sul link, login).

---

## 4) Provarlo prima sul tuo PC (consigliato)

```bash
# 1. installa le dipendenze
pip install -r requirements.txt
python -m playwright install chromium

# 2. crea il file .env (NON verrà caricato su git)
cp .env.example .env
#    poi apri .env e scrivi le tue credenziali

# 3. prova "a vuoto": non clicca e non fa login, stampa solo cosa farebbe
DRY_RUN=true python bidoo_bot.py

# 4. prova reale vedendo il browser aprirsi
HEADLESS=false python bidoo_bot.py
```

---

## 5) Opzioni (variabili d'ambiente / Secrets facoltativi)

| Variabile    | Default        | Cosa fa                                                        |
|--------------|----------------|----------------------------------------------------------------|
| `BIDOO_FROM` | `bidoo`        | Sottostringa cercata nel mittente per riconoscere le email.    |
| `MAX_EMAILS` | `5`            | Quante email al massimo processare per esecuzione.             |
| `MARK_SEEN`  | `true`         | Segna le email come lette dopo averle gestite.                 |
| `CLICK_ALL`  | `false`        | `true` = clicca tutti i link Bidoo; `false` = solo il primo.   |
| `DRY_RUN`    | `false`        | `true` = non clicca/non fa login, stampa solo cosa farebbe.    |
| `HEADLESS`   | `true`         | `false` in locale per vedere il browser (su Actions resta `true`). |
| `IMAP_FOLDER`| `INBOX`        | Cartella da cui leggere.                                       |

Su GitHub puoi impostarle come **Variables** (Settings → Variables), non come Secrets.

---

## 6) Se qualcosa non funziona

- **Login IMAP fallito** → stai usando la password normale invece della *password per
  le app*, oppure l'IMAP è disattivato su Gmail.
- **Non trova email** → controlla `BIDOO_FROM` (il mittente reale di Bidoo) e che le
  email siano **non lette**.
- **Il login su Bidoo non va** → guarda lo screenshot `after-login` / `login-*`.
  Il sito potrebbe aver cambiato il form: i selettori nel codice sono generici
  (campo password, campo username visibile, bottone con testo *ENTRA*) e si possono
  ritoccare in `bidoo_bot.py`.

---

## File del progetto

```
bidoo_bot.py                 # lo script del bot
requirements.txt             # dipendenze (Playwright)
.github/workflows/bidoo.yml  # esecuzione automatica su GitHub Actions
.env.example                 # modello per i test in locale (copialo in .env)
.gitignore                   # esclude .env e screenshots da git
```
