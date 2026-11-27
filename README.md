# reel-recipe

An agent skill that reverse engineers the edit of a reel, TikTok or Short you liked, and gives you the recipe to rebuild it with your own content.

You send a link. It downloads the video, transcribes it, measures every cut and overlay, checks the frames, and writes down where the title sits, the caption style, when the cards pop in, how long each shot lasts, what the sound does, and what not to copy.

Shared design skills give everyone the same look. This one gives you the structure of whatever stops *you* on your feed.

## Install

Works with any agent that reads `SKILL.md` files (Claude Code, Codex, Hermes, OpenCode...).

```bash
git clone https://github.com/Hugo-SEQUIER/reel-recipe
# Claude Code: put it where your skills live
cp -r reel-recipe ~/.claude/skills/reel-recipe
pip install -r reel-recipe/requirements.txt
```

Also needed:
- **ffmpeg** (with ffprobe)
- **yt-dlp** to download links (`pip install yt-dlp`, or have `uvx`)
- **Node 18+** for HyperFrames' measuring commands (run through `npx`, free, local, no account)

## Use

Ask your agent:

> Reverse engineer the edit of this reel: https://www.instagram.com/p/...

Or run the measuring step yourself:

```bash
python3 scripts/measure_reel.py "https://www.instagram.com/p/..." --out reel-quiz
```

Then open `reel-quiz/summary.md` and the pictures in `reel-quiz/sheets/`.

## What you get

`summary.md`, measured:
- size, frame rate, length
- where the face sits and whether the framing ever changes
- every visual change with its time, its place in the frame, the word spoken at that moment, and whether it is a camera cut or an overlay (card, text, picture)
- regions that move all the time (gameplay, b-roll)
- words per second and the first 3 seconds spoken
- music under the voice or not, loudness, sound hits between words
- the full transcript with times

Pictures: frames with a 10% grid to read positions, consecutive frames around each entrance (hard cut, pop, slide or fade), before/after pairs for every change, one frame per second.

Then the agent writes `recipe.md`: timeline, layout table, captions, cards, pace, sound, a skip list, your version beat by beat, and step-by-step build instructions for any editor.

## Copy the method, not the video

The skill never reuses someone's footage, voice, words or music, and it flags third-party clips (gameplay, movie scenes) in the skip list. Download only what the platform and the creator allow.

## Credits

Measuring commands by [HyperFrames](https://github.com/heygen-com/hyperframes) (Apache-2.0), transcripts by [faster-whisper](https://github.com/SYSTRAN/faster-whisper), downloads by [yt-dlp](https://github.com/yt-dlp/yt-dlp).

MIT license.
