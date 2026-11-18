#!/usr/bin/env bash
# Prüft im gebauten Frontend-Image, dass nginx ALLE ausgelieferten Skripte
# (.js und .mjs) mit einem JavaScript-MIME-Typ ausliefert.
#
#   Aufruf:  verify-mime-types.sh <frontend-image>
#
# Hintergrund: Die mime.types von nginx kennen .mjs nicht. Der von Vite
# gebündelte PDF.js-Worker (assets/pdf.worker.min-*.mjs) ging deshalb als
# application/octet-stream raus; Browser verweigern Modul-Skripte/-Worker mit
# diesem Typ -> die PDF-Vorschau blieb leer. Der Build selbst blieb dabei grün,
# deshalb dieser Laufzeit-Check gegen das echte Image.
#
# Verwendet von .github/workflows/pr-checks.yml (Job frontend-build).
set -euo pipefail

IMAGE="${1:?Nutzung: verify-mime-types.sh <frontend-image>}"
NAME="dms-frontend-mime-$$"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# nginx löst den proxy_pass-Upstream "backend" schon beim Start auf und bricht
# ohne DNS-Eintrag ab -> für den Check auf localhost zeigen lassen (die API wird
# hier nicht aufgerufen). Port dynamisch und nur auf Loopback veröffentlichen.
docker run -d --name "$NAME" --add-host backend:127.0.0.1 -p 127.0.0.1::80 "$IMAGE" >/dev/null
PORT="$(docker port "$NAME" 80/tcp | head -n1 | sed 's/.*://')"
BASE="http://127.0.0.1:${PORT}"

for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "${BASE}/" 2>/dev/null; then break; fi
  sleep 0.5
done
if ! curl -fsS -o /dev/null "${BASE}/"; then
  echo "FEHLER: nginx im Image ${IMAGE} antwortet nicht." >&2
  docker logs "$NAME" >&2 || true
  exit 1
fi

FILES="$(docker exec "$NAME" sh -c \
  "cd /usr/share/nginx/html && find . -type f \( -name '*.js' -o -name '*.mjs' \) | sed 's#^\./##' | sort")"
if [ -z "$FILES" ]; then
  echo "FEHLER: keine .js/.mjs-Dateien im Image gefunden – Build leer?" >&2
  exit 1
fi

fail=0
count=0
mjs=0
while IFS= read -r f; do
  count=$((count + 1))
  case "$f" in *.mjs) mjs=$((mjs + 1)) ;; esac
  ctype="$(curl -fsSI "${BASE}/${f}" | tr -d '\r' | awk -F': *' 'tolower($1)=="content-type"{print tolower($2)}')"
  case "$ctype" in
    application/javascript*|text/javascript*) ;;
    *)
      echo "FEHLER: /${f} wird als '${ctype:-<kein Content-Type>}' ausgeliefert (erwartet: JavaScript-MIME-Typ)." >&2
      fail=1
      ;;
  esac
done <<EOF_FILES
$FILES
EOF_FILES

if [ "$fail" -ne 0 ]; then
  echo "MIME-Check fehlgeschlagen – siehe frontend/nginx.conf (types-Block)." >&2
  exit 1
fi
echo "OK: ${count} Skript-Dateien (davon ${mjs} .mjs) mit JavaScript-MIME-Typ ausgeliefert."
