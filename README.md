# auto-video-editor

A command-line editor for talking-head video. Point it at a raw recording and it removes
the dead air, cleans up the audio, and cuts the takes you flubbed, so a one-take screen
recording comes out watchable without touching a timeline.

Python and FFmpeg, plus a small Remotion sub-project for the 3D transition.

## What it does

1. **Silence removal with a neural VAD.** It runs Silero VAD (a small voice-activity model)
   over the audio to find real speech, instead of FFmpeg's amplitude threshold. The
   difference matters: threshold-based cutters clip soft consonants and keep breaths;
   detecting *speech* keeps the words and drops the gaps.
2. **Audio cleanup.** EQ, compression, and normalization to about -16 LUFS (the loudness
   most platforms target), so the voice sits at a consistent level.
3. **Mistake removal.** Say "cut cut" during recording, pause, and redo the line. The editor
   detects that phrase and drops the bad take, so you never stop recording to fix a flub.
4. **A 3D swivel transition** (the Remotion piece) for an optional teaser at the top.

## Use

```bash
# full edit: silence removal + audio cleanup
python jump_cut_vad_parallel.py input.mp4 out.mp4 --enhance-audio

# also drop flubbed takes marked with "cut cut"
python jump_cut_vad_parallel.py input.mp4 out.mp4 --enhance-audio --detect-restarts

# add the 3D swivel teaser
python insert_3d_transition.py out.mp4 final.mp4 --bg-image bg.png
```

The `_parallel` variant splits the render across cores; `jump_cut_vad.py` is the simpler
single-process version.

## How the silence cut works

The audio is decoded to 16 kHz mono and fed through Silero VAD in small frames. The model
returns speech probabilities; a bit of hysteresis (a short pad before and after each speech
run, and a minimum-gap threshold) turns those into keep/cut segments, so it doesn't chatter
on every micro-pause. The kept segments are cut and concatenated losslessly where possible,
re-encoding only across cut boundaries. "cut cut" detection runs a quick match over the
transcript-adjacent audio to find restart markers and drops everything from the previous
sentence boundary up to the marker.

## Requirements

- Python 3.10+, `ffmpeg` on PATH
- `pip install torch silero-vad numpy` (VAD)
- Node + `npm install` inside `video_effects/` for the Remotion transition

## License

MIT.
