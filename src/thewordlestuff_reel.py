#!/usr/bin/env python3
import asyncio
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
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
    "en-US-AndrewMultilingualNeural",
    "en-US-AvaMultilingualNeural",
    "en-US-BrianMultilingualNeural",
    "en-US-EmmaMultilingualNeural",
]

VOICE_OPENERS = [
    "Okay, let's play this one out.",
    "Alright, let's see where this goes.",
    "Okay, new one. Let's work through it.",
]

VOICE_REACTIONS = {
    "miss": ["Hmm, not much there.", "Okay, mostly a cleanup guess.", "That clears a few letters at least."],
    "some": ["Okay, we got something.", "That gives us a little direction.", "Not amazing, but it helps."],
    "good": ["Wait, that's actually useful.", "Now we're getting somewhere.", "That narrows it down a lot."],
    "close": ["Oh, that's close.", "I think I can see it now.", "That feels one move away."],
    "final": ["Yeah, this should be it.", "I think this is the one.", "This has to be it."],
}


def load_words():
    return json.loads(WORDS_FILE.read_text(encoding="utf-8"))


def font(size, bold=False):
    candidates = [
        "/Library/Fonts/SF-Pro-Display-Bold.otf" if bold else "/Library/Fonts/SF-Pro-Display-Regular.otf",
        "/Library/Fonts/SF-Pro-Text-Bold.otf" if bold else "/Library/Fonts/SF-Pro-Text-Regular.otf",
        "/usr/share/fonts/truetype/inter/Inter-Bold.ttf" if bold else "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
    target_len = rng.choice([4, 4, 5, 5, 6])
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
        pick_zone = candidates[max(0, len(candidates) // 2 - 8):] or candidates
        guess = rng.choice(pick_zone)
        guesses.append(guess)
        scores.append(score_guess(guess, answer))

    guesses.append(answer)
    return guesses[:6]


def voice_for_guess(guess, answer, turn, rng):
    if guess == answer:
        return f"{rng.choice(VOICE_REACTIONS['final'])} {answer}. There it is."
    score = score_guess(guess, answer)
    greens = score.count("correct")
    yellows = score.count("present")
    if turn == 1:
        return f"I'll start with {guess}. {rng.choice(VOICE_REACTIONS['some'] if greens + yellows else VOICE_REACTIONS['miss'])}"
    if greens >= 3 or greens + yellows >= 4:
        return f"Let's try {guess}. {rng.choice(VOICE_REACTIONS['close'])}"
    if greens >= 2 or greens + yellows >= 3:
        return f"Maybe {guess}. {rng.choice(VOICE_REACTIONS['good'])}"
    if greens + yellows >= 1:
        return f"I'll test {guess}. {rng.choice(VOICE_REACTIONS['some'])}"
    return f"Trying {guess}. {rng.choice(VOICE_REACTIONS['miss'])}"


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


def draw_frame(guesses, answer, active_row, typed_letters, reveal_letters, title, subtitle, pressed_key=None):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((W / 2, 178), f"{title}   •   {subtitle}", font=F_TITLE, fill=MUTED, anchor="mm")

    tile = 118
    gap = 10
    grid_w = 5 * tile + 4 * gap
    start_x = (W - grid_w) // 2
    start_y = 285

    visible_guesses = guesses[: active_row + 1]
    scores = [score_guess(g, answer) for g in visible_guesses]
    key_states = keyboard_colors(visible_guesses[:active_row], scores[:active_row])

    for r in range(6):
        for c in range(5):
            x = start_x + c * (tile + gap)
            y = start_y + r * (tile + gap)
            fill = BG
            border = GRID_BORDER
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
                    letter = guesses[r][c]
                    border = "#878a8c"
            draw.rectangle((x, y, x + tile, y + tile), fill=fill, outline=border, width=4)
            if letter:
                text_center(draw, (x, y, tile, tile), letter, F_TILE, "#ffffff" if fill != BG else TEXT)

    key_w = 90
    key_h = 88
    key_gap = 10
    key_y = 1160
    for row_i, row in enumerate(KEY_ROWS):
        row_w = len(row) * key_w + (len(row) - 1) * key_gap
        x0 = (W - row_w) // 2
        if row_i == 1:
            x0 += 4
        if row_i == 2:
            x0 += 42
        for i, ch in enumerate(row):
            x = x0 + i * (key_w + key_gap)
            y = key_y + row_i * (key_h + 18)
            state = key_states.get(ch)
            fill = tile_color(state) if state else KEY_BG
            if pressed_key == ch:
                fill = "#b8bec4" if not state else fill
            draw.rectangle((x, y, x + key_w, y + key_h), fill=fill)
            text_center(draw, (x, y, key_w, key_h), ch, F_KEY, "#ffffff" if state else TEXT)

    draw.text((W / 2, 1625), "@thewordlestuff", font=F_HANDLE, fill="#111111", anchor="mm")
    return img


async def edge_voice(text, out_path, voice_index):
    deps = ROOT / ".deps"
    if deps.exists():
        sys.path.insert(0, str(deps))
    import edge_tts

    voice = EDGE_VOICES[voice_index % len(EDGE_VOICES)]
    mp3 = out_path.with_suffix(".mp3")
    communicate = edge_tts.Communicate(text, voice=voice, rate="+8%", pitch="+0Hz")
    await communicate.save(str(mp3))
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", str(out_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mp3.unlink(missing_ok=True)


def make_voice_clip(text, out_path, voice_index):
    try:
        asyncio.run(edge_voice(text, out_path, voice_index))
        return
    except Exception as exc:
        print(f"edge-tts unavailable, falling back to system voice: {exc}")
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
        return
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-s", "165", "-w", str(out_path), text], check=True)
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
    cursor = 0.0

    def silence(seconds):
        return b"\x00" * max(0, int(round(seconds * framerate)) * sampwidth)

    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        for start, clip_path in events:
            if start > cursor:
                out.writeframes(silence(start - cursor))
                cursor = start
            with wave.open(str(clip_path), "rb") as clip:
                out.writeframes(clip.readframes(clip.getnframes()))
                cursor += clip.getnframes() / clip.getframerate()
        if total_seconds > cursor:
            out.writeframes(silence(total_seconds - cursor))


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


def main():
    OUT.mkdir(exist_ok=True)
    words = load_words()
    requested_date = puzzle_date()
    offset = (requested_date - START_DATE).days
    puzzle = fetch_wordle_puzzle(requested_date)
    answer = puzzle["answer"]
    title = f"WORDLE #{puzzle['id']}"
    subtitle = pretty_date(datetime.strptime(puzzle["date"], "%Y-%m-%d").date())
    guesses = choose_guesses(answer, words)
    rng = random.Random(answer)
    voice = [f"{rng.choice(VOICE_OPENERS)} Wordle number {puzzle['id']}."]
    for i, guess in enumerate(guesses, 1):
        voice.append(voice_for_guess(guess, answer, i, rng))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        frames_dir = tmp / "frames"
        clips_dir = tmp / "clips"
        frames_dir.mkdir()
        clips_dir.mkdir()
        audio = tmp / "voice.wav"
        clips = []
        for i, line in enumerate(voice):
            clip = clips_dir / f"voice_{i:02d}.wav"
            make_voice_clip(line, clip, puzzle["id"])
            clips.append(clip)
        frame_no = 0
        audio_events = []

        def add_frames(seconds, active_row, typed_letters, reveal_letters, card_subtitle=None, pressed_key=None):
            nonlocal frame_no
            count = max(1, int(round(seconds * FPS)))
            for _ in range(count):
                img = draw_frame(
                    guesses,
                    answer,
                    active_row,
                    typed_letters,
                    reveal_letters,
                    title,
                    card_subtitle or subtitle,
                    pressed_key,
                )
                img.save(frames_dir / f"frame_{frame_no:05d}.png")
                frame_no += 1

        audio_events.append((0.12, clips[0]))
        add_frames(1.05, 0, 0, 0)
        for row, _guess in enumerate(guesses):
            audio_events.append((frame_no / FPS + 0.05, clips[row + 1]))
            add_frames(0.25 + rng.random() * 0.16, row, 0, 0)
            for letters in range(1, 6):
                add_frames(0.08, row, letters, 0, pressed_key=_guess[letters - 1])
                add_frames(0.08 + rng.random() * 0.05, row, letters, 0)
            add_frames(0.38 + rng.random() * 0.15, row, 5, 0)
            for letters in range(1, 6):
                add_frames(0.24 + rng.random() * 0.08, row, 5, letters)
            add_frames((1.65 if _guess == answer else 0.9) + rng.random() * 0.35, row + 1, 0, 0)

        add_frames(1.4, len(guesses), 0, 0, "Did you get it before the reveal?")
        write_audio_timeline(audio_events, audio, frame_no / FPS)
        audio_seconds = wav_duration(audio)
        while frame_no / FPS < audio_seconds + 0.2:
            add_frames(0.25, len(guesses), 0, 0, "Did you get it before the reveal?")

        render_video(frames_dir, audio, frame_no)

    STORY_OUT.write_text(
        json.dumps(
            {
                "wordle_id": puzzle["id"],
                "date": puzzle["date"],
                "offset": offset,
                "answer": answer,
                "guesses": guesses,
                "voice": voice,
                "voice_timing": [{"at": round(start, 2), "line": voice[i]} for i, (start, _clip) in enumerate(audio_events)],
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
