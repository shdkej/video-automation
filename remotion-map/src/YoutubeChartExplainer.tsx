import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

const clamp = { extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const };
const values = [5, 7, 8, 8, 2, 6, 6];
const labels = ['9.07', '9.08', '9.09', '9.10', '9.11', '9.12', '9.13'];
const points = values.map((value, index) => ({ x: 136 + index * 135, y: 1270 - value * 76, value }));
const line = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');

export const YoutubeChartExplainer: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const heading = interpolate(frame, [0, fps * 0.3], [0, 1], clamp);
  const draw = interpolate(frame, [fps * 0.28, fps * 1.85], [0, 1], clamp);

  return <AbsoluteFill style={{ background: '#F7F7F3', color: '#161616', fontFamily: 'Pretendard, Arial, sans-serif', overflow: 'hidden' }}>
    <AbsoluteFill style={{ opacity: 0.45, backgroundImage: 'linear-gradient(#DBDDD7 1px, transparent 1px), linear-gradient(90deg, #DBDDD7 1px, transparent 1px)', backgroundSize: '48px 48px' }} />
    <div style={{ position: 'absolute', top: 176, left: 104, right: 104, opacity: heading, transform: `translateY(${(1 - heading) * 14}px)` }}>
      <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '2px', color: '#59615D' }}>OPENCLAW · WEEKLY CHAT</div>
      <div style={{ marginTop: 22, display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 84, fontWeight: 800, letterSpacing: '-6px' }}>42<span style={{ fontSize: 42, marginLeft: 8, letterSpacing: '-3px' }}>회</span></div>
        <div style={{ fontSize: 29, fontWeight: 700, letterSpacing: '-1px', color: '#59615D' }}>9.07 — 9.13</div>
      </div>
    </div>
    <svg width="1080" height="1920" viewBox="0 0 1080 1920" fill="none" aria-hidden="true">
      {[0, 4, 8].map((value) => {
        const y = 1270 - value * 76;
        return <g key={value}><path d={`M104 ${y}H976`} stroke="#161616" strokeWidth="2" opacity={value === 0 ? 0.48 : 0.14} /><text x="76" y={y + 9} textAnchor="end" fill="#59615D" fontSize="25" fontWeight="700">{value}</text></g>;
      })}
      <path d={line} stroke="#161616" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - draw} />
      {points.map((point, index) => {
        const visible = spring({ frame: frame - (fps * 1.42 + index * 2.7), fps, config: { damping: 20, stiffness: 150, mass: 0.55 } });
        const scale = Math.min(1, Math.max(0, visible));
        return <g key={labels[index]} opacity={scale} transform={`translate(${point.x} ${point.y}) scale(${scale}) translate(${-point.x} ${-point.y})`}>
          <circle cx={point.x} cy={point.y} r="11" fill="#F7F7F3" stroke="#161616" strokeWidth="5" />
          <text x={point.x} y={point.y - 31} textAnchor="middle" fill="#161616" fontSize="32" fontWeight="800">{point.value}</text>
          <text x={point.x} y="1333" textAnchor="middle" fill="#59615D" fontSize="24" fontWeight="700">{labels[index]}</text>
        </g>;
      })}
    </svg>
    <div style={{ position: 'absolute', left: 104, right: 104, bottom: 222, display: 'flex', justifyContent: 'space-between', borderTop: '2px solid rgba(22,22,22,.55)', paddingTop: 24, fontSize: 28, fontWeight: 700, letterSpacing: '-1px' }}>
      <span>하루 평균 6회</span><span>최고 8회 · 9.09–10</span>
    </div>
  </AbsoluteFill>;
};
