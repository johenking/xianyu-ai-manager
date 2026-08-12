#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
MODE=${1:---check}

hash_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

FILES='native_browser_helper/server.py
native_browser_helper/installer.py
frontend/services/api/nativeBrowser.ts
frontend/components/AccountList.tsx
tests/test_native_browser_helper.py
frontend/components/AccountList.test.tsx'

post_hash() {
  case "$1" in
    native_browser_helper/server.py) printf '%s\n' cd4c9fab9503a22b74da9e872900f4b03615e72bd4fe12d1d50e771debe4f6c9 ;;
    native_browser_helper/installer.py) printf '%s\n' 1b1886baf180fc6f51608accaefdd73ce5c674b5f3b3b54a0eb7824cd0d7578a ;;
    frontend/services/api/nativeBrowser.ts) printf '%s\n' 368d645f5b03465dcf7eed588349d46138d476719a6fa759aaa3c632e277d41d ;;
    frontend/components/AccountList.tsx) printf '%s\n' b60e2b289370d341c7750d37753f39c9ff76bd3695c416df7d7f1cadf32d5491 ;;
    tests/test_native_browser_helper.py) printf '%s\n' 1d316c33f661ddf28d6417effe5eb2bd7a30d6edede2e2c881d2a49b506300b7 ;;
    frontend/components/AccountList.test.tsx) printf '%s\n' 87ef33dd0bbd37d6813fda3d35107f61d80ddc4fdcd6b8536c7687e6f7a020b2 ;;
    *) return 1 ;;
  esac
}

pre_hash() {
  case "$1" in
    native_browser_helper/server.py) printf '%s\n' 0b0462d6288ceb4865eddc4c276a0882626a6679f660f6c42dcf25ab717e59ff ;;
    native_browser_helper/installer.py) printf '%s\n' 508ec43c262d2cd076a704fb4cf632455d4450b945cd509a45ec89dfeb075b52 ;;
    frontend/services/api/nativeBrowser.ts) printf '%s\n' 36d3dc57daf5889bf81dd78ea36edb44ff9caf5c33444a9bc04709659f24a433 ;;
    frontend/components/AccountList.tsx) printf '%s\n' 7c55349d9b6a17eebd5dcd42c0f6c361323a11592e697d8872f88c2aa3393b42 ;;
    tests/test_native_browser_helper.py) printf '%s\n' e8d90b615e15d8fa515ee7dcf19a9ca52025841bd61102277b035e34ac7d1abc ;;
    frontend/components/AccountList.test.tsx) printf '%s\n' 93de594e28f08c6e4d9fa9812ab8d5af966b369cdc2c5493dbef23a0c69521f7 ;;
    *) return 1 ;;
  esac
}

check_post() {
  for file in $FILES; do
    expected=$(post_hash "$file")
    actual=$(hash_file "$ROOT/$file")
    if [ "$actual" != "$expected" ]; then
      printf 'rollback: current hash mismatch for %s\nexpected=%s\nactual=%s\n' "$file" "$expected" "$actual" >&2
      return 1
    fi
  done
}

check_head_pre() {
  for file in $FILES; do
    expected=$(pre_hash "$file")
    actual=$(git -C "$ROOT" show "HEAD:$file" | shasum -a 256 | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
      printf 'rollback: HEAD baseline mismatch for %s\nexpected=%s\nactual=%s\n' "$file" "$expected" "$actual" >&2
      return 1
    fi
  done
}

check_pre() {
  for file in $FILES; do
    expected=$(pre_hash "$file")
    actual=$(hash_file "$ROOT/$file")
    if [ "$actual" != "$expected" ]; then
      printf 'rollback: restore verification failed for %s\nexpected=%s\nactual=%s\n' "$file" "$expected" "$actual" >&2
      return 1
    fi
  done
}

case "$MODE" in
  --check)
    check_head_pre
    check_post
    printf 'rollback check: post hashes match; concurrent edits would be rejected\n'
    ;;
  --run)
    check_head_pre
    check_post
    temp=$(mktemp -d /private/tmp/sample-optimization-rollback-XXXXXX)
    trap 'rm -rf "$temp"' EXIT INT TERM
    for file in $FILES; do
      target="$ROOT/$file"
      staged="$temp/$(basename "$file").$$"
      git -C "$ROOT" show "HEAD:$file" > "$staged"
      chmod 644 "$staged"
      current=$(hash_file "$target")
      expected=$(post_hash "$file")
      if [ "$current" != "$expected" ]; then
        printf 'rollback: concurrent edit detected for %s; no overwrite\n' "$file" >&2
        exit 1
      fi
      mv "$staged" "$target"
    done
    check_pre
    printf 'rollback: restored six helper/preflight files to their HEAD baseline\n'
    ;;
  *)
    printf 'usage: %s [--check|--run]\n' "$0" >&2
    exit 2
    ;;
esac
