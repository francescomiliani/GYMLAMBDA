# AGENTS.md

## Documentazione del repository

- `README.md` è la guida per configurazione, uso, notifiche, deploy e pianificazione.
- `docs/superpowers/plans/` contiene i piani e i limiti di implementazione; verificare lo stato attuale nel codice prima di considerarli completati.
- Le informazioni sulle risorse AWS nel README sono uno snapshot: verificare lo stato reale prima di operare.

## Progetto

GymBooking è un client Python e una funzione AWS Lambda per il portale Sportrick Reply Wellness. Il codice locale può differire da quello già distribuito.

- `lambda_function/app.py`: handler Lambda, compatibilità con i payload precedenti e notifiche SNS/Telegram.
- `lambda_function/sportrick.py`: client HTTP, catalogo, calendario, impegni e operazioni di prenotazione.
- `lambda_function/requirements.txt`: dipendenze del pacchetto; `boto3` è disponibile nel runtime Lambda.
- `tests/`: test unitari con mock; `template.yaml`: definizione SAM di base, non equivalente all'intera configurazione AWS attiva.

## Comportamento

- **Semplicità e modifiche mirate:** riusare codice e dipendenze esistenti, preservare compatibilità e non aggiungere funzionalità non richieste.
- **Pianificare e verificare:** per modifiche articolate definire passaggi e controlli, aggiornare la documentazione interessata e riferire risultati e limiti reali.
- **Flussi reali:** le invocazioni di prenotazione, cancellazione e lista d'attesa modificano il portale; il payload legacy vuoto prenota davvero. Per test reali usare solo operazioni autorizzate dall'utente e verificare l'eventuale rimozione dell'impegno di prova.
- **Esiti incerti:** non ripetere automaticamente operazioni di modifica dopo timeout o risposte ambigue. Prima verificare gli impegni sul portale.
- **Tipi e identificativi:** trattare gli ID come stringhe. Le lezioni `class` e i servizi `service` hanno flussi diversi; non presumere che la prenotazione di servizi o una scelta aggiuntiva nel form sia supportata.
- **Notifiche:** inviarle solo per operazioni confermate; un errore di notifica non deve nascondere una prenotazione o cancellazione riuscita. `accepted` indica accettazione dal servizio, non lettura del messaggio.

## Credenziali e AWS

- Non committare, stampare o inserire nei log credenziali Sportrick, cookie, token Telegram o file locali di configurazione. `lambda_function/credentials.json` è ignorato da Git; i dati Telegram locali sono fuori dal repository.
- Le modifiche al codice e i test locali non aggiornano la Lambda. Deploy, modifiche IAM o nuove schedule richiedono una richiesta dell'utente; ispezionare prima risorse e modifiche correnti.
- Esiste una schedule che può invocare la Lambda automaticamente: controllare stato, payload, fuso e policy di retry prima di cambiarla. EventBridge Scheduler e Lambda asincrona hanno politiche di retry distinte; il codice non è idempotente.
- `update-schedule` ripristina ai valori predefiniti le opzioni omesse. Conservare la configurazione esistente e verificare il risultato. Un aggiornamento delle variabili Lambda sostituisce l'intera mappa: preservare quelle presenti e usare `RevisionId`.

## Test e verifica

```bash
python3 -m unittest discover -s tests -v
```

I test unitari non inviano prenotazioni o notifiche. Per le letture reali e i comandi AWS usare il profilo e la regione documentati nel README; non stampare l'output contenente variabili d'ambiente o credenziali. Distinguere sempre conferme del portale, accettazione SNS/Telegram e consegna effettiva.
