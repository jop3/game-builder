#!/usr/bin/env python3
"""mux_audio.py -- lägg det procedurella ljudspåret på en inspelad Othello-film.

Spelet (othello.gd) skriver vid inspelning:
  audio_events.txt   "fps <f>" + rader "frame kind idx"
  sfx_place.wav, sfx_win.wav, sfx_flip_0..3.wav   (samma PCM som spelas i spelet)

Vi bygger en tyst master lika lång som filmen och blandar in varje effekt vid
frame/fps sekunder, skriver soundtrack.wav och muxar in det med ffmpeg. Så blir
ljudet exakt synkat mot bilden, och kommer från samma synthes som interaktivt.

    python3 mux_audio.py --dir <record_dir> --video in.mp4 --out out.mp4
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import wave

import numpy as np


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return a, rate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="inspelningsmapp med wav + events")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ev_path = os.path.join(args.dir, "audio_events.txt")
    lines = open(ev_path).read().splitlines()
    fps = float(lines[0].split()[1])

    # ladda effekterna
    sfx: dict[str, np.ndarray] = {}
    rate = 22050
    place, rate = _read_wav(os.path.join(args.dir, "sfx_place.wav"))
    sfx["place"] = place
    win, _ = _read_wav(os.path.join(args.dir, "sfx_win.wav"))
    sfx["win"] = win
    flips = []
    for p in sorted(glob.glob(os.path.join(args.dir, "sfx_flip_*.wav"))):
        a, _ = _read_wav(p)
        flips.append(a)

    # masterns längd = antal renderade bildrutor / fps (+ svans)
    nframes = len(glob.glob(os.path.join(args.dir, "frame_*.png")))
    total = int(nframes / fps * rate) + rate
    master = np.zeros(total, dtype=np.float32)

    def mix(at: int, clip: np.ndarray, gain: float) -> None:
        end = min(at + len(clip), total)
        master[at:end] += clip[: end - at] * gain

    for ln in lines[1:]:
        if not ln.strip():
            continue
        f_s, kind, idx_s = ln.split()
        at = int(round(int(f_s) / fps * rate))
        if kind == "place":
            mix(at, sfx["place"], 0.9)
        elif kind == "flip":
            mix(at, flips[int(idx_s) % len(flips)], 0.7)
        elif kind == "win":
            mix(at, sfx["win"], 0.8)

    # mjuk kompression av toppar + liten headroom
    peak = float(np.max(np.abs(master))) or 1.0
    if peak > 0.98:
        master *= 0.98 / peak
    pcm = (np.clip(master, -1.0, 1.0) * 32767.0).astype(np.int16)

    track = os.path.join(args.dir, "soundtrack.wav")
    with wave.open(track, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())

    subprocess.run(
        ["ffmpeg", "-y", "-i", args.video, "-i", track,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
         "-movflags", "+faststart", args.out],
        check=True,
    )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
