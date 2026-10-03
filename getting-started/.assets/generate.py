#!/usr/bin/env python3
"""Render the Getting Started recipe video from its source assets.

Everything the video is made of lives next to this file:

    scenes.json     narration text, voice settings, and the shot list (what to zoom to,
                    highlight, and where the cursor goes). The single source of truth.
    screenshots/    3840x2160 PNGs of the app, named in the order the video uses them.
    audio/          one narration clip per scene, plus manifest.json recording which
                    narration text each clip was generated from.

Usage (run from anywhere):

    ./generate.py status              which narration clips are missing or out of date
    ./generate.py script              print the narration, for review
    ./generate.py render              render ../getting-started.mp4 at 720p, for embedding (gitignored)
    ./generate.py render --height 1080  render ../getting-started-1080p.mp4 at full resolution
    ./generate.py render --preview    quick low-resolution render to check timing and framing
    ./generate.py poster              write poster.jpg, the image the README links from
    ./generate.py stamp s03-hosting   record that audio/s03-hosting.mp3 matches the current text

Narration is generated once per scene and kept in audio/, so a render never calls a voice
service and never changes a clip you did not edit. After changing a scene's narration, run
`status`: it lists the scenes whose clip no longer matches. Regenerate those clips with the
voice in scenes.json, save each as audio/<scene id>.mp3, and run `stamp` for them.

Requires Python 3.10+, Pillow, and ffmpeg/ffprobe on PATH. Fonts: set RECIPE_FONT (and
optionally RECIPE_FONT_BOLD) to a .ttf/.ttc path, or a system font is found automatically.
"""

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
RECIPE_DIR = HERE.parent
SCENES_FILE = HERE / "scenes.json"
AUDIO_DIR = HERE / "audio"
SHOT_DIR = HERE / "screenshots"
MANIFEST = AUDIO_DIR / "manifest.json"
POSTER = HERE / "poster.jpg"

CSS_WIDTH = 1280  # the browser viewport the focus/cursor coordinates in scenes.json refer to
ACCENT = (47, 125, 246)
CARD_TOP = (15, 23, 42)
CARD_BOTTOM = (30, 41, 59)
CARD_KICKER = (89, 180, 242)
CARD_SUBTITLE = (160, 174, 192)
CAMERA_SETTLE = 3.5  # seconds a camera move takes before it holds still
CAPTION_MAX_CHARS = 95
DEFAULT_HEIGHT = 720  # the rendered video most viewers get; fine for an embedded player


# --------------------------------------------------------------------------------------
# Source data
# --------------------------------------------------------------------------------------


def load_config():
    return json.loads(SCENES_FILE.read_text())


def spoken(cfg, text):
    """The text as the voice should read it: acronyms and symbols spelled out."""
    for key in sorted(cfg.get("pronounce", {}), key=len, reverse=True):
        text = text.replace(key, cfg["pronounce"][key])
    return text


def narration_hash(cfg, scene):
    payload = {"text": spoken(cfg, scene["narration"]), "voice": cfg["voice"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def scene_audio_state(cfg, manifest, scene):
    mp3 = AUDIO_DIR / f"{scene['id']}.mp3"
    if not mp3.exists():
        return "missing"
    return "ok" if manifest.get(scene["id"]) == narration_hash(cfg, scene) else "stale"


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return float(out.strip())


# --------------------------------------------------------------------------------------
# Timeline: turns scenes.json + clip lengths into timed shots, captions and audio segments
# --------------------------------------------------------------------------------------


def caption_chunks(narration):
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", narration.strip()):
        if len(sentence) <= CAPTION_MAX_CHARS:
            chunks.append(sentence)
            continue
        current = ""
        for part in re.split(r"(?<=,)\s+", sentence):
            if current and len(current) + 1 + len(part) > CAPTION_MAX_CHARS:
                chunks.append(current)
                current = part
            else:
                current = f"{current} {part}".strip()
        if current:
            chunks.append(current)
    return chunks


def speech_pauses(mp3):
    """(start, end) of each pause in a narration clip, found from the audio itself."""
    err = subprocess.run(
        ["ffmpeg", "-i", str(mp3), "-af", "silencedetect=noise=-33dB:d=0.12", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def cue_time(narration, phrase_index, audio, pauses, tolerance=1.0):
    """When the words starting at phrase_index are spoken, in seconds into the clip.

    There are no word timestamps, so the time is estimated from the text position, then snapped
    to the end of the nearest pause when the words follow punctuation (a pause is where speech
    resumes). Returns (seconds, snapped).
    """
    predicted = phrase_index / len(narration) * audio
    follows_punct = phrase_index == 0 or narration[max(0, phrase_index - 2) : phrase_index] in (". ", ", ", "! ", "? ")
    if follows_punct:
        ends = [e for _, e in pauses if e < audio - 0.05]
        if phrase_index == 0 and pauses and pauses[0][0] < 0.05:
            return pauses[0][1], True
        near = [e for e in ends if abs(e - predicted) <= tolerance]
        if near:
            return min(near, key=lambda e: abs(e - predicted)), True
    return predicted, False


def end_crop(size, focus, zoom):
    width, height = size
    k = width / CSS_WIDTH
    fx, fy, fw, fh = (c * k for c in focus)
    cx, cy = fx + fw / 2, fy + fh / 2
    w, h = width / zoom, height / zoom
    x0 = min(max(cx - w / 2, 0), width - w)
    y0 = min(max(cy - h / 2, 0), height - h)
    return (x0, y0, x0 + w, y0 + h)


def build_timeline(cfg):
    v = cfg["video"]
    shots, segments, captions = [], [], []
    sizes = {}

    def size_of(name):
        if name not in sizes:
            with Image.open(SHOT_DIR / name) as im:
                sizes[name] = im.size
        return sizes[name]

    t = 0.0
    shots.append({"kind": "card", "card": cfg["title_card"], "start": t, "end": t + v["title_seconds"]})
    segments.append({"kind": "silence", "dur": v["title_seconds"]})
    t += v["title_seconds"]

    for scene in cfg["scenes"]:
        mp3 = AUDIO_DIR / f"{scene['id']}.mp3"
        audio = probe_duration(mp3)
        dur = v["lead"] + audio + v["tail"]
        narration = scene["narration"]
        pauses = speech_pauses(mp3)
        starts = []
        for i, spec in enumerate(scene["shots"]):
            if i == 0:
                starts.append(t)
                continue
            idx = narration.find(spec["at"])
            if idx < 0:
                sys.exit(f"{scene['id']}: shot cue {spec['at']!r} is not in the narration")
            spoken_at, snapped = cue_time(narration, idx, audio, pauses)
            start = t + v["lead"] + spoken_at - (0.1 if snapped else 0.3)
            starts.append(max(start, starts[-1] + 0.9))
        for i, spec in enumerate(scene["shots"]):
            end = starts[i + 1] if i + 1 < len(starts) else t + dur
            size = size_of(spec["image"])
            c1 = end_crop(size, spec.get("frame", spec["focus"]), spec.get("zoom", 1.0))
            prev = shots[-1]
            same = prev["kind"] == "img" and prev["image"] == spec["image"]
            c0 = prev["crop1"] if same else (0.0, 0.0, float(size[0]), float(size[1]))
            shots.append(
                {
                    "kind": "img",
                    "scene": scene["id"],
                    "image": spec["image"],
                    "size": size,
                    "start": starts[i],
                    "end": end,
                    "focus": spec["focus"],
                    "highlight": spec.get("highlight", False),
                    "cursor": spec.get("cursor"),
                    "caption": spec.get("caption", "bottom"),
                    "crop0": c0,
                    "crop1": c1,
                    "continues": same,
                    "xf": v["crossfade"] if i == 0 else 0.12,
                }
            )
        chunks = caption_chunks(narration)
        chunk_starts, search = [], 0
        for chunk in chunks:
            idx = narration.find(chunk, search)
            search = idx + len(chunk)
            chunk_starts.append(t + v["lead"] + cue_time(narration, idx, audio, pauses)[0])
        for i, chunk in enumerate(chunks):
            end = chunk_starts[i + 1] if i + 1 < len(chunks) else t + v["lead"] + audio
            captions.append({"start": chunk_starts[i], "end": end, "text": chunk})
        segments.append({"kind": "scene", "id": scene["id"], "mp3": str(mp3), "lead": v["lead"], "dur": dur})
        t += dur

    shots.append({"kind": "card", "card": cfg["end_card"], "start": t, "end": t + v["end_seconds"]})
    segments.append({"kind": "silence", "dur": v["end_seconds"]})
    t += v["end_seconds"]
    return {"shots": shots, "segments": segments, "captions": captions, "total": t}


# --------------------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------------------

_FONT_CANDIDATES = [
    (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ),
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
]
_fonts = {}


def font(size, bold=False):
    key = (round(size), bold)
    if key not in _fonts:
        env = os.environ.get("RECIPE_FONT_BOLD" if bold else "RECIPE_FONT")
        paths = [env] if env else []
        for regular, heavy in _FONT_CANDIDATES:
            paths.append(heavy if bold else regular)
        for path in paths:
            if path and Path(path).exists():
                index = 1 if bold and path.endswith("Helvetica.ttc") else 0
                _fonts[key] = ImageFont.truetype(path, round(size), index=index)
                break
        else:
            sys.exit("No usable font found: set RECIPE_FONT to a .ttf or .ttc file")
    return _fonts[key]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


_images = {}


def get_image(name):
    if name not in _images:
        _images[name] = Image.open(SHOT_DIR / name).convert("RGB")
    return _images[name]


_card_bg = {}


def card_background(out):
    if out not in _card_bg:
        grad = Image.linear_gradient("L").resize(out)
        _card_bg[out] = ImageOps.colorize(grad, black=CARD_TOP, white=CARD_BOTTOM)
    return _card_bg[out].copy()


def spaced_text(draw, center_x, y, text, fnt, fill, spacing):
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = center_x - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + spacing


def render_card(card, out, play_badge=False):
    width, height = out
    s = height / 1080
    img = card_background(out)
    draw = ImageDraw.Draw(img)
    cx = width / 2
    y = height * (0.30 if play_badge else 0.34)
    spaced_text(draw, cx, y, card["kicker"].upper(), font(34 * s, True), CARD_KICKER, 7 * s)
    title_font = font(128 * s, True)
    tw = draw.textlength(card["title"], font=title_font)
    draw.text((cx - tw / 2, y + 70 * s), card["title"], font=title_font, fill=(255, 255, 255))
    sub_font = font(44 * s)
    sw = draw.textlength(card["subtitle"], font=sub_font)
    draw.text((cx - sw / 2, y + 250 * s), card["subtitle"], font=sub_font, fill=CARD_SUBTITLE)
    if play_badge:
        r = 78 * s
        bx, by = cx, height * 0.80
        ss = 4
        tile = Image.new("RGBA", (int(2 * r * ss) + 8, int(2 * r * ss) + 8), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        c = tile.width / 2
        td.ellipse((c - r * ss, c - r * ss, c + r * ss, c + r * ss), fill=(255, 255, 255, 255))
        tri = r * ss * 0.46
        td.polygon([(c - tri * 0.55, c - tri), (c - tri * 0.55, c + tri), (c + tri * 1.05, c)], fill=ACCENT + (255,))
        tile = tile.resize((tile.width // ss, tile.height // ss), Image.LANCZOS)
        img.paste(tile.convert("RGB"), (int(bx - tile.width / 2), int(by - tile.height / 2)), tile.split()[3])
    return img


def draw_highlight(frame, shot, crop, alpha, out):
    width, height = out
    k = shot["size"][0] / CSS_WIDTH
    sc = width / (crop[2] - crop[0])
    pad = 10 * k
    fx, fy, fw, fh = (c * k for c in shot["focus"])
    r = ((fx - pad - crop[0]) * sc, (fy - pad - crop[1]) * sc, (fx + fw + pad - crop[0]) * sc, (fy + fh + pad - crop[1]) * sc)
    radius = 14 * (height / 1080)
    mask = Image.new("L", out, int(255 * 0.42 * alpha))
    ImageDraw.Draw(mask).rounded_rectangle(r, radius=radius, fill=0)
    frame.paste((0, 0, 0), mask=mask)
    margin, ss = 12, 4
    tx, ty = int(r[0]) - margin, int(r[1]) - margin
    tw, th = int(r[2] - r[0]) + 2 * margin, int(r[3] - r[1]) + 2 * margin
    tile = Image.new("RGBA", (tw * ss, th * ss), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        ((r[0] - tx) * ss, (r[1] - ty) * ss, (r[2] - tx) * ss, (r[3] - ty) * ss),
        radius=radius * ss,
        outline=ACCENT + (int(255 * alpha),),
        width=int(5 * (height / 1080) * ss),
    )
    tile = tile.resize((tw, th), Image.LANCZOS)
    frame.paste(tile.convert("RGB"), (tx, ty), tile.split()[3])


def draw_cursor(frame, shot, crop, t_local, out):
    width, height = out
    k = shot["size"][0] / CSS_WIDTH
    sc = width / (crop[2] - crop[0])
    s = height / 1080
    p0 = [c * k for c in shot["cursor"]["from"]]
    p1 = [c * k for c in shot["cursor"]["to"]]
    start, move = 0.5, min(1.3, 0.55 * (shot["end"] - shot["start"]))
    u = clamp((t_local - start) / move)
    e = ease(u)
    arc = math.sin(math.pi * e) * 60 * k
    x = p0[0] + (p1[0] - p0[0]) * e + arc * 0.4
    y = p0[1] + (p1[1] - p0[1]) * e + arc
    ox, oy = (x - crop[0]) * sc, (y - crop[1]) * sc
    ss = 4
    tile_w, tile_h = int(60 * s) + 24, int(70 * s) + 24
    tile = Image.new("RGBA", (tile_w * ss, tile_h * ss), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    poly = [(0, 0), (0, 30), (7, 23), (12, 35), (17, 33), (12, 21), (21, 21)]
    pts = [(12 * ss + px * s * ss, 12 * ss + py * s * ss) for px, py in poly]
    td.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=int(2 * ss))
    tile = tile.resize((tile_w, tile_h), Image.LANCZOS)
    if u >= 1:
        tc = t_local - (start + move)
        if 0 <= tc < 0.6:
            ring = Image.new("RGBA", out, (0, 0, 0, 0))
            rr = (12 + 44 * (tc / 0.6)) * s
            ImageDraw.Draw(ring).ellipse((ox - rr, oy - rr, ox + rr, oy + rr), outline=ACCENT + (int(255 * (1 - tc / 0.6)),), width=max(2, int(4 * s)))
            frame.paste(ring.convert("RGB"), (0, 0), ring.split()[3])
    frame.paste(tile.convert("RGB"), (int(ox - 12), int(oy - 12)), tile.split()[3])


def shot_frame(shot, t, out, cfg):
    if shot["kind"] == "card":
        return render_card(shot["card"], out)
    t_local = t - shot["start"]
    u = ease(t_local / min(shot["end"] - shot["start"], CAMERA_SETTLE))
    crop = tuple(a + (b - a) * u for a, b in zip(shot["crop0"], shot["crop1"]))
    frame = get_image(shot["image"]).resize(out, Image.LANCZOS, box=crop)
    if shot["highlight"]:
        alpha = ease((t_local - 0.35) / 0.45)
        if alpha > 0:
            draw_highlight(frame, shot, crop, alpha, out)
    if shot["cursor"]:
        draw_cursor(frame, shot, crop, t_local, out)
    return frame


_caption_cache = {}


def caption_image(text, out):
    key = (text, out)
    if key in _caption_cache:
        return _caption_cache[key]
    width, height = out
    s = height / 1080
    fnt = font(40 * s)
    max_w = width * 0.78
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and probe.textlength(trial, font=fnt) > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    line_h = 54 * s
    pad_x, pad_y = 34 * s, 20 * s
    box_w = max(probe.textlength(ln, font=fnt) for ln in lines) + 2 * pad_x
    box_h = line_h * len(lines) + 2 * pad_y - 8 * s
    img = Image.new("RGBA", (int(box_w), int(box_h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=18 * s, fill=(8, 12, 24, 190))
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=fnt)
        d.text(((img.width - lw) / 2, pad_y + i * line_h - 2 * s), ln, font=fnt, fill=(255, 255, 255, 255))
    _caption_cache[key] = img
    return img


def draw_caption(frame, tl, t, out, cfg):
    for cap in tl["captions"]:
        if cap["start"] - 0.05 <= t < cap["end"]:
            fade = min(clamp((t - cap["start"] + 0.05) / 0.2), clamp((cap["end"] - t) / 0.15))
            shot = next(s for s in tl["shots"] if s["start"] <= t < s["end"] + 1e-9)
            img = caption_image(cap["text"], out)
            x = (out[0] - img.width) // 2
            y = int(out[1] * 0.055) if shot.get("caption") == "top" else int(out[1] * 0.915 - img.height)
            alpha = img.split()[3].point(lambda v: int(v * fade))
            frame.paste(img.convert("RGB"), (x, y), alpha)
            return


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------

_ctx = {}


def init_worker(cfg, tl, out, captions):
    _ctx.update(cfg=cfg, tl=tl, out=out, captions=captions)


def render_frame(index_and_t):
    cfg, tl, out = _ctx["cfg"], _ctx["tl"], _ctx["out"]
    t = index_and_t
    shots = tl["shots"]
    i = next((n for n, s in enumerate(shots) if s["start"] <= t < s["end"]), len(shots) - 1)
    shot = shots[i]
    frame = shot_frame(shot, t, out, cfg)
    xf = shot.get("xf", cfg["video"]["crossfade"])
    if i > 0 and t - shot["start"] < xf:
        prev = shots[i - 1]
        continuous = shot.get("continues", False)
        if not continuous:
            before = shot_frame(prev, prev["end"], out, cfg)
            frame = Image.blend(before, frame, ease((t - shot["start"]) / xf))
    if _ctx["captions"]:
        draw_caption(frame, tl, t, out, cfg)
    fade_in, fade_out = 0.4, 0.5
    f = min(clamp(t / fade_in), clamp((tl["total"] - t) / fade_out))
    if f < 1:
        frame = Image.blend(Image.new("RGB", out, (0, 0, 0)), frame, f)
    return frame.tobytes()


def build_narration(tl, cfg, workdir):
    parts = []
    for i, seg in enumerate(tl["segments"]):
        wav = workdir / f"{i:02d}.wav"
        if seg["kind"] == "silence":
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seg['dur']:.3f}", str(wav)]
        else:
            delay = int(seg["lead"] * 1000)
            cmd = [
                "ffmpeg", "-y", "-v", "error", "-i", seg["mp3"],
                "-af", f"adelay={delay}:all=1,apad=whole_dur={seg['dur']:.3f}",
                "-t", f"{seg['dur']:.3f}", "-ar", "44100", "-ac", "1", str(wav),
            ]
        subprocess.run(cmd, check=True)
        parts.append(wav)
    listing = workdir / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    out = workdir / "narration.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "1", str(out)],
        check=True,
    )
    return out


def cmd_status(args):
    cfg = load_config()
    manifest = load_manifest()
    bad = 0
    for scene in cfg["scenes"]:
        state = scene_audio_state(cfg, manifest, scene)
        extra = ""
        if state != "missing":
            extra = f"  {probe_duration(AUDIO_DIR / (scene['id'] + '.mp3')):5.1f}s"
        print(f"{state:8} {scene['id']}{extra}")
        bad += state != "ok"
    print("narration is current" if not bad else f"{bad} scene(s) need narration: see the usage notes in this file")
    return 1 if bad else 0


def cmd_script(args):
    cfg = load_config()
    words = 0
    for scene in cfg["scenes"]:
        print(f"[{scene['id']}]\n{scene['narration']}\n")
        words += len(scene["narration"].split())
    print(f"{words} words")


def cmd_stamp(args):
    cfg = load_config()
    manifest = load_manifest()
    ids = [s["id"] for s in cfg["scenes"]] if args.all else args.ids
    known = {s["id"]: s for s in cfg["scenes"]}
    for sid in ids:
        if sid not in known:
            sys.exit(f"unknown scene {sid}")
        if not (AUDIO_DIR / f"{sid}.mp3").exists():
            sys.exit(f"audio/{sid}.mp3 does not exist")
        manifest[sid] = narration_hash(cfg, known[sid])
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"stamped {len(ids)} scene(s)")


def cmd_poster(args):
    cfg = load_config()
    out = (1280, 720)
    render_card(cfg["title_card"], out, play_badge=True).save(POSTER, quality=90)
    print(f"wrote {POSTER}")


def cmd_render(args):
    cfg = load_config()
    if cmd_status(argparse.Namespace()) and not args.allow_stale:
        sys.exit("refusing to render: fix the narration first (or pass --allow-stale)")
    tl = build_timeline(cfg)
    v = cfg["video"]
    height = 540 if args.preview else args.height
    scale = height / v["height"]
    out = (int(round(v["width"] * scale / 2)) * 2, int(round(height / 2)) * 2)
    fps = 15 if args.preview else v["fps"]
    # 720p is the default deliverable, so it keeps the plain name; other sizes say so in theirs.
    name = f"{cfg['slug']}.mp4" if height == DEFAULT_HEIGHT else f"{cfg['slug']}-{height}p.mp4"
    target = Path(args.out) if args.out else RECIPE_DIR / name
    frames = int(round(tl["total"] * fps))
    print(f"{tl['total']:.1f}s, {frames} frames at {out[0]}x{out[1]} -> {target}")
    with tempfile.TemporaryDirectory() as tmp:
        narration = build_narration(tl, cfg, Path(tmp))
        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out[0]}x{out[1]}",
             "-r", str(fps), "-i", "-", "-i", str(narration),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20" if height < 1080 else "18",
             "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-t", f"{tl['total']:.3f}", str(target)],
            stdin=subprocess.PIPE,
        )
        times = [i / fps for i in range(frames)]
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(min(os.cpu_count() or 2, 8), initializer=init_worker, initargs=(cfg, tl, out, not args.no_captions)) as pool:
            for n, data in enumerate(pool.imap(render_frame, times, chunksize=4)):
                enc.stdin.write(data)
                if n % 300 == 0:
                    print(f"  frame {n}/{frames}", flush=True)
        enc.stdin.close()
        if enc.wait():
            sys.exit("ffmpeg failed")
    print(f"done: {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("script").set_defaults(fn=cmd_script)
    sub.add_parser("poster").set_defaults(fn=cmd_poster)
    st = sub.add_parser("stamp")
    st.add_argument("ids", nargs="*")
    st.add_argument("--all", action="store_true")
    st.set_defaults(fn=cmd_stamp)
    rd = sub.add_parser("render")
    rd.add_argument("--preview", action="store_true", help="half resolution, 15 fps, for checking timing and framing")
    rd.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="output height in pixels (default 720; 1080 is the source size)")
    rd.add_argument("--no-captions", action="store_true")
    rd.add_argument("--allow-stale", action="store_true")
    rd.add_argument("--out", help="output path (default: ../<slug>.mp4)")
    rd.set_defaults(fn=cmd_render)
    args = parser.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
