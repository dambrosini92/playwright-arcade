#!/usr/bin/env python3
"""Mixer audio in tempo reale. Gira in un processo a parte.

Su macOS esce da CoreAudio, altrove da sounddevice (ARCADE_AUDIO=sounddevice per forzarlo).

Comandi su stdin, uno per riga:
  music <nome> | music -   cambia brano con dissolvenza (o silenzio)
  sfx <nome> [volume]      effetto immediato
  sting <nome> [volume]    effetto importante: abbassa la musica mentre suona
  mute 0|1
Si chiude da solo quando stdin si chiude, cioè quando il gioco termina.
"""
import ctypes as C
import os
import signal
import sys
import threading
import wave
from pathlib import Path

import numpy as np

RATE = 44100
FRAMES = 512  # 3 buffer da 512 frame: circa 35 ms di latenza
ASSETS = Path(__file__).resolve().parent / 'assets'
MUSIC_VOLUME = .85


class ASBD(C.Structure):
    _fields_ = [('rate', C.c_double), ('fmt', C.c_uint32), ('flags', C.c_uint32), ('bpp', C.c_uint32),
                ('fpp', C.c_uint32), ('bpf', C.c_uint32), ('ch', C.c_uint32), ('bits', C.c_uint32),
                ('res', C.c_uint32)]


class ABuf(C.Structure):
    _fields_ = [('cap', C.c_uint32), ('data', C.c_void_p), ('size', C.c_uint32), ('ud', C.c_void_p),
                ('pdc', C.c_uint32), ('pd', C.c_void_p), ('npd', C.c_uint32)]


CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.POINTER(ABuf))
cache = {}


def load(name):
    if name not in cache:
        with wave.open(str(ASSETS / (name + '.wav'))) as w:
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2')
        cache[name] = (raw.astype(np.float32) / 32768).reshape(-1, 2)
    return cache[name]


class Mixer:
    def __init__(self):
        self.lock = threading.Lock()
        self.track = None      # [dati, posizione, guadagno, guadagno obiettivo]
        self.fading = []       # brani in uscita
        self.voices = []       # [dati, posizione, volume]
        self.stings = []       # come voices, ma abbassano la musica
        self.duck = 1.
        self.muted = False
        self.out = np.zeros((FRAMES, 2), dtype=np.float32)

    @staticmethod
    def loop(entry, out):
        data, pos, gain, target = entry
        n = len(out)
        idx = (pos + np.arange(n)) % len(data)
        ramp = np.linspace(gain, gain + np.clip(target - gain, -.012, .012), n, dtype=np.float32)
        out += data[idx] * ramp[:, None]
        entry[1] = (pos + n) % len(data)
        entry[2] = float(ramp[-1])

    @staticmethod
    def play(voices, out):
        alive = []
        for v in voices:
            data, pos, vol = v
            n = min(len(out), len(data) - pos)
            out[:n] += data[pos:pos + n] * vol
            v[1] = pos + n
            if v[1] < len(data):
                alive.append(v)
        return alive

    def mix(self):
        music = np.zeros((FRAMES, 2), dtype=np.float32)
        out = self.out
        out[:] = 0
        with self.lock:
            if self.track:
                self.loop(self.track, music)
            for f in self.fading:
                self.loop(f, music)
            self.fading = [f for f in self.fading if f[2] > .002]
            self.voices = self.play(self.voices, out)
            self.stings = self.play(self.stings, out)
            goal = .22 if self.stings else 1.
            step = .02 if self.stings else .004
            start = self.duck
            self.duck += float(np.clip(goal - self.duck, -step, step))
        out += music * np.linspace(start, self.duck, FRAMES, dtype=np.float32)[:, None] * MUSIC_VOLUME
        np.tanh(out, out=out)  # limitatore morbido: più suoni insieme non gracchiano
        if self.muted:
            out[:] = 0
        return out

    def command(self, line):
        parts = line.split()
        if not parts:
            return
        op = parts[0]
        if op == 'mute':
            self.muted = parts[1] == '1'
        elif op == 'music':
            data = None if parts[1] == '-' else load(parts[1])
            with self.lock:
                if self.track and data is not None and self.track[0] is data:
                    return
                if self.track:
                    self.track[3] = 0.
                    self.fading.append(self.track)
                self.track = None if data is None else [data, 0, 0., 1.]
        elif op in ('sfx', 'sting'):
            data = load(parts[1])
            volume = float(parts[2]) if len(parts) > 2 else 1.
            with self.lock:
                bank = self.stings if op == 'sting' else self.voices
                if op == 'sfx' and len(bank) > 10:
                    bank.pop(0)
                bank.append([data, 0, volume])


def read_commands(mixer):
    for line in sys.stdin:
        try:
            mixer.command(line)
        except (OSError, ValueError, IndexError, EOFError, wave.Error):
            pass


def portable():
    import sounddevice as sd
    mixer = Mixer()

    def fill(out, _frames, _time, _status):
        out[:] = mixer.mix()

    with sd.OutputStream(samplerate=RATE, blocksize=FRAMES, channels=2, dtype='float32', latency='low', callback=fill):
        read_commands(mixer)
    return 0


def coreaudio():
    at = C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox')
    at.AudioQueueNewOutput.argtypes = [C.POINTER(ASBD), CALLBACK, C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint32,
                                       C.POINTER(C.c_void_p)]
    at.AudioQueueAllocateBuffer.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.POINTER(ABuf))]
    at.AudioQueueEnqueueBuffer.argtypes = [C.c_void_p, C.POINTER(ABuf), C.c_uint32, C.c_void_p]
    at.AudioQueueStart.argtypes = [C.c_void_p, C.c_void_p]
    at.AudioQueueDispose.argtypes = [C.c_void_p, C.c_bool]
    mixer = Mixer()

    def fill(_user, queue, buf):
        try:
            data = mixer.mix()
            C.memmove(buf.contents.data, data.ctypes.data, data.nbytes)
            buf.contents.size = data.nbytes
            at.AudioQueueEnqueueBuffer(queue, buf, 0, None)
        except Exception:  # un errore nel callback non deve mai bloccare il gioco
            pass

    callback = CALLBACK(fill)
    fmt = ASBD(float(RATE), 0x6c70636d, 1 | 8, 8, 1, 8, 2, 32, 0)  # lpcm float32 stereo interleaved
    queue = C.c_void_p()
    if at.AudioQueueNewOutput(C.byref(fmt), callback, None, None, None, 0, C.byref(queue)):
        return 1
    for _ in range(3):
        buf = C.POINTER(ABuf)()
        if at.AudioQueueAllocateBuffer(queue, FRAMES * 8, C.byref(buf)):
            return 1
        fill(None, queue.value, buf)
    if at.AudioQueueStart(queue, None):
        return 1
    read_commands(mixer)
    at.AudioQueueDispose(queue, True)
    return 0


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    backend = os.environ.get('ARCADE_AUDIO') or ('coreaudio' if sys.platform == 'darwin' else 'sounddevice')
    return coreaudio() if backend == 'coreaudio' else portable()


if __name__ == '__main__':
    sys.exit(main())
