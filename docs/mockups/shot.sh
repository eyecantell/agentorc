#!/usr/bin/env bash
# Screenshot artboards with headless Chromium for a quick look before republishing.
# Usage: docs/mockups/shot.sh [OUT_DIR] [Name ...]   (default: all artboards, out dir /tmp/agentorc-shots)
# Note: this Chromium hangs silently when given a fresh --user-data-dir here, so it uses the default
# profile and runs one page at a time (the default profile is single-instance).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/agentorc-shots}; shift || true
mkdir -p "$out"
ch=$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | tail -1)
[ -n "$ch" ] || ch=$(command -v chromium || command -v google-chrome || true)
[ -n "$ch" ] || { echo "no chromium found" >&2; exit 1; }
names=("$@"); [ ${#names[@]} -gt 0 ] || names=($(cd "$here" && ls *.dc.html | sed 's/\.dc\.html$//'))
for n in "${names[@]}"; do
  src="$here/$n.dc.html"
  python3 - "$src" "$out/$n.html" <<'PY'
import sys, re, pathlib
src, dst = sys.argv[1:]
s = pathlib.Path(src).read_text()
helm = re.search(r"<helmet>(.*?)</helmet>", s, re.S).group(1)
body = s.split("</helmet>", 1)[1].split("</x-dc>")[0]
pathlib.Path(dst).write_text(f"<!doctype html><html><head><meta charset='utf-8'>{helm}</head><body>{body}</body></html>")
PY
  dims=$(grep -o "width: [0-9]*px; min-height: [0-9]*px" "$src" | head -1 | grep -o "[0-9]*" | tr '\n' ' ')
  w=${dims%% *}; h=${dims#* }; h=${h% }
  timeout 90 "$ch" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --window-size="${w:-1440},${h:-900}" --screenshot="$out/$n.png" "file://$out/$n.html" >/dev/null 2>&1 \
    && echo "$n ${w}x${h} -> $out/$n.png" || echo "$n FAILED" >&2
done
