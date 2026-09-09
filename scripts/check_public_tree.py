#!/usr/bin/env python3
"""Reject credential/state artifacts and non-example contact literals in a public tree.

Only file paths, line numbers and categories are reported, never matched values.
Run alongside Gitleaks; this check is not a substitute for secret scanning.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
try:
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z'], stderr=subprocess.DEVNULL).decode().split('\0')
    files = [ROOT / p for p in tracked if p]
except subprocess.CalledProcessError:
    files = [p for p in ROOT.rglob('*') if p.is_file() and not set(p.relative_to(ROOT).parts) & {'.git', '__pycache__', '.pytest_cache', '.venv'}]

blocked_parts = {'state', 'state-test', 'backups', 'builds', 'secrets', 'private', 'node_modules', '__pycache__'}
blocked_suffixes = {'.key', '.pem', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db', '.sql', '.tgz', '.zip', '.mp4', '.webm', '.pyc'}
email = re.compile(r'[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})')
patterns = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'provider-credential': re.compile(r'\b(?:sk_live_|rk_live_|whsec_|ghp_|github_pat_)[A-Za-z0-9_\-]{16,}'),
    'saved-agent-token': re.compile(r'\bbh_[A-Za-z0-9_\-]{30,}'),
    'local-home-path': re.compile(r'/(?:Users|home)/[a-zA-Z][a-zA-Z0-9_.-]*/'),
}
issues = []
for p in files:
    rel = p.relative_to(ROOT)
    if p.is_symlink() or set(rel.parts) & blocked_parts or p.suffix in blocked_suffixes or (p.name.startswith('.env') and p.name != '.env.example'):
        issues.append((str(rel), 0, 'excluded-artifact'))
        continue
    if not p.is_file():
        issues.append((str(rel), 0, 'missing-file'))
        continue
    try:
        text = p.read_text()
    except UnicodeDecodeError:
        issues.append((str(rel), 0, 'unreviewed-binary'))
        continue
    for number, line in enumerate(text.splitlines(), 1):
        for category, expression in patterns.items():
            if expression.search(line):issues.append((str(rel), number, category))
        for domain in email.findall(line):
            domain = domain.lower()
            if not (domain.endswith(('.test', '.invalid', '.example')) or domain in {'example.com', 'example.net', 'example.org'} or domain.endswith(('.example.com', '.example.net', '.example.org'))):
                issues.append((str(rel), number, 'non-example-email'))
if issues:
    for path, line, category in issues:print(f'{path}:{line}: {category}')
    sys.exit(1)
print(f'Public-tree hygiene passed ({len(files)} files).')
