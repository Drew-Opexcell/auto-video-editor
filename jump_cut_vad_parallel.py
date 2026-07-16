#!/usr/bin/env python3
"""Parallel version of jump_cut_vad.py."""

import subprocess
import tempfile
import os
import argparse
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# winget installs ffmpeg without always putting it on PATH
if os.name == "nt":
    _winget_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for _p in _winget_dir.glob("Gyan.FFmpeg*/ffmpeg-*-full_build/bin"):
        if (_p / "ffmpeg.exe").exists() and str(_p) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(_p) + os.pathsep + os.environ["PATH"]
            break

MIN_SILENCE_DURATION = 0.5
PADDING_MS = 50
MIN_SPEECH_DURATION = 0.25
RESTART_PHRASE = "cut cut"
RESTART_LOOKBACK = 10.0

PARALLEL_WORKERS = 4

SMART_RENDER_THRESHOLD = 5.0
SMART_RENDER_EDGE = 1.0

AUDIO_FILTERS = {
    "highpass": "highpass=f=80",
    "lowpass": "lowpass=f=12000",
    "presence": "equalizer=f=3000:t=q:w=1.5:g=2",
    "warmth": "equalizer=f=200:t=q:w=1:g=-1",
    "compression": "acompressor=threshold=-20dB:ratio=3:attack=5:release=50",
    "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
}

SUPPORTED_LUT_FORMATS = [".cube", ".3dl", ".dat", ".m3d", ".csp"]

HARDWARE_ENCODER = "h264_videotoolbox"
SOFTWARE_ENCODER = "libx264"
HARDWARE_BITRATE = "10M"  # videotoolbox has no CRF
SOFTWARE_CRF = "18"


def check_hardware_encoder_available():
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5
        )
        return "h264_videotoolbox" in result.stdout
    except Exception:
        return False


def get_video_encoder_args(use_hardware=True):
    if use_hardware and check_hardware_encoder_available():
        return ["-c:v", HARDWARE_ENCODER, "-b:v", HARDWARE_BITRATE]
    return ["-c:v", SOFTWARE_ENCODER, "-preset", "fast", "-crf", SOFTWARE_CRF]


_hardware_encoder_available = None
_keyframe_cache = {}


def get_keyframes(input_path):
    if input_path in _keyframe_cache:
        return _keyframe_cache[input_path]

    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags",
        "-of", "json",
        input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    keyframes = []
    try:
        data = json.loads(result.stdout)
        for packet in data.get("packets", []):
            if "K" in packet.get("flags", ""):
                pts = packet.get("pts_time")
                if pts is not None:
                    keyframes.append(float(pts))
    except (json.JSONDecodeError, KeyError):
        pass

    keyframes.sort()
    _keyframe_cache[input_path] = keyframes
    return keyframes


def find_keyframe_at_or_after(keyframes: list[float], target: float) -> float | None:
    for kf in keyframes:
        if kf >= target - 0.001:  # float tolerance
            return kf
    return None


def find_keyframe_at_or_before(keyframes: list[float], target: float) -> float | None:
    result = None
    for kf in keyframes:
        if kf <= target + 0.001:
            result = kf
        else:
            break
    return result


def get_cached_encoder_args():
    global _hardware_encoder_available
    if _hardware_encoder_available is None:
        _hardware_encoder_available = check_hardware_encoder_available()
        if _hardware_encoder_available:
            print("Hardware encoding enabled (h264_videotoolbox)")
        else:
            print("Using software encoding (libx264)")
    return get_video_encoder_args(_hardware_encoder_available)


def extract_audio(input_path, output_path, sample_rate=16000):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-ar", str(sample_rate), "-ac", "1",
        "-acodec", "pcm_s16le",
        "-loglevel", "error", output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def transcribe_with_whisper(audio_path, model_name="base"):
    import whisper

    print(f"Transcribing with Whisper ({model_name})...")
    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path, word_timestamps=True)

    words = []
    for segment in result.get("segments", []):
        for word_info in segment.get("words", []):
            words.append({
                "word": word_info["word"].strip().lower(),
                "start": word_info["start"],
                "end": word_info["end"]
            })

    print(f"   Transcribed {len(words)} words")
    return words


def find_restart_phrases(words: list[dict], phrase: str = "cut cut") -> list[float]:
    phrase_words = phrase.lower().split()
    phrase_len = len(phrase_words)

    restart_times = []

    for i in range(len(words) - phrase_len + 1):
        match = True
        for j, target_word in enumerate(phrase_words):
            actual_word = words[i + j]["word"].strip().lower()
            actual_word = ''.join(c for c in actual_word if c.isalnum())
            if actual_word != target_word:
                match = False
                break

        if match:
            phrase_end = words[i + phrase_len - 1]["end"]
            restart_times.append(phrase_end)
            print(f"   Found '{phrase}' at {phrase_end:.2f}s")

    return restart_times


def find_previous_checkpoint(restart_time: float, speech_segments: list[tuple[float, float]],
                             lookback: float = 10.0) -> float:
    for i, (seg_start, seg_end) in enumerate(speech_segments):
        if seg_start <= restart_time <= seg_end:
            if i > 0:
                return speech_segments[i - 1][1]
            else:
                return seg_start

    for i in range(len(speech_segments) - 1, -1, -1):
        if speech_segments[i][0] < restart_time:
            return speech_segments[i][0]

    return 0.0


def apply_restart_cuts(speech_segments: list[tuple[float, float]],
                       restart_times: list[float],
                       lookback: float = 10.0) -> list[tuple[float, float]]:
    if not restart_times:
        return speech_segments

    restart_times = sorted(restart_times)
    segments_to_remove = set()

    for restart_time in restart_times:
        for i, (seg_start, seg_end) in enumerate(speech_segments):
            if seg_start <= restart_time <= seg_end:
                segments_to_remove.add(i)
                print(f"   Removing segment {i} ({seg_start:.2f}s - {seg_end:.2f}s) - contains restart phrase")

                if i > 0:
                    prev_start, prev_end = speech_segments[i - 1]
                    segments_to_remove.add(i - 1)
                    print(f"   Removing segment {i-1} ({prev_start:.2f}s - {prev_end:.2f}s) - mistake before restart")
                break

    return [seg for i, seg in enumerate(speech_segments) if i not in segments_to_remove]


def get_speech_timestamps_silero(audio_path, min_speech_duration=0.25, min_silence_duration=0.5):
    import torch

    model, utils = torch.hub.load(
        repo_or_dir='snakers4/silero-vad',
        model='silero_vad',
        force_reload=False,
        trust_repo=True
    )

    (get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils

    SAMPLE_RATE = 16000
    wav = read_audio(audio_path, sampling_rate=SAMPLE_RATE)

    speech_timestamps = get_speech_timestamps(
        wav,
        model,
        sampling_rate=SAMPLE_RATE,
        threshold=0.5,
        min_speech_duration_ms=int(min_speech_duration * 1000),
        min_silence_duration_ms=int(min_silence_duration * 1000),
        speech_pad_ms=100,
    )

    return [(ts['start'] / SAMPLE_RATE, ts['end'] / SAMPLE_RATE) for ts in speech_timestamps]


def merge_close_segments(segments: list[tuple[float, float]], max_gap: float) -> list[tuple[float, float]]:
    if not segments:
        return []

    merged = [segments[0]]
    for start, end in segments[1:]:
        prev_start, prev_end = merged[-1]

        if start - prev_end <= max_gap:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))

    return merged


def add_padding(segments: list[tuple[float, float]], padding_s: float, duration: float) -> list[tuple[float, float]]:
    if not segments:
        return []

    padded = [(max(0, start - padding_s), min(duration, end + padding_s)) for start, end in segments]

    merged = [padded[0]]
    for start, end in padded[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def build_audio_filter_chain(enhance=False):
    if not enhance:
        return ""

    filters = [
        AUDIO_FILTERS["highpass"],
        AUDIO_FILTERS["lowpass"],
        AUDIO_FILTERS["warmth"],
        AUDIO_FILTERS["presence"],
        AUDIO_FILTERS["compression"],
        AUDIO_FILTERS["loudnorm"],
    ]
    return ",".join(filters)


def build_video_filter_chain(lut_path=None):
    if lut_path:
        return f"lut3d='{lut_path}':interp=trilinear"
    return ""


def encode_single_segment(args):
    idx, input_path, start, end, seg_path, encoder_args, audio_filter, video_filter, smart_render, keyframes = args

    duration = end - start

    if smart_render and duration > SMART_RENDER_THRESHOLD and not video_filter:
        return encode_segment_smart(idx, input_path, start, end, seg_path, encoder_args, audio_filter, keyframes)

    # -ss after -i for frame-accurate seeking, -to for an exact end point
    end_time = start + duration
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", str(start),
        "-to", str(end_time),
    ]

    if video_filter:
        cmd.extend(["-vf", video_filter])

    cmd.extend(encoder_args)

    if audio_filter:
        cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.extend(["-avoid_negative_ts", "make_zero", "-loglevel", "error", seg_path])
    subprocess.run(cmd, capture_output=True)

    return (idx, seg_path)


def encode_segment_smart(idx: int, input_path: str, start: float, end: float,
                          seg_path: str, encoder_args: list[str], audio_filter: str,
                          keyframes: list[float] = None) -> tuple[int, str]:
    """Re-encode only the edges of a long segment and stream-copy the middle."""
    duration = end - start
    edge = SMART_RENDER_EDGE

    if keyframes is None:
        keyframes = get_keyframes(input_path)

    ideal_part1_end = start + edge
    part1_end_kf = find_keyframe_at_or_after(keyframes, ideal_part1_end)

    ideal_part3_start = end - edge
    part3_start_kf = find_keyframe_at_or_before(keyframes, ideal_part3_start)

    if (part1_end_kf is None or part3_start_kf is None or
        part1_end_kf >= part3_start_kf or
        part1_end_kf <= start or part3_start_kf >= end):
        return encode_segment(idx, input_path, start, end, seg_path, encoder_args, audio_filter)

    with tempfile.TemporaryDirectory() as tmpdir:
        parts = []

        part1 = os.path.join(tmpdir, "part1.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(start),
            "-to", str(part1_end_kf),  # -to is exclusive when re-encoding
        ] + encoder_args

        if audio_filter:
            cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        cmd.extend(["-avoid_negative_ts", "make_zero", "-loglevel", "error", part1])
        subprocess.run(cmd, capture_output=True)
        parts.append(part1)

        # known issue: stream copy can glitch about one frame at the GOP boundary
        middle_duration = part3_start_kf - part1_end_kf

        if middle_duration > 0:
            part2 = os.path.join(tmpdir, "part2.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(part1_end_kf),
                "-i", input_path,
                "-t", str(middle_duration),
                "-c:v", "copy",
            ]
            # audio still re-encoded so it matches the other parts
            if audio_filter:
                cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            cmd.extend(["-avoid_negative_ts", "make_zero", "-loglevel", "error", part2])
            subprocess.run(cmd, capture_output=True)
            parts.append(part2)

        part3 = os.path.join(tmpdir, "part3.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(part3_start_kf),
            "-to", str(end),
        ] + encoder_args

        if audio_filter:
            cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        cmd.extend(["-avoid_negative_ts", "make_zero", "-loglevel", "error", part3])
        subprocess.run(cmd, capture_output=True)
        parts.append(part3)

        concat_file = os.path.join(tmpdir, "concat.txt")
        with open(concat_file, "w") as f:
            for part in parts:
                f.write(f"file '{part}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", "-loglevel", "error", seg_path
        ]
        subprocess.run(cmd, capture_output=True)

    return (idx, seg_path)


def concatenate_segments_parallel(input_path: str, segments: list[tuple[float, float]], output_path: str,
                                   enhance_audio: bool = False, lut_path: str = None,
                                   workers: int = PARALLEL_WORKERS, smart_render: bool = True):
    mode = "smart render" if smart_render else "full re-encode"
    print(f"Concatenating {len(segments)} segments (parallel {workers}x, {mode})...")
    if enhance_audio:
        print("Audio enhancement enabled")
    if lut_path:
        print(f"Color grading with LUT: {os.path.basename(lut_path)}")

    start_time = time.time()

    encoder_args = get_cached_encoder_args()

    audio_filter = build_audio_filter_chain(enhance_audio)
    video_filter = build_video_filter_chain(lut_path)

    keyframes = None
    if smart_render and not video_filter:
        smart_count = sum(1 for s, e in segments if (e - s) > SMART_RENDER_THRESHOLD)
        if smart_count > 0:
            print("   Pre-fetching keyframes for smart rendering...")
            keyframes = get_keyframes(input_path)
            print(f"   Smart rendering {smart_count}/{len(segments)} long segments ({len(keyframes)} keyframes found)")

    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = []
        for i, (start, end) in enumerate(segments):
            seg_path = os.path.join(tmpdir, f"seg_{i:04d}.mp4")
            tasks.append((i, input_path, start, end, seg_path, encoder_args, audio_filter, video_filter, smart_render, keyframes))

        segment_files = [None] * len(segments)  # indexed so concat order survives as_completed
        completed = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(encode_single_segment, task): task[0] for task in tasks}

            for future in as_completed(futures):
                idx, seg_path = future.result()
                segment_files[idx] = seg_path
                completed += 1

                if completed % 10 == 0 or completed == len(segments):
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (len(segments) - completed) / rate if rate > 0 else 0
                    print(f"   Encoded {completed}/{len(segments)} segments ({elapsed:.1f}s elapsed, ~{remaining:.0f}s remaining)")

        encode_time = time.time() - start_time
        print(f"   Parallel encoding complete in {encode_time:.1f}s ({len(segments)/encode_time:.1f} seg/s)")

        concat_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_path, "w") as f:
            for seg_path in segment_files:
                f.write(f"file '{seg_path}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
            "-c", "copy", "-loglevel", "error", output_path
        ]
        subprocess.run(cmd, capture_output=True)

    total_time = time.time() - start_time
    print(f"Output saved to {output_path} (total: {total_time:.1f}s)")


def get_duration(input_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description="Jump cut editor using Silero VAD (PARALLEL version)")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--min-silence", type=float, default=MIN_SILENCE_DURATION,
                        help=f"Minimum silence duration to cut (default: {MIN_SILENCE_DURATION}s)")
    parser.add_argument("--min-speech", type=float, default=MIN_SPEECH_DURATION,
                        help=f"Minimum speech duration to keep (default: {MIN_SPEECH_DURATION}s)")
    parser.add_argument("--padding", type=int, default=PADDING_MS,
                        help=f"Padding around speech in ms (default: {PADDING_MS})")
    parser.add_argument("--merge-gap", type=float, default=0.3,
                        help="Merge segments closer than this (default: 0.3s)")
    parser.add_argument("--keep-start", action="store_true", default=True,
                        help="Always start from 0:00 (preserve intro, default: True)")
    parser.add_argument("--no-keep-start", action="store_false", dest="keep_start",
                        help="Allow cutting silence at the very beginning")
    parser.add_argument("--enhance-audio", action="store_true", default=False,
                        help="Apply audio enhancement (EQ, compression, loudness normalization)")
    parser.add_argument("--detect-restarts", action="store_true", default=False,
                        help=f"Detect '{RESTART_PHRASE}' and remove mistake segments")
    parser.add_argument("--restart-phrase", type=str, default=RESTART_PHRASE,
                        help=f"Phrase to trigger restart (default: '{RESTART_PHRASE}')")
    parser.add_argument("--whisper-model", type=str, default="base",
                        help="Whisper model size for restart detection (default: base)")
    parser.add_argument("--apply-lut", type=str, default=None,
                        help="Path to LUT file for color grading (.cube, .3dl, .dat, .m3d, .csp)")
    parser.add_argument("--workers", type=int, default=PARALLEL_WORKERS,
                        help=f"Number of parallel encoding workers (default: {PARALLEL_WORKERS})")
    parser.add_argument("--smart-render", action="store_true", default=False,
                        help="Use smart rendering (~30%% faster but may have 1-frame glitch at internal boundaries)")
    parser.add_argument("--no-smart-render", action="store_false", dest="smart_render",
                        help="Full re-encode all segments - perfect quality (default)")

    args = parser.parse_args()

    if args.apply_lut:
        lut_path = Path(args.apply_lut)
        if not lut_path.exists():
            print(f"LUT file not found: {args.apply_lut}")
            return
        if lut_path.suffix.lower() not in SUPPORTED_LUT_FORMATS:
            print(f"Unsupported LUT format: {lut_path.suffix}")
            print(f"   Supported: {', '.join(SUPPORTED_LUT_FORMATS)}")
            return

    input_path = args.input
    output_path = args.output

    print("Jump Cut Editor (Silero VAD) - PARALLEL VERSION")
    print(f"   Input: {input_path}")
    print(f"   Output: {output_path}")
    print(f"   Parallel workers: {args.workers}")
    print()

    overall_start = time.time()

    duration = get_duration(input_path)
    print(f"Video duration: {duration:.2f}s")

    print("Extracting audio...")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        extract_audio(input_path, audio_path)

        print(f"Running Silero VAD (min_silence={args.min_silence}s, min_speech={args.min_speech}s)...")
        speech_segments = get_speech_timestamps_silero(
            audio_path,
            min_speech_duration=args.min_speech,
            min_silence_duration=args.min_silence
        )
        print(f"   Found {len(speech_segments)} speech segments")

        for i, (start, end) in enumerate(speech_segments[:5]):
            print(f"     {i+1}. {start:.2f}s - {end:.2f}s ({end-start:.2f}s)")
        if len(speech_segments) > 5:
            print(f"     ... and {len(speech_segments) - 5} more")

    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

    if not speech_segments:
        print("No speech detected!")
        return

    speech_segments = merge_close_segments(speech_segments, args.merge_gap)
    print(f"After merging close segments: {len(speech_segments)} segments")

    padding_s = args.padding / 1000
    speech_segments = add_padding(speech_segments, padding_s, duration)
    print(f"After adding {args.padding}ms padding: {len(speech_segments)} segments")

    if args.detect_restarts:
        print(f"\nDetecting restart phrases ('{args.restart_phrase}')...")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            whisper_audio_path = tmp.name

        try:
            extract_audio(input_path, whisper_audio_path, sample_rate=16000)
            words = transcribe_with_whisper(whisper_audio_path, model_name=args.whisper_model)
            restart_times = find_restart_phrases(words, phrase=args.restart_phrase)

            if restart_times:
                print(f"   Found {len(restart_times)} restart phrase(s)")
                speech_segments = apply_restart_cuts(speech_segments, restart_times, RESTART_LOOKBACK)
                print(f"   After restart cuts: {len(speech_segments)} segments")
            else:
                print("   No restart phrases found")
        finally:
            if os.path.exists(whisper_audio_path):
                os.remove(whisper_audio_path)

    if args.keep_start and speech_segments and speech_segments[0][0] > 0:
        first_start, first_end = speech_segments[0]
        speech_segments[0] = (0.0, first_end)
        print("Preserving intro: extended first segment to start at 0:00")

    concatenate_segments_parallel(input_path, speech_segments, output_path,
                                   enhance_audio=args.enhance_audio, lut_path=args.apply_lut,
                                   workers=args.workers, smart_render=args.smart_render)

    new_duration = get_duration(output_path)
    removed = duration - new_duration
    overall_time = time.time() - overall_start

    print()
    print("Stats:")
    print(f"   Original: {duration:.2f}s")
    print(f"   New: {new_duration:.2f}s")
    print(f"   Removed: {removed:.2f}s ({100*removed/duration:.1f}%)")
    print(f"   Total processing time: {overall_time:.1f}s")


if __name__ == "__main__":
    main()
