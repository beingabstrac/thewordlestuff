#!/usr/bin/env python3
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
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

VOICE_OPENERS = ["Let's solve this one.", "Okay, new puzzle.", "Let's see if we can get it."]

VOICE_REACTIONS = {
    "miss": ["That did not help much.", "Rough start, but we learned what is not in it.", "Okay, mostly clearing letters."],
    "some": ["Okay, that gives us something.", "Not bad, we have a clue.", "That helped a little."],
    "good": ["Wait, that is actually useful.", "Now we are getting close.", "That narrowed it down a lot."],
    "close": ["That is really close.", "I think I see it now.", "One letter away kind of feeling."],
    "final": ["I think this is it.", "This has to be it.", "Final answer."],
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


F_TITLE = font(78, True)
F_SUB = font(36, False)
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
        return f"{rng.choice(VOICE_REACTIONS['final'])} {answer}. That is the word."
    score = score_guess(guess, answer)
    greens = score.count("correct")
    yellows = score.count("present")
    if turn == 1:
        return f"Starting with {guess}."
    if greens >= 3 or greens + yellows >= 4:
        return rng.choice(VOICE_REACTIONS["close"])
    if greens >= 2 or greens + yellows >= 3:
        return rng.choice(VOICE_REACTIONS["good"])
    if greens + yellows >= 1:
        return rng.choice(VOICE_REACTIONS["some"])
    return rng.choice(VOICE_REACTIONS["miss"])


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
    draw.text((x + (w - box[2]) / 2, y + (h - box[3]) / 2 - 6), text, font=font_obj, fill=fill)


def draw_frame(guesses, answer, reveal_row, reveal_letters, title, subtitle):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((W / 2, 82), title, font=F_TITLE, fill=TEXT, anchor="mm")
    draw.line((92, 144, W - 92, 144), fill="#e1e4e8", width=2)
    draw.text((W / 2, 188), subtitle, font=F_SUB, fill=MUTED, anchor="mm")

    tile = 118
    gap = 10
    grid_w = 5 * tile + 4 * gap
    start_x = (W - grid_w) // 2
    start_y = 255

    visible_guesses = guesses[: reveal_row + 1]
    scores = [score_guess(g, answer) for g in visible_guesses]
    key_states = keyboard_colors(visible_guesses[:reveal_row], scores[:reveal_row])

    for r in range(6):
        for c in range(5):
            x = start_x + c * (tile + gap)
            y = start_y + r * (tile + gap)
            fill = BG
            border = GRID_BORDER
            letter = ""
            if r < reveal_row:
                state = score_guess(guesses[r], answer)[c]
                fill = tile_color(state)
                border = fill
                letter = guesses[r][c]
            elif r == reveal_row and reveal_row < len(guesses) and c < reveal_letters:
                state = score_guess(guesses[r], answer)[c]
                fill = tile_color(state)
                border = fill
                letter = guesses[r][c]
                key_states[letter] = state
            elif r == reveal_row and reveal_row < len(guesses):
                letter = guesses[r][c]
                border = "#878a8c"
            draw.rectangle((x, y, x + tile, y + tile), fill=fill, outline=border, width=4)
            if letter:
                text_center(draw, (x, y, tile, tile), letter, F_TILE, "#ffffff" if fill != BG else TEXT)

    key_w = 90
    key_h = 88
    key_gap = 10
    key_y = 1128
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
            draw.rounded_rectangle((x, y, x + key_w, y + key_h), radius=8, fill=fill)
            text_center(draw, (x, y, key_w, key_h), ch, F_KEY, "#ffffff" if state else TEXT)

    draw.text((W / 2, 1598), "@thewordlestuff", font=F_HANDLE, fill="#111111", anchor="mm")
    return img


def make_voice(lines, out_path):
    text = " ".join(lines)
    if shutil.which("say"):
        aiff = out_path.with_suffix(".aiff")
        subprocess.run(["say", "-v", "Samantha", "-r", "185", "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(aiff), "-ar", "44100", str(out_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            "-shortest",
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
        frames_dir.mkdir()
        audio = tmp / "voice.wav"
        make_voice(voice, audio)
        audio_seconds = max(wav_duration(audio), len(guesses) * 2.4 + 2.0)
        total_frames = int(math.ceil(audio_seconds * FPS))
        frames_per_guess = max(42, total_frames // (len(guesses) + 1))
        frame_no = 0

        for row, _guess in enumerate(guesses):
            for hold in range(18):
                img = draw_frame(guesses, answer, row, 0, title, subtitle)
                img.save(frames_dir / f"frame_{frame_no:05d}.png")
                frame_no += 1
            for letters in range(1, 6):
                for _ in range(9):
                    img = draw_frame(guesses, answer, row, letters, title, subtitle)
                    img.save(frames_dir / f"frame_{frame_no:05d}.png")
                    frame_no += 1
            while frame_no < (row + 1) * frames_per_guess:
                img = draw_frame(guesses, answer, row + 1, 0, title, subtitle)
                img.save(frames_dir / f"frame_{frame_no:05d}.png")
                frame_no += 1

        while frame_no < total_frames:
            img = draw_frame(guesses, answer, len(guesses), 0, title, "Did you get it before the reveal?")
            img.save(frames_dir / f"frame_{frame_no:05d}.png")
            frame_no += 1

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
