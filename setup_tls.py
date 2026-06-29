"""
Generate a self-signed TLS certificate for the cyclotron dashboard server.

Usage:
    python setup_tls.py

Creates data/tls/cert.pem and data/tls/key.pem.
After running, 'python main.py serve' will automatically enable HTTPS on port 8443.

Certificate: RSA-4096, SHA-256, 10-year validity, CN=cyclotron-monitor
Subject Alternative Names: DNS:localhost, IP:127.0.0.1

IMPORTANT: This is a self-signed certificate.
  - Browsers will show a security warning — this is expected for internal tools.
  - Add the cert to your local trust store to suppress the warning.
  - The certificate binds to 127.0.0.1 only; it cannot be used for remote access.
"""
import datetime
import ipaddress
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent
_TLS_DIR = _ROOT / 'data' / 'tls'
_CERT = _TLS_DIR / 'cert.pem'
_KEY  = _TLS_DIR / 'key.pem'

_OPENSSL_CANDIDATES = [
    'openssl',                                                   # on PATH
    r'C:\Program Files\Git\usr\bin\openssl.exe',                # Git for Windows
    r'C:\Program Files\OpenSSL-Win64\bin\openssl.exe',          # Win64 OpenSSL installer
    r'C:\Program Files (x86)\OpenSSL-Win32\bin\openssl.exe',    # Win32 OpenSSL installer
    r'C:\Windows\System32\openssl.exe',                         # rare Windows installs
]


def _find_openssl() -> str | None:
    for candidate in _OPENSSL_CANDIDATES:
        try:
            r = subprocess.run([candidate, 'version'], capture_output=True, timeout=5)
            if r.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            continue
    return None


def _gen_with_openssl(openssl: str):
    subj = '/CN=cyclotron-monitor/O=PET Labs Cyclotron Monitor/C=ZA'
    san  = 'subjectAltName=DNS:localhost,IP:127.0.0.1'
    cmd  = [
        openssl, 'req', '-x509', '-newkey', 'rsa:4096',
        '-keyout', str(_KEY), '-out', str(_CERT),
        '-days', '3650', '-nodes',
        '-subj', subj,
        '-addext', san,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'openssl failed:\n{r.stderr}')


def _gen_with_cryptography():
    """Fallback: use the 'cryptography' package if openssl is not available."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError(
            "Could not find openssl or the 'cryptography' Python package.\n"
            "Install one of:\n"
            "  pip install cryptography\n"
            "  https://git-scm.com/download/win  (Git for Windows includes openssl)\n"
            "  https://slproweb.com/products/Win32OpenSSL.html  (Win64 OpenSSL)"
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'ZA'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'PET Labs Cyclotron Monitor'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'cyclotron-monitor'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    _KEY.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    _CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main():
    print('Cyclotron Dashboard — TLS Certificate Setup')
    print('=' * 44)

    if _CERT.exists() and _KEY.exists():
        ans = input(f'Certificate already exists at {_TLS_DIR}\nOverwrite? [y/N] ').strip().lower()
        if ans != 'y':
            print('Aborted.')
            sys.exit(0)

    _TLS_DIR.mkdir(parents=True, exist_ok=True)

    openssl = _find_openssl()
    if openssl:
        print(f'Using openssl: {openssl}')
        _gen_with_openssl(openssl)
    else:
        print('openssl not found — trying cryptography package ...')
        _gen_with_cryptography()

    print(f'\nCertificate: {_CERT}')
    print(f'Private key: {_KEY}')
    print('\nStart the server with: python main.py serve')
    print('The server will listen on https://127.0.0.1:8443/')
    print('\nNOTE: Your browser will warn about the self-signed certificate.')
    print('To suppress the warning, add cert.pem to your OS certificate trust store.')


if __name__ == '__main__':
    main()
