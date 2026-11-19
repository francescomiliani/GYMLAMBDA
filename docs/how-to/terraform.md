---
title: Manage the AWS environment with Terraform
type: how-to
status: draft
owner: gymbooking
last-verified: 2026-11-19
relevant-code:
  - infra/terraform/
  - infra/terraform/waitlist_watch.tf
  - infra/bootstrap/main.tf
  - infra/scripts/inventory.sh
  - infra/scripts/tf.sh
  - scripts/build_bot_package.py
---

# Manage the AWS environment with Terraform

Stato: **draft**. Dal 2026-11-19 Terraform gestisce 31 risorse (passi 1-3 e 5 della migrazione fatti, `plan`
senza modifiche). Lo stack SAM `gymbot-telegram` e' ancora attivo (passi 4, 6 e 7 da fare, dopo aver visto girare il
guard): finche' non e' dismesso, `docs/how-to/telegram-bot-deploy.md` descrive ancora il deploy del codice.
Ogni comando che modifica AWS richiede l'autorizzazione esplicita dell'utente.

## Lezioni dalla migrazione del 2026-11-19

- Il primo `apply` ha ricaricato il codice di webhook e worker (39 KB, senza `requests`) nonostante
  `ignore_changes`: il bot ha risposto 502 finche' non e' stato rimesso il pacchetto SAM originale da S3
  (bucket `aws-sam-cli-managed-default-samclisourcebucket-*`). Causa non determinata. Prima e dopo ogni apply
  confrontare `CodeSize` delle tre Lambda con `aws lambda get-function-configuration`.
- Il guard e' pubblicato a mano (`update-function-code --publish`) e poi `plan`/`apply` sposta il target della
  schedule; con `publish = false` Terraform non pubblica versioni.
- `SNS_TOPIC_ARN` del guard e' vuota in esercizio: `guard_sns_enabled = false` la riproduce.
- Il permesso `lambda:InvokeFunction` della Function URL ha la condizione `lambda:InvokedViaFunctionUrl`
  (`invoked_via_function_url = true`).
- I pacchetti vanno costruiti con le dipendenze (`--with-deps`): senza `requests` ogni Lambda fallisce all'import.

## Cosa gestisce

Tabella DynamoDB, tre Lambda del bot (webhook con Function URL, worker, guard), Lambda legacy
`GymBooking-personale`, ruoli e policy IAM, log group (retention 7 giorni), topic SNS e iscrizione
email, schedule `gym-booking-mwf-0700` con il suo ruolo. **Non** gestisce i cinque parametri SSM
`/gymbot/*`: restano creati a mano (ADR 0001) e Terraform ne conosce solo i nomi.

## Risorse nuove: controllo lista d'attesa

`infra/terraform/waitlist_watch.tf` definisce risorse che non esistono nello stack SAM e quindi **non sono importate**: Lambda `gymbot-waitlist-watch`, ruolo e policy (DynamoDB Get/Put sulla tabella, SSM sui parametri del bot), ruolo dello scheduler che invoca solo questa funzione, log group e schedule `cron(0/30 6-22 * * ? *)` Europe/Rome con retry 0. `bot-template.yaml` non è stato esteso perché lo stack SAM sta per essere dismesso. Il primo `apply` le crea e carica il pacchetto corrente: costruirlo prima con `--with-deps` (`tf.sh` lo fa) e confrontare `CodeSize` delle Lambda prima e dopo, come nelle lezioni sopra. Stato iniziale della schedule: `waitlist_watch_enabled = true` (la funzione legge soltanto il portale); metterlo a `false` per crearla spenta. Un `plan` deve mostrare solo aggiunte.

## Struttura

| Percorso | Contenuto |
|---|---|
| `infra/bootstrap/` | Crea il bucket S3 dello state (versioning, SSE, accesso pubblico bloccato). State locale. |
| `infra/terraform/` | Configurazione principale. State in S3 con `use_lockfile`. |
| `infra/scripts/inventory.sh` | Sola lettura: elenca ID fisici, policy e permessi da copiare in `terraform.tfvars`. |
| `infra/scripts/tf.sh` | Costruisce i pacchetti Lambda e lancia `terraform` con profilo e regione fissi. |

## Prerequisiti

- Terraform >= 1.10, AWS CLI v2, login `aws login --profile aws-personale`.
- `terraform.tfvars` (gitignorato) da `terraform.tfvars.example`: contiene ID Telegram, email e nomi fisici.

## Migrazione dall'ambiente esistente

1. Bootstrap: `terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply`.
2. `infra/scripts/inventory.sh`, poi compilare `terraform.tfvars`. Verificare in particolare nomi delle
   policy inline, permesso della Function URL (`lambda:InvokeFunctionUrl` e, se presente, `lambda:InvokeFunction`),
   policy gestite dei ruoli e versione del guard puntata dalla schedule.
3. `terraform -chdir=infra/terraform init` e `infra/scripts/tf.sh plan`. Il piano deve contenere solo import
   e aggiunta dei tag; nessun replace, nessuna modifica a schedule, variabili d'ambiente o codice. Se
   compaiono differenze, allineare il `.tf` all'AWS reale, non il contrario.
4. Aggiungere `DeletionPolicy: Retain` e `UpdateReplacePolicy: Retain` alle risorse di `bot-template.yaml`,
   fare `sam deploy --no-execute-changeset`, controllare il change set e solo dopo eseguirlo.
5. `infra/scripts/tf.sh apply` (importa lo stato, non distribuisce codice).
6. `aws cloudformation delete-stack --stack-name gymbot-telegram`. Verificare che funzioni, tabella, URL e log
   group esistano ancora e che `getWebhookInfo` non segnali errori.
7. `infra/scripts/tf.sh plan` deve dare zero modifiche. Rimuovere `imports.tf`, `bot-template.yaml`,
   `template.yaml` e `tests/test_bot_template.py`; segnare `telegram-bot-deploy.md` come `superseded`.

Rollback prima del passo 6: `terraform state rm` sulle risorse importate; lo stack SAM e' intatto.

## Uso quotidiano

- `infra/scripts/tf.sh plan` / `apply`.
- **Deploy del codice**: durante la migrazione le Lambda hanno `ignore_changes = [filename, source_code_hash]`.
  Per distribuire, rimuoverlo in un commit dedicato e leggere il piano: il guard ha ora `publish = false` (e `publish` in `ignore_changes`) per non spostare il target della
  schedule attiva. Per distribuire il guard: `publish = true`, tolto da `ignore_changes`; ogni nuovo codice pubblica
  una versione e sposta il target della schedule; l'invoke config con retry 0 segue la versione. La Lambda legacy non si distribuisce da Terraform.
- Schedule: `state` ed `schedule_expression` sono in `ignore_changes` perche' li governano il bot e la procedura
  amministrativa (ADR 0002). Terraform non accende mai la schedule.

## Rischi noti

- La Lambda legacy ha password e token Telegram **in chiaro** nelle variabili d'ambiente. Con `ignore_changes`
  Terraform non le modifica, ma l'import le scrive nello state: il bucket e' privato, cifrato e versionato.
  Portare la legacy su SSM come il bot toglie il problema.
- Le variabili d'ambiente delle altre Lambda contengono solo nomi di parametri SSM e ID, mai segreti.
- Il nome del bucket dello state contiene l'ID account; con un altro account usare `init -reconfigure -backend-config`.
