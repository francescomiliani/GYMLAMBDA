# Skill Claude `gymbooking`: gestione prenotazioni Sportrick via HTTP diretto

## Context
Utente vuole skill Claude Code che permetta a Claude di usare il portale Sportrick
(`https://ecomm.sportrick.com/replywellnessTO`) e gestire prenotazioni **chiamando
direttamente le API HTTP** (curl), senza Lambda e senza passare dal codice Python.
La skill documenta login, endpoint, parametri, risposte, regole di sicurezza. Credenziali
lette da `lambda_function/credentials.json` (gitignored). Notifica solo Telegram.
Fonte di verità degli endpoint: `lambda_function/sportrick.py` (`SportrickClient`).

## File
1. **Nuovo** `docs/superpowers/plans/2026-11-14-claude-skill-gymbooking.md`: copia di questo
   piano (primo passo dell'esecuzione), stile degli altri piani in quella cartella.
2. **Nuovo** `.claude/skills/gymbooking/SKILL.md` (versionato nel repo).
3. **Nuovo** `tests/test_skill.py` (vedi sotto).
4. **Modifica** `AGENTS.md`, sezione "Documentazione del repository": una riga che punta alla skill.
5. **Modifica** `docs/index.md` se elenca i piani: aggiungere il nuovo.

Niente script helper (YAGNI: curl + python3 stdlib bastano).

## Test `tests/test_skill.py` (unittest stdlib, nessuna rete)
Scopo: impedire che la skill si disallinei da `SportrickClient` o contenga segreti.
- Frontmatter: inizia con `---`, ha `name: gymbooking` e `description:` non vuota.
- **Endpoint allineati in entrambe le direzioni**: estrarre con regex i path
  `/Account/...` e `/Booking/...` da `lambda_function/sportrick.py` (sorgente) e dalla
  SKILL.md; i due insiemi devono coincidere (esclusa `BookActivity`, assente nel client).
- **Parametri form**: per ogni endpoint di modifica, i nomi campo usati in `sportrick.py`
  (`appointmentID`, `id`, `bookingId`, `appointmentId`, `__RequestVerificationToken`,
  `currentDate`, `activityID`, `skillID`, `activityFlags`, `getForCatchUp`) compaiono nella skill.
- **Costanti di esito/precondizione** presenti: `Prenotazione confermata con successo.`,
  `Prenota`, `btnConfirmAppointmentBooking`, `btnAggiungiListaAttesa`, `idImpegno`,
  `seleziona-attivita`, `data-idattivita`, `data-flags`.
- **BASE** uguale a `sportrick.BASE`.
- **Nessun segreto**: se esiste `lambda_function/credentials.json`, né username né password
  compaiono nella skill; se esiste `~/.config/gymbooking/telegram-token`, il token non compare.
  Nessun pattern tipo token Telegram (`\d{8,}:[A-Za-z0-9_-]{30,}`).
- Regole di sicurezza presenti: menzione di conferma esplicita prima delle modifiche e
  divieto di ritentare su esito incerto (match su parole chiave).
TDD: scrivere prima il test (fallisce: file mancante), poi la skill fino a verde.

## Contenuto SKILL.md
Frontmatter: `name: gymbooking`, `description:` trigger IT/EN (palestra, Sportrick,
Reply Wellness, prenota/cancella lezione, corsi, calendario, impegni, lista d'attesa).

Sezioni:
1. **Regole di sicurezza** (da AGENTS.md):
   - book/cancel/waitlist_* modificano davvero il portale: chiedere conferma esplicita
     all'utente (corso, data, ora, ID) prima di ogni POST di modifica.
   - Mai stampare password, cookie, token Telegram; mai `cat` di credentials.json o cookie jar;
     `curl -s` senza `-v`/`-i` sui POST di login.
   - Esito incerto (timeout, risposta non riconosciuta) → **non ritentare**: rileggere impegni.
   - ID sempre stringhe; parsing JSON con `python3 -c 'import json…'` (int a precisione
     arbitraria), non jq per gli ID lunghi.
   - Solo `Type == "class"`; servizi (`service`, es. visita medica) non supportati.
2. **Setup sessione**: `BASE`, cookie jar `$CJ` nello scratchpad (`umask 077`), rimosso a fine.
3. **Login** (`SportrickClient.login`):
   - `GET $BASE/Account/Login?returnUrl=%2FreplywellnessTO` con `-c $CJ`; estrarre
     `__RequestVerificationToken` dall'input hidden (python3 regex/HTMLParser).
   - `POST` stesso URL, form `UserName`, `Password`, `__RequestVerificationToken`,
     header `Referer`, senza seguire redirect. Credenziali via
     `--data-urlencode "Password@<(python3 -c … )"` per non metterle in output.
   - Successo = HTTP 302/303 con `Location` che non contiene `Account/Login`
     (`-w '%{http_code} %{redirect_url}'`, `-o /dev/null`).
   - Sessione scaduta: qualunque GET che finisce su `/Account/Login` → rifare login.
4. **Endpoint lettura** (tabella metodo, path, parametri, campi utili):
   - `GET /Booking/ChooseActivity` → HTML; attività = `<a class="seleziona-attivita"
     data-idattivita data-flags>`, nome in `h3.list-group-item-heading`, descrizione in
     `p.list-group-item-text`. Snippet python per estrarle.
   - `POST /Booking/GetActivitySchedule` form `currentDate=YYYY-MM-DD`, `activityID`,
     `skillID=0`, `activityFlags=<data-flags>`, `getForCatchUp=false` → JSON
     `{status:"OK", data:[…]}`; campi `AppointmentId, TimeStart, TimeDuration, Notes,
     Status, Type, EventName, ServiceId, StartDateTime, ParentCourseId`.
   - `GET /Booking/GetCustomerAppointments` → `{status:"OK", data:[…]}`; campi `id`
     (= booking_id), `appointmentId, activityId, DateStart, TimeStart, TimeDuration,
     description, isOnWaitlist, isService, canDelete, canDeleteFromWaitlist,
     canMoveToAttendants`.
5. **Endpoint modifica** (ognuno con precondizioni da verificare prima):
   - **Prenota**: slot con `Type=="class"`, `Status=="Available"`, `Notes=="Prenota"`;
     poi `GET /Booking/ConfirmActivityBooking?appointmentID=…` deve contenere hidden
     `appointmentID` uguale, `btnConfirmAppointmentBooking`, nessun input extra
     (select/textarea/input non hidden) → altrimenti stop. Poi
     `POST /Booking/BookAppointment` form `appointmentID`. Confermato solo se
     `message == "Prenotazione confermata con successo."`; `status=="ERROR"` → rifiutato;
     altro/timeout → incerto.
   - **Cancella**: impegno in `GetCustomerAppointments` con `canDelete==true` e
     `isOnWaitlist!=true` → `POST /Booking/DeleteBooking` form `id=<booking_id>`.
   - **Lista d'attesa, entra**: `GET ConfirmActivityBooking?appointmentID=…` deve contenere
     `btnAggiungiListaAttesa` e hidden `idImpegno` → `POST /Booking/ConfirmAddToWaitList`
     form `appointmentId=<idImpegno>`.
   - **Esci**: `POST /Booking/RemoveFromWaitlist` form `bookingId`.
   - **Conferma da attesa**: `POST /Booking/MoveToAttendants` form `appointmentId=<booking_id>`.
   - Esito generico mutate: `status` `OK`→confermato, `ERROR`→rifiutato, altro→incerto;
     messaggio in `message` o `error_message`.
6. **Regole portale**: finestra di prenotazione ~7 giorni (a 9 giorni "Prenotazione non
   consentita"); date nel fuso `Europe/Rome`; lezione piena = `Notes` ≠ `Prenota` →
   proporre lista d'attesa; lista d'attesa può prenotare automaticamente/consumare crediti.
7. **Notifica Telegram** (solo dopo prenotazione/cancellazione confermata, se esistono
   `~/.config/gymbooking/telegram-token` e `telegram-chat-id`):
   `POST https://api.telegram.org/bot<token>/sendMessage` JSON `{chat_id, text}` con token
   letto da file dentro il comando (mai in chiaro); testo
   `"<data> <ora> - <corso> - prenotazione confermata"` / `cancellazione confermata`.
   `ok:true` = accettato, non letto. Errore notifica non cambia l'esito.
8. **Flussi tipici**: "cosa ho prenotato", "prenota X giovedì alle 18:30"
   (attività → calendario → conferma utente → prenota → rilettura impegni → Telegram),
   "cancella", "mettimi in lista d'attesa". Report finale: confermato/rifiutato/incerto.

## Verifica
- Solo letture reali (autorizzate implicitamente come sola lettura): seguire la skill passo
  passo con curl → login OK (302), elenco attività, calendario di una data a +3 giorni,
  impegni. Nessun POST di modifica senza richiesta esplicita dell'utente.
- Controllare che nessun output contenga password/cookie/token.
- `python3 -m unittest discover -s tests -v`: tutti verdi, incluso `test_skill`.
- Prova di regressione del test: rimuovere temporaneamente un endpoint dalla skill → il test
  deve fallire; ripristinare.
- Pulizia cookie jar nello scratchpad.
