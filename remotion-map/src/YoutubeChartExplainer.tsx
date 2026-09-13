import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

const clamp = { extrapolateLeft: 'clamp' as const, extrapolateRight: 'clamp' as const };

export const YoutubeChartExplainer: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const headline = interpolate(frame, [2, 13], [0, 1], clamp);
  const chartReveal = interpolate(frame, [15, 54], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  const markerProgress = spring({ frame: frame - 49, fps, config: { damping: 18, stiffness: 150, mass: 0.55 } });
  const closing = interpolate(frame, [67, 79], [0, 1], clamp);
  const chartPath = 'M 118 718 C 214 690, 262 625, 354 613 S 492 546, 590 518 S 731 387, 844 308';
  const dashLength = 1050;

  return (
    <AbsoluteFill style={{ backgroundColor: '#151515', color: '#F6F0E6', fontFamily: 'Pretendard, Arial, sans-serif', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: 0, opacity: 0.22, backgroundImage: 'linear-gradient(rgba(246,240,230,.13) 1px, transparent 1px), linear-gradient(90deg, rgba(246,240,230,.13) 1px, transparent 1px)', backgroundSize: '72px 72px' }} />
      <div style={{ position: 'absolute', top: 142, left: 84, right: 84, opacity: headline, transform: `translateY(${(1 - headline) * 26}px)` }}>
        <div style={{ color: '#B8FF6C', fontSize: 26, fontWeight: 800, letterSpacing: '0.08em' }}>SMALL ACTIONS / 30 DAYS</div>
        <div style={{ marginTop: 28, fontSize: 82, fontWeight: 800, lineHeight: 1.08, letterSpacing: '-0.075em' }}>작은 실행은<br />쌓여서 방향이 된다</div>
      </div>
      <div style={{ position: 'absolute', top: 535, left: 84, right: 84, height: 750, borderTop: '1px solid rgba(246,240,230,.42)', borderBottom: '1px solid rgba(246,240,230,.42)' }}>
        {[184, 368, 552].map((top) => <div key={top} style={{ position: 'absolute', top, left: 0, right: 0, borderTop: '1px solid rgba(246,240,230,.16)' }} />)}
        <div style={{ position: 'absolute', top: 16, left: 0, color: 'rgba(246,240,230,.62)', fontSize: 24, fontWeight: 700 }}>실행 밀도</div>
        <svg viewBox="0 0 928 750" width="928" height="750" style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
          <path d={chartPath} fill="none" stroke="#B8FF6C" strokeWidth="11" strokeLinecap="round" strokeDasharray={dashLength} strokeDashoffset={dashLength * (1 - chartReveal)} />
          <path d="M 118 718 C 214 690, 262 625, 354 613 S 492 546, 590 518 S 731 387, 844 308 L 844 750 L 118 750 Z" fill="rgba(184,255,108,.09)" opacity={chartReveal} />
          {[
            { cx: 354, cy: 613 },
            { cx: 590, cy: 518 },
            { cx: 844, cy: 308 },
          ].map(({ cx, cy }, index) => {
            const delay = index * 7;
            const show = Math.max(0, Math.min(1, markerProgress - delay / 18));
            return <circle key={cx} cx={cx} cy={cy} r={18 * show} fill="#151515" stroke="#F6F0E6" strokeWidth="7" />;
          })}
        </svg>
        <div style={{ position: 'absolute', bottom: 24, left: 30, fontSize: 25, fontWeight: 700, color: 'rgba(246,240,230,.72)' }}>1일</div>
        <div style={{ position: 'absolute', bottom: 24, left: 270, fontSize: 25, fontWeight: 700, color: 'rgba(246,240,230,.72)' }}>7일</div>
        <div style={{ position: 'absolute', bottom: 24, left: 520, fontSize: 25, fontWeight: 700, color: 'rgba(246,240,230,.72)' }}>14일</div>
        <div style={{ position: 'absolute', bottom: 24, right: 22, fontSize: 25, fontWeight: 700, color: 'rgba(246,240,230,.72)' }}>30일</div>
      </div>
      <div style={{ position: 'absolute', left: 84, right: 84, bottom: 172, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', opacity: closing, transform: `translateY(${(1 - closing) * 18}px)` }}>
        <div style={{ fontSize: 48, fontWeight: 800, letterSpacing: '-0.055em' }}>한 번보다,<br />다음 한 번</div>
        <div style={{ width: 132, height: 132, border: '2px solid #B8FF6C', borderRadius: 999, display: 'grid', placeItems: 'center', color: '#B8FF6C', fontSize: 27, fontWeight: 800 }}>NEXT</div>
      </div>
    </AbsoluteFill>
  );
};
