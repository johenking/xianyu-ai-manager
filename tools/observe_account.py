#!/usr/bin/env python3
"""单账号云端灰度观察脚本（只读，供阶段4 的 72h 灰度盯盘）。

给定一个账号（cookie_id），只读汇总它当前的登录态 / 续签结果 / 代理出口 / L3 记忆
状态，并可选扫描近 N 小时运行日志，把「保活 / 续签 / 风控 / 代理」相关行分桶计数。
用于灰度期间随时核对：代理出口 IP 是不是住宅、免密续签有没有真跑、有没有触发风控滑块。

只读安全（照 backfill_order_snapshots.py 的既定纪律）：
- DB 一律用 SQLite mode=ro 直接读 DB_PATH，绝不初始化 DBManager、不迁移、不写库。
- 日志只读扫描，不改动、不删除。
- 默认不做实时探测；加 --live-probe 才对已配置代理跑一次出口自检（发一个网络请求，
  只读账号凭据密钥、不写库）。

用法：
    .venv/bin/python tools/observe_account.py <cookie_id>
    .venv/bin/python tools/observe_account.py <cookie_id> --hours 72
    .venv/bin/python tools/observe_account.py <cookie_id> --live-probe
    DB_PATH=/app/data/xianyu_data.db .venv/bin/python tools/observe_account.py <cookie_id>
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# 想读的 cookies 列（与 get_cookie_details 对齐）；实际按表结构取交集，缺列不报错。
_ACCOUNT_COLUMNS = (
    "id",
    "xianyu_unb",
    "xianyu_nick",
    "login_method",
    "last_login_at",
    "last_validated_at",
    "last_expired_at",
    "has_l3_memory",
    "l3_memory_at",
    "proxy_enabled",
    "proxy_server",
    "proxy_username",
    "proxy_password_encrypted",
    "proxy_region",
    "proxy_last_ip",
    "proxy_last_status",
    "proxy_last_check_at",
)

_REFRESH_COLUMNS = (
    "state",
    "trigger",
    "message",
    "error_code",
    "started_at",
    "last_attempt_at",
    "last_success_at",
    "expires_at",
    "updated_at",
)

# 日志分桶关键词：只在 message 里匹配，命中即计入对应桶（一行可进多个桶）。
_LOG_BUCKETS = {
    "保活": ("主动保活", "L3 主动保活", "keepalive"),
    "续签": ("免密续签", "续签", "记忆重建", "reseed"),
    "风控/验证": (
        "风控",
        "滑块",
        "human_verification",
        "ILLEGAL_ACCESS",
        "需要人工",
        "manual_reauth",
        "fast_entry_unavailable",
        "重新扫码",
    ),
    "代理": ("代理", "proxy", "出口"),
}


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """只读连接（mode=ro 而非 immutable：服务器可能在跑、要如实读 WAL 新行）。"""
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)


def _table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return set()
    return {str(row[1]) for row in cursor.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _select_row(cursor: sqlite3.Cursor, table: str, wanted, where_col: str, key: str):
    """按现存列取一行，返回 {列名: 值}；表/行不存在返回 None。"""
    available = _table_columns(cursor, table)
    if not available or where_col not in available:
        return None
    cols = [c for c in wanted if c in available]
    if not cols:
        return None
    quoted = ", ".join(f'"{c}"' for c in cols)
    row = cursor.execute(
        f'SELECT {quoted} FROM "{table}" WHERE "{where_col}" = ?', (key,)
    ).fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def _fmt_ts(value, *, now: float) -> str:
    """epoch 秒 → '2026-08-29 18:08:15（3.2h前）'；空值返回 —。"""
    if value in (None, "", 0):
        return "—"
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return str(value)
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    age_h = (now - epoch) / 3600.0
    if age_h < 0:
        return f"{stamp}（未来 {abs(age_h):.1f}h）"
    return f"{stamp}（{age_h:.1f}h前）"


def _mask_ref(cookie_id: str) -> str:
    """还原日志里对账号 id 的脱敏形态：account_<sha256(id)[:10]>。"""
    digest = hashlib.sha256(cookie_id.encode("utf-8")).hexdigest()[:10]
    return f"account_{digest}"


def _parse_log_time(line: str):
    """解析 loguru 行首时间戳 'YYYY-MM-DD HH:MM:SS.mmm'。"""
    head = line[:23]
    try:
        return datetime.strptime(head, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _scan_logs(log_dir: str, cookie_id: str, hours: float, now: float) -> dict:
    """只读扫描 logs/xianyu_*.log，命中脱敏账号引用且在窗口内的行按关键词分桶。"""
    result = {"scanned_files": [], "buckets": {name: [] for name in _LOG_BUCKETS}, "note": ""}
    directory = Path(log_dir).expanduser()
    if not directory.is_dir():
        result["note"] = f"日志目录不存在：{directory}（跳过日志扫描）"
        return result
    files = sorted(glob.glob(str(directory / "xianyu_*.log")))
    if not files:
        result["note"] = f"{directory} 下无 xianyu_*.log（跳过日志扫描）"
        return result

    masked = _mask_ref(cookie_id)
    cutoff = now - hours * 3600.0
    for path in files:
        matched_any = False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    if masked not in raw:
                        continue
                    stamp = _parse_log_time(raw)
                    if stamp is not None and stamp.timestamp() < cutoff:
                        continue
                    matched_any = True
                    for name, keywords in _LOG_BUCKETS.items():
                        if any(kw in raw for kw in keywords):
                            result["buckets"][name].append(raw.rstrip("\n"))
        except OSError as exc:
            result["note"] = f"读取 {path} 失败：{type(exc).__name__}"
            continue
        if matched_any:
            result["scanned_files"].append(os.path.basename(path))
    return result


def _live_probe(account: dict, db_path: str) -> dict:
    """对已配置代理跑一次出口自检；只读凭据密钥、不写库。"""
    from utils.browser_runtime import probe_proxy_egress

    encrypted = str(account.get("proxy_password_encrypted") or "")
    password = ""
    if encrypted:
        try:
            from security_utils import AccountCredentialCipher

            password = AccountCredentialCipher(db_path).decrypt(encrypted)
        except Exception as exc:  # noqa: BLE001 - 观察脚本不因解密失败中断
            return {"ok": False, "status": "decrypt_failed", "ip": "", "error": type(exc).__name__}
    proxy = {
        "server": str(account.get("proxy_server") or ""),
        "username": str(account.get("proxy_username") or ""),
        "password": password,
    }
    return probe_proxy_egress(proxy)


def _print_report(account: dict, refresh, logs: dict, args, now: float) -> None:
    cookie_id = account["id"]
    unb = str(account.get("xianyu_unb") or "")
    print("=" * 64)
    print(f"账号观察报告 · {cookie_id}")
    print(f"生成时间：{datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')} · 窗口 {args.hours}h")
    print("=" * 64)

    print("\n【身份】")
    print(f"  昵称     : {account.get('xianyu_nick') or '—'}")
    print(f"  unb      : {unb or '—'}")
    print(f"  登录方式 : {account.get('login_method') or '—'}")

    print("\n【登录态】")
    print(f"  最近登录 : {_fmt_ts(account.get('last_login_at'), now=now)}")
    print(f"  最近校验 : {_fmt_ts(account.get('last_validated_at'), now=now)}")
    print(f"  最近失效 : {_fmt_ts(account.get('last_expired_at'), now=now)}")

    print("\n【L3 记忆】")
    has_l3 = bool(account.get("has_l3_memory"))
    print(f"  DB has_l3_memory : {has_l3}    建档时间：{_fmt_ts(account.get('l3_memory_at'), now=now)}")
    if unb:
        ready = Path(args.profile_root).expanduser() / f"user_{unb}" / ".l3_ready"
        present = ready.exists()
        print(f"  .l3_ready 文件   : {present}    ({ready})")
        if present != has_l3:
            print("  ⚠ DB 标记与 .l3_ready 文件不一致（split-brain，续签可能异常）")

    print("\n【续签状态】(account_session_refresh_status)")
    if not refresh:
        print("  无续签记录（该号从未触发过自动续签，或表不存在）")
    else:
        print(f"  state    : {refresh.get('state') or '—'}")
        print(f"  trigger  : {refresh.get('trigger') or '—'}")
        print(f"  message  : {refresh.get('message') or '—'}")
        print(f"  error    : {refresh.get('error_code') or '—'}")
        print(f"  最近尝试 : {_fmt_ts(refresh.get('last_attempt_at'), now=now)}")
        print(f"  最近成功 : {_fmt_ts(refresh.get('last_success_at'), now=now)}")
        print(f"  更新于   : {_fmt_ts(refresh.get('updated_at'), now=now)}")

    print("\n【代理】")
    if not bool(account.get("proxy_enabled")):
        print("  未启用代理（走机房 IP 直连，符合未配置账号的原行为）")
    else:
        print(f"  server   : {account.get('proxy_server') or '—'}")
        print(f"  归属地   : {account.get('proxy_region') or '—'}")
        print(f"  最近出口 : {account.get('proxy_last_ip') or '—'}    状态：{account.get('proxy_last_status') or '—'}")
        print(f"  最近自检 : {_fmt_ts(account.get('proxy_last_check_at'), now=now)}")
        if args.live_probe:
            probe = _live_probe(account, args.db)
            flag = "✅" if probe.get("ok") else "❌"
            print(f"  实时探测 : {flag} status={probe.get('status')} ip={probe.get('ip') or '—'} {probe.get('error') or ''}")

    print("\n【近 %sh 日志】" % args.hours)
    if logs.get("note"):
        print(f"  {logs['note']}")
    if logs.get("scanned_files"):
        print(f"  命中文件 : {', '.join(logs['scanned_files'])}")
    total = sum(len(v) for v in logs["buckets"].values())
    if total == 0 and not logs.get("note"):
        print("  窗口内无该账号相关日志（可能账号空闲，或日志已轮转清理）")
    for name, lines in logs["buckets"].items():
        if not lines:
            continue
        print(f"  · {name}：{len(lines)} 条")
        for line in lines[-5:]:
            print(f"      {line[:200]}")
    print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="单账号云端灰度观察（只读）")
    parser.add_argument("cookie_id", help="要观察的账号 cookie_id")
    parser.add_argument("--hours", type=float, default=72.0, help="日志回看窗口小时数（默认 72）")
    parser.add_argument(
        "--db",
        default=os.getenv("DB_PATH", "data/xianyu_data.db"),
        help="SQLite 数据库路径（默认取 DB_PATH 或 data/xianyu_data.db）",
    )
    parser.add_argument("--log-dir", default="logs", help="日志目录（默认 logs）")
    parser.add_argument("--profile-root", default="browser_data", help="浏览器档案根目录（默认 browser_data）")
    parser.add_argument(
        "--live-probe",
        action="store_true",
        help="对已配置代理跑一次实时出口自检（发一个网络请求，只读凭据）",
    )
    args = parser.parse_args(argv)

    now = datetime.now().timestamp()
    try:
        conn = _connect_ro(args.db)
    except FileNotFoundError as exc:
        print(f"数据库不存在：{exc}", file=sys.stderr)
        return 2
    try:
        cursor = conn.cursor()
        account = _select_row(cursor, "cookies", _ACCOUNT_COLUMNS, "id", args.cookie_id)
        if not account:
            print(f"账号未找到：{args.cookie_id}（确认 cookie_id 与 --db 是否正确）", file=sys.stderr)
            return 1
        refresh = _select_row(
            cursor, "account_session_refresh_status", _REFRESH_COLUMNS, "cookie_id", args.cookie_id
        )
    finally:
        conn.close()

    logs = _scan_logs(args.log_dir, args.cookie_id, args.hours, now)
    _print_report(account, refresh, logs, args, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
