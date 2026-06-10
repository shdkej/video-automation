import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';
import meta from './data-fly.json';

const ACCENT = '#ffd166';
const pad4 = (n: number) => String(n).padStart(4, '0');

// 미리 구운 정적 플라이 프레임 시퀀스 재생 + 오버레이 (WebGL 불필요, 결정적)
export const MapFlyStatic: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const idx = Math.max(0, Math.min(meta.frames - 1, frame));

  const titleIn = spring({ frame: frame - 6, fps, config: { damping: 14 } });
  const badgeIn = spring({ frame: frame - 50, fps, config: { damping: 14 } });

  return (
    <AbsoluteFill style={{ backgroundColor: '#0b0d12', fontFamily: '"Helvetica Neue", Arial, sans-serif' }}>
      <Img src={staticFile(`fly/frame-${pad4(idx)}.png`)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />

      {/* 비네트 */}
      <AbsoluteFill style={{ pointerEvents: 'none', background: 'radial-gradient(ellipse at 50% 45%, rgba(11,13,18,0) 55%, rgba(11,13,18,0.62) 100%)' }} />

      {/* 타이틀 */}
      <div style={{
        position: 'absolute', top: 40, left: 0, right: 0, textAlign: 'center',
        opacity: titleIn, transform: `translateY(${interpolate(titleIn, [0, 1], [-24, 0])}px)`,
      }}>
        <div style={{ fontSize: 44, fontWeight: 800, color: '#fff', letterSpacing: 3, textShadow: '0 4px 18px rgba(0,0,0,0.95)' }}>
          BERLIN <span style={{ color: ACCENT }}>→</span> BUDAPEST
        </div>
      </div>

      {/* 거리 배지 */}
      <div style={{
        position: 'absolute', bottom: 44, left: 0, right: 0, textAlign: 'center',
        opacity: badgeIn, transform: `translateY(${interpolate(badgeIn, [0, 1], [24, 0])}px)`,
      }}>
        <span style={{
          display: 'inline-block', padding: '10px 26px', borderRadius: 999,
          background: 'rgba(255,209,102,0.14)', border: `1px solid ${ACCENT}`,
          color: '#fff', fontSize: 22, fontWeight: 700, letterSpacing: 1,
        }}>
          {meta.distanceKm} km · Germany → Hungary
        </span>
      </div>
    </AbsoluteFill>
  );
};
