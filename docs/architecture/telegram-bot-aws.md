---
title: Telegram bot on AWS
type: architecture
status: canonical
owner: gymbooking
last-verified: 2026-11-18
relevant-code:
  - bot-template.yaml
  - lambda_function/telegram_bot.py
  - lambda_function/telegram_worker.py
  - lambda_function/bot_store.py
  - lambda_function/bot_scheduler.py
  - lambda_function/scheduled_guard.py
  - lambda_function/ssm_config.py
  - lambda_function/app.py
  - lambda_function/sportrick.py
  - lambda_function/calendar_ics.py
  - lambda_function/google_calendar.py
  - scripts/build_bot_package.py
  - scripts/google_calendar_auth.py
---

# Telegram bot on AWS

Bot privato per consultare e gestire le prenotazioni Sportrick e controllare la
schedule. Stack CloudFormation `gymbot-telegram`, account `163876584948`,
regione `eu-north-1`, profilo CLI `aws-personale`.

## Architettura implementata

![Architettura locale del bot GymBooking](architecture.drawio.png)

[Apri il diagramma modificabile](architecture.drawio). Il diagramma descrive il
codice e il template nel repository, non certifica lo stato attuale delle risorse
AWS. Le informazioni sulle risorse distribuite sotto sono uno snapshot da
verificare prima di operare.

- Telegram invia gli aggiornamenti alla Function URL del webhook. Il codice
  verifica secret, metodo, utente e chat; le letture possono essere inoltrate al
  worker asincrono.
- Per una mutazione il webhook registra una conferma in DynamoDB; dopo la
  conferma accoda il job. Il worker usa il client Sportrick condiviso, registra
  l'esito e invia la notifica. Un esito incerto non autorizza nuovi tentativi
  automatici.
- La guardia schedulata implementa un claim persistente dell'occorrenza prima
  di avviare il flusso legacy. È separata dal webhook: il collegamento effettivo
  alla schedule deve essere verificato e lo snapshot sottostante lo indica come
  non attivo.
- Il bot legge e modifica la schedule nei limiti descritti sotto. SSM fornisce
  i segreti a runtime; le notifiche legacy possono usare SNS o Telegram.

## Risorse

| Risorsa | Ruolo |
|---|---|
| Lambda webhook (`telegram_bot`) | Function URL pubblica `AuthType: NONE`. Verifica secret e utente autorizzato, registra conferme, accoda lavoro. Risponde 403 senza secret, 405 su GET. |
| Lambda worker (`telegram_worker`) | Esegue letture e mutazioni in modo asincrono, notifica l'esito. Timeout 180 s, retry asincroni 0. |
| Lambda guardia (`scheduled_guard`) | Target della schedule, versione 1. At-most-once per occorrenza. |
| DynamoDB | Una tabella provisioned 1 RCU / 1 WCU, TTL su `purge_at`. Conferme, deduplica, stato dei job. |
| SSM Parameter Store | Cinque `SecureString` Standard `/gymbot/*`, chiave AWS managed `alias/aws/ssm`, lette a runtime da `ssm_config.get_secret`. |
| CloudWatch Logs | Un log group per funzione, retention 7 giorni. |
| S3 | Bucket creato da SAM (`--resolve-s3`) solo per i pacchetti di deploy. |
| IAM | Ruoli dedicati. `ssm:GetParameter` sugli ARN esatti, DynamoDB limitato alla tabella. |

Risorse preesistenti, non gestite dallo stack: Lambda `GymBooking-personale`
(legacy, non idempotente) e schedule `gym-booking-mwf-0700` (`ENABLED` dal
2026-11-18, retry di consegna 0, target la versione 1 del guard).

## Invarianti

- Nessuna mutazione senza conferma persistente (token opaco, TTL). Il consumo e
  il claim del job sono condizionali: una conferma produce al massimo un job.
- Un esito incerto non viene mai ritentato in automatico. Si mostra "in corso o
  esito incerto" e si verificano gli impegni sul portale.
- I segreti non stanno in env var in chiaro ne' nel template. Se SSM e'
  configurato e fallisce, non c'e' fallback silenzioso.
- Autorizzazione e notifiche usano lo stesso ID Telegram.
- I servizi (`isService`) non sono prenotabili dal bot.
- Dopo l'autenticazione ogni update riceve HTTP 200, anche in caso di errore (l'utente vede un
  avviso). Un 4xx/5xx farebbe ritentare a Telegram lo stesso update all'infinito e bloccherebbe la
  coda: e' la causa del bot rimasto muto il 2026-11-18. 4xx restano solo per richieste non
  autenticate o malformate (403, 405, 413, 400 su corpo non valido). Un errore transitorio non
  viene ritentato da Telegram: l'utente ripreme il bottone, la conferma resta valida finche' non scade.
- I callback usano ID impacchettati (`gs:`/`gw:`) per restare entro 64 byte.

## Preferenze e fallback

Dati nella stessa tabella DynamoDB (`state#prefs`, `state#skip#DATA`, `state#last_run`).

- `/prefs` mostra, `/prefs MON 07:00,12:30` imposta (con conferma), `/prefs MON off` toglie il giorno.
  Gli orari sono quelli della sala attrezzi (`timetable.GYM_SLOTS`) in ordine di priorita': prima
  le 07:00, poi le 12:30. Il giorno e' quello della lezione. Il lunedì a mezzanotte
  prenota tre date di quella settimana; gli altri trigger prenotano lo stesso giorno
  sette giorni dopo.
- `scheduled_guard` legge le preferenze prima del claim e chiama `app.book` con la lista ordinata
  (`fallback_times`): vale il primo slot con `Notes == 'Prenota'`, senza POST su slot non prenotabili.
  Giorno senza preferenze (con preferenze presenti) oppure data in `/skip`: nessuna prenotazione e
  nessun claim. Nessuna preferenza salvata: orario predefinito `BOT_BOOKING_TIME` come prima.
  Un errore di lettura dello stato interrompe senza prenotare.
- `/skip YYYY-MM-DD` salta la lezione di quella data, `/skip off YYYY-MM-DD` la riattiva.
- `/status` riassume schedule e prossima esecuzione, preferenze, ultima esecuzione, date saltate e
  `getWebhookInfo` (update in coda, ultimo errore). Ogni sezione fallisce in modo indipendente.
- Il corso (sala corsi) e' previsto nel modello (`activity` per voce) ma oggi le preferenze validano
  solo la sala attrezzi.

Sezione verificata sul codice e sui test il 2026-11-18. Lo stesso giorno lo stack `gymbot-telegram` è in `UPDATE_COMPLETE` e la schedule `gym-booking-mwf-0700` è `ENABLED` sulla versione 1 del guard.

## Calendario

`/calendar` legge gli impegni e scrive su Google Calendar (`primary`) le lezioni ancora aperte.
Promemoria popup a 12 ore e a 1 ora, fuso `Europe/Rome`. Servizi e liste d'attesa restano fuori.
L'id dell'evento sta in `state#gcal#<id>` e la stessa `iCalUID` (`gymbooking-<id>@gymbooking`) evita
un secondo evento se skill e Lambda scrivono entrambi. Senza segreto, o se la chiamata fallisce,
il bot manda solo allora un file `.ics`: un import ripetuto può duplicare, e cancellare sul
portale non toglie un evento importato da file. La cancellazione confermata cancella l'evento
Google quando l'id è noto.

Il segreto è un JSON `client_id`, `client_secret`, `refresh_token`. Dal deploy del 2026-11-18
worker e guard lo leggono da SSM (`SSM_GOOGLE_CALENDAR`, parametro `/gymbot/google-calendar`,
SecureString creato lo stesso giorno). Il webhook non ha quella variabile: in produzione la
scrittura passa dal worker.
In locale skill e `local_bot` usano `~/.config/gymbooking/google-calendar.json` (permessi `0600`).
Il client OAuth desktop si chiama GymBooking, scope `https://www.googleapis.com/auth/calendar.events`,
progetto Cloud «My First Project». Il consenso una tantum è `scripts/google_calendar_auth.py`.
Manca il segreto: la prenotazione resta confermata e si usa il file `.ics`.

L'app OAuth è esterna, stato In production dal 2026-11-18. Homepage
`https://francescomiliani.github.io/gymbooking/`, privacy policy
`https://francescomiliani.github.io/gymbooking/privacy.html`, dominio autorizzato
`francescomiliani.github.io`. Il limite dei 7 giorni del refresh token vale solo in Testing.
Google segnala comunque che lo scope `calendar.events` va sottoposto a verifica: la review non
è stata inviata.

Sezione verificata sul codice, sui test e sul parametro SSM il 2026-11-18.

## Schedule

Dal 2026-11-18 `gym-booking-mwf-0700` è `ENABLED`. Espressione `cron(0 0 ? * MON *)`,
fuso `Europe/Rome`: il lunedì a mezzanotte prenota la sala attrezzi delle 07:00 di
lunedì, mercoledì e venerdì di quella stessa settimana. Il portale non accetta
prenotazioni a nove giorni, quindi un solo trigger settimanale non può prendere
il lunedì, il mercoledì e il venerdì della settimana dopo.

Il target è la versione 1 di `scheduled_guard` (non `$LATEST`). Retry dello
scheduler e della Lambda: 0. Età massima dell'evento: 60 secondi. Il bot puo'
consultare e disattivare la schedule. `/enable` dal bot resta bloccato: vedi
`docs/decisions/0002-schedule-enable-blocked.md`. `BotScheduler.enable()` esiste
ma non e' collegato.

## Limiti noti

- Concorrenza riservata del webhook non disponibile (limite account): due
  modifiche contemporanee alla schedule non sono serializzate.
- La deduplicazione degli update vale finche' i record non scadono per TTL.
- Il bucket S3 di SAM resta dopo la cancellazione dello stack.
- Function URL pubblica: richieste non autorizzate consumano comunque
  invocazioni (vedi `docs/reference/aws-costs.md`).
