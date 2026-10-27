"""纯逻辑单测（不碰浏览器和真实数据库）。

运行：cd server && python -m unittest discover -s tests -v
"""

import unittest
from dataclasses import replace
from datetime import datetime

import app.modules.douyin_spark.executor as executor
from app.models import SendPlan
from app.modules.douyin_spark.chat_page import classify_flame_state, parse_streak, strip_flame_suffix
from app.modules.douyin_spark.service import plan_runs_on
from app.settings import settings


class ParseStreakTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_streak("🔥 81"), 81)
        self.assertEqual(parse_streak("81"), 81)
        self.assertEqual(parse_streak("81天"), 81)
        self.assertEqual(parse_streak(" 3 "), 3)

    def test_unparseable(self):
        self.assertEqual(parse_streak(""), 0)
        self.assertEqual(parse_streak(None), 0)
        self.assertEqual(parse_streak("🔥"), 0)

    def test_reignition_text_is_not_days(self):
        # "重燃中 2/3" 里的 2/3 不是天数，不能当成 2 天
        self.assertEqual(parse_streak("重燃中 2/3"), 0)
        self.assertEqual(parse_streak("3天后复燃"), 0)


class StripFlameSuffixTest(unittest.TestCase):
    """会话标题剥离 —— 剥错了就确认不了会话，好友发不出去。"""

    def test_normal_and_special_states(self):
        self.assertEqual(strip_flame_suffix("卞秋 708"), "卞秋")
        self.assertEqual(strip_flame_suffix("卞秋 重燃中 2/3"), "卞秋")
        self.assertEqual(strip_flame_suffix("iannnnnnn 6 天后消失"), "iannnnnnn")
        self.assertEqual(strip_flame_suffix("A B 5 天后消失"), "A B")

    def test_nickname_ending_with_digits_is_kept(self):
        # 回归：曾经的循环剥离会把昵称尾部数字也剥掉
        self.assertEqual(strip_flame_suffix("小甜甜123 708"), "小甜甜123")
        self.assertEqual(strip_flame_suffix("用户007 重燃中 2/3"), "用户007")

    def test_names_with_spaces(self):
        self.assertEqual(
            strip_flame_suffix("zan_, 小比方, 海谷, 礿箹龠玥 728"),
            "zan_, 小比方, 海谷, 礿箹龠玥",
        )

    def test_plain_name(self):
        self.assertEqual(strip_flame_suffix("叶秀青"), "叶秀青")
        self.assertEqual(strip_flame_suffix(""), "")


class ClassifyFlameStateTest(unittest.TestCase):
    """状态编码在火焰图标 URL：flame_icon/<款式>/<颜色>_<款式>.png"""

    def test_gray_variants(self):
        base = "https://p3-pc.douyinpic.com/obj/eden-cn/ljhwZthlaukjlkulzlp/flame_icon"
        self.assertEqual(classify_flame_state(f"{base}/normal/gray_normal.png"), "gray")
        self.assertEqual(classify_flame_state(f"{base}/mate/gray_mate.png"), "gray")
        self.assertEqual(classify_flame_state(f"{base}/soulmate/gray_soulmate.png"), "gray")

    def test_active_variants(self):
        base = "https://p3-pc.douyinpic.com/obj/eden-cn/ljhwZthlaukjlkulzlp/flame_icon"
        # 已续态的具体文件名没抓到过样本（扫的时候全是灰的），
        # 规则是：flame_icon 下、文件名不以 gray_ 开头 = normal
        self.assertEqual(classify_flame_state(f"{base}/normal/orange_normal.png"), "normal")
        self.assertEqual(classify_flame_state(f"{base}/normal/light_normal.png"), "normal")

    def test_no_icon(self):
        self.assertEqual(classify_flame_state(""), "")
        self.assertEqual(classify_flame_state("https://other.example.com/avatar.png"), "")


class PlanRunsOnTest(unittest.TestCase):
    def test_weekday_filter(self):
        plan = SendPlan(days_of_week="0")  # 只周一
        self.assertTrue(plan_runs_on(plan, datetime(2026, 10, 26)))   # 周一
        self.assertFalse(plan_runs_on(plan, datetime(2026, 10, 27)))  # 周二

    def test_date_range(self):
        plan = SendPlan(days_of_week="0,1,2,3,4,5,6", date_from="2026-01-01", date_to="2026-01-31")
        self.assertTrue(plan_runs_on(plan, datetime(2026, 1, 15)))
        self.assertFalse(plan_runs_on(plan, datetime(2026, 2, 1)))

    def test_garbage_days_means_every_day(self):
        plan = SendPlan(days_of_week="abc")
        self.assertTrue(plan_runs_on(plan, datetime(2026, 10, 24)))


class QuietHoursTest(unittest.TestCase):
    """跨零点区间 23:00 → 08:00。"""

    def setUp(self):
        self.original = executor.settings
        executor.settings = replace(
            settings, quiet_hours_start="23:00", quiet_hours_end="08:00"
        )

    def tearDown(self):
        executor.settings = self.original

    def test_in_quiet_hours(self):
        self.assertTrue(executor._in_quiet_hours(datetime(2026, 10, 24, 23, 30)))
        self.assertTrue(executor._in_quiet_hours(datetime(2026, 10, 25, 2, 0)))
        self.assertFalse(executor._in_quiet_hours(datetime(2026, 10, 24, 12, 0)))
        self.assertFalse(executor._in_quiet_hours(datetime(2026, 10, 24, 8, 0)))  # 结束时刻不含

    def test_quiet_hours_end_boundary(self):
        # 深夜 23:30 → 结束时刻是次日 08:00
        end = executor._quiet_hours_end(datetime(2026, 10, 24, 23, 30))
        self.assertEqual((end.month, end.day, end.hour, end.minute), (10, 25, 8, 0))
        # 凌晨 06:00 → 结束时刻是当天 08:00
        end = executor._quiet_hours_end(datetime(2026, 10, 24, 6, 0))
        self.assertEqual((end.day, end.hour, end.minute), (24, 8, 0))

    def test_non_crossing_window(self):
        executor.settings = replace(
            settings, quiet_hours_start="12:00", quiet_hours_end="14:00"
        )
        self.assertTrue(executor._in_quiet_hours(datetime(2026, 10, 24, 13, 0)))
        end = executor._quiet_hours_end(datetime(2026, 10, 24, 13, 0))
        self.assertEqual((end.hour, end.minute), (14, 0))


if __name__ == "__main__":
    unittest.main()
