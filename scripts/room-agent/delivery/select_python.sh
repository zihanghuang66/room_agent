#!/usr/bin/env bash
# A selector only: never installs or changes the existing app/GPU interpreter.
room_select_python() {
  local candidate resolved version
  local candidates=()
  if [[ -n "${ROOM_ORCH_BOOTSTRAP_PYTHON:-}" ]]; then
    candidates=("$ROOM_ORCH_BOOTSTRAP_PYTHON")
  else
    if [[ -n "${ROOM_APP_PYTHON:-}" ]]; then candidates+=("$ROOM_APP_PYTHON"); fi
    candidates+=(/opt/room/envs/app/bin/python /usr/bin/python3.10 python3.10 python3.11 python3.12 python3)
  fi
  for candidate in "${candidates[@]}"; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -z "$resolved" ]]; then
      printf 'PYTHON_CANDIDATE: %s NOT_FOUND\n' "$candidate" >&2
      continue
    fi
    if version="$("$resolved" -c 'import sys; print(".".join(map(str,sys.version_info[:3]))); sys.exit(0 if sys.version_info >= (3,10) else 3)' 2>&1)"; then
      printf 'PYTHON_CANDIDATE: %s %s SUPPORTED\n' "$resolved" "$version" >&2
      printf '%s\n' "$resolved"
      return 0
    fi
    printf 'PYTHON_CANDIDATE: %s %s UNSUPPORTED_OR_BROKEN\n' "$resolved" "$version" >&2
  done
  printf 'No working Python 3.10+ found; see python_candidates.log. Existing app/GPU environments were not changed.\n' >&2
  return 1
}
