#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 4 )); then
    echo "Usage: $0 FORCE_OUTPUT FLOW_OUTPUT IMAGE_SOURCE OUTPUT" >&2
    exit 2
fi

FORCE_OUTPUT=$1
FLOW_OUTPUT=$2
IMAGE_SOURCE=$3
OUTPUT=$4
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}

mkdir -p "$OUTPUT"
"$PYTHON_BIN" "$SCRIPT_DIR/make_colonius_final_figure.py" \
    --force-output "$FORCE_OUTPUT" \
    --flow-output "$FLOW_OUTPUT" \
    --image-source "$IMAGE_SOURCE" \
    --output "$OUTPUT"

(
    cd "$OUTPUT"
    sha256sum \
        TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png \
        TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.pdf \
        READ_ME_FIRST.md > SHA256SUMS.txt
    zip -q -9 TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.zip \
        TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png \
        TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.pdf \
        READ_ME_FIRST.md \
        SHA256SUMS.txt
)

echo "TIM_COLONIUS_FINAL_FIGURE=PASS"
echo "PNG=$OUTPUT/TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png"
echo "PDF=$OUTPUT/TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.pdf"
echo "ARCHIVE=$OUTPUT/TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.zip"
