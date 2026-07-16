import React from "react";
import {
  AbsoluteFill,
  Sequence,
  Img,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
  spring,
  Easing,
  staticFile,
  getInputProps,
} from "remotion";

interface VideoTransition3DProps {
  frameCount: number;
  frameDir: string; // Directory containing frame_%04d.jpg
  startSwivel: number;
  startTilt: number;
  endSwivel: number;
  endTilt: number;
  perspective: number;
  playbackRate: number;
}

export const VideoTransition3D: React.FC<VideoTransition3DProps> = ({
  frameCount,
  frameDir,
  startSwivel = -30,
  startTilt = 15,
  endSwivel = 0,
  endTilt = 0,
  perspective = 1000,
  playbackRate = 7,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const sourceFrameIndex = Math.min(
    Math.floor(frame * playbackRate),
    frameCount - 1
  );

  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const swivel = interpolate(progress, [0, 1], [startSwivel, endSwivel]);
  const tilt = interpolate(progress, [0, 1], [startTilt, endTilt]);
  const scale = interpolate(progress, [0, 1], [1.15, 1]);

  const frameFilename = `frame_${String(sourceFrameIndex).padStart(4, "0")}.jpg`;
  const framePath = staticFile(`${frameDir}/${frameFilename}`);

  return (
    <AbsoluteFill
      style={{
        perspective: `${perspective}px`,
        backgroundColor: "black",
      }}
    >
      <AbsoluteFill
        style={{
          transform: `rotateY(${swivel}deg) rotateX(${tilt}deg) scale(${scale})`,
          transformStyle: "preserve-3d",
          backfaceVisibility: "hidden",
        }}
      >
        <Img
          src={framePath}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

export const VideoTransition3DDynamic: React.FC = () => {
  const props = getInputProps() as VideoTransition3DProps;
  return <VideoTransition3D {...props} />;
};
