---
title: Telegram bot on AWS
type: architecture
status: canonical
owner: gymbooking
last-verified: 2026-11-12
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
  - scripts/build_bot_package.py
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
| Lambda guardia (`scheduled_guard`) | Distribuita ma non e' il target della schedule. At-most-once per occorrenza. |
| DynamoDB | Una tabella provisioned 1 RCU / 1 WCU, TTL su `purge_at`. Conferme, deduplica, stato dei job. |
| SSM Parameter Store | Quattro `SecureString` Standard `/gymbot/*`, chiave AWS managed `alias/aws/ssm`, lette a runtime da `ssm_config.get_secret`. |
| CloudWatch Logs | Un log group per funzione, retention 7 giorni. |
| S3 | Bucket creato da SAM (`--resolve-s3`) solo per i pacchetti di deploy. |
| IAM | Ruoli dedicati. `ssm:GetParameter` sugli ARN esatti, DynamoDB limitato alla tabella. |

Risorse preesistenti, non gestite dallo stack: Lambda `GymBooking-personale`
(legacy, non idempotente) e schedule `gym-booking-mwf-0700` (`DISABLED`, fino a 185
retry di consegna dello Scheduler).

## Invarianti

- Nessuna mutazione senza conferma persistente (token opaco, TTL). Il consumo e
  il claim del job sono condizionali: una conferma produce al massimo un job.
- Un esito incerto non viene mai ritentato in automatico. Si mostra "in corso o
  esito incerto" e si verificano gli impegni sul portale.
- I segreti non stanno in env var in chiaro ne' nel template. Se SSM e'
  configurato e fallisce, non c'e' fallback silenzioso.
- Autorizzazione e notifiche usano lo stesso ID Telegram.
- I servizi (`isService`) non sono prenotabili dal bot.
- I callback usano ID impacchettati (`gs:`/`gw:`) per restare entro 64 byte.

## Schedule

Il bot puo' consultare e disattivare la schedule. Giorni e ora si modificano
solo a schedule `DISABLED`. `/enable` e' bloccato: la schedule ha 185 retry e la
Lambda legacy non e' idempotente. `BotScheduler.enable()` esiste ma non e'
collegato. Vedi `docs/decisions/0002-schedule-enable-blocked.md`.

## Limiti noti

- Concorrenza riservata del webhook non disponibile (limite account): due
  modifiche contemporanee alla schedule non sono serializzate.
- La deduplicazione degli update vale finche' i record non scadono per TTL.
- Il bucket S3 di SAM resta dopo la cancellazione dello stack.
- Function URL pubblica: richieste non autorizzate consumano comunque
  invocazioni (vedi `docs/reference/aws-costs.md`).
