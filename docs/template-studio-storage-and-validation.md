# 템플릿 제작 구현 및 저장 규칙

2026-10-16. 구현 위치: `work/template-studio-development`. 운영 반영은 진행 중인 제작 작업 종료 후 가능하다.

## 저장 형식

JSON과 파일 자산을 함께 보관한다. 기존 PSD와 기존 폴더는 삭제하거나 이동하지 않는다.

```
templates/
  originals/{sha256}/source.psd
  originals/{sha256}/source.json
  channels/{channel_id}/active.json
  channels/{channel_id}/items/{template_id}/draft/
    template.json
    edits.json
    layers/
    fonts/
    reference.png
    preview.png
  channels/{channel_id}/items/{template_id}/versions/{version_id}/
    template.json
    edits.json
    layers/
    fonts/
    preview.png
  channels/{channel_id}/items/{template_id}/versions/{version_id}.json
```

- `template.json`: 가져온 레이어 구조, 원본 속성, 크기, 자산 상대 경로, 재현 한계.
- `edits.json`: 변경한 속성, 레이어 순서, 초안 revision. 분리하여 가져온 레이어 속성과 사용자 변경을 구별한다.
- `source.json`: 원본 파일명, SHA-256, 보관 시각. 파일명 중복으로 덮어쓰지 않는다.
- `active.json`: 채널별 표지/본문/CTA의 활성 버전 기준. 첫 전환 시 기존 승인 템플릿을 그대로 참조하고 디자인을 임의로 변경하지 않는다.
- 버전 메타데이터: 역할, 레이어 매핑, 글자 수 제한, 스냅샷 경로·해시. 확정 버전은 변경하지 않고 새 버전을 만든다.
- 제작 작업은 시작 시 버전을 고정하고 결과 폴더에도 필요한 자산을 보관한다. 기존 제작물 수정은 당시 버전을 사용한다.
- 이전 편집기 초안은 새 저장소에 복사하며, 기존 경로는 그대로 보존한다.

## 구현한 UI

사이드바 독립 진입점, 어두운 편집 화면, 레이어 목록·중앙 캔버스·속성 패널, 확대/축소, 드래그와 방향키 이동, 비율 유지 크기 조절, 원본 비교, 순서/표시/잠금, 그룹 이동, 텍스트/폰트/크기/행간/자간/색상, 구간별 글자 크기·색상, 그림자·외곽선, 이미지 교체·크롭, 도형·그라데이션, 실행 취소/다시 실행, 저장 충돌 차단, 버전 적용/복원.

브러시 기능은 포함하지 않는다. PhotoCraft의 모든 기능이나 Photoshop 렌더링 엔진을 복제하지 않았다. 사진 자르기는 교체한 이미지에 적용하며, 기존 스마트 오브젝트는 래스터 표현을 보존하고 사진 교체를 지원한다. 복잡한 그룹 효과·클리핑 등 미지원 PSD는 설명과 함께 거부한다.

## PhotoCraft 근거와 한계

- 설치 경로: `C:/Program Files/PhotoCraft`.
- REA `open_binary`로 실행 파일의 PE/x86_64 형식을 확인했다.
- REA 상세 네이티브 분석은 Ghidra/IDA가 설정되지 않아 수행하지 못했다.
- 설치된 PhotoCraft CLI가 제공하는 명령 909개를 조회해 `work/template-studio-development/photocraft-commands.json`에 보관했다. 레이어 속성/정렬/잠금, 텍스트 스타일, 가이드, 효과 등 실제 명령을 참고했다.
- 실제 PhotoCraft GUI를 관찰한 결과로 주장하지 않는다. UI는 NOWA 편집 작업에 맞춘 자체 구현이다.

## 검증 결과

- 관련 자동 테스트 93개 통과.
- 실제 PSD 7개, 총 레이어 68개, 편집 가능한 글자 레이어 18개를 가져와 글자 수정 렌더링까지 검증했다.
- 최초 합성의 평균 RGB 오차는 파일별 약 0.18~1.81/255였다. 작은 평균 오차가 모든 글자·효과의 일치를 보장하지 않는다.
- Chromium에서 기존 뉴스 후보 171개, 산출물 28개, 두 채널 표시, 제작 기록/감사 기록 탭, 편집기 진입, 초안 저장/새로고침, CTA 버전 적용을 확인했다. 페이지 JavaScript 오류 0개.
- 조회 요청을 강제로 실패시켜도 기존 데이터가 유지되고, 재연결 후 안내가 사라지는지 확인했다.
- 운영 DB·인증 파일·설정은 이 구현 과정에서 교체하지 않았다. 오래된 8766 미리보기 서버는 종료했다.

## 반영 상태

2026-10-18 운영 코드(`src/cardnews_auto`)에 병합했다. 개발본 이후 운영에 추가된 출력 재개·용량 정리 변경은 유지했다.

- `template_migration.migrate`를 운영 데이터에 한 번 실행했다. 두 채널의 `active.json`이 생성됐고 풋매코 초안 1개가 새 저장소로 이관됐다. 기존 PSD 승인 템플릿은 그대로 유지된다.
- 개발본에서 브라우저 검증용으로 만든 CTA 버전은 테스트 데이터라 옮기지 않았다.
- 저장은 한 번만 한다. 확정 버전은 초안 레이어를, 결과물은 확정 버전 묶음을 하드링크로 참조한다. 원본 PSD(`templates/originals`)만 실제 복사본이다. 원본 경로가 나중에 덮어써져도 보관본이 바뀌지 않아야 하기 때문이다. 레거시 PSD 원본은 `import-existing` 시점에 보관한다.
- 예전 PSD AI 분석 업로드·승인 API는 410으로 닫고 관련 모듈(`template_ai.py`, `template_approval.py`)을 삭제했다.
- 검증: 전체 테스트 271개 통과. 실제 데이터로 overview, 템플릿 제작 채널·카탈로그·초안 미리보기, 두 채널 승인 템플릿 검사를 확인했다. 운영 서버를 띄운 브라우저 검증은 하지 않았다. 대시보드를 켜면 템플릿 제작 화면을 한 번 확인한다.
- PhotoCraft 명령 목록은 `docs/photocraft-commands.json`으로 옮겼다.

사용자가 요청한 별도 기존 에이전트의 OMC 리뷰는 대상 세션을 식별하지 못해 아직 전달하지 않았다. 자체 코드 검토와 자동/브라우저 검증 결과를 외부 에이전트 리뷰로 표현하지 않는다.
