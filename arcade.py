#!/usr/bin/env python3
"""Playwright Arcade: launcher dei test a forma di videogioco da terminale."""
import argparse
import subprocess
import sys

from core import DEMO, PROJECTS, discover


def main():
    parser = argparse.ArgumentParser(description='Playwright Arcade')
    parser.add_argument('--list', choices=[p[0] for p in PROJECTS + [DEMO]], help='Mostra i test senza avviarli')
    parser.add_argument('--demo', action='store_true', help='Aggiunge una suite finta per provare il gioco')
    parser.add_argument('--audio-test', action='store_true', help='Fa suonare jingle e musica per provare l\'audio')
    args = parser.parse_args()
    if args.audio_test:
        import game
        return game.audio_test()
    if args.list:
        try:
            items, message = discover(*next(p for p in PROJECTS + [DEMO] if p[0] == args.list))
            print('\n'.join(x[0] for x in items) if items else message)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('Avvia Playwright Arcade da un terminale.', file=sys.stderr)
        return 1
    try:
        import game
    except ImportError as e:
        print('Manca una dipendenza (%s). Installa con: %s -m pip install --user -r requirements.txt' % (e, sys.executable),
              file=sys.stderr)
        return 1
    projects = PROJECTS + [DEMO] if args.demo or not PROJECTS else PROJECTS
    return game.run(projects)


if __name__ == '__main__':
    sys.exit(main())
