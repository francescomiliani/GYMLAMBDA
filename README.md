# GymBooking

Lambda che prenota una lezione in palestra sul portale Sportrick (`replywellnessTO`).

Gira su pianificazione **lunedì, mercoledì e venerdì alle 07:00** (ora di Roma) e prenota la
lezione delle 07:00 **7 giorni dopo**; si può anche invocare a mano con una data e un orario.
Ogni esecuzione con payload di prenotazione è una prenotazione reale.

## Come funziona

Tre richieste HTTP dentro un'unica sessione:

1. **Login** — `POST /Account/Login` con `UserName` e `Password`.
2. **Calendario** — `POST /Booking/GetActivitySchedule` con `currentDate` e `activityID`;
   restituisce gli slot del giorno, ognuno con `TimeStart`, `AppointmentId` e `Notes`.
3. **Prenotazione** — `POST /Booking/BookAppointment` con `appointmentID` dello slot scelto.

**Disponibilità:** il calendario elenca anche le lezioni piene. Il segnale affidabile è
`Notes == 'Prenota'`; per una lezione al completo vale "Lezione al completo. Vuoi metterti in
lista d'attesa?" e il portale rifiuta il POST con un messaggio generico. Il codice controlla
`Notes` prima di prenotare e non manda mai il POST per uno slot non prenotabile.

La risposta di prenotazione è considerata riuscita solo se il campo `message` vale
`Prenotazione confermata con successo.`; qualunque altro testo diventa un errore con il
messaggio del portale incluso, così si distingue "posti esauriti" da "già prenotato".


Ogni richiesta ha `timeout=15`. Credenziali, cookie di sessione e corpi delle risposte
non vengono mai stampati nei log.

## Configurazione

La funzione legge due variabili d'ambiente:

| Variabile | Contenuto |
|---|---|
| `SPORTRICK_USERNAME` | username del portale |
| `SPORTRICK_PASSWORD` | password del portale |
| `SNS_TOPIC_ARN` | topic SNS per la mail di conferma (opzionale) |

Senza le prime due la funzione si ferma prima di qualsiasi chiamata di rete. Se
`SNS_TOPIC_ARN` manca, la prenotazione funziona e nessuna mail viene inviata.

Il file `lambda_function/credentials.json` è solo un residuo locale del primo prototipo:
è escluso da Git e **non** viene letto dal codice.

## Invocazione

Payload vuoto → prenota la lezione delle **18:15** a **7 giorni** da oggi:

```json
{}
```

Scegliere giorno e ora:

```json
{"date": "2026-11-17", "time": "07:00"}
```

Sola lettura, nessuna prenotazione: elenca gli slot del giorno e i campi del form.

```json
{"inspect": true, "date": "2026-11-17"}
```

Fallback **opzionale, spento di default**: elencare orari alternativi in `fallback_times` fa
sì che, se lo slot richiesto non è prenotabile, si provi il successivo. Il ripiego scatta solo
su `Notes != 'Prenota'`: un POST rifiutato dal portale non fa mai partire un secondo tentativo.

```json
{"time": "07:00", "fallback_times": ["13:30", "19:15"]}
```

Da CLI:

```bash
aws lambda invoke \
  --function-name GymBooking-personale \
  --region eu-north-1 \
  --payload '{"inspect":true,"date":"2026-11-17"}' \
  --cli-binary-format raw-in-base64-out \
  --log-type Tail --output json /tmp/out.json
```

## Orari, fuso orario e cambio dell'ora

Tre cose che si sbagliano facilmente:

1. **Il runtime Lambda vive in UTC.** `datetime.now()` senza fuso, tra mezzanotte e le
   02:00 italiane, calcola la data del giorno prima. Il codice usa `ZoneInfo('Europe/Rome')`
   (`local_now()`), verificato nel runtime: restituisce `+01:00` in inverno, `+02:00` in estate.

2. **Gli EventBridge Rules classici sono solo UTC.** Un `cron(1 0 ? * MON-FRI *)` scatta alle
   01:01 italiane d'inverno e alle 02:01 d'estate: l'ora locale cambia da sola due volte
   l'anno. Per una pianificazione in ora locale servirebbe **EventBridge Scheduler**, che
   accetta `ScheduleExpressionTimezone: Europe/Rome` e gestisce il DST: in primavera salta
   l'ora inesistente, in autunno esegue una volta sola invece di due.

3. **La finestra flessibile sposta solo in avanti.** Con `FlexibleTimeWindow.Mode: OFF` la
   pianificazione è all'ora esatta, salvo il ritardo di consegna fisiologico. Con `FLEXIBLE`
   AWS può consegnare l'evento più tardi, entro la finestra configurata (1–1440 minuti).
   Non esiste una deriva "in anticipo".

**Attenzione alla ridondanza.** Avere più schedule attorno alla mezzanotte è una difesa
ragionevole contro i ritardi, ma questa funzione **prenota a ogni invocazione**: tre
schedule significano tre tentativi la stessa notte. Se serve ridondanza, prima va resa
idempotente (es. controllo della prenotazione esistente prima del POST).

## Pianificazione attiva

Schedule `gym-booking-mwf-0700` su EventBridge Scheduler, `eu-north-1`:

| Voce | Valore |
|---|---|
| Espressione | `cron(0 7 ? * MON,WED,FRI *)` |
| Fuso | `Europe/Rome` (DST gestito da AWS) |
| Finestra | `FlexibleTimeWindow: OFF` |
| Payload | `{"time":"07:00"}` → prenota le 07:00 a +7 giorni |
| Ruolo | `GymBooking-personale-scheduler-role` |

Creata così:

```bash
aws scheduler create-schedule \
  --name gym-booking-mwf-0700 \
  --schedule-expression "cron(0 7 ? * MON,WED,FRI *)" \
  --schedule-expression-timezone "Europe/Rome" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target '{"Arn":"arn:aws:lambda:eu-north-1:ACCOUNT:function:GymBooking-personale","RoleArn":"arn:aws:iam::ACCOUNT:role/GymBooking-personale-scheduler-role","Input":"{\"time\":\"07:00\"}"}' \
  --region eu-north-1
```

Per sospendere senza cancellare: `aws scheduler update-schedule --name gym-booking-mwf-0700
--state DISABLED` (con gli stessi altri argomenti).

Nessun retry: un errore potrebbe essere arrivato dopo una prenotazione riuscita, e un
secondo tentativo cieco sarebbe rischioso. La latenza di consegna resta fisiologica (nel test
reale: 43 secondi di ritardo su una schedule fissata al secondo).

## Notifica email

Quando una prenotazione viene confermata la funzione pubblica un messaggio sul topic SNS
`GymBooking-notifiche`, che lo consegna via email a chi è iscritto. L'indirizzo **non è nel
codice**: vive solo nell'iscrizione SNS, quindi non finisce nel repo.

| Voce | Valore |
|---|---|
| Topic | `arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche` |
| Iscritto | `france.mi18@gmail.com` (protocollo `email`) |
| Variabile Lambda | `SNS_TOPIC_ARN` |
| Permesso | `sns:Publish` sul topic, policy inline `PublishBookingNotifications` nel ruolo della Lambda |
| Oggetto / corpo | `GymBooking: prenotazione confermata` / `2026-11-20 07:00 - Prenotazione confermata con successo.` |

La notifica è **best effort**: è dentro un `try`, e se SNS o `boto3` falliscono la funzione
stampa `notifica non inviata: ...` e restituisce comunque la prenotazione confermata. Una
mail persa non deve far sembrare fallita una prenotazione riuscita. L'errore resta nei log
CloudWatch della funzione.

Cambiare destinatario:

```bash
aws sns subscribe --topic-arn arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche \
  --protocol email --notification-endpoint nuovo@example.com --region eu-north-1
```

L'iscrizione resta `PendingConfirmation` finché il destinatario non apre la mail di AWS e
clicca il link: fino ad allora le notifiche non vengono consegnate.

Verificato l'11/11/2026 su una prenotazione reale (mercoledì 18 novembre, 07:00): log
`Prenotazione confermata con successo.`, nessun `notifica non inviata`, e
`AWS/SNS NumberOfNotificationsDelivered = 1` sul minuto dell'invocazione.

## Deploy

La funzione gira in `eu-north-1`, Python 3.12, 128 MB, timeout 60 s, ruolo
`GymBooking-personale-lambda-role`.

Aggiornamento del codice:

```bash
BUILD=$(mktemp -d)
python3 -m pip install --quiet --target "$BUILD" -r lambda_function/requirements.txt
cp lambda_function/app.py "$BUILD/app.py"
(cd "$BUILD" && zip -qr /tmp/gym-lambda.zip .)

aws lambda update-function-code \
  --function-name GymBooking-personale \
  --zip-file fileb:///tmp/gym-lambda.zip \
  --region eu-north-1 --profile <profilo>
```

`template.yaml` contiene la definizione SAM equivalente, con i parametri `NoEcho` per le
credenziali e senza evento `Schedule`.

## Test

I test sono unitari e usano mock: nessuna chiamata di rete, nessuna prenotazione.

```bash
python3 -m unittest discover -s tests -v
```

Coprono:

- login → calendario → prenotazione, con il payload esatto inviato al portale;
- assenza di credenziali: errore immediato, nessuna sessione aperta;
- errore di rete: messaggio generico, nessuna credenziale nei log o nell'eccezione;
- orario non presente nel calendario: errore, nessun tentativo di prenotazione;
- lezione al completo (`Notes` diverso da `Prenota`): nessun `BookAppointment`;
- fallback attivo: prenota il primo slot prenotabile fra quelli elencati;
- fallback spento di default: nessun ripiego senza `fallback_times`;
- prenotazione confermata → `publish` sul topic con data e ora;
- notifica che fallisce: la prenotazione resta valida;
- nessun `SNS_TOPIC_ARN`: nessuna notifica, nessun errore;
- rifiuto del portale: messaggio riportato senza dati sensibili;
- modalità `inspect`: elenca gli slot e non chiama mai `BookAppointment`;
- data e ora predefinite a +7 giorni e 18:15, calcolate in `Europe/Rome`.

## Costi

Un'invocazione dura circa 2,5 s con 128 MB, cioè pochi millesimi di GB-secondo: resta
dentro i 400.000 GB-s e il milione di richieste gratuite mensili di Lambda. Le notifiche SNS
via email hanno 1.000 consegne gratuite al mese, ben oltre le ~13 prenotazioni mensili.
Non sono usati altri servizi AWS a pagamento.

## Avvertenza

L'invocazione con payload di prenotazione **prenota davvero**, e la schedule lo fa ogni
lunedì, mercoledì e venerdì alle 07:00. Il codice non è idempotente: se la lezione è già
prenotata non manda il POST solo quando lo slot risulta non prenotabile, non perché
riconosca una prenotazione esistente. Non aggiungere altre schedule di prenotazione in
parallelo senza averlo reso idempotente. Per prove innocue usare `{"inspect": true}`.
