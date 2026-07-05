# thewordlestuff

Free MVP renderer for 5-letter Wordle-style guessing reels.

Default puzzle starts at June 19, 2021.

Run:

```bash
python3 -m pip install -r requirements.txt
./scripts/render.sh
```

Output:

```text
outputs/thewordlestuff_mvp.mp4
```

Render a specific date:

```bash
WORDLE_DATE=2021-06-19 ./scripts/render.sh
```

Render old-to-new:

```bash
./scripts/render.sh
python3 scripts/advance_progress.py
```
