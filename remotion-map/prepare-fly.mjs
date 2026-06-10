// 라이브 카메라 플라이를 "미리 구운" 정적 프레임으로 생성한다.
// Mapbox Static Images API는 bearing/pitch와 path 오버레이를 지원하므로,
// 프레임마다 카메라(center/zoom/bearing/pitch)와 진행 경로선을 바꿔 PNG를 내려받는다.
// → Remotion은 이 이미지 시퀀스만 재생 (WebGL 불필요, 완전 결정적).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { greatCircleArc, bearing as bearingOf, haversineKm, CITIES } from './src/geo.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadToken() {
  for (const p of [join(__dirname, '.env'), join(__dirname, '..', '.env')]) {
    try {
      const txt = readFileSync(p, 'utf8');
      const m = txt.match(/MAPBOX_API_KEY\s*=\s*(.+)/) || txt.match(/REMOTION_MAPBOX_TOKEN\s*=\s*(.+)/);
      if (m) return m[1].trim().replace(/^["']|["']$/g, '');
    } catch {}
  }
  throw new Error('MAPBOX 토큰을 .env에서 찾지 못했습니다.');
}

// ---- 설정 ----
const W = 1280, H = 720, FPS = 30, TOTAL = 210; // 7초
const STYLE = 'mapbox/dark-v11';
const ACCENT = 'ffd166';
const BERLIN = CITIES.berlin.lonlat;
const BUDAPEST = CITIES.budapest.lonlat;
const ARC = greatCircleArc(BERLIN, BUDAPEST, 300); // 부드러운 카메라용 고해상 경로
const TRAVEL_BEARING = bearingOf(BERLIN, BUDAPEST); // 일정 방위(전진 글라이드, 종점 회전 방지)

// ---- 보간 헬퍼 ----
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const easeInOutCubic = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
function keyframe(frame, xs, ys) {
  if (frame <= xs[0]) return ys[0];
  if (frame >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (frame >= xs[i] && frame <= xs[i + 1]) {
      const t = (frame - xs[i]) / (xs[i + 1] - xs[i]);
      return ys[i] + (ys[i + 1] - ys[i]) * t;
    }
  }
  return ys[ys.length - 1];
}

// frame → 카메라
function cameraAt(frame) {
  const p = easeInOutCubic(clamp01((frame - 20) / (TOTAL - 30 - 20)));
  const lastIdx = ARC.length - 1;
  const idx = Math.min(lastIdx, Math.round(p * lastIdx));
  const center = ARC[idx];
  const zoom = keyframe(frame, [0, 20, TOTAL - 25, TOTAL - 1], [4.8, 5.5, 5.95, 6.6]);
  const pitch = keyframe(frame, [0, 20, TOTAL - 1], [20, 52, 52]);
  return { center, zoom, bearing: TRAVEL_BEARING, pitch, sliceCount: Math.max(2, idx + 1) };
}

// ---- 폴리라인 인코딩 (Google precision 5) ----
function encodeSigned(v) {
  let s = v < 0 ? ~(v << 1) : v << 1;
  let out = '';
  while (s >= 0x20) {
    out += String.fromCharCode((0x20 | (s & 0x1f)) + 63);
    s >>= 5;
  }
  out += String.fromCharCode(s + 63);
  return out;
}
function encodePolyline(coords) {
  let res = '', pLat = 0, pLng = 0;
  for (const [lng, lat] of coords) {
    const la = Math.round(lat * 1e5), ln = Math.round(lng * 1e5);
    res += encodeSigned(la - pLat) + encodeSigned(ln - pLng);
    pLat = la; pLng = ln;
  }
  return res;
}
// 경로 점을 최대 maxN개로 다운샘플 (URL 길이 안전)
function downsample(coords, maxN = 80) {
  if (coords.length <= maxN) return coords;
  const step = (coords.length - 1) / (maxN - 1);
  const out = [];
  for (let i = 0; i < maxN; i++) out.push(coords[Math.round(i * step)]);
  out[out.length - 1] = coords[coords.length - 1];
  return out;
}

function buildUrl(cam, token) {
  const drawn = downsample(ARC.slice(0, cam.sliceCount), 80);
  const poly = encodeURIComponent(encodePolyline(drawn));
  const overlay = `path-5+${ACCENT}-1(${poly})`;
  const [lon, lat] = cam.center;
  const pos = `${lon.toFixed(5)},${lat.toFixed(5)},${cam.zoom.toFixed(3)},${cam.bearing.toFixed(1)},${cam.pitch.toFixed(1)}`;
  return `https://api.mapbox.com/styles/v1/${STYLE}/static/${overlay}/${pos}/${W}x${H}?access_token=${token}&attribution=false&logo=false`;
}

// ---- 실행 ----
const token = loadToken();
const outDir = join(__dirname, 'public', 'fly');
mkdirSync(outDir, { recursive: true });

const pad = (n) => String(n).padStart(4, '0');
const CONCURRENCY = 8;
let done = 0;
let failed = 0;

async function fetchFrame(i) {
  const cam = cameraAt(i);
  const url = buildUrl(cam, token);
  const res = await fetch(url);
  if (!res.ok) {
    failed++;
    if (failed <= 3) console.error(`frame ${i} 실패 ${res.status}: ${(await res.text()).slice(0, 200)}`);
    throw new Error(`frame ${i} ${res.status}`);
  }
  const buf = Buffer.from(await res.arrayBuffer());
  writeFileSync(join(outDir, `frame-${pad(i)}.png`), buf);
  done++;
  if (done % 30 === 0 || done === TOTAL) console.log(`  ${done}/${TOTAL}`);
}

console.log(`정적 플라이 프레임 ${TOTAL}장 생성 (concurrency ${CONCURRENCY})...`);
const queue = Array.from({ length: TOTAL }, (_, i) => i);
async function worker() {
  while (queue.length) {
    const i = queue.shift();
    try { await fetchFrame(i); } catch (e) { /* 위에서 카운트 */ }
  }
}
await Promise.all(Array.from({ length: CONCURRENCY }, worker));

if (failed > 0) throw new Error(`${failed}개 프레임 실패`);

writeFileSync(
  join(__dirname, 'src', 'data-fly.json'),
  JSON.stringify({ width: W, height: H, fps: FPS, frames: TOTAL, distanceKm: haversineKm(BERLIN, BUDAPEST) }, null, 2)
);
console.log('완료: public/fly/*.png, src/data-fly.json');
