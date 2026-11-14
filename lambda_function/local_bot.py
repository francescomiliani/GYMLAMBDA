"""Telegram long polling and local scheduling, without AWS services."""
import argparse
import datetime
import json
import logging
import os
import time
from zoneinfo import ZoneInfo

import requests

from . import app, telegram_bot, telegram_worker
from .bot_scheduler import fingerprint
from .local_store import LocalStore, StoreConflict

ROME = ZoneInfo('Europe/Rome')
DAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
LOG = logging.getLogger('gymbot')


class LocalScheduler:
    def __init__(self, store):
        self.store = store

    def get_schedule(self):
        return self.store.get('schedule') or {'State': 'DISABLED', 'ScheduleExpression': 'non configurato',
                                               'ScheduleExpressionTimezone': 'Europe/Rome', 'days': [], 'hour': 0, 'minute': 0}

    def _save(self, snapshot, changes):
        if fingerprint(snapshot) != fingerprint(self.get_schedule()):
            raise ValueError('Programmazione cambiata')
        snapshot = dict(snapshot, **changes)
        with self.store.db:
            self.store.put('schedule', snapshot)
        return snapshot

    def set_weekly_time(self, snapshot, days, hour, minute):
        if (snapshot['State'] != 'DISABLED' or not days or len(set(days)) != len(days)
                or any(day not in DAYS for day in days) or type(hour) is not int or not 0 <= hour <= 23
                or type(minute) is not int or not 0 <= minute <= 59):
            raise ValueError('Programmazione non valida')
        days = [day for day in DAYS if day in days]
        return self._save(snapshot, {'days': days, 'hour': hour, 'minute': minute,
                                     'ScheduleExpression': f'{",".join(days)} {hour:02d}:{minute:02d}'})

    def enable(self, snapshot):
        if not snapshot['days']:
            raise ValueError('Configurare prima giorni e ora')
        return self._save(snapshot, {'State': 'ENABLED'})

    def disable(self, snapshot, *, confirmed=False):
        if not confirmed:
            raise ValueError('Conferma richiesta')
        return self._save(snapshot, {'State': 'DISABLED'})

    def reserve_due(self, now, started):
        snapshot = self.get_schedule()
        minute = now.replace(second=0, microsecond=0)
        if (snapshot['State'] != 'ENABLED' or DAYS[now.weekday()] not in snapshot['days']
                or (now.hour, now.minute) != (snapshot['hour'], snapshot['minute']) or minute <= started):
            return False
        key = 'scheduled#' + now.date().isoformat()
        with self.store.db:
            if self.store.get(key):
                return False
            self.store.put(key, {'status': 'running', 'scheduled_at': minute.isoformat()})
        return True


class LocalBot:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.scheduler = LocalScheduler(store)
        self.token = config['telegram_token']
        self.user = str(config['allowed_user_id'])
        self.chat = str(config['allowed_chat_id'])
        self.started = datetime.datetime.now(ROME)
        secrets = {'SPORTRICK_USERNAME': config['sportrick_username'], 'SPORTRICK_PASSWORD': config['sportrick_password']}
        telegram_worker.get_secret = secrets.get
        telegram_bot._store = lambda: self.store
        telegram_bot._scheduler = lambda: self.scheduler
        telegram_bot._dispatch = self.dispatch
        telegram_bot._cancel_confirmation = lambda store, token, owner: store.cancel_confirmation(token, owner)
        telegram_bot._async_reads = lambda: False

    def api(self, method, payload, timeout=15):
        try:
            response = requests.post(f'https://api.telegram.org/bot{self.token}/{method}', json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get('ok') is not True:
                raise ValueError('Telegram response invalid')
            return data.get('result')
        except (requests.RequestException, ValueError, TypeError):
            raise RuntimeError('Richiesta Telegram fallita') from None

    def check_webhook(self):
        result = self.api('getWebhookInfo', {})
        if not isinstance(result, dict) or not isinstance(result.get('url'), str):
            raise RuntimeError('Risposta webhook non valida')
        if result.get('url'):
            raise RuntimeError('Webhook attivo: seguire la procedura di passaggio al polling; nessuna modifica effettuata')

    def warn_unfinished(self):
        rows = self.store.db.execute("SELECT value FROM state WHERE key LIKE 'scheduled#%' OR key LIKE 'job#%'")
        if any(json.loads(row[0]).get('status') in ('running', 'uncertain') for row in rows):
            LOG.warning('Operazioni precedenti con esito incerto: verifica gli impegni')
            try:
                telegram_bot._send(self.token, self.chat, '⚠️ Operazioni precedenti con esito incerto: verifica gli impegni.')
            except telegram_bot.TelegramError:
                LOG.warning('Avviso Telegram non accettato')

    def dispatch(self, job_id):
        result = telegram_worker.run_job(job_id, self.store)
        try:
            telegram_bot._send(self.token, self.chat, telegram_worker.outcome_text(result))
        except telegram_bot.TelegramError:
            LOG.warning('Notifica Telegram non accettata; esito salvato nel database')

    def handle(self, update):
        if not isinstance(update, dict) or type(update.get('update_id')) is not int or update['update_id'] < 0:
            return
        callback = update.get('callback_query')
        message = callback.get('message') if isinstance(callback, dict) else update.get('message')
        sender = callback.get('from') if isinstance(callback, dict) else message.get('from') if isinstance(message, dict) else None
        chat = message.get('chat') if isinstance(message, dict) else None
        if (not isinstance(sender, dict) or type(sender.get('id')) is not int or str(sender['id']) != self.user
                or not isinstance(chat, dict) or chat.get('type') != 'private' or type(chat.get('id')) is not int
                or str(chat['id']) != self.chat):
            return
        owner = self.user
        if callback:
            data = callback.get('data')
            confirmation = self.store.get('confirmation#' + data) if isinstance(data, str) else None
            if confirmation and confirmation.get('payload', {}).get('action') == 'enable':
                self.enable_callback(update, callback, confirmation, owner)
            else:
                telegram_bot._handle_callback(update, callback, self.token, owner)
        elif message.get('text') == '/enable':
            payload = {'action': 'enable', 'fingerprint': fingerprint(self.scheduler.get_schedule())}
            previous = self.store.get_update(str(update['update_id']))
            if previous:
                payload = previous['payload']
            token = self.store.create_confirmation_once(owner, payload, int(time.time()) + 300, str(update['update_id']))
            telegram_bot._send(self.token, self.chat, '🤔 Confermi l’attivazione della programmazione?',
                               [[{'text': '✅ Conferma', 'callback_data': token}, {'text': '❌ Annulla', 'callback_data': 'cancel:' + token}]])
        else:
            telegram_bot._handle_message(message, self.token, owner, update['update_id'])

    def enable_callback(self, update, callback, confirmation, owner):
        try:
            telegram_bot._telegram(self.token, 'answerCallbackQuery', {'callback_query_id': callback['id']})
        except telegram_bot.TelegramError:
            pass
        try:
            job = self.store.consume_confirmation(callback['data'], owner, update['update_id'])
            if not self.store.should_dispatch(job):
                telegram_bot._send(self.token, self.chat, 'ℹ️ Operazione già ricevuta; verifica la programmazione.')
                return
            self.store.claim_job(callback['data'])
            snapshot = self.scheduler.get_schedule()
            if fingerprint(snapshot) != job['payload']['fingerprint']:
                raise ValueError('Programmazione cambiata')
            self.scheduler.enable(snapshot)
            self.store.finish_job(callback['data'], 'completed', {'status': 'confirmed'})
        except StoreConflict:
            telegram_bot._send(self.token, self.chat, '⌛ Conferma non valida o scaduta.')
            return
        except Exception:
            try:
                self.store.finish_job(callback['data'], 'uncertain', {'status': 'uncertain'})
            except Exception:
                pass
            telegram_bot._send(self.token, self.chat, '⚠️ Esito incerto: verifica la programmazione.')
            return
        telegram_bot._send(self.token, self.chat, '✅ Programmazione attivata.')

    def run_scheduled(self, now):
        if not self.scheduler.reserve_due(now, self.started):
            return
        key = 'scheduled#' + now.date().isoformat()
        try:
            date = (now.date() + datetime.timedelta(days=7)).isoformat()
            result = app.book({'UserName': self.config['sportrick_username'], 'Password': self.config['sportrick_password']}, date, ['18:15'])
            status = 'confirmed' if isinstance(result, dict) and result.get('message') == 'Prenotazione confermata con successo.' else 'uncertain'
        except Exception:
            status = 'uncertain'
        with self.store.db:
            self.store.put(key, {'status': status, 'date': date})
        try:
            text = '✅ Prenotazione programmata confermata.' if status == 'confirmed' else '⚠️ Esito prenotazione programmata incerto: verifica gli impegni.'
            telegram_bot._send(self.token, self.chat, text)
        except telegram_bot.TelegramError:
            LOG.warning('Notifica Telegram non accettata; esito salvato nel database')

    def poll_once(self):
        self.run_scheduled(datetime.datetime.now(ROME))
        offset = self.store.get('offset') or 0
        updates = self.api('getUpdates', {'offset': offset, 'timeout': 10, 'allowed_updates': ['message', 'callback_query']}, timeout=15)
        if not isinstance(updates, list):
            raise RuntimeError('Risposta aggiornamenti non valida')
        for update in updates:
            if not isinstance(update, dict) or type(update.get('update_id')) is not int:
                raise RuntimeError('Aggiornamento non valido')
            try:
                self.handle(update)
            except Exception:
                LOG.warning('Aggiornamento non completato; verificare gli impegni e riprovare manualmente')
            with self.store.db:
                self.store.put('offset', update['update_id'] + 1)


def main():
    parser = argparse.ArgumentParser(description='Bot Telegram locale')
    parser.add_argument('--config', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    with open(args.config, encoding='utf-8') as file:
        config = json.load(file)
    for key in ('telegram_token', 'allowed_user_id', 'allowed_chat_id', 'sportrick_username', 'sportrick_password'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise SystemExit('Configurazione incompleta: ' + key)
    store = LocalStore(args.database)
    try:
        bot = LocalBot(config, store)
        telegram_bot.HELP = telegram_bot.HELP.replace('L’attivazione è bloccata.', 'Usa /enable per attivare.')
        bot.check_webhook()
        bot.warn_unfinished()
        LOG.info('Bot locale avviato')
        while True:
            try:
                bot.poll_once()
            except RuntimeError:
                LOG.warning('Connessione Telegram fallita; nuovo tentativo tra 5 secondi')
                time.sleep(5)
    finally:
        store.close()


if __name__ == '__main__':
    main()
