"""Regenerate the tab-attention notification sounds in data/sounds/.

Purely procedural: no third-party audio is involved, so the shipped sounds carry
no licence obligations and their provenance is this file. Everything is additive
synthesis with an exponential decay envelope, a few milliseconds of attack to
avoid a click, and a short fade-out.

Writes .wav; encode to .oga with:
    for f in src/ashyterm/data/sounds/*.wav; do
        ffmpeg -y -i "$f" -c:a libvorbis -qscale:a 3 -ar 44100 -ac 1 "${f%.wav}.oga"
        rm "$f"
    done
"""

import math
import struct
import wave
from pathlib import Path

SR = 44100
PEAK = 0.72  # headroom so nothing clips after encoding
OUT = Path(__file__).resolve().parent.parent / "src" / "ashyterm" / "data" / "sounds"


def tone(freq, dur, harmonics=(1.0,), decay=7.0, attack=0.004, detune=0.0):
    """One decaying partial stack. ``detune`` in Hz adds a slow beat."""
    n = int(SR * dur)
    buf = [0.0] * n
    for i in range(n):
        t = i / SR
        env = math.exp(-decay * t)
        if t < attack:
            env *= t / attack
        value = 0.0
        for k, amp in enumerate(harmonics, start=1):
            value += amp * math.sin(2 * math.pi * freq * k * t)
            if detune:
                value += amp * 0.5 * math.sin(2 * math.pi * (freq * k + detune) * t)
        buf[i] = env * value
    return buf


def mix(*layers):
    length = max(len(layer) for layer in layers)
    out = [0.0] * length
    for layer in layers:
        for i, value in enumerate(layer):
            out[i] += value
    return out


def at(buf, offset_s, length_s=None):
    """Shift ``buf`` to start at ``offset_s``, padding the front with silence."""
    pad = [0.0] * int(SR * offset_s)
    out = pad + buf
    if length_s:
        target = int(SR * length_s)
        out = out[:target] + [0.0] * max(0, target - len(out))
    return out


def finish(buf, fade=0.008):
    """Normalize to PEAK and fade the tail so it never ends on a click."""
    peak = max(abs(v) for v in buf) or 1.0
    scale = PEAK / peak
    n_fade = int(SR * fade)
    out = []
    total = len(buf)
    for i, value in enumerate(buf):
        v = value * scale
        tail = total - i
        if tail < n_fade:
            v *= tail / n_fade
        out.append(v)
    return out


def write(name, buf):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.wav"
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(
            b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in buf)
        )
    return path


# Equal-tempered notes used below.
C6, D6, E6, G6, A6, C7 = 1046.5, 1174.7, 1318.5, 1568.0, 1760.0, 2093.0
A5, E5, G5 = 880.0, 659.3, 784.0

SOUNDS = {
    # 1. Bare, quick blip — the most discreet option.
    "blip": lambda: tone(A5, 0.14, (1.0, 0.12), decay=26.0),
    # 2. Struck bell with inharmonic shimmer.
    "bell": lambda: tone(E6, 0.9, (1.0, 0.5, 0.22, 0.08), decay=6.5, detune=1.4),
    # 3. Two rising notes: the classic "done".
    "rise": lambda: mix(
        at(tone(C6, 0.3, (1.0, 0.2), decay=13.0), 0.0, 0.5),
        at(tone(E6, 0.34, (1.0, 0.2), decay=11.0), 0.1, 0.5),
    ),
    # 4. Major triad arpeggio — clearly celebratory.
    "success": lambda: mix(
        at(tone(C6, 0.34, (1.0, 0.25), decay=12.0), 0.00, 0.62),
        at(tone(E6, 0.34, (1.0, 0.25), decay=12.0), 0.09, 0.62),
        at(tone(G6, 0.40, (1.0, 0.25), decay=10.0), 0.18, 0.62),
    ),
    # 5. Plucked, marimba-like body.
    "pluck": lambda: tone(G5, 0.42, (1.0, 0.62, 0.18), decay=15.0, attack=0.002),
    # 6. Percussive pop, almost no pitch.
    "pop": lambda: tone(520.0, 0.1, (1.0, 0.9, 0.55), decay=40.0, attack=0.001),
    # 7. Two notes a fifth apart, struck together.
    "chime": lambda: mix(
        tone(C6, 0.85, (1.0, 0.3, 0.1), decay=7.0),
        tone(G6, 0.85, (0.55, 0.2), decay=7.5, detune=0.9),
    ),
    # 8. Falling pair — neutral "finished", less cheerful.
    "settle": lambda: mix(
        at(tone(E6, 0.3, (1.0, 0.2), decay=13.0), 0.0, 0.5),
        at(tone(C6, 0.36, (1.0, 0.22), decay=10.0), 0.1, 0.5),
    ),
    # 9. Double beep — reads as "attention" more than "done".
    "double": lambda: mix(
        at(tone(A6, 0.1, (1.0, 0.15), decay=30.0), 0.00, 0.34),
        at(tone(A6, 0.1, (1.0, 0.15), decay=30.0), 0.16, 0.34),
    ),
    # 10. Soft low bell — the quietest, for a busy tab bar.
    "soft": lambda: tone(E5, 1.0, (1.0, 0.35, 0.12), decay=5.0, attack=0.012),
}

if __name__ == "__main__":
    for name, build in SOUNDS.items():
        path = write(name, finish(build()))
        size = path.stat().st_size
        print(f"{name:9} {size/1024:6.1f} KB  {path}")
