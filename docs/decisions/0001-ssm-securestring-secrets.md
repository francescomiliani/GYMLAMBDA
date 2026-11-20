---
title: Secrets in SSM SecureString read at runtime
type: decision
status: canonical
owner: gymbooking
last-verified: 2026-11-18
relevant-code:
  - lambda_function/ssm_config.py
  - infra/terraform/lambda_bot.tf
---

# Secrets in SSM SecureString read at runtime

## Contesto

Token Telegram, webhook secret, credenziali Sportrick e il JSON di Google Calendar
non devono comparire
come parametri CloudFormation ne' come env var in chiaro. Secrets Manager costa
0,40 USD a segreto al mese. `AWS::SSM::Parameter` non supporta `SecureString` e
`{{resolve:ssm-secure}}` non e' valido nelle env var Lambda.

## Decisione

Cinque `SecureString` Standard con chiave AWS managed, creati fuori dallo
stack. Il quinto, `/gymbot/google-calendar`, è il JSON del refresh token (dal
2026-11-18). Le funzioni ricevono solo il nome del parametro (`SSM_<NOME>`) e leggono il
valore con `ssm_config.get_secret`, con cache per processo e timeout brevi.
`SSM_GOOGLE_CALENDAR` è sul worker e sul guard. Il webhook non lo ha: la
scrittura del calendario avviene nel worker.
Se SSM e' configurato e fallisce, errore senza fallback a env. IAM: solo
`ssm:GetParameter` sugli ARN esatti. Le credenziali si leggono prima di ogni
claim irreversibile.

## Conseguenze

- Nessun costo aggiuntivo.
- La rotazione richiede di riciclare le funzioni (cache) e, per il webhook
  secret, di rieseguire `setWebhook`.
- SSM conserva le versioni precedenti dei parametri.
- Il pacchetto e' costruito da allowlist, mai dalla cartella con `credentials.json`.
