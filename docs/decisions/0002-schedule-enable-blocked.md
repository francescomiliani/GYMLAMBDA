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

Dal 2026-11-12 la schedule punta al guard con retry 0. Il 2026-11-18, con
autorizzazione esplicita, è stata accesa: `cron(0 0 ? * MON *)` in `Europe/Rome`,
target la versione 1 del guard, retry Lambda 0, età massima 60 secondi. Un
lunedì a mezzanotte prenota le 07:00 di lunedì, mercoledì e venerdì di quella
settimana. `/enable` dal bot resta bloccato.

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
