#!/usr/bin/env python3
"""3D swivel transition rendered with Remotion."""

import subprocess
import tempfile
import os
import argparse
import json
import shutil
from pathlib import Path

DEFAULT_SWIVEL_START = 3.5
DEFAULT_SWIVEL_END = -3.5
DEFAULT_TILT_START = 1.7
DEFAULT_TILT_END = 1.7
DEFAULT_PERSPECTIVE = 1000
DEFAULT_PLAYBACK_RATE = 7
DEFAULT_OUTPUT_DURATION = 1.0
DEFAULT_EASING = "linear"
DEFAULT_BG_COLOR = "#2d3436"

REMOTION_DIR = Path(__file__).parent / "video_effects"


def get_video_info(input_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json", input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)

    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})

    # r_frame_rate looks like "30/1"
    fps_str = stream.get("r_frame_rate", "30/1")
    num, den = map(int, fps_str.split("/"))
    fps = num / den

    return {
        "width": int(stream.get("width", 1920)),
        "height": int(stream.get("height", 1080)),
        "fps": fps,
        "duration": float(fmt.get("duration", stream.get("duration", 0)))
    }


def extract_frames(input_path, output_dir, start, duration, fps):
    print(f"Extracting frames from {start}s for {duration}s...")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        "-loglevel", "error",
        os.path.join(output_dir, "frame_%04d.jpg")
    ]
    subprocess.run(cmd, check=True)

    frame_count = len([f for f in os.listdir(output_dir) if f.startswith("frame_")])
    print(f"   Extracted {frame_count} frames")
    return frame_count


def render_transition(
    frame_dir: str,
    output_path: str,
    frame_count: int,
    width: int,
    height: int,
    fps: float,
    output_duration: float,
    swivel_start: float,
    swivel_end: float,
    tilt_start: float,
    tilt_end: float,
    perspective: int,
    playback_rate: float,
    easing: str = "easeOut",
    bg_color: str = DEFAULT_BG_COLOR,
    bg_image: str = None
):
    """Render the transition with Remotion."""
    print("Rendering 3D transition...")

    output_frames = int(output_duration * fps)

    # remotion can only load images from its public folder
    public_dir = REMOTION_DIR / "public" / "frames"
    public_dir.mkdir(parents=True, exist_ok=True)

    frame_files = [f for f in os.listdir(frame_dir) if f.startswith("frame_")]
    print(f"   Copying {len(frame_files)} frames to {public_dir}...")
    for f in frame_files:
        shutil.copy(os.path.join(frame_dir, f), public_dir / f)
    print("   Frames copied")

    bg_image_filename = None
    if bg_image and os.path.exists(bg_image):
        bg_image_filename = "bg_image" + Path(bg_image).suffix
        shutil.copy(bg_image, public_dir / bg_image_filename)
        print(f"Using background image: {bg_image}")

    if easing == "spring":
        easing_code = '''
  const progress = spring({
    frame,
    fps: ''' + str(int(fps)) + ''',
    config: { damping: 15, stiffness: 80, mass: 0.5 },
  });'''
    elif easing == "easeInOut":
        easing_code = '''
  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });'''
    elif easing == "easeOut":
        easing_code = '''
  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });'''
    else:
        easing_code = '''
  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateRight: "clamp",
  });'''

    spring_import = ", spring" if easing == "spring" else ""

    if bg_image_filename:
        bg_jsx = f'''<Img
        src={{staticFile("frames/{bg_image_filename}")}}
        style={{{{ width: "100%", height: "100%", objectFit: "cover", position: "absolute" }}}}
      />'''
        bg_style = "backgroundColor: 'transparent'"
    else:
        bg_jsx = ""
        bg_style = f'backgroundColor: "{bg_color}"'

    dynamic_root = f'''
import React from "react";
import {{ Composition, AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig, Easing, staticFile{spring_import} }} from "remotion";

const VideoTransition3D: React.FC = () => {{
  const frame = useCurrentFrame();
  const {{ durationInFrames, fps }} = useVideoConfig();

  const frameCount = {frame_count};
  const playbackRate = {playback_rate};
  const swivelStart = {swivel_start};
  const swivelEnd = {swivel_end};
  const tiltStart = {tilt_start};
  const tiltEnd = {tilt_end};
  const perspectiveVal = {perspective};

  const sourceFrameIndex = Math.min(Math.floor(frame * playbackRate), frameCount - 1);
{easing_code}

  const swivelDeg = interpolate(progress, [0, 1], [swivelStart, swivelEnd]);
  const tiltDeg = interpolate(progress, [0, 1], [tiltStart, tiltEnd]);
  const scaleVal = 0.985;
  const translateY = 0;

  const frameNum = String(sourceFrameIndex + 1).padStart(4, "0");
  const frameFilename = "frame_" + frameNum + ".jpg";

  return (
    <AbsoluteFill style={{{{ perspective: perspectiveVal + "px", {bg_style} }}}}>
      {bg_jsx}
      <AbsoluteFill style={{{{
        transform: "translateY(" + translateY + "%) rotateY(" + swivelDeg + "deg) rotateX(" + tiltDeg + "deg) scale(" + scaleVal + ")",
        transformStyle: "preserve-3d",
      }}}}>
        <Img
          src={{staticFile("frames/" + frameFilename)}}
          style={{{{ width: "100%", height: "100%", objectFit: "cover" }}}}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
}};

export const DynamicRoot: React.FC = () => {{
  return (
    <Composition
      id="Pan3D"
      component={{VideoTransition3D}}
      durationInFrames={{{output_frames}}}
      fps={{{int(fps)}}}
      width={{{width}}}
      height={{{height}}}
    />
  );
}};
'''

    dynamic_root_path = REMOTION_DIR / "src" / "DynamicRoot.tsx"
    with open(dynamic_root_path, "w") as f:
        f.write(dynamic_root)

    dynamic_index_path = REMOTION_DIR / "src" / "dynamic-index.ts"
    with open(dynamic_index_path, "w") as f:
        f.write('import { registerRoot } from "remotion";\nimport { DynamicRoot } from "./DynamicRoot";\nregisterRoot(DynamicRoot);')

    # remotion runs with cwd=REMOTION_DIR, so the output path has to be absolute
    abs_output_path = os.path.abspath(output_path)

    cmd = [
        "npx", "remotion", "render",
        str(dynamic_index_path),
        "Pan3D",
        abs_output_path,
    ]

    result = subprocess.run(cmd, cwd=str(REMOTION_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Render error: {result.stderr}")
        raise RuntimeError("Remotion render failed")

    print(f"Rendered to {output_path}")

    shutil.rmtree(public_dir)


def create_transition(
    input_path: str,
    output_path: str,
    start: float = 0,
    source_duration: float = None,
    output_duration: float = DEFAULT_OUTPUT_DURATION,
    swivel_start: float = DEFAULT_SWIVEL_START,
    swivel_end: float = DEFAULT_SWIVEL_END,
    tilt_start: float = DEFAULT_TILT_START,
    tilt_end: float = DEFAULT_TILT_END,
    perspective: int = DEFAULT_PERSPECTIVE,
    playback_rate: float = DEFAULT_PLAYBACK_RATE,
    easing: str = DEFAULT_EASING,
    bg_color: str = DEFAULT_BG_COLOR,
    bg_image: str = None,
):
    """Create a 3D pan transition from a video segment."""
    info = get_video_info(input_path)
    print(f"Input: {info['width']}x{info['height']} @ {info['fps']:.2f}fps")

    if source_duration is None:
        source_duration = output_duration * playback_rate

    if start + source_duration > info["duration"]:
        source_duration = info["duration"] - start
        print(f"Adjusted source duration to {source_duration:.2f}s")

    with tempfile.TemporaryDirectory() as tmpdir:
        # only extract the frames the output needs, spread evenly over the source
        output_frames_needed = int(output_duration * info["fps"])
        extraction_fps = output_frames_needed / source_duration
        print(f"Extracting {output_frames_needed} frames at {extraction_fps:.2f}fps (spaced across {source_duration:.1f}s)")

        frame_count = extract_frames(
            input_path, tmpdir, start, source_duration, extraction_fps
        )

        if frame_count == 0:
            raise RuntimeError("No frames extracted")

        render_transition(
            frame_dir=tmpdir,
            output_path=output_path,
            frame_count=frame_count,
            width=info["width"],
            height=info["height"],
            fps=info["fps"],
            output_duration=output_duration,
            swivel_start=swivel_start,
            swivel_end=swivel_end,
            tilt_start=tilt_start,
            tilt_end=tilt_end,
            perspective=perspective,
            playback_rate=1,  # extraction already did the speed-up, so map frames 1:1
            easing=easing,
            bg_color=bg_color,
            bg_image=bg_image,
        )


def main():
    parser = argparse.ArgumentParser(description="Create 3D pan transition effect")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--start", type=float, default=0,
                        help="Start time in seconds (default: 0)")
    parser.add_argument("--source-duration", type=float, default=None,
                        help="Source duration to use (default: auto based on speed)")
    parser.add_argument("--output-duration", type=float, default=DEFAULT_OUTPUT_DURATION,
                        help=f"Output transition duration in seconds (default: {DEFAULT_OUTPUT_DURATION})")
    parser.add_argument("--swivel-start", type=float, default=DEFAULT_SWIVEL_START,
                        help=f"Start Y-rotation in degrees (default: {DEFAULT_SWIVEL_START})")
    parser.add_argument("--swivel-end", type=float, default=DEFAULT_SWIVEL_END,
                        help=f"End Y-rotation in degrees (default: {DEFAULT_SWIVEL_END})")
    parser.add_argument("--tilt-start", type=float, default=DEFAULT_TILT_START,
                        help=f"Start X-rotation in degrees (default: {DEFAULT_TILT_START})")
    parser.add_argument("--tilt-end", type=float, default=DEFAULT_TILT_END,
                        help=f"End X-rotation in degrees (default: {DEFAULT_TILT_END})")
    parser.add_argument("--perspective", type=int, default=DEFAULT_PERSPECTIVE,
                        help=f"Perspective depth (default: {DEFAULT_PERSPECTIVE})")
    parser.add_argument("--speed", type=float, default=DEFAULT_PLAYBACK_RATE,
                        help=f"Playback speed multiplier (default: {DEFAULT_PLAYBACK_RATE}x)")
    parser.add_argument("--easing", type=str, default=DEFAULT_EASING,
                        choices=["linear", "easeOut", "easeInOut", "spring"],
                        help=f"Easing type (default: {DEFAULT_EASING})")
    parser.add_argument("--bg-color", type=str, default=DEFAULT_BG_COLOR,
                        help=f"Background color (hex, default: {DEFAULT_BG_COLOR})")
    parser.add_argument("--bg-image", type=str, default=None,
                        help="Background image path (overrides --bg-color)")

    args = parser.parse_args()

    print("3D Pan Transition Generator")
    print(f"   Input: {args.input}")
    print(f"   Output: {args.output}")
    print(f"   Swivel: {args.swivel_start}° -> {args.swivel_end}°")
    print(f"   Tilt: {args.tilt_start}° -> {args.tilt_end}°")
    print(f"   Speed: {args.speed}x")
    print(f"   Easing: {args.easing}")
    print()

    create_transition(
        input_path=args.input,
        output_path=args.output,
        start=args.start,
        source_duration=args.source_duration,
        output_duration=args.output_duration,
        swivel_start=args.swivel_start,
        swivel_end=args.swivel_end,
        tilt_start=args.tilt_start,
        tilt_end=args.tilt_end,
        perspective=args.perspective,
        playback_rate=args.speed,
        easing=args.easing,
        bg_color=args.bg_color,
        bg_image=args.bg_image,
    )

    print()
    print(f"Done! Output saved to {args.output}")


if __name__ == "__main__":
    main()
