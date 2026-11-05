#!/usr/bin/env python3
"""Sintetizza da zero tutta la colonna sonora di Playwright Arcade in assets/.

Serve solo per rigenerare i suoni: `python3 tools/make_audio.py`.
Musica ed effetti sono composizioni originali, nessun campione esterno.
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / 'assets'
rng = np.random.default_rng(7)
NOTES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def midi(name):
    """'A4', 'F#3', 'Bb2' -> numero MIDI."""
    n = NOTES[name[0]]
    rest = name[1:]
    if rest[0] == '#':
        n, rest = n + 1, rest[1:]
    elif rest[0] == 'b':
        n, rest = n - 1, rest[1:]
    return n + 12 * (int(rest) + 1)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def phase(f, n):
    f = np.broadcast_to(np.asarray(f, dtype=np.float64), (n,))
    return np.cumsum(f) / SR


def pulse(f, n, duty=.5):
    p = phase(f, n) % 1
    return np.where(p < duty, 1., -1.)


def saw(f, n):
    return 2 * (phase(f, n) % 1) - 1


def tri(f, n):
    return 2 * np.abs(2 * (phase(f, n) % 1) - 1) - 1


def sine(f, n):
    return np.sin(2 * np.pi * phase(f, n))


def adsr(n, a=.004, d=.08, s=.6, r=.04):
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    e = np.full(n, s, dtype=np.float64)
    a, r = min(a, n), min(r, n)
    e[:a] = np.linspace(0, 1, a, endpoint=False)
    dn = min(d, n - a)
    if dn > 0:
        e[a:a + dn] = np.linspace(1, s, dn)
    if r:
        e[n - r:] *= np.linspace(1, 0, r)
    return e


def fft_filter(x, lo=0., hi=20000., slope=1.5):
    """Filtro morbido nel dominio della frequenza (circolare: non rompe i loop)."""
    n = x.shape[0]
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if hi < SR / 2:
        g /= np.sqrt(1 + (f / hi) ** (2 * slope))
    if lo > 0:
        g *= 1 - 1 / np.sqrt(1 + (f / lo) ** (2 * slope))
    if x.ndim == 1:
        return np.fft.irfft(np.fft.rfft(x) * g, n)
    return np.fft.irfft(np.fft.rfft(x, axis=0) * g[:, None], n, axis=0)


class Bus:
    """Traccia stereo. In modalità loop le code rientrano dall'inizio."""

    def __init__(self, seconds, loop=False):
        self.n = int(seconds * SR)
        self.x = np.zeros((self.n, 2))
        self.loop = loop

    def add(self, sig, t, gain=1., pan=0.):
        i = int(t * SR)
        l, r = gain * np.cos((pan + 1) * np.pi / 4) * 1.414, gain * np.sin((pan + 1) * np.pi / 4) * 1.414
        idx = np.arange(i, i + len(sig))
        if self.loop:
            idx %= self.n
        else:
            keep = idx < self.n
            idx, sig = idx[keep], sig[keep]
        np.add.at(self.x[:, 0], idx, sig * l)
        np.add.at(self.x[:, 1], idx, sig * r)


def echo(x, delay, feedback=.42, taps=5, pingpong=True):
    """Eco circolare a ping-pong."""
    out = np.zeros_like(x)
    d = int(delay * SR)
    g = feedback
    for k in range(1, taps + 1):
        y = np.roll(x, d * k, axis=0) * g
        if pingpong and k % 2:
            y = y[:, ::-1]
        out += y
        g *= feedback
    return out


def reverb(x, seconds=1.6, damp=4500):
    """Convoluzione con coda di rumore decrescente, circolare."""
    n = x.shape[0]
    m = min(int(seconds * SR), n)
    ir = np.zeros((n, 2))
    t = np.arange(m) / SR
    decay = np.exp(-t * 6 / seconds)
    ir[:m, 0] = rng.standard_normal(m) * decay
    ir[:m, 1] = rng.standard_normal(m) * decay
    ir = fft_filter(ir, lo=180, hi=damp)
    ir /= np.sqrt((ir ** 2).sum(axis=0))
    return np.fft.irfft(np.fft.rfft(x, axis=0) * np.fft.rfft(ir, axis=0), n, axis=0)


# --- strumenti -------------------------------------------------------------

def kick(punch=1.):
    n = int(.26 * SR)
    t = np.arange(n) / SR
    body = sine(48 + 150 * np.exp(-t * 38), n) * np.exp(-t * 13)
    click = rng.standard_normal(n) * np.exp(-t * 420) * .25
    return np.tanh((body + click) * 1.6 * punch)


def snare():
    n = int(.22 * SR)
    t = np.arange(n) / SR
    tone = sine(196 - 40 * t, n) * np.exp(-t * 24)
    nz = fft_filter(rng.standard_normal(n), lo=1400, hi=9000) * np.exp(-t * 17)
    return tone * .7 + nz * 1.5


def hat(open_=False):
    n = int((.2 if open_ else .05) * SR)
    t = np.arange(n) / SR
    return fft_filter(rng.standard_normal(n), lo=7000) * np.exp(-t * (20 if open_ else 95))


def crash():
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    return fft_filter(rng.standard_normal(n), lo=3500) * np.exp(-t * 3.2)


def lead(m, dur, vib=True, duty=.25, a=.006):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(m) * (1 + (0.006 * np.sin(2 * np.pi * 5.6 * t) * np.clip((t - .14) * 8, 0, 1) if vib else 0))
    d = duty + .12 * np.sin(2 * np.pi * 1.7 * t)
    p1 = phase(f, n) % 1
    p2 = phase(f * 1.004, n) % 1
    s = np.where(p1 < d, 1., -1.) * .6 + np.where(p2 < d, 1., -1.) * .4
    return s * adsr(n, a=a, d=.12, s=.7, r=min(.06, dur / 3))


def pluck(m, dur=.16, duty=.125):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return pulse(hz(m), n, duty) * np.exp(-t * 15)


def bass(m, dur):
    n = int(dur * SR)
    s = saw(hz(m), n) * .55 + pulse(hz(m) / 2, n, .5) * .45
    return s * adsr(n, a=.003, d=.09, s=.75, r=.02)


def pad(ms, dur):
    n = int(dur * SR)
    s = np.zeros(n)
    for m in ms:
        for det in (-.07, 0, .08):
            s += saw(hz(m + det), n)
    return s / (3 * len(ms)) * adsr(n, a=.18, d=.2, s=.9, r=.3)


def seq(text):
    """'E5:1 G5:.5 -:.5' -> [(midi|None, battute in quarti)]."""
    out = []
    for tok in text.split():
        n, b = tok.split(':')
        out.append((None if n == '-' else midi(n), float(b)))
    return out


def sidechain(n, beats, beat_s, depth=.6, release=.2):
    g = np.ones(n)
    r = int(release * SR)
    curve = 1 - depth * (1 - np.linspace(0, 1, r)) ** 2
    for b in beats:
        i = int(b * beat_s * SR) % n
        idx = (i + np.arange(r)) % n
        g[idx] = np.minimum(g[idx], curve)
    return g[:, None]


def master(x, peak=.8, drive=1.25):
    x = x / max(1e-9, np.abs(x).max())
    x = np.tanh(x * drive) / np.tanh(drive)
    return x * peak


def save(name, x):
    OUT.mkdir(exist_ok=True)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    with wave.open(str(OUT / (name + '.wav')), 'wb') as w:
        w.setparams((2, 2, SR, 0, 'NONE', 'not compressed'))
        w.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())
    print('%-14s %6.2fs  rms %.3f' % (name, len(x) / SR, np.sqrt((x ** 2).mean())))


# --- musica ---------------------------------------------------------------

def song(bpm, chords, melody, arp_pat, drum_fn, bars_pad=True, lead_gain=.34, swing=0.):
    """chords: una voce per battuta (lista di midi). melody: [(battuta_iniziale, testo)]."""
    beat = 60 / bpm
    bars = len(chords)
    dur = bars * 4 * beat
    drums, bs, pads, arps, leads = (Bus(dur, True) for _ in range(5))
    kicks = []
    for bar, ch in enumerate(chords):
        t0 = bar * 4 * beat
        if ch is None:
            continue
        root = ch[0]
        if bars_pad:
            pads.add(pad([c + 12 for c in ch], 4 * beat * 1.02), t0, .34)
        for i, step in enumerate(arp_pat(bar)):
            if step is None:
                continue
            m = ch[step % len(ch)] + 24 + 12 * (step // len(ch))
            arps.add(pluck(m), t0 + i * beat / 4, .17, pan=.45 * (1 if i % 2 else -1))
        drum_fn(bar, t0, beat, drums, bs, root, kicks)
    for start, text in melody:
        t = start * 4 * beat
        for m, b in seq(text):
            if m is not None:
                leads.add(lead(m, b * beat * .96), t, lead_gain, pan=-.08)
            t += b * beat
    sc = sidechain(bs.n, kicks, beat)
    pads_x = fft_filter(pads.x, lo=120, hi=2400) * sc
    bass_x = fft_filter(bs.x, hi=1400) * (.55 + .45 * sc)
    arps_x = fft_filter(arps.x, lo=300, hi=6500)
    leads_x = fft_filter(leads.x, lo=240, hi=7500)
    wet = echo(arps_x, beat * .75, .4) * .6 + echo(leads_x, beat * .75, .36) * .5
    room = reverb(leads_x * .45 + pads_x * .3 + drums.x * .08 + arps_x * .2)
    return drums.x + bass_x + pads_x + arps_x + leads_x + wet + room * .55


def make_menu():
    bpm = 108
    Am, F, C, G, Dm, E = ([57, 60, 64], [53, 57, 60], [48, 55, 64], [55, 59, 62], [50, 57, 65], [52, 56, 59])
    chords = [Am, Am, F, F, C, C, G, G, Am, F, C, G, Dm, F, E, E]

    def arp(bar):
        return [0, 1, 2, 3, 2, 1, 0, 1, 0, 2, 3, 4, 3, 2, 1, 2]

    def drums(bar, t0, beat, d, bs, root, kicks):
        full = bar >= 4
        for q in range(4):
            t = t0 + q * beat
            if full or q in (0, 2):
                d.add(kick(), t, .62)
                kicks.append(t / beat)
            if full and q in (1, 3):
                d.add(snare(), t, .3, pan=.05)
            d.add(hat(), t + beat / 2, .09, pan=.25)
            if full:
                d.add(hat(), t, .05, pan=-.25)
        for i, off in enumerate((0, .75, 1.5, 2, 2.75, 3.5)):
            bs.add(bass(root - 12, beat * .55), t0 + off * beat, .42)
        if bar == 15:
            for k, off in enumerate((3, 3.25, 3.5, 3.75)):
                d.add(snare(), t0 + off * beat, .16 + .05 * k)
        if bar in (0, 8):
            d.add(crash(), t0, .14, pan=.2)

    mel = [(4, 'E5:1.5 G5:.5 A5:2 G5:.5 E5:.5 D5:1 C5:2 D5:1 E5:1 D5:1.5 B4:.5 G4:2 -:4'),
           (8, 'A5:1.5 C6:.5 B5:1 A5:1 G5:1.5 A5:.5 F5:2 E5:1 G5:1 C6:2 B5:1 G5:1 D5:2'),
           (12, 'F5:1 A5:1 D6:2 C6:1 A5:1 F5:2 G#5:1.5 B5:.5 E6:2 D6:.5 B5:.5 G#5:1 E5:2')]
    save('music_menu', master(song(bpm, chords, mel, arp, drums), .62))


def make_battle():
    bpm = 150
    Em, C, D, Bm, Am, G, B = ([52, 59, 64], [48, 55, 64], [50, 57, 62], [47, 54, 62], [45, 52, 60], [43, 50, 59], [47, 51, 54])
    a = [Em, Em, C, D, Em, Em, C, B]
    b = [Am, C, G, D, Am, C, B, B]
    brk = [Em, C, Am, B]
    chords = a + a + b + brk + a[:4]

    def arp(bar):
        if 24 <= bar < 28:
            return [0, None, 2, None, 3, None, 2, None, 0, None, 2, None, 4, None, 3, None]
        return [0, 2, 3, 2, 4, 2, 3, 2, 0, 2, 3, 5, 4, 3, 2, 1]

    def drums(bar, t0, beat, d, bs, root, kicks):
        breakdown = 24 <= bar < 28
        for q in range(4):
            t = t0 + q * beat
            if not breakdown or (bar == 27 and q >= 2):
                d.add(kick(1.1), t, .72)
                kicks.append(t / beat)
            if not breakdown and q in (1, 3):
                d.add(snare(), t, .42, pan=.04)
            d.add(hat(q == 3 and bar % 2 == 1), t + beat / 2, .14, pan=.28)
            for e in (.25, .75):
                d.add(hat(), t + e * beat, .05, pan=-.28)
        if breakdown:
            bs.add(bass(root - 12, beat * 3.6), t0, .34)
        else:
            for i in range(8):
                oct_ = 12 if i in (3, 7) else 0
                bs.add(bass(root - 12 + oct_, beat * .42), t0 + i * beat / 2, .46)
        if bar % 8 == 7 and not breakdown:
            for k, off in enumerate((3, 3.25, 3.5, 3.625, 3.75, 3.875)):
                d.add(snare(), t0 + off * beat, .16 + .04 * k, pan=-.3 + .12 * k)
        if bar in (0, 8, 16, 28):
            d.add(crash(), t0, .22, pan=-.2)

    A = 'E5:.5 E5:.5 B5:1 A5:.5 G5:.5 F#5:1 E5:1.5 G5:.5 B5:2 '  \
        'C6:1 B5:.5 G5:.5 E5:1 G5:1 F#5:1 A5:1 D6:2 ' \
        'E6:.5 D6:.5 B5:1 G5:.5 A5:.5 B5:1 E5:1.5 G5:.5 B5:2 ' \
        'C6:1 B5:.5 A5:.5 G5:1 E5:1 D#5:1 F#5:1 B5:2'
    B_ = 'A5:1.5 C6:.5 E6:2 D6:.5 C6:.5 B5:1 G5:2 ' \
         'B5:1.5 D6:.5 G6:2 F#6:.5 E6:.5 D6:1 A5:2 ' \
         'C6:1 E6:1 A6:2 G6:.5 E6:.5 C6:1 G5:2 ' \
         'D#6:1 F#6:1 B6:2 A6:1 F#6:1 D#6:2'
    mel = [(8, A), (16, B_), (28, 'E5:.5 G5:.5 B5:.5 E6:.5 D6:1 B5:1 G5:.5 A5:.5 B5:1 E6:2 '
                             'C6:1 E6:1 G6:2 F#6:1 D6:1 A5:2')]
    save('music_battle', master(song(bpm, chords, mel, arp, drums, lead_gain=.3), .62))


# --- jingle ---------------------------------------------------------------

def render(events, seconds, verb=.3, delay=None, gain_peak=.85):
    b = Bus(seconds)
    for sig, t, g, p in events:
        b.add(sig, t, g, p)
    x = b.x
    tail = Bus(seconds + 1.2)
    tail.x[:b.n] = x
    y = tail.x
    wet = reverb(y, 1.1) * verb
    if delay:
        wet = wet + echo(y, delay, .3, 3) * .4
    y = y + wet
    n = len(y)
    fade = np.ones(n)
    fade[-int(.35 * SR):] = np.linspace(1, 0, int(.35 * SR))
    return master(y * fade[:, None], gain_peak, 1.1)


def sparkle(t0, count=14, span=.7, base=84):
    ev = []
    scale = [0, 2, 4, 7, 9, 12, 14, 16, 19]
    for i in range(count):
        m = base + scale[int(rng.integers(0, len(scale)))]
        ev.append((pluck(m, .2, .25) * .8, t0 + span * i / count + rng.random() * .02,
                   .1 * (1 - i / count / 1.4), float(rng.uniform(-.8, .8))))
    return ev


def make_pass(name, up):
    """Test superato: arpeggio maggiore che sale, accordo pieno e pioggia di scintille."""
    ev = []
    run = [60, 64, 67, 72, 76]
    for i, m in enumerate(run):
        ev.append((lead(m + up + 12, .13, False, .3, .002), i * .07, .4, -.3 + .15 * i))
        ev.append((pluck(m + up + 24, .2, .25), i * .07, .13, .3 - .15 * i))
    t = .36
    for m in (72, 76, 79, 84):
        ev.append((lead(m + up, .62, True, .25, .003), t, .24, 0))
    ev.append((bass(48 + up, .5), t, .4, 0))
    ev.append((kick(), t, .55, 0))
    ev.append((crash(), t, .1, .2))
    ev += sparkle(t + .05, base=84 + up)
    save(name, render(ev, 1.25, .3, .12))


def make_fail():
    """Test fallito: quattro lamenti che scendono e l'ultimo si spegne tremando."""
    ev = []
    t = 0.
    n = int(.5 * SR)
    # colpo sordo iniziale
    tt = np.arange(n) / SR
    ev.append((sine(95 - 55 * tt, n) * np.exp(-tt * 6), 0, .8, 0))
    ev.append((fft_filter(rng.standard_normal(n), hi=500) * np.exp(-tt * 9) * 2, 0, .45, 0))
    t = .16
    for i, (m, d) in enumerate(((58, .33), (57, .33), (56, .33), (55, 1.15))):
        k = int(d * SR)
        u = np.arange(k) / SR
        last = i == 3
        vib = 1 + (.022 * np.sin(2 * np.pi * 5.2 * u) * np.clip(u * 3, 0, 1) if last else 0)
        bend = 2 ** ((-1.3 * np.clip(u - .55, 0, 1)) / 12) if last else 1
        f = hz(m) * vib * bend
        # timbro da ottone con sordina: il "wah" apre e chiude le armoniche
        wah = .5 - .5 * np.cos(2 * np.pi * np.clip(u / d, 0, 1) * (3.2 if last else 1))
        s = saw(f, k) * (.35 + .65 * wah) + tri(f, k) * .55 + sine(f / 2, k) * .3
        s = fft_filter(s, hi=1500) * adsr(k, a=.02, d=.05, s=.85, r=.3 if last else .05)
        ev.append((s, t, .62, -.1))
        t += d + .02
    for m in (45, 48, 52):  # accordo minore in sottofondo
        ev.append((pad([m + 12], 2.1), .16, .3, 0))
    save('test_fail', render(ev, 2.2, .35, None, .82))


def make_skip():
    ev = [(tri(hz(67), int(.12 * SR)) * adsr(int(.12 * SR)), 0, .4, 0),
          (tri(hz(62), int(.2 * SR)) * adsr(int(.2 * SR)), .13, .4, 0)]
    save('test_skip', render(ev, .4, .15, None, .6))


def make_clear():
    """Fanfara di missione compiuta."""
    beat = .15
    ev = []
    line = 'C5:1 C5:1 C5:1 C5:1 G#4:2 A#4:2 C5:2 A#4:1 C5:5'.split()
    # melodia originale in Do maggiore con salita finale
    line = [('G4', 1), ('C5', 1), ('E5', 1), ('G5', 2), ('E5', 1), ('G5', 3),
            ('A5', 1), ('G5', 1), ('A5', 1), ('C6', 2), ('D6', 1), ('E6', 8)]
    t = 0.
    for n, b in line:
        m = midi(n)
        ev.append((lead(m, b * beat * .95, b > 3, .3, .003), t, .34, -.1))
        ev.append((lead(m - 12, b * beat * .95, False, .5, .003), t, .15, .15))
        t += b * beat
    end = t - 8 * beat
    for bt, ms in ((0, (48, 55, 64)), (6 * beat, (53, 57, 65)), (10 * beat, (55, 59, 67)), (end / 1, (48, 60, 64, 67, 72))):
        for m in ms:
            ev.append((pad([m + 12], 1.5 if bt == end else 1.), bt, .22, 0))
        ev.append((bass(ms[0] - 12, .5), bt, .42, 0))
        ev.append((kick(), bt, .6, 0))
    for i in range(int(end / beat)):
        ev.append((hat(), i * beat, .1, .2 if i % 2 else -.2))
        if i % 4 == 2:
            ev.append((snare(), i * beat, .3, 0))
    ev.append((crash(), end, .28, 0))
    ev += sparkle(end, 26, 1.3, 84)
    save('stage_clear', render(ev, end + 2.1, .34, .15))


def make_gameover():
    """Discesa lenta in minore, con un ultimo respiro sospeso."""
    ev = []
    t = 0.
    for n, b in (('E5', .5), ('C5', .5), ('A4', .7), ('G#4', .5), ('B4', .5), ('E4', 1.5)):
        m = midi(n)
        ev.append((lead(m, b * .96, True, .22, .02), t, .3, -.1))
        t += b
    for bt, ms in ((0, (45, 52, 60)), (1.7, (40, 47, 56)), (2.7, (45, 48, 52))):
        for m in ms:
            ev.append((pad([m + 12], 2.2), bt, .3, 0))
        ev.append((bass(ms[0] - 12, 1.2), bt, .34, 0))
    save('game_over', render(ev, 4.6, .5, .3, .78))


def make_sfx():
    def blip(f0, f1, d, duty=.5, g=1.):
        n = int(d * SR)
        t = np.arange(n) / SR
        return pulse(f0 * (f1 / f0) ** (t / d), n, duty) * np.exp(-t * 3 / d) * g

    save('move', master(blip(740, 1040, .05, .25), .34))
    save('select', master(np.concatenate([blip(660, 660, .05), blip(990, 990, .05), blip(1320, 1500, .1)]), .46))
    save('back', master(np.concatenate([blip(520, 520, .05), blip(330, 260, .09)]), .4))
    save('coin', render([(pulse(hz(83), int(.07 * SR), .5) * adsr(int(.07 * SR), r=.005), 0, .3, 0),
                         (pulse(hz(88), int(.4 * SR), .5) * np.exp(-np.arange(int(.4 * SR)) / SR * 9), .07, .3, 0)], .5, .2, None, .6))
    save('count', master(blip(880, 880, .14, .5), .5))
    save('go', render([(lead(m, .5, False, .3, .002), 0, .3, 0) for m in (72, 76, 79, 84)]
                      + [(kick(), 0, .7, 0), (crash(), 0, .22, 0)], .7, .25, None, .8))
    n = int(.09 * SR)
    t = np.arange(n) / SR
    save('shoot', master(pulse(1500 * np.exp(-t * 22) + 240, n, .3) * np.exp(-t * 34), .17))
    n = int(.34 * SR)
    t = np.arange(n) / SR
    boom = fft_filter(rng.standard_normal(n), hi=1600) * np.exp(-t * 12) + sine(120 * np.exp(-t * 8) + 40, n) * np.exp(-t * 11)
    save('explode', master(boom, .46))
    n = int(1.3 * SR)
    t = np.arange(n) / SR
    big = fft_filter(rng.standard_normal(n), hi=900) * np.exp(-t * 3.4) * 1.4 + sine(90 * np.exp(-t * 3) + 28, n) * np.exp(-t * 3.1)
    save('boss_boom', master(big, .72))
    n = int(.2 * SR)
    t = np.arange(n) / SR
    save('hurt', master(saw(320 * np.exp(-t * 8), n) * np.exp(-t * 16) + fft_filter(rng.standard_normal(n), hi=2200) * np.exp(-t * 26), .42))
    save('powerup', render([(pulse(hz(m), int(.07 * SR), .25) * adsr(int(.07 * SR), r=.01), i * .05, .28, 0)
                            for i, m in enumerate((72, 76, 79, 84, 88, 91))], .45, .2, None, .55))
    n = int(1.1 * SR)
    t = np.arange(n) / SR
    alarm = pulse(620 + 240 * (np.sin(2 * np.pi * 3.1 * t) > 0), n, .4) * adsr(n, r=.1)
    save('boss_in', render([(alarm, 0, .2, 0), (sine(52 + 12 * t, n) * adsr(n, a=.1, r=.2), 0, .5, 0)], 1.1, .22, None, .62))
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    whoosh = fft_filter(rng.standard_normal(n), lo=250, hi=5000) * (np.sin(np.pi * t / 1.4) ** 2) + sine(70 + 500 * (t / 1.4) ** 2, n) * .35 * (np.sin(np.pi * t / 1.4))
    save('warp', master(whoosh, .5))


if __name__ == '__main__':
    make_menu()
    make_battle()
    make_pass('test_pass1', 0)
    make_pass('test_pass2', 2)
    make_pass('test_pass3', 5)
    make_fail()
    make_skip()
    make_clear()
    make_gameover()
    make_sfx()
