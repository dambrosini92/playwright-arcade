"""Modello per local/projects.py. Tutto è facoltativo tranne PROJECTS."""
from pathlib import Path

# (nome del settore, cartella che contiene playwright.config.*)
PROJECTS = [('SHOP', Path.home() / 'code/shop')]

# Progetti con id a punti (gruppo.test): i livelli sono i gruppi e non i file di spec
GROUPED_BY_ID = ()

# Rapporto JSON lasciato dal runner: lista di {name, result, duration} oppure {id, esito, secondi}
REPORTS = {'SHOP': 'out/report.json'}

# Avviso nel briefing prima di partire
BRIEFINGS = {'SHOP': 'SHOP avvia il suo server di prova.'}

# Aspetto del pianeta: (seme, sfumature RGB, anello)
PLANETS = {'SHOP': (7, [(20, 40, 90), (60, 140, 200), (230, 240, 255)], True)}


def ready(name, root):
    """True o False se il progetto è pronto; None per cercare playwright.config.*"""
    return None


def discover(name, root):
    """Restituisce ([(etichetta, id)], messaggio); None per usare playwright --list."""
    return None


def command(name, root, selected, group):
    """Restituisce (comando, ambiente, cartella); None per playwright test. group: lancia un livello intero."""
    return None


def prepare(name, selected):
    """Avvia un servizio prima del test, per esempio un tunnel. Il processo restituito viene fermato alla fine."""
    return None
