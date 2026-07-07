import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type NotePage = {
  src: string; // public/ 기준 상대 경로
  start: number; // 초
  end: number; // 초
};

export type NoteOverlayProps = {
  videoSrc: string; // public/ 기준 상대 경로
  pages: NotePage[];
  width?: number;
  height?: number;
  fps?: number;
  durationSec?: number;
  pageWidthRatio?: number; // 화면 폭 대비 노트 폭 (기본 0.78)
};

const EXIT_SEC = 0.5;

const Page: React.FC<{ src: string; durationInFrames: number; ratio: number }> = ({
  src,
  durationInFrames,
  ratio,
}) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  // 등장: 책장 넘기듯 왼쪽 축 rotateY flip
  const flipIn = spring({ frame, fps, config: { damping: 16, mass: 0.9, stiffness: 90 } });
  const rotateY = interpolate(flipIn, [0, 1], [-100, 0]);

  // 퇴장: 다음 장으로 넘어가듯 오른쪽으로 젖히며 fade
  const exitStart = durationInFrames - EXIT_SEC * fps;
  const exit = interpolate(frame, [exitStart, durationInFrames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const exitRotate = exit * 70;
  const opacity = interpolate(flipIn, [0, 0.25], [0, 1]) * (1 - exit);

  // 유지: 느린 sine float — 종이가 공중에 떠 있는 느낌
  const t = frame / fps;
  const floatY = Math.sin(t * 1.1) * 7;
  const floatR = Math.sin(t * 0.7 + 1.3) * 0.7;

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', perspective: 1400 }}>
      <Img
        src={staticFile(src)}
        style={{
          width: width * ratio,
          transformOrigin: 'left center',
          transform: `translateY(${floatY}px) rotate(${floatR}deg) rotateY(${rotateY + exitRotate}deg)`,
          opacity,
          borderRadius: 6,
          filter: 'drop-shadow(0 24px 48px rgba(0,0,0,0.45)) drop-shadow(0 4px 10px rgba(0,0,0,0.3))',
        }}
      />
    </AbsoluteFill>
  );
};

export const NoteOverlay: React.FC<NoteOverlayProps> = ({
  videoSrc,
  pages,
  pageWidthRatio = 0.78,
}) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ backgroundColor: '#000' }}>
      <OffthreadVideo
        src={staticFile(videoSrc)}
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
      {pages.map((p, i) => {
        const from = Math.round(p.start * fps);
        const dur = Math.max(1, Math.round((p.end - p.start) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur}>
            <Page src={p.src} durationInFrames={dur} ratio={pageWidthRatio} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
