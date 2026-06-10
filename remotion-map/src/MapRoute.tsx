import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
  Easing,
} from 'remotion';
import { project } from './geo.mjs';
import data from './data.json';

const ACCENT = '#ffd166';
const ACCENT2 = '#ff7b00';
const W = data.width;
const H = data.height;

// 위경도 → 픽셀 (정적지도와 동일 center/zoom)
const toPx = (lonlat: number[]) =>
  project(lonlat, data.center, data.zoom, W, H);

const berlin = toPx(data.cities.berlin.lonlat);
const budapest = toPx(data.cities.budapest.lonlat);

// 곡선 비행경로(2차 베지어): 중점에서 진행방향에 수직으로 위로 솟게
const mid = { x: (berlin.x + budapest.x) / 2, y: (berlin.y + budapest.y) / 2 };
const dx = budapest.x - berlin.x;
const dy = budapest.y - berlin.y;
const len = Math.hypot(dx, dy);
// 수직 단위벡터 중 위(y 감소)로 향하는 것
let nx = -dy / len;
let ny = dx / len;
if (ny > 0) {
  nx = -nx;
  ny = -ny;
}
const bow = len * 0.22;
const ctrl = { x: mid.x + nx * bow, y: mid.y + ny * bow };

// 베지어 한 점과 접선각
const bezier = (t: number) => {
  const mt = 1 - t;
  const x = mt * mt * berlin.x + 2 * mt * t * ctrl.x + t * t * budapest.x;
  const y = mt * mt * berlin.y + 2 * mt * t * ctrl.y + t * t * budapest.y;
  const dxt = 2 * mt * (ctrl.x - berlin.x) + 2 * t * (budapest.x - ctrl.x);
  const dyt = 2 * mt * (ctrl.y - berlin.y) + 2 * t * (budapest.y - ctrl.y);
  return { x, y, angle: (Math.atan2(dyt, dxt) * 180) / Math.PI };
};

const PATH_D = `M ${berlin.x} ${berlin.y} Q ${ctrl.x} ${ctrl.y} ${budapest.x} ${budapest.y}`;

const CityPin: React.FC<{
  px: { x: number; y: number };
  name: string;
  country: string;
  appearAt: number;
  align: 'left' | 'right';
}> = ({ px, name, country, appearAt, align }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame: frame - appearAt, fps, config: { damping: 12, stiffness: 160 } });
  if (frame < appearAt) return null;

  const pulse = interpolate((frame - appearAt) % 60, [0, 60], [0, 1]);
  const ringScale = interpolate(pulse, [0, 1], [0.6, 3.2]);
  const ringOpacity = interpolate(pulse, [0, 1], [0.55, 0]);

  return (
    <div style={{ position: 'absolute', left: px.x, top: px.y, transform: 'translate(-50%,-50%)' }}>
      {/* 펄스 링 */}
      <div
        style={{
          position: 'absolute', left: '50%', top: '50%', width: 16, height: 16,
          marginLeft: -8, marginTop: -8, borderRadius: '50%',
          border: `2px solid ${ACCENT}`, transform: `scale(${ringScale})`, opacity: ringOpacity,
        }}
      />
      {/* 코어 점 */}
      <div
        style={{
          position: 'absolute', left: '50%', top: '50%', width: 14, height: 14,
          marginLeft: -7, marginTop: -7, borderRadius: '50%', background: ACCENT,
          boxShadow: `0 0 12px 3px ${ACCENT}`, transform: `scale(${pop})`,
        }}
      />
      {/* 라벨 */}
      <div
        style={{
          position: 'absolute',
          left: align === 'left' ? 16 : undefined,
          right: align === 'right' ? 16 : undefined,
          top: -14,
          transform: `scale(${pop})`,
          transformOrigin: align === 'left' ? 'left center' : 'right center',
          whiteSpace: 'nowrap',
          textAlign: align === 'right' ? 'right' : 'left',
        }}
      >
        <div style={{ fontSize: 30, fontWeight: 800, color: '#fff', letterSpacing: 0.5, textShadow: '0 2px 10px rgba(0,0,0,0.9)' }}>{name}</div>
        <div style={{ fontSize: 16, fontWeight: 600, color: ACCENT, textTransform: 'uppercase', letterSpacing: 2, textShadow: '0 2px 8px rgba(0,0,0,0.9)' }}>{country}</div>
      </div>
    </div>
  );
};

export const MapRoute: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // 배경 페이드 + 살짝 줌아웃
  const bgOpacity = interpolate(frame, [0, 25], [0, 1], { extrapolateRight: 'clamp' });
  const bgScale = interpolate(frame, [0, 40], [1.08, 1], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });

  // 경로 진행도
  const progress = interpolate(frame, [40, 205], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic),
  });
  const marker = bezier(Math.max(0.0001, progress));

  // 타이틀 등장
  const titleIn = spring({ frame: frame - 8, fps, config: { damping: 14 } });
  const badgeIn = spring({ frame: frame - 70, fps, config: { damping: 14 } });

  return (
    <AbsoluteFill style={{ backgroundColor: '#0b0d12', fontFamily: '"Helvetica Neue", Arial, sans-serif' }}>
      {/* 지도 배경 */}
      <AbsoluteFill style={{ opacity: bgOpacity, transform: `scale(${bgScale})` }}>
        <Img src={staticFile('map-bg.png')} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        {/* 가독성용 비네트 */}
        <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, rgba(11,13,18,0) 45%, rgba(11,13,18,0.78) 100%)' }} />
      </AbsoluteFill>

      {/* 경로 SVG */}
      <AbsoluteFill>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute' }}>
          <defs>
            <linearGradient id="line" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={ACCENT} />
              <stop offset="100%" stopColor={ACCENT2} />
            </linearGradient>
          </defs>
          {/* 전체 경로(흐린 점선) */}
          <path d={PATH_D} fill="none" stroke="#ffffff" strokeOpacity={0.18} strokeWidth={2.5} strokeDasharray="2 10" strokeLinecap="round" />
          {/* 진행 경로(빛나는 선) */}
          <path
            d={PATH_D} fill="none" stroke="url(#line)" strokeWidth={5} strokeLinecap="round"
            pathLength={1} strokeDasharray={1} strokeDashoffset={1 - progress}
            style={{ filter: `drop-shadow(0 0 6px ${ACCENT})` }}
          />
        </svg>

        {/* 이동 마커(비행기 삼각형 + 글로우) */}
        {progress > 0 && progress < 1 && (
          <div style={{ position: 'absolute', left: marker.x, top: marker.y, transform: `translate(-50%,-50%) rotate(${marker.angle}deg)` }}>
            <div style={{
              width: 0, height: 0, borderLeft: '15px solid #fff', borderTop: '9px solid transparent', borderBottom: '9px solid transparent',
              filter: `drop-shadow(0 0 8px ${ACCENT})`,
            }} />
          </div>
        )}
        {/* 마커 헤드 글로우 점 */}
        {progress > 0 && (
          <div style={{
            position: 'absolute', left: marker.x, top: marker.y, width: 10, height: 10,
            marginLeft: -5, marginTop: -5, borderRadius: '50%', background: '#fff',
            boxShadow: `0 0 16px 5px ${ACCENT}`, opacity: progress < 1 ? 1 : 0,
          }} />
        )}
      </AbsoluteFill>

      {/* 도시 핀 */}
      <CityPin px={berlin} name={data.cities.berlin.name} country={data.cities.berlin.country} appearAt={28} align="left" />
      <CityPin px={budapest} name={data.cities.budapest.name} country={data.cities.budapest.country} appearAt={198} align="right" />

      {/* 타이틀 */}
      <div style={{
        position: 'absolute', top: 44, left: 0, right: 0, textAlign: 'center',
        opacity: titleIn, transform: `translateY(${interpolate(titleIn, [0, 1], [-24, 0])}px)`,
      }}>
        <div style={{ fontSize: 46, fontWeight: 800, color: '#fff', letterSpacing: 3, textShadow: '0 4px 18px rgba(0,0,0,0.9)' }}>
          BERLIN <span style={{ color: ACCENT }}>→</span> BUDAPEST
        </div>
      </div>

      {/* 거리 배지 */}
      <div style={{
        position: 'absolute', bottom: 46, left: 0, right: 0, textAlign: 'center',
        opacity: badgeIn, transform: `translateY(${interpolate(badgeIn, [0, 1], [24, 0])}px)`,
      }}>
        <span style={{
          display: 'inline-block', padding: '10px 26px', borderRadius: 999,
          background: 'rgba(255,209,102,0.12)', border: `1px solid ${ACCENT}`,
          color: '#fff', fontSize: 22, fontWeight: 700, letterSpacing: 1, backdropFilter: 'blur(6px)',
        }}>
          {data.distanceKm} km · Germany → Hungary
        </span>
      </div>
    </AbsoluteFill>
  );
};
