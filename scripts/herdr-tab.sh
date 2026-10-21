#!/bin/sh
# herdr-tab — хоткеи-хелпер для herdr (вызывается из config.toml).
#
#   herdr-tab ws <1..10>     сфокусировать N-й воркспейс
#   herdr-tab n <1..10>     сфокусировать N-й таб текущего воркспейса
#   herdr-tab move <1..10>  унести текущую панель в N-й воркспейс сплитом
#                           и выровнять все панели таба равными долями
#   herdr-tab equalize <tab_id>  выровнять панели таба (ratio = листья/всего)
set -eu

HERDR_SOCK="${HERDR_SOCK:-$HOME/.config/herdr/herdr.sock}"

snapshot() { herdr api snapshot 2>/dev/null; }

# api <method> <params-json> — newline-delimited JSON по unix-сокету
api() {
  printf '{"id":"herdr-tab","method":"%s","params":%s}\n' "$1" "$2" |
    nc -U "$HERDR_SOCK" -w 2 2>/dev/null
}

# equalize <tab_id>: каждому сплиту ratio = leaves(first)/leaves(node) —
# все листья получают равные доли. layout.apply НЕ использовать: он
# пересоздаёт панели (новые pane_id, процессы умирают).
equalize() {
  tree=$(api layout.export "{\"tab_id\":\"$1\"}" | jq -c '.result.layout.root // empty')
  [ -n "$tree" ] || return 0
  printf '%s' "$tree" | jq -r '
    def leaves: if .type == "pane" then 1 else ((.first|leaves)+(.second|leaves)) end;
    def walk($p):
      if .type == "pane" then empty else
        "\($p | tostring) \((.first|leaves) / ((.first|leaves)+(.second|leaves)))",
        (.first | walk($p + [false])),
        (.second | walk($p + [true]))
      end;
    walk([])' |
    while IFS=' ' read -r path ratio; do
      api layout.set_split_ratio \
        "{\"tab_id\":\"$1\",\"path\":$path,\"ratio\":$ratio}" >/dev/null
    done
}

case "${1:-}" in
ws)
  n=$2
  wid=$(herdr workspace list 2>/dev/null |
    jq -r ".result.workspaces[$((n - 1))].workspace_id // empty")
  [ -n "$wid" ] && herdr workspace focus "$wid" >/dev/null
  ;;
n)
  n=$2
  wid=$(snapshot | jq -r '.result.snapshot.focused_workspace_id')
  tid=$(herdr tab list --workspace "$wid" 2>/dev/null |
    jq -r ".result.tabs[] | select(.number == $n) | .tab_id" | head -1)
  [ -n "$tid" ] && herdr tab focus "$tid" >/dev/null
  ;;
move)
  n=$2
  snap=$(snapshot)
  cur=$(printf '%s' "$snap" | jq -r '.result.snapshot.focused_workspace_id')
  pid=$(printf '%s' "$snap" | jq -r '.result.snapshot.focused_pane_id')
  src_tid=$(printf '%s' "$snap" | jq -r '.result.snapshot.focused_tab_id // empty')
  ws=$(herdr workspace list 2>/dev/null |
    jq -c ".result.workspaces[$((n - 1))] // empty")
  wid=$(printf '%s' "$ws" | jq -r '.workspace_id // empty')
  # active_tab_id — у нефокусного ws все табы focused:false, селект по флагу не работает
  tid=$(printf '%s' "$ws" | jq -r '.active_tab_id // empty')
  if [ -z "$wid" ]; then
    # воркспейса N нет — создаём новый и уносим панель туда (первым табом)
    herdr pane move "$pid" --new-workspace --focus >/dev/null
  elif [ "$wid" != "$cur" ]; then
    [ -z "$tid" ] && tid=$(herdr tab list --workspace "$wid" 2>/dev/null |
      jq -r '.result.tabs[0].tab_id // empty')
    if [ -n "$tid" ]; then
      herdr pane move "$pid" --tab "$tid" --split right --focus >/dev/null
    else
      herdr pane move "$pid" --workspace "$wid" --new-tab --focus >/dev/null
    fi
    equalize "$tid"
  fi
  # источник: ws мог опустеть и закрыться — равняем, только если жив
  src_alive=$(herdr workspace list 2>/dev/null |
    jq -r --arg w "$cur" '.result.workspaces[] | select(.workspace_id == $w) | .active_tab_id // empty')
  [ -n "$src_alive" ] && [ "$src_alive" != "$tid" ] && equalize "$src_alive"
  ;;
equalize)
  equalize "$2"
  ;;
*)
  echo "usage: herdr-tab ws|n|move|equalize <1..10|tab_id>" >&2
  exit 2
  ;;
esac
