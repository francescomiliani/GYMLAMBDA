---
title: Deploy the Telegram bot
type: how-to
status: canonical
owner: gymbooking
last-verified: 2026-11-12
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

Quattro `SecureString` Standard, creati con `Overwrite=False` da SDK, leggendo i
valori da file locali e non da argomenti di comando:

- `/gymbot/telegram-bot-token`
- `/gymbot/telegram-webhook-secret` (generato casualmente, non rigenerare a ogni deploy)
- `/gymbot/sportrick-username`
- `/gymbot/sportrick-password`

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

## 4. Telegram

- `setWebhook` verso la Function URL (output `WebhookUrl`) con `secret_token` uguale
  al parametro SSM e `allowed_updates=["message","callback_query"]`.
- `setMyCommands`: `start`, `bookings`, `activities`, `automation`, `weekly`,
  `disable`, `help`. Il menu non si aggiorna da solo.
  Telegram usa lo scope piu' specifico: un elenco su `all_private_chats` o
  `all_group_chats` nasconde quello `default`. Imposta tutti e tre gli scope e
  verifica ciascuno con `getMyCommands`. Il client ha una cache: riapri la chat.
  Incidente del 2026-11-12: un vecchio elenco `/hello` su `all_private_chats`
  nascondeva i comandi del bot.

## 5. Verifica

- POST senza secret alla URL: 403. GET: 405.
- Su Telegram: `/start`, poi `/bookings` e `/automation` (sola lettura).
- Log in CloudWatch `/aws/lambda/gymbot-telegram-*`.
- Schedule ancora `DISABLED`.

## Rollback

1. Telegram `deleteWebhook`.
2. `aws cloudformation delete-stack --stack-name gymbot-telegram --region eu-north-1 --profile aws-personale`
3. Facoltativo: eliminare i parametri `/gymbot/*` e il bucket S3 creato da SAM.

La Lambda legacy non viene toccata. Dal 2026-11-12 la schedule punta al guard (vedi sotto).

## Migrazione schedule al guard (2026-11-12)

La schedule `gym-booking-mwf-0700` (sempre `DISABLED`) ora invoca `scheduled_guard`
invece della Lambda legacy `GymBooking-personale`:

- Payload `{"schedule_arn": <arn>, "scheduled_time": "<aws.scheduler.scheduled-time>"}`.
- Retry 0, `MaximumEventAgeInSeconds` 60 (prima 185 retry / 86400 s).
- Il guard registra l'occorrenza in DynamoDB (at-most-once) e poi chiama la legacy.
- Ruolo scheduler: aggiunta la policy inline `InvokeGymBookingGuard`; la vecchia
  `InvokeGymBooking` e' stata lasciata come backup per il rollback.
- La Lambda legacy resta deployata e invocabile direttamente.
- Il guard prenota sempre l'orario `BookingTime` (default 07:00) a +7 giorni dalla
  data schedulata. Se cambi l'orario via `/weekly`, aggiorna anche `BookingTime`.

Rollback della sola schedule (lasciarla `DISABLED`): `update-schedule` con target
`arn:aws:lambda:eu-north-1:163876584948:function:GymBooking-personale`, `Input`
`{"time":"07:00"}`, `RetryPolicy` 185 tentativi / 86400 s, stesso `RoleArn`. Passa tutti
i campi (`update-schedule` azzera quelli omessi).

`/enable` dal bot resta bloccato finche' non viene deciso diversamente (ADR 0002).
