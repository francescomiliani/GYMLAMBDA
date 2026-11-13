"""SQLite state for one local bot process."""
import fcntl
import json
import secrets
import sqlite3
import time
from pathlib import Path

if __package__:
    from .store_conflict import StoreConflict
else:
    from store_conflict import StoreConflict


class LocalStore:
    def __init__(self, path, clock=time.time):
        path = Path(path).resolve()
        self.lock = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('Database già in uso da un altro processo') from None
        self.db = sqlite3.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()
        self.clock = clock

    def close(self):
        self.db.close()
        self.lock.close()

    def get(self, key):
        row = self.db.execute('SELECT value FROM state WHERE key = ?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', (key, json.dumps(value)))

    def get_update(self, update_id):
        return self.get('update#' + str(update_id))

    def get_job(self, job_id):
        return self.get('job#' + job_id)

    def create_confirmation_once(self, owner, payload, expires_at, update_id):
        with self.db:
            old = self.get_update(update_id)
            if old:
                if old.get('owner') != owner or old.get('payload') != payload or old.get('kind') != 'confirmation':
                    raise StoreConflict('Update already used')
                return old['token']
            if not owner or not isinstance(payload, dict) or expires_at <= self.clock():
                raise ValueError('Invalid confirmation')
            token = secrets.token_urlsafe(24)
            self.put('confirmation#' + token, {'owner': owner, 'payload': payload, 'expires_at': int(expires_at)})
            self.put('update#' + str(update_id), {'kind': 'confirmation', 'owner': owner, 'payload': payload, 'token': token})
            return token

    def cancel_confirmation(self, token, owner):
        with self.db:
            item = self.get('confirmation#' + token)
            if not item or item['owner'] != owner or item.get('job_id'):
                return False
            self.db.execute('DELETE FROM state WHERE key = ?', ('confirmation#' + token,))
            return True

    def consume_confirmation(self, token, owner, update_id):
        with self.db:
            item = self.get('confirmation#' + token)
            update = self.get_update(update_id)
            if not item or item['owner'] != owner or (update and update.get('job_id') != token):
                raise StoreConflict('Invalid confirmation')
            if not item.get('job_id'):
                if update or item['expires_at'] <= self.clock():
                    raise StoreConflict('Expired confirmation')
                item['job_id'] = token
                self.put('confirmation#' + token, item)
                self.put('job#' + token, {'status': 'pending', 'payload': item['payload'], 'expires_at': item['expires_at']})
            self.put('update#' + str(update_id), {'job_id': token})
            return self.get_job(token)

    def should_dispatch(self, job):
        return bool(job and job['status'] == 'pending' and job['expires_at'] > self.clock())

    def claim_job(self, job_id):
        with self.db:
            job = self.get_job(job_id)
            if not self.should_dispatch(job):
                raise StoreConflict('Job already claimed')
            job['status'] = 'running'
            self.put('job#' + job_id, job)
            return job

    def finish_job(self, job_id, status, result):
        if status not in ('completed', 'uncertain'):
            raise ValueError('Invalid final status')
        with self.db:
            job = self.get_job(job_id)
            if not job or job['status'] != 'running':
                raise StoreConflict('Job state changed')
            job.update(status=status, result=result)
            self.put('job#' + job_id, job)
            return job
