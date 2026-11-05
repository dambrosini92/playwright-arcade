# Playwright Arcade

Launcher di test Playwright a forma di videogioco da terminale, per macOS. Ogni test è un boss: musica, animazioni e suoni seguono gli esiti reali.

Dal terminale avvia `./arcade`. Serve una finestra di almeno 80×24; il gioco rende meglio da 120×40 in su.

Scegli un settore (progetto), un livello (gruppo di test) e una missione (test) con le frecce; Invio apre il briefing e un altro Invio parte, dopo il conto alla rovescia, nel browser visibile. Ctrl+C chiude tutto.

## I livelli

Ogni progetto è diviso in livelli: i file di spec oppure, per i runner a nodi, i gruppi degli id (`accesso.T01_login` sta nel livello `accesso`). La barra mostra quante missioni hai superato l'ultima volta.

Se il progetto dichiara un rapporto su disco, entrando il gioco legge l'ultimo, così contano pure le partite fatte fuori da arcade.

Dentro un livello la prima riga, **TUTTO IL LIVELLO**, lancia l'intero gruppo; le righe `>>` lanciano un sotto-gruppo; le altre una missione singola. Dalla lista dei livelli, Spazio va dritto al briefing del livello intero.

Un livello parte come un'unica esecuzione del runner. I test che non appartengono a un gruppo sono nel livello SPECIALI.

## La battaglia

Mentre il test gira sei in uno sparatutto. Ogni fase del test è un boss; le righe dei reporter guidano il gioco:

- **Test superato**: il boss esplode tra fuochi d'artificio e coriandoli, suona un arpeggio gioioso che sale con la combo e l'arma si potenzia.
- **Test fallito**: il boss ti colpisce, lo schermo trema, la scena perde colore sotto la pioggia, parte il lamento triste e appare il messaggio d'errore.
- **Test saltato**: il boss se ne va, con un breve avviso.

Punteggio e rango sono solo gioco: il verdetto finale viene dal codice di uscita del test e dai suoi esiti, mai da come hai giocato. Il boss muore solo se il test passa.

Tasti in battaglia: frecce per muovere la nave (se non tocchi nulla guida il pilota automatico), Spazio bomba, Tab log completo, M audio, Esc ferma il test. A fine partita: Invio torna alle missioni, R riprova, Tab rilegge il log, Esc torna ai settori.

La musica accompagna anche il test; M la toglie e la scelta viene ricordata. Record, livello ed esiti per test stanno in `save.json`: cancellalo per ripartire da zero.

Se non senti nulla: `./arcade --audio-test` fa suonare il motore vero; un guasto appare in basso a destra e il motivo finisce in `audio.log`.

Per provare il gioco senza toccare alcun progetto: `./arcade --demo` aggiunge il settore DEMO, che è anche l'unico quando non hai configurato progetti, con una suite finta che copre vittorie, errori, crash e test velocissimi.

## I progetti

I tuoi progetti stanno in `local/projects.py`, che git ignora. Parti dall'esempio:

```bash
mkdir -p local && cp projects.example.py local/projects.py
```

Basta `PROJECTS` per una suite Playwright standard: il gioco cerca `playwright.config.*` e la lancia. Le altre voci dell'esempio sono facoltative e servono per runner propri, rapporti su disco, tunnel o avvisi nel briefing. Per un formato di output nuovo, modifica `parse_line` in `game.py`.

Per leggere l'elenco senza avviare nulla: `./arcade --list NOME`.

## Com'è fatto

- `core.py`: scoperta dei test e comandi.
- `game.py`: scene, battaglia e lettura delle righe dei reporter (`parse_line`).
- `gfx.py`: grafica a pixel nel terminale, a 24 bit oppure 256 colori (`ARCADE_COLOR=256` per forzarli).
- `audio_engine.py`: mixer in tempo reale su CoreAudio, in un processo a parte.
- `tools/make_audio.py`: sintetizza musica ed effetti in `assets/`.
- `projects.example.py`: modello per `local/projects.py`.

Richiede numpy per `/usr/bin/python3`.
