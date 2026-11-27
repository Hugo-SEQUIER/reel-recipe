#!/usr/bin/env python3
"""Measure the edit of a short video (reel, TikTok, Short) so an agent can write its recipe.

Usage:
  python3 scripts/measure_reel.py <url-or-file> [--out DIR] [--model small] [--no-transcript]

Writes into DIR (default ./reel-<id>):
  video.mp4             the video
  summary.md            every measured number, in plain text (read this first)
  measurements.json     the same data as JSON
  sheets/overview-*.png one frame per second, labelled with the time
  sheets/changes-*.png  before/after pairs around every visual change, labelled with the word spoken
  raw/                  raw outputs of the measuring tools

Needs: ffmpeg + ffprobe, Pillow and numpy. Optional: yt-dlp (or uvx) for links,
faster-whisper for the transcript, Node 18+ for HyperFrames' measuring commands.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Missing Python packages: pip install -r requirements.txt (Pillow, numpy, faster-whisper)")

HF_VERSION = os.environ.get("REEL_RECIPE_HF_VERSION", "0.9.9")
GX, GY = 4, 8  # the frame is split in 4 columns x 8 rows to say where a change happens


def log(msg):
    print(f"[reel-recipe] {msg}", file=sys.stderr, flush=True)


def run(cmd, cwd=None, timeout=900):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def need(binary):
    if not shutil.which(binary):
        sys.exit(f"{binary} not found on PATH. Install ffmpeg first.")


# ---------- 1. get the video ----------

def fetch(src, out):
    video = out / "video.mp4"
    if Path(src).is_file():
        shutil.copy(src, video)
        return video, {"source": str(Path(src).resolve())}
    meta_kept = out / "raw" / "video.info.json"
    if video.exists():
        log("video already downloaded, reusing it")
        return video, read_info(src, meta_kept)
    if shutil.which("yt-dlp"):
        ytdlp = ["yt-dlp"]
    elif shutil.which("uvx"):
        ytdlp = ["uvx", "yt-dlp"]
    else:
        sys.exit("To download a link, install yt-dlp (pip install yt-dlp) or pass a local video file.")
    log("downloading the video")
    r = run(ytdlp + ["-q", "--no-warnings", "-o", str(out / "video.%(ext)s"),
                     "--merge-output-format", "mp4", "--write-info-json", src])
    if r.returncode != 0 or not video.exists():
        sys.exit("Download failed (private post, login wall or removed). Download it yourself and pass the file.\n"
                 + r.stderr[-800:])
    meta = out / "video.info.json"
    if meta.exists():
        meta.rename(meta_kept)
    return video, read_info(src, meta_kept)


def read_info(src, meta):
    if not meta.exists():
        return {"source": src}
    d = json.loads(meta.read_text())
    return {
        "source": src,
        "author": d.get("uploader") or d.get("channel"),
        "date": d.get("upload_date"),
        "views": d.get("view_count"),
        "likes": d.get("like_count"),
        "comments": d.get("comment_count"),
        "caption": (d.get("description") or "")[:500],
    }


# ---------- 2. basic facts ----------

def probe(video):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(video)])
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []):
        rot = abs(int(sd.get("rotation", 0)))
    if rot in (90, 270):
        w, h = h, w
    return {
        "width": w,
        "height": h,
        "fps": round(fps, 3),
        "duration": round(float(d["format"]["duration"]), 3),
        "has_audio": any(s["codec_type"] == "audio" for s in d["streams"]),
    }


def decode_audio(video):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


# ---------- 3. transcript ----------

def transcribe(audio, model_name):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("faster-whisper not installed: no transcript (pip install faster-whisper)")
        return None
    log(f"transcribing with whisper {model_name} (about 1 minute per minute of video on a laptop CPU)")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio, word_timestamps=True, vad_filter=False)
    words, segments = [], []
    for s in segs:
        segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        for w in s.words or []:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()})
    return {"language": info.language, "segments": segments, "words": words}


def word_at(words, t):
    if not words:
        return ""
    best = min(words, key=lambda w: 0 if w["start"] <= t <= w["end"] else min(abs(w["start"] - t), abs(w["end"] - t)))
    return best["word"] if min(abs(best["start"] - t), abs(best["end"] - t)) < 0.6 or best["start"] <= t <= best["end"] else ""


def audio_bed(audio, words):
    """Level of the sound in the gaps between words, against the speech: tells if music or sound sits under the voice."""
    if audio.size == 0 or not words:
        return None
    sr = 16000

    def db(chunk):
        if chunk.size == 0:
            return None
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        return 20 * math.log10(rms) if rms > 1e-6 else -120.0

    speech, gaps = [], []
    for w in words:
        speech.append(audio[int(w["start"] * sr):int(w["end"] * sr)])
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] >= 0.25:
            gaps.append(audio[int((a["end"] + 0.05) * sr):int((b["start"] - 0.05) * sr)])
    if words[0]["start"] > 0.4:
        gaps.append(audio[: int((words[0]["start"] - 0.1) * sr)])
    tail = audio[int((words[-1]["end"] + 0.2) * sr):]
    if tail.size > sr * 0.3:
        gaps.append(tail)
    s = db(np.concatenate(speech)) if speech else None
    g = db(np.concatenate(gaps)) if gaps else None
    if s is None or g is None:
        return None
    diff = s - g
    pause_seconds = round(sum(c.size for c in gaps) / sr, 1)
    verdict = ("music or sound under the voice" if diff < 20 else
               "quiet bed under the voice (low music or room tone)" if diff < 32 else
               "voice only, silent between words")
    return {"speech_db": round(s, 1), "gaps_db": round(g, 1), "gap_below_speech_db": round(diff, 1), "verdict": verdict,
            "pause_seconds": pause_seconds}


# ---------- 4. HyperFrames measuring commands (free, local, no sign-in) ----------

def hf_cmd():
    if os.environ.get("HYPERFRAMES_BIN"):
        return [os.environ["HYPERFRAMES_BIN"]]
    node = shutil.which("node")
    if not node:
        log("Node not found: skipping HyperFrames (install Node 22+); cuts come from ffmpeg, no face box or sound reading")
        return None
    v = run([node, "-v"]).stdout.strip().lstrip("v")
    if not v or int(v.split(".")[0]) < 22:
        log(f"Node {v or '?'} is too old for HyperFrames (needs 22+): skipping it; cuts come from ffmpeg, "
            "no face box or sound reading")
        return None
    if shutil.which("hyperframes"):
        return ["hyperframes"]
    if shutil.which("npx"):
        return ["npx", "-y", f"hyperframes@{HF_VERSION}"]
    return None


def hf_json(base, args, cwd):
    r = run(base + args, cwd=cwd)
    text = r.stdout.strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError((r.stderr or r.stdout)[-400:])
    return json.loads(text[start:])


def hyperframes(video, out):
    base = hf_cmd()
    if not base:
        return {}
    res = {}
    for key, args in {
        "cuts": ["measure", "cuts", video.name],
        "events": ["measure", "events", video.name, "--map"],
        "face": ["measure", "face", video.name, "--samples", "12"],
        "audio": ["audio", video.name, "--json"],
    }.items():
        saved = out / "raw" / f"hf-{key}.json"
        if saved.exists():
            res[key] = json.loads(saved.read_text())
            continue
        log(f"hyperframes {' '.join(args[:2])}")
        try:
            res[key] = hf_json(base, args, cwd=out)
            saved.write_text(json.dumps(res[key], indent=1))
        except Exception as e:  # one failed tool must not stop the others
            log(f"hyperframes {key} failed: {e}")
    return res


def ffmpeg_cuts(video):
    r = run(["ffmpeg", "-v", "info", "-i", str(video), "-vf", "select='gt(scene,0.25)',showinfo", "-an", "-f", "null", "-"])
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


# ---------- 5. frames ----------

def frame_path(out, n, width):
    return out / "frames" / f"n{n:05d}w{width}.png"


def grab_n(video, n, fps, out, width=360):
    """Frame number n, exactly: input seeking keeps the first frame at or after the seek time,
    so seek a quarter frame before it. Cached by frame number and width."""
    path = frame_path(out, n, width)
    if not path.exists():
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max((n - 0.25) / fps, 0):.4f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:-2", str(path)])
    return path if path.exists() else None


def grab_many(video, items, fps, out):
    """items: list of (frame number, width). Runs the ffmpeg seeks in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        return list(ex.map(lambda it: grab_n(video, it[0], fps, out, it[1]), items))


def font(size):
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grid_frame(src, path):
    """A frame with lines every 10% so positions can be read as shares of the frame."""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    f = font(18)
    for i in range(1, 10):
        x, y = w * i // 10, h * i // 10
        d.line([(x, 0), (x, h)], fill=(255, 0, 80), width=1)
        d.line([(0, y), (w, y)], fill=(255, 0, 80), width=1)
        d.text((x + 3, 3), f"{i * 10}", fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(255, 0, 80))
        d.text((3, y + 3), f"{i * 10}", fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(255, 0, 80))
    im.save(path)
    return path


def sheet(images, labels, path, cols=6):
    imgs = [Image.open(p).convert("RGB") for p in images]
    w, h = imgs[0].size
    band = 34
    rows = math.ceil(len(imgs) / cols)
    canvas = Image.new("RGB", (cols * w, rows * (h + band)), "white")
    draw = ImageDraw.Draw(canvas)
    f = font(20)
    for i, (im, lab) in enumerate(zip(imgs, labels)):
        x, y = (i % cols) * w, (i // cols) * (h + band)
        canvas.paste(im.resize((w, h)), (x, y + band))
        draw.text((x + 6, y + 6), lab[:32], fill="black", font=f)
    canvas.save(path)


# ---------- 6. where things change ----------
# The video is decoded once, small and grey, at SCAN_FPS. Each frame is cut in GX x GY cells.

SCAN_FPS, CELL = 10, 18
LAG = 4  # frames at SCAN_FPS between the two pictures compared (0.4 s): long enough to catch fades and morphs


def decode_small(video):
    w, h = GX * CELL, GY * CELL
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                        f"fps={SCAN_FPS},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    return a[: a.size // (w * h) * (w * h)].reshape(-1, h, w).astype(np.float32)


def cell_means(d):
    """(N, H, W) absolute differences -> (N, GY, GX) mean per cell."""
    return d.reshape(d.shape[0], GY, CELL, GX, CELL).mean(axis=(2, 4))


def largest_block(cells):
    """Keep the largest 4-connected group of cells (drops lone cells such as a moving hand)."""
    cells, best = set(cells), set()
    while cells:
        stack, group = [cells.pop()], set()
        while stack:
            r, c = stack.pop()
            group.add((r, c))
            for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if nb in cells:
                    cells.remove(nb)
                    stack.append(nb)
        if len(group) > len(best):
            best = group
    return best


def span(cells):
    rows = sorted({r for r, _ in cells})
    cols = sorted({c for _, c in cells})
    return (f"y {rows[0] * 100 // GY}-{(rows[-1] + 1) * 100 // GY}%, "
            f"x {cols[0] * 100 // GX}-{(cols[-1] + 1) * 100 // GX}%")


def scan_changes(small, ignore):
    """Times where a region changes far above its usual motion, including fades and morphs that a
    hard-cut detector misses. Returns the per-cell threshold too, for classifying the changes."""
    if small.shape[0] <= LAG + 2:
        return [], None
    D = cell_means(np.abs(small[LAG:] - small[:-LAG]))      # change over 0.4 s, index i = frames i..i+LAG
    D1 = cell_means(np.abs(small[1:] - small[:-1]))          # change frame to frame
    thr = np.maximum(10, np.median(D, axis=0) * 2.5 + 6)
    active = D > thr
    mask = np.ones((GY, GX), bool)
    for r, c in ignore:
        mask[r, c] = False
    score = (active & mask).sum(axis=(1, 2))
    events, i = [], 0
    while i < len(score):
        if score[i] >= 2:
            j = i
            while j + 1 < len(score) and score[j + 1] >= 2:
                j += 1
            cells = active[i:j + 1].any(axis=0) & mask
            # the frame where those cells move the most is the change itself
            lo, hi = i, min(j + LAG, len(D1) - 1)
            strength = D1[lo:hi + 1][:, cells].mean(axis=1)
            k = lo + int(np.argmax(strength))
            peak = strength.max()
            moving = int((strength > max(3.0, peak * 0.35)).sum())
            events.append({"t": round((k + 1) / SCAN_FPS, 3), "scan_frames": moving})
            i = j + 1
        else:
            i += 1
    return events, thr


def classify(small, t, thr, face_cells, ignore):
    k = int(round(t * SCAN_FPS))
    a, b = max(k - LAG // 2, 0), min(k + LAG // 2, small.shape[0] - 1)
    d = cell_means(np.abs(small[b:b + 1] - small[a:a + 1]))[0]
    changed = {(r, c) for r in range(GY) for c in range(GX) if d[r, c] > thr[r, c]}
    if not changed:
        return "small change", ""
    share = len(changed) / (GX * GY)
    if share >= 0.75:
        return "whole-frame change (camera cut or full-screen graphic)", "y 0-100%, x 0-100%"
    if face_cells and face_cells <= changed and share >= 0.4:
        return "camera cut, punch-in or move", span(changed)
    block = largest_block(changed - set(ignore)) or largest_block(changed)
    return "overlay change (text, card, picture)", span(block)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="video link (Instagram, TikTok, YouTube...) or local file")
    ap.add_argument("--out", help="output folder (default ./reel-<id>)")
    ap.add_argument("--model", default="small", help="whisper model: tiny, base, small (default), medium, small.en...")
    ap.add_argument("--no-transcript", action="store_true")
    ap.add_argument("--no-hyperframes", action="store_true")
    a = ap.parse_args()

    need("ffmpeg")
    need("ffprobe")
    slug = re.sub(r"[^A-Za-z0-9_-]", "", a.src.rstrip("/").split("/")[-1])[:24] or \
        hashlib.md5(a.src.encode()).hexdigest()[:8]
    out = Path(a.out or f"reel-{slug}").resolve()
    for d in ["raw", "sheets", "frames"]:
        (out / d).mkdir(parents=True, exist_ok=True)

    video, info = fetch(a.src, out)
    p = probe(video)
    log(f"{p['width']}x{p['height']}, {p['fps']} fps, {p['duration']} s")

    audio = decode_audio(video) if p["has_audio"] else np.zeros(0, np.float32)
    cached = out / "raw" / "transcript.json"
    if cached.exists() and not a.no_transcript:
        log("reusing raw/transcript.json")
        tr = json.loads(cached.read_text())
    else:
        tr = None if (a.no_transcript or audio.size == 0) else transcribe(audio, a.model)
    words = tr["words"] if tr else []
    if tr:
        cached.write_text(json.dumps(tr, indent=1))

    hf = {} if a.no_hyperframes else hyperframes(video, out)

    fps = p["fps"] or 30
    n_frames = int(p["duration"] * fps)

    def frame_of(t):
        return max(0, min(n_frames - 1, int(round(t * fps))))

    # face position (where the person sits in the frame) and the body under it
    face = hf.get("face") or {}
    face_box, face_cells, body_cells, face_steady = None, set(), set(), None
    boxes = [b["box"] for b in face.get("boxes", []) if b.get("box")]
    if boxes:
        face_box = [round(statistics.median(b[i] for b in boxes), 3) for i in range(4)]
        x, y, w, h = face_box
        cx = [b[0] + b[2] / 2 for b in boxes]
        cy = [b[1] + b[3] / 2 for b in boxes]
        hs = [b[3] for b in boxes]
        # the detector's box jitters by a few %, so judge moves against the face's own size
        face_steady = (max(cx) - min(cx) < 0.35 * w and max(cy) - min(cy) < 0.35 * h and max(hs) / min(hs) < 1.35)
        fcx = x + w / 2
        for r in range(GY):
            for c in range(GX):
                x0, y0 = c / GX, r / GY
                if x0 < x + w and x0 + 1 / GX > x and y0 < y + h and y0 + 1 / GY > y:
                    face_cells.add((r, c))
                elif y0 + 1 / GY > y + h and x0 < fcx + 1.25 * w and x0 + 1 / GX > fcx - 1.25 * w:
                    body_cells.add((r, c))

    # usual motion per region, from the whole video decoded small
    log("scanning every region for changes")
    small = decode_small(video)
    if small.shape[0] > LAG:
        usual = np.median(cell_means(np.abs(small[LAG:] - small[:-LAG])), axis=0)
    else:
        usual = np.zeros((GY, GX))
    always = [(r, c) for r in range(GY) for c in range(GX)
              if usual[r, c] >= 6 and (r, c) not in face_cells | body_cells]
    ignore = face_cells | body_cells | set(always)
    scanned, thr = scan_changes(small, ignore)

    # hard cuts from HyperFrames (frame-exact) + changes only the region scan sees (fades, morphs, slides)
    if hf.get("cuts", {}).get("cuts") is not None:
        hard = [(c["t"], c.get("frame", frame_of(c["t"]))) for c in hf["cuts"]["cuts"]]
        flashes = [n["t"] for n in hf["cuts"].get("not_cuts", [])]
        change_source = f"hyperframes measure cuts {HF_VERSION} + region scan"
    else:
        hard = [(t, frame_of(t)) for t in ffmpeg_cuts(video)]
        flashes, change_source = [], "ffmpeg scene score + region scan"
    found = [{"t": round(t, 3), "frame": n, "transition": "hard cut"} for t, n in hard]
    for e in scanned:
        if all(abs(e["t"] - c["t"]) > 0.35 for c in found):
            speed = (f"gradual, about {e['scan_frames'] / SCAN_FPS:.1f} s (fade, morph or slide)"
                     if e["scan_frames"] >= 3 else "fast, under 0.2 s")
            found.append({"t": e["t"], "frame": frame_of(e["t"]), "transition": speed})
    found.sort(key=lambda c: c["t"])

    changes, prev = [], 0.0
    for c in found:
        kind, where = classify(small, c["t"], thr, face_cells, ignore) if thr is not None else ("unknown", "")
        if kind == "small change" and c["transition"] != "hard cut":
            continue
        changes.append({**c, "since_previous": round(c["t"] - prev, 2), "kind": kind, "where": where,
                        "word": word_at(words, c["t"])})
        prev = c["t"]

    # every picture we need, extracted in parallel
    log("extracting frames")
    pair_jobs, ent_jobs, grid_jobs, ov_jobs = [], [], [], []
    for c in changes:
        gap = 2 if c["transition"] == "hard cut" else int(0.3 * fps)
        pair_jobs.append(((max(c["frame"] - gap, 0), 360), (min(c["frame"] + gap, n_frames - 1), 360)))
    seen, ent_pick = set(), []
    for c in changes:
        key = (c["kind"], c["transition"].split(",")[0], c["where"][:4])
        if key not in seen and len(ent_pick) < 4:
            seen.add(key)
            ent_pick.append(c)
    offsets = (-1, 0, 1, 2, 3, 5, 8, 12)
    for c in ent_pick:
        ent_jobs += [(min(max(c["frame"] + k, 0), n_frames - 1), 240) for k in offsets]
    marks = [min(1.0, p["duration"] / 4), p["duration"] / 2, max(0, p["duration"] - 1.5)]
    if changes:
        marks[1] = changes[len(changes) // 2]["t"] + 0.4
    grid_jobs = [(frame_of(t), 540) for t in marks]
    ov_times = [min(s + 0.5, p["duration"] - 0.05) for s in range(int(p["duration"]) + 1)]
    ov_jobs = [(frame_of(t), 270) for t in ov_times]
    grab_many(video, [j for pair in pair_jobs for j in pair] + ent_jobs + grid_jobs + ov_jobs, fps, out)

    def fp(job):
        path = frame_path(out, *job)
        return path if path.exists() else None

    pair_imgs, pair_labels = [], []
    for c, (a_job, b_job) in zip(changes, pair_jobs):
        if fp(a_job) and fp(b_job):
            pair_imgs += [fp(a_job), fp(b_job)]
            pair_labels += [f"{c['t']:.2f}s before", f"after: {c['word']}"]
    for n in range(0, len(pair_imgs), 12):
        sheet(pair_imgs[n:n + 12], pair_labels[n:n + 12], out / "sheets" / f"changes-{n // 12 + 1}.png")

    ent_imgs, ent_labels = [], []
    for i, c in enumerate(ent_pick):
        for k, job in zip(offsets, ent_jobs[i * len(offsets):(i + 1) * len(offsets)]):
            if fp(job):
                ent_imgs.append(fp(job))
                ent_labels.append(f"{c['t']:.2f}s {k:+d}f")
    if ent_imgs:
        sheet(ent_imgs, ent_labels, out / "sheets" / "entrances.png", cols=8)

    grids = [grid_frame(fp(j), out / "frames" / f"grid{j[0]:05d}.png") for j in grid_jobs if fp(j)]
    if grids:
        sheet(grids, [f"{t:.1f}s (lines every 10%)" for t in marks], out / "sheets" / "layout-grid.png", cols=3)

    ov = [(fp(j), f"{t:.1f}s {word_at(words, t)}") for j, t in zip(ov_jobs, ov_times) if fp(j)]
    for n in range(0, len(ov), 24):
        part = ov[n:n + 24]
        sheet([x for x, _ in part], [y for _, y in part], out / "sheets" / f"overview-{n // 24 + 1}.png")

    # sound
    hfa = (hf.get("audio") or {}).get("items", [{}])[0] if hf.get("audio") else {}
    onsets = [o["t"] for o in (hfa.get("onsets") or {}).get("events", []) if o.get("strength", 0) >= 0.3]
    in_gaps = [t for t in onsets if words and not any(w["start"] - 0.08 <= t <= w["end"] + 0.08 for w in words)]
    sound = {
        "kind": (hfa.get("kind") or {}).get("value"),
        "kind_confidence": (hfa.get("kind") or {}).get("confidence"),
        "loudness_lufs": (hfa.get("loudness") or {}).get("lufsIntegrated"),
        "bed": audio_bed(audio, words),
        "hits_between_words": in_gaps,
    }

    speech_secs = (words[-1]["end"] - words[0]["start"]) if words else 0
    m = {
        "info": info,
        "video": p,
        "change_source": change_source,
        "changes": changes,
        "flashes": flashes,
        "face": {"box_median": face_box, "height_share": face.get("face_height"), "steady": face_steady,
                 "samples": len(face.get("boxes", [])), "found_in": len(boxes)},
        "always_moving": span(always) if always else "",
        "speech": {
            "words": len(words),
            "words_per_second": round(len(words) / speech_secs, 2) if speech_secs else None,
            "first_word_at": words[0]["start"] if words else None,
            "hook_first_3s": " ".join(w["word"] for w in words if w["start"] < (words[0]["start"] + 3)) if words else "",
            "language": tr["language"] if tr else None,
        },
        "sound": sound,
    }
    (out / "measurements.json").write_text(json.dumps(m, indent=1))
    (out / "summary.md").write_text(render_summary(m, tr))
    print(out / "summary.md")


def pct(v):
    return f"{v * 100:.0f}%"


def render_summary(m, tr):
    p, L = m["video"], []
    L.append("# Measured edit\n")
    i = m["info"]
    if i:
        L.append("## Source")
        for k in ["source", "author", "date", "views", "likes", "comments"]:
            if i.get(k) is not None:
                L.append(f"- {k}: {i[k]}")
            elif k == "views" and i.get("likes") is not None:
                L.append("- views: not given by the platform (read them on the post)")
        if i.get("caption"):
            L.append(f"- caption: {i['caption'].splitlines()[0][:200]}")
        L.append("")
    L.append("## Video")
    L.append(f"- {p['width']}x{p['height']}, {p['fps']} fps, {p['duration']} s")
    f = m["face"]
    if f["box_median"]:
        x, y, w, h = f["box_median"]
        L.append(f"- face: x {pct(x)}-{pct(x + w)}, y {pct(y)}-{pct(y + h)} of the frame, face height {pct(f['height_share'] or h)} of the frame "
                 f"(found in {f['found_in']} of {f['samples']} sampled frames; the detector box can sit a few % off, confirm on the grid); "
                 + ("same framing all video (no punch-ins between samples)" if f["steady"] else "framing moves: check for punch-ins and zooms"))
    else:
        L.append("- face: none found (no person, or HyperFrames not run)")
    if m["always_moving"]:
        L.append(f"- region that moves all the time away from the speaker (gameplay, b-roll, screen recording, or the speaker's hands): {m['always_moving']}")
    s = m["speech"]
    if s["words"]:
        L.append(f"- speech: {s['words']} words, {s['words_per_second']} words/s, first word at {s['first_word_at']} s")
        L.append(f"- first 3 s spoken: \"{s['hook_first_3s']}\"")
    L.append("")
    L.append(f"## Visual changes ({len(m['changes'])}, from {m['change_source']})")
    L.append("- hard cuts are frame-exact; the region scan adds fades, morphs and slides (timed to about 0.1 s)")
    kinds = {}
    for c in m["changes"]:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    for k, n in kinds.items():
        L.append(f"- {k}: {n}")
    if any(k.startswith("whole-frame") for k in kinds):
        L.append("  (a whole-frame change is a camera cut only if the speaker is on both sides: check the changes sheet)")
    gaps = [c["since_previous"] for c in m["changes"][1:]]
    if gaps:
        L.append(f"- time between changes: median {statistics.median(gaps):.2f} s, shortest {min(gaps):.2f} s, longest {max(gaps):.2f} s")
    if m["changes"]:
        L.append(f"- first change at {m['changes'][0]['t']} s, last at {m['changes'][-1]['t']} s")
    if m["flashes"]:
        L.append(f"- flashes (one-frame light changes, not cuts): {', '.join(str(t) for t in m['flashes'])}")
    L.append("")
    L.append("| t (s) | since previous | kind | transition | where | word spoken |")
    L.append("|---|---|---|---|---|---|")
    for c in m["changes"]:
        L.append(f"| {c['t']} | {c['since_previous']} | {c['kind']} | {c['transition']} | {c['where']} | {c['word']} |")
    L.append("")
    so = m["sound"]
    L.append("## Sound")
    if so["kind"]:
        L.append(f"- HyperFrames reads the track as: {so['kind']} (confidence {so['kind_confidence']})")
    if so["loudness_lufs"] is not None:
        L.append(f"- loudness: {so['loudness_lufs']} LUFS integrated")
    if so["bed"]:
        b = so["bed"]
        L.append(f"- between words: {b['gaps_db']} dB vs speech {b['speech_db']} dB ({b['gap_below_speech_db']} dB lower): {b['verdict']}"
                 f" (from {b['pause_seconds']} s of pauses{'; low confidence, listen to it' if b['pause_seconds'] < 3 else ''})")
    if so["hits_between_words"]:
        L.append(f"- sound hits between words (possible SFX, whoosh or beat): {', '.join(str(t) for t in so['hits_between_words'][:30])}")
    elif so["kind"]:
        L.append("- no clear sound hits between words (no SFX layer found)")
    L.append("")
    if tr:
        L.append("## Transcript")
        for seg in tr["segments"]:
            L.append(f"- [{seg['start']:.1f}] {seg['text']}")
        L.append("")
    L.append("## Pictures")
    L.append("- sheets/layout-grid.png: hook, middle and end frames with a line every 10%: read positions here")
    L.append("- sheets/entrances.png: consecutive frames around the first change of each place: how things enter")
    L.append("- sheets/overview-*.png: one frame per second, labelled with time and word")
    L.append("- sheets/changes-*.png: before/after pairs around each change above")
    L.append("- frames/: every single frame used")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
