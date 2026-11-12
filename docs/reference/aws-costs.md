---
title: AWS costs and free tier
type: reference
status: canonical
owner: gymbooking
last-verified: 2026-11-12
relevant-code:
  - bot-template.yaml
---

# AWS costs and free tier

Snapshot della ricerca del **2026-11-12**. Prezzi e quote AWS cambiano:
rivalida prima di decisioni di spesa. Risorse descritte in
`docs/architecture/telegram-bot-aws.md`.

## Costo atteso per servizio

| Servizio | Uso del bot | Quota gratuita | Fonte | Esito |
|---|---|---|---|---|
| Lambda | 3 funzioni, poche invocazioni al giorno | 1M richieste e 400.000 GB-s al mese | Pagina prezzi AWS Lambda (verificata) | Gratis |
| Function URL | Nessun costo a parte | Si pagano solo le invocazioni Lambda | Conoscenza generale, non riverificata | Gratis |
| DynamoDB | 1 RCU, 1 WCU, pochi KB | 25 RCU, 25 WCU, 25 GB provisioned | Pagina prezzi AWS DynamoDB (verificata) | Gratis |
| SSM Parameter Store | 4 parametri Standard | Nessun costo per Standard | Pagina prezzi AWS Systems Manager (verificata) | Gratis |
| CloudWatch Logs | Retention 7 giorni | 5 GB al mese | Conoscenza generale, non riverificata | Gratis |
| S3 | Pochi KB di pacchetti | 5 GB per i nuovi account | Conoscenza generale, non riverificata | Quasi zero |
| KMS (chiave AWS managed) | Decrypt delle richieste SSM | 20.000 richieste al mese, nessun canone | Conoscenza generale, non riverificata | Gratis |

Assenti per scelta: NAT Gateway, VPC, endpoint privati, Secrets Manager (0,40 USD
a segreto al mese).

**Conclusione:** costo atteso 0 EUR con l'uso previsto. Non e' un tetto di spesa
garantito.

## Stato dell'account (2026-11-12)

- Piano Free attivo, credito residuo circa 120 USD, scadenza piano 2027-05-11.
- L'API Free Tier non ha restituito dati di consumo: i consumi non sono verificati
  direttamente.
- Alla scadenza del piano o dei crediti AWS richiede un piano a pagamento. Le
  quote "always free" di Lambda e DynamoDB restano.

## Rischi di spesa

1. Function URL pubblica: richieste abusive ricevono 403 ma consumano invocazioni.
   Rischio basso per un bot privato.
2. Le quote gratuite sono per account e regione, condivise con altri usi.
3. Il bucket S3 di SAM resta dopo la cancellazione dello stack.

## Azioni aperte

- Controllare periodicamente crediti e consumi in Billing.

## Budget attivo

Creato il 2026-11-12 (API Budgets, regione `us-east-1`): `gymbot-monthly-1usd`,
costo mensile con limite 1 USD, crediti esclusi dal calcolo (misura il consumo
lordo, non quello coperto dai crediti). Alert email all'indirizzo dell'account
AWS quando il costo effettivo supera l'80% e il 100%. L'indirizzo non e'
riportato nel repository. Il budget non blocca le risorse: avvisa soltanto.

## Fonti

- https://aws.amazon.com/lambda/pricing/
- https://aws.amazon.com/dynamodb/pricing/
- https://aws.amazon.com/systems-manager/pricing/
