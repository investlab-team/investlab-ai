# 기업분석 DB 마이그레이션

PostgreSQL 16의 investlab_research database는 AI 전용 역할로 접근합니다. SQLAlchemy 드라이버는 `postgresql+psycopg`(Psycopg 3)이며 URL.create로 환경변수를 조합해 비밀번호를 안전하게 인코딩합니다. 물리 ERD 확정 후 Alembic 초기화와 마이그레이션을 작성합니다. 현재 거래 DB 접근·변경은 없습니다.

UUID·TIMESTAMPTZ·NUMERIC·JSONB를 사용하고 ERD v1.1을 기준으로 제약을 설계합니다. investlab_core와 다른 database이므로 교차 FK는 사용하지 않고 계약 식별자와 검증으로 연결합니다. 벡터 저장 방식은 별도 결정입니다.
