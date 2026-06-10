// 준비 스크립트: .env에서 Mapbox 토큰을 읽어 정적 지도 PNG를 내려받고,
// 컴포넌트가 쓸 center/zoom/도시/거리 메타를 src/data.json으로 저장한다.
// 토큰이 번들/소스에 들어가지 않도록, 이미지는 미리 받아 public/에 둔다.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { fitCenterZoom, haversineKm, CITIES } from './src/geo.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadToken() {
  for (const p of [join(__dirname, '.env'), join(__dirname, '..', '.env')]) {
    try {
      const txt = readFileSync(p, 'utf8');
      const m = txt.match(/^\s*MAPBOX_API_KEY\s*=\s*(.+)\s*$/m);
      if (m) return m[1].trim().replace(/^["']|["']$/g, '');
    } catch {}
  }
  if (process.env.MAPBOX_API_KEY) return process.env.MAPBOX_API_KEY.trim();
  throw new Error('MAPBOX_API_KEY를 .env(현재/상위)에서 찾지 못했습니다.');
}

const WIDTH = 1280;
const HEIGHT = 720;
const STYLE = 'mapbox/dark-v11';

const token = loadToken();
const berlin = CITIES.berlin.lonlat;
const budapest = CITIES.budapest.lonlat;

// 곡선 비행경로의 정점(아치 꼭대기)까지 화면에 담기도록 가상 지점도 fit에 포함
const midLon = (berlin[0] + budapest[0]) / 2;
const midLat = (berlin[1] + budapest[1]) / 2 + 1.6; // 위로 솟는 아치 여유
const { center, zoom } = fitCenterZoom(
  [berlin, budapest, [midLon, midLat]],
  WIDTH,
  HEIGHT,
  0.6
);

const distanceKm = haversineKm(berlin, budapest);

const url =
  `https://api.mapbox.com/styles/v1/${STYLE}/static/` +
  `${center[0].toFixed(5)},${center[1].toFixed(5)},${zoom.toFixed(3)},0/` +
  `${WIDTH}x${HEIGHT}@2x?logo=false&attribution=false&access_token=${token}`;

console.log('정적 지도 요청 중...', { center, zoom: +zoom.toFixed(3), distanceKm });
const res = await fetch(url);
if (!res.ok) {
  throw new Error(`Mapbox Static API 실패 ${res.status}: ${await res.text()}`);
}
const buf = Buffer.from(await res.arrayBuffer());
writeFileSync(join(__dirname, 'public', 'map-bg.png'), buf);
console.log(`map-bg.png 저장 (${(buf.length / 1024).toFixed(0)} KB)`);

const data = {
  width: WIDTH,
  height: HEIGHT,
  center,
  zoom,
  distanceKm,
  cities: CITIES,
};
writeFileSync(join(__dirname, 'src', 'data.json'), JSON.stringify(data, null, 2));
console.log('src/data.json 저장 완료');
