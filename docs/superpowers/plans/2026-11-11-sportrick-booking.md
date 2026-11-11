---
status: planned
phase: 1
updated: 2026-11-11
---

# Piano: corsi e prenotazioni Sportrick

**Obiettivo:** estendere GymBooking con la consultazione dei corsi e degli impegni e la gestione esplicita di prenotazioni e liste d'attesa, mantenendo la Lambda esistente.

**Architettura proposta:** un piccolo client Python usa la sessione `requests` esistente e restituisce dati strutturati; `lambda_handler` traduce l'evento nella chiamata al client. Le operazioni di lettura e quelle che modificano lo stato hanno azioni distinte. Nessun nuovo servizio AWS, dipendenza o trigger pianificato è necessario in questa fase.

**Alternative:** aggiungere tutte le operazioni direttamente a `app.py` sarebbe il cambiamento iniziale più breve, ma mescolerebbe trasporto, parsing e gestione Lambda; riscrivere il progetto come servizio web aggiungerebbe infrastruttura non richiesta. Si propone invece un modulo client e un adattatore Lambda sottile, conservando distribuzione e credenziali esistenti.

## Evidenze e limiti

- `lambda_function/app.py:39` fissa ID e tipo `Classes` per Sala attrezzi; `:69` prenota tramite `appointmentID`. `:18` non restituisce attualmente il risultato di `book()`.
- `lambda_function/app.py:54` offre già `inspect`; `README.md` documenta che `{}` prenota alle 18:15 a +7 giorni. Conservare entrambi i comportamenti per compatibilità: anche dopo la modifica `{}` continuerà a creare una prenotazione reale.
- Nel portale autenticato, `Booking/ChooseActivity` mostra nove attività con attributi `data-idattivita` e `data-flags`. Otto sono `Classes`; Visita medica ha `Classes, Services`. Gli identificativi vanno trattati come stringhe, perché possono superare la precisione degli interi JavaScript.
- Il calendario Pilates mostrava una lezione completa con lista d'attesa; `Booking` mostrava un impegno con pulsante Cancella. Non sono stati osservati i payload di cancellazione, iscrizione o uscita dalla lista, né il completamento della visita medica. Non presumere che questi flussi usino gli stessi parametri della prenotazione normale.
- I comportamenti descritti dalla [guida ufficiale alla lista d'attesa](https://sportrickhelp.freshdesk.com/it/support/solutions/articles/1000298707-come-faccio-a-registrarmi-in-lista-d-attesa-da-e-commerce-) dipendono dalla configurazione della palestra; una lista può prevedere inserimento automatico o una conferma successiva.

## Contratto proposto

L'interfaccia usa `action` esplicita, `activity_id` e `appointment_id` come stringhe, date `YYYY-MM-DD` e orari locali `HH:MM`. I valori di `activity_flags` provengono dal catalogo, compreso `Classes, Services`; non dedurli dal nome del corso. Esempi indicativi:

| Azione | Evento Lambda | Risultato atteso |
|---|---|---|
| Catalogo | `{"action":"activities"}` | ID, nome, descrizione e flags restituiti dal portale |
| Calendario | `{"action":"schedule","activity_id":"<id>","date":"2026-11-17"}` | slot, ID appuntamento, orario, durata e stato quando il portale li fornisce |
| Impegni | `{"action":"bookings"}` | prenotazioni e, se identificabili, posizioni in lista d'attesa |
| Prenotazione | `{"action":"book","appointment_id":"<id>"}` | esito confermato, rifiutato o incerto |
| Cancellazione | `{"action":"cancel","booking_id":"<id>"}` | esito confermato, rifiutato o incerto |
| Lista d'attesa | `{"action":"waitlist_join","appointment_id":"<id>"}` / `{"action":"waitlist_leave","booking_id":"<id>"}` | esito confermato, rifiutato o incerto |

Gli identificativi e i nomi dei campi relativi a cancellazione e lista d'attesa sono contratti proposti: adattarli alle evidenze raccolte nella fase 1. Se la lista standard richiede una successiva conferma di partecipazione, aggiungere una **azione esplicita distinta** dopo averne verificato il flusso. Nessuna iscrizione in lista implicita quando una prenotazione è piena e nessun pagamento automatico.

## File previsti

- `lambda_function/sportrick.py` — client autenticato, catalogo, calendario, impegni e operazioni confermate dalle verifiche.
- `lambda_function/app.py` — dispatch Lambda, validazione degli eventi e compatibilità dei payload precedenti.
- `tests/test_sportrick.py` — test del client con risposte e fixture anonimizzate.
- `tests/test_app.py` — contratto Lambda, compatibilità e assenza di modifiche per azioni di sola lettura.
- `README.md` — esempi di invocazione, significato degli esiti, vincoli e procedura di verifica/deploy.
- `template.yaml` e `lambda_function/requirements.txt` — invariati salvo necessità verificata; includere il nuovo modulo nel pacchetto di deploy documentato.

## Fase 1 — Verificare il protocollo [PENDING]

- [ ] 1.1 Rilevare in sola lettura richieste e risposte di catalogo, calendario e impegni, per almeno un corso, Sala attrezzi e Visita medica. Registrare metodo, percorso, parametri, tipo di contenuto, campi e casi senza disponibilità; anonimizzare esempi e rimuovere cookie, token, credenziali e dati personali.
- [ ] 1.2 Verificare se il calendario di `Classes, Services` usa lo stesso endpoint e come distingue disponibilità, esaurito, lista d'attesa e prenotazione già esistente. Non inventare uno stato quando il portale non lo espone.
- [ ] 1.3 Analizzare in sicurezza i flussi di prenotazione, cancellazione, ingresso/uscita lista e partecipazione dalla lista: leggere pagina, moduli e codice client senza attivare pulsanti che modificano lo stato. Se un payload o risultato non è verificabile in sola lettura, documentarlo come non verificato e fermare la relativa implementazione fino a un test esplicitamente autorizzato.
- [ ] 1.4 Per Visita medica verificare eventuali requisiti distinti, professionista, dati aggiuntivi e pagamento. Non automatizzare prenotazioni a pagamento o inserimento di dati sanitari senza una richiesta specifica.

## Fase 2 — Client e sola lettura [PENDING]

- [ ] 2.1 Scrivere test con fixture anonime per login valido/non valido, parsing catalogo, flags multipli, ID lunghi, catalogo vuoto e HTML/risposta inattesi. Implementare `list_activities()` con sessione e timeout; conservare esattamente gli ID come stringhe.
- [ ] 2.2 Scrivere test per `get_schedule(activity_id, date)` su `Classes`, `Classes, Services`, giorno vuoto, stato pieno/lista e errore di rete. Ricavare i flags dal catalogo o validarli contro di esso; propagare solo campi e stati effettivamente osservati.
- [ ] 2.3 Scrivere test per `list_bookings()` con zero e più impegni, distinguendo la lista d'attesa solo quando i dati lo consentono. Non esporre informazioni personali non necessarie o segreti in output/log.
- [ ] 2.4 Aggiungere le azioni `activities`, `schedule` e `bookings` al dispatcher: input errati o azioni sconosciute falliscono senza eseguire prenotazioni; i test verificano l'assenza di chiamate di modifica.

## Fase 3 — Operazioni che modificano lo stato [PENDING]

- [ ] 3.1 Testare e implementare `book` sul flusso verificato, usando l'ID dello slot scelto. Distinguere conferma, rifiuto, già prenotato e risposta sconosciuta; non segnalare successo senza conferma del portale.
- [ ] 3.2 Solo dopo 1.3, testare e implementare `cancel`, `waitlist_join` e `waitlist_leave` con gli identificativi corretti. Mantenere esiti separati per prenotazione e lista; non effettuare operazioni alternative senza richiesta esplicita.
- [ ] 3.3 Per timeout o disconnessione dopo l'invio, restituire un esito **incerto**, senza ritentare automaticamente: verificare gli impegni prima di qualsiasi nuovo tentativo. Testare anche doppie invocazioni, slot non trovato e risposta non riconosciuta.
- [ ] 3.4 Mantenere `inspect` e il comportamento legacy senza `action`, con test di regressione. Garantire che ogni nuovo percorso di lettura non possa ricadere nel percorso legacy di prenotazione. Restituire il risultato della prenotazione nel percorso nuovo; non cambiare silenziosamente il contratto legacy.

## Fase 4 — Documentazione e verifica [PENDING]

- [ ] 4.1 Aggiornare README con contratto, esempi per ogni azione, distinzione tra stati, compatibilità `{}`, limiti del portale, test e istruzioni di deploy che includano entrambi i file Python. Non aggiungere segreti o payload personali alle fixture.
- [ ] 4.2 Eseguire `python3 -m unittest discover -s tests -v`; controllare i pacchetti ZIP e il deploy documentato. Le fixture riproducono il comportamento verificato, non lo sostituiscono.
- [ ] 4.3 Eseguire le sole letture reali con credenziali già configurate e confrontare catalogo, un calendario e impegni con il browser. Test reali di prenotazione, cancellazione o lista d'attesa solo su richiesta esplicita, indicando prima attività e conseguenze; verificare lo stato finale nel portale.
- [ ] 4.4 Validare il template SAM se lo strumento è disponibile. L'eventuale pubblicazione AWS è una fase separata, con pacchetto e cambiamenti verificabili; nessuna schedule viene attivata automaticamente.

## Criteri di completamento

I corsi e gli impegni sono leggibili; i tipi osservati sono preservati; ogni modifica di stato supportata ha payload ed esito verificati, test di successo e fallimento, e non viene ritentata alla cieca. Compatibilità legacy e sicurezza delle credenziali sono coperte dai test. Qualsiasi flusso non verificabile viene riportato esplicitamente come non supportato, anziché simulato come completato.
