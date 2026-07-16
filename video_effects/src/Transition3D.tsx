import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
  spring,
  Easing,
} from "remotion";

interface Transition3DProps {
  imageSrc: string;
  startSwivel?: number;
  startTilt?: number;
  endSwivel?: number;
  endTilt?: number;
  perspective?: number;
  easeType?: "spring" | "easeOut" | "easeInOut";
}

export const Transition3D: React.FC<Transition3DProps> = ({
  imageSrc,
  startSwivel = -30,
  startTilt = 15,
  endSwivel = 0,
  endTilt = 0,
  perspective = 1000,
  easeType = "easeOut",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  let progress: number;

  if (easeType === "spring") {
    progress = spring({
      frame,
      fps,
      config: {
        damping: 20,
        stiffness: 100,
        mass: 0.5,
      },
    });
  } else if (easeType === "easeOut") {
    progress = interpolate(frame, [0, durationInFrames], [0, 1], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.cubic),
    });
  } else {
    progress = interpolate(frame, [0, durationInFrames], [0, 1], {
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.cubic),
    });
  }

  const swivel = interpolate(progress, [0, 1], [startSwivel, endSwivel]);
  const tilt = interpolate(progress, [0, 1], [startTilt, endTilt]);

  const scale = interpolate(progress, [0, 1], [1.1, 1]);

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
          src={imageSrc}
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

export const Transition3DVideo: React.FC<{
  videoSrc: string;
  startSwivel?: number;
  startTilt?: number;
  endSwivel?: number;
  endTilt?: number;
  perspective?: number;
  easeType?: "spring" | "easeOut" | "easeInOut";
  playbackRate?: number;
}> = ({
  videoSrc,
  startSwivel = -30,
  startTilt = 15,
  endSwivel = 0,
  endTilt = 0,
  perspective = 1000,
  easeType = "easeOut",
  playbackRate = 7,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  let progress: number;

  if (easeType === "spring") {
    progress = spring({
      frame,
      fps,
      config: {
        damping: 20,
        stiffness: 100,
        mass: 0.5,
      },
    });
  } else if (easeType === "easeOut") {
    progress = interpolate(frame, [0, durationInFrames], [0, 1], {
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.cubic),
    });
  } else {
    progress = interpolate(frame, [0, durationInFrames], [0, 1], {
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.cubic),
    });
  }

  const swivel = interpolate(progress, [0, 1], [startSwivel, endSwivel]);
  const tilt = interpolate(progress, [0, 1], [startTilt, endTilt]);
  const scale = interpolate(progress, [0, 1], [1.1, 1]);

  // placeholder: the python side renders extracted frames instead of video
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
        <video
          src={videoSrc}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
          muted
          playsInline
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
