# Bot Telegram per GymBooking

## Obiettivo e vincoli

Bot privato su AWS per consultare e gestire prenotazioni e liste d'attesa, e attivare, disattivare o riprogrammare l'automazione per giorni della settimana e ora di Roma. Riutilizzare le azioni Sportrick esistenti; non implementare prenotazioni di servizi non supportati. La pianificazione della lezione e quella dell'esecuzione sono distinte.

Solo servizi con quota Free Tier disponibile; verificare consumi e idoneità dell'account prima del deploy. Il Free Tier non garantisce un tetto di spesa. Nessun deploy, modifica IAM o schedule senza autorizzazione esplicita e ispezione preventiva delle risorse reali.

## Architettura proposta

- Lambda dedicata con Function URL per webhook Telegram; autenticazione tramite secret e verifica utente e chat privata.
- Worker Lambda separato senza endpoint pubblico per operazioni lente; riuso diretto di `SportrickClient`, senza instradare eventi Telegram all'handler legacy o notificare prima di salvare l'esito.
- Stato persistente minimo per conferme con scadenza e deduplicazione, con valutazione di DynamoDB e della relativa quota gratuita prima del deploy.
- EventBridge Scheduler esistente, aggiornato preservando configurazione, target, ruolo, payload, fuso e retry. Cambiare giorni e ora non abilita una schedule disabilitata.
- Niente server sempre acceso, gateway superflui, framework Telegram o servizi a costo fisso.

## Funzioni

1. Menu: prenotazioni, prenota, lista d'attesa, automazione e aiuto.
2. Visualizzare impegni e cancellare solo quelli consentiti, previa conferma.
3. Selezionare attività, data e slot, verificare nuovamente la disponibilità e confermare prima della prenotazione.
4. Gestire iscrizione, uscita e conferma della lista d'attesa con avviso delle possibili conseguenze.
5. Visualizzare stato, giorni, ora, fuso, payload e anticipo; modificare una programmazione settimanale con più giorni e un unico orario.

## Sicurezza e affidabilità

- Nessun segreto o dato di autenticazione nei log o nel repository; autorizzare solo utente e chat privata configurati.
- Verificare callback e usare conferme temporanee consumate atomicamente; respingere pulsanti scaduti e duplicati.
- Nessun retry automatico delle operazioni Sportrick con esito ambiguo; invitare a verificare gli impegni. Non promettere exactly-once sul portale.
- Registrare atomicamente conferma e lavoro `pending`, quindi invocare il worker. Una riconsegna può riavviare solo un lavoro ancora `pending`; il worker lo acquisisce atomicamente come `running`. Un lavoro interrotto in `running` resta incerto, mai ripetuto automaticamente. Salvare il risultato prima delle notifiche.
- Le conferme contengono token opachi, con scadenza verificata esplicitamente; il TTL elimina dati vecchi ma non applica la scadenza. Verificare gli impegni e le capacità di lista d'attesa prima delle mutazioni.
- Esaminare sia retry Scheduler sia retry asincroni Lambda prima di consentire l'attivazione: il flusso automatico esistente non è idempotente.
- Non abilitare la schedule finché non è protetta ogni occorrenza con identificativo stabile e data calcolata dall'istante pianificato. Un eventuale cambio di payload o target richiede autorizzazione esplicita separata.
- Limitare IAM alle risorse necessarie; l'endpoint pubblico può comunque consumare quote anche per richieste non autorizzate.

## Fasi e responsabilità

1. **Explorer:** mappare API, chiamanti, test, packaging e limiti con riferimenti ai file, senza esporre credenziali.
2. **Designer:** definire menu, pulsanti e flussi Telegram, inclusi annullamento, errori e conferme.
3. **Oracle:** verificare architettura webhook/worker, stato persistente, deduplicazione, IAM e retry.
4. **Orchestrator:** verificare in sola lettura risorse AWS e idoneità Free Tier, inclusi DynamoDB, log, trasferimenti e consumi condivisi; bloccare il deploy quando la verifica non è conclusiva.
5. **Planner:** consolidare contratti, file e criteri di accettazione; sola lettura.
6. **Orchestrator:** implementare integrazioni sensibili, autenticazione, stato e gestione Scheduler.
7. **Fixer:** implementare parti circoscritte e test su file distinti; nessuna modifica AWS.
8. **Oracle:** revisione indipendente delle modifiche e dei failure mode.
9. **Orchestrator:** riconciliare, eseguire suite e documentare risultati; preparare modifiche AWS per approvazione.
10. **Orchestrator:** solo previa approvazione, deploy controllato e verifiche di sola lettura; operazioni reali solo se autorizzate. Observer facoltativo per verifica visuale Telegram.

Concorrenza massima del preset: due agenti. Evitare writer contemporanei sugli stessi file.

## Verifica

Comando finale: `python3 -m unittest discover -s tests -v`.

Test con mock: accessi negati e secret errato, ID stringa, compatibilità payload precedenti, callback duplicate/scadute e concorrenza, conferme, esiti incerti senza retry, fallimento notifiche, preservazione delle impostazioni Scheduler, giorni/ore/fuso e packaging. Nessun test unitario deve effettuare prenotazioni o inviare notifiche reali.

Prima del deploy: verificare configurazione e costi effettivi, lasciare la schedule invariata, predisporre rollback del webhook e delle risorse create. Dopo il deploy: verificare prima menu e letture; modifiche al portale solo con autorizzazione specifica.

## Stato dell'implementazione

Sono presenti codice locale, test con mock e template SAM separato. Il percorso guidato usa conferme opache e una guardia dedicata è predisposta per identificare le occorrenze dello Scheduler. Non è collegata alla schedule esistente: finché target, IAM, payload e retry reali non sono modificati e verificati con autorizzazione, la riattivazione dal bot resta bloccata. La guardia privilegia l'assenza di ripetizioni: un'interruzione dopo il claim può causare una prenotazione mancata o un esito incerto.

La Function URL pubblica può consumare quote anche con richieste non autorizzate; le quote AWS non costituiscono un tetto di spesa. Le modifiche al codice non distribuiscono il bot e non cambiano la configurazione AWS.
