#!/usr/bin/env bash
# Wrapper: costruisce i pacchetti Lambda e lancia terraform nella cartella giusta.
# Uso: infra/scripts/tf.sh plan|apply|validate|... [argomenti terraform]
# Non fa mai apply automatico: il comando e' quello passato dall'utente.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-aws-personale}"
export AWS_REGION="${AWS_REGION:-eu-north-1}"

case "${1:-}" in
  plan|apply|refresh|import)
    python3 "$ROOT/scripts/build_bot_package.py" --with-deps
    python3 "$ROOT/scripts/build_bot_package.py" --legacy --with-deps
    ;;
  "") echo "uso: $0 <comando terraform> [args]" >&2; exit 2 ;;
esac

exec terraform -chdir="$ROOT/infra/terraform" "$@"
