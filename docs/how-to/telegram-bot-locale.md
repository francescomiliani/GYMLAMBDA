---
title: Bot Telegram locale
type: how-to
status: canonical
owner: gymbooking
last-verified: 2026-11-13
---

# Bot Telegram locale

Il processo gira su Linux o Raspberry Pi con Python 3.9+ e tzdata di sistema. Usa lo stesso token del bot AWS: Telegram consegna gli aggiornamenti al webhook oppure al polling, non a entrambi. Il dispositivo deve rimanere acceso e connesso. L'automazione locale parte disattivata e non recupera le esecuzioni perse. La prenotazione programmata usa il flusso legacy: sala attrezzi alle 18:15, per il giorno a sette giorni dalla data di esecuzione.

## Installazione

Dalla directory del repository:

```sh
python3 -m venv .venv
.venv/bin/pip install -r lambda_function/requirements.txt
mkdir -p ~/.config/gymbooking ~/.local/share/gymbooking
chmod 700 ~/.config/gymbooking ~/.local/share/gymbooking
```

Creare `~/.config/gymbooking/config.local.json`, proteggendolo con `chmod 600`, con questi campi (tutti stringhe). Sostituire i segnaposto senza condividere il file:

```json
{
  "telegram_token": "TOKEN_BOT",
  "allowed_user_id": "ID_UTENTE_TELEGRAM",
  "allowed_chat_id": "ID_CHAT_PRIVATA",
  "sportrick_username": "USERNAME",
  "sportrick_password": "PASSWORD"
}
```

L'ID utente e quello della chat privata devono corrispondere ai valori Telegram effettivi. Il bot accetta solo aggiornamenti che rispettano entrambi e che provengono da una chat privata. Non salvare il file nel repository; `config.local.json` è comunque ignorato da Git.

## Passaggio dal webhook

Prima verificare e fermare eventuali altri consumer e automazioni AWS: il polling non disattiva le schedule AWS. Controllare webhook, schedule, fuso e retry in AWS prima di modificarli. L'avvio locale si arresta se Telegram segnala un webhook e non lo rimuove automaticamente.

Quando si è pronti a interrompere la ricezione webhook AWS, rimuoverlo esplicitamente, senza eliminare gli aggiornamenti in attesa:

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import requests
config = json.loads((Path.home() / '.config/gymbooking/config.local.json').read_text())
response = requests.post('https://api.telegram.org/bot' + config['telegram_token'] + '/deleteWebhook',
                         json={'drop_pending_updates': False}, timeout=15)
response.raise_for_status()
print('Webhook rimosso:', response.json().get('ok') is True)
PY
```

Gli aggiornamenti ancora presenti su Telegram potranno essere elaborati dal processo locale. Non usare questo comando se non si desidera il passaggio. Per tornare ad AWS, fermare il processo locale e seguire la procedura webhook in `telegram-bot-deploy.md`.

## Avvio e pianificazione

```sh
.venv/bin/python -m lambda_function.local_bot \
  --config "$HOME/.config/gymbooking/config.local.json" \
  --database "$HOME/.local/share/gymbooking/bot.sqlite3"
```

Un secondo processo sullo stesso database viene rifiutato. Aprire la chat privata e usare `/start`. `/automation` mostra stato e orario; `/weekly MON,WED 08:30` imposta giorni e ora nel fuso Europe/Rome, con conferma, quando la pianificazione è disattivata. `/enable` attiva e `/disable` sospende, entrambi con conferma. Per cambiare l'ora: disattivare, modificare e riattivare. I pulsanti e i comandi per prenotazioni, cancellazioni e lista d'attesa seguono i flussi del bot esistente.

L'esecuzione viene registrata prima della chiamata al portale; dopo un arresto o timeout non viene ripetuta automaticamente. Un avviso di esito incerto richiede di verificare `/bookings` prima di qualsiasi nuova operazione. La risposta Telegram indica accettazione della richiesta, non lettura del messaggio. Controllare anche i log se una notifica non arriva.

## Avvio automatico con systemd

Creare `~/.config/systemd/user/gymbot.service`, sostituendo entrambi i percorsi assoluti:

```ini
[Unit]
Description=GymBooking Telegram locale
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/percorso/assoluto/GYMLAMBDA
ExecStart=/percorso/assoluto/GYMLAMBDA/.venv/bin/python -m lambda_function.local_bot --config /home/utente/.config/gymbooking/config.local.json --database /home/utente/.local/share/gymbooking/bot.sqlite3
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=default.target
```

Eseguire `systemctl --user daemon-reload` e `systemctl --user enable --now gymbot.service`. Per l'avvio senza sessione aperta può essere necessario `loginctl enable-linger "$USER"`. Stato e log: `systemctl --user status gymbot.service` e `journalctl --user -u gymbot.service -f`. Fermare con `systemctl --user stop gymbot.service`.

## Backup

Fermare il servizio prima di copiare `bot.sqlite3` in una destinazione privata e protetta. Conservare separatamente il file di configurazione: contiene credenziali e token. Ripristinare il database solo a servizio fermo; un backup vecchio può non contenere operazioni recenti e non garantisce la deduplicazione di quelle successive al backup. Dopo un ripristino verificare gli impegni e la pianificazione prima di riattivarla.
