#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-crawl4ai}"
IMAGE="${IMAGE:-unclecode/crawl4ai:latest}"
PORT="${PORT:-11235}"
LLM_ENV_FILE="${LLM_ENV_FILE:-.llm.env}"
SHM_SIZE="${SHM_SIZE:-20g}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/deploy_crawl4ai.sh <command>

Commands:
  init-env   Create .llm.env template if missing
  up         Run/recreate crawl4ai container (without --env-file)
  up-llm     Run/recreate crawl4ai container with --env-file .llm.env
  status     Show container status and port mapping
  logs       Tail container logs
  test       Smoke test endpoints in container
  stop       Stop and remove container

Env overrides:
  CONTAINER_NAME (default: crawl4ai)
  IMAGE          (default: unclecode/crawl4ai:latest)
  PORT           (default: 11235)
  LLM_ENV_FILE   (default: .llm.env)
  SHM_SIZE       (default: 1g)
EOF
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

init_env() {
  if [[ -f "$LLM_ENV_FILE" ]]; then
    echo "$LLM_ENV_FILE already exists, skip."
    return
  fi
  cat >"$LLM_ENV_FILE" <<'EOL'
# OpenAI
OPENAI_API_KEY=sk-your-key

# Anthropic
ANTHROPIC_API_KEY=your-anthropic-key

# Other providers as needed
# DEEPSEEK_API_KEY=your-deepseek-key
# GROQ_API_KEY=your-groq-key
# TOGETHER_API_KEY=your-together-key
# MISTRAL_API_KEY=your-mistral-key
# GEMINI_API_TOKEN=your-gemini-token

# Optional: Global LLM settings
# LLM_PROVIDER=openai/gpt-4o-mini
# LLM_TEMPERATURE=0.7
# LLM_BASE_URL=https://api.custom.com/v1

# Optional: Provider-specific overrides
# OPENAI_TEMPERATURE=0.5
# OPENAI_BASE_URL=https://custom-openai.com/v1
# ANTHROPIC_TEMPERATURE=0.3
EOL
  chmod 600 "$LLM_ENV_FILE"
  echo "created $LLM_ENV_FILE (permission 600)"
}

remove_if_exists() {
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
    docker rm -f "$CONTAINER_NAME" >/dev/null
    echo "removed existing container: $CONTAINER_NAME"
  fi
}

run_container() {
  local with_llm="${1:-false}"
  remove_if_exists
  if [[ "$with_llm" == "true" ]]; then
    if [[ ! -f "$LLM_ENV_FILE" ]]; then
      echo "$LLM_ENV_FILE not found. Run: bash scripts/deploy_crawl4ai.sh init-env" >&2
      exit 1
    fi
    docker run -d \
      -p "${PORT}:11235" \
      --name "$CONTAINER_NAME" \
      --env-file "$LLM_ENV_FILE" \
      --shm-size="$SHM_SIZE" \
      "$IMAGE" >/dev/null
    echo "started $CONTAINER_NAME with LLM env ($LLM_ENV_FILE)"
  else
    docker run -d \
      -p "${PORT}:11235" \
      --name "$CONTAINER_NAME" \
      --shm-size="$SHM_SIZE" \
      "$IMAGE" >/dev/null
    echo "started $CONTAINER_NAME without LLM env"
  fi
}

status() {
  docker ps --filter "name=^/${CONTAINER_NAME}$" \
    --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
}

logs() {
  docker logs --tail=120 "$CONTAINER_NAME"
}

test_endpoints() {
  docker exec "$CONTAINER_NAME" sh -lc "python - <<'PY'
import requests
base='http://127.0.0.1:11235'
for p,m in [('/', 'get'), ('/health', 'get'), ('/docs', 'get'), ('/crawl', 'post')]:
    try:
        if m=='get':
            r=requests.get(base+p, timeout=8)
        else:
            r=requests.post(base+p, json={'urls':'https://example.com'}, timeout=20)
        print(f'{p} {r.status_code}')
    except Exception as e:
        print(f'{p} ERR {e}')
PY"
}

stop_and_remove() {
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "stopped and removed: $CONTAINER_NAME"
}

main() {
  need_cmd docker
  local cmd="${1:-}"
  case "$cmd" in
    init-env) init_env ;;
    up) run_container false; status ;;
    up-llm) run_container true; status ;;
    status) status ;;
    logs) logs ;;
    test) test_endpoints ;;
    stop) stop_and_remove ;;
    -h|--help|help|"") usage ;;
    *)
      echo "unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "${1:-}"
