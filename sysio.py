#!/usr/bin/env python3
"""Tutto ciò che cambia tra macOS, Linux e Windows: tastiera, console e processi."""
import os
import signal
import subprocess
import time
from pathlib import Path

WINDOWS = os.name == 'nt'

if WINDOWS:
    import ctypes
    import msvcrt
    # Il test sta in un gruppo suo: Ctrl+C sulla console non lo tocca, lo ferma stop_group
    SPAWN = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    # Tasti speciali di msvcrt, tradotti nelle stesse sequenze dei terminali POSIX
    SPECIAL = {b'H': b'\x1b[A', b'P': b'\x1b[B', b'M': b'\x1b[C', b'K': b'\x1b[D', b'I': b'\x1b[5~', b'Q': b'\x1b[6~'}
else:
    import select
    import termios
    import tty
    SPAWN = {'start_new_session': True}


def node_bin(folder, name):
    return Path(folder) / 'node_modules/.bin' / (name + '.cmd' if WINDOWS else name)


def setup_terminal():
    """Prepara la console al gioco e restituisce ciò che serve a restore_terminal."""
    if WINDOWS:
        return setup_console()
    saved = termios.tcgetattr(0)
    tty.setcbreak(0)
    attrs = termios.tcgetattr(0)
    attrs[6][termios.VSUSP] = b'\0'  # niente Ctrl+Z: lascerebbe il terminale nello schermo alternativo
    termios.tcsetattr(0, termios.TCSANOW, attrs)
    return saved


def restore_terminal(saved):
    if WINDOWS:
        kernel, handle, mode, codepage = saved
        kernel.SetConsoleMode(handle, mode)
        kernel.SetConsoleOutputCP(codepage)
    else:
        termios.tcsetattr(0, termios.TCSADRAIN, saved)


def setup_console():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        raise SystemExit('Avvia Playwright Arcade da una console, meglio Windows Terminal.')
    # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING: colori e cursore con le sequenze ANSI
    if not kernel.SetConsoleMode(handle, mode.value | 0x0001 | 0x0004):
        raise SystemExit('Questa console non gestisce le sequenze ANSI: serve Windows 10 o Windows Terminal.')
    codepage = kernel.GetConsoleOutputCP()
    kernel.SetConsoleOutputCP(65001)
    msvcrt.setmode(1, os.O_BINARY)  # senza, ogni \n diventerebbe \r\n dentro i fotogrammi
    return kernel, handle, mode.value, codepage


def read_input(wait):
    """I byte premuti, aspettando al massimo wait secondi. Vuoto se non è arrivato nulla."""
    if not WINDOWS:
        ready, _, _ = select.select([0], [], [], max(0., wait))
        return os.read(0, 256) if ready else b''
    end = time.monotonic() + wait
    while not msvcrt.kbhit():
        left = end - time.monotonic()
        if left <= 0:
            return b''
        time.sleep(min(.004, left))
    data = b''
    while msvcrt.kbhit():
        key = msvcrt.getch()
        if key == b'\x03':
            raise KeyboardInterrupt
        data += SPECIAL.get(msvcrt.getch(), b'') if key in (b'\x00', b'\xe0') else key
    return data


def stop_group(proc):
    if not proc:
        return
    if WINDOWS:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                time.sleep(.6)
            except (OSError, ValueError):
                pass
        if proc.poll() is None:  # /T prende anche browser e server avviati dal test
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
    else:
        # Signal the group even when its original parent has already exited.
        for sig, delay in ((signal.SIGINT, 0.6), (signal.SIGTERM, 0.6), (signal.SIGKILL, 0)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            if delay:
                time.sleep(delay)
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
