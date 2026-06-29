"""
Set up HTTP Basic Authentication for the cyclotron dashboard server.

Usage:
    python setup_credentials.py

Creates data/.credentials.json with a PBKDF2-SHA256 hashed password.
Run this once before using: python main.py serve

The password is stored as a salted PBKDF2-SHA256 hash (600,000 iterations).
The plaintext password is never written anywhere.
"""
import getpass
import json
import sys
from pathlib import Path

# Resolve project root relative to this script, not the cwd.
_ROOT = Path(__file__).parent
_CREDS_PATH = _ROOT / 'data' / '.credentials.json'


def main():
    print('Cyclotron Dashboard — Credential Setup')
    print('=' * 40)

    username = input('Username: ').strip()
    if not username:
        sys.exit('ERROR: Username cannot be empty.')
    if len(username) > 64:
        sys.exit('ERROR: Username too long (max 64 chars).')

    password = getpass.getpass('Password: ')
    confirm  = getpass.getpass('Confirm password: ')
    if password != confirm:
        sys.exit('ERROR: Passwords do not match.')
    if len(password) < 12:
        sys.exit('ERROR: Password must be at least 12 characters.')

    # Import after argument validation — avoids loading serve.py prematurely if
    # the user hits an early exit condition above.
    sys.path.insert(0, str(_ROOT))
    from serve import hash_password  # noqa: E402

    print('\nHashing password (this takes a moment) ...')
    stored = hash_password(password)

    _CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CREDS_PATH, 'w', encoding='utf-8') as f:
        json.dump({'username': username, 'hash': stored}, f, indent=2)

    print(f'\nCredentials written to: {_CREDS_PATH}')
    print('Keep this file outside your git repository (already in .gitignore).')
    print('\nStart the server with: python main.py serve')


if __name__ == '__main__':
    main()
