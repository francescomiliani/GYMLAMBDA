#!/usr/bin/env python3
"""Consenso una tantum per scrivere sul calendario Google dell'utente.

Legge GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET dall'ambiente, apre il browser
sullo scope calendar.events e scrive ~/.config/gymbooking/google-calendar.json
con permessi 0600. Non stampa il refresh token.
"""
import json
import os
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPE = 'https://www.googleapis.com/auth/calendar.events'
REDIRECT = 'http://127.0.0.1:8765/'
DEST = Path.home() / '.config' / 'gymbooking' / 'google-calendar.json'


def main():
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        raise SystemExit('Servono GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nell\'ambiente.')
    query = urllib.parse.urlencode({
        'client_id': client_id, 'redirect_uri': REDIRECT, 'response_type': 'code',
        'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
    })
    code = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            code['value'] = urllib.parse.parse_qs(parsed.query).get('code', [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Consenso ricevuto. Puoi chiudere questa scheda.')

        def log_message(self, fmt, *args):
            return

    server = HTTPServer(('127.0.0.1', 8765), Handler)
    webbrowser.open('https://accounts.google.com/o/oauth2/v2/auth?' + query)
    server.handle_request()
    if not code.get('value'):
        raise SystemExit('Consenso non ricevuto.')
    body = urllib.parse.urlencode({
        'code': code['value'], 'client_id': client_id, 'client_secret': client_secret,
        'redirect_uri': REDIRECT, 'grant_type': 'authorization_code',
    }).encode()
    with urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', body), timeout=15) as response:
        token = json.load(response)
    refresh = token.get('refresh_token')
    if not refresh:
        raise SystemExit('Google non ha restituito un refresh token. Ripeti il consenso.')
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({
        'client_id': client_id, 'client_secret': client_secret, 'refresh_token': refresh,
    }))
    DEST.chmod(0o600)
    print(f'Scritto {DEST} (0600). Il token non e\' in questo output.')


if __name__ == '__main__':
    main()
