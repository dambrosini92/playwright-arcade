"""Le scene del gioco. L'esito dei test viene solo dai test: il punteggio non lo cambia mai."""
import json
import math
import os
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from core import (ANSI, BRIEFINGS, HERE, PLANETS as LOCAL_PLANETS, build_levels, command, discover, hook, level_rows,
                  merge_past, past_results, project_ready, stop_group)
from sysio import SPAWN, read_input, restore_terminal, setup_terminal
from gfx import Planet, Screen, Stars, creature, hsv, mix, safe_text, sprite, sunset

FPS = 24
SAVE = HERE / 'save.json'
PANEL_MIN_WIDTH = 104

CYAN, PINK, GOLD, GREEN, RED, GREY = (70, 225, 255), (255, 70, 190), (255, 214, 80), (90, 255, 140), (255, 70, 80), (140, 150, 175)
WHITE, DIM = (240, 244, 255), (120, 130, 165)
PARTY = [(255, 80, 120), (255, 214, 80), (90, 255, 140), (70, 225, 255), (200, 110, 255), (255, 255, 255)]

PLANETS = {'DEMO': (66, [(30, 30, 60), (60, 120, 200), (240, 90, 160), (255, 240, 200)], True), **LOCAL_PLANETS}

SHIP = sprite([
    '.....w.....',
    '....wcw....',
    '....wcw....',
    '...wwcww...',
    '..pwwbwwp..',
    '.pwwwbwwwp.',
    'ppw.wbw.wpp',
    'p...bbb...p',
], {'w': (225, 235, 255), 'c': (70, 225, 255), 'b': (70, 90, 190), 'p': (255, 70, 190)})

STEP_RE = re.compile(r'^\[\s*(\d+)/(\d+)\]\s*▶\s*(.+)$')
RESULT_RE = re.compile(r'^\s*(✅\s*SUCCESS|❌\s*FAIL|⏭️?\s*SKIP)\s*(.*)$')
TIME_RE = re.compile(r'^(\d[\d.,]*\s*(?:ms|s|min|m)\b(?:\s+\d[\d.,]*\s*s\b)?)?\s*(.*)$')
LIST_RE = re.compile(r'^\s*(✓|✔|ok|✘|✗|×|x|-)\s+\d+\s+(.+?)(?:\s+\(([\d.,]+\s*m?s|[\d.,]+m(?:\s*[\d.,]+s)?)\))?\s*$')
TOTAL_RE = re.compile(r'^Running (\d+) tests? using')
# Runner a nodi: una riga sola per nodo, a fine nodo. "[ 1/12] ok      id (5.9 s)", "ERRORE  id (5.9 s): motivo", "SALTATO id: motivo"
NODE_RE = re.compile(r'^\[\s*(\d+)/(\d+)\]\s+(ok|ERRORE|SALTATO)\s+([^\s:]+)(?:\s+\(([^)]*)\))?:?\s*(.*)$')
LOCATION_RE = re.compile(r'([^\s›]+\.spec\.\w+:\d+)(?::\d+)?')


def parse_line(line):
    """Trasforma una riga dei reporter in un evento di gioco, oppure None."""
    m = STEP_RE.match(line.strip())
    if m:
        return {'kind': 'step', 'n': int(m[1]), 'total': int(m[2]), 'title': m[3].strip()}
    m = NODE_RE.match(line.strip())
    if m:
        status = {'ok': 'pass', 'ERRORE': 'fail', 'SALTATO': 'skip'}[m[3]]
        return {'kind': 'result', 'status': status, 'time': m[5] or '', 'message': m[6].strip(),
                'title': m[4], 'total': int(m[2])}
    m = RESULT_RE.match(line)
    if m:
        status = 'pass' if 'SUCCESS' in m[1] else 'fail' if 'FAIL' in m[1] else 'skip'
        t = TIME_RE.match(m[2])
        return {'kind': 'result', 'status': status, 'time': (t[1] or '').strip(), 'message': t[2].strip()}
    m = TOTAL_RE.match(line.strip())
    if m:
        return {'kind': 'total', 'total': int(m[1])}
    m = LIST_RE.match(line)
    if m and '›' in m[2]:
        status = 'pass' if m[1] in ('✓', '✔', 'ok') else 'skip' if m[1] == '-' else 'fail'
        where = LOCATION_RE.search(m[2])
        return {'kind': 'result', 'status': status, 'time': m[3] or '', 'message': '',
                'title': m[2].split('›')[-1].strip(), 'where': where[1] if where else ''}
    return None


class Sound:
    """Canale verso audio_engine.py. Se l'audio non parte, il gioco resta muto ma funziona."""

    def __init__(self, muted=False):
        self.muted = muted
        self.music_name = None
        try:
            with open(HERE / 'audio.log', 'w') as log:  # se il motore cade, il motivo resta qui
                self.proc = subprocess.Popen([sys.executable, str(HERE / 'audio_engine.py')], stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=log, text=True, bufsize=1)
        except OSError:
            self.proc = None
        self.send('mute %d' % muted)

    def send(self, line):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(line + '\n')
            except (OSError, ValueError):
                self.proc = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def sfx(self, name, volume=1.):
        self.send('sfx %s %.2f' % (name, volume))

    def sting(self, name, volume=1.):
        self.send('sting %s %.2f' % (name, volume))

    def music(self, name):
        if name != self.music_name:
            self.music_name = name
            self.send('music ' + (name or '-'))

    def toggle(self):
        self.muted = not self.muted
        self.send('mute %d' % self.muted)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.proc.kill()


class Particles:
    def __init__(self, cap=1600):
        self.cap = cap
        self.x, self.y, self.vx, self.vy = (np.zeros(cap, np.float32) for _ in range(4))
        self.life, self.total, self.grav = (np.zeros(cap, np.float32) for _ in range(3))
        self.col = np.zeros((cap, 3), np.float32)
        self.head = 0

    def emit(self, x, y, n, speed, colors, life=1., grav=0., spread=math.tau, angle=0.):
        idx = (self.head + np.arange(n)) % self.cap
        self.head = (self.head + n) % self.cap
        a = np.random.uniform(angle - spread / 2, angle + spread / 2, n)
        v = np.random.uniform(.25, 1, n) * speed
        self.x[idx], self.y[idx] = x, y
        self.vx[idx], self.vy[idx] = np.cos(a) * v, np.sin(a) * v
        self.life[idx] = self.total[idx] = np.random.uniform(.5, 1, n) * life
        self.grav[idx] = grav
        self.col[idx] = np.array(colors, np.float32)[np.random.randint(0, len(colors), n)]

    def update(self, dt):
        self.vy += self.grav * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.life -= dt

    def draw(self, s, width):
        alive = self.life > 0
        ok = alive & (self.x >= 0) & (self.x < width) & (self.y >= 0) & (self.y < s.H)
        if ok.any():
            fade = np.clip(self.life[ok] / self.total[ok] * 1.6, 0, 1)[:, None]
            s.fb[self.y[ok].astype(int), self.x[ok].astype(int)] = (self.col[ok] * fade).astype(np.uint8)


def format_clock(seconds):
    return '%02d:%02d' % (seconds // 60, seconds % 60)


class Game:
    def __init__(self, projects):
        self.projects = projects
        self.s = Screen()
        self.save = self.load_save()
        self.sound = Sound(self.save.get('muted', False) or bool(os.environ.get('ARCADE_MUTE')))
        self.stars = Stars()
        self.fx = Particles()
        self.t = 0.
        self.scene = 'title'
        self.scene_t = 0.
        self.fade = 1.
        self.project = 0
        self.choice = 0
        self.items = []
        self.groups = set()   # id delle righe che lanciano un gruppo intero
        self.levels = []
        self.level = 0
        self.message = ''
        self.lines = []
        self.log_view = False
        self.log_scroll = 0
        self.proc = self.tunnel = None
        self.q = queue.Queue()
        self.load_token = 0
        self.loaded = None
        self.planets = {}
        self.bosses = {}
        self.status = {}
        self.pending = b''
        self.starter = None
        self.abort = False
        self.exit_code = None
        threading.Thread(target=self.scan_status, daemon=True).start()
        self.sound.music('music_menu')

    # --- salvataggio --------------------------------------------------------
    @staticmethod
    def load_save():
        try:
            data = json.loads(SAVE.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_save(self):
        try:
            tmp = SAVE.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.save, indent=1, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, SAVE)
        except OSError:
            pass

    @property
    def rank_level(self):
        return self.save.get('xp', 0) // 1000 + 1

    def record(self, key=None):
        name = self.projects[self.project][0]
        key = key or self.items[self.choice][1]
        return self.save.setdefault('tests', {}).get(name + '/' + key, {})

    def scan_status(self):
        for name, root in self.projects:
            if name == 'DEMO':
                self.status[name] = 'simulazione'
            else:
                self.status[name] = 'presente' if project_ready(name, root) else 'da preparare'

    # --- utilità ------------------------------------------------------------
    def go(self, scene):
        self.scene = scene
        self.scene_t = 0.
        self.fade = 1.

    def planet(self, name, radius):
        key = (name, radius)
        if key not in self.planets:
            seed, ramp, ring = PLANETS.get(name, (99, [(40, 40, 60), (120, 120, 150), (230, 230, 250)], False))
            self.planets[key] = Planet(seed, radius, ramp, ring)
        return self.planets[key]

    def boss_sprite(self, seed, w=25, h=17):
        key = (seed, w, h)
        if key not in self.bosses:
            self.bosses[key] = creature(seed, w, h, (seed * .137) % 1)
        return self.bosses[key]

    def seed_of(self, text):
        return sum(ord(c) * (i + 3) for i, c in enumerate(text)) % 9973

    def footer(self, text):
        s = self.s
        s.rect(0, s.H - 2, s.W, 2, (6, 6, 20))
        s.text(1, s.rows - 1, text, DIM)
        audio = 'AUDIO GUASTO: vedi audio.log' if not self.sound.alive else 'M: audio ' + ('OFF' if self.sound.muted else 'ON')
        s.text(s.cols - len(audio) - 1, s.rows - 1, audio, RED if self.sound.muted or not self.sound.alive else DIM)

    def header(self, title):
        s = self.s
        s.big(title, 3, 3, 1, color=WHITE, color2=CYAN, shadow=(20, 30, 80))
        info = 'LV %d   XP %d   RECORD %07d' % (self.rank_level, self.save.get('xp', 0), self.hiscore())
        s.text(s.cols - len(info) - 2, 0, info, GOLD)

    def hiscore(self):
        scores = self.save.get('scores', [])
        return scores[0]['score'] if scores else 0

    def space(self, dt, vx=-.01, vy=.015):
        s = self.s
        s.fb[:] = (5, 5, 20)
        self.stars.update(dt, vx, vy)
        self.stars.draw(s, t=self.t)

    # --- scene: titolo ------------------------------------------------------
    def draw_title(self, dt):
        s = self.s
        sunset(s, self.t)
        self.stars.update(dt, .004, 0)
        hz = int(s.H * .58)
        ys = (self.stars.y * hz * .6).astype(int)
        xs = (self.stars.x * s.W).astype(int)
        s.fb[ys, xs] = (self.stars.z[:, None] * np.array((255, 240, 255))).astype(np.uint8)
        sc = s.fit_scale('PLAYWRIGHT', s.W - 8, 3)
        y = 3 + int(math.sin(self.t * 1.6) * 1.5)
        drop = max(0., 1 - self.scene_t / .8) ** 2 * s.H * .6
        wave = lambda i: (0, int(math.sin(self.t * 3 + i * .6) * 1.3 - drop))
        s.big_center('PLAYWRIGHT', y, sc, color=(255, 255, 255), color2=(60, 220, 255), shadow=(40, 0, 90), offsets=wave)
        sc2 = s.fit_scale('ARCADE', s.W - 20, sc + 1)
        s.big_center('ARCADE', y + 7 * sc + 3, sc2, color=(255, 240, 120), color2=(255, 40, 160), shadow=(70, 0, 80), offsets=wave)
        fly = (self.t * 18) % (s.W + 40) - 20
        s.blit(SHIP, fly, s.H * .5 + math.sin(self.t * 2) * 5)
        self.fx.emit(fly + 5, s.H * .5 + math.sin(self.t * 2) * 5 + 8, 2, 8, [(255, 200, 80), (255, 90, 160)], .5, 0, 1, math.pi / 2)
        self.fx.update(dt)
        self.fx.draw(s, s.W)
        row = int(s.rows * .84)
        if int(self.t * 2.2) % 2 == 0:
            s.text_center(row, '>>  PREMI INVIO PER INIZIARE  <<', WHITE)
        s.text_center(row + 2, 'RECORD %07d   LV %d' % (self.hiscore(), self.rank_level), GOLD)

    def key_title(self, k):
        if k == 'ENTER' or k == 'SPACE':
            self.sound.sfx('coin')
            self.go('projects')

    # --- scene: settori -----------------------------------------------------
    def draw_projects(self, dt):
        s = self.s
        self.space(dt, -.012, .002)
        n = len(self.projects)
        roomy = s.H >= 60
        cx, cy = s.W // 2, int(s.H * .44)
        big_r = max(9, min(int(s.H * .22), 24))
        small_r = max(4, big_r // 3)
        for off in (-2, -1, 1, 2):
            i = (self.project + off) % n
            if n < 5 and abs(off) == 2:
                continue
            x = cx + off * int(s.W * .21)
            self.planet(self.projects[i][0], small_r).draw(s, x, cy + abs(off) * 3, self.t * 8)
            s.text_center(int((cy + small_r + abs(off) * 3) / 2) + 2, self.projects[i][0], DIM, cx=x)
        name, _ = self.projects[self.project]
        self.planet(name, big_r).draw(s, cx, cy, self.t * 12)
        bob = int(math.sin(self.t * 5) * 1.5)
        pulse = .5 + .5 * math.sin(self.t * 5)
        ring = mix(CYAN, WHITE, pulse)
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            r = big_r + 6 + bob
            s.rect(cx + dx * r - (4 if dx > 0 else 0), cy + dy * r, 5, 1, ring)
            s.rect(cx + dx * r, cy + dy * r - (4 if dy > 0 else 0), 1, 5, ring)
        self.header('SETTORI')
        sc = s.fit_scale(name, s.W // 2, 2 if roomy else 1)
        y = cy + big_r + (8 if roomy else 5)
        s.big_center(name, y, sc, color=WHITE, color2=PINK, shadow=(40, 0, 70))
        state = self.status.get(name, '...')
        row = min((y + 7 * sc) // 2 + 1, s.rows - 3)
        s.text_center(row, state.upper(), GREEN if state in ('presente', 'simulazione') else GOLD)
        prefix = name + '/'
        tests = [v for k, v in self.save.get('tests', {}).items() if k.startswith(prefix)]
        wins, loss = sum(v.get('passes', 0) for v in tests), sum(v.get('fails', 0) for v in tests)
        s.text_center(row + 1, 'vittorie %d   sconfitte %d' % (wins, loss), DIM)
        self.footer('Frecce: scegli   Invio: entra   Ctrl+C: esci')

    def key_projects(self, k):
        if k in ('UP', 'LEFT'):
            self.project = (self.project - 1) % len(self.projects)
            self.sound.sfx('move')
        elif k in ('DOWN', 'RIGHT'):
            self.project = (self.project + 1) % len(self.projects)
            self.sound.sfx('move')
        elif k == 'ENTER':
            self.sound.sfx('select')
            self.sound.sfx('warp', .8)
            self.load_token += 1
            self.loaded = None
            threading.Thread(target=self.load_tests, args=(self.load_token, self.project), daemon=True).start()
            self.go('loading')

    def load_tests(self, token, index):
        try:
            items, message = discover(*self.projects[index])
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            items, message = [], str(e).replace('\n', ' ')
        if token == self.load_token:
            self.loaded = (items, message)

    # --- scene: caricamento -------------------------------------------------
    def draw_loading(self, dt):
        s = self.s
        s.fb[:] = (4, 4, 22)
        self.stars.update(dt, 0, .9)
        self.stars.draw(s, streak=9, t=self.t)
        s.big_center('WARP!', s.H // 2 - 12, s.fit_scale('WARP!', s.W - 8, 3), color=WHITE, color2=CYAN)
        s.text_center(s.rows // 2 + 3, 'Lettura dei test' + '.' * (int(self.t * 3) % 4), DIM)
        self.footer('Esc: annulla')
        if self.loaded and self.scene_t > .7:
            items, self.message = self.loaded
            if merge_past(self.save, *self.projects[self.project]):
                self.write_save()
            self.levels = build_levels(self.projects[self.project][0], items)
            self.level = 0
            if len(self.levels) > 1:
                self.go('levels')
            else:
                self.open_level(0)

    def key_loading(self, k):
        if k == 'ESC':
            self.load_token += 1
            self.sound.sfx('back')
            self.go('projects')

    # --- scene: livelli -----------------------------------------------------
    def open_level(self, index):
        self.level = index
        name = self.projects[self.project][0]
        rows = level_rows(name, self.levels[index]) if self.levels else []
        self.items = [(label, key) for label, key, _ in rows]
        self.groups = {key for _, key, group in rows if group}
        self.choice = 0
        self.go('tests')

    def level_progress(self, level):
        done = sum(1 for _, key in level['entries'] if self.record(key).get('last') == 'pass')
        return done, len(level['entries'])

    def draw_levels(self, dt):
        s = self.s
        self.space(dt, .012, .01)
        name = self.projects[self.project][0]
        self.header('LIVELLI')
        s.text(s.cols - len(name) - 2, 2, name, PINK)
        right = s.W >= 90
        lw = s.cols - 34 if right else s.cols - 4
        s.panel(2, 12, lw, s.H - 17)
        count = self.list_rows()
        start = max(0, min(self.level - count // 2, len(self.levels) - count))
        for i in range(start, min(len(self.levels), start + count)):
            row = 7 + i - start
            level = self.levels[i]
            done, total = self.level_progress(level)
            complete = done == total
            selected = i == self.level
            if selected:
                s.rect(3, row * 2, lw - 2, 2, mix((30, 40, 120), (80, 40, 150), .5 + .5 * math.sin(self.t * 4)))
                s.text(4, row, '>', GOLD)
            s.text(6, row, '*' if complete else '%d' % (i + 1) if i < 9 else '.', GOLD if complete else DIM)
            s.text(8, row, level['title'][:lw - 28], GOLD if complete else WHITE if selected else (175, 185, 215))
            tally = '%d/%d' % (done, total)
            s.text(lw - 19, row, tally, GREEN if done else DIM)
            bar = 10
            s.rect(lw - 11, row * 2 + 1, bar, 1, (40, 44, 90))
            s.rect(lw - 11, row * 2 + 1, bar * done // total, 1, GOLD if complete else GREEN)
        level = self.levels[self.level]
        s.text(3, 5, '%d / %d' % (self.level + 1, len(self.levels)), CYAN)
        if right:
            x = lw + 5
            s.panel(x, 12, s.W - x - 2, s.H - 17, PINK)
            cx = x + (s.W - x - 2) // 2
            boss = self.boss_sprite(self.seed_of(level['title']))
            s.blit(boss, cx - boss[1].shape[1] // 2, 18 + math.sin(self.t * 2.4) * 2)
            s.text_center(20, 'BOSS DI LIVELLO', PINK, cx=cx)
            s.text_center(22, '%d missioni' % len(level['entries']), WHITE, cx=cx)
            if level['id'] is not None:
                rec = self.record(level['id'])
                s.text_center(23, 'vinte %d  perse %d' % (rec.get('passes', 0), rec.get('fails', 0)), DIM, cx=cx)
        self.footer('Invio: apri   Spazio: gioca tutto   Esc: indietro')

    def key_levels(self, k):
        if k == 'ESC':
            self.sound.sfx('back')
            self.go('projects')
        elif k in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            self.level = (self.level + (1 if k in ('DOWN', 'RIGHT') else -1)) % len(self.levels)
            self.sound.sfx('move')
        elif k == 'ENTER':
            self.sound.sfx('select')
            self.open_level(self.level)
        elif k == 'SPACE' and self.levels[self.level]['id'] is not None:
            self.sound.sfx('select')
            self.open_level(self.level)
            self.go('confirm')

    # --- scene: missioni ----------------------------------------------------
    def list_rows(self):
        return max(3, self.s.rows - 9)

    def draw_tests(self, dt):
        s = self.s
        self.space(dt, 0, .02)
        name = self.projects[self.project][0]
        self.header('MISSIONI')
        title = self.levels[self.level]['title'] if self.levels else name
        s.text(s.cols - len(title) - 2, 2, title, PINK)
        right = s.W >= 90
        lw = s.cols - 34 if right else s.cols - 4
        s.panel(2, 12, lw, s.H - 17)
        if not self.items:
            for i in range(0, min(len(self.message), lw * 6), lw - 6):
                s.text(5, 8 + i // (lw - 6), self.message[i:i + lw - 6], GOLD)
            self.footer('Esc: indietro')
            return
        count = self.list_rows()
        start = max(0, min(self.choice - count // 2, len(self.items) - count))
        for i in range(start, min(len(self.items), start + count)):
            row = 7 + i - start
            label, key = self.items[i]
            rec = self.record(key)
            mark, mcol = ('*', GOLD) if rec.get('last') == 'pass' else ('x', RED) if rec.get('last') == 'fail' else ('.', DIM)
            if i == self.choice:
                s.rect(3, row * 2, lw - 2, 2, mix((30, 40, 120), (80, 40, 150), .5 + .5 * math.sin(self.t * 4)))
                s.text(4, row, '>', GOLD)
            s.text(6, row, mark, mcol)
            shade = GOLD if key in self.groups else WHITE if i == self.choice else (175, 185, 215)
            s.text(8, row, label[:lw - 10], shade)
        s.text(3, 5, '%d / %d' % (self.choice + 1, len(self.items)), CYAN)
        if right:
            x = lw + 5
            s.panel(x, 12, s.W - x - 2, s.H - 17, PINK)
            cx = x + (s.W - x - 2) // 2
            label, key = self.items[self.choice]
            boss = self.boss_sprite(self.seed_of(key))
            s.blit(boss, cx - boss[1].shape[1] // 2, 18 + math.sin(self.t * 2.4) * 2)
            col = cx
            rec = self.record(key)
            s.text_center(20, 'INTERO GRUPPO' if key in self.groups else 'BERSAGLIO', PINK, cx=col)
            s.text_center(22, 'vinte %d  perse %d' % (rec.get('passes', 0), rec.get('fails', 0)), WHITE, cx=col)
            best = rec.get('best')
            s.text_center(23, 'miglior tempo ' + (format_clock(best) if best is not None else '--:--'), GOLD, cx=col)
        self.footer('Frecce: scegli   Invio: briefing   Esc: indietro')

    def key_tests(self, k):
        if k == 'ESC':
            self.sound.sfx('back')
            self.go('levels' if len(self.levels) > 1 else 'projects')
        elif self.items and k in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            step = {'UP': -1, 'DOWN': 1, 'LEFT': -self.list_rows(), 'RIGHT': self.list_rows()}[k]
            if abs(step) == 1:
                self.choice = (self.choice + step) % len(self.items)
            else:
                self.choice = max(0, min(len(self.items) - 1, self.choice + step))
            self.sound.sfx('move')
        elif self.items and k == 'ENTER':
            self.sound.sfx('select')
            self.go('confirm')

    # --- scene: briefing e conto alla rovescia ------------------------------
    def draw_confirm(self, dt):
        s = self.s
        self.space(dt, 0, .03)
        name = self.projects[self.project][0]
        s.big_center('PRONTO?', 3, 1 if s.rows < 30 else 2, color=(255, 240, 120), color2=(255, 90, 90), shadow=(60, 0, 40))
        top = 7 if s.rows < 30 else 10
        s.text_center(top, self.items[self.choice][0][:s.cols - 6], WHITE)
        s.text_center(top + 2, 'Browser visibile. I test possono modificare dati di test.', GOLD)
        if name in BRIEFINGS:
            s.text_center(top + 3, BRIEFINGS[name][:s.cols - 6], GOLD)
        if self.items[self.choice][1] in self.groups:
            s.text_center(top + 1, 'Livello ' + self.levels[self.level]['title'][:s.cols - 14], CYAN)
            s.text_center(top + 4, 'Piu setup e dipendenze. Un errore salta solo chi ne dipende.', GOLD)
        boss = self.boss_sprite(self.seed_of(self.items[self.choice][1]))
        s.blit(boss, s.W // 2 - boss[1].shape[1] // 2, (top + 5) * 2 + 2 + math.sin(self.t * 3) * 2)
        if int(self.t * 3) % 3:
            s.text_center(s.rows - 3, 'INVIO = avvia   |   ESC = annulla', WHITE)
        self.footer('')

    def key_confirm(self, k):
        if k == 'ESC':
            self.sound.sfx('back')
            self.go('tests')
        elif k == 'ENTER':
            self.count_step = -1
            self.go('countdown')

    def draw_countdown(self, dt):
        s = self.s
        self.space(dt, 0, .05 + self.scene_t * .3)
        step = int(self.scene_t / .55)
        if step != self.count_step:
            self.count_step = step
            if step < 3:
                self.sound.sfx('count')
            elif step == 3:
                self.sound.sting('go')
        word = ('3', '2', '1', 'VIA!')[min(step, 3)]
        pop = 1 - (self.scene_t % .55) / .55
        sc = max(2, s.fit_scale(word, s.W - 10, 8) - (0 if pop > .3 else 1))
        s.big_center(word, s.H // 2 - 4 * sc, sc, color=WHITE, color2=PINK if step < 3 else GREEN, shadow=(30, 0, 70))
        s.blit(SHIP, s.W // 2 - 5, s.H - 14 - (self.scene_t ** 3))
        self.fade = 0
        if self.scene_t >= 2.3:
            self.launch()

    def key_countdown(self, k):
        if k == 'ESC':
            self.sound.sfx('back')
            self.go('confirm')

    # --- avvio del test ------------------------------------------------------
    def launch(self):
        self.lines = ['Preparazione ' + self.projects[self.project][0] + '...']
        self.q = queue.Queue()
        self.proc = None
        self.exit_code = None
        self.launch_error = None
        self.abort = False
        self.stopping = False
        self.reader_done = threading.Event()
        self.events = []
        self.steps = []
        self.late_titles = False
        self.total = 0
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.weapon = 1
        self.bombs = 1
        self.boss = None
        self.banner = None
        self.toast = None
        self.next_event_at = 0.
        self.started = time.time()
        self.elapsed = 0.
        self.ship_x, self.ship_y = .5, .85
        self.ship_vx = self.ship_vy = 0.
        self.last_input = -99.
        self.enemies, self.shots, self.foes, self.fireworks = [], [], [], []
        self.spawn_t = self.fire_t = 0.
        self.shake = self.flash = self.sad = self.joy = self.hit_t = self.beam = 0.
        self.log_view = False
        self.log_scroll = 0
        self.warp = 1.
        self.sound.music('music_battle')
        self.go('battle')
        self.starter = threading.Thread(target=self.start_process, args=(self.project, self.items[self.choice][1], self.q), daemon=True)
        self.starter.start()

    def start_process(self, index, selected, q):
        name, root = self.projects[index]
        try:
            self.tunnel = hook('prepare', name, selected)
            if self.abort:
                stop_group(self.tunnel)
                self.tunnel = None
                self.reader_done.set()
                return
            cmd, env, cwd = command(name, root, selected, selected in self.groups)
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1,
                                    **SPAWN)
            self.proc = proc
            if self.abort:  # Esc premuto proprio mentre il processo partiva
                stop_group(proc)
                stop_group(self.tunnel)
                self.tunnel = None
            for line in proc.stdout:
                q.put(ANSI.sub('', line).rstrip())
            self.reader_done.set()
        except (OSError, RuntimeError) as e:
            q.put('ERRORE: ' + str(e))
            self.launch_error = str(e)
            stop_group(self.tunnel)
            self.tunnel = None
            self.reader_done.set()

    def request_stop(self):
        if self.exit_code is not None:
            # il test è già finito: l'esito resta quello vero, Esc salta solo le animazioni
            while self.events:
                self.next_event_at = 0
                self.next_event()
            self.banner = None
            return
        if self.stopping:
            return
        self.stopping = self.abort = True
        self.lines.append('Arresto richiesto...')

        def work():
            stop_group(self.proc)
            stop_group(self.tunnel)
            self.tunnel = None
            self.reader_done.set()
        threading.Thread(target=work, daemon=True).start()

    # --- battaglia: eventi dai test ------------------------------------------
    def pump_output(self):
        while True:
            try:
                line = self.q.get_nowait()
            except queue.Empty:
                break
            self.lines.append(line)
            event = parse_line(line)
            if event:
                self.events.append(event)
        self.lines = self.lines[-1000:]

    def field(self):
        s = self.s
        return (s.W - 40 if s.W >= PANEL_MIN_WIDTH else s.W), (s.H if s.W >= PANEL_MIN_WIDTH else s.H - 14)

    def next_event(self):
        """Gli eventi passano uno alla volta: ogni test ha il tempo di avere il suo momento."""
        if not self.events or self.t < self.next_event_at:
            return
        e = self.events.pop(0)
        backlog = len(self.events)
        if e['kind'] == 'total':
            self.total = e['total']
        elif e['kind'] == 'step':
            self.total = e['total']
            self.steps.append({'n': e['n'], 'title': e['title'], 'status': 'run', 'time': '', 'message': ''})
            self.spawn_boss(e['title'], e['n'])
            self.next_event_at = self.t + (.05 if backlog else 0)
        else:
            self.total = e.get('total', self.total)
            if not self.steps or self.steps[-1]['status'] != 'run':
                self.steps.append({'n': len(self.steps) + 1, 'title': e.get('title', 'test'), 'status': 'run', 'time': '', 'message': ''})
                self.spawn_boss(self.steps[-1]['title'], self.steps[-1]['n'])
            step = self.steps[-1]
            if 'title' in e:  # questi reporter dicono il nome solo a fine test
                self.late_titles = True
                step['title'] = e['title']
            step.update(status=e['status'], time=e['time'], message=e['message'], where=e.get('where', ''))
            if e['status'] == 'pass':
                self.on_pass(step)
                pause = 2.
            elif e['status'] == 'fail':
                self.on_fail(step)
                pause = 3.
            else:
                self.on_skip(step)
                pause = .12
            self.next_event_at = self.t + (pause if backlog < 4 else pause * .3)

    def next_blind_boss(self):
        """Senza riga di inizio test, il boss successivo arriva appena finisce l'animazione."""
        n = len(self.steps) + 1
        if (self.late_titles and n <= self.total and not self.events and self.t >= self.next_event_at
                and self.steps[-1]['status'] != 'run' and self.exit_code is None and not self.stopping):
            self.steps.append({'n': n, 'title': 'Missione %d' % n, 'status': 'run', 'time': '', 'message': ''})
            self.spawn_boss(self.steps[-1]['title'], n)

    def spawn_boss(self, title, n):
        gw, _ = self.field()
        seed = self.seed_of(title) + n
        self.boss = {'sprite': self.boss_sprite(seed, 21 if gw < 70 else 25, 15 if gw < 70 else 17), 'x': .5, 'y': -20.,
                     'born': self.t, 'flash': 0., 'title': title, 'n': n, 'state': 'alive', 'hp': 1.}
        if self.banner is None or self.banner['kind'] == 'phase':
            self.banner = {'kind': 'phase', 't': self.t, 'until': self.t + 1.3, 'title': title, 'n': n}
        self.sound.sfx('boss_in', .6)

    def boss_center(self):
        b = self.boss
        gw, _ = self.field()
        h, w = b['sprite'][1].shape
        return b['x'] * (gw - w) + w / 2, b['y'] + h / 2

    def on_pass(self, step):
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.score += 1000 * self.combo
        self.bombs = min(3, self.bombs + 1)
        self.weapon = min(3, self.weapon + 1)
        self.sound.sting('test_pass%d' % min(3, self.combo), 1.)
        self.sound.sfx('boss_boom', .6)
        gw, gh = self.field()
        if self.boss:
            x, y = self.boss_center()
            self.boss['state'] = 'dead'
            self.fx.emit(x, y, 170, 62, PARTY, 1.3, 16)
            self.fx.emit(x, y, 90, 30, [(255, 255, 255), (255, 220, 120)], .7)
        for i in range(7):  # fuochi d'artificio in sequenza
            self.fireworks.append((self.t + .12 + i * .2, random.uniform(.12, .88) * gw, random.uniform(.12, .55) * gh))
        for x in range(0, gw, 3):  # coriandoli dall'alto
            self.fx.emit(x, -random.random() * 8, 1, 3, PARTY, 2.2, 22, 1.2, math.pi / 2)
        self.foes.clear()
        for e in self.enemies:
            self.fx.emit(e['x'], e['y'], 14, 28, PARTY, .7)
            self.score += 50
        self.enemies.clear()
        self.joy = 1.
        self.warp = 1.
        self.flash = max(self.flash, .35)
        self.banner = {'kind': 'pass', 't': self.t, 'until': self.t + 1.9, 'title': step['title'], 'time': step['time'], 'combo': self.combo}

    def on_fail(self, step):
        self.combo = 0
        self.weapon = max(1, self.weapon - 1)
        self.sound.sting('test_fail', .78)
        self.sound.sfx('hurt')
        if self.boss:
            self.boss['state'] = 'gloat'
        self.beam = 1.
        self.shake = 1.
        self.sad = 1.
        self.hit_t = 2.
        self.flash = .55
        self.foes.clear()
        gw, gh = self.field()
        self.fx.emit(self.ship_x * gw, self.ship_y * gh, 90, 44, [(255, 90, 60), (255, 190, 80), (120, 120, 130)], 1.2, 12)
        self.banner = {'kind': 'fail', 't': self.t, 'until': self.t + 2.9, 'title': step['title'], 'time': step['time'], 'message': step['message']}

    def on_skip(self, step):
        self.sound.sfx('test_skip', .5)
        if self.boss:
            self.boss['state'] = 'leave'
        self.toast = ('SALTATO  ' + step['title'], self.t + 1.)

    # --- battaglia: gioco -----------------------------------------------------
    def update_battle(self, dt):
        self.pump_output()
        self.next_event()
        self.next_blind_boss()
        if self.exit_code is None and not self.stopping:
            self.elapsed = time.time() - self.started
        gw, gh = self.field()
        self.shake = max(0., self.shake - dt * 1.7)
        self.flash = max(0., self.flash - dt * 1.8)
        self.sad = max(0., self.sad - dt * .34)
        self.joy = max(0., self.joy - dt * .55)
        self.hit_t = max(0., self.hit_t - dt)
        self.beam = max(0., self.beam - dt * 1.9)
        self.warp = max(0., self.warp - dt * .8)
        if self.banner and self.t > self.banner['until']:
            self.banner = None

        # nave: tasti se li usi, pilota automatico se stai guardando il browser
        if self.t - self.last_input > 2.5:
            target = None
            if self.boss and self.boss['state'] == 'alive' and self.boss['y'] > 0:
                target = self.boss['x']
            near = [e for e in self.enemies if e['y'] > gh * .25]
            if near:
                target = max(near, key=lambda e: e['y'])['x'] / gw
            danger = [f for f in self.foes if abs(f['x'] / gw - self.ship_x) < .06 and f['y'] > gh * .55]
            if danger:
                target = self.ship_x + (.2 if danger[0]['x'] / gw < self.ship_x else -.2)
            if target is not None:
                self.ship_vx += (min(.92, max(.08, target)) - self.ship_x) * dt * 9
            self.ship_y += (.85 - self.ship_y) * dt * 2
        self.ship_vx *= .86 ** (dt * FPS)
        self.ship_vy *= .84 ** (dt * FPS)
        self.ship_x = min(.96, max(.04, self.ship_x + self.ship_vx * dt))
        self.ship_y = min(.92, max(.45, self.ship_y + self.ship_vy * dt))
        sx, sy = self.ship_x * gw, self.ship_y * gh

        # il boss prende colpi per spettacolo, ma muore solo se il test passa
        b = self.boss
        if b:
            age = self.t - b['born']
            if b['state'] == 'alive':
                b['y'] += (9 - b['y']) * dt * 3
                b['x'] = .5 + .33 * math.sin(age * .8)
                b['hp'] = max(.1, 1 - age / (age + 22))
                if age > 1.5 and random.random() < dt * 1.3:
                    x, y = self.boss_center()
                    self.foes.append({'x': x, 'y': y + 6, 'vx': (sx - x) * .35, 'vy': 34.})
            elif b['state'] == 'gloat':
                b['x'] += math.sin(self.t * 22) * .002
                b['y'] += (11 - b['y']) * dt * 3
                if self.t - b['born'] > 0 and self.beam == 0 and not self.banner:
                    b['state'] = 'leave'
            elif b['state'] == 'leave':
                b['y'] -= 70 * dt
                if b['y'] < -24:
                    self.boss = None
            else:
                self.boss = None
            if self.boss:
                b['flash'] = max(0., b['flash'] - dt * 7)

        # ondate di nemici
        self.spawn_t -= dt
        if self.spawn_t <= 0 and len(self.enemies) < 7 and not (self.banner and self.banner['kind'] in ('pass', 'fail')):
            self.spawn_t = random.uniform(.7, 1.6)
            lane = random.uniform(.1, .9)
            kind = random.randint(0, 3)
            for i in range(random.randint(1, 3)):
                self.enemies.append({'x': (lane + (i - 1) * .08) * gw, 'y': -6. - i * 7, 'kind': kind, 'hp': 1 + (kind == 3),
                                     'phase': random.random() * 6, 'base': (lane + (i - 1) * .08) * gw})

        # fuoco automatico
        self.fire_t -= dt
        if self.fire_t <= 0:
            self.fire_t = .2
            offsets = {1: (0,), 2: (-3, 3), 3: (-5, 0, 5)}[self.weapon]
            for o in offsets:
                self.shots.append([sx + o, sy - 5, o * 2.2])
            self.sound.sfx('shoot', .35)
        for shot in self.shots:
            shot[1] -= 120 * dt
            shot[0] += shot[2] * dt
        self.shots = [p for p in self.shots if p[1] > -2]
        for e in self.enemies:
            e['y'] += (15 + e['kind'] * 3) * dt
            e['x'] = e['base'] + math.sin(self.t * 2 + e['phase']) * 9
            if e['kind'] == 2 and random.random() < dt * .35 and e['y'] > 0:
                self.foes.append({'x': e['x'], 'y': e['y'] + 3, 'vx': 0., 'vy': 40.})
        for f in self.foes:
            f['x'] += f['vx'] * dt
            f['y'] += f['vy'] * dt
        self.foes = [f for f in self.foes if f['y'] < gh]

        # collisioni
        keep = []
        for shot in self.shots:
            hit = False
            for e in self.enemies:
                if abs(e['x'] - shot[0]) < 5 and abs(e['y'] - shot[1]) < 4:
                    e['hp'] -= 1
                    hit = True
                    if e['hp'] <= 0:
                        self.fx.emit(e['x'], e['y'], 22, 34, [hsv(e['kind'] * .2 + .55), (255, 240, 180)], .6)
                        self.score += 100 * (1 + self.combo)
                        self.sound.sfx('explode', .45)
                    break
            if not hit and b and self.boss and b['state'] == 'alive' and b['y'] > 0:
                x, y = self.boss_center()
                h, w = b['sprite'][1].shape
                if abs(shot[0] - x) < w / 2 and abs(shot[1] - y) < h / 2:
                    hit = True
                    b['flash'] = 1.
                    self.score += 5
                    self.fx.emit(shot[0], shot[1], 3, 22, [(255, 255, 200), (255, 150, 90)], .25)
            if not hit:
                keep.append(shot)
        self.shots = keep
        self.enemies = [e for e in self.enemies if e['hp'] > 0 and e['y'] < gh + 6]
        hurt = any(abs(f['x'] - sx) < 4 and abs(f['y'] - sy) < 4 for f in self.foes) or \
            any(abs(e['x'] - sx) < 6 and abs(e['y'] - sy) < 4 for e in self.enemies)
        if hurt and self.hit_t <= 0:
            self.hit_t = 1.1
            self.shake = max(self.shake, .3)
            self.score = max(0, self.score - 100)
            self.foes = [f for f in self.foes if abs(f['x'] - sx) >= 4 or abs(f['y'] - sy) >= 4]
            self.fx.emit(sx, sy, 20, 30, [(255, 120, 80), (255, 230, 120)], .5)
            self.sound.sfx('hurt', .6)

        while self.fireworks and self.fireworks[0][0] <= self.t:
            _, x, y = self.fireworks.pop(0)
            colors = random.sample(PARTY, 2) + [(255, 255, 255)]
            self.fx.emit(x, y, 80, 46, colors, 1.15, 20)
            self.sound.sfx('explode', .22)
        if self.sad > .25:  # pioggia sulla scena dopo un fallimento
            for _ in range(5):
                self.fx.emit(random.random() * gw, 0, 1, 2, [(90, 120, 170), (130, 160, 210)], 1.2, 95, .1, math.pi / 2)
        if self.joy > .3:
            self.fx.emit(random.random() * gw, -1, 3, 4, PARTY, 2., 24, 1.2, math.pi / 2)
        self.fx.emit(sx, sy + 5, 2, 26, [(255, 200, 80), (255, 90, 160)], .3, 0, .6, math.pi / 2)
        self.fx.update(dt)
        self.check_end()

    def check_end(self):
        proc = self.proc
        finished = proc is not None and proc.poll() is not None
        if self.launch_error or (self.abort and self.reader_done.is_set()):
            finished = True
        if not finished:
            return
        if self.exit_code is None:
            self.exit_code = proc.returncode if proc is not None and proc.returncode is not None else -1
            self.finish_at = self.t + 1.5
            if self.tunnel:
                stop_group(self.tunnel)
                self.tunnel = None
            self.lines.append('Processo concluso: codice ' + str(self.exit_code))
        # un figlio rimasto vivo può tenere aperta la pipe: non aspettarlo per sempre
        if not self.reader_done.is_set() and self.t < self.finish_at:
            return
        self.pump_output()
        if self.events and not self.abort:
            return
        if (self.banner and self.banner['kind'] in ('pass', 'fail')) and not self.abort:
            return
        self.show_result()

    def show_result(self):
        counts = {k: sum(1 for x in self.steps if x['status'] == k) for k in ('pass', 'fail', 'skip')}
        if self.abort:
            outcome = 'aborted'
        elif self.launch_error:
            outcome = 'error'
        elif self.exit_code == 0 and not counts['fail']:
            outcome = 'clear'
        else:
            outcome = 'over'
        for x in self.steps:
            if x['status'] == 'run':
                x['status'] = 'stop'
        rank = '-'
        record = False
        if outcome in ('clear', 'over'):
            key = self.projects[self.project][0] + '/' + self.items[self.choice][1]
            rec = self.save.setdefault('tests', {}).setdefault(key, {'passes': 0, 'fails': 0})
            old_best = rec.get('best')
            if outcome == 'clear':
                rec['passes'] += 1
                rec['best'] = self.elapsed if old_best is None else min(old_best, self.elapsed)
                self.score += 5000
                rank = 'S' if old_best is None or self.elapsed <= old_best else 'A'
            else:
                rec['fails'] += 1
                done = counts['pass'] + counts['fail']
                ratio = counts['pass'] / done if done else 0
                rank = 'B' if ratio >= .8 else 'C' if ratio >= .5 else 'D'
            rec['last'] = 'pass' if outcome == 'clear' else 'fail'
            # un livello aggiorna anche ogni singola missione giocata, così la barra avanza
            prefix = self.projects[self.project][0] + '/'
            known = [k for lv in self.levels for _, k in lv['entries']]
            for st in self.steps:
                where = st.get('where')
                hit = st['title'] if st['title'] in known else next((k for k in known if where and k.endswith(where)), None)
                if hit and st['status'] in ('pass', 'fail'):
                    one = self.save['tests'].setdefault(prefix + hit, {'passes': 0, 'fails': 0})
                    one['last'] = st['status']
            # il rapporto di questa partita è già contato qui: non va importato di nuovo
            stamp = past_results(*self.projects[self.project])[0]
            if stamp is not None:
                self.save.setdefault('imports', {})[self.projects[self.project][0]] = stamp
            gain = 100 * counts['pass'] + (250 if outcome == 'clear' else 25)
            self.save['xp'] = self.save.get('xp', 0) + gain
            record = self.score > self.hiscore()
            scores = self.save.setdefault('scores', [])
            scores.append({'score': self.score, 'test': self.items[self.choice][0], 'date': time.strftime('%Y-%m-%d')})
            scores.sort(key=lambda x: -x['score'])
            del scores[5:]
            self.write_save()
        self.result = {'outcome': outcome, 'counts': counts, 'rank': rank, 'record': record}
        self.shown_score = 0
        self.sound.music(None)
        if outcome == 'clear':
            self.sound.sting('stage_clear')
            for i in range(14):
                self.fireworks.append((self.t + .2 + i * .34, random.uniform(.1, .9) * self.s.W, random.uniform(.1, .5) * self.s.H))
        elif outcome == 'over':
            self.sound.sting('game_over', .85)
        else:
            self.sound.sfx('back')
        self.log_view = False
        self.go('result')

    def draw_enemy(self, e):
        s = self.s
        c = hsv(e['kind'] * .2 + .55, .8, 1)
        x, y = int(e['x']), int(e['y'])
        flap = int(self.t * 6 + e['phase']) % 2
        s.rect(x - 2, y - 1, 5, 3, c)
        s.rect(x - 1, y, 3, 1, (255, 250, 200))
        s.rect(x - 4, y - flap, 2, 2, mix(c, (0, 0, 0), .3))
        s.rect(x + 3, y - flap, 2, 2, mix(c, (0, 0, 0), .3))

    def draw_battle(self, dt):
        self.update_battle(dt)
        if self.scene != 'battle':
            return
        s = self.s
        gw, gh = self.field()
        if self.log_view:
            self.draw_log('LOG DEL TEST', 'Tab: torna al gioco   Su/Giu: scorri   Esc: ferma il test')
            return
        s.fb[:] = (5, 5, 20) if self.sad < .1 else (6, 7, 18)
        self.stars.update(dt, 0, .07 + self.warp * .7)
        self.stars.draw(s, width=gw, streak=int(self.warp * 6), t=self.t)
        b = self.boss
        if b:
            h, w = b['sprite'][1].shape
            bx = b['x'] * (gw - w)
            s.blit(b['sprite'], bx, b['y'], tint=(255, 255, 255) if b['flash'] > .5 else None)
            if b['state'] == 'alive' and b['y'] > 0:
                bw = int(gw * .5)
                x0 = (gw - bw) // 2
                s.rect(x0 - 1, 3, bw + 2, 3, (50, 20, 40))
                s.rect(x0, 4, int(bw * b['hp']), 1, mix(RED, GOLD, b['hp']))
        for e in self.enemies:
            self.draw_enemy(e)
        for f in self.foes:
            s.rect(f['x'] - 1, f['y'] - 1, 2, 2, (255, 90, 90) if int(self.t * 12) % 2 else (255, 220, 120))
        for shot in self.shots:
            s.rect(shot[0], shot[1], 1, 3, (180, 255, 255))
        sx, sy = self.ship_x * gw, self.ship_y * gh
        if self.beam > 0 and b:
            x, y = self.boss_center()
            for i in range(40):
                t = i / 40
                px = x + (sx - x) * t + math.sin(i + self.t * 40) * 1.5
                py = y + (sy - y) * t
                s.rect(px - 1, py, 3, 2, mix((255, 255, 255), RED, 1 - self.beam))
        wobble = math.sin(self.t * 9) * 2 if self.sad > .25 else 0
        if self.hit_t <= 0 or int(self.t * 14) % 2:
            s.blit(SHIP, sx - 5, sy - 4 + wobble, tint=(120, 130, 150) if self.sad > .5 else None)
        self.fx.draw(s, gw)
        if self.sad > 0:
            s.desaturate(min(.85, self.sad * 1.3), gw)
        self.draw_banner(gw, gh)
        if self.flash > 0:
            col = RED if self.sad > .5 else (255, 255, 230)
            s.overlay(col, self.flash * .6, gw)
        if self.shake > 0:
            amp = int(self.shake * 4) + 1
            s.fb[:, :gw] = np.roll(np.roll(s.fb[:, :gw], random.randint(-amp, amp), axis=1), random.randint(-amp, amp), axis=0)
        self.draw_hud(gw, gh)

    def draw_banner(self, gw, gh):
        s = self.s
        bn = self.banner
        cols = gw
        if self.toast and self.t < self.toast[1]:
            s.text_center(4, self.toast[0][:cols - 2], GREY, cx=cols // 2)
        if not bn:
            if not self.steps and self.exit_code is None and self.proc is None:
                s.text_center(gh // 4, 'Apertura collegamento...', CYAN, cx=cols // 2)
            elif not self.steps and self.exit_code is None:
                s.text_center(gh // 4, 'In attesa del primo test...', CYAN, cx=cols // 2)
            return
        age = self.t - bn['t']
        cx = gw // 2
        if bn['kind'] == 'phase':
            label = 'FASE %d' % bn['n'] + ('/%d' % self.total if self.total else '')
            sc = s.fit_scale(label, gw - 10, 2)
            s.rect(0, gh // 2 - 11, gw, 22, (10, 12, 44), .78)
            s.big_center(label, gh // 2 - 8, sc, cx=cx, color=WHITE, color2=CYAN, shadow=(10, 10, 60))
            s.text_center(gh // 4 + 3, bn['title'][:cols - 4], CYAN, cx=cols // 2)
        elif bn['kind'] == 'pass':
            word = 'SUPERATO!'
            sc = s.fit_scale(word, gw - 6, 3)
            pop = max(0., 1 - age / .3)
            y = max(4, gh // 2 - 18) - int(pop * 14)
            s.rect(0, y - 3, gw, 7 * sc + 19, (6, 40, 34), .72)
            s.big_center(word, y, sc, cx=cx, shadow=(0, 50, 20),
                         color=lambda i: hsv((self.t * .8 + i * .09) % 1, .55, 1),
                         offsets=lambda i: (0, int(math.sin(self.t * 11 + i * .9) * 2.5)))
            row = (y + 7 * sc) // 2 + 2
            s.text_center(row, bn['title'][:cols - 4], WHITE, cx=cols // 2)
            detail = ('tempo ' + bn['time'] + '   ' if bn['time'] else '') + '+%d' % (1000 * bn['combo'])
            s.text_center(row + 1, detail, GREEN, cx=cols // 2)
            if bn['combo'] > 1:
                s.text_center(row + 2, 'COMBO x%d' % bn['combo'], GOLD, cx=cols // 2)
        elif bn['kind'] == 'fail':
            word = 'FALLITO'
            sc = s.fit_scale(word, gw - 6, 3)
            y = max(4, gh // 2 - 18)
            s.rect(0, y - 3, gw, 7 * sc + 22, (44, 6, 16), .78)

            def sag(i):
                # le lettere tremano, poi una alla volta cedono e scivolano giù
                if age < .45:
                    return random.randint(-2, 2), random.randint(-1, 1)
                fall = max(0., age - .55 - ((i * 5) % 7) * .11)
                return 0, int(min(5 * sc, fall * fall * 42))
            s.big_center(word, y, sc, cx=cx, color=(255, 90, 90), color2=(130, 10, 40), shadow=(40, 0, 10), offsets=sag)
            row = (y + 7 * sc) // 2 + 3
            s.text_center(row, bn['title'][:cols - 4], WHITE, cx=cols // 2)
            msg = bn['message'] or 'Controlla il log con Tab'
            s.text_center(row + 1, msg[:cols - 4], (255, 170, 170), cx=cols // 2)
            s.text_center(row + 2, 'combo persa', GREY, cx=cols // 2)

    def draw_hud(self, gw, gh):
        s = self.s
        done = sum(1 for x in self.steps if x['status'] != 'run')
        s.rect(0, 0, gw, 2, (6, 6, 24), .9)
        phase = '%d/%d' % (len(self.steps), self.total) if self.total else str(len(self.steps))
        ok = sum(1 for x in self.steps if x['status'] == 'pass')
        ko = sum(1 for x in self.steps if x['status'] == 'fail')
        s.text(1, 0, '%07d' % self.score, GOLD)
        s.text(10, 0, 'x%d' % max(1, self.combo), PINK)
        s.text(15, 0, 'FASE ' + phase, CYAN)
        s.text(27, 0, '+%d' % ok, GREEN)
        s.text(32, 0, 'x%d' % ko, RED if ko else DIM)
        s.text(gw - 12, 0, 'B%d ' % self.bombs + format_clock(self.elapsed), WHITE)
        if self.total:
            s.rect(0, 2, int(gw * done / self.total), 1, GREEN if not ko else GOLD)
        if self.stopping:
            s.text_center(2, 'ARRESTO IN CORSO...', RED, cx=gw // 2)
        if s.W >= PANEL_MIN_WIDTH:
            x0, width, top, bottom = gw, 40, 0, s.rows
            s.rect(x0, 0, width, s.H, (8, 9, 28))
            s.rect(x0, 0, 1, s.H, (60, 70, 140))
        else:
            x0, width, top, bottom = 0, gw, (s.H - 14) // 2, s.rows
            s.rect(0, s.H - 14, gw, 14, (8, 9, 28))
            s.rect(0, s.H - 14, gw, 1, (60, 70, 140))
        col = x0 + 2
        span = width - 3
        row = top + 1
        if s.W >= PANEL_MIN_WIDTH:
            s.text(col, row, self.items[self.choice][0][:span], WHITE)
            row += 2
            rows = max(3, (bottom - row) // 2 - 1)
            for st in self.steps[-rows:]:
                mark, c = {'pass': ('+', GREEN), 'fail': ('x', RED), 'skip': ('-', GREY), 'run': ('>', GOLD)}.get(st['status'], ('?', GREY))
                if st['status'] == 'run' and int(self.t * 4) % 2:
                    mark = ' '
                s.text(col, row, mark, c)
                s.text(col + 2, row, st['title'][:span - 2], c if st['status'] != 'skip' else GREY)
                row += 1
            row = top + 3 + rows + 1
            s.rect(x0 + 1, row * 2 - 1, width - 2, 1, (40, 46, 100))
        tail = self.lines[-max(1, bottom - row - 1):]
        for i, line in enumerate(tail):
            s.text(col, row + i, line[:span], self.log_color(line))
        s.text(col, s.rows - 1, 'Tab log  Esc ferma  M audio  Spazio bomba'[:span], DIM)

    @staticmethod
    def log_color(line):
        if '✅' in line or 'PASSED' in line:
            return GREEN
        if '❌' in line or 'ERRORE' in line or 'FAILED' in line or 'Error' in line:
            return (255, 130, 130)
        if '▶' in line:
            return CYAN
        return (165, 175, 205)

    def key_battle(self, k):
        if k == 'ESC':
            self.request_stop()
        elif k == 'TAB':
            self.log_view = not self.log_view
            self.log_scroll = 0
        elif self.log_view:
            self.key_log(k)
        elif k in ('LEFT', 'RIGHT', 'UP', 'DOWN'):
            self.last_input = self.t
            self.ship_vx += {'LEFT': -.42, 'RIGHT': .42}.get(k, 0)
            self.ship_vy += {'UP': -.42, 'DOWN': .42}.get(k, 0)
        elif k == 'SPACE' and self.bombs > 0:
            self.bombs -= 1
            self.last_input = self.t
            for e in self.enemies:
                self.fx.emit(e['x'], e['y'], 24, 40, PARTY, .8)
                self.score += 150
            self.enemies.clear()
            self.foes.clear()
            self.flash = max(self.flash, .3)
            self.sound.sfx('boss_boom', .5)

    def key_log(self, k):
        page = max(1, self.s.rows - 6)
        if k == 'UP':
            self.log_scroll = min(max(0, len(self.lines) - page), self.log_scroll + 1)
        elif k == 'DOWN':
            self.log_scroll = max(0, self.log_scroll - 1)
        elif k == 'LEFT':
            self.log_scroll = min(max(0, len(self.lines) - page), self.log_scroll + page)
        elif k == 'RIGHT':
            self.log_scroll = max(0, self.log_scroll - page)

    def draw_log(self, title, foot):
        s = self.s
        s.fb[:] = (7, 8, 24)
        s.text(2, 1, title, CYAN)
        page = max(1, s.rows - 6)
        end = len(self.lines) - self.log_scroll
        for i, line in enumerate(self.lines[max(0, end - page):end]):
            s.text(2, 3 + i, line[:s.cols - 4], self.log_color(line))
        self.footer(foot)

    # --- risultati ------------------------------------------------------------
    def draw_result(self, dt):
        s = self.s
        r = self.result
        if self.log_view:
            self.draw_log('LOG DEL TEST', 'Tab: risultati   Su/Giu: scorri   Esc: settori')
            return
        out = r['outcome']
        if out == 'clear':
            self.space(dt, 0, .1)
        else:
            s.fb[:] = (10, 6, 16) if out == 'over' else (8, 8, 20)
            self.stars.update(dt, 0, .006)
            self.stars.draw(s, t=self.t)
        while self.fireworks and self.fireworks[0][0] <= self.t:
            _, x, y = self.fireworks.pop(0)
            self.fx.emit(x, y, 90, 50, random.sample(PARTY, 3), 1.3, 20)
            self.sound.sfx('explode', .2)
        if out == 'clear' and random.random() < .7:
            self.fx.emit(random.random() * s.W, -1, 4, 4, PARTY, 2.6, 22, 1.2, math.pi / 2)
        if out == 'over':
            self.fx.emit(random.random() * s.W, 0, 3, 2, [(80, 100, 150), (120, 140, 190)], 1.1, 100, .1, math.pi / 2)
        self.fx.update(dt)
        self.fx.draw(s, s.W)
        word = {'clear': 'VITTORIA!', 'over': 'GAME OVER', 'aborted': 'ANNULLATA', 'error': 'ERRORE'}[out]
        sc = s.fit_scale(word, s.W - 6, 3)
        if out == 'clear':
            s.big_center(word, 4, sc, shadow=(0, 60, 30), color=lambda i: hsv((self.t * .5 + i * .05) % 1, .5, 1),
                         offsets=lambda i: (0, int(math.sin(self.t * 6 + i * .7) * 2)))
        elif out == 'over':
            s.big_center(word, 4, sc, color=(255, 90, 90), color2=(120, 10, 40), shadow=(40, 0, 10),
                         offsets=lambda i: (0, int(min(3, max(0, self.scene_t - i * .18) * 3))))
        else:
            s.big_center(word, 4, sc, color=(200, 210, 230), color2=(110, 120, 160), shadow=(20, 20, 50))
        top = (6 + 7 * sc) // 2 + 2
        c = r['counts']
        s.text_center(top, self.items[self.choice][0][:s.cols - 4], WHITE)
        s.text_center(top + 1, 'superati %d   falliti %d   saltati %d   tempo %s   codice %s' % (
            c['pass'], c['fail'], c['skip'], format_clock(self.elapsed), self.exit_code), (200, 210, 235))
        if out in ('clear', 'over'):
            self.shown_score += max(1, (self.score - self.shown_score) * dt * 5)
            shown = int(min(self.score, self.shown_score + .5))
            if shown < self.score and int(self.t * FPS) % 3 == 0:
                self.sound.sfx('move', .25)
            wide = s.W >= 100
            s.text_center(top + 3, 'PUNTEGGIO %07d' % shown + ('' if wide else '   RANGO ' + r['rank']), GOLD)
            if r['record'] and shown >= self.score and int(self.t * 3) % 2:
                s.text_center(top + 4, '*** NUOVO RECORD ***', PINK)
            rank_col = {'S': GOLD, 'A': GREEN, 'B': CYAN, 'C': (255, 170, 80), 'D': RED}[r['rank']]
            if wide:
                s.big(r['rank'], s.W - 22, (top + 3) * 2, 3, color=WHITE, color2=rank_col, shadow=(30, 20, 60))
        row = top + 6
        failed = [x for x in self.steps if x['status'] == 'fail']
        shown_steps = failed or self.steps
        if self.launch_error:
            s.text_center(row, self.launch_error[:s.cols - 4], (255, 170, 170))
        elif failed:
            s.text(3, row, 'DA GUARDARE', RED)
            row += 1
        for st in shown_steps[-max(0, s.rows - row - 3):]:
            mark, col = {'pass': ('+', GREEN), 'fail': ('x', RED), 'skip': ('-', GREY)}.get(st['status'], ('?', GOLD))
            s.text(3, row, mark, col)
            s.text(5, row, st['title'][:44], WHITE)
            s.text(51, row, (st['time'] + '  ' + st['message'])[:s.cols - (77 if s.W >= 100 and st['status'] != 'fail' else 53)], (255, 170, 170) if st['status'] == 'fail' else DIM)
            row += 1
        self.footer('Invio: missioni   R: riprova   Tab: log   Esc: settori')

    def key_result(self, k):
        if k == 'TAB':
            self.log_view = not self.log_view
            self.log_scroll = 0
        elif self.log_view and k in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            self.key_log(k)
        elif k == 'ESC':
            self.sound.sfx('back')
            self.back_to_menu('projects')
        elif k == 'ENTER':
            self.sound.sfx('select')
            self.back_to_menu('tests')
        elif k == 'r':
            self.sound.sfx('select')
            self.tunnel = self.proc = None
            self.sound.music('music_menu')
            self.go('confirm')

    def back_to_menu(self, scene):
        stop_group(self.tunnel)
        self.tunnel = self.proc = None
        self.fireworks = []
        self.sound.music('music_menu')
        self.go(scene)

    # --- ciclo principale -----------------------------------------------------
    def read_keys(self, wait):
        keys = []
        data = read_input(wait)
        if not data:
            return keys
        data = self.pending + data
        self.pending = b''
        table = ((b'\x1b[A', 'UP'), (b'\x1b[B', 'DOWN'), (b'\x1b[C', 'RIGHT'), (b'\x1b[D', 'LEFT'),
                 (b'\x1bOA', 'UP'), (b'\x1bOB', 'DOWN'), (b'\x1bOC', 'RIGHT'), (b'\x1bOD', 'LEFT'),
                 (b'\x1b[5~', 'LEFT'), (b'\x1b[6~', 'RIGHT'))
        letters = {b'j': 'DOWN', b'k': 'UP', b'h': 'LEFT', b'l': 'RIGHT', b'\r': 'ENTER', b'\n': 'ENTER',
                   b' ': 'SPACE', b'\t': 'TAB', b'm': 'MUTE', b'M': 'MUTE', b'r': 'r', b'R': 'r'}
        while data:
            for seq, name in table:
                if data.startswith(seq):
                    keys.append(name)
                    data = data[len(seq):]
                    break
            else:
                if data[:1] == b'\x1b':
                    if data[1:2] in (b'[', b'O'):  # sequenza sconosciuta: la scarta intera
                        m = re.match(rb'\x1b[\[O][0-9;]*[ -/]*[@-~]?', data)
                        data = data[m.end():]
                    else:
                        keys.append('ESC')
                        data = data[1:]
                else:
                    name = letters.get(data[:1])
                    if name:
                        keys.append(name)
                    data = data[1:]
        return keys

    def too_small(self):
        s = self.s
        s.clear((8, 8, 24))
        s.text(1, max(0, s.rows // 2 - 1), 'Allarga la finestra'[:s.cols - 2], GOLD)
        s.text(1, max(0, s.rows // 2), 'almeno 80 x 24 (ora %dx%d)' % (s.tc, s.tr), WHITE)

    def frame(self, dt):
        s = self.s
        s.resize()
        s.chars[:] = 0
        if s.cols < 80 or s.rows < 24:
            self.too_small()
            if self.scene == 'battle':
                self.update_battle(dt)
            return
        getattr(self, 'draw_' + self.scene)(dt)
        if self.fade > 0:
            s.overlay((0, 0, 0), min(1., self.fade))
            self.fade = max(0., self.fade - dt * 4.5)

    def loop(self):
        s = self.s
        step = 1 / FPS
        last = time.time()
        while True:
            now = time.time()
            dt = min(.1, now - last)
            last = now
            self.t += dt
            self.scene_t += dt
            self.frame(dt)
            s.present()
            spare = step - (time.time() - now)
            for k in self.read_keys(spare):
                if k == 'MUTE':
                    self.sound.toggle()
                    self.save['muted'] = self.sound.muted
                    self.write_save()
                else:
                    handler = getattr(self, 'key_' + self.scene, None)
                    if handler:
                        handler(k)
            rest = step - (time.time() - now)
            if rest > 0:
                time.sleep(min(rest, step))

    def close(self):
        self.abort = True
        if self.starter:  # il tunnel potrebbe essere in apertura: non lasciarlo orfano
            self.starter.join(timeout=4)
        stop_group(self.proc)
        stop_group(self.tunnel)
        self.sound.close()


def run(projects):
    saved = setup_terminal()
    game = None

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    if hasattr(signal, 'SIGHUP'):  # non esiste su Windows
        signal.signal(signal.SIGHUP, terminate)
    try:
        game = Game(projects)
        game.s.enter()
        game.loop()
    except KeyboardInterrupt:
        pass
    finally:
        if game:
            game.s.leave()
            game.close()
        restore_terminal(saved)
    return 0


def audio_test():
    """Fa suonare il motore vero, per capire se il problema è nel gioco o nell'uscita audio."""
    sound = Sound(False)
    steps = (('Jingle test superato', 'sting', 'test_pass1', 2.6), ('Lamento test fallito', 'sting', 'test_fail', 3.4),
             ('Musica di battaglia', 'music', 'music_battle', 3.5))
    time.sleep(.8)
    for label, kind, name, wait in steps:
        print(label + '...', flush=True)
        getattr(sound, kind)(name)
        time.sleep(wait)
        if not sound.alive:
            print('Motore audio caduto: vedi audio.log')
            return 1
    sound.close()
    print('Motore audio OK. Se non hai sentito nulla, controlla volume e uscita audio del computer.')
    return 0
