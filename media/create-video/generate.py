#!/usr/bin/env python3
"""Render a narrated explainer video from a script of scenes.

A video is a folder: the current folder, or the one given with --dir.

    scenes.json    the script: what each scene shows and says, the voice, and the look
    audio/         one narration clip per scene, <scene id>.mp3, and manifest.json, which
                   records the text each clip was made from
    <slug>.mp4     the rendered video

Commands:

    python3 generate.py check             is everything this script needs installed? It installs nothing.
    python3 generate.py init              write a starter scenes.json to edit
    python3 generate.py script            print the narration, to review before it is voiced
    python3 generate.py status            which scenes still need a narration clip
    python3 generate.py status --json     the same for an agent: the exact text to voice, and the voice
    python3 generate.py stamp ID...       record that audio/<ID>.mp3 was made from the current text
    python3 generate.py draft             voice the scenes that need it with this computer's own voice
                                          (macOS only), for a free rough cut
    python3 generate.py render --preview  quick low-resolution render, to check timing
    python3 generate.py render            render the video
    python3 generate.py poster            write poster.jpg, a still of the title card

scenes.json:

    slug          names the output file
    video         width, height, fps; lead and tail (seconds of silence around each clip);
                  crossfade (seconds); captions (true shows the narration as captions)
    brand         kicker (the small line above every title), footer (the small line at the bottom)
    colors        background_top, background_bottom, panel, panel_border, text, muted, kicker and
                  accent, each as "#rrggbb"
    fonts         regular and bold: paths to .ttf or .ttc files. Optional: a system font is found.
    voice         what the narration is made with. This script never calls a voice service. It
                  keeps these values with each clip, so it can tell when a clip is out of date.
    pronounce     words the voice misreads, and how to say them: {"SQL": "sequel"}. Applied to
                  the text that is voiced, not to the captions.
    title_card    kicker, title, subtitle, seconds. Shown first, without narration.
    end_card      the same, shown last
    scenes        a list. Each scene has:
        id          names its clip: audio/<id>.mp3
        title       the headline
        points      up to four short lines, shown as numbered panels. A point can be
                    {"text": "...", "at": "words from the narration"}: it then appears when those
                    words are spoken. Otherwise the points appear as the scene starts.
        note        one line under the points (optional)
        narration   what the voice says

Narration is made once per scene and kept in audio/, so a render never changes a clip that nobody
edited. After you change a scene's narration, `status` reports the scene as stale: make that one
clip again, save it as audio/<id>.mp3, and `stamp` it. Any voice works, as long as each scene ends
up as an mp3 there.

Needs Python 3.9 or later, Pillow, and ffmpeg (with ffprobe) on PATH.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import PIL
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # `check` reports it; every drawing command stops with the install hint
    PIL = None

REF_HEIGHT = 1080  # the layout below is written for a 1920x1080 frame and scaled to the output
CAPTION_MAX_CHARS = 95
REVEAL_SECONDS = 0.45  # how long a point takes to fade in
PREVIEW_HEIGHT = 540

DEFAULT_VIDEO = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "lead": 0.5,
    "tail": 0.7,
    "crossfade": 0.35,
    "captions": True,
}
DEFAULT_COLORS = {
    "background_top": "#0f172a",
    "background_bottom": "#1e293b",
    "panel": "#1a2740",
    "panel_border": "#3b4d6b",
    "text": "#ffffff",
    "muted": "#a0aec0",
    "kicker": "#59b4f2",
    "accent": "#f5b82e",
}
DEFAULT_CARD_SECONDS = 3.0

STARTER = {
    "slug": "my-video",
    "video": dict(DEFAULT_VIDEO),
    "brand": {"kicker": "Your company", "footer": ""},
    "colors": dict(DEFAULT_COLORS),
    "voice": {
        "provider": "elevenlabs",
        "voice_name": "",
        "voice_id": "",
        "model_id": "eleven_multilingual_v2",
        "seed": 42,
        "voice_settings": {"stability": 0.6, "similarity_boost": 0.75, "style": 0, "use_speaker_boost": True},
    },
    "pronounce": {},
    "title_card": {"title": "Your title", "subtitle": "One line that says what the viewer will learn", "seconds": 3},
    "end_card": {"title": "What to do next", "subtitle": "Where to go, or who to ask", "seconds": 3},
    "scenes": [
        {
            "id": "s01-what",
            "title": "Say what this is about.",
            "points": ["First idea", "Second idea", "Third idea"],
            "note": "One line the viewer should remember.",
            "narration": "Two or three sentences that the voice says for this scene. Keep them short and plain.",
        },
        {
            "id": "s02-how",
            "title": "Show how it works.",
            "points": [
                {"text": "First step", "at": "First"},
                {"text": "Second step", "at": "Then"},
                {"text": "Third step", "at": "Finally"},
            ],
            "narration": "First, say what happens first. Then, say what happens next. Finally, say how it ends.",
        },
    ],
}


# --------------------------------------------------------------------------------------
# What this script needs
# --------------------------------------------------------------------------------------


def install_hint(tool):
    system = platform.system()
    if tool == "pillow":
        if system == "Windows":
            return "py -m venv .venv && .venv\\Scripts\\pip install pillow   (then run this script with .venv\\Scripts\\python)"
        return "python3 -m venv .venv && .venv/bin/pip install pillow   (then run this script with .venv/bin/python)"
    if system == "Darwin":
        return "brew install ffmpeg   (needs Homebrew: https://brew.sh)"
    if system == "Windows":
        return "winget install Gyan.FFmpeg"
    return "sudo apt install ffmpeg   (Debian and Ubuntu; otherwise use your distribution's package manager)"


def need_pillow():
    if PIL is None:
        sys.exit(f"Pillow is not installed. Install it with:\n  {install_hint('pillow')}")


def need_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        sys.exit(f"{' and '.join(missing)} not found on PATH. Install ffmpeg with:\n  {install_hint('ffmpeg')}")


def ffmpeg_encoders():
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}


# --------------------------------------------------------------------------------------
# Source data
# --------------------------------------------------------------------------------------


def project_root(arg):
    """The video's folder: --dir, else the current folder if it holds a scenes.json, else this script's."""
    if arg:
        return Path(arg).resolve()
    if (Path.cwd() / "scenes.json").exists():
        return Path.cwd()
    here = Path(__file__).resolve().parent
    return here if (here / "scenes.json").exists() else Path.cwd()


def load_config(root):
    path = root / "scenes.json"
    if not path.exists():
        sys.exit(f"No scenes.json in {root}. Run `init` to write a starter, or pass --dir.")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"scenes.json is not valid JSON: {exc}")
    cfg["video"] = {**DEFAULT_VIDEO, **cfg.get("video", {})}
    cfg["colors"] = {**DEFAULT_COLORS, **cfg.get("colors", {})}
    for key in ("brand", "voice", "pronounce", "fonts"):
        cfg.setdefault(key, {})
    cfg.setdefault("slug", root.name)
    scenes = cfg.get("scenes") or []
    if not scenes:
        sys.exit("scenes.json has no scenes")
    seen = set()
    for scene in scenes:
        sid = scene.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sid):
            sys.exit(f"scene id {sid!r}: use letters, digits, hyphens and underscores")
        if sid in seen:
            sys.exit(f"scene id {sid!r} is used twice")
        seen.add(sid)
        for field in ("title", "narration"):
            if not str(scene.get(field, "")).strip():
                sys.exit(f"{sid}: {field} is empty")
        points = [p if isinstance(p, dict) else {"text": p} for p in scene.get("points", [])]
        if len(points) > 4:
            sys.exit(f"{sid}: a scene shows at most four points")
        for point in points:
            if point.get("at") and point["at"] not in scene["narration"]:
                sys.exit(f"{sid}: the cue {point['at']!r} is not in the narration")
        scene["points"] = points
    return cfg


def spoken(cfg, text):
    """The text as the voice should read it: acronyms and symbols spelled out."""
    for key in sorted(cfg["pronounce"], key=len, reverse=True):
        text = text.replace(key, cfg["pronounce"][key])
    return text


def narration_hash(cfg, scene):
    payload = {"text": spoken(cfg, scene["narration"]), "voice": cfg["voice"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def load_manifest(root):
    path = root / "audio" / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_manifest(root, manifest):
    (root / "audio").mkdir(exist_ok=True)
    (root / "audio" / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def clip_path(root, scene):
    return root / "audio" / f"{scene['id']}.mp3"


def clip_state(root, cfg, manifest, scene):
    """missing, stale (made from other text or another voice), draft (the computer's voice), or ok."""
    if not clip_path(root, scene).exists():
        return "missing"
    digest = narration_hash(cfg, scene)
    recorded = manifest.get(scene["id"])
    if recorded == digest:
        return "ok"
    return "draft" if recorded == f"draft:{digest}" else "stale"


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return float(out.strip())


# --------------------------------------------------------------------------------------
# Timeline: turns scenes.json and the clip lengths into timed shots, captions and audio
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
        ["ffmpeg", "-nostdin", "-i", str(mp3), "-af", "silencedetect=noise=-33dB:d=0.12", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def narration_clock(narration, audio, pauses):
    """A function from a position in the narration (a character index) to seconds into its clip.

    There are no word timestamps. A voice pauses at punctuation, so the pauses found in the audio
    are matched, in order, to the punctuation in the text: a pause may match no punctuation (the
    voice took a breath) and punctuation may match no pause (a comma read straight through).
    Between matches the time is interpolated.
    """
    pauses = list(pauses)
    speech_start, speech_end = 0.0, audio
    if pauses and pauses[0][0] < 0.05:
        speech_start = pauses.pop(0)[1]
    if pauses and pauses[-1][1] > audio - 0.1:
        speech_end = pauses.pop()[0]

    # Where a phrase ends and the next begins, and whether a sentence ends there.
    breaks = re.finditer(r"(?<=\S)([.!?,:;]+|\s[-\u2013\u2014])[\"')\]]*\s+", narration)
    marks = [(m.start(1), m.end(), bool(set(m.group(1)) & set(".!?"))) for m in breaks]
    quiet, positions = 0.0, []  # each pause's position in time with the pauses before it removed
    for a, b in pauses:
        positions.append(a - speech_start - quiet)
        quiet += b - a
    spoken_seconds = max(speech_end - speech_start - quiet, 0.001)
    expected = [end / len(narration) * spoken_seconds for end, _, _ in marks]

    # cost[j][k]: the cheapest way to account for the first j marks and the first k pauses
    m, n = len(marks), len(pauses)
    skip_mark = [1.2 if strong else 0.35 for _, _, strong in marks]
    skip_pause = [0.5 + (b - a) for a, b in pauses]
    cost = [[0.0] * (n + 1) for _ in range(m + 1)]
    for j in range(1, m + 1):
        cost[j][0] = cost[j - 1][0] + skip_mark[j - 1]
    for k in range(1, n + 1):
        cost[0][k] = cost[0][k - 1] + skip_pause[k - 1]
    for j in range(1, m + 1):
        for k in range(1, n + 1):
            match = cost[j - 1][k - 1] + abs(positions[k - 1] - expected[j - 1])
            cost[j][k] = min(match, cost[j - 1][k] + skip_mark[j - 1], cost[j][k - 1] + skip_pause[k - 1])
    anchors = [(len(narration), speech_end)]
    j, k = m, n
    while j > 0 and k > 0:
        if cost[j][k] == cost[j - 1][k - 1] + abs(positions[k - 1] - expected[j - 1]):
            anchors += [(marks[j - 1][1], pauses[k - 1][1]), (marks[j - 1][0], pauses[k - 1][0])]
            j, k = j - 1, k - 1
        elif cost[j][k] == cost[j - 1][k] + skip_mark[j - 1]:
            j -= 1
        else:
            k -= 1
    anchors.append((0, speech_start))
    anchors.reverse()

    def seconds_at(index):
        for (i0, t0), (i1, t1) in zip(anchors, anchors[1:]):
            if i0 <= index <= i1:
                return t0 if i1 == i0 else t0 + (t1 - t0) * (index - i0) / (i1 - i0)
        return speech_end

    return seconds_at


def build_timeline(root, cfg):
    v = cfg["video"]
    shots, segments, captions = [], [], []
    t = 0.0

    def add_card(card):
        nonlocal t
        if not card:
            return
        seconds = float(card.get("seconds", DEFAULT_CARD_SECONDS))
        shots.append({"kind": "card", "card": card, "start": t, "end": t + seconds})
        segments.append({"kind": "silence", "dur": seconds})
        t += seconds

    add_card(cfg.get("title_card"))
    for index, scene in enumerate(cfg["scenes"]):
        mp3 = clip_path(root, scene)
        audio = probe_duration(mp3)
        dur = v["lead"] + audio + v["tail"]
        narration = scene["narration"]
        seconds_at = narration_clock(narration, audio, speech_pauses(mp3))
        reveal = []
        for point in scene["points"]:
            if point.get("at"):
                # a moment early, so the point is on screen as its words begin
                reveal.append(max(0.2, v["lead"] + seconds_at(narration.find(point["at"])) - 0.2))
            else:
                reveal.append(reveal[-1] + 0.35 if reveal else 0.25)
        note_at = (max(reveal) if reveal else 0.0) + REVEAL_SECONDS
        shots.append({"kind": "scene", "index": index, "start": t, "end": t + dur, "reveal": reveal, "note_at": note_at})
        chunks = caption_chunks(narration)
        starts, search = [], 0
        for chunk in chunks:
            idx = narration.find(chunk, search)
            search = idx + len(chunk)
            starts.append(t + v["lead"] + seconds_at(idx))
        for i, chunk in enumerate(chunks):
            end = starts[i + 1] if i + 1 < len(chunks) else t + v["lead"] + audio
            captions.append({"start": starts[i], "end": end, "text": chunk})
        segments.append({"kind": "scene", "mp3": str(mp3), "lead": v["lead"], "dur": dur})
        t += dur
    add_card(cfg.get("end_card"))
    return {"shots": shots, "segments": segments, "captions": captions, "total": t}


# --------------------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------------------

# (regular file, bold file, index of the bold face in its file)
_FONT_CANDIDATES = [
    ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", 0),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        0,
    ),
    ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),
]

_ctx = {}  # what a render process draws from: cfg, root, and, in a render, tl, out and captions
_fonts = {}
_cache = {}


def font_files(root, cfg):
    """((regular path, face index), (bold path, face index)), or None for Pillow's built-in font."""
    chosen = cfg.get("fonts", {})
    regular = chosen.get("regular") or os.environ.get("VIDEO_FONT")
    if regular:
        bold = chosen.get("bold") or os.environ.get("VIDEO_FONT_BOLD") or regular
        files = [str((root / p).resolve()) for p in (regular, bold)]
        for path in files:
            if not Path(path).exists():
                sys.exit(f"font not found: {path}")
        return (files[0], 0), (files[1], 0)
    for regular, bold, bold_index in _FONT_CANDIDATES:
        if Path(regular).exists() and Path(bold).exists():
            return (regular, 0), (bold, bold_index)
    return None


def font(size, bold=False):
    key = (round(size), bold)
    if key not in _fonts:
        files = font_files(_ctx["root"], _ctx["cfg"])
        if files:
            path, index = files[1 if bold else 0]
            _fonts[key] = ImageFont.truetype(path, key[0], index=index)
        else:
            try:
                _fonts[key] = ImageFont.load_default(key[0])
            except TypeError:
                sys.exit("No font found. Set fonts.regular and fonts.bold in scenes.json to .ttf files.")
    return _fonts[key]


def color(name):
    value = _ctx["cfg"]["colors"][name].lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def wrap(draw, text, fnt, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=fnt) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def fit(draw, text, sizes, bold, max_width, max_lines):
    """The largest of sizes at which text wraps into max_lines: (font, lines)."""
    for size in sizes:
        fnt = font(size, bold)
        lines = wrap(draw, text, fnt, max_width)
        if len(lines) <= max_lines and all(draw.textlength(ln, font=fnt) <= max_width for ln in lines):
            break
    return fnt, lines


def background(out):
    key = ("background", out)
    if key not in _cache:
        width, height = out
        s = height / REF_HEIGHT
        grad = Image.linear_gradient("L").resize(out)
        img = ImageOps.colorize(grad, black=color("background_top"), white=color("background_bottom"))
        # A soft disc in the top right corner, drawn large and scaled down so its edge is smooth.
        ss = 2
        mask = Image.new("L", (width * ss, height * ss), 0)
        cx, cy, r = (width - 240 * s) * ss, 90 * s * ss, 430 * s * ss
        ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=110)
        img.paste(color("panel"), mask=mask.resize(out, Image.LANCZOS))
        ImageDraw.Draw(img).rectangle((0, height - round(7 * s), width, height), fill=color("accent"))
        _cache[key] = img
    return _cache[key]


def spaced_text(draw, center_x, y, text, fnt, fill, spacing):
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    x = center_x - (sum(widths) + spacing * (len(text) - 1)) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + spacing


def render_card(card, out, play_badge=False):
    """A title or end card: kicker, title and subtitle, centered."""
    width, height = out
    s = height / REF_HEIGHT
    img = background(out).copy()
    draw = ImageDraw.Draw(img)
    cx = width / 2
    y = height * (0.30 if play_badge else 0.34)
    kicker = card.get("kicker", _ctx["cfg"]["brand"].get("kicker", ""))
    if kicker:
        spaced_text(draw, cx, y, kicker.upper(), font(34 * s, True), color("kicker"), 7 * s)
    title_font, lines = fit(draw, card.get("title", ""), [128 * s, 112 * s, 96 * s, 80 * s, 64 * s], True, width * 0.86, 1)
    title = " ".join(lines)
    draw.text((cx, y + 180 * s), title, font=title_font, fill=color("text"), anchor="ms")
    sub_font, lines = fit(draw, card.get("subtitle", ""), [44 * s, 38 * s, 32 * s], False, width * 0.86, 1)
    draw.text((cx, y + 290 * s), " ".join(lines), font=sub_font, fill=color("muted"), anchor="ms")
    if play_badge:
        r, ss = 78 * s, 4
        tile = Image.new("RGBA", (int(2 * r * ss) + 8, int(2 * r * ss) + 8), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        c = tile.width / 2
        td.ellipse((c - r * ss, c - r * ss, c + r * ss, c + r * ss), fill=(255, 255, 255, 255))
        tri = r * ss * 0.46
        td.polygon(
            [(c - tri * 0.55, c - tri), (c - tri * 0.55, c + tri), (c + tri * 1.05, c)],
            fill=color("background_top") + (255,),
        )
        tile = tile.resize((tile.width // ss, tile.height // ss), Image.LANCZOS)
        img.paste(tile, (int(cx - tile.width / 2), int(height * 0.80 - tile.height / 2)), tile.getchannel("A"))
    return img


def scene_base(index, out):
    """The part of a scene that never moves: background, kicker, title and footer."""
    key = ("base", index, out)
    if key not in _cache:
        cfg = _ctx["cfg"]
        width, height = out
        s = height / REF_HEIGHT
        img = background(out).copy()
        draw = ImageDraw.Draw(img)
        left = 100 * s
        kicker = cfg["brand"].get("kicker", "")
        if kicker:
            draw.text((left, 100 * s), kicker.upper(), font=font(27 * s, True), fill=color("kicker"), anchor="ls")
        title = cfg["scenes"][index]["title"]
        title_font, lines = fit(draw, title, [72 * s, 64 * s, 56 * s, 48 * s], True, width - 2 * left - 320 * s, 2)
        baseline = (255 if len(lines) == 1 else 215) * s
        for line in lines:
            draw.text((left, baseline), line, font=title_font, fill=color("text"), anchor="ls")
            baseline += title_font.size * 1.2
        footer = cfg["brand"].get("footer", "")
        if footer:
            draw.text((left, 1035 * s), footer, font=font(21 * s), fill=color("muted"), anchor="ls")
        draw.text((width - left, 1035 * s), f"{index + 1:02d}", font=font(24 * s), fill=color("muted"), anchor="rs")
        _cache[key] = img
    return _cache[key]


def scene_tiles(index, out):
    """What fades in during a scene, each as (image, (x, y)): one panel per point, then the note."""
    key = ("tiles", index, out)
    if key in _cache:
        return _cache[key]
    scene = _ctx["cfg"]["scenes"][index]
    width, height = out
    s = height / REF_HEIGHT
    left, gap, top, panel_h = 100 * s, 30 * s, 400 * s, 250 * s
    points = scene["points"]
    panels = []
    if points:
        panel_w = (width - 2 * left - gap * (len(points) - 1)) / len(points)
        size = (int(panel_w), int(panel_h))
        ss = 2  # the rounded panel is drawn large and scaled down so its corners are smooth
        for i, point in enumerate(points):
            shape = Image.new("RGBA", (size[0] * ss, size[1] * ss), (0, 0, 0, 0))
            ImageDraw.Draw(shape).rounded_rectangle(
                (0, 0, shape.width - 1, shape.height - 1),
                radius=24 * s * ss,
                fill=color("panel") + (255,),
                outline=color("panel_border") + (255,),
                width=max(ss, round(2 * s * ss)),
            )
            tile = shape.resize(size, Image.LANCZOS)
            draw = ImageDraw.Draw(tile)
            pad = 32 * s
            draw.text((pad, 66 * s), f"{i + 1:02d}", font=font(30 * s, True), fill=color("accent"), anchor="ls")
            text_font, lines = fit(draw, point["text"], [39 * s, 35 * s, 31 * s, 27 * s], True, size[0] - 2 * pad, 3)
            for n, line in enumerate(lines[:3]):
                y = 134 * s + n * text_font.size * 1.3
                draw.text((pad, y), line, font=text_font, fill=color("text"), anchor="ls")
            panels.append((tile, (int(left + i * (panel_w + gap)), int(top))))
    note = None
    if scene.get("note"):
        probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        note_font, lines = fit(probe, scene["note"], [40 * s, 36 * s, 32 * s], True, width - 2 * left, 2)
        line_h = note_font.size * 1.3
        tile = Image.new("RGBA", (int(width - 2 * left), int(line_h * len(lines) + 12 * s)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        for n, line in enumerate(lines):
            draw.text((0, note_font.size + n * line_h), line, font=note_font, fill=color("accent"), anchor="ls")
        note_top = (top + panel_h + 42 * s) if points else top
        note = (tile, (int(left), int(note_top)))
    _cache[key] = (panels, note)
    return _cache[key]


def paste_faded(frame, tile, position, alpha, rise=0.0):
    if alpha <= 0:
        return
    x, y = position
    mask = tile.getchannel("A")
    if alpha < 1:
        mask = mask.point(lambda v: int(v * alpha))
        y += int((1 - alpha) * rise)
    frame.paste(tile, (x, y), mask)


def shot_alphas(shot, t):
    """How far each moving part of a shot has faded in at time t: one value per point, then the note."""
    if shot["kind"] == "card":
        return ()
    local = t - shot["start"]
    parts = [ease((local - at) / REVEAL_SECONDS) for at in shot["reveal"]]
    parts.append(ease((local - shot["note_at"]) / REVEAL_SECONDS))
    return tuple(round(a, 3) for a in parts)


def shot_frame(shot, alphas, out):
    if shot["kind"] == "card":
        return render_card(shot["card"], out)
    frame = scene_base(shot["index"], out).copy()
    panels, note = scene_tiles(shot["index"], out)
    rise = 18 * out[1] / REF_HEIGHT
    for (tile, position), alpha in zip(panels, alphas):
        paste_faded(frame, tile, position, alpha, rise)
    if note:
        paste_faded(frame, note[0], note[1], alphas[-1])
    return frame


def caption_image(text, out):
    key = ("caption", text, out)
    if key in _cache:
        return _cache[key]
    width, height = out
    s = height / REF_HEIGHT
    fnt = font(40 * s)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lines = wrap(probe, text, fnt, width * 0.78)
    line_h = 54 * s
    pad_x, pad_y = 34 * s, 20 * s
    box_w = max(probe.textlength(ln, font=fnt) for ln in lines) + 2 * pad_x
    box_h = line_h * len(lines) + 2 * pad_y - 8 * s
    img = Image.new("RGBA", (int(box_w), int(box_h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=18 * s, fill=(8, 12, 24, 205))
    for i, ln in enumerate(lines):
        lw = d.textlength(ln, font=fnt)
        d.text(((img.width - lw) / 2, pad_y + i * line_h - 2 * s), ln, font=fnt, fill=(255, 255, 255, 255))
    _cache[key] = img
    return img


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def init_worker(ctx):
    _ctx.update(ctx)


def render_frame(t):
    tl, out, v = _ctx["tl"], _ctx["out"], _ctx["cfg"]["video"]
    shots = tl["shots"]
    i = next((n for n, s in enumerate(shots) if s["start"] <= t < s["end"]), len(shots) - 1)
    shot = shots[i]
    alphas = shot_alphas(shot, t)
    blend = 1.0
    if i > 0 and v["crossfade"] > 0 and t - shot["start"] < v["crossfade"]:
        blend = round(ease((t - shot["start"]) / v["crossfade"]), 3)
    caption, caption_fade = None, 0.0
    if _ctx["captions"]:
        for cap in tl["captions"]:
            if cap["start"] - 0.05 <= t < cap["end"]:
                caption = cap["text"]
                caption_fade = round(min(clamp((t - cap["start"] + 0.05) / 0.2), clamp((cap["end"] - t) / 0.15)), 3)
                break
    fade = round(min(clamp(t / 0.4), clamp((tl["total"] - t) / 0.5)), 3)

    # Most frames are identical to the one before: nothing is moving. Keep the last few.
    state = (i, alphas, blend, caption, caption_fade, fade)
    frames = _cache.setdefault("frames", {})
    if state in frames:
        return frames[state]

    frame = shot_frame(shot, alphas, out)
    if blend < 1:
        before = shots[i - 1]
        frame = Image.blend(shot_frame(before, shot_alphas(before, before["end"]), out), frame, blend)
    if caption and caption_fade > 0:
        img = caption_image(caption, out)
        position = ((out[0] - img.width) // 2, int(out[1] * 0.895 - img.height))
        paste_faded(frame, img, position, caption_fade)
    if fade < 1:
        frame = Image.blend(Image.new("RGB", out, (0, 0, 0)), frame, fade)
    data = frame.tobytes()
    if len(frames) >= 6:
        frames.clear()
    frames[state] = data
    return data


def build_narration(tl, workdir):
    parts = []
    for i, seg in enumerate(tl["segments"]):
        wav = workdir / f"{i:02d}.wav"
        if seg["kind"] == "silence":
            cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seg['dur']:.3f}", str(wav)]
        else:
            delay = int(seg["lead"] * 1000)
            cmd = [
                "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", seg["mp3"],
                "-af", f"adelay={delay}:all=1,apad=whole_dur={seg['dur']:.3f}",
                "-t", f"{seg['dur']:.3f}", "-ar", "44100", "-ac", "1", str(wav),
            ]  # fmt: skip
        subprocess.run(cmd, check=True)
        parts.append(wav)
    listing = workdir / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = workdir / "narration.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "1", str(out)],
        check=True,
    )  # fmt: skip
    return out


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_check(args):
    rows = []  # (ok, required, what, how to fix)
    rows.append((sys.version_info >= (3, 9), True, f"Python {platform.python_version()}", "install Python 3.9 or later"))
    rows.append((PIL is not None, True, f"Pillow {PIL.__version__}" if PIL else "Pillow", install_hint("pillow")))
    have_ffmpeg = bool(shutil.which("ffmpeg"))
    rows.append((have_ffmpeg, True, "ffmpeg", install_hint("ffmpeg")))
    rows.append((bool(shutil.which("ffprobe")), True, "ffprobe (part of ffmpeg)", install_hint("ffmpeg")))
    if have_ffmpeg:
        encoders = ffmpeg_encoders()
        for name in ("libx264", "aac"):
            rows.append((name in encoders, True, f"ffmpeg encoder {name}", "install a full build of ffmpeg"))
    root = project_root(args.dir)
    files = font_files(root, json.loads((root / "scenes.json").read_text()) if (root / "scenes.json").exists() else {})
    rows.append((True, False, f"font: {files[0][0]}" if files else "font: Pillow's built-in font", ""))
    rows.append((bool(shutil.which("say")), False, "draft voice (macOS `say`)", "optional: only the `draft` command uses it"))
    for ok, required, what, fix in rows:
        label = "ok" if ok else ("MISSING" if required else "none")
        print(f"{label:8} {what}" + ("" if ok or not fix else f"\n         {fix}"))
    failed = [what for ok, required, what, _ in rows if required and not ok]
    print("ready to render" if not failed else "not ready: nothing was installed or changed")
    return 1 if failed else 0


def cmd_init(args):
    root = project_root(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "scenes.json"
    if target.exists():
        sys.exit(f"{target} already exists: edit it, or delete it to start again")
    target.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
    (root / "audio").mkdir(exist_ok=True)
    print(f"wrote {target}\nEdit it, then run `status` to see which scenes need narration.")


def cmd_status(args):
    root = project_root(args.dir)
    cfg = load_config(root)
    manifest = load_manifest(root)
    rows = []
    for scene in cfg["scenes"]:
        state = clip_state(root, cfg, manifest, scene)
        seconds = None
        if state != "missing" and shutil.which("ffprobe"):
            seconds = round(probe_duration(clip_path(root, scene)), 1)
        text = spoken(cfg, scene["narration"])
        rows.append({"id": scene["id"], "state": state, "seconds": seconds, "file": f"audio/{scene['id']}.mp3", "text": text})
    todo = [r for r in rows if r["state"] in ("missing", "stale")]
    drafts = [r for r in rows if r["state"] == "draft"]
    if getattr(args, "json", False):
        print(json.dumps({"voice": cfg["voice"], "scenes": rows}, indent=2))
        return 1 if todo else 0
    for row in rows:
        length = "" if row["seconds"] is None else f"  {row['seconds']:5.1f}s"
        print(f"{row['state']:8} {row['id']}{length}")
    if todo:
        print(f"{len(todo)} scene(s) need narration: save each as audio/<id>.mp3, then `stamp` it")
    elif drafts:
        print(f"{len(drafts)} scene(s) use the draft voice: before the video is final, replace each clip and `stamp` it")
    else:
        print("narration is current")
    return 1 if todo else 0


def cmd_script(args):
    cfg = load_config(project_root(args.dir))
    words = 0
    for scene in cfg["scenes"]:
        print(f"[{scene['id']}] {scene['title']}")
        for point in scene["points"]:
            print(f"  - {point['text']}")
        if scene.get("note"):
            print(f"  ({scene['note']})")
        print(f"{scene['narration']}\n")
        words += len(scene["narration"].split())
    print(f"{words} words of narration, about {words / 2.5:.0f} seconds")


def cmd_stamp(args):
    root = project_root(args.dir)
    cfg = load_config(root)
    manifest = load_manifest(root)
    known = {s["id"]: s for s in cfg["scenes"]}
    ids = list(known) if args.all else args.ids
    if not ids:
        sys.exit("name the scenes to stamp, or pass --all")
    for sid in ids:
        if sid not in known:
            sys.exit(f"unknown scene {sid}")
        if not clip_path(root, known[sid]).exists():
            sys.exit(f"audio/{sid}.mp3 does not exist")
        manifest[sid] = narration_hash(cfg, known[sid])
    save_manifest(root, manifest)
    print(f"stamped {len(ids)} scene(s)")


def cmd_draft(args):
    if not shutil.which("say"):
        sys.exit("`draft` uses the macOS `say` command, which this computer does not have. Make the clips with a voice service instead.")
    need_ffmpeg()
    root = project_root(args.dir)
    cfg = load_config(root)
    manifest = load_manifest(root)
    (root / "audio").mkdir(exist_ok=True)
    made = 0
    for scene in cfg["scenes"]:
        state = clip_state(root, cfg, manifest, scene)
        if args.ids and scene["id"] not in args.ids:
            continue
        if state == "ok":
            if args.ids:
                sys.exit(f"{scene['id']} has a finished clip. Delete audio/{scene['id']}.mp3 first to replace it with a draft.")
            continue
        if state == "draft" and not args.ids:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            words = Path(tmp) / "words.txt"
            words.write_text(spoken(cfg, scene["narration"]), encoding="utf-8")
            aiff = Path(tmp) / "clip.aiff"
            voice = ["-v", args.voice] if args.voice else []
            for _attempt in range(3):  # the first `say` after a while sometimes writes an empty file
                subprocess.run(["say", *voice, "-r", "170", "-f", str(words), "-o", str(aiff)], check=True)
                if probe_duration(aiff) > 0.3:
                    break
            else:
                sys.exit(f"{scene['id']}: `say` produced no audio")
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(aiff), "-ar", "44100", "-ac", "1", "-b:a", "128k", str(clip_path(root, scene))],
                check=True,
            )
        manifest[scene["id"]] = f"draft:{narration_hash(cfg, scene)}"
        made += 1
    save_manifest(root, manifest)
    print(f"drafted {made} scene(s) with this computer's voice")


def cmd_poster(args):
    need_pillow()
    root = project_root(args.dir)
    cfg = load_config(root)
    _ctx.update(cfg=cfg, root=root)
    card = cfg.get("title_card") or {"title": cfg["slug"]}
    target = root / "poster.jpg"
    render_card(card, (1280, 720), play_badge=True).save(target, quality=90)
    print(f"wrote {target}")


def cmd_render(args):
    need_pillow()
    need_ffmpeg()
    root = project_root(args.dir)
    cfg = load_config(root)
    manifest = load_manifest(root)
    states = {s["id"]: clip_state(root, cfg, manifest, s) for s in cfg["scenes"]}
    missing = [sid for sid, state in states.items() if state == "missing"]
    stale = [sid for sid, state in states.items() if state == "stale"]
    if missing:
        sys.exit(f"no narration for: {', '.join(missing)}. Run `status`.")
    if stale and not args.allow_stale:
        sys.exit(f"narration is out of date for: {', '.join(stale)}. Make those clips again and `stamp` them, or pass --allow-stale.")
    drafts = [sid for sid, state in states.items() if state == "draft"]
    if drafts:
        print(f"note: {len(drafts)} scene(s) use the draft voice")

    v = cfg["video"]
    tl = build_timeline(root, cfg)
    usual = int(v.get("default_height", v["height"]))
    height = PREVIEW_HEIGHT if args.preview else (args.height or usual)
    scale = height / v["height"]
    out = (int(round(v["width"] * scale / 2)) * 2, int(round(height / 2)) * 2)
    fps = 15 if args.preview else v["fps"]
    if args.out:
        target = Path(args.out).resolve()
    else:
        # The usual size keeps the plain name; other sizes say so in theirs.
        suffix = "-preview" if args.preview else ("" if height == usual else f"-{height}p")
        target = (root / cfg.get("output_dir", ".")).resolve() / f"{cfg['slug']}{suffix}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(tl["total"] * fps))
    print(f"{tl['total']:.1f}s, {frames} frames at {out[0]}x{out[1]} -> {target}")

    captions = v["captions"] and not args.no_captions
    ctx = {"cfg": cfg, "root": root, "tl": tl, "out": out, "captions": captions}
    with tempfile.TemporaryDirectory() as tmp:
        narration = build_narration(tl, Path(tmp))
        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out[0]}x{out[1]}",
             "-r", str(fps), "-i", "-", "-i", str(narration),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20" if height < 1080 else "18",
             "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-t", f"{tl['total']:.3f}", str(target)],
            stdin=subprocess.PIPE,
        )  # fmt: skip
        times = [i / fps for i in range(frames)]
        pool = multiprocessing.get_context("spawn").Pool(min(os.cpu_count() or 2, 8), initializer=init_worker, initargs=(ctx,))
        with pool:
            for n, data in enumerate(pool.imap(render_frame, times, chunksize=8)):
                enc.stdin.write(data)
                if n % 300 == 0:
                    print(f"  frame {n}/{frames}", flush=True)
        enc.stdin.close()
        if enc.wait():
            sys.exit("ffmpeg failed")
    print(f"done: {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", help="the video's folder (default: the current folder)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="report what is installed; installs nothing").set_defaults(fn=cmd_check)
    sub.add_parser("init", help="write a starter scenes.json").set_defaults(fn=cmd_init)
    sub.add_parser("script", help="print the narration").set_defaults(fn=cmd_script)
    st = sub.add_parser("status", help="which scenes need a narration clip")
    st.add_argument("--json", action="store_true", help="for an agent: each scene's state and the text to voice")
    st.set_defaults(fn=cmd_status)
    sp = sub.add_parser("stamp", help="record that a scene's clip matches its text")
    sp.add_argument("ids", nargs="*")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_stamp)
    dr = sub.add_parser("draft", help="voice scenes with this computer's own voice (macOS)")
    dr.add_argument("ids", nargs="*", help="scenes to draft (default: those with no clip or a stale one)")
    dr.add_argument("--voice", help="a voice name from `say -v '?'`")
    dr.set_defaults(fn=cmd_draft)
    sub.add_parser("poster", help="write poster.jpg").set_defaults(fn=cmd_poster)
    rd = sub.add_parser("render", help="render the video")
    rd.add_argument("--preview", action="store_true", help="half resolution, 15 fps, for checking timing")
    rd.add_argument("--height", type=int, help="output height in pixels (default: the video's own)")
    rd.add_argument("--no-captions", action="store_true")
    rd.add_argument("--allow-stale", action="store_true", help="render even if a clip no longer matches its text")
    rd.add_argument("--out", help="output file (default: <slug>.mp4 in the video's folder)")
    rd.set_defaults(fn=cmd_render)
    args = parser.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
