"""Pure, redacted shadow comparison for Playwright and MTop search results."""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ShadowThresholds:
    minimum_recall: float = 0.70
    minimum_jaccard: float = 0.50
    maximum_price_mismatch_ratio: float = 0.10
    maximum_region_mismatch_ratio: float = 0.20
    maximum_rank_displacement_ratio: float = 0.35
    price_tolerance: float = 0.01

    def normalized(self) -> "ShadowThresholds":
        return ShadowThresholds(
            minimum_recall=max(0.0, min(float(self.minimum_recall), 1.0)),
            minimum_jaccard=max(0.0, min(float(self.minimum_jaccard), 1.0)),
            maximum_price_mismatch_ratio=max(
                0.0, min(float(self.maximum_price_mismatch_ratio), 1.0)
            ),
            maximum_region_mismatch_ratio=max(
                0.0, min(float(self.maximum_region_mismatch_ratio), 1.0)
            ),
            maximum_rank_displacement_ratio=max(
                0.0, min(float(self.maximum_rank_displacement_ratio), 1.0)
            ),
            price_tolerance=max(0.0, min(float(self.price_tolerance), 100.0)),
        )


def _normalized_text(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _item_view(item: Any) -> Optional[Dict[str, Any]]:
    if hasattr(item, "public_dict"):
        item = item.public_dict()
    if not isinstance(item, Mapping):
        return None
    item_id = str(item.get("item_id") or "").strip()[:256]
    if not item_id:
        item_url = str(item.get("item_url") or "").strip()
        marker = "item?id="
        if marker in item_url:
            item_id = item_url.split(marker, 1)[1].split("&", 1)[0][:256]
    if not item_id:
        return None
    try:
        price = None if item.get("price") is None else round(float(item.get("price")), 2)
    except (TypeError, ValueError):
        price = None
    return {
        "item_id": item_id,
        "price": price,
        "region": _normalized_text(item.get("region") or item.get("area")),
    }


def compare_shadow_results(
    playwright_items: Sequence[Any],
    mtop_items: Sequence[Any],
    *,
    thresholds: Optional[ShadowThresholds] = None,
    allow_empty: bool = False,
    expected_non_empty: bool = False,
) -> Dict[str, Any]:
    """Compare only allowlisted normalized fields; never retain source payloads."""
    limits = (thresholds or ShadowThresholds()).normalized()
    playwright = [view for item in playwright_items if (view := _item_view(item))]
    mtop = [view for item in mtop_items if (view := _item_view(item))]
    playwright_map = {item["item_id"]: item for item in playwright}
    mtop_map = {item["item_id"]: item for item in mtop}
    playwright_ids = list(playwright_map)
    mtop_ids = list(mtop_map)
    playwright_set = set(playwright_ids)
    mtop_set = set(mtop_ids)
    intersection = playwright_set & mtop_set
    union = playwright_set | mtop_set

    recall = len(intersection) / len(playwright_set) if playwright_set else 1.0
    jaccard = len(intersection) / len(union) if union else 1.0

    price_compared = 0
    price_mismatches = 0
    region_compared = 0
    region_mismatches = 0
    for item_id in intersection:
        playwright_item = playwright_map[item_id]
        mtop_item = mtop_map[item_id]
        if playwright_item["price"] is not None and mtop_item["price"] is not None:
            price_compared += 1
            if abs(playwright_item["price"] - mtop_item["price"]) > limits.price_tolerance:
                price_mismatches += 1
        if playwright_item["region"] and mtop_item["region"]:
            region_compared += 1
            if playwright_item["region"] != mtop_item["region"]:
                region_mismatches += 1

    price_mismatch_ratio = price_mismatches / price_compared if price_compared else 0.0
    region_mismatch_ratio = region_mismatches / region_compared if region_compared else 0.0
    if len(intersection) <= 1:
        rank_displacement_ratio = 0.0
    else:
        playwright_rank = {item_id: index for index, item_id in enumerate(playwright_ids)}
        mtop_rank = {item_id: index for index, item_id in enumerate(mtop_ids)}
        denominator = max(len(playwright_ids), len(mtop_ids)) - 1
        rank_displacement_ratio = sum(
            abs(playwright_rank[item_id] - mtop_rank[item_id]) / denominator
            for item_id in intersection
        ) / len(intersection)

    reasons = []
    both_empty = not playwright_set and not mtop_set
    if expected_non_empty and not playwright_set:
        reasons.append("reference_expected_non_empty")
    if both_empty and not allow_empty:
        reasons.append("empty_not_accepted")
    if bool(playwright_set) != bool(mtop_set):
        reasons.append("one_source_empty")
    if playwright_set and recall < limits.minimum_recall:
        reasons.append("recall_below_threshold")
    if union and jaccard < limits.minimum_jaccard:
        reasons.append("jaccard_below_threshold")
    if price_mismatch_ratio > limits.maximum_price_mismatch_ratio:
        reasons.append("price_mismatch_above_threshold")
    if region_mismatch_ratio > limits.maximum_region_mismatch_ratio:
        reasons.append("region_mismatch_above_threshold")
    if rank_displacement_ratio > limits.maximum_rank_displacement_ratio:
        reasons.append("rank_displacement_above_threshold")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "counts": {
            "playwright": len(playwright_set),
            "mtop": len(mtop_set),
            "intersection": len(intersection),
            "union": len(union),
        },
        "metrics": {
            "recall_vs_playwright": round(recall, 6),
            "jaccard": round(jaccard, 6),
            "price_compared": price_compared,
            "price_mismatch_ratio": round(price_mismatch_ratio, 6),
            "region_compared": region_compared,
            "region_mismatch_ratio": round(region_mismatch_ratio, 6),
            "rank_displacement_ratio": round(rank_displacement_ratio, 6),
        },
        "empty": {
            "both_empty": both_empty,
            "allow_empty": bool(allow_empty),
            "expected_non_empty": bool(expected_non_empty),
        },
        "thresholds": asdict(limits),
        "evidence_scope": "normalized_allowlist_only",
    }


__all__ = ["ShadowThresholds", "compare_shadow_results"]
