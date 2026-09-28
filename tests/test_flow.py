import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import DomainError, RadioDB


class RadioSchedulingFlowTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = RadioDB(self.path)
        self.p1 = self.db.add_program("早间新闻", "talk", 30, "2026-01-01", "2026-12-31", None, 0, ["华东"])
        self.p2 = self.db.add_program("品牌广告", "ad", 5, "2026-01-01", "2026-12-31", "青柠", 0, ["华东"])

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_complete_replace_playout_and_reconcile_flow(self):
        first = self.db.schedule_slot("2026-09-28", "09:00", self.p1, "华东")
        second = self.db.schedule_slot("2026-09-28", "10:00", self.p2, "华东")
        self.assertEqual("planned", self.db.get_slot(first)["status"])
        replaced = self.db.replace_slot(first, self.p2)
        self.assertEqual("replaced", replaced["status"])
        self.assertEqual(self.p2, replaced["program_id"])
        self.db.record_playout(first, "09:00", 5, self.p1, "临时切回旧内容")
        self.db.record_playout(second, "10:00", 5, self.p2)
        exceptions = self.db.reconcile_date("2026-09-28")
        kinds = {(row["slot_id"], row["kind"]) for row in exceptions}
        self.assertIn((first, "wrong_program"), kinds)

    def test_rejects_overlap_and_unauthorized_region(self):
        self.db.schedule_slot("2026-09-28", "09:00", self.p1, "华东")
        with self.assertRaisesRegex(DomainError, "重叠"):
            self.db.schedule_slot("2026-09-28", "09:15", self.p1, "华东")
        with self.assertRaisesRegex(DomainError, "未授权"):
            self.db.schedule_slot("2026-09-28", "11:00", self.p1, "华北")


class ScheduleLockTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = RadioDB(self.path)
        self.date = "2026-09-28"
        self.region = "华东"
        self.p1 = self.db.add_program("早间新闻", "talk", 30, "2026-01-01", "2026-12-31", None, 0, [self.region])
        self.p2 = self.db.add_program("品牌广告", "ad", 5, "2026-01-01", "2026-12-31", "青柠", 0, [self.region])
        self.slot = self.db.schedule_slot(self.date, "09:00", self.p1, self.region)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_lock_blocks_create_and_replace_but_playout_continues(self):
        event = self.db.lock_schedule(self.date, self.region, "值班员张敏")
        self.assertEqual("locked", event["action"])
        self.assertEqual("值班员张敏", event["operator"])
        status = self.db.lock_status(self.date, self.region)
        self.assertTrue(status["locked"])
        self.assertEqual("值班员张敏", status["locked_by"])
        # 锁定后不能新建该地区节目排期
        with self.assertRaisesRegex(DomainError, "已锁定"):
            self.db.schedule_slot(self.date, "10:00", self.p2, self.region)
        # 锁定后不能替换原安排
        with self.assertRaisesRegex(DomainError, "已锁定"):
            self.db.replace_slot(self.slot, self.p2)
        # 实播登记照常
        log_id = self.db.record_playout(self.slot, "09:01", 30, self.p1)
        self.assertGreater(log_id, 0)

    def test_lock_does_not_affect_other_region_or_date(self):
        north = self.db.add_program("华北节目", "talk", 30, "2026-01-01", "2026-12-31", None, 0, ["华北"])
        self.db.lock_schedule(self.date, self.region, "张敏")
        # 其他地区不受影响
        other = self.db.schedule_slot(self.date, "09:00", north, "华北")
        self.assertGreater(other, 0)
        # 其他日期不受影响
        other_date = self.db.schedule_slot("2026-09-29", "09:00", self.p1, self.region)
        self.assertGreater(other_date, 0)

    def test_cannot_lock_twice_and_operator_required(self):
        self.db.lock_schedule(self.date, self.region, "张敏")
        with self.assertRaisesRegex(DomainError, "已是锁定状态"):
            self.db.lock_schedule(self.date, self.region, "李强")
        with self.assertRaisesRegex(DomainError, "锁定人不能为空"):
            self.db.lock_schedule("2026-10-01", self.region, "  ")

    def test_unlock_requires_reason_and_locked_state(self):
        with self.assertRaisesRegex(DomainError, "未锁定"):
            self.db.unlock_schedule(self.date, self.region, "张敏", "临时调整")
        self.db.lock_schedule(self.date, self.region, "张敏")
        with self.assertRaisesRegex(DomainError, "必须填写原因"):
            self.db.unlock_schedule(self.date, self.region, "张敏", "  ")
        event = self.db.unlock_schedule(self.date, self.region, "李强", "突发直播需要调整")
        self.assertEqual("unlocked", event["action"])
        self.assertEqual("突发直播需要调整", event["reason"])
        status = self.db.lock_status(self.date, self.region)
        self.assertFalse(status["locked"])
        self.assertEqual("李强", status["unlocked_by"])
        self.assertEqual("张敏", status["last_locked_by"])
        # 解锁后可以改排期
        self.db.replace_slot(self.slot, self.p2)

    def test_history_kept_and_relock_possible(self):
        self.db.lock_schedule(self.date, self.region, "张敏")
        self.db.unlock_schedule(self.date, self.region, "李强", "插播紧急通告")
        # 解锁后可以调整安排
        new_slot = self.db.schedule_slot(self.date, "10:00", self.p2, self.region)
        self.assertGreater(new_slot, 0)
        # 重新锁定
        self.db.lock_schedule(self.date, self.region, "王芳")
        status = self.db.lock_status(self.date, self.region)
        self.assertTrue(status["locked"])
        self.assertEqual("王芳", status["locked_by"])
        # 重新锁定后原安排再次不能动
        with self.assertRaisesRegex(DomainError, "已锁定"):
            self.db.replace_slot(new_slot, self.p1)
        events = self.db.lock_events(self.date, self.region)
        actions = [e["action"] for e in events]
        self.assertEqual(["locked", "unlocked", "locked"], actions)
        # 历史按倒序返回：新解锁后还能重新锁定，原锁定记录保留
        reasons = [e["reason"] for e in events]
        self.assertIn("插播紧急通告", reasons)

    def test_snapshot_exposes_lock_states_and_events(self):
        self.db.lock_schedule(self.date, self.region, "张敏")
        snap = self.db.snapshot()
        self.assertIn("lock_states", snap)
        self.assertIn("lock_events", snap)
        state = next(s for s in snap["lock_states"] if s["air_date"] == self.date and s["region"] == self.region)
        self.assertTrue(state["locked"])
        self.assertEqual("张敏", state["locked_by"])


if __name__ == "__main__":
    unittest.main()
