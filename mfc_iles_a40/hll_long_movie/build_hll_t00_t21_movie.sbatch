#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=06:00:00

set -Eeuo pipefail
: "${CHAIN:?}"
: "${SOURCE_T06:?}"
: "${SCRIPT:?}"
: "${OUTPUT:?}"

# Keep the submitted Miniforge environment. Unity has no
# conda/miniforge3-24.7.1 module and no system ffmpeg.
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export MPLCONFIGDIR="$OUTPUT/matplotlib-cache"
mkdir -p "$OUTPUT" "$MPLCONFIGDIR"

python3 - <<'PY'
import numpy, matplotlib, PIL, imageio_ffmpeg
print("COMPUTE_NODE_PYTHON_PREFLIGHT=PASS")
print("ffmpeg", imageio_ffmpeg.get_ffmpeg_exe())
PY

FFMPEG_EXE=$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
test -x "$FFMPEG_EXE"
FFMPEG_BIN_DIR="$OUTPUT/ffmpeg-bin"
mkdir -p "$FFMPEG_BIN_DIR"
ln -sf "$FFMPEG_EXE" "$FFMPEG_BIN_DIR/ffmpeg"
export PATH="$FFMPEG_BIN_DIR:$PATH"
command -v ffmpeg
ffmpeg -version | head -n1

VORT_T06=$(find "$SOURCE_T06/movie_products" -maxdepth 1 -type f \
  -name '*vorticity*.mp4' -print -quit)
SCHL_T06=$(find "$SOURCE_T06/movie_products" -maxdepth 1 -type f \
  \( -name '*schlieren*.mp4' -o -name '*shock*.mp4' \) -print -quit)
test -s "$VORT_T06"
test -s "$SCHL_T06"

python3 "$SCRIPT" "$CHAIN" --output "$OUTPUT" --fps 20 --stride 1 \
  2>&1 | tee "$OUTPUT/movie-build-t06-t21.log"

T06_T21="$OUTPUT/MFC_HLL_T06_T21_SCHLIEREN_VORTICITY.mp4"
T00_T06="$OUTPUT/segment_t00_t06.mp4"
T06_T21_NORM="$OUTPUT/segment_t06_t21.mp4"
FINAL="$OUTPUT/MFC_HLL_T00_T21_SCHLIEREN_VORTICITY.mp4"

# Existing t=0..6 movies contain every Delta-t=0.05 frame but use 2 fps.
# Speed them by 10x, preserve every frame, and assemble the two panels.
ffmpeg -y -loglevel warning \
  -i "$SCHL_T06" -i "$VORT_T06" \
  -filter_complex \
  "[0:v]setpts=0.1*PTS,scale=1080:720:force_original_aspect_ratio=decrease,pad=1080:720:(ow-iw)/2:(oh-ih)/2:white,fps=20[s];\
[1:v]setpts=0.1*PTS,scale=1080:720:force_original_aspect_ratio=decrease,pad=1080:720:(ow-iw)/2:(oh-ih)/2:white,fps=20[v];\
[s][v]hstack=inputs=2,setsar=1[out]" \
  -map '[out]' -an -c:v libx264 -preset medium -crf 19 \
  -pix_fmt yuv420p -movflags +faststart "$T00_T06"

# Remove the duplicate t=6 frame from the continuation.
ffmpeg -y -loglevel warning -i "$T06_T21" \
  -vf "select='gte(n,1)',setpts=N/(20*TB),scale=2160:720:force_original_aspect_ratio=decrease,pad=2160:720:(ow-iw)/2:(oh-ih)/2:white,setsar=1" \
  -an -r 20 -c:v libx264 -preset medium -crf 19 \
  -pix_fmt yuv420p -movflags +faststart "$T06_T21_NORM"

# Re-encode the join so differing historical encoder time bases cannot break it.
ffmpeg -y -loglevel warning -i "$T00_T06" -i "$T06_T21_NORM" \
  -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0[out]" \
  -map '[out]' -an -r 20 -c:v libx264 -preset medium -crf 19 \
  -pix_fmt yuv420p -movflags +faststart "$FINAL"

test -s "$OUTPUT/MFC_HLL_T06_T21_MOVIE_OK.txt"
test -s "$OUTPUT/MFC_HLL_T21_MOVIE_PREVIEW.png"
test -s "$FINAL"

read -r FRAMES DURATION < <(python3 - "$FINAL" <<'PY'
import sys
import imageio_ffmpeg
reader = imageio_ffmpeg.read_frames(sys.argv[1], pix_fmt="rgb24")
meta = next(reader)
fps = float(meta["fps"])
duration = float(meta["duration"])
frames = int(round(fps * duration))
print(frames, f"{duration:.6f}")
PY
)

python3 - "$FRAMES" "$DURATION" <<'PY'
import sys
frames = int(sys.argv[1])
duration = float(sys.argv[2])
assert 415 <= frames <= 425, (frames, duration)
assert 20.0 <= duration <= 22.0, (frames, duration)
print(f"FINAL_MOVIE_GATE=PASS frames={frames} duration={duration:.3f}")
PY

cd "$OUTPUT"
sha256sum "$(basename "$FINAL")" >"$(basename "$FINAL").sha256.txt"
cat >MFC_HLL_T00_T21_MOVIE_OK.txt <<EOF
status=PASS
time_range=0:21
save_dt=0.05
fps=20
frames=$FRAMES
duration_seconds=$DURATION
source_t00_t06_schlieren=$SCHL_T06
source_t00_t06_vorticity=$VORT_T06
EOF

echo "MFC_HLL_T00_T21_MOVIE=PASS"
echo "FINAL_MOVIE=$FINAL"
echo "PREVIEW=$OUTPUT/MFC_HLL_T21_MOVIE_PREVIEW.png"
echo "SHA256=$FINAL.sha256.txt"
