#!/usr/bin/env python3
import asyncio
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
from array import array
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
WORDS_FILE = ROOT / "data" / "words.json"
PROGRESS_FILE = ROOT / "data" / "progress.json"
VIDEO_OUT = OUT / "thewordlestuff_mvp.mp4"
STORY_OUT = OUT / "thewordlestuff_storyboard.json"
START_DATE = date(2021, 6, 19)

W, H = 1080, 1920
FPS = 30
BG = "#fbfaf7"
TEXT = "#1f2328"
MUTED = "#878a8c"
GRID_BORDER = "#d3d6da"
ABSENT = "#787c7e"
PRESENT = "#c9b458"
CORRECT = "#6aaa64"
KEY_BG = "#d3d6da"

KEY_ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]

EDGE_VOICES = [
    "en-US-AvaNeural",
    "en-US-AndrewNeural",
    "en-US-EmmaNeural",
    "en-US-BrianNeural",
]

# Per-beat prosody: solve-moment beat -> (rate, pitch) for edge-tts. Lets one solver
# voice ride the tension and payoff instead of reading every line at one flat speed.
EDGE_BEAT_PROSODY = {
    "opener": ("+0%", "+0Hz"),
    "calm": ("+0%", "+0Hz"),
    "uncertain": ("-5%", "-1Hz"),
    "rushed": ("+13%", "+3Hz"),
    "tense": ("+8%", "+2Hz"),
    "locked_in": ("+3%", "+1Hz"),
    "flat": ("-3%", "-2Hz"),
    "curious": ("+2%", "+1Hz"),
    "good": ("+5%", "+2Hz"),
    "excited": ("+11%", "+4Hz"),
    "triumphant": ("+2%", "+2Hz"),
}
# Kokoro fallback has no pitch control, so map the same beats to speed only.
KOKORO_BEAT_SPEED = {
    "opener": 0.98, "calm": 0.98, "uncertain": 0.92, "rushed": 1.15, "tense": 1.08,
    "locked_in": 1.02, "flat": 0.95, "curious": 1.0, "good": 1.04, "excited": 1.1,
    "triumphant": 1.0,
}

KOKORO_VOICES = [
    "af_heart",
    "am_adam",
    "af_bella",
    "am_michael",
    "af_nicole",
    "am_eric",
]
KOKORO_PIPELINE = None

VOICE_OPENERS = [
    "Okay.",
    "Alright.",
    "Let's see.",
    "New one.",
]

VOICE_REACTIONS = {
    "miss": ["nothing.", "not much.", "okay, that mostly clears letters."],
    "some": ["hmm, one clue.", "okay, that helps a little.", "not great, but useful."],
    "good": ["that's useful.", "okay, now we're close.", "that helps a lot."],
    "close": ["oh, that's close.", "I think I see it.", "wait, that's almost it."],
    "final": ["yeah, this should be it.", "I think that's it.", "this has to be it."],
}

VOICE_FILLERS = ["Hmm", "Okay", "Right", "Oh"]
VOICE_HESITATIONS = ["Wait", "Actually", "Hold on", "Maybe"]
MOODS = ["calm", "rushed", "uncertain", "locked_in"]

CHOREO_PROFILES = [
    {
        "name": "thoughtful",
        "pre": (0.35, 0.24),
        "press": 0.09,
        "between": (0.10, 0.08),
        "submit": (0.55, 0.20),
        "reveal": (0.29, 0.09),
        "hold": (1.05, 0.45),
        "final_hold": (1.95, 0.45),
    },
    {
        "name": "steady",
        "pre": (0.24, 0.16),
        "press": 0.08,
        "between": (0.08, 0.05),
        "submit": (0.42, 0.14),
        "reveal": (0.24, 0.08),
        "hold": (0.85, 0.35),
        "final_hold": (1.65, 0.35),
    },
    {
        "name": "snappy",
        "pre": (0.18, 0.14),
        "press": 0.07,
        "between": (0.06, 0.04),
        "submit": (0.34, 0.12),
        "reveal": (0.21, 0.07),
        "hold": (0.72, 0.30),
        "final_hold": (1.45, 0.30),
    },
]


def load_words():
    return json.loads(WORDS_FILE.read_text(encoding="utf-8"))


def font(size, bold=False):
    bundled = ROOT / "assets" / "fonts" / "Inter.ttf"
    if bundled.exists():
        loaded = ImageFont.truetype(str(bundled), size)
        loaded.set_variation_by_axes([14, 700 if bold else 400])
        return loaded
    candidates = [
        "/usr/share/fonts/truetype/inter/Inter-Bold.ttf" if bold else "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/inter-vf/Inter.var.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/SF-Pro-Display-Bold.otf" if bold else "/Library/Fonts/SF-Pro-Display-Regular.otf",
        "/Library/Fonts/SF-Pro-Text-Bold.otf" if bold else "/Library/Fonts/SF-Pro-Text-Regular.otf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


F_TITLE = font(38, True)
F_TILE = font(78, True)
F_KEY = font(32, True)
F_HANDLE = font(42, True)


def puzzle_date():
    raw = os.getenv("WORDLE_DATE")
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    offset = int(os.getenv("WORDLE_OFFSET", str(load_progress_offset())))
    return START_DATE + timedelta(days=offset)


def load_progress_offset():
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    return int(data.get("next_offset", 0))


def pretty_date(value):
    return value.strftime("%B %-d, %Y") if os.name != "nt" else value.strftime("%B %#d, %Y")


def fetch_wordle_puzzle(value):
    url = f"https://www.nytimes.com/svc/wordle/v2/{value.isoformat()}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "thewordlestuff-mvp/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Wordle puzzle unavailable for {value.isoformat()}: HTTP {exc.code}") from exc
    return {
        "id": int(data.get("id") or (value - START_DATE).days + 1),
        "answer": data["solution"].upper(),
        "date": data.get("print_date", value.isoformat()),
    }


def score_guess(guess, answer):
    result = ["absent"] * 5
    remaining = {}
    for i, (g, a) in enumerate(zip(guess, answer)):
        if g == a:
            result[i] = "correct"
        else:
            remaining[a] = remaining.get(a, 0) + 1
    for i, g in enumerate(guess):
        if result[i] == "correct":
            continue
        if remaining.get(g, 0):
            result[i] = "present"
            remaining[g] -= 1
    return result


def compatible(word, guesses, scores):
    for guess, score in zip(guesses, scores):
        if score_guess(guess, word) != score:
            return False
    return True


def choose_guesses(answer, words):
    rng = random.Random(hashlib.sha256(answer.encode()).hexdigest())
    openers = [w for w in words["openers"] if w != answer]
    valid = [w for w in words["valid"] if w != answer]
    target_len = rng.choices([1, 2, 3, 4, 5, 6], weights=[3, 8, 18, 30, 26, 15], k=1)[0]
    if target_len == 1:
        return [answer]

    guesses = [rng.choice(openers)]
    scores = [score_guess(guesses[0], answer)]

    for _ in range(target_len - 2):
        candidates = [w for w in valid if w not in guesses and compatible(w, guesses, scores)]
        if not candidates:
            candidates = [w for w in valid if w not in guesses]
        def rank(word):
            score = score_guess(word, answer)
            return score.count("correct") * 3 + score.count("present")
        candidates.sort(key=lambda w: (rank(w), rng.random()))
        if target_len >= 5 and rng.random() < 0.35:
            start = max(0, len(candidates) // 4 - 8)
            end = max(start + 1, len(candidates) // 2)
            pick_zone = candidates[start:end]
        else:
            pick_zone = candidates[max(0, len(candidates) // 2 - 8):]
        pick_zone = pick_zone or candidates
        guess = rng.choice(pick_zone)
        guesses.append(guess)
        scores.append(score_guess(guess, answer))

    guesses.append(answer)
    return guesses[:6]


def strategy_line(guess, answer, previous_guesses, rng, used_letters=None):
    if not previous_guesses:
        return None
    scores = [score_guess(g, answer) for g in previous_guesses]
    locked = []
    misplaced = []
    for old_guess, score in zip(previous_guesses, scores):
        for i, state in enumerate(score):
            if state == "correct":
                locked.append((old_guess[i], i))
            elif state == "present":
                misplaced.append(old_guess[i])
    used_letters = used_letters if used_letters is not None else set()
    fresh_locked = [(letter, i) for letter, i in locked if letter not in used_letters and guess[i] == letter]
    fresh_misplaced = [letter for letter in misplaced if letter not in used_letters and letter in guess]
    if fresh_locked and rng.random() < 0.28:
        letter, _i = rng.choice(fresh_locked)
        used_letters.add(letter)
        return f"{letter.upper()} is fixed. {spoken_word(guess).capitalize()} makes sense."
    if fresh_misplaced and rng.random() < 0.28:
        letter = rng.choice(fresh_misplaced)
        used_letters.add(letter)
        return f"Need to move the {letter.upper()}. Maybe {spoken_word(guess)}."
    return None


def spoken_word(word):
    return word.lower()


def voice_text(text):
    return (
        text.replace("Wordle", "wordle")
        .replace("Okay,", "Okay...")
        .replace("Hmm,", "Hmm...")
        .replace("Right,", "Right...")
        .replace("Oh,", "Oh...")
        .replace("Wait,", "Wait...")
    )


def voice_before_guess(guess, answer, turn, total_turns, rng, has_mistype=False, previous_guesses=None, mood="calm", used_strategy_letters=None):
    # Returns (text, beat). beat drives per-line prosody in make_voice_clip.
    if guess == answer:
        if turn == 1:
            return f"{spoken_word(answer)} first. Why not.", "locked_in"
        if turn == 2:
            return f"Could just be {spoken_word(answer)}.", "locked_in"
        if turn == total_turns and turn >= 5:
            return f"No more overthinking. {spoken_word(answer)}.", "locked_in"
        return f"I think it's {spoken_word(answer)}.", "locked_in"
    if turn == 1:
        word = spoken_word(guess)
        return rng.choice([f"{word.capitalize()} first.", f"Let's start {word}.", f"I'll open with {word}."]), "opener"
    if total_turns >= 6 and turn >= 5:
        return f"Running out of room. {spoken_word(guess)}.", "rushed"
    if has_mistype:
        return f"{rng.choice(['Wait', 'Actually', 'Hold on'])}... no. {spoken_word(guess)}.", "uncertain"
    strategy = strategy_line(guess, answer, previous_guesses or [], rng, used_strategy_letters)
    if strategy:
        return strategy, mood
    if mood == "rushed" and rng.random() < 0.45:
        return f"{spoken_word(guess)}.", "rushed"
    if mood == "uncertain" and rng.random() < 0.45:
        return f"Maybe {spoken_word(guess)}.", "uncertain"
    if rng.random() < 0.28:
        return f"{rng.choice(VOICE_HESITATIONS)}... {spoken_word(guess)}.", "uncertain"
    openers = ["Try", "Maybe", "What about", "Let's check"]
    return f"{rng.choice(openers)} {spoken_word(guess)}.", mood


def choose_reaction(category, rng, used_reactions):
    options = VOICE_REACTIONS[category]
    fresh = [option for option in options if option not in used_reactions]
    choice = rng.choice(fresh or options)
    used_reactions.add(choice)
    return choice


def spoken_reaction(category, rng, used_reactions):
    reaction = choose_reaction(category, rng, used_reactions)
    if reaction.lower().startswith(("hmm", "okay", "oh", "wait", "yeah")):
        return reaction[:1].upper() + reaction[1:]
    return f"{rng.choice(VOICE_FILLERS)}, {reaction}"


def voice_after_guess(guess, answer, turn, total_turns, rng, used_reactions=None):
    # Returns (text, beat). beat drives per-line prosody in make_voice_clip.
    used_reactions = used_reactions if used_reactions is not None else set()
    if guess == answer:
        if turn == 1:
            return f"No way. First try.", "triumphant"
        if turn == 2:
            return f"Second try. I'll take that.", "triumphant"
        if turn >= 6:
            return f"Finally. Last try.", "triumphant"
        if turn >= 5:
            return f"That was close. Got it.", "triumphant"
        return f"{rng.choice(['Yep', 'There we go', 'Nice'])}. Got it.", "triumphant"
    score = score_guess(guess, answer)
    greens = score.count("correct")
    yellows = score.count("present")
    filler = rng.choice(VOICE_FILLERS)
    if total_turns >= 6 and turn >= 5 and greens + yellows < 3:
        return f"{filler}, that's not enough. This is getting tight.", "tense"
    if greens >= 3 or greens + yellows >= 4:
        return spoken_reaction("close", rng, used_reactions), "excited"
    if greens >= 2 or greens + yellows >= 3:
        return spoken_reaction("good", rng, used_reactions), "good"
    if greens + yellows >= 1:
        return spoken_reaction("some", rng, used_reactions), "curious"
    return spoken_reaction("miss", rng, used_reactions), "flat"


def tile_color(state):
    return {"correct": CORRECT, "present": PRESENT, "absent": ABSENT}.get(state, BG)


def keyboard_colors(guesses, scores):
    priority = {"absent": 1, "present": 2, "correct": 3}
    colors = {}
    for guess, score in zip(guesses, scores):
        for ch, state in zip(guess, score):
            if priority[state] >= priority.get(colors.get(ch, ""), 0):
                colors[ch] = state
    return colors


def text_center(draw, xy, text, font_obj, fill):
    box = draw.textbbox((0, 0), text, font=font_obj)
    x, y, w, h = xy
    text_w = box[2] - box[0]
    text_h = box[3] - box[1]
    draw.text(
        (x + (w - text_w) / 2 - box[0], y + (h - text_h) / 2 - box[1]),
        text,
        font=font_obj,
        fill=fill,
    )


def draw_frame(
    guesses,
    answer,
    active_row,
    typed_letters,
    reveal_letters,
    title,
    subtitle,
    pressed_key=None,
    typed_word=None,
    blink=False,
):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((W / 2, 232), f"{title}   •   {subtitle}", font=F_TITLE, fill=MUTED, anchor="mm")

    tile = 118
    gap = 10
    grid_w = 5 * tile + 4 * gap
    start_x = (W - grid_w) // 2
    start_y = 340

    visible_guesses = guesses[: active_row + 1]
    scores = [score_guess(g, answer) for g in visible_guesses]
    key_states = keyboard_colors(visible_guesses[:active_row], scores[:active_row])

    for r in range(6):
        for c in range(5):
            x = start_x + c * (tile + gap)
            y = start_y + r * (tile + gap)
            fill = BG
            border = "#b8bec4" if r == active_row and active_row < len(guesses) else GRID_BORDER
            letter = ""
            if r < active_row:
                state = score_guess(guesses[r], answer)[c]
                fill = tile_color(state)
                border = fill
                letter = guesses[r][c]
            elif r == active_row and active_row < len(guesses):
                if c < reveal_letters:
                    state = score_guess(guesses[r], answer)[c]
                    fill = tile_color(state)
                    border = fill
                    letter = guesses[r][c]
                    key_states[letter] = state
                elif c < typed_letters:
                    active_word = typed_word or guesses[r]
                    letter = active_word[c]
                    border = "#878a8c"
            draw.rectangle((x, y, x + tile, y + tile), fill=fill, outline=border, width=4)
            if letter:
                text_center(draw, (x, y, tile, tile), letter, F_TILE, "#ffffff" if fill != BG else TEXT)
            elif blink and r == active_row and c == typed_letters and reveal_letters == 0:
                cursor_x = x + tile // 2
                draw.line((cursor_x, y + 30, cursor_x, y + tile - 30), fill="#878a8c", width=5)

    key_w = 90
    key_h = 88
    key_gap = 10
    key_row_gap = 10
    key_y = 1215
    for row_i, row in enumerate(KEY_ROWS):
        row_w = len(row) * key_w + (len(row) - 1) * key_gap
        x0 = (W - row_w) // 2
        if row_i == 1:
            x0 += 4
        if row_i == 2:
            x0 += 42
        for i, ch in enumerate(row):
            x = x0 + i * (key_w + key_gap)
            y = key_y + row_i * (key_h + key_row_gap)
            state = key_states.get(ch)
            fill = tile_color(state) if state else KEY_BG
            if pressed_key == ch:
                fill = "#b8bec4" if not state else fill
                inset = 4
            else:
                inset = 0
            draw.rectangle((x + inset, y + inset, x + key_w - inset, y + key_h - inset), fill=fill)
            text_center(draw, (x + inset, y + inset, key_w - inset * 2, key_h - inset * 2), ch, F_KEY, "#ffffff" if state else TEXT)

    draw.text((W / 2, 1680), "@thewordlestuff", font=F_HANDLE, fill="#111111", anchor="mm")
    return img


async def edge_voice(text, out_path, voice_index, beat="calm"):
    deps = ROOT / ".deps"
    if deps.exists():
        sys.path.insert(0, str(deps))
    import edge_tts

    voice = EDGE_VOICES[voice_index % len(EDGE_VOICES)]
    rate, pitch = EDGE_BEAT_PROSODY.get(beat, EDGE_BEAT_PROSODY["calm"])
    mp3 = out_path.with_suffix(".mp3")
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(mp3))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", str(out_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mp3.unlink(missing_ok=True)


def kokoro_voice(text, out_path, voice_index, beat="calm"):
    global KOKORO_PIPELINE
    dep_paths = [Path(p) for p in os.getenv("TTS_DEPS_DIR", "").split(os.pathsep) if p]
    dep_paths.append(ROOT / ".deps")
    for deps in dep_paths:
        if deps.exists() and str(deps) not in sys.path:
            sys.path.insert(0, str(deps))

    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    if KOKORO_PIPELINE is None:
        KOKORO_PIPELINE = KPipeline(lang_code="a")

    raw = out_path.with_suffix(".kokoro.wav")
    voice = KOKORO_VOICES[voice_index % len(KOKORO_VOICES)]
    speed = KOKORO_BEAT_SPEED.get(beat, 0.98)
    chunks = []
    generator = KOKORO_PIPELINE(voice_text(text), voice=voice, speed=speed, split_pattern=r"\n+")
    for _graphemes, _phonemes, audio in generator:
        chunks.append(np.asarray(audio, dtype=np.float32))
    if not chunks:
        raise RuntimeError("kokoro produced no audio")
    sf.write(str(raw), np.concatenate(chunks), 24000)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw), "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", str(out_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    raw.unlink(missing_ok=True)


def soften_clip(path):
    tmp = path.with_suffix(".soft.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            "afade=t=in:st=0:d=0.025,areverse,afade=t=in:st=0:d=0.035,areverse",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-acodec",
            "pcm_s16le",
            str(tmp),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    tmp.replace(path)


def make_voice_clip(text, out_path, voice_index, beat="calm"):
    # edge-tts is the primary voice (warmer/more natural here); kokoro is a fallback.
    # Set TTS_ENGINE=kokoro to prefer kokoro instead.
    engine = os.getenv("TTS_ENGINE", "edge").lower()
    order = ["kokoro", "edge"] if engine == "kokoro" else ["edge", "kokoro"]
    for eng in order:
        try:
            if eng == "edge":
                asyncio.run(edge_voice(text, out_path, voice_index, beat))
            else:
                kokoro_voice(text, out_path, voice_index, beat)
            soften_clip(out_path)
            return
        except Exception as exc:
            print(f"{eng} unavailable, falling back: {exc}")
    if shutil.which("say"):
        aiff = out_path.with_suffix(".aiff")
        mac_voices = ["Samantha", "Alex", "Ava", "Tom"]
        subprocess.run(["say", "-v", mac_voices[voice_index % len(mac_voices)], "-r", "178", "-o", str(aiff), text], check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(aiff), "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", str(out_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        aiff.unlink(missing_ok=True)
        soften_clip(out_path)
        return
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-s", "165", "-w", str(out_path), text], check=True)
        soften_clip(out_path)
        return
    with wave.open(str(out_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100)


def wav_duration(path):
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def write_audio_timeline(events, out_path, total_seconds):
    framerate = 44100
    sampwidth = 2
    total_samples = max(1, int(round((total_seconds + 0.25) * framerate)))
    mix = array("i", [0]) * total_samples

    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        for start, clip_path in events:
            start_sample = max(0, int(round(start * framerate)))
            with wave.open(str(clip_path), "rb") as clip:
                frames = array("h")
                frames.frombytes(clip.readframes(clip.getnframes()))
            for i, sample in enumerate(frames):
                pos = start_sample + i
                if pos >= total_samples:
                    break
                mix[pos] += sample
        clipped = array("h", (max(-32768, min(32767, sample)) for sample in mix))
        out.writeframes(clipped.tobytes())


def write_sfx(path, kind):
    framerate = 44100
    specs = {
        "key": (0.030, 880, 1150),
        "delete": (0.040, 420, 1200),
        "submit": (0.045, 260, 1000),
        "reveal": (0.050, 620, 900),
    }
    seconds, freq, amp = specs[kind]
    samples = []
    total = int(seconds * framerate)
    for i in range(total):
        t = i / framerate
        env = math.exp(-70 * t)
        wave_a = math.sin(2 * math.pi * freq * t)
        wave_b = 0.35 * math.sin(2 * math.pi * (freq * 1.8) * t)
        samples.append(int(amp * env * (wave_a + wave_b)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(framerate)
        out.writeframes(array("h", samples).tobytes())


def render_video(frames_dir, audio_path, total_frames):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frames_dir / "frame_%05d.png"),
            "-i",
            str(audio_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(VIDEO_OUT),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def mistype_plan(guesses, words, rng):
    pool = [w for w in words["valid"] + words["openers"] if len(w) == 5]
    mistakes = {}
    for row, guess in enumerate(guesses):
        is_final = row == len(guesses) - 1
        if is_final or row == 0 or rng.random() > 0.22:
            continue
        prefix_len = rng.choice([2, 3])
        options = [w for w in pool if w != guess and w[:prefix_len] != guess[:prefix_len]]
        if options:
            mistakes[row] = (rng.choice(options), prefix_len)
    return mistakes


def hesitation_plan(guesses, mistakes, rng):
    plan = {}
    for row, _guess in enumerate(guesses):
        if row in mistakes or row == len(guesses) - 1:
            continue
        if rng.random() < 0.34:
            plan[row] = {
                "at": rng.choice([2, 3, 4]),
                "delete": rng.random() < 0.45,
                "pause": rng.uniform(0.22, 0.58),
            }
    return plan


def main():
    OUT.mkdir(exist_ok=True)
    words = load_words()
    requested_date = puzzle_date()
    offset = (requested_date - START_DATE).days
    puzzle = fetch_wordle_puzzle(requested_date)
    answer = puzzle["answer"]
    title = f"Wordle #{puzzle['id']}"
    subtitle = pretty_date(datetime.strptime(puzzle["date"], "%Y-%m-%d").date())
    guesses = choose_guesses(answer, words)
    rng = random.Random(answer)
    profile = rng.choice(CHOREO_PROFILES)
    mood = rng.choice(MOODS)
    mistakes = mistype_plan(guesses, words, rng)
    hesitations = hesitation_plan(guesses, mistakes, rng)
    voice_lines = [f"{rng.choice(VOICE_OPENERS)} Wordle number {puzzle['id']}."]
    voice_beats = ["opener"]
    used_strategy_letters = set()
    used_reactions = set()
    for i, guess in enumerate(guesses, 1):
        before_text, before_beat = voice_before_guess(
            guess,
            answer,
            i,
            len(guesses),
            rng,
            i - 1 in mistakes,
            guesses[: i - 1],
            mood,
            used_strategy_letters,
        )
        voice_lines.append(before_text)
        voice_beats.append(before_beat)
        after_text, after_beat = voice_after_guess(guess, answer, i, len(guesses), rng, used_reactions)
        voice_lines.append(after_text)
        voice_beats.append(after_beat)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        frames_dir = tmp / "frames"
        clips_dir = tmp / "clips"
        sfx_dir = tmp / "sfx"
        frames_dir.mkdir()
        clips_dir.mkdir()
        sfx_dir.mkdir()
        audio = tmp / "voice.wav"
        sfx = {}
        for kind in ["key", "delete", "submit", "reveal"]:
            sfx[kind] = sfx_dir / f"{kind}.wav"
            write_sfx(sfx[kind], kind)
        clips = []
        clip_durations = []
        clip_lines = {}
        for i, line in enumerate(voice_lines):
            clip = clips_dir / f"voice_{i:02d}.wav"
            make_voice_clip(line, clip, puzzle["id"], voice_beats[i])
            clips.append(clip)
            clip_lines[clip] = line
            clip_durations.append(wav_duration(clip))
        frame_no = 0
        audio_events = []

        def add_sfx(kind, delay=0.0):
            audio_events.append((frame_no / FPS + delay, sfx[kind]))

        def apply_drift(img, local_frame):
            if os.getenv("ENABLE_CAMERA_DRIFT") != "1":
                return img
            drift_seed = random.Random(f"{answer}:drift")
            drift_phase_x = drift_seed.random() * math.tau
            drift_phase_y = drift_seed.random() * math.tau
            dx = int(round(1.6 * math.sin(local_frame / 53 + drift_phase_x)))
            dy = int(round(1.2 * math.sin(local_frame / 67 + drift_phase_y)))
            if dx == 0 and dy == 0:
                return img
            canvas = Image.new("RGB", (W, H), BG)
            canvas.paste(img, (dx, dy))
            return canvas

        def add_frames(seconds, active_row, typed_letters, reveal_letters, pressed_key=None, typed_word=None, cursor=False):
            nonlocal frame_no
            count = max(1, int(round(seconds * FPS)))
            for _ in range(count):
                blink = cursor
                img = draw_frame(
                    guesses,
                    answer,
                    active_row,
                    typed_letters,
                    reveal_letters,
                    title,
                    subtitle,
                    pressed_key,
                    typed_word,
                    blink,
                )
                img = apply_drift(img, frame_no)
                img.save(frames_dir / f"frame_{frame_no:05d}.png")
                frame_no += 1

        if os.getenv("DISABLE_FIRST_FRAME_COVER") != "1":
            cover_rng = random.Random(f"{answer}:cover")
            max_filled = max(1, len(guesses) - 1)
            cover_row = min(max_filled, cover_rng.choice([2, 3, 3, 4]))
            cover_typed = 0
            if cover_row < len(guesses) and cover_rng.random() < 0.35:
                cover_typed = cover_rng.choice([2, 3])
            cover = draw_frame(guesses, answer, cover_row, cover_typed, 0, title, subtitle)
            cover.save(frames_dir / f"frame_{frame_no:05d}.png")
            frame_no += 1

        voice_index = 0
        audio_events.append((0.12, clips[voice_index]))
        voice_index += 1
        add_frames(1.05, 0, 0, 0, cursor=True)
        for row, _guess in enumerate(guesses):
            audio_events.append((frame_no / FPS + 0.05, clips[voice_index]))
            voice_index += 1
            thinking = profile["pre"][0] + rng.random() * profile["pre"][1]
            if profile["name"] == "thoughtful" and rng.random() < 0.55:
                thinking += rng.uniform(0.35, 0.95)
            elif rng.random() < 0.22:
                thinking += rng.uniform(0.20, 0.55)
            add_frames(thinking, row, 0, 0, cursor=True)

            if row in mistakes:
                wrong_word, wrong_letters = mistakes[row]
                for letters in range(1, wrong_letters + 1):
                    add_sfx("key")
                    add_frames(profile["press"], row, letters, 0, pressed_key=wrong_word[letters - 1], typed_word=wrong_word)
                    add_frames(0.07 + rng.random() * 0.07, row, letters, 0, typed_word=wrong_word, cursor=True)
                add_frames(0.18 + rng.random() * 0.18, row, wrong_letters, 0, typed_word=wrong_word, cursor=True)
                for letters in range(wrong_letters - 1, -1, -1):
                    add_sfx("delete")
                    add_frames(0.08 + rng.random() * 0.04, row, letters, 0, typed_word=wrong_word, cursor=True)
                add_frames(0.22 + rng.random() * 0.18, row, 0, 0, cursor=True)

            hesitation = hesitations.get(row)
            for letters in range(1, 6):
                add_sfx("key")
                add_frames(profile["press"], row, letters, 0, pressed_key=_guess[letters - 1])
                if hesitation and letters == hesitation["at"]:
                    add_frames(0.14 + hesitation["pause"], row, letters, 0, cursor=True)
                    if hesitation["delete"]:
                        add_sfx("delete")
                        add_frames(0.09 + rng.random() * 0.04, row, letters - 1, 0, cursor=True)
                        add_frames(0.14 + rng.random() * 0.16, row, letters - 1, 0, cursor=True)
                        add_sfx("key")
                        add_frames(profile["press"], row, letters, 0, pressed_key=_guess[letters - 1])
                add_frames(profile["between"][0] + rng.random() * profile["between"][1], row, letters, 0, cursor=True)
            add_sfx("submit")
            add_frames(profile["submit"][0] + rng.random() * profile["submit"][1], row, 5, 0)
            for letters in range(1, 6):
                add_sfx("reveal")
                add_frames(profile["reveal"][0] + rng.random() * profile["reveal"][1], row, 5, letters)
            audio_events.append((frame_no / FPS + 0.10, clips[voice_index]))
            after_clip_duration = clip_durations[voice_index]
            voice_index += 1
            hold_base = profile["final_hold"] if _guess == answer else profile["hold"]
            final_bonus = rng.uniform(0.25, 0.75) if _guess == answer else 0
            hold_seconds = max(hold_base[0] + rng.random() * hold_base[1] + final_bonus, after_clip_duration + 0.32)
            add_frames(hold_seconds, row + 1, 0, 0)

        add_frames(1.4, len(guesses), 0, 0)
        write_audio_timeline(audio_events, audio, frame_no / FPS)
        audio_seconds = wav_duration(audio)
        while frame_no / FPS < audio_seconds + 0.2:
            add_frames(0.25, len(guesses), 0, 0)

        render_video(frames_dir, audio, frame_no)

    STORY_OUT.write_text(
        json.dumps(
            {
                "wordle_id": puzzle["id"],
                "date": puzzle["date"],
                "offset": offset,
                "answer": answer,
                "guesses": guesses,
                "solve_turn": len(guesses),
                "mistake_rows": sorted(row + 1 for row in mistakes),
                "hesitation_rows": sorted(row + 1 for row in hesitations),
                "choreography": profile["name"],
                "mood": mood,
                "voice": voice_lines,
                "voice_timing": [
                    {"at": round(start, 2), "line": clip_lines[_clip]}
                    for start, _clip in audio_events
                    if _clip in clip_lines
                ],
                "output": str(VIDEO_OUT),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(VIDEO_OUT)
    print(STORY_OUT)


if __name__ == "__main__":
    main()
