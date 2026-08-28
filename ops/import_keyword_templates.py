"""高频问题关键词模板导入脚本（幂等，默认 dry-run）。

依据 2026-08-28 生产消息统计（outputs/keyword-templates-20260828/report.md）：
- 邀请业务专属模板只分发到纯邀请/邀请为主的账号；
- 通用安全模板分发到全部启用 AI 的账号；
- 已存在同关键词（通用级）的账号一律跳过，不覆盖人工配置。

用法（在生产容器或本机仓库根目录）：
    python ops/import_keyword_templates.py            # dry-run，只打印将插入的行
    python ops/import_keyword_templates.py --apply    # 实际写入
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_manager import db_manager  # noqa: E402

# 邀请业务账号（纯邀请或邀请为主）；混卖账号 2222616470727、非邀请账号 2217620515655 不在列。
INVITE_ACCOUNTS = [
    "3358186199",
    "2220000015630",
    "2217422055234",
    "2220280841416",
    "3373827289",
    "2995229232",
    "2218454962647",
]

# 全部启用 AI 的账号（通用安全模板）。
ALL_ACCOUNTS = INVITE_ACCOUNTS + ["2222616470727", "2217620515655"]

TUTORIAL_REPLY = "拍下自动发货，会发您兑换链接，打开填邮箱按提示操作就行；拍前记得先看邀请界面有没有额度哦"
REDEEM_REPLY = "拍下后自动发您兑换链接，打开链接填邮箱，按提示确认邀请就行啦"
POINTS_REPLY = "您好，去设置-使用与计费-点数额度 里面的余额就是送的点数，额度用完自动扣点数，有效期12个月"

# (关键词, 回复) —— 邀请业务专属。
INVITE_TEMPLATES = [
    ("怎么操作", TUTORIAL_REPLY),
    ("怎么弄", TUTORIAL_REPLY),
    ("怎么兑换", REDEEM_REPLY),
    ("怎么使用", REDEEM_REPLY),
    ("直接拍", "可以直接拍哈，拍下自动发货；拍前先确认邀请界面有额度哦"),
    ("还有吗", "在的哈，能拍就是有货，拍下自动发货~"),
    ("重置", "不是重置，去桌面端左下角查看邀请好友页面，确认有额度再下单，没有写额度就是没有，请勿下单"),
    ("核验", POINTS_REPLY),
    ("奖励", POINTS_REPLY),
    ("到账", POINTS_REPLY),
    ("点数", POINTS_REPLY),
    ("有效期", "12个月"),
]

# (关键词, 回复) —— 全账号通用安全模板（与具体商品无关）。
GENERIC_TEMPLATES = [
    ("人工", "稍等一会哈，老板看到消息会尽快回复您"),
    ("AI", "目前是AI在回消息，稍等一会人工处理！"),
]


def existing_generic_keywords(cookie_id: str) -> set:
    with db_manager.lock:
        rows = db_manager.conn.execute(
            "SELECT keyword FROM keywords "
            "WHERE cookie_id = ? AND (item_id IS NULL OR item_id = '')",
            (cookie_id,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def plan_inserts() -> list:
    plan = []
    for cookie_id in ALL_ACCOUNTS:
        existing = existing_generic_keywords(cookie_id)
        templates = list(GENERIC_TEMPLATES)
        if cookie_id in INVITE_ACCOUNTS:
            templates = INVITE_TEMPLATES + templates
        for keyword, reply in templates:
            if keyword in existing:
                continue
            plan.append((cookie_id, keyword, reply))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = parser.parse_args()

    plan = plan_inserts()
    if not plan:
        print("所有模板均已存在，无需插入。")
        return 0

    by_account: dict = {}
    for cookie_id, keyword, _ in plan:
        by_account.setdefault(cookie_id, []).append(keyword)
    for cookie_id, keywords in by_account.items():
        print(f"{cookie_id}: +{len(keywords)} -> {'、'.join(keywords)}")
    print(f"合计待插入 {len(plan)} 条（已存在的同名关键词均已跳过）。")

    if not args.apply:
        print("dry-run 结束；确认无误后加 --apply 执行。")
        return 0

    inserted = 0
    with db_manager.lock:
        for cookie_id, keyword, reply in plan:
            db_manager.conn.execute(
                "INSERT INTO keywords (cookie_id, keyword, reply, item_id, type) "
                "VALUES (?, ?, ?, '', 'text')",
                (cookie_id, keyword, reply),
            )
            inserted += 1
        db_manager.conn.commit()
    print(f"已插入 {inserted} 条关键词模板。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
