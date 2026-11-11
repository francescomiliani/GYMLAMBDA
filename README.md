# GymBooking

Lambda che prenota una lezione in palestra sul portale Sportrick (`replywellnessTO`), con
notifica email a prenotazione avvenuta.

Gira su pianificazione **lunedì, mercoledì e venerdì alle 07:00** (ora di Roma) e prenota la
lezione delle 07:00 **7 giorni dopo**; si può anche invocare a mano con data e orario.
**Ogni invocazione con payload di prenotazione prenota davvero.**

## Risorse in esercizio

| Risorsa | Valore |
|---|---|
| Funzione | `GymBooking-personale`, `eu-north-1` (Stoccolma), Python 3.12, 128 MB, timeout 60 s |
| Ruolo Lambda | `GymBooking-personale-lambda-role` (log + `sns:Publish`) |
| Schedule | `gym-booking-mwf-0700` — `cron(0 7 ? * MON,WED,FRI *)`, `Europe/Rome`, finestra `OFF` |
| Topic notifiche | `arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche` |
| Variabili d'ambiente | `SPORTRICK_USERNAME`, `SPORTRICK_PASSWORD`, `SNS_TOPIC_ARN` |
| Prenotazioni attive | mar 17 e mer 18 novembre 2026, 07:00 |

## Come funziona

Tre richieste HTTP dentro un'unica sessione `requests`, poi una notifica:

1. **Login** — `POST /Account/Login` con `UserName` e `Password`.
2. **Calendario** — `POST /Booking/GetActivitySchedule` con `currentDate` e `activityID`;
   restituisce gli slot del giorno, ognuno con `TimeStart`, `AppointmentId` e `Notes`.
3. **Prenotazione** — `POST /Booking/BookAppointment` con `appointmentID` dello slot scelto.
4. **Notifica** — `sns:Publish` sul topic, solo se la prenotazione è stata confermata.

**Disponibilità.** Il calendario elenca anche le lezioni piene: il segnale affidabile è
`Notes == 'Prenota'`. Per una lezione al completo vale "Lezione al completo. Vuoi metterti in
lista d'attesa?" e il portale risponde al POST con un messaggio generico ("Nessun oggetto
permette di terminare la prenotazione"). Il codice legge `Notes` prima di prenotare e non
manda mai il POST per uno slot non prenotabile.

**Esito.** La prenotazione è riuscita solo se il campo `message` vale
`Prenotazione confermata con successo.`; qualunque altro testo diventa un errore con il
messaggio del portale incluso, così "posti esauriti" si distingue da "già prenotato".

Ogni richiesta ha `timeout=15`. Credenziali, cookie di sessione e corpi delle risposte non
vengono mai stampati nei log.

## Configurazione

La funzione legge tre variabili d'ambiente:

| Variabile | Contenuto |
|---|---|
| `SPORTRICK_USERNAME` | username del portale |
| `SPORTRICK_PASSWORD` | password del portale |
| `SNS_TOPIC_ARN` | topic SNS della notifica (opzionale) |

Senza le prime due la funzione si ferma prima di qualsiasi chiamata di rete. Se
`SNS_TOPIC_ARN` manca, la prenotazione funziona e nessuna mail viene inviata.

Il file `lambda_function/credentials.json` è un residuo locale del primo prototipo: è
escluso da Git e **non** viene letto dal codice.

## Invocazione

Payload vuoto → prenota le **18:15** a **7 giorni** da oggi (ora di Roma):

```json
{}
```

Scegliere giorno e ora:

```json
{"date": "2026-11-18", "time": "07:00"}
```

Sola lettura, nessuna prenotazione: elenca slot, `Notes` di ogni slot e i campi del form.

```json
{"inspect": true, "date": "2026-11-18"}
```

Fallback **opzionale, spento di default**: elencando orari alternativi in `fallback_times`,
se lo slot richiesto non è prenotabile si prova il successivo. Il ripiego scatta **solo** su
`Notes != 'Prenota'`: un POST rifiutato dal portale non fa mai partire un secondo tentativo.
Gli orari si valutano sul calendario già scaricato, quindi una sola sessione e una sola
lettura.

```json
{"time": "07:00", "fallback_times": ["13:30", "19:15"]}
```

Da CLI:

```bash
aws lambda invoke \
  --function-name GymBooking-personale \
  --region eu-north-1 \
  --profile aws-personale \
  --payload '{"inspect":true,"date":"2026-11-18"}' \
  --cli-binary-format raw-in-base64-out \
  --log-type Tail --output json /tmp/out.json
```

La funzione restituisce `{date, time, message}` quando prenota, `{date, slots, local_now}`
con `inspect`.

## Orari, fuso orario e cambio dell'ora

Tre cose che si sbagliano facilmente:

1. **Il runtime Lambda vive in UTC.** `datetime.now()` senza fuso, tra mezzanotte e le
   02:00 italiane, calcola la data del giorno prima. Il codice usa `ZoneInfo('Europe/Rome')`
   (`local_now()`), verificato nel runtime: `+01:00` in inverno, `+02:00` in estate.

2. **Gli EventBridge Rules classici sono solo UTC.** Un `cron(1 0 ? * MON-FRI *)` scatta alle
   01:01 italiane d'inverno e alle 02:01 d'estate: l'ora locale cambia da sola due volte
   l'anno. Per questo la pianificazione usa **EventBridge Scheduler**, che accetta
   `ScheduleExpressionTimezone: Europe/Rome` e gestisce il DST: in primavera salta l'ora
   inesistente, in autunno esegue una volta sola invece di due.

3. **La finestra flessibile sposta solo in avanti.** Con `FlexibleTimeWindow.Mode: OFF` la
   pianificazione è all'ora esatta, salvo il ritardo di consegna fisiologico. Con `FLEXIBLE`
   AWS può consegnare l'evento più tardi, entro la finestra configurata (1–1440 minuti). Non
   esiste una deriva "in anticipo": nel test reale, 43 secondi di ritardo su una schedule
   fissata al secondo.

**Finestra del portale.** Le prenotazioni sono accettate solo a breve distanza: una data a
9 giorni risponde `Prenotazione non consentita` per ogni slot. Da qui il default `+7 giorni`,
che cade esattamente al limite.

**Attenzione alla ridondanza.** Più schedule ravvicinate sono una difesa ragionevole contro i
ritardi di consegna, ma questa funzione **prenota a ogni invocazione**: tre schedule
significano tre tentativi. Prima di aggiungerne altre va resa idempotente.

## Pianificazione attiva

| Voce | Valore |
|---|---|
| Nome | `gym-booking-mwf-0700` |
| Espressione | `cron(0 7 ? * MON,WED,FRI *)` |
| Fuso | `Europe/Rome` (DST gestito da AWS) |
| Finestra | `FlexibleTimeWindow: OFF` |
| Payload | `{"time":"07:00"}` → prenota le 07:00 a +7 giorni |
| Ruolo | `GymBooking-personale-scheduler-role` (policy inline `InvokeGymBooking`) |
| Retry | nessuno |

```bash
aws scheduler create-schedule \
  --name gym-booking-mwf-0700 \
  --schedule-expression "cron(0 7 ? * MON,WED,FRI *)" \
  --schedule-expression-timezone "Europe/Rome" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target '{"Arn":"arn:aws:lambda:eu-north-1:ACCOUNT:function:GymBooking-personale","RoleArn":"arn:aws:iam::ACCOUNT:role/GymBooking-personale-scheduler-role","Input":"{\"time\":\"07:00\"}"}' \
  --region eu-north-1
```

Sospendere senza cancellare: `aws scheduler update-schedule --name gym-booking-mwf-0700
--state DISABLED` (con gli stessi altri argomenti).

Nessun retry di proposito: un errore può arrivare dopo una prenotazione già registrata, e un
secondo tentativo cieco rischierebbe un doppio POST.

## Notifica email

Quando una prenotazione viene confermata la funzione pubblica un messaggio sul topic SNS, che
lo consegna via email agli iscritti. L'indirizzo **non è nel codice**: vive solo
nell'iscrizione SNS, quindi non finisce nel repo.

| Voce | Valore |
|---|---|
| Variabile Lambda | `SNS_TOPIC_ARN` |
| Permesso | `sns:Publish` sul topic, policy inline `PublishBookingNotifications` nel ruolo della Lambda |
| Oggetto | `GymBooking: prenotazione confermata` |
| Corpo | `2026-11-18 07:00 - Prenotazione confermata con successo.` |

La notifica è **best effort**: sta in un `try`, e se SNS o `boto3` falliscono la funzione
stampa `notifica non inviata: ...` e restituisce comunque la prenotazione confermata. Una mail
persa non deve far sembrare fallita una prenotazione riuscita; l'errore resta nei log
CloudWatch. `boto3` è importato dentro `notify()` perché è già presente nel runtime Lambda e
serve solo su quel percorso.

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

## Annullare una prenotazione

Non c'è codice di annullamento: si fa dal portale o dall'app. La funzione non riconosce una
prenotazione esistente, quindi non serve tenerne conto per la schedule.

## Deploy

Aggiornamento del codice:

```bash
BUILD=$(mktemp -d)
python3 -m pip install --quiet --target "$BUILD" -r lambda_function/requirements.txt
cp lambda_function/app.py "$BUILD/app.py"
(cd "$BUILD" && zip -qr /tmp/gym-lambda.zip .)

aws lambda update-function-code \
  --function-name GymBooking-personale \
  --zip-file fileb:///tmp/gym-lambda.zip \
  --region eu-north-1 --profile aws-personale
aws lambda wait function-updated-v2 --function-name GymBooking-personale \
  --region eu-north-1 --profile aws-personale
```

`requirements.txt` contiene solo `requests`: `boto3` è fornito dal runtime. `template.yaml`
contiene la definizione SAM equivalente, con i parametri `NoEcho` per le credenziali e senza
evento `Schedule`.

## Test

14 test unitari con mock: nessuna chiamata di rete, nessuna prenotazione, nessuna email.

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
- data e ora predefinite a +7 giorni e 18:15, calcolate in `Europe/Rome`;
- `local_now()` usa davvero `Europe/Rome`.

`boto3` non è installato nell'ambiente locale di test: i test della notifica lo iniettano in
`sys.modules`, e l'import dentro `notify()` non tocca gli altri percorsi.

## Costi

Un'invocazione dura 2,5 s a caldo, fino a ~8 s a freddo con `boto3` da caricare, sempre a
128 MB: pochi millesimi di GB-secondo, dentro i 400.000 GB-s e il milione di richieste
gratuite mensili di Lambda. Le notifiche SNS via email hanno 1.000 consegne gratuite al mese,
ben oltre le ~13 prenotazioni mensili. A riposo il costo è nullo: senza invocazioni Lambda
non addebita nulla. Non sono usati altri servizi AWS a pagamento.

## Avvertenza

L'invocazione con payload di prenotazione **prenota davvero**, e la schedule lo fa ogni
lunedì, mercoledì e venerdì alle 07:00. Il codice non è idempotente: evita il POST solo quando
lo slot risulta non prenotabile, non perché riconosca una prenotazione esistente. Non
aggiungere altre schedule di prenotazione in parallelo senza averlo reso idempotente. Per
prove innocue usare `{"inspect": true}`. Un fallimento (lezione piena, fuori finestra, portale
giù) non manda email: si vede solo nei log CloudWatch.
