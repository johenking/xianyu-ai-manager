"""观察脚本（tools/observe_account.py）的只读纪律与报告正确性测试。

这里锁死的行为：
1. `_connect_ro` 是真只读：任何写操作必须被 SQLite 拒绝——灰度盯盘工具绝不能碰生产库；
2. `_mask_ref` 与生产日志脱敏（session_registry.sanitize_log_record）同源同形，
   否则日志形态一漂移，观察脚本就永远 grep 不到目标账号的日志；
3. `_scan_logs` 的时间窗过滤、账号隔离与关键词分桶；
4. `main()` 端到端：库不存在 rc=2、账号不存在 rc=1、正常出报告 rc=0，
   且 DB has_l3_memory 与 .l3_ready 文件不一致时必须给出 split-brain 提示。
"""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from session_registry import sanitize_log_record
from tools.observe_account import (
    _connect_ro,
    _fmt_ts,
    _mask_ref,
    _scan_logs,
    _select_row,
    main,
)

COOKIE_ID = "acct-observe-1"


def _seed_db(path: str, *, has_l3_memory=1, with_refresh=True, proxy_enabled=1) -> float:
    """建一个带 cookies / 续签状态表的临时库，返回落库用的 now 基准。"""
    now = datetime.now().timestamp()
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE cookies (
            id TEXT PRIMARY KEY, xianyu_unb TEXT, xianyu_nick TEXT, login_method TEXT,
            last_login_at REAL, last_validated_at REAL, last_expired_at REAL,
            has_l3_memory INTEGER, l3_memory_at REAL,
            proxy_enabled INTEGER, proxy_server TEXT, proxy_username TEXT,
            proxy_password_encrypted TEXT, proxy_region TEXT,
            proxy_last_ip TEXT, proxy_last_status TEXT, proxy_last_check_at REAL
        )"""
    )
    conn.execute(
        "INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            COOKIE_ID, "9988", "灰度观察号", "qrcode",
            now - 3600, now - 600, None,
            has_l3_memory, now - 7200,
            proxy_enabled, "http://1.2.3.4:8000", "proxy-user", "", "上海静态住宅",
            "1.2.3.4", "ok", now - 300,
        ),
    )
    if with_refresh:
        conn.execute(
            """CREATE TABLE account_session_refresh_status (
                cookie_id TEXT PRIMARY KEY, state TEXT, "trigger" TEXT, message TEXT,
                error_code TEXT, started_at REAL, last_attempt_at REAL,
                last_success_at REAL, expires_at REAL, updated_at REAL
            )"""
        )
        conn.execute(
            "INSERT INTO account_session_refresh_status VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                COOKIE_ID, "success", "L3主动保活", "免密续签成功", "",
                now - 900, now - 900, now - 900, None, now - 900,
            ),
        )
    conn.commit()
    conn.close()
    return now


class MaskParityTests(unittest.TestCase):
    def test_mask_ref_matches_production_log_masking(self):
        # 观察脚本按脱敏形态 grep 日志；必须与生产 patcher 的输出逐字节一致。
        record = {"message": f"【{COOKIE_ID}】L3 主动保活成功"}
        sanitize_log_record(record)
        self.assertIn(_mask_ref(COOKIE_ID), record["message"])
        self.assertNotIn(COOKIE_ID, record["message"])


class ReadOnlyTests(unittest.TestCase):
    def test_connect_ro_rejects_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "ro.db")
            _seed_db(db_path)
            conn = _connect_ro(db_path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE cookies SET xianyu_nick = 'hacked'")
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("CREATE TABLE evil (x)")
            finally:
                conn.close()

    def test_connect_ro_missing_db_raises_instead_of_creating(self):
        # sqlite 默认会悄悄新建空库文件；只读工具必须显式拒绝。
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.db"
            with self.assertRaises(FileNotFoundError):
                _connect_ro(str(missing))
            self.assertFalse(missing.exists())


class SelectRowTests(unittest.TestCase):
    def test_intersects_with_actual_schema(self):
        # 老库缺代理列：按现存列取交集，不报错、不返回幻影列。
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE cookies (id TEXT, xianyu_nick TEXT)")
        conn.execute("INSERT INTO cookies VALUES ('a1', 'nick')")
        row = _select_row(
            conn.cursor(), "cookies", ("id", "xianyu_nick", "proxy_server"), "id", "a1"
        )
        conn.close()
        self.assertEqual(row, {"id": "a1", "xianyu_nick": "nick"})

    def test_missing_table_or_row_returns_none(self):
        conn = sqlite3.connect(":memory:")
        self.assertIsNone(_select_row(conn.cursor(), "absent", ("a",), "a", "x"))
        conn.execute("CREATE TABLE t (a TEXT)")
        self.assertIsNone(_select_row(conn.cursor(), "t", ("a",), "a", "missing"))
        conn.close()


class FmtTsTests(unittest.TestCase):
    def test_empty_and_invalid_values(self):
        now = datetime.now().timestamp()
        for empty in (None, "", 0):
            self.assertEqual(_fmt_ts(empty, now=now), "—")
        self.assertEqual(_fmt_ts("not-a-number", now=now), "not-a-number")

    def test_age_suffix(self):
        now = datetime.now().timestamp()
        self.assertIn("h前）", _fmt_ts(now - 7200, now=now))
        self.assertIn("未来", _fmt_ts(now + 7200, now=now))


class ScanLogTests(unittest.TestCase):
    def test_window_account_isolation_and_buckets(self):
        now = datetime.now()
        masked = _mask_ref(COOKIE_ID)
        other = _mask_ref("someone-else-9")
        fresh = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
        stale = (now - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "xianyu_2026-08-29.log"
            log.write_text(
                "\n".join(
                    [
                        f"{fresh} | INFO | 【{masked}】L3 主动保活成功",
                        f"{stale} | INFO | 【{masked}】免密续签成功",  # 超出 72h 窗口，不计
                        f"{fresh} | INFO | 【{other}】免密续签成功",  # 别的账号，不计
                        f"{fresh} | WARNING | 【{masked}】代理出口不通（status=proxy_error）",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = _scan_logs(tmp, COOKIE_ID, hours=72.0, now=now.timestamp())

        self.assertEqual(result["scanned_files"], ["xianyu_2026-08-29.log"])
        self.assertEqual(len(result["buckets"]["保活"]), 1)
        self.assertEqual(len(result["buckets"]["续签"]), 0)
        self.assertEqual(len(result["buckets"]["代理"]), 1)
        self.assertEqual(len(result["buckets"]["风控/验证"]), 0)

    def test_missing_dir_noted_not_crash(self):
        result = _scan_logs(
            "/nonexistent-dir-observe-test", COOKIE_ID, 72.0, datetime.now().timestamp()
        )
        self.assertIn("不存在", result["note"])
        self.assertEqual(sum(len(v) for v in result["buckets"].values()), 0)


class MainEndToEndTests(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_missing_db_returns_2(self):
        rc, _out, err = self._run([COOKIE_ID, "--db", "/nonexistent/observe.db"])
        self.assertEqual(rc, 2)
        self.assertIn("数据库不存在", err)

    def test_unknown_account_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "d.db")
            _seed_db(db_path)
            rc, _out, err = self._run(
                ["nobody-here", "--db", db_path, "--log-dir", tmp, "--profile-root", tmp]
            )
        self.assertEqual(rc, 1)
        self.assertIn("账号未找到", err)

    def test_full_report_flags_split_brain(self):
        # DB 说有 L3 记忆，但档案目录没有 .l3_ready → 必须提示 split-brain。
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "d.db")
            _seed_db(db_path, has_l3_memory=1)
            rc, out, _err = self._run(
                [
                    COOKIE_ID,
                    "--db", db_path,
                    "--log-dir", str(Path(tmp) / "logs"),
                    "--profile-root", str(Path(tmp) / "profiles"),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("账号观察报告", out)
        self.assertIn("split-brain", out)
        self.assertIn("1.2.3.4", out)  # 代理最近出口
        self.assertIn("上海静态住宅", out)
        self.assertIn("L3主动保活", out)  # 续签 trigger

    def test_consistent_l3_state_has_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "d.db")
            _seed_db(db_path, has_l3_memory=1)
            ready = Path(tmp) / "profiles" / "user_9988" / ".l3_ready"
            ready.parent.mkdir(parents=True)
            ready.write_text("ok", encoding="utf-8")
            rc, out, _err = self._run(
                [
                    COOKIE_ID,
                    "--db", db_path,
                    "--log-dir", str(Path(tmp) / "logs"),
                    "--profile-root", str(Path(tmp) / "profiles"),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertNotIn("split-brain", out)

    def test_proxyless_account_reports_direct_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "d.db")
            _seed_db(db_path, proxy_enabled=0)
            rc, out, _err = self._run(
                [COOKIE_ID, "--db", db_path, "--log-dir", tmp, "--profile-root", tmp]
            )
        self.assertEqual(rc, 0)
        self.assertIn("未启用代理", out)


if __name__ == "__main__":
    unittest.main()
