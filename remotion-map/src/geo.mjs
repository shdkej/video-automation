// 공유 지오 유틸 — prepare.mjs(준비 스크립트)와 Remotion 컴포넌트가 함께 사용한다.
// Mapbox Static Images API는 512px 타일 기반 Web Mercator를 쓰므로 그 수식을 그대로 복제한다.

export const TILE_SIZE = 512;

// 위경도 → 정규화 머케이터 좌표(0..1)
export function lonToMercX(lon) {
  return (lon + 180) / 360;
}
export function latToMercY(lat) {
  const rad = (lat * Math.PI) / 180;
  return (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2;
}

// 여러 지점을 W×H 캔버스에 여백(pad, 0~1) 남기고 담는 center(위경도)·zoom 계산
export function fitCenterZoom(points, width, height, pad = 0.62) {
  const xs = points.map((p) => lonToMercX(p[0]));
  const ys = points.map((p) => latToMercY(p[1]));
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const dx = Math.max(maxX - minX, 1e-9);
  const dy = Math.max(maxY - minY, 1e-9);

  // worldSize = TILE_SIZE * 2^zoom. dx*worldSize <= width*pad 를 만족하는 최대 zoom.
  const zoomX = Math.log2((width * pad) / (dx * TILE_SIZE));
  const zoomY = Math.log2((height * pad) / (dy * TILE_SIZE));
  const zoom = Math.min(zoomX, zoomY);

  const centerMercX = (minX + maxX) / 2;
  const centerMercY = (minY + maxY) / 2;
  const centerLon = centerMercX * 360 - 180;
  // 머케이터 Y → 위도 역변환
  const n = Math.PI * (1 - 2 * centerMercY);
  const centerLat = (180 / Math.PI) * Math.atan(Math.sinh(n));

  return { center: [centerLon, centerLat], zoom };
}

// 위경도 → 캔버스 픽셀 (center+zoom 기준, 정적지도와 동일 투영)
export function project(lonlat, center, zoom, width, height) {
  const worldSize = TILE_SIZE * Math.pow(2, zoom);
  const cx = lonToMercX(center[0]) * worldSize;
  const cy = latToMercY(center[1]) * worldSize;
  const px = lonToMercX(lonlat[0]) * worldSize;
  const py = latToMercY(lonlat[1]) * worldSize;
  return {
    x: width / 2 + (px - cx),
    y: height / 2 + (py - cy),
  };
}

// 대원거리(km)
export function haversineKm(a, b) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return Math.round(2 * R * Math.asin(Math.sqrt(h)));
}

// --- 라이브 카메라 플라이용 구면 헬퍼 ---

const toRad = (d) => (d * Math.PI) / 180;
const toDeg = (r) => (r * 180) / Math.PI;

// 대권(great-circle) 보간: a→b 사이를 n+1개 점 [lon,lat]로
export function greatCircleArc(a, b, n = 256) {
  const lon1 = toRad(a[0]), lat1 = toRad(a[1]);
  const lon2 = toRad(b[0]), lat2 = toRad(b[1]);
  const d =
    2 *
    Math.asin(
      Math.sqrt(
        Math.sin((lat2 - lat1) / 2) ** 2 +
          Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2
      )
    );
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n;
    if (d === 0) {
      pts.push([a[0], a[1]]);
      continue;
    }
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
    const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    const lat = Math.atan2(z, Math.sqrt(x * x + y * y));
    const lon = Math.atan2(y, x);
    pts.push([toDeg(lon), toDeg(lat)]);
  }
  return pts;
}

// a에서 b를 바라보는 방위각(deg, 북=0, 시계방향)
export function bearing(a, b) {
  const lat1 = toRad(a[1]), lat2 = toRad(b[1]);
  const dLon = toRad(b[0] - a[0]);
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

// 도시 좌표 [lon, lat]
export const CITIES = {
  berlin: { name: 'Berlin', country: 'Germany', lonlat: [13.405, 52.52] },
  budapest: { name: 'Budapest', country: 'Hungary', lonlat: [19.0402, 47.4979] },
};
