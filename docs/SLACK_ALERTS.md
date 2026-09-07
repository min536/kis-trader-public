---
Purpose: Slack 알림 시스템 구조 및 규칙
Read when: 알림 메시지 포맷 변경, 새 알림 추가 전
Do not use for: 주문 로직 자체 변경
---

# Slack Alerts (TL;DR)

`app/notifications/slack.py`에서 담당.

## 알림 채널
- 용도에 따라 Operator, Orders, Bottlenecks, Summary, Path Finder 등으로 분리.
- `SLACK_BOT_TOKEN` 환경변수 필수 (Dry-run 모드 시 미전송).

## 정책 및 규칙
1. **Gross Amount 계산 방식**: 총액(수량 × 가격)을 보여주되 민감한 잔고 정보는 숨김.
2. **Boilerplate 숨김**: "주문 API 호출 직전" 같은 단순 반복 로그는 알림에서 생략/요약.
3. **오류 코드 유지**: 매매 실패 사유나 KIS API의 원본 에러 코드(`EGW00201` 등)는 디버깅을 위해 반드시 알림에 보존.
4. **태그 표시**: Symbol Tag 요약을 알림 하단에 덧붙임 (분류, 특성, 주의사항).
