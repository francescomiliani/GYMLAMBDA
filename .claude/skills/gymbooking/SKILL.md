---
name: gymbooking
description: Gestisce la palestra Reply Wellness sul portale Sportrick con richieste HTTP dirette (curl), senza Lambda. Usare per elencare corsi, vedere il calendario, controllare gli impegni, prenotare o cancellare una lezione, entrare o uscire dalla lista d'attesa. Trigger - palestra, gym, Sportrick, Reply Wellness, prenota/cancella lezione, "cosa ho prenotato", lista d'attesa. Use for booking gym classes on Sportrick.
---

# GymBooking via HTTP

Portale: `https://ecomm.sportrick.com/replywellnessTO`. Fonte di verità degli endpoint:
`lambda_function/sportrick.py` (`SportrickClient`); se il portale cambia, leggerla e aggiornare questa skill.

## Regole di sicurezza (obbligatorie)

1. **Modifiche reali.** Prenotare, cancellare e le azioni sulla lista d'attesa modificano davvero
   il portale. Prima di ogni POST di modifica serve **conferma esplicita** dell'utente, mostrando
   corso, data, ora e ID. Le letture non richiedono conferma.
2. **Esito incerto: non ritentare.** Timeout, risposta non JSON o non riconosciuta: il portale
   potrebbe aver modificato lo stato. Rileggere gli impegni (`GetCustomerAppointments`) e riferire
   all'utente; mai ripetere il POST in automatico.
3. **Segreti.** Non stampare né committare password, cookie, token Telegram. Non fare `cat` di
   `lambda_function/credentials.json`, del cookie jar o dei file in `~/.config/gymbooking/`.
   Niente `-v`/`-i` sui comandi di login. Leggere i segreti dentro il comando che li usa.
4. **ID = stringhe.** Superano la precisione dei numeri JavaScript e di `jq`: estrarli con
   `python3 -c 'import json,sys; ...'`, mai con `jq` e mai convertirli in numero.
5. **Solo lezioni.** Slot con `Type == "class"`. I servizi (`service`, es. visita medica) non sono
   supportati: non prenotarli. Se il modulo di conferma chiede altre scelte, fermarsi.
6. **Fuso e date.** Date `YYYY-MM-DD` nel fuso `Europe/Rome` (`TZ=Europe/Rome date +%F`).
   Il portale accetta prenotazioni solo entro circa 7 giorni (a 9 giorni: "Prenotazione non consentita").

## Sessione

Usare lo scratchpad per il cookie jar e rimuoverlo a fine lavoro.

```bash
BASE=https://ecomm.sportrick.com/replywellnessTO
CJ=$(umask 077; mktemp)    # cookie jar; rm -f "$CJ" alla fine
CRED=lambda_function/credentials.json   # chiavi UserName, Password (file ignorato da Git)
```

### Login

```bash
F=$(umask 077; mktemp -d)   # file temporanei con i segreti, rimossi subito dopo il login
python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))
for k in ("UserName","Password"): open(sys.argv[2]+"/"+k,"w").write(c[k])' "$CRED" "$F"
URL="$BASE/Account/Login?returnUrl=%2FreplywellnessTO"
TOKEN=$(curl -s -c "$CJ" -b "$CJ" "$URL" | python3 -c 'import re,sys; m=re.search(r"name=\"__RequestVerificationToken\"[^>]*value=\"([^\"]*)\"|value=\"([^\"]*)\"[^>]*name=\"__RequestVerificationToken\"", sys.stdin.read()); print(m and (m.group(1) or m.group(2)) or "")')
test -n "$TOKEN" || echo "pagina di login non valida"
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -c "$CJ" -b "$CJ" -e "$URL" "$URL" \
  --data-urlencode "UserName@$F/UserName" --data-urlencode "Password@$F/Password" \
  --data-urlencode "__RequestVerificationToken=$TOKEN"
rm -rf "$F"
```

Riuscito solo se HTTP **302/303** e il redirect **non** contiene `Account/Login` (un 200 significa
credenziali o token rifiutati). Qualunque richiesta che finisce su `/Account/Login` significa
sessione scaduta: rifare il login. Nota: `--data-urlencode "nome@file"` legge il valore dal file;
non usare `<(...)` tra virgolette, la shell non lo espande.

Tutte le richieste seguenti usano `-s -b "$CJ" --max-time 15`.

## Letture

| Cosa | Richiesta | Risposta |
|---|---|---|
| Attività | `GET $BASE/Booking/ChooseActivity` | HTML: `<a class="seleziona-attivita" data-idattivita=ID data-flags=FLAGS>`, nome in `h3.list-group-item-heading`, descrizione in `p.list-group-item-text` |
| Calendario | `POST $BASE/Booking/GetActivitySchedule` form `currentDate=YYYY-MM-DD`, `activityID=ID`, `skillID=0`, `activityFlags=FLAGS`, `getForCatchUp=false` | JSON `{status:"OK", data:[...]}`; per slot `AppointmentId`, `TimeStart`, `TimeDuration`, `Notes`, `Status`, `Type`, `EventName`, `ServiceId`, `StartDateTime`, `ParentCourseId` |
| Impegni | `GET $BASE/Booking/GetCustomerAppointments` | JSON `{status:"OK", data:[...]}`; per impegno `id` (= booking_id), `appointmentId`, `activityId`, `DateStart`, `TimeStart`, `TimeDuration`, `description`, `isOnWaitlist`, `isService`, `canDelete`, `canDeleteFromWaitlist`, `canMoveToAttendants` |

Elenco attività (`data-flags` serve al calendario, usare quello dell'attività scelta):

```bash
curl -s -b "$CJ" "$BASE/Booking/ChooseActivity" | python3 -c '
import re,sys
for m in re.finditer(r"<a[^>]*seleziona-attivita[^>]*>.*?</a>", sys.stdin.read(), re.S):
    a=m.group(0); g=lambda p: re.search(p,a)
    print(g(r"data-idattivita=\"(\d+)\"").group(1), g(r"data-flags=\"([^\"]*)\"").group(1),
          " ".join(re.sub(r"<[^>]+>"," ",g(r"list-group-item-heading[^>]*>(.*?)</h3>").group(1)).split()))'
```

Calendario e impegni: salvare la risposta e stampare con python (`json.load`), mai `jq` sugli ID.
Una lezione è prenotabile solo con `Notes == "Prenota"` e `Status == "Available"`: le lezioni piene
compaiono comunque nel calendario.

## Modifiche (solo dopo conferma esplicita)

Esito di ogni POST: JSON con `status` `OK` = confermato, `ERROR` = rifiutato (testo in `message` o
`error_message`), altro/timeout = **incerto** (vedi regola 2).

### Prenotare una lezione

1. Dal calendario: slot con `Type == "class"`, `Status == "Available"`, `Notes == "Prenota"`.
2. `GET $BASE/Booking/ConfirmActivityBooking?appointmentID=<AppointmentId>`. La pagina deve contenere
   un hidden `appointmentID` uguale all'ID, il pulsante `btnConfirmAppointmentBooking` e **nessun**
   altro campo (select, textarea, input non hidden), altrimenti fermarsi e dirlo all'utente.
3. Dopo la conferma dell'utente: `POST $BASE/Booking/BookAppointment` form `appointmentID=<AppointmentId>`.
   **Confermato solo** se `message == "Prenotazione confermata con successo."`. Se `status == "ERROR"`:
   rifiutato. Altrimenti incerto.
4. Rileggere gli impegni per verificare, poi notificare (sezione Notifiche).

### Cancellare

Trovare l'impegno in `GetCustomerAppointments`: serve `canDelete == true` e `isOnWaitlist` non vero,
con `description`, `DateStart`, `TimeStart` presenti. Dopo conferma:
`POST $BASE/Booking/DeleteBooking` form `id=<id impegno>`. Rileggere gli impegni, poi notificare.

### Lista d'attesa

La lista d'attesa può prenotare automaticamente o consumare crediti, secondo le regole della palestra: dirlo all'utente.

- **Entrare** (lezione piena, `Notes` diverso da `Prenota`): `GET $BASE/Booking/ConfirmActivityBooking?appointmentID=<AppointmentId>`;
  la pagina deve contenere `btnAggiungiListaAttesa` e un hidden `idImpegno`. Poi
  `POST $BASE/Booking/ConfirmAddToWaitList` form `appointmentId=<idImpegno>` (non l'ID dello slot).
- **Uscire**: `POST $BASE/Booking/RemoveFromWaitlist` form `bookingId=<id impegno>`.
- **Confermare il posto** (`canMoveToAttendants`): `POST $BASE/Booking/MoveToAttendants` form `appointmentId=<id impegno>`
  (non ancora provato dal vivo: serve un posto liberato).

Dopo l'ingresso l'impegno compare in `GetCustomerAppointments` con `isOnWaitlist == true` e
`canDeleteFromWaitlist == true`; usare l'`id` letto lì per uscire e rileggere gli impegni per verificare.
Provato dal vivo: ingresso `OK` ("Aggiunto alla lista d'attesa!"), uscita `OK`.

## Notifiche (Telegram + SNS)

Dopo ogni operazione **confermata** (prenotazione, cancellazione, ingresso o uscita dalla lista
d'attesa), mai prima della verifica negli impegni. Due canali indipendenti:

- **Telegram**: se esistono `~/.config/gymbooking/telegram-token` e `~/.config/gymbooking/telegram-chat-id`.
- **SNS** (email agli iscritti del topic): `aws sns publish` con profilo `aws-personale`, regione
  `eu-north-1`. Se la sessione AWS è scaduta (`aws login --profile aws-personale`, lo lancia l'utente),
  il canale risulta `failed`: riferirlo, senza bloccare nulla.

Il **Subject SNS è solo ASCII** (le emoji lo fanno rifiutare): le emoji stanno nel corpo del messaggio.
Il token resta nel comando, mai nell'output. Un errore di notifica non cambia l'esito dell'operazione.
`accepted` significa accettazione dal servizio, non lettura o consegna.

```bash
notify() {  # uso: notify book|cancel|wl_join|wl_leave YYYY-MM-DD HH:MM "CORSO"
python3 - "$@" <<'PY'
import json, subprocess, sys, urllib.request
from pathlib import Path
kind, date, time, course = sys.argv[1:5]
SUBJECT = {'book': 'prenotazione confermata', 'cancel': 'cancellazione confermata',
           'wl_join': "in lista d'attesa", 'wl_leave': "uscito dalla lista d'attesa"}
HEAD = {'book': '🎉 Prenotazione confermata!', 'cancel': '🗑️ Cancellazione confermata',
        'wl_join': "⏳ Sei in lista d'attesa!", 'wl_leave': "👋 Uscito dalla lista d'attesa"}
TAIL = {'book': 'Ci vediamo in palestra, si suda! 💪🔥', 'cancel': 'Sarà per la prossima volta! 😅',
        'wl_join': 'Dita incrociate 🤞🍀', 'wl_leave': 'Nessun rimpianto 😌'}
text = f'{HEAD[kind]}\n🏋️ {course}\n📅 {date}   ⏰ {time}\n{TAIL[kind]}'
cfg = Path.home() / '.config' / 'gymbooking'
if (cfg / 'telegram-token').is_file() and (cfg / 'telegram-chat-id').is_file():
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{(cfg / 'telegram-token').read_text().strip()}/sendMessage",
            json.dumps({'chat_id': (cfg / 'telegram-chat-id').read_text().strip(), 'text': text}).encode(),
            {'Content-Type': 'application/json'})
        print('telegram:', 'accepted' if json.load(urllib.request.urlopen(req, timeout=15)).get('ok') is True else 'failed')
    except Exception:  # l'eccezione può contenere il token: non stamparla
        print('telegram: failed')
else:
    print('telegram: skipped')
try:
    subprocess.run(['aws', 'sns', 'publish', '--topic-arn', 'arn:aws:sns:eu-north-1:163876584948:GymBooking-notifiche',
                    '--subject', f'GymBooking: {SUBJECT[kind]}', '--message', text,
                    '--region', 'eu-north-1', '--profile', 'aws-personale'],
                   check=True, capture_output=True, timeout=30)
    print('sns: accepted')
except Exception:
    print('sns: failed')
PY
}
```

Messaggi (stessi per entrambi i canali, corso/data/ora dall'impegno confermato):

| Tipo | Testo |
|---|---|
| `book` | `🎉 Prenotazione confermata!` / `🏋️ CORSO` / `📅 DATA   ⏰ ORA` / `Ci vediamo in palestra, si suda! 💪🔥` |
| `cancel` | `🗑️ Cancellazione confermata` / ... / `Sarà per la prossima volta! 😅` |
| `wl_join` | `⏳ Sei in lista d'attesa!` / ... / `Dita incrociate 🤞🍀` |
| `wl_leave` | `👋 Uscito dalla lista d'attesa` / ... / `Nessun rimpianto 😌` |

## Flussi tipici

- **"Cosa ho prenotato?"** login, impegni, tabella (data, ora, corso, lista d'attesa).
- **"Prenota X giovedì alle 18:30"** login, attività, calendario del giorno, scegliere lo slot,
  chiedere conferma, prenotare, rileggere impegni, notificare, riferire.
- **"Cancella ..."** impegni, individuare l'impegno, chiedere conferma, cancellare, verificare, notificare.
- **Lezione piena** proporre la lista d'attesa, mai insistere con `BookAppointment`.
- **Notificare**: `notify book 2026-11-19 18:30 "PILATES"` (tipi: `book`, `cancel`, `wl_join`, `wl_leave`).

Report finale sempre con l'esito: confermato, rifiutato o incerto. A fine lavoro `rm -f "$CJ"`.
