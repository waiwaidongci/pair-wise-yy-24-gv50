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

    def test_locked_schedule_blocks_create_and_replace_but_allows_playout(self):
        slot = self.db.schedule_slot("2026-09-28", "09:00", self.p1, "华东")
        lock = self.db.lock_schedule("2026-09-28", "华东", "值班员小王")
        self.assertTrue(lock["locked"])
        self.assertEqual("值班员小王", lock["operator"])
        self.assertIn("locked_at", lock)
        # 已审核安排锁定：新建、替换都被直接拒绝
        with self.assertRaisesRegex(DomainError, "节目单已锁定"):
            self.db.schedule_slot("2026-09-28", "10:00", self.p2, "华东")
        with self.assertRaisesRegex(DomainError, "节目单已锁定"):
            self.db.replace_slot(slot, self.p2)
        # 其他地区不受影响
        other = self.db.add_program("华北节目", "music", 30, "2026-01-01", "2026-12-31", None, 0, ["华北"])
        self.assertTrue(self.db.schedule_slot("2026-09-28", "10:00", other, "华北"))
        # 实播登记照常
        log_id = self.db.record_playout(slot, "09:00", 30, self.p1)
        self.assertTrue(log_id)
        # 快照带锁定状态
        snap = self.db.snapshot()
        locked_slots = [s for s in snap["slots"] if s["air_date"] == "2026-09-28" and s["region"] == "华东"]
        self.assertTrue(all(s["locked"] for s in locked_slots))
        self.assertFalse(any(s["locked"] for s in snap["slots"] if s["region"] == "华北"))
        self.assertEqual({("2026-09-28", "华东")}, {(l["air_date"], l["region"]) for l in snap["locks"]})

    def test_unlock_requires_reason_and_history_is_kept_across_relock(self):
        self.db.schedule_slot("2026-09-28", "09:00", self.p1, "华东")
        self.db.lock_schedule("2026-09-28", "华东", "小王")
        # 重复锁定被拒绝
        with self.assertRaisesRegex(DomainError, "已经是锁定状态"):
            self.db.lock_schedule("2026-09-28", "华东", "小王")
        # 解锁必须填原因和操作人
        with self.assertRaisesRegex(DomainError, "原因"):
            self.db.unlock_schedule("2026-09-28", "华东", "小王", "")
        with self.assertRaisesRegex(DomainError, "解锁人"):
            self.db.unlock_schedule("2026-09-28", "华东", "", "临时插播")
        self.db.unlock_schedule("2026-09-28", "华东", "值班长老李", "突发新闻需要插播")
        self.assertIsNone(self.db.get_lock("2026-09-28", "华东"))
        # 未锁定时无需再解锁
        with self.assertRaisesRegex(DomainError, "未锁定"):
            self.db.unlock_schedule("2026-09-28", "华东", "老李", "再试一次")
        # 解锁后可以改动
        slot = self.db.schedule_slot("2026-09-28", "10:00", self.p2, "华东")
        # 历史记录保留，并能重新锁定
        history = self.db.list_lock_history()
        self.assertEqual(["unlocked", "locked"], [row["action"] for row in history])
        self.assertEqual("突发新闻需要插播", history[0]["reason"])
        relock = self.db.lock_schedule("2026-09-28", "华东", "小王")
        self.assertTrue(relock["locked"])
        with self.assertRaisesRegex(DomainError, "节目单已锁定"):
            self.db.replace_slot(slot, self.p1)
        actions = [row["action"] for row in self.db.list_lock_history()]
        self.assertEqual(["locked", "unlocked", "locked"], actions)

    def test_lock_validates_date_region_operator(self):
        with self.assertRaisesRegex(DomainError, "YYYY-MM-DD"):
            self.db.lock_schedule("09-28", "华东", "小王")
        with self.assertRaisesRegex(DomainError, "地区"):
            self.db.lock_schedule("2026-09-28", "  ", "小王")
        with self.assertRaisesRegex(DomainError, "锁定人"):
            self.db.lock_schedule("2026-09-28", "华东", " ")


if __name__ == "__main__":
    unittest.main()
