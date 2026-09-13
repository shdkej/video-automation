import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import type { ReactNode } from 'react';

const clamp = (value: number) => Math.min(1, Math.max(0, value));

const Reveal: React.FC<{ delay: number; children: ReactNode; y?: number }> = ({ delay, children, y = 14 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({ frame: frame - delay, fps, config: { damping: 19, stiffness: 135, mass: 0.72 } });
  const settled = clamp(progress);
  return <div style={{ opacity: interpolate(settled, [0, 0.25, 1], [0, 1, 1]), transform: `translateY(${(1 - settled) * y}px) scale(${0.96 + settled * 0.04})` }}>{children}</div>;
};

const Chart = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const line = interpolate(frame, [fps * 1.05, fps * 1.72], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const node = interpolate(frame, [fps * 1.62, fps * 1.88], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const path = 'M36 216 C88 214 108 194 144 196 C190 198 206 158 244 160 C290 162 304 96 352 74';
  return <svg width="354" height="260" viewBox="0 0 390 270" fill="none" aria-hidden="true">
    <path d="M36 40V226H362" stroke="#F8FAF8" strokeWidth="3" opacity="0.85" />
    <path d={path} stroke="#F8FAF8" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - line} />
    <circle cx="352" cy="74" r={10 * node} fill="#F8FAF8" />
  </svg>;
};

export const YoutubeChartExplainer: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const connector = interpolate(frame, [fps * 0.65, fps * 1.05], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const takeaway = clamp(spring({ frame: frame - 54, fps, config: { damping: 19, stiffness: 135, mass: 0.72 } }));

  return <AbsoluteFill style={{ background: '#F8FAF8', color: '#161616', fontFamily: 'Pretendard, Arial, sans-serif', overflow: 'hidden' }}>
    <AbsoluteFill style={{ opacity: 0.62, backgroundImage: 'linear-gradient(#DCE4DE 1px, transparent 1px), linear-gradient(90deg, #DCE4DE 1px, transparent 1px)', backgroundSize: '48px 48px' }} />
    <Reveal delay={2} y={16}>
      <div style={{ position: 'absolute', top: 158, width: '100%', textAlign: 'center', fontSize: 55, lineHeight: 1.18, fontWeight: 800, letterSpacing: '-4px' }}>작은 실행은, 쌓일수록 방향이 된다</div>
    </Reveal>

    <Reveal delay={10}>
      <div style={{ position: 'absolute', left: 88, top: 610, width: 302, height: 302, border: '4px solid #161616', borderRadius: 32, background: 'rgba(255,255,255,.8)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 20 }}>
        <div style={{ width: 80, height: 80, border: '4px solid #161616', borderRadius: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 48, fontWeight: 800 }}>+</div>
        <div style={{ fontSize: 42, fontWeight: 800, letterSpacing: '-3px' }}>작은 실행</div>
      </div>
    </Reveal>

    <div style={{ position: 'absolute', left: 406, top: 758, width: 270, height: 4, background: '#161616', transformOrigin: 'left', transform: `scaleX(${connector})` }} />
    <Reveal delay={25} y={0}>
      <div style={{ position: 'absolute', left: 456, top: 696, width: 170, height: 66, border: '3px solid #161616', borderRadius: 34, background: '#F8FAF8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 30, fontWeight: 800, letterSpacing: '-2px' }}>반복</div>
    </Reveal>

    <Reveal delay={31}>
      <div style={{ position: 'absolute', left: 688, top: 520, width: 306, height: 480, borderRadius: 34, background: '#161616', color: '#F8FAF8', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <Chart />
        <div style={{ fontSize: 42, lineHeight: 1.18, textAlign: 'center', fontWeight: 800, letterSpacing: '-3px' }}>쌓이는<br />변화</div>
      </div>
    </Reveal>

    <div style={{ position: 'absolute', bottom: 180, width: '100%', textAlign: 'center', fontSize: 45, fontWeight: 800, letterSpacing: '-3px', opacity: takeaway, transform: `translateY(${(1 - takeaway) * 10}px)` }}><span style={{ borderBottom: '5px solid #161616', paddingBottom: 12 }}>쌓이면, 궤적이 된다</span></div>
  </AbsoluteFill>;
};
