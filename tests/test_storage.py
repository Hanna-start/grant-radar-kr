"""SQLite 저장소와 수집(변경/마감 감지) 테스트. 실제 API를 호출하지 않는다."""

from datetime import datetime, timezone

from grant_radar.normalization.kstartup import normalize_announcement
from grant_radar.services.ingestion import KST, ingest_page, is_closed
from grant_radar.storage.sqlite import AnnouncementStore, content_hash

from tests.test_normalization import full_item

SEEN_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def make_store():
    return AnnouncementStore(":memory:")


def ann(**overrides):
    return normalize_announcement(full_item(**overrides), SEEN_AT)


class TestUpsert:
    def test_first_time_is_new(self):
        with make_store() as store:
            assert store.upsert(ann(), SEEN_AT) == "NEW"
            assert store.count() == 1

    def test_same_content_twice_is_unchanged_and_not_duplicated(self):
        with make_store() as store:
            store.upsert(ann(), SEEN_AT)
            assert store.upsert(ann(), LATER) == "UNCHANGED"
            assert store.count() == 1
            row = store.get("kstartup", "900001")
            assert row["first_seen_at"] == SEEN_AT.isoformat()
            assert row["last_seen_at"] == LATER.isoformat()
            assert row["last_changed_at"] is None
            assert row["previous_hash"] is None

    def test_major_field_change_is_updated_with_history(self):
        with make_store() as store:
            store.upsert(ann(), SEEN_AT)
            old_hash = store.get("kstartup", "900001")["content_hash"]
            changed = ann(pbanc_rcpt_end_dt="20260815")  # 접수 종료일 변경
            assert store.upsert(changed, LATER) == "UPDATED"
            row = store.get("kstartup", "900001")
            assert row["previous_hash"] == old_hash
            assert row["content_hash"] != old_hash
            assert row["last_changed_at"] == LATER.isoformat()
            assert store.count() == 1

    def test_minor_field_change_is_unchanged(self):
        # 담당자 연락처는 지시서 12절의 해시 대상이 아니므로 UNCHANGED
        with make_store() as store:
            store.upsert(ann(), SEEN_AT)
            assert store.upsert(ann(prch_cnpl_no="9999"), LATER) == "UNCHANGED"

    def test_missing_source_id_is_unknown_and_not_stored(self):
        item = full_item()
        del item["pbanc_sn"]
        broken = normalize_announcement(item, SEEN_AT)
        with make_store() as store:
            assert store.upsert(broken, SEEN_AT) == "UNKNOWN"
            assert store.count() == 0

    def test_persists_across_reopen(self, tmp_path):
        db = tmp_path / "test.db"
        with AnnouncementStore(db) as store:
            store.upsert(ann(), SEEN_AT)
        with AnnouncementStore(db) as store:
            assert store.count() == 1
            assert store.upsert(ann(), LATER) == "UNCHANGED"

    def test_raw_json_preserved_for_reprocessing(self):
        with make_store() as store:
            store.upsert(ann(), SEEN_AT)
            row = store.get("kstartup", "900001")
            assert '"pbanc_sn": 900001' in row["raw_json"]


class TestContentHash:
    def test_deterministic_and_independent_of_fetched_at(self):
        a = normalize_announcement(full_item(), SEEN_AT)
        b = normalize_announcement(full_item(), LATER)
        assert content_hash(a) == content_hash(b)

    def test_each_major_field_affects_hash(self):
        base = content_hash(ann())
        assert content_hash(ann(biz_pbanc_nm="다른 제목")) != base
        assert content_hash(ann(supt_regin="부산")) != base
        assert content_hash(ann(pbanc_rcpt_bgng_dt="20260702")) != base
        assert content_hash(ann(rcrt_prgs_yn="N")) != base
        assert content_hash(ann(aply_excl_trgt_ctnt="다른 제외 조건")) != base


class TestClosedPolicy:
    def as_of(self, y, m, d, hour=10):
        return datetime(y, m, d, hour, 0, 0, tzinfo=KST)

    def test_recruitment_n_is_closed(self):
        assert is_closed(ann(rcrt_prgs_yn="N"), self.as_of(2026, 7, 1)) is True

    def test_end_date_passed_is_closed(self):
        # 종료일: 20260731 → 8월 1일(KST)부터 마감
        assert is_closed(ann(), self.as_of(2026, 8, 1)) is True

    def test_end_date_today_is_still_open(self):
        # 종료일 당일은 하루가 끝날 때까지 마감이 아니다 (Asia/Seoul 기준)
        assert is_closed(ann(), self.as_of(2026, 7, 31, hour=23)) is False

    def test_utc_time_is_converted_to_kst(self):
        # UTC 7/31 16:00 = KST 8/1 01:00 → 마감
        as_of = datetime(2026, 7, 31, 16, 0, 0, tzinfo=timezone.utc)
        assert is_closed(ann(), as_of) is True

    def test_unparseable_end_date_is_not_closed(self):
        assert is_closed(ann(pbanc_rcpt_end_dt="미정"), self.as_of(2026, 12, 31)) is False

    def test_no_information_is_not_closed(self):
        target = ann(rcrt_prgs_yn=None, pbanc_rcpt_end_dt=None)
        assert is_closed(target, self.as_of(2026, 12, 31)) is False

    def test_recruitment_y_but_date_passed_is_closed(self):
        # 모집 여부 필드가 갱신되지 않아도 종료일 경과는 마감으로 본다
        assert is_closed(ann(rcrt_prgs_yn="Y"), self.as_of(2026, 9, 1)) is True


class TestIngestPage:
    def envelope(self, *items):
        return {
            "currentCount": len(items),
            "matchCount": 100,
            "page": 1,
            "perPage": len(items),
            "totalCount": 100,
            "data": list(items),
        }

    def test_first_run_all_new_second_run_unchanged(self):
        body = self.envelope(full_item(), full_item(pbanc_sn=900002))
        with make_store() as store:
            first = ingest_page(store, body, fetched_at=SEEN_AT, as_of=SEEN_AT)
            assert [o.change for o in first] == ["NEW", "NEW"]
            second = ingest_page(store, body, fetched_at=LATER, as_of=LATER)
            assert [o.change for o in second] == ["UNCHANGED", "UNCHANGED"]
            assert store.count() == 2

    def test_closed_and_display_status(self):
        body = self.envelope(
            full_item(),  # 종료일 20260731
            full_item(pbanc_sn=900003, rcrt_prgs_yn="N"),
        )
        as_of = datetime(2026, 7, 21, 10, 0, 0, tzinfo=KST)
        with make_store() as store:
            outcomes = ingest_page(store, body, as_of=as_of)
        assert [o.closed for o in outcomes] == [False, True]
        assert outcomes[0].display_status == "NEW"
        assert outcomes[1].display_status == "CLOSED"

    def test_unknown_item_kept_in_outcomes(self):
        item = full_item()
        del item["pbanc_sn"]
        with make_store() as store:
            outcomes = ingest_page(store, self.envelope(item), as_of=SEEN_AT)
            assert [o.change for o in outcomes] == ["UNKNOWN"]
            assert store.count() == 0
