#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="/Users/mac/Documents/咸鱼监控台"
ROOT="${SAMPLE_ROLLBACK_ROOT:-$DEFAULT_ROOT}"
WORK="${SAMPLE_ANALYSIS_WORK:-}"
RESEARCH="$ROOT/docs/research"
PROGRESS="$ROOT/PROGRESS.md"
BLOCKED="$ROOT/BLOCKED.md"
ARTIFACTS=(
  "$RESEARCH/sample-feasibility.md"
  "$RESEARCH/sample-manifest.tsv"
  "$RESEARCH/sample-capability-diff.tsv"
  "$RESEARCH/sample-verification.txt"
  "$RESEARCH/sample-rollback.sh"
)

usage() {
  printf 'usage: %s --check|--run\n' "$0"
}

prefix_check() {
  local path="$1"
  case "$path" in
    "$ROOT/docs/research/"*|"$PROGRESS"|"$BLOCKED") return 0 ;;
    *) printf 'rollback path check failed: %s\n' "$path" >&2; return 1 ;;
  esac
}

marker_state() {
  local path="$1"
  local marker="$2"
  if [[ ! -f "$path" ]]; then
    printf 'absent\n'
    return 0
  fi
  python3 - "$path" "$marker" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
marker = sys.argv[2]
text = path.read_text(encoding="utf-8")
print("present" if marker in text else "absent")
PY
}

check_only() {
  local bad=0
  local research_count=0
  for path in "${ARTIFACTS[@]}" "$PROGRESS" "$BLOCKED"; do
    prefix_check "$path" || bad=1
  done
  if [[ -d "$RESEARCH" ]]; then
    while IFS= read -r -d '' path; do
      case "$path" in
        "${ARTIFACTS[0]}"|"${ARTIFACTS[1]}"|"${ARTIFACTS[2]}"|"${ARTIFACTS[3]}"|"${ARTIFACTS[4]}") ;;
        *) printf 'rollback unexpected research file: %s\n' "$path"; bad=1 ;;
      esac
    done < <(find "$RESEARCH" -type f -print0)
  fi
  if [[ -n "$WORK" ]]; then
    case "$WORK" in
      /private/tmp/sample-analysis-[0-9][0-9]*) ;;
      *) printf 'rollback temp prefix check failed: %s\n' "$WORK"; bad=1 ;;
    esac
  fi
  if [[ -d "$RESEARCH" ]]; then
    research_count="$(find "$RESEARCH" -type f | wc -l | tr -d ' ')"
  fi
  printf 'rollback check: research files present=%s\n' "$research_count"
  printf 'rollback check: PROGRESS marker=%s\n' "$(marker_state "$PROGRESS" '## Windows 样本静态研究开工')"
  printf 'rollback check: BLOCKED marker=%s\n' "$(marker_state "$BLOCKED" '## Windows 样本静态研究限制')"
  if [[ -n "$WORK" ]]; then
    if [[ -f "$WORK/.sample-analysis-owned" ]]; then
      printf 'rollback check: temp ownership=owned\n'
    else
      printf 'rollback check: temp ownership=marker-missing\n'
    fi
  else
    printf 'rollback check: temp ownership=not-requested\n'
  fi
  printf 'rollback check: no action taken\n'
  return "$bad"
}

run_rollback() {
  check_only
  python3 - "$PROGRESS" "$BLOCKED" <<'PY'
from pathlib import Path
import sys
markers = {
    sys.argv[1]: "\n## Windows \u6837\u672c\u9759\u6001\u7814\u7a76\u5f00\u5de5",
    sys.argv[2]: "\n## Windows \u6837\u672c\u9759\u6001\u7814\u7a76\u9650\u5236",
}
for name, marker in markers.items():
    path = Path(name)
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8")
    index = text.find(marker)
    if index < 0:
        continue
    if text.find("\n## ", index + len(marker)) >= 0:
        raise SystemExit(f"rollback marker is not the final section: {path}")
    path.write_text(text[:index].rstrip() + "\n", encoding="utf-8")
PY
  for path in "${ARTIFACTS[@]}"; do
    prefix_check "$path"
    rm -f -- "$path"
  done
  if [[ -d "$RESEARCH" ]]; then
    if ! rmdir "$RESEARCH" 2>/dev/null; then
      printf 'rollback: retained non-empty research directory\n'
    fi
  fi
  if [[ -n "$WORK" ]]; then
    case "$WORK" in
      /private/tmp/sample-analysis-[0-9][0-9]*) ;;
      *) printf 'rollback temp prefix check failed: %s\n' "$WORK" >&2; return 1 ;;
    esac
    if [[ -f "$WORK/.sample-analysis-owned" ]]; then
      if [[ -f /private/tmp/sample-analysis-current ]] && [[ "$(< /private/tmp/sample-analysis-current)" == "$WORK" ]]; then
        rm -f -- /private/tmp/sample-analysis-current
      fi
      rm -rf -- "$WORK"
      printf 'rollback: removed owned temp work\n'
    else
      printf 'rollback: retained temp work (ownership marker missing)\n'
    fi
  fi
  printf 'rollback complete\n'
}

case "${1:-}" in
  --check) check_only ;;
  --run) run_rollback ;;
  *) usage >&2; exit 2 ;;
esac
