"""Motore grafico: framebuffer di pixel disegnato nel terminale con i mezzi blocchi."""
import math
import os
import sys
import unicodedata

import numpy as np

MAX_COLS, MAX_ROWS = 150, 46

FONT = {
    ' ': '0000000000', '0': '3E5149453E', '1': '00427F4000', '2': '4261514946', '3': '2141454B31',
    '4': '1814127F10', '5': '2745454539', '6': '3C4A494930', '7': '0171090503', '8': '3649494936',
    '9': '064949291E', 'A': '7E1111117E', 'B': '7F49494936', 'C': '3E41414122', 'D': '7F4141221C',
    'E': '7F49494941', 'F': '7F09090901', 'G': '3E4149497A', 'H': '7F0808087F', 'I': '00417F4100',
    'J': '2040413F01', 'K': '7F08142241', 'L': '7F40404040', 'M': '7F020C027F', 'N': '7F0408107F',
    'O': '3E4141413E', 'P': '7F09090906', 'Q': '3E4151215E', 'R': '7F09192946', 'S': '4649494931',
    'T': '01017F0101', 'U': '3F4040403F', 'V': '1F2040201F', 'W': '3F4038403F', 'X': '6314081463',
    'Y': '0708700807', 'Z': '6151494543', '!': '00005F0000', '.': '0060600000', ':': '0036360000',
    '-': '0808080808', '/': '2010080402', '?': '0201510906', '+': '08083E0808', '%': '2313086462',
    "'": '0005030000', '>': '4122140800', '<': '0008142241', ',': '0050300000',
}
_glyphs = {}


def glyph(ch, scale):
    key = (ch, scale)
    if key not in _glyphs:
        hexes = FONT.get(ch, FONT['?'])
        cols = [int(hexes[i:i + 2], 16) for i in range(0, 10, 2)]
        g = np.array([[(c >> r) & 1 for c in cols] for r in range(7)], dtype=bool)
        _glyphs[key] = np.repeat(np.repeat(g, scale, axis=0), scale, axis=1)
    return _glyphs[key]


def plain(text):
    """Toglie gli accenti per il font pixel."""
    return ''.join(c for c in unicodedata.normalize('NFD', text.upper()) if not unicodedata.combining(c))


def safe_text(text):
    """Solo caratteri larghi una cella, altrimenti la griglia di testo si sfalsa."""
    swaps = {'✅': '+', '❌': 'x', '⏭': '>'}
    out = []
    for c in text:
        c = swaps.get(c, c)
        if c == '\t':
            out.append('  ')
        elif unicodedata.category(c) in ('Mn', 'Me', 'Cf', 'Cc', 'Cs', 'Co', 'Cn'):
            continue
        elif unicodedata.east_asian_width(c) in ('W', 'F') or ord(c) > 0xFFFF:
            out.append('?')
        else:
            out.append(c)
    return ''.join(out)


def rgb(h):
    return (h >> 16 & 255, h >> 8 & 255, h & 255)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def hsv(h, s=1., v=1.):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))[i]
    return int(r * 255), int(g * 255), int(b * 255)


def truecolor_supported():
    forced = os.environ.get('ARCADE_COLOR')
    if forced:
        return forced != '256'
    if os.name == 'nt' or os.environ.get('COLORTERM', '').lower() in ('truecolor', '24bit'):
        return True  # le console di Windows 10 e successivi gestiscono i 24 bit
    if os.environ.get('TERM_PROGRAM') == 'Apple_Terminal':
        # Terminal.app gestisce i 24 bit da macOS 26 (build 460 in poi)
        try:
            return int(os.environ.get('TERM_PROGRAM_VERSION', '0').split('.')[0]) >= 460
        except ValueError:
            return False
    return os.environ.get('TERM_PROGRAM') in ('iTerm.app', 'WezTerm', 'ghostty', 'vscode', 'Hyper')


class Screen:
    def __init__(self):
        self.truecolor = truecolor_supported()
        self.fg_cache, self.bg_cache = {}, {}
        self.cols = self.rows = 0
        self.bytes = 0
        self.resize()

    def resize(self):
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            tc, tr = size.columns, size.lines
        except OSError:
            tc, tr = 100, 34
        c, r = min(tc, MAX_COLS), min(tr, MAX_ROWS)
        if (c, r, tc, tr) == (self.cols, self.rows, getattr(self, 'tc', 0), getattr(self, 'tr', 0)):
            return False
        self.tc, self.tr, self.cols, self.rows = tc, tr, c, r
        self.ox, self.oy = (tc - c) // 2, (tr - r) // 2
        self.W, self.H = c, r * 2
        self.fb = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        self.chars = np.zeros((r, c), dtype=np.uint32)
        self.fgs = np.zeros((r, c), dtype=np.int32)
        self.prev = None
        y = np.linspace(-1, 1, self.H)[:, None]
        x = np.linspace(-1, 1, self.W)[None, :]
        vign = 1 - .34 * np.clip(x * x * .8 + y * y * .8 - .25, 0, 1)
        scan = np.where(np.arange(self.H) % 2 == 1, .88, 1.)[:, None]
        self.crt = (vign * scan).astype(np.float32)[:, :, None]
        self.write('\x1b[0m\x1b[2J')
        return True

    @staticmethod
    def write(data):
        raw = data.encode('utf-8', 'replace')
        view = memoryview(raw)
        while view:
            try:
                view = view[os.write(1, view):]
            except BlockingIOError:
                pass
        return len(raw)

    def enter(self):
        self.write('\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J')

    def leave(self):
        self.write('\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l')

    # --- primitive ---------------------------------------------------------
    def clear(self, color=(0, 0, 0)):
        self.fb[:] = color
        self.chars[:] = 0

    def rect(self, x, y, w, h, color, alpha=1.):
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(self.W, int(x + w)), min(self.H, int(y + h))
        if x0 >= x1 or y0 >= y1:
            return
        area = self.fb[y0:y1, x0:x1]
        if alpha >= 1:
            area[:] = color
        else:
            area[:] = (area * (1 - alpha) + np.array(color, dtype=np.float32) * alpha).astype(np.uint8)

    def frame(self, x, y, w, h, color):
        self.rect(x, y, w, 1, color)
        self.rect(x, y + h - 1, w, 1, color)
        self.rect(x, y, 1, h, color)
        self.rect(x + w - 1, y, 1, h, color)

    def panel(self, x, y, w, h, border=(70, 220, 255), fill=(8, 10, 30), alpha=.86):
        self.rect(x, y, w, h, fill, alpha)
        self.frame(x, y, w, h, border)

    def mask(self, m, x, y, color, color2=None):
        """Disegna una maschera booleana, con sfumatura verticale opzionale."""
        x, y = int(x), int(y)
        h, w = m.shape
        sx, sy = max(0, -x), max(0, -y)
        ex, ey = min(w, self.W - x), min(h, self.H - y)
        if sx >= ex or sy >= ey:
            return
        sub = m[sy:ey, sx:ex]
        area = self.fb[y + sy:y + ey, x + sx:x + ex]
        if color2 is None:
            area[sub] = color
        else:
            t = (np.arange(sy, ey) / max(1, h - 1)).astype(np.float32)[:, None]
            grad = (np.array(color, np.float32) * (1 - t) + np.array(color2, np.float32) * t).astype(np.uint8)
            rows = np.nonzero(sub)[0]
            area[sub] = grad[rows]

    def blit(self, sprite, x, y, tint=None):
        colors, m = sprite
        x, y = int(x), int(y)
        h, w = m.shape
        sx, sy = max(0, -x), max(0, -y)
        ex, ey = min(w, self.W - x), min(h, self.H - y)
        if sx >= ex or sy >= ey:
            return
        sub = m[sy:ey, sx:ex]
        area = self.fb[y + sy:y + ey, x + sx:x + ex]
        area[sub] = tint if tint is not None else colors[sy:ey, sx:ex][sub]

    @staticmethod
    def width(text, scale=1):
        return len(text) * 6 * scale - scale

    def big(self, text, x, y, scale=1, color=(255, 255, 255), color2=None, shadow=None, offsets=None):
        """Testo pixel. offsets(i) -> (dx, dy) anima ogni lettera per conto suo."""
        text = plain(text)
        for i, ch in enumerate(text):
            dx, dy = offsets(i) if offsets else (0, 0)
            px, py = x + i * 6 * scale + dx, y + dy
            g = glyph(ch, scale)
            if shadow is not None:
                self.mask(g, px + max(1, scale // 2), py + max(1, scale // 2), shadow)
            c = color(i) if callable(color) else color
            self.mask(g, px, py, c, color2)

    def big_center(self, text, y, scale=1, cx=None, **kw):
        cx = self.W // 2 if cx is None else cx
        self.big(text, cx - self.width(plain(text), scale) // 2, y, scale, **kw)

    def fit_scale(self, text, width, top=4):
        return max(1, min(top, (width + 1) // (len(text) * 6)))

    def text(self, col, row, text, color=(235, 240, 255)):
        """Testo nitido, un carattere per cella."""
        col, row = int(col), int(row)
        if not 0 <= row < self.rows:
            return
        packed = color[0] << 16 | color[1] << 8 | color[2]
        for i, ch in enumerate(safe_text(str(text))):
            c = col + i
            if c >= self.cols:
                break
            if c >= 0:
                self.chars[row, c] = ord(ch)
                self.fgs[row, c] = packed

    def text_center(self, row, text, color=(235, 240, 255), cx=None):
        cx = self.cols // 2 if cx is None else cx
        self.text(cx - len(text) // 2, row, text, color)

    # --- effetti -----------------------------------------------------------
    def shake(self, dx, dy):
        self.fb[:] = np.roll(np.roll(self.fb, int(dy), axis=0), int(dx), axis=1)

    def overlay(self, color, alpha, width=None):
        w = width or self.W
        self.fb[:, :w] = (self.fb[:, :w] * (1 - alpha) + np.array(color, np.float32) * alpha).astype(np.uint8)

    def desaturate(self, amount, width=None, tone=(.72, .8, 1.)):
        w = width or self.W
        a = self.fb[:, :w].astype(np.float32)
        gray = (a @ np.array([.3, .59, .11], np.float32))[:, :, None] * np.array(tone, np.float32)
        self.fb[:, :w] = (a * (1 - amount) + gray * amount).astype(np.uint8)

    # --- uscita ------------------------------------------------------------
    def pack(self, px):
        if self.truecolor:
            q = (px & 0xF8).astype(np.int32)
            return q[..., 0] << 16 | q[..., 1] << 8 | q[..., 2]
        q = ((px.astype(np.uint16) * 5 + 127) // 255).astype(np.int32)
        return 16 + q[..., 0] * 36 + q[..., 1] * 6 + q[..., 2]

    def seq(self, value, background):
        cache = self.bg_cache if background else self.fg_cache
        s = cache.get(value)
        if s is None:
            lead = '48' if background else '38'
            if self.truecolor:
                s = '\x1b[%s;2;%d;%d;%dm' % (lead, value >> 16 & 255, value >> 8 & 255, value & 255)
            else:
                s = '\x1b[%s;5;%dm' % (lead, value)
            if len(cache) > 60000:
                cache.clear()
            cache[value] = s
        return s

    def present(self):
        px = (self.fb * self.crt).astype(np.uint8)
        top, bot = self.pack(px[0::2]), self.pack(px[1::2])
        fgs = self.fgs if self.truecolor else self.pack(np.stack(
            [self.fgs >> 16 & 255, self.fgs >> 8 & 255, self.fgs & 255], axis=-1).astype(np.uint8))
        state = (top, bot, self.chars.copy(), fgs.copy())
        if self.prev is None:
            changed = np.ones(top.shape, dtype=bool)
        else:
            p = self.prev
            changed = (top != p[0]) | (bot != p[1]) | (self.chars != p[2]) | (fgs != p[3])
        self.prev = state
        ys, xs = np.nonzero(changed)
        if not len(ys):
            self.bytes = 0
            return
        out = ['\x1b[?2026h']
        tl, bl, cl, fl = top[ys, xs].tolist(), bot[ys, xs].tolist(), self.chars[ys, xs].tolist(), fgs[ys, xs].tolist()
        cy = cx = -1
        cur_fg = cur_bg = None
        seq = self.seq
        ox, oy = self.ox + 1, self.oy + 1
        for y, x, t, b, c, f in zip(ys.tolist(), xs.tolist(), tl, bl, cl, fl):
            if y != cy or x != cx:
                out.append('\x1b[%d;%dH' % (y + oy, x + ox))
            if c:
                if b != cur_bg:
                    out.append(seq(b, True))
                    cur_bg = b
                if f != cur_fg:
                    out.append(seq(f, False))
                    cur_fg = f
                out.append(chr(c))
            elif t == b:
                if b != cur_bg:
                    out.append(seq(b, True))
                    cur_bg = b
                out.append(' ')
            else:
                if t != cur_fg:
                    out.append(seq(t, False))
                    cur_fg = t
                if b != cur_bg:
                    out.append(seq(b, True))
                    cur_bg = b
                out.append('▀')
            cy, cx = y, x + 1
        out.append('\x1b[0m\x1b[?2026l')
        self.bytes = self.write(''.join(out))


# --- arte procedurale ------------------------------------------------------

def sprite(rows, palette):
    h, w = len(rows), max(len(r) for r in rows)
    colors = np.zeros((h, w, 3), dtype=np.uint8)
    m = np.zeros((h, w), dtype=bool)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in palette:
                colors[y, x] = palette[ch]
                m[y, x] = True
    return colors, m


def creature(seed, w, h, hue):
    """Alieno simmetrico generato dal seme: ogni boss ha la sua faccia."""
    r = np.random.default_rng(seed)
    half = (w + 1) // 2
    g = r.random((h, half))
    yy, xx = np.mgrid[0:h, 0:half]
    dist = np.sqrt(((xx - half) / half) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    body = g > (dist * .95 - .12)
    for _ in range(2):  # ammorbidisce i pixel isolati
        n = np.zeros_like(body, dtype=int)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            n += np.roll(np.roll(body, dy, 0), dx, 1)
        body = (body & (n >= 1)) | (n >= 3)
    full = np.concatenate([body, body[:, ::-1][:, (1 if w % 2 else 0):]], axis=1)
    colors = np.zeros((h, full.shape[1], 3), dtype=np.uint8)
    base, dark, light = hsv(hue, .75, .95), hsv(hue, .85, .5), hsv((hue + .08) % 1, .35, 1)
    for y in range(h):
        for x in range(full.shape[1]):
            edge = y == 0 or x == 0 or y == h - 1 or x == full.shape[1] - 1 or not (
                full[y - 1, x] and full[y + 1, x] and full[y, x - 1] and full[y, x + 1])
            colors[y, x] = dark if edge else (light if y < h * .4 else base)
    # occhi
    ey = int(h * .42)
    ex = max(1, int(full.shape[1] * .28))
    for x in (ex, full.shape[1] - 1 - ex):
        size = 2 if w >= 16 else 1
        full[ey:ey + size, x:x + size] = True
        colors[ey:ey + size, x:x + size] = (255, 250, 120)
    return colors, full


class Planet:
    def __init__(self, seed, radius, ramp, ring=False):
        r = np.random.default_rng(seed)
        self.radius, self.ring = radius, ring
        tw, th = 256, 128
        spec = r.standard_normal((th, tw))
        fy = np.fft.fftfreq(th)[:, None] * th
        fx = np.fft.fftfreq(tw)[None, :] * tw
        f = np.sqrt(fx ** 2 + (fy * (2.4 if seed % 2 else 1)) ** 2) + 1
        tex = np.fft.ifft2(np.fft.fft2(spec) / f ** 1.75).real
        tex = (tex - tex.min()) / np.ptp(tex)
        ramp = np.array(ramp, dtype=np.float32)
        pos = np.clip(tex * (len(ramp) - 1), 0, len(ramp) - 1.001)
        i = pos.astype(int)
        t = (pos - i)[..., None]
        self.tex = (ramp[i] * (1 - t) + ramp[i + 1] * t).astype(np.float32)
        d = radius * 2 + 1
        yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1].astype(np.float32)
        nx, ny = xx / radius, yy / radius
        rr = nx ** 2 + ny ** 2
        self.disc = rr <= 1
        nz = np.sqrt(np.clip(1 - rr, 0, 1))
        self.lon = ((np.arctan2(nx, nz) / (2 * math.pi) + .5) * tw).astype(int)
        self.lat = np.clip((np.arcsin(np.clip(ny, -1, 1)) / math.pi + .5) * th, 0, th - 1).astype(int)
        light = np.clip(nx * -.55 + ny * -.5 + nz * .67, 0, 1)
        self.shade = (.12 + .98 * light ** 1.1)[..., None].astype(np.float32)
        self.rim = (np.clip(1 - nz, 0, 1) ** 3 * .75)[..., None].astype(np.float32)
        self.glow = (rr > 1) & (rr <= (1 + 3.5 / radius) ** 2)
        self.glow_a = np.clip(1 - (np.sqrt(rr) - 1) * radius / 3.5, 0, 1)[..., None].astype(np.float32)
        self.atmo = ramp[-1] * .6 + np.array([40, 60, 90], dtype=np.float32)
        self.size = d

    def draw(self, s, cx, cy, spin):
        r = self.radius
        x0, y0 = int(cx) - r, int(cy) - r
        lon = (self.lon + int(spin)) % self.tex.shape[1]
        px = self.tex[self.lat, lon] * self.shade + self.atmo * self.rim
        px = np.clip(px, 0, 255).astype(np.uint8)
        sx, sy = max(0, -x0), max(0, -y0)
        ex, ey = min(self.size, s.W - x0), min(self.size, s.H - y0)
        if sx >= ex or sy >= ey:
            return
        area = s.fb[y0 + sy:y0 + ey, x0 + sx:x0 + ex]
        g = self.glow[sy:ey, sx:ex]
        ga = self.glow_a[sy:ey, sx:ex]
        blended = (area * (1 - ga * .5) + self.atmo * ga * .5).astype(np.uint8)
        area[g] = blended[g]
        d = self.disc[sy:ey, sx:ex]
        area[d] = px[sy:ey, sx:ex][d]
        if self.ring:
            a = np.linspace(0, 2 * math.pi, 240)
            for k, rad in enumerate((1.45, 1.55, 1.68)):
                xs = cx + np.cos(a) * r * rad
                ys = cy + np.sin(a) * r * rad * .26 + np.cos(a) * r * rad * .1
                front = np.sin(a) > -.05
                ok = front & (xs >= 0) & (xs < s.W) & (ys >= 0) & (ys < s.H)
                s.fb[ys[ok].astype(int), xs[ok].astype(int)] = self.atmo.astype(np.uint8) if k != 1 else (230, 220, 200)


class Stars:
    def __init__(self, count=130, seed=3):
        r = np.random.default_rng(seed)
        self.x, self.y = r.random(count), r.random(count)
        self.z = r.choice([.25, .5, 1.], count, p=[.55, .3, .15])
        self.tw = r.random(count) * 6.28

    def update(self, dt, vx, vy):
        self.x = (self.x + vx * self.z * dt) % 1
        self.y = (self.y + vy * self.z * dt) % 1

    def draw(self, s, width=None, streak=0, t=0.):
        w = width or s.W
        b = (self.z * 235 * (.75 + .25 * np.sin(self.tw + t * 3))).astype(np.uint8)
        col = np.stack([b, b, np.minimum(255, b.astype(int) + 30).astype(np.uint8)], axis=1)
        for k in range(max(1, int(streak)) if streak else 1):
            xs = (self.x * w).astype(int)
            ys = ((self.y * s.H).astype(int) - (k * self.z * 2).astype(int)) % s.H
            s.fb[ys, xs] = (col * (1 - k / max(1, streak))).astype(np.uint8) if k else col


def sunset(s, t):
    """Cielo sintetico, sole a strisce e griglia prospettica. Tutto calcolato sul posto."""
    W, H = s.W, s.H
    hz = int(H * .58)
    y = np.linspace(0, 1, hz, dtype=np.float32)[:, None]
    top, mid, low = (np.array(c, np.float32) for c in ((10, 6, 44), (96, 16, 112), (255, 70, 130)))
    sky = np.where(y < .6, top + (mid - top) * (y / .6), mid + (low - mid) * ((y - .6) / .4))
    s.fb[:hz] = sky[:, None, :].astype(np.uint8)
    cx, cy, r = W // 2, hz - int(H * .08), int(H * .27)
    yy, xx = np.mgrid[0:hz, 0:W]
    d = (xx - cx) ** 2 + (yy - cy) ** 2
    disc = d <= r * r
    rel = (yy - (cy - r)) / (2 * r)
    gap = (rel > .42) & (((yy + int(t * 5)) % 7) < (1 + rel * 4).astype(int))
    disc &= ~gap
    c1, c2 = np.array((255, 236, 120), np.float32), np.array((255, 40, 130), np.float32)
    col = c1 + (c2 - c1) * np.clip(rel, 0, 1)[..., None]
    s.fb[:hz][disc] = col[disc].astype(np.uint8)
    gh = H - hz
    ground = np.zeros((gh, W, 3), dtype=np.float32)
    ground[:] = (16, 4, 44)
    line = np.array((255, 50, 220), np.float32)
    rows = np.arange(gh)
    # binari: ogni linea è un raggio che parte dal punto di fuga
    for k in range(-30, 31):
        xs = (cx + (k + .5) * (rows + 3) * 1.15).astype(int)
        ok = (xs >= 0) & (xs < W)
        ground[rows[ok], xs[ok]] = line
    # traversine: più fitte verso l'orizzonte, scorrono verso chi guarda
    count = 9
    for i in range(count + 1):
        z = ((i + (t * .9) % 1) / count) ** 2.3
        y = int(z * (gh - 1))
        if y < gh:
            ground[y, :] = line
    fade = np.clip(.3 + rows / gh * 1.1, 0, 1)[:, None, None].astype(np.float32)
    s.fb[hz:] = (ground * fade + np.array((16, 4, 44), np.float32) * (1 - fade)).astype(np.uint8)
    s.rect(0, hz, W, 1, (255, 170, 240))
