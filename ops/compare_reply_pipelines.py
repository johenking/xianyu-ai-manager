"""AI 回复新旧管线对拍报告脚本（只读，不发送任何消息）。

用途：转正订单感知路径前，用真实历史买家消息生成对比报告。
- 旧回复：直接取历史里已发送的 assistant 记录（legacy 管线的真实输出）；
- 新候选：用 shadow 模式跑订单感知 + 分阶段剧本管线（不写库、不发送）。

用法（在生产容器或本机仓库根目录）：
    python ops/compare_reply_pipelines.py --limit 30 --out outputs/reply_compare.md
    python ops/compare_reply_pipelines.py --limit 30 --dry-run   # 只列样本不调模型

注意：非 dry-run 会按账号配置调用模型（每条样本 1-2 次），请控制 --limit。
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_manager import db_manager  # noqa: E402
from ai_reply_engine import ai_reply_engine  # noqa: E402

STAGE_LABELS = ai_reply_engine.TRADE_STAGE_LABELS


def collect_samples(limit: int, cookie_id: str = "") -> list:
    """抽最近的真实买家消息及其后紧随的已发送回复。"""
    where = "c.role IN ('user', 'buyer')"
    params: list = []
    if cookie_id:
        where += " AND c.cookie_id = ?"
        params.append(cookie_id)
    with db_manager.lock:
        rows = db_manager.conn.execute(
            f"""
            SELECT c.id, c.cookie_id, c.chat_id, c.user_id, c.item_id,
                   c.content, c.created_at
            FROM ai_conversations c
            WHERE {where}
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT ?
            """,
            (*params, limit * 3),
        ).fetchall()

    samples = []
    seen = set()
    for row in rows:
        record_id, cookie, chat_id, user_id, item_id, content, created_at = row
        content = str(content or "").strip()
        if not content or len(content) > 300:
            continue
        key = (chat_id, content)
        if key in seen:
            continue
        seen.add(key)
        with db_manager.lock:
            reply_row = db_manager.conn.execute(
                """
                SELECT content FROM ai_conversations
                WHERE cookie_id = ? AND chat_id = ? AND item_id = ?
                  AND role IN ('assistant', 'assistant_generated') AND id > ?
                ORDER BY id ASC LIMIT 1
                """,
                (cookie, chat_id, item_id, record_id),
            ).fetchone()
        samples.append({
            "cookie_id": cookie,
            "chat_id": chat_id,
            "user_id": user_id,
            "item_id": item_id,
            "message": content,
            "created_at": created_at,
            "legacy_reply": str(reply_row[0]).strip() if reply_row else "（历史无回复）",
        })
        if len(samples) >= limit:
            break
    return samples


def annotate_stage(sample: dict) -> None:
    scope_info = ai_reply_engine.resolve_order_scope(
        sample["chat_id"], sample["cookie_id"], sample["item_id"],
        user_id=sample["user_id"],
    )
    scope = scope_info.get("scope") or "legacy"
    order_id = scope_info.get("order_id") or ""
    summary = ""
    if scope in {"exact", "unique"} and order_id:
        summary = ai_reply_engine._get_verified_order_summary(
            scope, order_id, sample["cookie_id"], sample["item_id"], sample["user_id"],
        )
    stage = ai_reply_engine.resolve_trade_stage(scope, summary)
    sample["scope"] = scope
    sample["stage"] = stage or "legacy"
    sample["stage_label"] = STAGE_LABELS.get(stage, "（未注入阶段）") if stage else "（未注入阶段）"
    sample["intent"] = ai_reply_engine.detect_intent(sample["message"], sample["cookie_id"])


def generate_candidate(sample: dict) -> str:
    item_info_raw = db_manager.get_item_info(sample["cookie_id"], sample["item_id"]) or {}
    item_info = {
        "title": item_info_raw.get("item_title", "未知商品"),
        "price": item_info_raw.get("item_price", "未知"),
        "desc": item_info_raw.get("item_detail", "暂无商品描述"),
    }
    try:
        candidate = ai_reply_engine.generate_shadow_reply(
            message=sample["message"],
            item_info=item_info,
            chat_id=sample["chat_id"],
            cookie_id=sample["cookie_id"],
            user_id=sample["user_id"],
            item_id=sample["item_id"],
        )
        return candidate or "（生成失败/账号未启用AI）"
    except Exception as exc:  # 对拍报告不中断
        return f"（生成异常: {type(exc).__name__}）"


def render_report(samples: list, dry_run: bool) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# AI 回复新旧管线对拍报告",
        "",
        f"- 生成时间：{now}",
        f"- 样本数：{len(samples)}（真实历史买家消息）",
        "- 旧回复：历史上 legacy 管线真实发出的回复",
        "- 新候选：订单感知 + 分阶段剧本管线（shadow 只读生成，未发送）",
        "",
        "| # | 交易阶段 | 意图 | 买家消息 | 旧回复（已发出） | 新候选（未发送） |",
        "|---|---------|------|----------|------------------|------------------|",
    ]
    for index, sample in enumerate(samples, start=1):
        candidate = sample.get("candidate", "（dry-run 未生成）" if dry_run else "")
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                index,
                sample["stage_label"],
                sample["intent"],
                sample["message"].replace("|", "\\|").replace("\n", " "),
                sample["legacy_reply"].replace("|", "\\|").replace("\n", " "),
                str(candidate).replace("|", "\\|").replace("\n", " "),
            )
        )
    lines += [
        "",
        "## 阶段分布",
        "",
    ]
    stage_counts: dict = {}
    for sample in samples:
        stage_counts[sample["stage_label"]] = stage_counts.get(sample["stage_label"], 0) + 1
    for label, count in sorted(stage_counts.items(), key=lambda pair: -pair[1]):
        lines.append(f"- {label}：{count} 条")
    lines += [
        "",
        "## 评审说明",
        "",
        "- 重点看：新候选是否正确反映订单阶段（付款前/待发货/已发货/售后），是否消除答非所问。",
        "- 「存在多笔订单待确认」阶段的候选应是追问订单号，而不是硬答。",
        "- 报告通过后，切换开关：系统设置 ai_reply_order_aware=1（或环境变量 AI_REPLY_ORDER_AWARE=on），",
        "  并同时设置 AI_REPLY_SHADOW_ENABLED=false 停用旁路，避免重复记录。",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 回复新旧管线对拍（只读）")
    parser.add_argument("--limit", type=int, default=20, help="样本条数上限")
    parser.add_argument("--cookie-id", default="", help="仅对拍指定账号")
    parser.add_argument("--out", default="", help="报告输出路径（默认打印到 stdout）")
    parser.add_argument("--dry-run", action="store_true", help="只抽样与标注阶段，不调用模型")
    args = parser.parse_args()

    if ai_reply_engine.order_aware_enabled():
        print("警告：订单感知开关已打开，shadow 短路无法生成候选；请先关闭开关再对拍。")
        return 2

    samples = collect_samples(max(1, min(args.limit, 100)), args.cookie_id)
    if not samples:
        print("没有可用的历史买家消息样本。")
        return 1

    for sample in samples:
        annotate_stage(sample)
        if not args.dry_run:
            sample["candidate"] = generate_candidate(sample)

    report = render_report(samples, args.dry_run)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"报告已写入 {out_path}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
