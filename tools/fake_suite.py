#!/usr/bin/env python3
"""Suite finta per `arcade --demo`: stampa gli stessi formati dei reporter veri, senza toccare nulla."""
import sys
import time

STEPS = ['setup.T01_primo_accesso', 'catalogo.T02_crea_prodotto', 'catalogo.T03_modifica_prezzo',
         'carrello.T04_aggiungi', 'carrello.T05_sconto', 'ordine.T06_conferma', 'ordine.T07_ricevuta']


def run(results, wait, titles=None):
    print('Avvio ambiente di prova...')
    time.sleep(1.2)
    total = len(results)
    for i, r in enumerate(results):
        print('[%2d/%d] ▶ %s' % (i + 1, total, (titles or STEPS)[i % len(titles or STEPS)]))
        time.sleep(wait)
        label = {'p': '✅ SUCCESS', 'f': '❌ FAIL', 's': '⏭️ SKIP'}[r]
        duration = '' if r == 's' else '%.1f s' % (wait * 7)
        message = "    expect(locator).toBeVisible() failed: pulsante 'Salva' non trovato" if r == 'f' else ''
        print('          %-12s%8s%s' % (label, duration, message))
        if r == 'f':
            print('              Call log: waiting for getByRole(button)')
    bad = results.count('f')
    print('\nE2E %s: %d passed · %d failed · %d skipped · 22.4 s' % ('FAILED' if bad else 'PASSED', results.count('p'), bad, results.count('s')))
    return 1 if bad else 0


def main():
    scenario = sys.argv[1]
    if scenario == 'base':
        return run('ppf', 1.5, ['base.tutto_verde', 'base.difficile.lungo', 'base.difficile.con_errori'])
    if scenario == 'base.difficile':
        return run('pf', 1.5, ['base.difficile.lungo', 'base.difficile.con_errori'])
    if scenario == 'casi':
        return run('ppp', 1, ['casi.reporter_playwright', 'casi.crash_senza_output', 'casi.tanti_veloci'])
    scenario = scenario.split('.')[-1]
    if scenario == 'lungo':
        return run('ppp', 2)
    if scenario == 'formato_nodi':
        # Runner a nodi: reporter "line" che ripulisce la riga, poi un rigo per nodo a fine nodo
        print('Avvio server...')
        time.sleep(2)
        rows = ['ok      casi.formato_nodi (5.9 s)', 'ok      ordine.T02_carrello (1.8 s)',
                "ERRORE  ordine.T03_salva (3.1 s): Error: expect(locator).toBeVisible() failed",
                'SALTATO ordine.T04_stampa: dipende da ordine.T03_salva',
                'ok      ordine.T05_chiudi (2.2 s)']
        print('\x1b[1A\x1b[2K[1/1] [chromium] › e2e/suite.spec.ts:60:1 › e2e')
        for i, row in enumerate(rows):
            time.sleep(4)
            print('\x1b[1A\x1b[2K[%2d/%d] %s\n' % (i + 1, len(rows), row))
        print('\n3 ok, 1 errori, 1 saltati. Dettagli in out/errori.')
        return 1
    if scenario == 'tutto_verde':
        return run('ppppp', 3)
    if scenario == 'con_errori':
        return run('ppfsppf', 2.5)
    if scenario == 'tanti_veloci':
        return run('ppppsssssssppppppppp', .05)
    if scenario == 'crash_senza_output':
        time.sleep(2)
        return 3
    print('Running 4 tests using 1 worker\n')
    for mark, title in (('✓', 'login (2.1s)'), ('✓', 'menu (1.4s)'), ('✘', 'salvataggio (3.0s)'), ('-', 'stampa')):
        time.sleep(2)
        print('  %s  1 [chromium] › tests/app.spec.ts:10:1 › %s' % (mark, title))
    print('\n  1 failed\n  2 passed (9s)')
    return 1


if __name__ == '__main__':
    sys.exit(main())
