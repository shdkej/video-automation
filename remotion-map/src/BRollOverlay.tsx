import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  random,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type BRollPage = {
  src: string; // public/ 기준 상대 경로
  start: number; // 초 (출력 타임라인)
  end: number; // 초
};

export type BRollOverlayProps = {
  videoSrc: string; // public/ 기준 상대 경로
  pages: BRollPage[];
  width?: number;
  height?: number;
  fps?: number;
  durationSec?: number;
};

const FADE_SEC = 0.22;

// B컷 컷어웨이 — A롤 오디오는 그대로, 화면만 이미지가 덮는다.
// 켄번즈(느린 줌 + 미세 팬)로 정지 이미지에 생기를 준다. 방향은 컷마다 랜덤.
const Cutaway: React.FC<{ src: string; durationInFrames: number; seed: string }> = ({
  src,
  durationInFrames,
  seed,
}) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const fade = FADE_SEC * fps;
  const opacity = interpolate(
    frame,
    [0, fade, Math.max(fade + 1, durationInFrames - fade), durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const p = frame / Math.max(1, durationInFrames);
  const zoomIn = random(seed) > 0.5;
  const zoom = zoomIn ? 1.03 + 0.09 * p : 1.12 - 0.09 * p;
  const panDir = random(seed + 'p') > 0.5 ? 1 : -1;
  const pan = panDir * width * 0.015 * p;
  return (
    <AbsoluteFill style={{ opacity }}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${zoom}) translateX(${pan}px)`,
        }}
      />
    </AbsoluteFill>
  );
};

export const BRollOverlay: React.FC<BRollOverlayProps> = ({ videoSrc, pages }) => {
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
            <Cutaway src={p.src} durationInFrames={dur} seed={`broll:${i}:${p.src}`} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
