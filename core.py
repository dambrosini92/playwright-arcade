#!/usr/bin/env python3
"""Logica del launcher: scoperta dei test e comandi. Non avvia test da sola."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from sysio import node_bin, stop_group  # noqa: F401  stop_group serve anche a game.py e ai progetti locali

try:  # i progetti propri stanno in local/projects.py, ignorato da git: vedi projects.example.py
    from local import projects as local
except ModuleNotFoundError as e:
    if e.name not in ('local', 'local.projects'):
        raise
    local = None

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
HERE = Path(__file__).resolve().parent
DEMO = ('DEMO', HERE / 'tools')
DEMO_SCENARIOS = ['base.tutto_verde', 'base.difficile.con_errori', 'base.difficile.lungo',
                  'casi.formato_nodi', 'casi.reporter_playwright', 'casi.crash_senza_output', 'casi.tanti_veloci']
PROJECTS = list(getattr(local, 'PROJECTS', []))
GROUPED_BY_ID = ('DEMO',) + tuple(getattr(local, 'GROUPED_BY_ID', ()))  # gli altri si raggruppano per file di spec
REPORTS = getattr(local, 'REPORTS', {})
BRIEFINGS = getattr(local, 'BRIEFINGS', {})
PLANETS = getattr(local, 'PLANETS', {})
PAST_STATUS = {'passed': 'pass', 'ok': 'pass', 'failed': 'fail', 'errore': 'fail'}


def hook(name, *args):
    """Chiama una funzione facoltativa di local/projects.py; None vuol dire comportamento standard."""
    fn = getattr(local, name, None)
    return fn(*args) if fn else None


def discover_config(root):
    if not root.is_dir():
        return None
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                   {'node_modules', 'bin', 'obj', 'dist', 'build', 'coverage', 'out', 'vendor', 'backups'}]
        if len(Path(base).relative_to(root).parts) >= 5:
            dirs[:] = []
        for filename in sorted(files):
            if re.fullmatch(r'playwright\.config\.(ts|js|mjs|mts|cjs|cts)', filename):
                return Path(base) / filename
    return None


def report_path(name, root):
    return root / REPORTS[name] if name in REPORTS else None


def past_results(name, root):
    """Legge l'ultimo rapporto lasciato dal progetto: (data del file, [(id, esito, secondi)])."""
    path = report_path(name, root)
    try:
        stamp = path.stat().st_mtime
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (AttributeError, OSError, ValueError):
        return None, []
    out = []
    for row in rows if isinstance(rows, list) else []:
        key, status = row.get('name') or row.get('id'), row.get('result') or row.get('esito')
        seconds = row['secondi'] if 'secondi' in row else (row.get('duration') or 0) / 1000
        if key and status in PAST_STATUS:
            out.append((key, PAST_STATUS[status], seconds))
    return stamp, out


def merge_past(save, name, root):
    """Porta nel salvataggio le missioni giocate fuori dal gioco. Ogni rapporto conta una volta sola."""
    stamp, rows = past_results(name, root)
    seen = save.setdefault('imports', {})
    if stamp is None or seen.get(name) == stamp:
        return 0
    seen[name] = stamp
    tests = save.setdefault('tests', {})
    for key, status, seconds in rows:
        rec = tests.setdefault(name + '/' + key, {'passes': 0, 'fails': 0})
        rec['last'] = status
        rec['passes' if status == 'pass' else 'fails'] = rec.get('passes' if status == 'pass' else 'fails', 0) + 1
        if status == 'pass' and seconds:
            rec['best'] = min(rec.get('best', seconds), seconds)
    return len(rows)


def project_ready(name, root):
    ready = hook('ready', name, root)
    return bool(discover_config(root)) if ready is None else ready


def read_json(cmd, cwd, env=None):
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=30, env=env)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Errore di lettura dei test')[:1500])
    return json.loads(result.stdout)


def playwright_list(root, env=None, prefix=''):
    config = discover_config(root)
    if not config:
        return [], 'Suite Playwright non ancora configurata.'
    executable = node_bin(config.parent, 'playwright')
    if not executable.exists():
        return [], 'Configurazione trovata, dipendenze Playwright mancanti: ' + str(config.parent)
    payload = read_json([str(executable), 'test', '--list', '--reporter=json'], config.parent, env)
    items = []

    def visit(suite):
        for spec in suite.get('specs', []):
            items.append((spec['title'], prefix + spec['file'].replace('\\', '/') + ':' + str(spec['line'])))
        for child in suite.get('suites', []):
            visit(child)
    for suite in payload.get('suites', []):
        visit(suite)
    return items, '' if items else 'Nessun caso Playwright rilevato.'


def discover(name, root):
    if name == 'DEMO':
        return [(x, x) for x in DEMO_SCENARIOS], ''
    found = hook('discover', name, root)
    return playwright_list(root) if found is None else found


def level_of(name, key):
    """Il gruppo che il runner sa lanciare da solo, oppure None per i test a parte."""
    if key.startswith('@'):
        return None
    if name in GROUPED_BY_ID:
        return key.split('.')[0] if '.' in key else None
    return key.rsplit(':', 1)[0] if ':' in key else None


def build_levels(name, items):
    """Divide i test in livelli, nell'ordine in cui arrivano; quelli a parte vanno in fondo."""
    groups = {}
    for label, key in items:
        groups.setdefault(level_of(name, key), []).append((label, key))
    levels = [{'id': k, 'title': k, 'entries': v} for k, v in groups.items() if k is not None]
    if None in groups:
        levels.append({'id': None, 'title': 'SPECIALI', 'entries': groups[None]})
    return levels


def level_rows(name, level):
    """Le righe del livello: (etichetta, id, lancia un gruppo?). Prima l'intero livello, poi sotto-gruppi e missioni."""
    lid, entries = level['id'], level['entries']
    if lid is None:
        return [(label, key, False) for label, key in entries]
    rows = [('TUTTO IL LIVELLO  (%d missioni)' % len(entries), lid, True)]
    if name not in GROUPED_BY_ID:
        return rows + [(label, key, False) for label, key in entries]
    prefix = lambda key: '.'.join(key.split('.')[:2]) if key.count('.') >= 2 else None
    sizes = {}
    for _, key in entries:
        sizes[prefix(key)] = sizes.get(prefix(key), 0) + 1
    seen = set()
    for _, key in entries:
        sub = prefix(key)
        grouped = sub is not None and sizes[sub] >= 2
        if grouped and sub not in seen:
            seen.add(sub)
            rows.append(('>> %s  (%d)' % (sub.split('.')[1], sizes[sub]), sub, True))
        rows.append(('   ' * grouped + key[len(sub if grouped else lid) + 1:], key, False))
    return rows


def command(name, root, selected, group=False):
    env = os.environ.copy()
    if name == 'DEMO':
        env['PYTHONUTF8'] = '1'  # su Windows la suite finta stamperebbe le emoji in cp1252
        return [sys.executable, '-u', str(root / 'fake_suite.py'), selected], env, root
    custom = hook('command', name, root, selected, group)
    if custom:
        return custom
    config = discover_config(root)
    if not config:
        raise RuntimeError('Configurazione Playwright non trovata.')
    return [str(node_bin(config.parent, 'playwright')), 'test', selected, '--headed'], env, config.parent
