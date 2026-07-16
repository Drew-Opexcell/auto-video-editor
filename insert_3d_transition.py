#!/usr/bin/env python3
"""Insert a sped-up 3D swivel teaser into a video, keeping the original audio."""

import subprocess
import tempfile
import os
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pan_3d_transition import create_transition, get_video_info
from jump_cut_vad import get_cached_encoder_args

DEFAULT_INSERT_AT = 3.0
DEFAULT_DURATION = 5.0
DEFAULT_TEASER_START = 60.0
MAX_PLAYBACK_RATE = 100.0


def composite_with_transition(
    input_path: str,
    output_path: str,
    insert_at: float = DEFAULT_INSERT_AT,
    duration: float = DEFAULT_DURATION,
    teaser_start: float = DEFAULT_TEASER_START,
    bg_color: str = "#2d3436",
    bg_image: str = None,
):
    """Insert the teaser (teaser_start to end, squeezed into `duration`) at insert_at."""
    info = get_video_info(input_path)
    total_duration = info["duration"]

    if teaser_start >= total_duration:
        raise ValueError(f"Teaser start ({teaser_start}s) must be less than video duration ({total_duration}s)")

    available_content = total_duration - teaser_start
    uncapped_rate = available_content / duration

    if uncapped_rate > MAX_PLAYBACK_RATE:
        # at the cap, only take as much content as fits
        playback_rate = MAX_PLAYBACK_RATE
        teaser_content = duration * MAX_PLAYBACK_RATE
        print(f"   Capping speed at {MAX_PLAYBACK_RATE}x (would have been {uncapped_rate:.1f}x)")
    else:
        playback_rate = uncapped_rate
        teaser_content = available_content

    print("Insert Swivel Teaser")
    print(f"   Input: {input_path}")
    print(f"   Insert at: {insert_at}s")
    print(f"   Teaser duration: {duration}s")
    print(f"   Teaser content: {teaser_start}s -> {total_duration:.1f}s ({teaser_content:.1f}s at {playback_rate:.1f}x speed)")
    print()

    if insert_at + duration > total_duration:
        raise ValueError(f"Insert point + duration ({insert_at + duration}s) exceeds video duration ({total_duration}s)")

    with tempfile.TemporaryDirectory() as tmpdir:
        transition_path = os.path.join(tmpdir, "transition.mp4")
        print("Generating swivel teaser...")

        create_transition(
            input_path=input_path,
            output_path=transition_path,
            start=teaser_start,
            source_duration=teaser_content,
            output_duration=duration,
            playback_rate=playback_rate,
            bg_color=bg_color,
            bg_image=bg_image,
        )

        # video only from here; the original audio is laid back on at the end
        print("\nExtracting video segments...")

        encoder_args = get_cached_encoder_args()

        seg1_path = os.path.join(tmpdir, "seg1.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-t", str(insert_at),
            "-an",
        ] + encoder_args + [
            "-loglevel", "error",
            seg1_path
        ]
        subprocess.run(cmd, check=True)
        print(f"   Segment 1: 0s - {insert_at}s")

        # re-encode the remotion output so it concats cleanly with the other two
        seg2_path = os.path.join(tmpdir, "seg2.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", transition_path,
            "-an",
        ] + encoder_args + [
            "-loglevel", "error",
            seg2_path
        ]
        subprocess.run(cmd, check=True)
        print(f"   Segment 2: transition ({duration}s)")

        seg3_path = os.path.join(tmpdir, "seg3.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-ss", str(insert_at + duration),
            "-an",
        ] + encoder_args + [
            "-loglevel", "error",
            seg3_path
        ]
        subprocess.run(cmd, check=True)
        remaining = total_duration - (insert_at + duration)
        print(f"   Segment 3: {insert_at + duration}s - end ({remaining:.1f}s)")

        print("\nConcatenating video segments...")
        concat_video_path = os.path.join(tmpdir, "concat_video.mp4")

        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w") as f:
            f.write(f"file '{seg1_path}'\n")
            f.write(f"file '{seg2_path}'\n")
            f.write(f"file '{seg3_path}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            "-loglevel", "error",
            concat_video_path
        ]
        subprocess.run(cmd, check=True)

        print("Extracting original audio...")
        audio_path = os.path.join(tmpdir, "audio.aac")
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vn", "-c:a", "aac", "-b:a", "192k",
            "-loglevel", "error",
            audio_path
        ]
        subprocess.run(cmd, check=True)

        print("Merging video and audio...")
        cmd = [
            "ffmpeg", "-y",
            "-i", concat_video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-shortest",  # in case of tiny duration mismatches
            "-loglevel", "error",
            output_path
        ]
        subprocess.run(cmd, check=True)

    print(f"\nOutput saved to {output_path}")
    print(f"   Timeline: [0-{insert_at}s] [swivel teaser {duration}s] [{insert_at+duration}s-end]")
    print(f"   Teaser shows: {teaser_start}s -> {total_duration:.1f}s ({teaser_content:.1f}s at {playback_rate:.1f}x speed)")
    print("   Audio: Original audio preserved throughout")


def main():
    parser = argparse.ArgumentParser(
        description="Insert 3D transition into video while preserving audio"
    )
    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--insert-at", type=float, default=DEFAULT_INSERT_AT,
                        help=f"Insert point in seconds (default: {DEFAULT_INSERT_AT})")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help=f"Transition duration in seconds (default: {DEFAULT_DURATION})")
    parser.add_argument("--teaser-start", type=float, default=DEFAULT_TEASER_START,
                        help=f"Where to start sourcing teaser content (default: {DEFAULT_TEASER_START}s = 1 minute)")
    parser.add_argument("--bg-color", type=str, default="#2d3436",
                        help="Background color (hex, default: #2d3436)")
    parser.add_argument("--bg-image", type=str, default=None,
                        help="Background image path (overrides --bg-color)")

    args = parser.parse_args()

    composite_with_transition(
        input_path=args.input,
        output_path=args.output,
        insert_at=args.insert_at,
        duration=args.duration,
        teaser_start=args.teaser_start,
        bg_color=args.bg_color,
        bg_image=args.bg_image,
    )


if __name__ == "__main__":
    main()
