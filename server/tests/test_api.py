"""接口冒烟测试：join → parse → todos 增删勾 → 子待办父子完成 → 埋点 → WS。

用临时 DB，避免污染 data/。依赖 fastapi TestClient（需 httpx）。
运行：python -m unittest discover -s tests -v
"""

import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 app 之前指定临时库
_TMP_DIR = tempfile.mkdtemp(prefix="family_todo_test_")
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ["STATS_RETENTION_DAYS"] = "90"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        with db.get_conn() as conn:
            family_id = auth.create_family(conn, "测试家庭")
            cls.family_id = family_id
            cls.family_token = auth.create_family_token(conn, family_id)
            joined = auth.join(conn, cls.family_token, device_name="测试手机")
            # 发布端设备：用于验证撤回只能撤自己发布的
            publisher = auth.join(
                conn, cls.family_token, device_name="发布手机", role="publisher"
            )
            # 另一个家庭：用于校验家庭间数据隔离
            other_id = auth.create_family(conn, "别家")
            cls.other_family_id = other_id
            other_token = auth.create_family_token(conn, other_id)
            other_joined = auth.join(conn, other_token, device_name="别家手机")
        cls.device_token = joined["device_token"]
        cls.headers = {"Authorization": f"Bearer {cls.device_token}"}
        cls.publisher_headers = {"Authorization": f"Bearer {publisher['device_token']}"}
        cls.other_headers = {"Authorization": f"Bearer {other_joined['device_token']}"}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ---- 认证 ----

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    # ---- App 自更新 ----

    def test_app_latest_json(self):
        r = self.client.get("/api/app/latest.json")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in (
            "version_code",
            "version_name",
            "apk_url",
            "changelog",
            "force",
            "min_supported_version_code",
        ):
            self.assertIn(key, data)
        self.assertIsInstance(data["version_code"], int)
        self.assertIsInstance(data["force"], bool)
        self.assertIsInstance(data["min_supported_version_code"], int)
        self.assertTrue(data["version_name"])
        self.assertTrue(data["apk_url"])

    def test_app_latest_json_root_alias(self):
        # /latest.json 与 /api/app/latest.json 返回同一份版本信息
        api = self.client.get("/api/app/latest.json").json()
        root = self.client.get("/latest.json")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.json(), api)

    def test_app_latest_json_override_file(self):
        # APP_LATEST_JSON 指向的覆盖文件优先于环境变量
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix="ft_latest_", suffix=".json")
        os.close(fd)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write('{"version_code": 99, "version_name": "9.9.9", "force": true}')
        old_env = os.environ.get("APP_LATEST_JSON")
        os.environ["APP_LATEST_JSON"] = tmp_path
        try:
            data = self.client.get("/api/app/latest.json").json()
            self.assertEqual(data["version_code"], 99)
            self.assertEqual(data["version_name"], "9.9.9")
            self.assertTrue(data["force"])
        finally:
            if old_env is None:
                os.environ.pop("APP_LATEST_JSON", None)
            else:
                os.environ["APP_LATEST_JSON"] = old_env
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


    def test_parse_requires_auth(self):
        self.assertEqual(self.client.post("/api/parse", json={"text": "葱"}).status_code, 401)

    def test_join(self):
        r = self.client.post(
            "/api/auth/join",
            json={"family_token": self.family_token, "device_name": "新手机"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["family_id"], self.family_id)
        self.assertTrue(body["device_token"])

    def test_join_bad_token(self):
        r = self.client.post("/api/auth/join", json={"family_token": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_me(self):
        r = self.client.get("/api/me", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["family_id"], self.family_id)
        self.assertEqual(r.json()["device_name"], "测试手机")

    def test_todo_creator_name(self):
        created = self.client.post(
            "/api/todos", json={"title": "谁发的"}, headers=self.headers
        ).json()
        self.assertEqual(created["creator_name"], "测试手机")
        listed = self.client.get("/api/todos?include_done=1&flat=1", headers=self.headers).json()
        item = next(i for i in listed["items"] if i["id"] == created["id"])
        self.assertEqual(item["creator_name"], "测试手机")

    def test_rename_device(self):
        joined = self.client.post(
            "/api/auth/join",
            json={"family_token": self.family_token, "device_name": "旧名"},
        ).json()
        headers = {"Authorization": f"Bearer {joined['device_token']}"}
        r = self.client.patch("/api/me", json={"device_name": "妈妈的手机"}, headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["device_name"], "妈妈的手机")
        # 改名后新发的待办带新名，旧待办不受影响
        created = self.client.post(
            "/api/todos", json={"title": "改名后"}, headers=headers
        ).json()
        self.assertEqual(created["creator_name"], "妈妈的手机")

    def test_rename_device_empty_400(self):
        r = self.client.patch("/api/me", json={"device_name": "   "}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    # ---- 拆分 ----

    def test_parse(self):
        r = self.client.post(
            "/api/parse",
            json={"text": "给你姥买葱花饼15元，青椒3个，可能后面补上"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["items"][0]["price"], 15)
        self.assertEqual(body["items"][1]["qty"], 3)
        self.assertEqual(body["notes"], ["可能后面补上"])

    def test_parse_dates_by_sentence(self):
        """一句话发布：按句中日期词归日，today 由客户端传入。"""
        r = self.client.post(
            "/api/parse",
            json={
                "text": "今天给你姥姥买三个大白馒头、晚上去拿快递、明天去给咱家买点花卷",
                "today": "2026-09-12",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(
            [i["start_date"] for i in items],
            ["2026-09-12", "2026-09-12", "2026-09-13"],
        )

    def test_parse_bad_today_400(self):
        r = self.client.post(
            "/api/parse",
            json={"text": "买菜", "today": "2026/09/12"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 400)

    def test_parse_allocation_merged_with_note(self):
        """分配式合并：响应只回一条，总数不重复计，note 透传。"""
        r = self.client.post(
            "/api/parse",
            json={"text": "买八个花卷，你姥姥四个咱家四个"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 8)
        self.assertEqual(items[0]["note"], "姥姥4+咱家4")

    # ---- 待办主流程 ----

    def test_todo_lifecycle(self):
        r = self.client.post(
            "/api/todos/bulk",
            json={
                "source": "oneline",
                "items": [
                    {"title": "青椒", "qty": 3, "unit": "个", "category": "shopping"},
                    {"title": "取快递", "category": "errand"},
                ],
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 201)
        created = r.json()["items"]
        self.assertEqual(len(created), 2)

        r = self.client.get("/api/todos", headers=self.headers)
        listed_ids = {i["id"] for i in r.json()["items"]}
        for item in created:
            self.assertIn(item["id"], listed_ids)

        todo_id = created[0]["id"]
        r = self.client.post(f"/api/todos/{todo_id}/complete", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], 1)

        r = self.client.post(f"/api/todos/{todo_id}/uncomplete", headers=self.headers)
        self.assertEqual(r.json()["status"], 0)

        r = self.client.patch(
            f"/api/todos/{todo_id}", json={"title": "青椒（改）"}, headers=self.headers
        )
        self.assertEqual(r.json()["title"], "青椒（改）")

        r = self.client.delete(f"/api/todos/{todo_id}", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIn(todo_id, r.json()["deleted"])

    def test_single_create_and_404(self):
        r = self.client.post(
            "/api/todos", json={"title": "扔垃圾", "category": "errand"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 201)
        self.assertIn(
            self.client.get("/api/todos/nope", headers=self.headers).status_code,
            (404, 405),
        )
        self.assertEqual(
            self.client.post("/api/todos/nope/complete", headers=self.headers).status_code, 404
        )

    def test_due_date_default_today(self):
        import datetime

        r = self.client.post(
            "/api/todos", json={"title": "默认今天"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["due_date"], datetime.date.today().isoformat())

    def test_due_date_filter(self):
        created = self.client.post(
            "/api/todos",
            json={"title": "未来事", "due_date": "2030-01-01"},
            headers=self.headers,
        ).json()
        self.assertEqual(created["due_date"], "2030-01-01")
        tid = created["id"]

        # 指定日期能查到
        on_day = self.client.get(
            "/api/todos?date=2030-01-01&include_done=1&flat=1", headers=self.headers
        ).json()
        self.assertTrue(any(i["id"] == tid for i in on_day["items"]))

        # 默认（今天）不堆历史待办，查不到
        today = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()
        self.assertFalse(any(i["id"] == tid for i in today["items"]))

        # date=all 不限日期
        all_days = self.client.get(
            "/api/todos?date=all&include_done=1&flat=1", headers=self.headers
        ).json()
        self.assertTrue(any(i["id"] == tid for i in all_days["items"]))

    def test_remove_soft_unfinished(self):
        created = self.client.post(
            "/api/todos", json={"title": "待软删"}, headers=self.headers
        ).json()
        tid = created["id"]
        r = self.client.post(f"/api/todos/{tid}/remove", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status_detail"], "unfinished")
        self.assertEqual(r.json()["status"], 1)
        # 不真删：仍能在当天列表（含已处理）里查到
        got = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()
        item = next(i for i in got["items"] if i["id"] == tid)
        self.assertEqual(item["status_detail"], "unfinished")

    def test_subtask_parent_auto_complete(self):
        parent = self.client.post(
            "/api/todos", json={"title": "买年货", "category": "shopping"}, headers=self.headers
        ).json()
        pid = parent["id"]
        s1 = self.client.post(
            f"/api/todos/{pid}/subtasks", json={"title": "瓜子"}, headers=self.headers
        ).json()
        s2 = self.client.post(
            f"/api/todos/{pid}/subtasks", json={"title": "糖"}, headers=self.headers
        ).json()

        r = self.client.get(f"/api/todos/{pid}/subtasks", headers=self.headers)
        self.assertEqual(r.json()["count"], 2)

        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)
        parent_now = self.client.get("/api/todos?include_done=1", headers=self.headers).json()
        # 还有子任务没完成，父仍未完成
        p = next(i for i in parent_now["items"] if i["id"] == pid)
        self.assertEqual(p["status"], 0)

        self.client.post(f"/api/todos/{s2['id']}/complete", headers=self.headers)
        parent_now = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()
        p = next(i for i in parent_now["items"] if i["id"] == pid)
        self.assertEqual(p["status"], 1)

        # 取消一个子任务，父回到未完成
        self.client.post(f"/api/todos/{s1['id']}/uncomplete", headers=self.headers)
        parent_now = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()
        p = next(i for i in parent_now["items"] if i["id"] == pid)
        self.assertEqual(p["status"], 0)
        self.assertEqual(p["status_detail"], "pending")

    # ---- 父子完成新规则（用户 09-12 定稿）----

    def _make_parent_with_subtasks(self, title, sub_titles):
        parent = self.client.post(
            "/api/todos", json={"title": title, "category": "shopping"}, headers=self.headers
        ).json()
        pid = parent["id"]
        children = [
            self.client.post(
                f"/api/todos/{pid}/subtasks", json={"title": sub}, headers=self.headers
            ).json()
            for sub in sub_titles
        ]
        return pid, children

    def _find(self, todo_id):
        body = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()
        return next(i for i in body["items"] if i["id"] == todo_id)

    def test_parent_complete_needs_decision(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("决策父", ["甲", "乙"])
        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)

        r = self.client.post(f"/api/todos/{pid}/complete", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["needs_decision"])
        self.assertEqual(set(body["actions"]), {"complete", "ignore_rest", "cancel"})
        self.assertEqual(body["pending_count"], 1)
        # 未做选择前父不完成，待办子项保持原状
        self.assertEqual(self._find(pid)["status"], 0)
        self.assertEqual(self._find(s2["id"])["status"], 0)

    def test_parent_complete_force(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("强制父", ["丙", "丁"])
        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)
        r = self.client.post(
            f"/api/todos/{pid}/complete", json={"decision": "complete"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], 1)
        self.assertEqual(r.json()["status_detail"], "completed")
        # 强制完成时，所有待办子项也一并标为已完成
        self.assertEqual(self._find(s1["id"])["status_detail"], "completed")
        self.assertEqual(self._find(s2["id"])["status_detail"], "completed")

    def test_parent_complete_ignore_rest(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("忽略父", ["戊", "己"])
        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)
        r = self.client.post(
            f"/api/todos/{pid}/complete", json={"decision": "ignore_rest"}, headers=self.headers
        )
        self.assertEqual(r.json()["status"], 1)
        self.assertEqual(r.json()["status_detail"], "completed")
        ignored = self._find(s2["id"])
        self.assertEqual(ignored["status"], 1)
        self.assertEqual(ignored["status_detail"], "ignored")

    def test_parent_complete_cancel(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("取消父", ["庚", "辛"])
        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)
        r = self.client.post(
            f"/api/todos/{pid}/complete", json={"decision": "cancel"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], 0)
        self.assertEqual(r.json()["status_detail"], "pending")

    def test_complete_bad_decision(self):
        pid, _ = self._make_parent_with_subtasks("坏选项父", ["壬"])
        r = self.client.post(
            f"/api/todos/{pid}/complete", json={"decision": "bogus"}, headers=self.headers
        )
        self.assertEqual(r.status_code, 400)

    def test_ignore_and_cancel_auto_complete(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("忽略取消父", ["癸", "子"])
        r = self.client.post(f"/api/todos/{s1['id']}/ignore", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status_detail"], "ignored")
        # 还有一个待办子项，父仍未完成
        self.assertEqual(self._find(pid)["status"], 0)

        r = self.client.post(f"/api/todos/{s2['id']}/cancel", headers=self.headers)
        self.assertEqual(r.json()["status_detail"], "cancelled")
        # 子集全部非待办 → 父自动完成
        self.assertEqual(self._find(pid)["status"], 1)
        self.assertEqual(self._find(pid)["status_detail"], "completed")

    # ---- 埋点 ----

    def test_stats(self):
        # 先制造一次 oneline 发布，避免依赖测试执行顺序
        self.client.post(
            "/api/todos/bulk",
            json={"source": "oneline", "items": [{"title": "统计用"}, {"title": "统计用2"}]},
            headers=self.headers,
        )
        r = self.client.post(
            "/api/stats/event",
            json={"metric": "subtask_expand_count", "value": 2},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/stats/event", json={"metric": "bogus"}, headers=self.headers
            ).status_code,
            400,
        )
        s = self.client.get("/api/stats/summary", headers=self.headers).json()
        self.assertGreaterEqual(s["metrics"]["publish_oneline_count"], 1)
        self.assertGreaterEqual(s["metrics"]["oneline_items_total"], 2)
        self.assertGreaterEqual(s["metrics"]["subtask_expand_count"], 2)

    # ---- 分配式附注（todo.note）：评论已下线，note 字段作为分配附注保留 ----

    def test_allocation_note_persisted(self):
        """拆分算出的分配附注（如「姥姥4+咱家4」）发布后仍随待办保留。"""
        r = self.client.post(
            "/api/todos/bulk",
            json={
                "source": "oneline",
                "items": [{"title": "花卷", "qty": 8, "note": "姥姥4+咱家4"}],
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 201)
        created = r.json()["items"][0]
        self.assertEqual(created["note"], "姥姥4+咱家4")
        listed = self.client.get(
            "/api/todos?include_done=1&flat=1", headers=self.headers
        ).json()["items"]
        item = next(i for i in listed if i["id"] == created["id"])
        self.assertEqual(item["note"], "姥姥4+咱家4")

    def test_comment_table_removed(self):
        """评论功能彻底下线：库里不应再有 todo_comment 表。"""
        with db.get_conn() as conn:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("todo_comment", names)

    # ---- V3 日期窗口 / 持久待办 / 清空（评审锁定语义）----

    def _create(self, **kwargs):
        body = {"title": "V3"}
        body.update(kwargs)
        r = self.client.post("/api/todos", json=body, headers=self.headers)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _ids(self, query):
        body = self.client.get("/api/todos?" + query, headers=self.headers).json()
        return {i["id"] for i in body["items"]}

    def test_todo_dates_dots(self):
        today = datetime.date.today()
        d_today = today.isoformat()
        d_future = (today + datetime.timedelta(days=17)).isoformat()
        d_past = (today - datetime.timedelta(days=17)).isoformat()
        r_start = (today + datetime.timedelta(days=23)).isoformat()
        r_mid = (today + datetime.timedelta(days=24)).isoformat()
        r_end = (today + datetime.timedelta(days=25)).isoformat()
        d_done = (today + datetime.timedelta(days=27)).isoformat()
        d_deleted = (today + datetime.timedelta(days=29)).isoformat()

        self._create(title="红点今天", start_date=d_today, end_date=d_today)
        self._create(title="红点未来", start_date=d_future, end_date=d_future)
        self._create(title="红点过去", start_date=d_past, end_date=d_past)
        self._create(title="红点区间", start_date=r_start, end_date=r_end)
        self._create(title="红点持久", persistent=True)
        done = self._create(title="红点已完成", start_date=d_done, end_date=d_done)
        self.client.post(f"/api/todos/{done['id']}/complete", headers=self.headers)
        deleted = self._create(title="红点已删", start_date=d_deleted, end_date=d_deleted)
        self.client.post(f"/api/todos/{deleted['id']}/remove", headers=self.headers)

        body = self.client.get("/api/todo-dates", headers=self.headers).json()
        dates = body["dates"]
        as_set = set(dates)
        self.assertEqual(body["count"], len(dates))
        self.assertEqual(dates, sorted(dates))
        # 今天 / 未来单日有红点
        self.assertIn(d_today, as_set)
        self.assertIn(d_future, as_set)
        # 区间待办按自然日逐天展开，含中间那天
        self.assertIn(r_start, as_set)
        self.assertIn(r_mid, as_set)
        self.assertIn(r_end, as_set)
        # 过去不标；持久无日期；已完成 / 软删不标
        self.assertNotIn(d_past, as_set)
        self.assertNotIn(d_done, as_set)
        self.assertNotIn(d_deleted, as_set)

    def test_todo_dates_family_isolated_and_requires_auth(self):
        self.assertEqual(self.client.get("/api/todo-dates").status_code, 401)
        other_day = (datetime.date.today() + datetime.timedelta(days=31)).isoformat()
        self.client.post(
            "/api/todos",
            json={"title": "别家红点", "start_date": other_day, "end_date": other_day},
            headers=self.other_headers,
        )
        body = self.client.get("/api/todo-dates", headers=self.headers).json()
        self.assertNotIn(other_day, body["dates"])

    def test_v3_window_today_and_specific_day(self):
        d1 = datetime.date.today().isoformat()
        d2 = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        d3 = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        todo = self._create(title="区间待办", start_date=d1, end_date=d2)
        self.assertEqual(todo["start_date"], d1)
        self.assertEqual(todo["end_date"], d2)
        self.assertIn(todo["id"], self._ids("date=today&include_done=1&flat=1"))
        self.assertIn(todo["id"], self._ids("date=%s&include_done=1&flat=1" % d2))
        self.assertNotIn(todo["id"], self._ids("date=%s&include_done=1&flat=1" % d3))

    def test_v3_future_and_all(self):
        d5 = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        todo = self._create(title="未来V3", start_date=d5, end_date=d5)
        self.assertIn(todo["id"], self._ids("date=future&include_done=1&flat=1"))
        self.assertNotIn(todo["id"], self._ids("date=today&include_done=1&flat=1"))
        self.assertIn(todo["id"], self._ids("date=all&include_done=1&flat=1"))

    def test_v3_overdue_forces_pending(self):
        d = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        todo = self._create(title="过期V3", start_date=d, end_date=d)
        self.assertIn(todo["id"], self._ids("date=overdue&flat=1"))
        self.client.post(f"/api/todos/{todo['id']}/complete", headers=self.headers)
        # 完成后 overdue 强制未完，不再出现，即便带 include_done
        self.assertNotIn(todo["id"], self._ids("date=overdue&include_done=1&flat=1"))

    def test_done_mode_only_completed(self):
        done = self._create(title="筛选已完成")
        pending = self._create(title="筛选待办")
        self.client.post(f"/api/todos/{done['id']}/complete", headers=self.headers)

        # date=done 只出已处理项，不出待办
        ids = self._ids("date=done&flat=1")
        self.assertIn(done["id"], ids)
        self.assertNotIn(pending["id"], ids)
        # 不带 include_done 也强制只出已完成
        forced = self._ids("date=done")
        self.assertIn(done["id"], forced)
        self.assertNotIn(pending["id"], forced)

        # status=1 的其它已了结项（忽略）同样属于「已完成」筛选
        ignored = self._create(title="筛选忽略")
        self.client.post(f"/api/todos/{ignored['id']}/ignore", headers=self.headers)
        self.assertIn(ignored["id"], self._ids("date=done&flat=1"))
        # 但它不会污染未完成的今天视图
        self.assertNotIn(ignored["id"], self._ids("date=today&flat=1"))

    def test_done_mode_includes_soft_deleted(self):
        # 已完成后被软删（撤下 / 清空）的历史项，仍应能在 done 筛选里查看
        deleted = self._create(title="完成后删除")
        self.client.post(f"/api/todos/{deleted['id']}/complete", headers=self.headers)
        self.client.delete(f"/api/todos/{deleted['id']}", headers=self.headers)
        self.assertIn(deleted["id"], self._ids("date=done&flat=1"))
        # 非平铺的 done 也应包含
        self.assertIn(deleted["id"], self._ids("date=done"))
        # 正常列表仍排除软删
        self.assertNotIn(deleted["id"], self._ids("date=today&include_done=1&flat=1"))

        # 已完成 + 发布者撤回（软删）→ done 仍可见
        withdrawn = self._pub_create(title="完成后撤回")
        self.client.post(
            f"/api/todos/{withdrawn['id']}/complete", headers=self.publisher_headers
        )
        self.client.post(
            f"/api/todos/{withdrawn['id']}/withdraw", headers=self.publisher_headers
        )
        self.assertIn(withdrawn["id"], self._ids("date=done&flat=1"))

        # 已完成 + 清空某日（软删）→ done 仍可见；用独立日期避免影响其它用例
        day = "2031-01-01"
        cleared = self._create(title="完成后清空", start_date=day, end_date=day)
        self.client.post(f"/api/todos/{cleared['id']}/complete", headers=self.headers)
        r = self.client.post("/api/todos/clear", json={"date": day}, headers=self.headers)
        self.assertIn(cleared["id"], r.json()["deleted"])
        self.assertIn(cleared["id"], self._ids("date=done&flat=1"))
        self.assertNotIn(cleared["id"], self._ids("date=all&include_done=1&flat=1"))

        # 软删的未处理项不属于「已完成」，不应出现在 done
        pending = self._create(title="待办被软删")
        self.client.delete(f"/api/todos/{pending['id']}", headers=self.headers)
        self.assertNotIn(pending["id"], self._ids("date=done&flat=1"))

    def test_v3_persistent_semantics(self):
        todo = self._create(title="持久V3", persistent=True)
        self.assertIsNone(todo["start_date"])
        self.assertIsNone(todo["end_date"])
        self.assertIsNone(todo["due_date"])
        # today 含持久；persistent 只出持久；具体日不出持久
        self.assertIn(todo["id"], self._ids("date=today&flat=1"))
        self.assertIn(todo["id"], self._ids("date=persistent&flat=1"))
        self.assertNotIn(todo["id"], self._ids("date=%s&flat=1" % datetime.date.today().isoformat()))

    def test_v3_persistent_not_forced_today(self):
        todo = self._create(title="持久不写今天", persistent=True)
        self.assertIsNone(todo["due_date"])
        # Android/旧前端无 date 参数（缺省=今天）仍能看到持久待办
        self.assertIn(todo["id"], self._ids("flat=1"))

    def test_v3_start_after_end_400(self):
        r = self.client.post(
            "/api/todos",
            json={"title": "坏区间", "start_date": "2030-01-02", "end_date": "2030-01-01"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 400)

    def test_v3_invalid_date_token_400(self):
        self.assertEqual(
            self.client.get("/api/todos?date=bogus", headers=self.headers).status_code, 400
        )

    def test_v3_single_side_auto_fill(self):
        todo = self._create(title="单边补齐", start_date="2030-05-05")
        self.assertEqual(todo["start_date"], "2030-05-05")
        self.assertEqual(todo["end_date"], "2030-05-05")
        self.assertEqual(todo["due_date"], "2030-05-05")

    def test_v3_subtask_inherits_window(self):
        parent = self._create(title="父窗口", start_date="2030-06-01", end_date="2030-06-03")
        child = self.client.post(
            f"/api/todos/{parent['id']}/subtasks", json={"title": "子窗口"}, headers=self.headers
        ).json()
        self.assertEqual(child["start_date"], "2030-06-01")
        self.assertEqual(child["end_date"], "2030-06-03")
        # 持久父 → 持久子
        pp = self._create(title="父持久", persistent=True)
        cc = self.client.post(
            f"/api/todos/{pp['id']}/subtasks", json={"title": "子持久"}, headers=self.headers
        ).json()
        self.assertIsNone(cc["start_date"])
        self.assertIsNone(cc["end_date"])

    def test_v3_patch_clear_to_persistent(self):
        todo = self._create(title="转持久", start_date="2030-07-01", end_date="2030-07-02")
        r = self.client.patch(
            f"/api/todos/{todo['id']}",
            json={"start_date": None, "end_date": None},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["start_date"])
        self.assertIsNone(r.json()["end_date"])
        self.assertIn(
            todo["id"], self._ids("date=persistent&include_done=1&flat=1")
        )
        # persistent=true 同样可转
        r = self.client.patch(
            f"/api/todos/{todo['id']}", json={"persistent": True}, headers=self.headers
        )
        self.assertIsNone(r.json()["due_date"])

    def test_v3_patch_single_side_fill(self):
        todo = self._create(title="PATCH单边")
        r = self.client.patch(
            f"/api/todos/{todo['id']}", json={"start_date": "2030-08-08"}, headers=self.headers
        )
        self.assertEqual(r.json()["start_date"], "2030-08-08")
        self.assertEqual(r.json()["end_date"], "2030-08-08")

    def test_v3_clear_day(self):
        today = datetime.date.today().isoformat()
        future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        done = self._create(title="当日完成Clear")
        self.client.post(f"/api/todos/{done['id']}/complete", headers=self.headers)
        pending = self._create(title="当日待办Clear")
        fut = self._create(title="未来Clear", start_date=future, end_date=future)
        per = self._create(title="持久Clear", persistent=True)

        r = self.client.post("/api/todos/clear", json={"date": today}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        deleted = r.json()["deleted"]
        self.assertIn(done["id"], deleted)
        self.assertNotIn(pending["id"], deleted)

        ids = self._ids("date=all&include_done=1&flat=1")
        self.assertNotIn(done["id"], ids)
        self.assertIn(pending["id"], ids)
        self.assertIn(fut["id"], ids)
        self.assertIn(per["id"], ids)

    def test_v3_clear_invalid_date_400(self):
        self.assertEqual(
            self.client.post(
                "/api/todos/clear", json={"date": "not-a-date"}, headers=self.headers
            ).status_code,
            400,
        )

    def test_v3_migration_backfill_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE todo(id TEXT PRIMARY KEY, family_id TEXT, status INTEGER, "
            "title TEXT, due_date TEXT)"
        )
        conn.execute(
            "INSERT INTO todo(id, family_id, status, title, due_date) "
            "VALUES('a','f',0,'旧A','2025-03-04')"
        )
        conn.execute(
            "INSERT INTO todo(id, family_id, status, title, due_date) "
            "VALUES('b','f',0,'旧B',NULL)"
        )
        db._migrate(conn)
        a = conn.execute("SELECT start_date, end_date FROM todo WHERE id='a'").fetchone()
        self.assertEqual(a["start_date"], "2025-03-04")
        self.assertEqual(a["end_date"], "2025-03-04")
        b = conn.execute("SELECT start_date, end_date FROM todo WHERE id='b'").fetchone()
        self.assertIsNone(b["start_date"])
        self.assertIsNone(b["end_date"])
        # 再跑一次：幂等不报错、值不变
        db._migrate(conn)
        a = conn.execute("SELECT start_date, end_date FROM todo WHERE id='a'").fetchone()
        self.assertEqual(a["start_date"], "2025-03-04")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(todo)")}
        self.assertIn("important", cols)
        conn.close()

    # ---- V5 重要置顶 / 撤回 / mine（用户 09-12 定稿）----

    def _pub_create(self, **kwargs):
        body = {"title": "V5发布"}
        body.update(kwargs)
        r = self.client.post("/api/todos", json=body, headers=self.publisher_headers)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _pub_ids(self):
        body = self.client.get(
            "/api/todos?date=all&include_done=1&flat=1&mine=1",
            headers=self.publisher_headers,
        ).json()
        return [i["id"] for i in body["items"]], body["items"]

    def test_v5_important_default_false(self):
        todo = self._pub_create(title="默认不重要的V5")
        self.assertFalse(todo["important"])

    def test_v5_important_pin_complete_uncomplete(self):
        normal = self._pub_create(title="普通V5")
        important = self._pub_create(title="重要V5", important=True)
        self.assertTrue(important["important"])

        ids, items = self._pub_ids()
        # 重要未完成 → 排在普通未完成之前
        self.assertLess(ids.index(important["id"]), ids.index(normal["id"]))

        # 勾选完成 → 落末尾（未完成在前）
        self.client.post(
            f"/api/todos/{important['id']}/complete", headers=self.publisher_headers
        )
        ids, items = self._pub_ids()
        self.assertGreater(ids.index(important["id"]), ids.index(normal["id"]))

        # 取消完成 → 重要属性保留 → 重新置顶
        self.client.post(
            f"/api/todos/{important['id']}/uncomplete", headers=self.publisher_headers
        )
        ids, items = self._pub_ids()
        self.assertLess(ids.index(important["id"]), ids.index(normal["id"]))
        restored = next(i for i in items if i["id"] == important["id"])
        self.assertTrue(restored["important"])

    def test_v5_patch_important(self):
        todo = self._pub_create(title="改重要V5")
        r = self.client.patch(
            f"/api/todos/{todo['id']}",
            json={"important": True},
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["important"])
        r = self.client.patch(
            f"/api/todos/{todo['id']}",
            json={"important": False},
            headers=self.publisher_headers,
        )
        self.assertFalse(r.json()["important"])

    def test_v5_bulk_important(self):
        r = self.client.post(
            "/api/todos/bulk",
            json={
                "source": "oneline",
                "items": [{"title": "批量重要V5", "important": True}],
            },
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["items"][0]["important"])

    def test_v5_mine_filter(self):
        mine = self._pub_create(title="我的V5")
        not_mine = self.client.post(
            "/api/todos", json={"title": "非我V5"}, headers=self.headers
        ).json()
        ids, _ = self._pub_ids()
        self.assertIn(mine["id"], ids)
        self.assertNotIn(not_mine["id"], ids)

    def test_v5_mine_filter_executor_device_self(self):
        # 回归：执行端设备（非 publisher）自己发布的待办，
        # mine=1 也要能按 created_by 拉回——发布管理不依赖角色。
        mine = self.client.post(
            "/api/todos", json={"title": "执行端自己发布的V5"}, headers=self.headers
        ).json()
        other = self._pub_create(title="发布端发的V5")
        r = self.client.get(
            "/api/todos?date=all&include_done=1&mine=1", headers=self.headers
        )
        self.assertEqual(r.status_code, 200)
        ids = [i["id"] for i in r.json()["items"]]
        self.assertIn(mine["id"], ids)
        self.assertNotIn(other["id"], ids)

    def test_v5_withdraw_own(self):
        todo = self._pub_create(title="撤回V5")
        r = self.client.post(
            f"/api/todos/{todo['id']}/withdraw", headers=self.publisher_headers
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(todo["id"], r.json()["deleted"])
        ids, _ = self._pub_ids()
        self.assertNotIn(todo["id"], ids)

    def test_v5_withdraw_others_forbidden(self):
        executor_todo = self.client.post(
            "/api/todos", json={"title": "别人发的V5"}, headers=self.headers
        ).json()
        r = self.client.post(
            f"/api/todos/{executor_todo['id']}/withdraw", headers=self.publisher_headers
        )
        self.assertEqual(r.status_code, 403)

    def test_v5_withdraw_missing_404(self):
        r = self.client.post(
            "/api/todos/nope/withdraw", headers=self.publisher_headers
        )
        self.assertEqual(r.status_code, 404)

    # ---- V5.1 发布端编辑自己发布的未完成待办（单个 / 批量）----

    def test_patch_mine_requires_owner(self):
        todo = self._pub_create(title="编辑权限")
        # 别人（执行端）带 mine=1 改 → 403，且不该被改动
        r = self.client.patch(
            f"/api/todos/{todo['id']}?mine=1",
            json={"title": "越权改"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 403)
        # 不带 mine 改内容字段同样拒绝
        r = self.client.patch(
            f"/api/todos/{todo['id']}",
            json={"tag": "越权标签"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 403)
        # 仅改日期窗口（执行端「重新唤起」）不属内容编辑，仍允许
        r = self.client.patch(
            f"/api/todos/{todo['id']}",
            json={"start_date": "2030-01-01", "end_date": "2030-01-01"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        # 发布者本人带 mine=1 改 → 200
        r = self.client.patch(
            f"/api/todos/{todo['id']}?mine=1",
            json={"title": "改标题", "tag": "标签", "start_date": "2030-09-09"},
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["title"], "改标题")
        self.assertEqual(body["tag"], "标签")
        self.assertEqual(body["start_date"], "2030-09-09")
        self.assertEqual(body["end_date"], "2030-09-09")

    def test_patch_mine_only_pending(self):
        completed = self._pub_create(title="完成后不可改")
        self.client.post(
            f"/api/todos/{completed['id']}/complete", headers=self.publisher_headers
        )
        ignored = self._pub_create(title="忽略后不可改")
        self.client.post(
            f"/api/todos/{ignored['id']}/ignore", headers=self.publisher_headers
        )
        cancelled = self._pub_create(title="取消后不可改")
        self.client.post(
            f"/api/todos/{cancelled['id']}/cancel", headers=self.publisher_headers
        )
        for tid in (completed["id"], ignored["id"], cancelled["id"]):
            r = self.client.patch(
                f"/api/todos/{tid}?mine=1",
                json={"title": "不该改"},
                headers=self.publisher_headers,
            )
            self.assertEqual(r.status_code, 400)

    def test_patch_foreign_404(self):
        r = self.client.patch(
            "/api/todos/nope?mine=1", json={"title": "x"}, headers=self.publisher_headers
        )
        self.assertEqual(r.status_code, 404)

    def test_bulk_update_own_pending(self):
        a = self._pub_create(title="批改A")
        b = self._pub_create(title="批改B")
        r = self.client.post(
            "/api/todos/bulk-update",
            json={
                "ids": [a["id"], b["id"]],
                "patch": {"tag": "统一标签", "start_date": "2030-09-09"},
            },
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["count"], 2)
        _, items = self._pub_ids()
        for tid in (a["id"], b["id"]):
            item = next(i for i in items if i["id"] == tid)
            self.assertEqual(item["tag"], "统一标签")
            self.assertEqual(item["start_date"], "2030-09-09")
            self.assertEqual(item["end_date"], "2030-09-09")

    def test_bulk_update_rejects_foreign(self):
        foreign = self.client.post(
            "/api/todos", json={"title": "别人发的批改"}, headers=self.headers
        ).json()
        r = self.client.post(
            "/api/todos/bulk-update",
            json={"ids": [foreign["id"]], "patch": {"tag": "越权"}},
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 403)

    def test_bulk_update_rejects_done_and_unknown(self):
        done = self._pub_create(title="批改已完成")
        self.client.post(
            f"/api/todos/{done['id']}/complete", headers=self.publisher_headers
        )
        r = self.client.post(
            "/api/todos/bulk-update",
            json={"ids": [done["id"]], "patch": {"tag": "x"}},
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            "/api/todos/bulk-update",
            json={"ids": ["nope"], "patch": {"tag": "x"}},
            headers=self.publisher_headers,
        )
        self.assertEqual(r.status_code, 404)

    # ---- WebSocket ----

    def test_ws_auth_and_broadcast(self):
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": self.device_token})
            ack = ws.receive_json()
            self.assertTrue(ack["ok"])
            self.assertEqual(ack["family_id"], self.family_id)

            self.client.post(
                "/api/todos", json={"title": "WS广播测试"}, headers=self.headers
            )
            event = ws.receive_json()
            self.assertEqual(event["type"], "todo.created")
            self.assertEqual(event["data"]["title"], "WS广播测试")

    def test_ws_rejects_bad_token(self):
        with self.client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "bad"})
            ack = ws.receive_json()
            self.assertFalse(ack["ok"])

# ---- 反馈闭环：知悉(ack) & 完成人(completed_by) ----

    def test_ack_single(self):
        created = self.client.post(
            "/api/todos", json={"title": "知悉测试"}, headers=self.headers
        ).json()
        r = self.client.post(
            f"/api/todos/{created['id']}/ack", headers=self.headers
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNotNone(body["acked_by"])
        self.assertIsNotNone(body["ack_at"])

    def test_ack_all_and_bulk(self):
        a = self.client.post("/api/todos", json={"title": "A1"}, headers=self.headers).json()
        b = self.client.post("/api/todos", json={"title": "A2"}, headers=self.headers).json()
        # 一键知悉全部
        r = self.client.post("/api/todos/ack-all", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 2)
        # 多选知悉：b 先完成后再多选 → 已完成被跳过，只成功 a
        self.client.post(f"/api/todos/{b['id']}/complete", headers=self.headers)
        r = self.client.post(
            "/api/todos/ack-bulk",
            json={"ids": [a["id"], b["id"]]},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 1)
        self.assertEqual(self._find(a["id"])["status_detail"], "pending")

    def test_completed_by_when_child_done(self):
        pid, (s1, s2) = self._make_parent_with_subtasks("完成人父", ["子一", "子二"])
        self.client.post(f"/api/todos/{s1['id']}/complete", headers=self.headers)
        self.client.post(f"/api/todos/{s2['id']}/complete", headers=self.headers)
        parent = self._find(pid)
        self.assertEqual(parent["status_detail"], "completed")
        self.assertIsNotNone(parent["completed_by"])
        s = self._find(s1["id"])
        self.assertIsNotNone(s["completed_by"])
        # 取消完成 → completed_by 清空
        self.client.post(f"/api/todos/{s1['id']}/uncomplete", headers=self.headers)
        s = self._find(s1["id"])
        self.assertIsNone(s["completed_by"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
