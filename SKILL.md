---
name: reel-recipe
description: Use when someone shares a reel, TikTok or Short they liked and wants its edit reverse engineered into a recipe (layout, captions, cards, timing, sound) they can rebuild with their own content.
---

# Reel recipe

Turn a short vertical video you liked into a recipe for its edit: where each layer sits, how the captions look, when the cards pop in, how long each shot lasts, what the sound does. Then adapt it to your own content.

The goal is the method, not a copy. You keep your face, your words and your topic; you borrow the structure.

## 1. Measure

Run the measuring script on the link (Instagram, TikTok, YouTube Shorts, X) or on a local file:

```bash
python3 <this skill>/scripts/measure_reel.py "<link or file>" --out reel-<name>
```

It takes 2 to 5 minutes for a 45 s video (most of it is the transcript). It downloads the video, transcribes it with word timings, runs HyperFrames' free local measuring commands (cuts, events, face, audio), and writes:

- `summary.md`: every measured number. Read it first.
- `sheets/layout-grid.png`: hook, middle and end frames with a line every 10%.
- `sheets/entrances.png`: consecutive frames around the first change of each place.
- `sheets/changes-*.png`: before/after pairs around each visual change, labelled with the word spoken.
- `sheets/overview-*.png`: one frame per second.

If the download fails (private post, login wall), ask the person to save the video and pass the file instead. Re-running on the same `--out` folder reuses the download, transcript and measurements.

## 2. Look

Open the sheets and name every layer: the speaker or main footage, the title, the captions, cards or pictures, b-roll or gameplay, stickers, progress bars, emoji. For each one:

- **Position and size** as a share of the frame, read on `layout-grid.png` (lines every 10%). A text's size is its cap height as a share of the frame height.
- **When it is on screen**: from the change table in `summary.md` (time, place, word spoken). A short gap (0.1 to 0.5 s) between two changes in the same place is usually one card leaving before the next one enters.
- **How it enters and leaves**, on `entrances.png` (one column per frame): visible in full on the first frame = hard cut; growing over 2 to 5 frames = pop or scale; moving in from an edge = slide; see-through first = fade.
- **Style**: font family (name the closest free font: Montserrat, Inter, Poppins, Anton, Bebas Neue...), weight, colour, outline, shadow or box, words per caption.

Need a closer look? HyperFrames' tools are free and local (`npx -y hyperframes@0.9.9 measure --help`):
- `measure frames <video> --range a,b --max 12` for consecutive frames of any moment
- `measure ink <video> --at t --roi x,y,w,h` for the exact pixel bounds of text on a plain background (on a busy background it catches everything, so use the grid instead)

## 3. Write the recipe

Fill `templates/recipe.md`. Rules:

- **Numbers come from the measurements.** Times, shot lengths, words per second, face size, loudness: copy them from `summary.md`. Positions, fonts and colours are read by eye: say so in the last section.
- **Explain why it works** in two or three lines (what makes people stop, stay, comment). Comments per view is a good signal for formats that ask for a reply.
- **Skip list**: never reuse their footage, voice, words, music or any third-party clip (gameplay, movie scenes, other creators' videos). Say what to use instead, or to drop it.
- **Your version**: if the person said what their video is about, map every beat to their content. If not, ask one question: what topic or series is this for?
- **Build steps** work in any editor (CapCut, Premiere, DaVinci, Descript): canvas, title, captions, cards, pace, sound, cover.

## 4. Hand over

Give the recipe in the chat (short), save it as `recipe.md` next to `summary.md`, and offer to write the script for their version.

## Limits

- The face box is sampled 12 times, so a quick punch-in between samples can be missed: check the changes sheet.
- Sound hits are found between words only; an SFX under the voice is not listed.
- Fonts are matched by eye.
- Videos you are not allowed to download stay out: use your own screen recording of the post if the platform allows it, or skip it.
