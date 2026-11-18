---
title: Schedule enable is blocked from the bot
type: decision
status: canonical
owner: gymbooking
last-verified: 2026-11-18
relevant-code:
  - lambda_function/bot_scheduler.py
  - lambda_function/scheduled_guard.py
---

# Schedule enable is blocked from the bot

## Contesto

La schedule `gym-booking-mwf-0700` aveva fino a 185 retry di consegna e la Lambda
legacy `GymBooking-personale` non e' idempotente: una riattivazione puo' produrre
prenotazioni duplicate. La guardia `scheduled_guard` rende un'occorrenza
at-most-once.

Dal 2026-11-12 la schedule, sempre `DISABLED`, punta al guard con retry 0.
`/enable` resta bloccato: mancano la versione immutabile del guard, i retry
Lambda a 0 e un'autorizzazione esplicita. Nessun controllo runtime dimostra
ancora IAM, retry Lambda e codice effettivo end to end.

## Decisione

Il bot consulta e disattiva la schedule. Giorni e ora cambiano solo a schedule
`DISABLED`. `/enable` non e' esposto. `BotScheduler.enable()` resta come
validazione strutturale fail-closed, non come certificazione di sicurezza.
La riattivazione e' una procedura amministrativa verificata.

## Conseguenze

- Per riattivare servono: target sulla guardia (versione immutabile), permesso
  di invocazione per il ruolo Scheduler, payload `{schedule_arn, scheduled_time}`,
  retry Scheduler 0, età massima breve, retry Lambda 0, autorizzazione dell'utente.
- Un crash dopo il claim della guardia puo' perdere una prenotazione, mai duplicarla.
