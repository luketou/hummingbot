#!/bin/bash

set -euo pipefail

IMAGE="${HB_DOCKER_TEST_IMAGE:-hummingbot-hummingbot:latest}"
MODE="focused"

usage() {
    cat <<'EOF'
Usage: ./run_avellaneda_tests_in_docker.sh [--package]

Runs Avellaneda perpetual market making tests inside Docker using an existing
Hummingbot image with compiled extensions already present.

Options:
  --package   Run the whole avellaneda_perpetual_making test package
  -h, --help  Show this help message

Environment:
  HB_DOCKER_TEST_IMAGE   Override the Docker image to use
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --package)
            MODE="package"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker CLI is not installed or not in PATH." >&2
    exit 1
fi

if ! timeout 10 docker info >/dev/null 2>&1; then
    echo "Docker daemon is not reachable." >&2
    exit 1
fi

if ! timeout 10 docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Docker image not found: $IMAGE" >&2
    echo "Build it first or set HB_DOCKER_TEST_IMAGE to an existing image." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$MODE" = "focused" ]; then
    PYTEST_TARGETS=(
        "test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_stale_close_orders.py"
        "test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py"
    )
    TEST_COPY_COMMANDS='
rm -rf "$tmp_dir/test/hummingbot/strategy/avellaneda_perpetual_making"
mkdir -p "$tmp_dir/test/hummingbot/strategy"
cp -a /workspace/test/hummingbot/strategy/avellaneda_perpetual_making "$tmp_dir/test/hummingbot/strategy/"
'
else
    PYTEST_TARGETS=(
        "test/hummingbot/strategy/avellaneda_perpetual_making"
    )
    TEST_COPY_COMMANDS='
rm -rf "$tmp_dir/test"
cp -a /workspace/test "$tmp_dir/"
'
fi

printf 'Running Avellaneda tests in Docker\n'
printf '  image: %s\n' "$IMAGE"
printf '  mode: %s\n' "$MODE"

docker run --rm \
    -v "$REPO_ROOT:/workspace:ro" \
    --entrypoint /bin/bash \
    "$IMAGE" \
    -lc "
set -euo pipefail
tmp_dir=\$(mktemp -d /tmp/hb-avellaneda-tests-XXXXXX)
trap 'rm -rf \"\$tmp_dir\"' EXIT

cp -a /home/hummingbot/. \"\$tmp_dir\"/

rm -rf \"\$tmp_dir/hummingbot/strategy/avellaneda_perpetual_making\"
mkdir -p \"\$tmp_dir/hummingbot/strategy\"
cp -a /workspace/hummingbot/strategy/avellaneda_perpetual_making \"\$tmp_dir/hummingbot/strategy/\"

${TEST_COPY_COMMANDS}

cd \"\$tmp_dir\"
/opt/conda/envs/hummingbot/bin/python -m pytest ${PYTEST_TARGETS[*]} -q
"
