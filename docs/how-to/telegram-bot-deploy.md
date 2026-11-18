---
title: Deploy the Telegram bot
type: how-to
status: canonical
owner: gymbooking
last-verified: 2026-11-18
relevant-code:
  - bot-template.yaml
  - scripts/build_bot_package.py
---

# Deploy the Telegram bot

Prima di distribuire: l'utente deve autorizzare esplicitamente. Non modificare la
schedule `gym-booking-mwf-0700` ne' la Lambda `GymBooking-personale`. Non
stampare token o segreti. Login CLI: `aws login --profile aws-personale`
(la sessione scade).

## 1. Segreti SSM (una sola volta)

Cinque `SecureString` Standard, creati fuori dallo stack, leggendo i valori da
file locali e non da argomenti di comando:

- `/gymbot/telegram-bot-token`
- `/gymbot/telegram-webhook-secret` (generato casualmente, non rigenerare a ogni deploy)
- `/gymbot/sportrick-username`
- `/gymbot/sportrick-password`
- `/gymbot/google-calendar` (JSON `client_id`, `client_secret`, `refresh_token`; creato il 2026-11-18). Stato OAuth e URL pubblici: sezione Calendario in `docs/architecture/telegram-bot-aws.md`.

Ruotare il webhook secret richiede di rieseguire `setWebhook`.

## 2. Pacchetto

```sh
python3 scripts/build_bot_package.py
```

Copia in `build/bot-package/` solo i file in allowlist. `credentials.json` non
entra mai: lo script fallisce se compare nell'output.

## 3. Build e deploy

`sam build` richiede Python 3.12 nel `PATH` (`uv python find 3.12`).

```sh
sam build -t bot-template.yaml
sam deploy -t .aws-sam/build/template.yaml --stack-name gymbot-telegram \
  --region eu-north-1 --profile aws-personale --resolve-s3 \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "TelegramAllowedUserId=<id>" ScheduleName=gym-booking-mwf-0700 \
  ScheduleRoleArn=arn:aws:iam::163876584948:role/GymBooking-personale-scheduler-role
```

Non impostare `ReservedConcurrentExecutions`: il limite dell'account lo rifiuta.

In un aggiornamento conserva i parametri già presenti, incluso `GoogleCalendarParameterName`.
Un valore vuoto (`SnsTopicArn`) si passa solo come `ParameterKey=SnsTopicArn,ParameterValue=`:
la forma `SnsTopicArn=` viene rifiutata. Non stampare i valori. Lo stack è stato aggiornato
il 2026-11-18 (`UPDATE_COMPLETE`). La schedule, dopo quel deploy, è stata accesa
a parte: vedi la sezione sotto. Non la spegne un aggiornamento dello stack.

## 4. Telegram

- `setWebhook` verso la Function URL (output `WebhookUrl`) con `secret_token` uguale
  al parametro SSM e `allowed_updates=["message","callback_query"]`.
- `setMyCommands`: `start`, `bookings`, `activities`, `automation`, `weekly`,
  `disable`, `orari`, `calendar`, `prefs`, `skip`, `status`, `help`. Il menu non si aggiorna da solo.
  Telegram usa lo scope piu' specifico: un elenco su `all_private_chats` o
  `all_group_chats` nasconde quello `default`. Imposta tutti e tre gli scope e
  verifica ciascuno con `getMyCommands`. Il client ha una cache: riapri la chat.
  Incidente del 2026-11-12: un vecchio elenco `/hello` su `all_private_chats`
  nascondeva i comandi del bot.

## 5. Verifica

- POST senza secret alla URL: 403. GET: 405.
- Su Telegram: `/start`, poi `/bookings`, `/automation`, `/orari` e `/status` (sola lettura). Dopo un deploy controllare con `getWebhookInfo` che `pending_update_count` sia 0 e che non ci sia un `last_error` recente.
- Log in CloudWatch `/aws/lambda/gymbot-telegram-*`.
- Schedule `gym-booking-mwf-0700`: `ENABLED`, `cron(0 0 ? * MON *)`, `Europe/Rome`, target la versione 1 del guard, retry 0.

## Rollback

1. Telegram `deleteWebhook`.
2. `aws cloudformation delete-stack --stack-name gymbot-telegram --region eu-north-1 --profile aws-personale`
3. Facoltativo: eliminare i parametri `/gymbot/*` e il bucket S3 creato da SAM.

La Lambda legacy non viene toccata. Dal 2026-11-12 la schedule punta al guard (vedi sotto).

## Migrazione schedule al guard (2026-11-12)

La schedule `gym-booking-mwf-0700` dal 2026-11-12 invoca `scheduled_guard`
invece della Lambda legacy `GymBooking-personale`. Dal 2026-11-18 è `ENABLED`,
`cron(0 0 ? * MON *)` in `Europe/Rome`, target la versione 1 del guard:

- Payload `{"schedule_arn": <arn>, "scheduled_time": "<aws.scheduler.scheduled-time>"}`.
- Retry 0, `MaximumEventAgeInSeconds` 60 (prima 185 retry / 86400 s).
- Il guard registra l'occorrenza in DynamoDB (at-most-once) e prenota con `app.lambda_handler` nello stesso pacchetto. Non invoca la Lambda legacy.
- Ruolo scheduler: aggiunta la policy inline `InvokeGymBookingGuard`; la vecchia
  `InvokeGymBooking` e' stata lasciata come backup per il rollback.
- La Lambda legacy resta deployata e invocabile direttamente.
- Il lunedì a mezzanotte prenota lunedì, mercoledì e venerdì di quella settimana all'orario in `/prefs` (dal 2026-11-18: 07:00). Senza preferenze salvate usa `BookingTime`. Una data in `/skip`, o un giorno tolto dalle preferenze, non si prenota.

Rollback della sola schedule (lasciarla `DISABLED`): `update-schedule` con target
`arn:aws:lambda:eu-north-1:163876584948:function:GymBooking-personale`, `Input`
`{"time":"07:00"}`, `RetryPolicy` 185 tentativi / 86400 s, stesso `RoleArn`. Passa tutti
i campi (`update-schedule` azzera quelli omessi).

`/enable` dal bot resta bloccato finche' non viene deciso diversamente (ADR 0002).
