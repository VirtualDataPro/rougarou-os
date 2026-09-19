#!/bin/sh
# Resolve recorded installer choices without evaluating their contents.
# Usage: select-packages.sh CORE_LIST PROFILE_MAP CHOICE_RECORD
set -eu
test "$#" -eq 3
awk -F= '
  NF != 2 || $1 !~ /^(agent|docker|podman|herdr)$/ || seen[$1]++ { exit 1 }
  $1 == "agent" && $2 !~ /^(none|codex|opencode|gemini|claude)$/ { exit 1 }
  $1 == "docker" && $2 !~ /^(none|rootless|system)$/ { exit 1 }
  $1 ~ /^(podman|herdr)$/ && $2 !~ /^(true|false)$/ { exit 1 }
  END { if (NR != 4) exit 1 }
' "$3" || { echo 'Invalid or incomplete software choices' >&2; exit 1; }
agent=$(sed -n 's/^agent=//p' "$3")
docker=$(sed -n 's/^docker=//p' "$3")
podman=$(sed -n 's/^podman=//p' "$3")
herdr=$(sed -n 's/^herdr=//p' "$3")
selected=''
test "$agent" = none || selected="$agent"
test "$docker" = none || selected="$selected docker-$docker"
test "$podman" = false || selected="$selected podman"
test "$herdr" = false || selected="$selected herdr"
awk -F'|' -v selected="$selected" '
  FNR == NR {
    if ($0 ~ /^[[:space:]]*(#|$)/) next
    if ($0 !~ /^[a-z0-9][a-z0-9+.-]*$/) { bad=1; exit 1 }
    packages[$0]=1
    next
  }
  /^[[:space:]]*(#|$)/ { next }
  {
    if (NF != 2 || $1 !~ /^[a-z][a-z0-9-]*$/ || ($1 in profiles)) { bad=1; exit 1 }
    profiles[$1]=$2
    count=split($2, roots, /[[:space:]]+/)
    if (!count) { bad=1; exit 1 }
    for (i=1; i<=count; i++) if (roots[i] !~ /^[a-z0-9][a-z0-9+.-]*$/) { bad=1; exit 1 }
  }
  END {
    if (bad) exit 1
    count=split(selected, wanted, /[[:space:]]+/)
    for (i=1; i<=count; i++) {
      if (wanted[i] == "") continue
      if (!(wanted[i] in profiles)) { print "Unavailable selected profile: " wanted[i] > "/dev/stderr"; exit 1 }
      n=split(profiles[wanted[i]], roots, /[[:space:]]+/)
      for (j=1; j<=n; j++) packages[roots[j]]=1
    }
    packages["rougarou-base"]=1
    for (package in packages) print package
  }
' "$1" "$2"
