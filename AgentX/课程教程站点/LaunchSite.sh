set -euo pipefail

root="$(cd "$(dirname "$0")/" && pwd)"
cd "$root"

exec python -m http.server 8778 --bind 127.0.0.1 --directory .
