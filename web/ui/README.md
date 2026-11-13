# Reel Room UI

React 19 + TypeScript + Vite 화면입니다. 기본 `/`는 사람이 쓴 대본에서 시작하는 통합 스튜디오이며 `?mode=cut`와 기존 `?job=<id>` 링크는 자동 컷 편집기를 엽니다.

- `src/studio/`: 대본·AI 추천·참고 자료/업로드·이미지 후보·음성·장면/출력. `api.ts`가 서버 계약과 요청을 소유하고 `useStudio.ts`가 저장 직렬화·복구·폴링을 처리합니다.
- `src/screens/`, `src/components/editor/`: 기존 영상 업로드·타임라인·재합성 편집기.
- `src/App.tsx`: 두 제작 흐름의 진입 연결.

```bash
npm ci
npm run dev   # API /fonts는 127.0.0.1:8765 FastAPI로 프록시
npm run build # 타입 검사 + dist 생성
```

프로젝트 데이터는 브라우저가 아니라 FastAPI 서버에 저장합니다. 서버·모델 설정, 테스트와 배포는 [루트 README](../../README.md)와 [Studio 계약](../../docs/STUDIO.md)을 따릅니다. 편집 미리보기는 근사이며 다운로드할 MP4에서 최종 결과를 확인합니다. 사용자 프로젝트는 API로만 읽고 API 키는 프런트에 넣지 않습니다.
