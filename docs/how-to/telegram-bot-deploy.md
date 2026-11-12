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

La schedule e la Lambda legacy non vengono toccate.
