---
name: blind-judge
description: Blind A/B/N comparison page for any media (text, images, audio, video, PDF, HTML) so the user can rank or label candidate outputs without knowing which model, prompt, or strategy made each one. Use whenever the user or agent is comparing 2+ candidate outputs, choosing between models/prompts/strategies/settings, or wants a blind taste test. Triggers include "blind comparison", "blind judge", "blind test", "let me rank these", "shootout", "which one is better", "A/B these", "human or AI?".
---

# blind-judge

One stdlib-only CLI builds a local judging page from a JSON spec, serves it on 127.0.0.1, and turns the saves into an unblinded summary for you to read. The user drags whole cards into rank order (or labels each card), adds notes, saves, and only then sees which card was which.

Older task-specific copies of this pattern exist: `/img`'s `bench/judge.py` (images) and `elevate-humanizer/benchmarks/judge/` (text, rank + label). This skill supersedes both for new comparisons. Leave those in place; their own pipelines still use them.

## Workflow

1. **Generate the candidates** (whatever you are comparing). Aim for **2 samples per variant** so one lucky or unlucky output does not decide it, and include **last round's winner as a control** ("champion") when iterating.
2. **Write a spec** (format below). Put it next to the candidates or in your scratchpad.
3. **Build:** `python SKILL_DIR/blind_judge.py build spec.json` (or `uv run SKILL_DIR/blind_judge.py ...`). Prints the run dir.
4. **Serve in the background** (Bash `run_in_background`): `python SKILL_DIR/blind_judge.py serve RUN_DIR --no-open` then open the URL for the user, or drop `--no-open` to let it open the browser. Default port is the first free one from 8495; use a different `--port` per concurrent run.
5. **Tell the user the URL and what to judge.** Never say which card is which, what the variants are called, or which one you expect to win. Describing the question ("which headshot would you use on LinkedIn?") is fine.
6. **After they say they saved:** `python SKILL_DIR/blind_judge.py results RUN_DIR` (add `--json` for machine use). Report the unblinded findings: winner by score, 1st places, would-use counts, and the notes that explain the ranking. Say plainly when the result is a coin flip (1-2 sets, close scores).
7. Stop the server when done (the background task, or `taskkill //F //PID <pid> //T` on Windows).

`SKILL_DIR` is the folder this file lives in (`~/.claude/skills/blind-judge`).

## Spec format

```json
{
  "title": "Headshot prompt shootout, round 3",
  "rubric": "image",
  "seed": "r3",
  "marks": ["issue", "error"],
  "sets": [
    {
      "id": "linkedin",
      "title": "LinkedIn headshot",
      "question": "Which one would you actually put on LinkedIn?",
      "task": "rank",
      "original": "C:/Users/me/Pictures/ref.png",
      "cards": [
        {"label": "plain prompt", "path": "gen/plain-1.png", "note": "12s, $0.04"},
        {"label": "plain prompt", "path": "gen/plain-2.png"},
        {"label": "champion (r2 winner)", "path": "gen/bestday-1.png"},
        {"label": "champion (r2 winner)", "path": "gen/bestday-2.png"}
      ]
    },
    {
      "id": "bio",
      "title": "About-page bio",
      "prompt": "Rank from most like something you'd write to least.",
      "original": "Source text the rewrites started from.\nLine breaks are kept.",
      "cards": [
        {"label": "sonnet", "text": "I build **data tools** for small teams."},
        {"label": "opus", "path": "out/opus.md"},
        {"label": "opus", "path": "out/opus-2.md"}
      ]
    },
    {
      "id": "turing",
      "title": "Human or AI?",
      "task": "label",
      "options": ["human", "AI"],
      "cards": [
        {"label": "human", "path": "samples/real.txt"},
        {"label": "gpt", "path": "samples/gen.txt", "answer": "AI"}
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `title` | Page heading; also names the default run dir. |
| `rubric` | Preset `"image"`, `"text"` or `"generic"`, or a list `[{"name", "text"}]` (+ optional `rubric_lead`). Omitted: picked from the card types. |
| `seed` | Optional. Card order is a seeded shuffle per set id, stable across reloads and rebuilds; change the seed to reshuffle. |
| `marks` | Highlight kinds for text cards (select a passage to mark it). Default `["issue", "error"]`; `[]` turns marking off. |
| set `id` | Unique, letters/digits/`_.-`. Card keys are `<id>-A`, `<id>-B`, ... |
| set `question` / `prompt` | Shown under the title (either name works). `tests` adds a small accent line. |
| set `task` | `"rank"` (default): drag to order, no ties. `"label"`: pick one of `options` per card (default `["human", "AI"]`) plus a confidence. |
| set `original` | Optional reference: a file path (any media type) or inline text. Images get hold-O-to-compare in the lightbox. |
| card `label` | The hidden identity. **Several cards may share a label**; results aggregate by label. |
| card `path` / `text` | Media file (relative to the spec file, or absolute) or inline text. |
| card `note` | Optional extra shown next to the label after save (timing, cost, settings). |
| card `answer` | Label tasks only: the correct option. If omitted and the label equals an option (case-insensitive), that is the answer. |
| card `kind` / `format` | Overrides: `kind` when the extension is unusual; `format: "plain"` turns off light markdown for text. |
| card `error` | A card with no output (model refused/failed): renders as a placeholder and still gets ranked. |

Media type comes from the extension: png/jpg/jpeg/webp/gif/avif/svg = image (compact grid, ~40vh cap, click to zoom); md/txt/inline = text (readable serif, line breaks kept, light markdown: headings, bold, italic, code, links shown as text; `.txt` is plain); mp3/wav/m4a/ogg/flac/aac/opus = audio player; mp4/webm/mov/m4v = video; pdf and html = iframe (HTML is sandboxed with scripts allowed; relative assets next to the file are not copied, so inline them). Every card has an "expand" view; arrow keys flip between cards there.

JSON gotcha: Windows paths need forward slashes or doubled backslashes.

## Runs and blinding

- **Default run dir:** `~/Pictures/blind-judge/<YYYY-MM-DD>-<title-slug>/` (override the root with `BLIND_JUDGE_HOME`, or pass `-o RUN_DIR`). Runs sit outside git on purpose: they can hold personal photos and text. `build` refuses to overwrite a dir that already has `judgments.jsonl`; the default path gets a `-2`, `-3` suffix instead.
- Run dir layout: `judge.html`, `media/<opaque hash>.<ext>` (hardlinked or copied), `_private/manifest.json` + `spec.json` (labels, sources), `judgments.jsonl` (one line per save, unblinded).
- **The page never contains labels or source filenames.** The server hands a set's labels back only in the response to that set's save, and never serves `_private/` or `judgments.jsonl`. The results tab stays locked until every set is saved.
- Saves persist in the browser (localStorage) and on the server; reloading or opening in another browser restores the order, notes and reveals. `results` reads the **latest save per set**, so re-saving a set replaces its earlier answer.

## Results

`results` prints, per label: **score** (100 = always ranked first, 0 = always last; evens out sets of different sizes), average rank, 1st places, would-use and missed-brief counts; for label tasks, the picks per true label and how many were right. Then each set in rank order with the user's notes, marked passages and the overall note. The page's Results tab shows the same numbers.

## Page behaviour worth knowing

- Drag a whole card to rank it (press on the image or the header bar; text bodies, players and form controls do not start drags, so text can be selected for marking). The badge reads `unranked` until the user actually reorders or fills every rank menu; a slipped or cancelled drag never fills in ranks. The rank menus stay in sync: picking a rank for one card moves it to that slot and leaves the rest unranked.
- "I'd use this" and "Missed the brief" checkboxes, a note per card, and an overall note per set.
- Rubric panel pinned to the bottom (collapsible, collapsed by default on phones). Works offline apart from optional Google Fonts.

## Best practices

- Two samples per variant; a single sample mostly measures luck.
- Keep the champion from the previous round in the next one, so "better" means better than what you already have.
- 3-6 cards per set. More than that and ranking the middle turns into noise; split into more sets instead.
- Don't reveal or hint at identities before the user saves, including in the spec's visible `title`/`question` fields.
- **Don't POST to a run the user is judging** while testing; build a separate throwaway run (`-o` in your scratchpad) and serve it on another port.
- One port per concurrent run (the default auto-picks the next free port from 8495).
