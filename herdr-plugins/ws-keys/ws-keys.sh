#!/bin/sh
# ws-keys — проставляет каждому воркспейсу строку "[<клавиша>] <имя>" одним
# токеном $key (одинарный пробел; разделитель между токенами herdr шире).
# Идемпотентен: пересчитывает весь список при каждом запуске.
set -eu
HERDR="${HERDR_BIN_PATH:-herdr}"

i=0
while IFS="$(printf '\t')" read -r wid label; do
  [ -n "$wid" ] || continue
  i=$((i + 1))
  k=$(printf 'q\nw\ne\nr\nt\ny\nu\ni\no\np\n' | sed -n "${i}p")
  [ -n "$k" ] && "$HERDR" workspace report-metadata "$wid" --source ws-keys \
    --token "key=[$k] $label" >/dev/null 2>&1 || true
done <<EOF
$("$HERDR" workspace list 2>/dev/null | jq -r '.result.workspaces[] | "\(.workspace_id)\t\(.label)"')
EOF
