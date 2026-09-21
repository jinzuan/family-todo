"""智能拆分单测：把设计样例表固化成基线。

规则改动必须保证本文件全绿（第 8.1 节风险对策）。
用标准库 unittest，无需额外依赖：
    python -m unittest discover -s tests -v
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.parser import (  # noqa: E402
    chinese_to_int,
    parse_sentence,
    parse_text,
    split_sentences,
)

# 第 2.5 节自检表：(原始句, 标题, 数量, 单位, 分类)
SAMPLE_TABLE = [
    ("给你姥买葱花饼15元", "给你姥买葱花饼", None, None, "shopping"),
    ("娃娃菜", "娃娃菜", None, None, "other"),
    ("青椒3个", "青椒", 3, "个", "shopping"),
    ("贝贝瓜2个", "贝贝瓜", 2, "个", "shopping"),
    ("拿着鸡心果", "拿着鸡心果", None, None, "shopping"),
    ("冬枣", "冬枣", None, None, "other"),
    ("葱", "葱", None, None, "other"),
    ("回来取快递", "取快递", None, None, "errand"),
    ("回来扔垃圾", "扔垃圾", None, None, "errand"),
    ("买煎饼2个", "买煎饼", 2, "个", "shopping"),
]


class TestSampleTable(unittest.TestCase):
    """第 2.5 节样例表逐句基线。"""

    def test_table(self):
        for raw, title, qty, unit, category in SAMPLE_TABLE:
            with self.subTest(raw=raw):
                r = parse_sentence(raw)
                self.assertFalse(r["is_note"], f"{raw} 不应被判为备注")
                self.assertEqual(r["title"], title)
                self.assertEqual(r["qty"], qty)
                self.assertEqual(r["unit"], unit)
                self.assertEqual(r["category"], category)

    def test_price_not_qty(self):
        """「葱花饼15元」是价格，不是 15 份（第 8.1 节最高风险）。"""
        r = parse_sentence("给你姥买葱花饼15元")
        self.assertEqual(r["price"], 15)
        self.assertIsNone(r["qty"])
        self.assertIsNone(r["unit"])

    def test_note(self):
        """「可能后面一段时间之后补上」判为备注，不建任务。"""
        r = parse_sentence("可能后面一段时间之后补上")
        self.assertTrue(r["is_note"])
        self.assertEqual(r["category"], "note")

    def test_note_prefix(self):
        self.assertTrue(parse_sentence("备注：牛奶先不买").get("is_note"))


class TestSplit(unittest.TestCase):
    """分句：标点/换行/顿号，且不拆坏小数与价格。"""

    def test_decimal_protected(self):
        self.assertEqual(split_sentences("买3.5个"), ["买3.5个"])

    def test_thousand_sep(self):
        self.assertEqual(split_sentences("预算1,000元"), ["预算1,000元"])

    def test_punctuation_and_newline(self):
        text = "给你姥买葱花饼15元，娃娃菜，青椒3个\n回来取快递"
        self.assertEqual(
            split_sentences(text),
            ["给你姥买葱花饼15元", "娃娃菜", "青椒3个", "回来取快递"],
        )

    def test_enumeration_comma(self):
        self.assertEqual(split_sentences("葱、姜、蒜"), ["葱", "姜", "蒜"])

    def test_fullwidth_digits(self):
        r = parse_sentence("青椒３个")
        self.assertEqual(r["title"], "青椒")
        self.assertEqual(r["qty"], 3)


class TestChineseNumber(unittest.TestCase):
    """中文数字识别：三个→3、两斤→2、十二→12、二十三→23。"""

    def test_chinese_to_int(self):
        cases = {
            "一": 1, "两": 2, "三": 3, "十": 10, "十二": 12,
            "二十": 20, "二十三": 23, "一百": 100, "一百零五": 105,
            "一千二百三十四": 1234,
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(chinese_to_int(text), expect)

    def test_chinese_qty(self):
        cases = [
            ("青椒三个", "青椒", 3, "个"),
            ("两斤苹果", "苹果", 2, "斤"),
            ("十二个鸡蛋", "鸡蛋", 12, "个"),
            ("二十三个饺子", "饺子", 23, "个"),
            ("买三个苹果", "买苹果", 3, "个"),
        ]
        for raw, title, qty, unit in cases:
            with self.subTest(raw=raw):
                r = parse_sentence(raw)
                self.assertEqual(r["title"], title)
                self.assertEqual(r["qty"], qty)
                self.assertEqual(r["unit"], unit)

    def test_chinese_price(self):
        r = parse_sentence("买葱花饼十五元")
        self.assertEqual(r["price"], 15)
        self.assertIsNone(r["qty"])
        self.assertEqual(r["title"], "买葱花饼")

    def test_arabic_still_works(self):
        r = parse_sentence("青椒3个")
        self.assertEqual(r["qty"], 3)
        self.assertEqual(r["unit"], "个")


class TestParseText(unittest.TestCase):
    """整段管线：items 与 notes 分流，顺序保持。"""

    def test_full_sample(self):
        text = "给你姥买葱花饼15元，娃娃菜，青椒3个，贝贝瓜2个，拿着鸡心果，冬枣，葱，回来取快递，可能后面一段时间之后补上，回来扔垃圾，买煎饼2个"
        out = parse_text(text)
        self.assertEqual(len(out["items"]), 10)
        self.assertEqual(out["notes"], ["可能后面一段时间之后补上"])
        self.assertEqual(out["items"][0]["title"], "给你姥买葱花饼")
        self.assertEqual(out["items"][0]["price"], 15)
        self.assertEqual(out["items"][2]["title"], "青椒")
        self.assertEqual(out["items"][7]["title"], "取快递")
        self.assertEqual(out["items"][9]["title"], "买煎饼")

    def test_no_content_loss(self):
        """每句原文必须出现在 items 的 raw 或 notes 里。"""
        text = "青椒3个，备注：再说，回来扔垃圾"
        out = parse_text(text)
        raws = [i["raw"] for i in out["items"]] + out["notes"]
        for seg in split_sentences(text):
            self.assertIn(seg, raws)


class TestDateRecognition(unittest.TestCase):
    """一句话日期识别：句中日期词切成「内容 + 日期」，无日期默认今天。"""

    TODAY = datetime.date(2026, 9, 12)

    def test_mixed_dates_in_one_sentence(self):
        """「今天…、晚上…、明天…」→ 前两条今天，最后一条明天（明天在句中也要切）。"""
        text = "今天给你姥姥买三个大白馒头、晚上去拿快递、明天去给咱家买点花卷"
        out = parse_text(text, today=self.TODAY)
        self.assertEqual(
            [i["title"] for i in out["items"]],
            ["给你姥姥买大白馒头", "晚上去拿快递", "给咱家买点花卷"],
        )
        self.assertEqual(
            [i["start_date"] for i in out["items"]],
            ["2026-09-12", "2026-09-12", "2026-09-13"],
        )
        self.assertEqual(
            [i["end_date"] for i in out["items"]],
            ["2026-09-12", "2026-09-12", "2026-09-13"],
        )

    def test_day_after_tomorrow(self):
        out = parse_text("后天交房租", today=self.TODAY)
        self.assertEqual(out["items"][0]["title"], "交房租")
        self.assertEqual(out["items"][0]["start_date"], "2026-09-14")

    def test_day_of_month(self):
        out = parse_text("15号交水电费", today=self.TODAY)
        self.assertEqual(out["items"][0]["title"], "交水电费")
        self.assertEqual(out["items"][0]["start_date"], "2026-09-15")

    def test_no_date_defaults_today(self):
        out = parse_text("买牛奶", today=self.TODAY)
        self.assertEqual(out["items"][0]["start_date"], "2026-09-12")
        self.assertEqual(out["items"][0]["end_date"], "2026-09-12")

    def test_month_day(self):
        out = parse_text("9月20号买米", today=self.TODAY)
        self.assertEqual(out["items"][0]["title"], "买米")
        self.assertEqual(out["items"][0]["start_date"], "2026-09-20")

    def test_date_in_middle_of_clause(self):
        """日期词在两段内容中间时也能切分。"""
        out = parse_text("买菜明天买花卷", today=self.TODAY)
        self.assertEqual([i["title"] for i in out["items"]], ["买菜", "买花卷"])
        self.assertEqual(
            [i["start_date"] for i in out["items"]], ["2026-09-12", "2026-09-13"]
        )

    def test_relative_words(self):
        cases = {
            "今天买菜": "2026-09-12",
            "明天买菜": "2026-09-13",
            "后天买菜": "2026-09-14",
            "大后天买菜": "2026-09-15",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    parse_text(text, today=self.TODAY)["items"][0]["start_date"], expect
                )


class TestAutoTag(unittest.TestCase):
    """给XX自动标签：动词+对象；无对象退化为动词；无动词保持分类。"""

    def test_object_verb_tags(self):
        cases = {
            "给姥姥买": "给姥姥买",
            "给姥姥拿": "给姥姥拿",
            "给咱家买点花卷": "给咱家买",
            "给你姥买葱花饼": "给姥姥买",
            "给小明买药": "给小明买",
            "给姥姥带回来": "给姥姥带回来",
        }
        for raw, expect in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_sentence(raw)["tag"], expect)

    def test_same_object_different_verb(self):
        """同对象不同动词必须是两个标签。"""
        self.assertEqual(parse_sentence("给姥姥买")["tag"], "给姥姥买")
        self.assertEqual(parse_sentence("给姥姥拿")["tag"], "给姥姥拿")
        self.assertNotEqual(
            parse_sentence("给姥姥买")["tag"], parse_sentence("给姥姥拿")["tag"]
        )

    def test_no_object_falls_back_to_verb(self):
        cases = {
            "回来买点花卷": "买",
            "取快递": "取",
            "去取快递": "取",
            "交房租": "交",
            "买煎饼2个": "买",
        }
        for raw, expect in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_sentence(raw)["tag"], expect)

    def test_no_verb_keeps_category(self):
        """无动词不臆造标签，交给分类字段（保持分类）。"""
        for raw in ("娃娃菜", "青椒3个"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_sentence(raw)["tag"])

    def test_parse_text_tags(self):
        out = parse_text("给姥姥买药，给姥姥拿快递，回来买点花卷，取快递，娃娃菜")
        self.assertEqual(
            [i["tag"] for i in out["items"]],
            ["给姥姥买", "给姥姥拿", "买", "取", None],
        )


class TestAllocationMerge(unittest.TestCase):
    """分配式识别：'买N个X，[给]A个/咱家B个' 合并成一条，普通列举不动。

    根因：旧算法逐逗号子句独立解析，把「总数句 + 分份句」当成了并列物品。
    """

    def test_total_with_shares_merged(self):
        """用户样例：总数 8 + 两份 4，合并为一条，note 记分配。"""
        out = parse_text("买八个花卷，你姥姥四个咱家四个")
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(item["title"], "买花卷")
        self.assertEqual(item["qty"], 8)  # 不能是 8+4+4=16
        self.assertEqual(item["unit"], "个")
        self.assertEqual(item["note"], "姥姥4+咱家4")
        self.assertEqual(item["raw"], "买八个花卷，你姥姥四个咱家四个")

    def test_no_total_uses_share_sum(self):
        """总数句没写数量：'买馒头，给你三个给我两个' → 总数=份数之和。"""
        out = parse_text("买馒头，给你三个给我两个")
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(item["title"], "买馒头")
        self.assertEqual(item["qty"], 5)
        self.assertEqual(item["unit"], "个")
        self.assertEqual(item["note"], "给你3+给我2")

    def test_enumeration_not_merged(self):
        """纯列举：每句都有独立物品名，保持多条。"""
        out = parse_text("买青椒3个，土豆2个")
        self.assertEqual([i["title"] for i in out["items"]], ["买青椒", "土豆"])
        self.assertEqual([i["qty"] for i in out["items"]], [3, 2])
        self.assertTrue(all(i["note"] is None for i in out["items"]))

    def test_separate_share_clauses_merged(self):
        """分份被逗号拆成多句，仍要合并成一条。"""
        out = parse_text("买八个花卷，你姥姥四个，咱家四个")
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["qty"], 8)
        self.assertEqual(out["items"][0]["note"], "姥姥4+咱家4")

    def test_merge_then_keep_independent_item(self):
        """分配式后面还有独立物品，独立物品不能被吞。"""
        out = parse_text("买八个花卷，你姥姥四个咱家四个，买青椒3个")
        self.assertEqual([i["title"] for i in out["items"]], ["买花卷", "买青椒"])
        self.assertEqual(out["items"][0]["note"], "姥姥4+咱家4")
        self.assertIsNone(out["items"][1]["note"])

    def test_total_larger_than_shares_keeps_total(self):
        """总数大于分出去的份：以总数句为准，不做加法。"""
        out = parse_text("买十个花卷，你姥姥四个咱家四个")
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["qty"], 10)
        self.assertEqual(out["items"][0]["note"], "姥姥4+咱家4")

    def test_unit_mismatch_not_merged(self):
        """分份单位与总数单位不一致 → 不合并（宁可拆开也不乱合）。"""
        out = parse_text("买八斤苹果，给你三个")
        self.assertEqual(len(out["items"]), 2)

    def test_share_sum_over_total_not_merged(self):
        """分出去的总量超过总数 → 判定为误识别，不合并（总数句保持原量）。"""
        out = parse_text("买三个花卷，你姥姥四个咱家四个")
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["items"][0]["title"], "买花卷")
        self.assertEqual(out["items"][0]["qty"], 3)
        self.assertTrue(all(i["note"] is None for i in out["items"]))

    def test_share_with_own_item_not_merged(self):
        """分份句带了独立物品名（给姥姥四个苹果）→ 不是纯分份，不合并。"""
        out = parse_text("买水果，给姥姥四个苹果")
        self.assertEqual(len(out["items"]), 2)

    def test_arabic_and_chinese_mixed(self):
        out = parse_text("买8个花卷，姥姥4个咱家4个")
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["qty"], 8)
        self.assertEqual(out["items"][0]["note"], "姥姥4+咱家4")

    def test_parse_sentence_has_note_key(self):
        """单句解析也带 note 键（默认 None），保持结构稳定。"""
        self.assertIsNone(parse_sentence("青椒3个")["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
