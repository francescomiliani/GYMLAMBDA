# GymBooking

Client Python e Lambda per consultare corsi e impegni sul portale Sportrick
(`replywellnessTO`), prenotare e cancellare lezioni e inviare notifiche email e Telegram.

**Stato:** le nuove azioni sono distribuite sulla Lambda; le variabili per email SNS e
Telegram sono configurate. La pianificazione rimane disattivata.

La pianificazione è **disattivata** dall'11/11/2026. Quando abilitata gira lunedì,
mercoledì e venerdì alle 07:00 (ora di Roma) e prenota la lezione delle 07:00 **7 giorni
dopo**; la funzione si può anche invocare a mano con data e orario.
**Ogni invocazione con payload di prenotazione prenota davvero.**

## Risorse in esercizio

| Risorsa | Valore |
|---|---|
| Funzione | `GymBooking-personale`, `eu-north-1` (Stoccolma), Python 3.12, 128 MB, timeout 60 s |
| Ruolo Lambda | `GymBooking-personale-lambda-role` (log + `sns:Publish`) |
| Schedule | `gym-booking-mwf-0700` — `DISABLED`, `cron(0 7 ? * MON,WED,FRI *)`, `Europe/Rome`, finestra `OFF` |
| Topic notifiche | `arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche` |
| Variabili d'ambiente | `SPORTRICK_USERNAME`, `SPORTRICK_PASSWORD`, `SNS_TOPIC_ARN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Prenotazioni verificate l'11/11/2026 | mar 17 e mer 18 novembre 2026, 07:00 |

Le nuove azioni esplicite consentono anche di consultare attività, calendari e impegni,
e gestire prenotazioni di lezioni e liste d'attesa. Non richiedono una schedule.

## Come funziona

Il flusso precedente usa un'unica sessione, poi invia una notifica:

1. **Login** — lettura del modulo e `POST /Account/Login` con credenziali e token del modulo.
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
`Prenotazione confermata con successo.`. Le azioni esplicite distinguono `confirmed`,
`rejected` e `uncertain`; il flusso precedente segnala i rifiuti come errori.

Le richieste Sportrick e Telegram hanno un timeout di 15 secondi; SNS usa timeout più brevi
e non ritenta. Credenziali e cookie non vengono stampati nei log.

## Prerequisiti

Per deploy, gestione delle schedule e comandi AWS serve **AWS CLI v2 installata sul
computer** e un profilo con accesso e permessi alle risorse interessate. Su macOS, con
Homebrew, si può installare con `brew install awscli`; in alternativa usare la
[guida ufficiale AWS](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).

```bash
aws --version
aws sts get-caller-identity --profile aws-personale
python3 --version
python3 -m pip --version
command -v zip
command -v unzip
```

Gli esempi usano il profilo `aws-personale`, la regione `eu-north-1`, Python con `pip`,
`zip` e `unzip`. Sostituire profilo, regione e nomi se si lavora su un altro account. `uv` serve
solo per l'esempio di esecuzione locale; la SAM CLI non è richiesta per il deploy ZIP.

## Configurazione

La funzione legge queste variabili d'ambiente:

| Variabile | Contenuto |
|---|---|
| `SPORTRICK_USERNAME` | username del portale |
| `SPORTRICK_PASSWORD` | password del portale |
| `SNS_TOPIC_ARN` | topic SNS della notifica (opzionale) |
| `TELEGRAM_BOT_TOKEN` | token del bot Telegram (opzionale; non committare) |
| `TELEGRAM_CHAT_ID` | chat destinataria Telegram (opzionale) |

Senza le prime due la funzione si ferma prima di qualsiasi chiamata di rete. Se
`SNS_TOPIC_ARN` manca, la prenotazione funziona e nessuna mail viene inviata.

Il file `lambda_function/credentials.json` è escluso da Git. Il client non lo legge
automaticamente; l'esempio locale seguente lo usa per impostare le variabili d'ambiente.

## Invocazione

### Azioni esplicite

Gli identificativi sono **stringhe**, anche quando contengono solo cifre: possono essere
troppo lunghi per un numero JavaScript. Prima elenca i corsi e consulta il calendario;
i tipi delle attività sono letti dal portale, senza identificativi fissati nel codice.

```json
{"action":"activities"}
```

```json
{"action":"schedule","activity_id":"224733210634138198","date":"2026-11-17"}
```

```json
{"action":"bookings"}
```

Le azioni seguenti modificano realmente il tuo account. Usa gli ID restituiti dal
calendario e dagli impegni: l'ID di una prenotazione può essere diverso dall'ID di uno slot.

```json
{"action":"book","activity_id":"<activity_id>","date":"2026-11-17","appointment_id":"<appointment_id>"}
```

```json
{"action":"cancel","booking_id":"<booking_id>"}
```

```json
{"action":"waitlist_join","appointment_id":"<appointment_id>"}
{"action":"waitlist_leave","booking_id":"<booking_id>"}
{"action":"waitlist_confirm","booking_id":"<booking_id>"}
```

Gli esiti sono `confirmed`, `rejected` o `uncertain`. Con `uncertain`, un timeout o una
risposta non riconosciuta potrebbero aver comunque modificato il portale: controlla gli
impegni prima di riprovare. Non vengono eseguiti ritentativi automatici.

Il calendario può includere lezioni e servizi. La prenotazione esplicita supporta solo
slot di tipo `class` con stato `Available` e nota `Prenota`: la prenotazione di servizi,
inclusa la visita medica quando è di tipo `service`, non è ancora implementata.
Una lezione che richiede ulteriori scelte nel modulo di conferma viene rifiutata.
La lista d'attesa può comportare una prenotazione automatica o l'uso di crediti,
secondo le regole della palestra.

### Esecuzione locale

Dal progetto, questo esempio legge le credenziali locali ed elenca i corsi senza prenotare:

```bash
uv run --no-project --with boto3 --with 'botocore[crt]' --with requests python - <<'PY'
import json
import os
from pathlib import Path
from lambda_function.app import lambda_handler

credentials = json.loads(Path('lambda_function/credentials.json').read_text())
os.environ['SPORTRICK_USERNAME'] = credentials['UserName']
os.environ['SPORTRICK_PASSWORD'] = credentials['Password']
os.environ.setdefault('AWS_PROFILE', 'aws-personale')
os.environ.setdefault('AWS_DEFAULT_REGION', 'eu-north-1')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche')
for variable, filename in [('TELEGRAM_BOT_TOKEN', 'telegram-token'), ('TELEGRAM_CHAT_ID', 'telegram-chat-id')]:
    path = Path.home() / '.config' / 'gymbooking' / filename
    if path.exists():
        os.environ.setdefault(variable, path.read_text().strip())

print(json.dumps(lambda_handler({'action': 'activities'}, None), ensure_ascii=False, indent=2))
PY
```

Per un'altra operazione, sostituisci il dizionario `{'action': 'activities'}` con uno dei
payload sopra. Prenotazioni, cancellazioni e azioni sulla lista d'attesa sono reali.

### Compatibilità con i payload precedenti

Payload vuoto → prenota la lezione delle **18:15** a **7 giorni** da oggi:

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

Il flusso precedente restituisce data, ora, messaggio e stato delle notifiche quando
prenota; con `inspect` restituisce `{date, slots, local_now}`.

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

## Pianificazione configurata

| Voce | Valore |
|---|---|
| Nome | `gym-booking-mwf-0700` |
| Stato | `DISABLED` dall'11/11/2026 |
| Espressione | `cron(0 7 ? * MON,WED,FRI *)` |
| Fuso | `Europe/Rome` (DST gestito da AWS) |
| Finestra | `FlexibleTimeWindow: OFF` |
| Payload | `{"time":"07:00"}` → prenota le 07:00 a +7 giorni |
| Ruolo | `GymBooking-personale-scheduler-role` (policy inline `InvokeGymBooking`) |
| Retry Scheduler | fino a 185 tentativi di consegna entro 86.400 secondi, rilevati l'11/11/2026 |

I retry di Scheduler riguardano la **consegna dell'evento**. Lambda gestisce separatamente i
retry delle invocazioni asincrone; in assenza di configurazione dedicata può riprovare due
volte gli errori della funzione. Il client non ritenta automaticamente un POST ambiguo,
ma la funzione non è idempotente: una nuova invocazione può ripetere la prenotazione.

Consultare la configurazione corrente, incluso il payload e la policy di retry:

```bash
aws scheduler get-schedule --name gym-booking-mwf-0700 \
  --region eu-north-1 --profile aws-personale \
  --query '{State:State,Expression:ScheduleExpression,Timezone:ScheduleExpressionTimezone,Window:FlexibleTimeWindow,Target:Target}'
```

### Creare una schedule

La schedule sopra **esiste già**. L'esempio crea una seconda schedule con un altro nome e
inizialmente `DISABLED`: attivarla solo se serve davvero, per non duplicare le prenotazioni.
Richiede il ruolo Scheduler esistente, che deve poter essere assunto da
`scheduler.amazonaws.com` e autorizzare `lambda:InvokeFunction` sulla funzione; l'identità
che crea la schedule deve poter usare `iam:PassRole`. In un nuovo account creare e
configurare prima un ruolo equivalente.

```bash
GYM_FUNCTION_ARN=$(aws lambda get-function --function-name GymBooking-personale \
  --region eu-north-1 --profile aws-personale --query Configuration.FunctionArn --output text)
GYM_SCHEDULER_ROLE_ARN=$(aws iam get-role --role-name GymBooking-personale-scheduler-role \
  --profile aws-personale --query Role.Arn --output text)
GYM_TARGET_FILE=$(mktemp)
python3 - "$GYM_FUNCTION_ARN" "$GYM_SCHEDULER_ROLE_ARN" "$GYM_TARGET_FILE" <<'PY'
import json
import sys
from pathlib import Path

target = {
    'Arn': sys.argv[1],
    'RoleArn': sys.argv[2],
    'Input': json.dumps({'time': '07:00'}),
    'RetryPolicy': {'MaximumEventAgeInSeconds': 60, 'MaximumRetryAttempts': 0},
}
Path(sys.argv[3]).write_text(json.dumps(target))
PY
aws scheduler create-schedule --name gym-booking-example --state DISABLED \
  --schedule-expression 'cron(0 7 ? * MON,WED,FRI *)' \
  --schedule-expression-timezone Europe/Rome --flexible-time-window '{"Mode":"OFF"}' \
  --target "file://$GYM_TARGET_FILE" --region eu-north-1 --profile aws-personale
rm "$GYM_TARGET_FILE"
```

I retry a zero nell'esempio si applicano solo alla consegna da Scheduler e non disattivano
i retry asincroni di Lambda. La schedule resta disattivata fino a un'esplicita attivazione.

### Sospendere o aggiornare una schedule

`update-schedule` sostituisce la configurazione e ripristina i campi opzionali omessi ai
valori predefiniti. Per sospendere quella esistente, questo esempio legge e riutilizza i
campi modificabili, inclusi target e retry:

```bash
(
  set -e
  GYM_SCHEDULE_FILE=$(mktemp)
  trap 'rm -f "$GYM_SCHEDULE_FILE"' EXIT
  aws scheduler get-schedule --name gym-booking-mwf-0700 \
    --region eu-north-1 --profile aws-personale --output json > "$GYM_SCHEDULE_FILE"
  python3 - "$GYM_SCHEDULE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
schedule = json.loads(path.read_text())
fields = ('Name', 'GroupName', 'Description', 'ScheduleExpression',
          'ScheduleExpressionTimezone', 'StartDate', 'EndDate', 'State',
          'FlexibleTimeWindow', 'Target', 'KmsKeyArn', 'ActionAfterCompletion')
path.write_text(json.dumps({key: schedule[key] for key in fields if key in schedule}))
PY
  aws scheduler update-schedule --cli-input-json "file://$GYM_SCHEDULE_FILE" \
    --state DISABLED --region eu-north-1 --profile aws-personale
)
aws scheduler get-schedule --name gym-booking-mwf-0700 \
  --region eu-north-1 --profile aws-personale --query '{State:State,Target:Target}'
```

Per riattivarla, ripetere la procedura leggendo di nuovo la configurazione e sostituire
`DISABLED` con `ENABLED`. Per cambiare orario, aggiungere `--schedule-expression` al comando
di aggiornamento con la nuova espressione; ricontrollare stato, fuso, target e retry.

## Notifiche

Quando una prenotazione viene confermata la funzione pubblica un messaggio sul topic SNS, che
lo consegna via email agli iscritti. L'indirizzo **non è nel codice**: vive solo
nell'iscrizione SNS, quindi non finisce nel repo.

| Voce | Valore |
|---|---|
| Variabile Lambda | `SNS_TOPIC_ARN` |
| Permesso | `sns:Publish` sul topic, policy inline `PublishBookingNotifications` nel ruolo della Lambda |
| Oggetto | `GymBooking: prenotazione confermata` |
| Corpo | `2026-11-18 07:00 - SALA ATTREZZI - prenotazione confermata` |

La funzione invia un avviso sia per le prenotazioni sia per le cancellazioni
confermate. Se sono configurati `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`, invia lo stesso
messaggio anche al bot Telegram. I due canali sono indipendenti; l'esito della prenotazione
resta valido anche se una notifica fallisce. Il campo `notifications` riporta `accepted`
quando il servizio accetta la richiesta, o `failed` se non riesce: l'accettazione non
garantisce la lettura o la consegna nella casella. Conservare il token fuori dal repository.

In locale occorrono `boto3`, credenziali AWS e la regione del topic; per esempio si può
usare `uv run --no-project --with boto3 --with 'botocore[crt]' --with requests python`,
impostando `AWS_PROFILE`, `AWS_DEFAULT_REGION` e `SNS_TOPIC_ARN`. L'extra CRT serve per
profili configurati con `aws login`; il runtime Lambda include già `boto3`.

Per la prova Telegram locale il token e l'ID della chat sono in `~/.config/gymbooking/`,
fuori dal repository. Caricarli nelle variabili d'ambiente della shell senza stamparli:

```bash
export TELEGRAM_BOT_TOKEN="$(cat ~/.config/gymbooking/telegram-token)"
export TELEGRAM_CHAT_ID="$(cat ~/.config/gymbooking/telegram-chat-id)"
```

La Lambda è stata aggiornata l'11/11/2026; le modifiche successive al codice locale vanno
distribuite con la procedura di deploy.

Cambiare destinatario:

```bash
aws sns subscribe --topic-arn arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche \
  --protocol email --notification-endpoint nuovo@example.com --region eu-north-1 \
  --profile aws-personale
```

L'iscrizione resta `PendingConfirmation` finché il destinatario non apre la mail di AWS e
clicca il link: fino ad allora le notifiche non vengono consegnate.

Verificato l'11/11/2026 su una prenotazione reale (mercoledì 18 novembre, 07:00): log
`Prenotazione confermata con successo.`, nessun `notifica non inviata`, e
`AWS/SNS NumberOfNotificationsDelivered = 1` sul minuto dell'invocazione.

La versione locale è stata provata l'11/11/2026 su **Functional Training, lunedì 16
novembre alle 18:30**: prenotazione e cancellazione confermate, entrambe le notifiche
ricevute sia via email sia via Telegram. L'impegno di prova è stato rimosso.
La Lambda aggiornata è stata verificata in sola lettura con catalogo, calendario e impegni;
non sono state effettuate nuove prenotazioni dalla Lambda dopo il deploy.

## Annullare una prenotazione

Con `action: cancel` e l'ID restituito da `bookings`, il client verifica che l'impegno sia
cancellabile e avvisa dopo la conferma. La schedule precedente non riconosce comunque una
prenotazione esistente.

## Deploy e aggiornamento Lambda

I comandi seguenti si eseguono dalla radice del repository e usano le risorse dell'account
indicato sopra. La schedule attuale punta alla funzione senza alias: aggiornare il codice
modifica `$LATEST`, usata anche dalla schedule. Prima del deploy controllare i cambiamenti
locali e lo stato della pianificazione.

### Preparare il pacchetto

Questo esempio crea uno ZIP nuovo con dipendenze compatibili con **Linux x86_64 e Python
3.12**, anche se il computer è un Mac ARM. Le variabili rimangono disponibili nella shell
per i comandi successivi.

```bash
GYM_BUILD_ROOT=$(mktemp -d)
GYM_PACKAGE_DIR="$GYM_BUILD_ROOT/package"
GYM_ZIP="$GYM_BUILD_ROOT/gymbooking.zip"
(
  set -e
  mkdir "$GYM_PACKAGE_DIR"
  python3 -m pip install --target "$GYM_PACKAGE_DIR" \
    --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
    --abi cp312 --abi abi3 --abi none --only-binary=:all: \
    -r lambda_function/requirements.txt
  cp lambda_function/app.py lambda_function/sportrick.py "$GYM_PACKAGE_DIR/"
  cd "$GYM_PACKAGE_DIR"
  zip -qr "$GYM_ZIP" .
)
unzip -l "$GYM_ZIP"
```

`requirements.txt` contiene `requests`; `boto3` è fornito dal runtime Lambda. I file
`app.py` e `sportrick.py` devono essere alla radice dello ZIP.

### Aggiornare una funzione esistente

```bash
aws lambda update-function-code --function-name GymBooking-personale \
  --zip-file "fileb://$GYM_ZIP" --region eu-north-1 --profile aws-personale \
  --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus,CodeSha256:CodeSha256}'
aws lambda wait function-updated-v2 --function-name GymBooking-personale \
  --region eu-north-1 --profile aws-personale
```

### Creare la funzione da zero

Usare questo comando **solo se la funzione non esiste**. Il ruolo indicato deve già
esistere, fidarsi del servizio Lambda e disporre dei permessi per log e, se richiesta,
pubblicazione SNS. In un altro account creare prima un ruolo equivalente e sostituirne
il nome; la creazione della funzione non imposta ancora le credenziali o una schedule.

```bash
GYM_LAMBDA_ROLE_ARN=$(aws iam get-role --role-name GymBooking-personale-lambda-role \
  --profile aws-personale --query Role.Arn --output text)
aws lambda create-function --function-name GymBooking-personale \
  --runtime python3.12 --architectures x86_64 --handler app.lambda_handler \
  --role "$GYM_LAMBDA_ROLE_ARN" --timeout 60 --memory-size 128 \
  --zip-file "fileb://$GYM_ZIP" --region eu-north-1 --profile aws-personale \
  --query '{FunctionName:FunctionName,State:State,FunctionArn:FunctionArn}'
aws lambda wait function-active-v2 --function-name GymBooking-personale \
  --region eu-north-1 --profile aws-personale
```

### Configurare credenziali e notifiche

Lambda sostituisce **l'intera mappa** delle variabili d'ambiente a ogni aggiornamento.
L'esempio legge le variabili esistenti senza stamparle, le integra con le credenziali del
file locale e, se entrambi presenti, con i file Telegram. Il `RevisionId` evita di
sovrascrivere modifiche concorrenti. Il file temporaneo contenente i segreti ha permessi
ristretti e viene eliminato all'uscita del blocco.

```bash
(
  set -e
  umask 077
  GYM_CONFIG_FILE=$(mktemp)
  trap 'rm -f "$GYM_CONFIG_FILE"' EXIT
  python3 - "$GYM_CONFIG_FILE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

config = json.loads(subprocess.check_output([
    'aws', 'lambda', 'get-function-configuration', '--function-name',
    'GymBooking-personale', '--region', 'eu-north-1', '--profile', 'aws-personale',
    '--output', 'json',
]))
if config.get('Environment', {}).get('Error'):
    raise RuntimeError('Impossibile leggere le variabili Lambda esistenti')
variables = config.get('Environment', {}).get('Variables', {}).copy()
credentials = json.loads(Path('lambda_function/credentials.json').read_text())
variables.update(SPORTRICK_USERNAME=credentials['UserName'], SPORTRICK_PASSWORD=credentials['Password'])
variables.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche')
telegram = Path.home() / '.config' / 'gymbooking'
token, chat = telegram / 'telegram-token', telegram / 'telegram-chat-id'
if token.is_file() and chat.is_file():
    variables.update(TELEGRAM_BOT_TOKEN=token.read_text().strip(), TELEGRAM_CHAT_ID=chat.read_text().strip())
payload = {'FunctionName': 'GymBooking-personale', 'RevisionId': config['RevisionId'],
           'Environment': {'Variables': variables}}
Path(sys.argv[1]).write_text(json.dumps(payload))
PY
  aws lambda update-function-configuration --cli-input-json "file://$GYM_CONFIG_FILE" \
    --region eu-north-1 --profile aws-personale --query FunctionName --output text
  aws lambda wait function-updated-v2 --function-name GymBooking-personale \
    --region eu-north-1 --profile aws-personale
)
```

Il topic SNS e la sua iscrizione email devono già esistere e il ruolo Lambda deve avere
`sns:Publish`; il bot Telegram deve essere stato avviato nella chat destinataria. Se si
lavora su un altro account, sostituire il topic predefinito nello script. Non mettere token
o password nel payload di Scheduler o nei comandi mostrati in chat.

### Verificare senza prenotare

```bash
GYM_RESULT_FILE=$(mktemp)
aws lambda invoke --function-name GymBooking-personale --region eu-north-1 \
  --profile aws-personale --cli-binary-format raw-in-base64-out \
  --payload '{"inspect":true,"date":"2026-11-18"}' \
  --query '{StatusCode:StatusCode,FunctionError:FunctionError}' "$GYM_RESULT_FILE"
cat "$GYM_RESULT_FILE"
rm "$GYM_RESULT_FILE"
```

Controllare sia `FunctionError` sia il contenuto della risposta: lo stato HTTP dell'API da
solo non prova che la funzione sia riuscita. Sostituire la data dell'esempio con quella da
verificare. `template.yaml` descrive solo la funzione di base e non configura SNS, Telegram
o la schedule esistente: non usarlo come sostituto completo della configurazione attiva.

## Test

I 41 test unitari usano mock: nessuna chiamata di rete, nessuna prenotazione, nessuna email.
Le indicazioni per gli agenti che lavorano sul repository sono in [AGENTS.md](AGENTS.md).

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
- prenotazione e cancellazione confermate → notifiche con corso, data e ora;
- errore SNS non impedisce l'invio Telegram e non modifica l'esito della prenotazione;
- notifica che fallisce: la prenotazione resta valida;
- nessun `SNS_TOPIC_ARN`: nessuna email SNS; Telegram resta indipendente;
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

L'invocazione con payload di prenotazione **prenota davvero**; la schedule, se riattivata,
lo farà ogni lunedì, mercoledì e venerdì alle 07:00. Il codice non è idempotente: evita il POST solo quando
lo slot risulta non prenotabile, non perché riconosca una prenotazione esistente. Non
aggiungere altre schedule di prenotazione in parallelo senza averlo reso idempotente. Per
prove innocue usare `{"inspect": true}`. Una prenotazione non confermata non invia una
notifica di conferma; l'esito è riportato nella risposta o nei log della Lambda.
