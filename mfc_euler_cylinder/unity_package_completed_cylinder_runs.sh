#!/usr/bin/env bash
set -Eeuo pipefail

# Discover the latest PASS-marked Euler baseline, Euler grid/CFL controls, and
# recovered viscous run, then submit one non-destructive Slurm packaging job.
# Raw restart directories remain in place on Unity and are excluded only from
# the transport archives because the Silo/binary products contain the fields
# required by the local shock/vortex audit.

DEFAULT_SCRATCH_ROOT=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv
BASELINE_ROOT="${BASELINE_ROOT:-$DEFAULT_SCRATCH_ROOT/mfc_euler_cylinder_long}"
CONTROL_ROOT="${CONTROL_ROOT:-$DEFAULT_SCRATCH_ROOT/mfc_euler_cylinder_vortex_controls}"
VISCOUS_ROOT="${VISCOUS_ROOT:-$DEFAULT_SCRATCH_ROOT/mfc_viscous_cylinder_recovery}"
PACKAGE_ROOT="${PACKAGE_ROOT:-$DEFAULT_SCRATCH_ROOT/cylinder_download_packages}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

for root in "$BASELINE_ROOT" "$CONTROL_ROOT" "$VISCOUS_ROOT"; do
    [[ "$root" == /* && "$root" != / && -d "$root" ]] || {
        echo "ERROR: required non-root source directory is missing: $root" >&2
        exit 2
    }
done
[[ "$PACKAGE_ROOT" == /* && "$PACKAGE_ROOT" != / ]] || {
    echo "ERROR: PACKAGE_ROOT must be a non-root absolute path." >&2
    exit 2
}

latest_pass_marker() {
    local root="$1"
    local filename="$2"
    local required_token="$3"
    python3 - "$root" "$filename" "$required_token" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
filename = sys.argv[2]
token = sys.argv[3]
candidates = []
for path in root.rglob(filename):
    normalized = path.as_posix()
    if "/smoke/" in normalized:
        continue
    if token != "-" and token not in normalized:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "status=PASS" not in text.splitlines():
            continue
        candidates.append((path.stat().st_mtime_ns, path))
    except OSError:
        continue
if not candidates:
    raise SystemExit(
        f"ERROR: no PASS marker {filename!r} containing {token!r} under {root}"
    )
print(max(candidates)[1])
PY
}

BASELINE_MARKER="$(latest_pass_marker \
    "$BASELINE_ROOT" RUN_OK_MFC_EULER_CYLINDER.txt -)"
GRID_MARKER="$(latest_pass_marker \
    "$CONTROL_ROOT" RUN_OK_MFC_EULER_CYLINDER.txt grid_f180_cfl0p20)"
CFL_MARKER="$(latest_pass_marker \
    "$CONTROL_ROOT" RUN_OK_MFC_EULER_CYLINDER.txt timestep_f90_cfl0p10)"
VISCOUS_MARKER="$(latest_pass_marker \
    "$VISCOUS_ROOT" RUN_OK_MFC_VISCOUS_CYLINDER_RECOVERED.txt /production/)"

BASELINE_CASE_DIR="$(dirname "$BASELINE_MARKER")"
GRID_CASE_DIR="$(dirname "$GRID_MARKER")"
CFL_CASE_DIR="$(dirname "$CFL_MARKER")"
VISCOUS_CASE_DIR="$(dirname "$VISCOUS_MARKER")"
BASELINE_RUN_ROOT="$(dirname "$BASELINE_CASE_DIR")"
GRID_RUN_ROOT="$(dirname "$GRID_CASE_DIR")"
CFL_RUN_ROOT="$(dirname "$CFL_CASE_DIR")"
VISCOUS_RUN_ROOT="$(dirname "$VISCOUS_CASE_DIR")"

validate_product() {
    local label="$1"
    local marker="$2"
    local final_product
    grep -qx 'status=PASS' "$marker" || {
        echo "ERROR: $label marker does not report PASS: $marker" >&2
        exit 3
    }
    final_product="$(awk -F= '$1 == "final_product" {sub($1 "=", ""); print}' "$marker")"
    [[ -n "$final_product" && -s "$final_product" ]] || {
        echo "ERROR: $label final product is missing: $final_product" >&2
        exit 3
    }
}

validate_product euler_baseline "$BASELINE_MARKER"
validate_product euler_grid_f180 "$GRID_MARKER"
validate_product euler_cfl_f90 "$CFL_MARKER"
validate_product viscous_recovered "$VISCOUS_MARKER"

PACKAGE_DIR="$PACKAGE_ROOT/cylinder_complete_$STAMP"
[[ ! -e "$PACKAGE_DIR" ]] || {
    echo "ERROR: package directory already exists: $PACKAGE_DIR" >&2
    exit 2
}
mkdir -p "$PACKAGE_DIR"

SELECTION="$PACKAGE_DIR/SELECTED_RUNS.tsv"
printf 'label\tmarker\trun_root\tcase_dir\n' >"$SELECTION"
printf 'euler_baseline\t%s\t%s\t%s\n' \
    "$BASELINE_MARKER" "$BASELINE_RUN_ROOT" "$BASELINE_CASE_DIR" >>"$SELECTION"
printf 'euler_grid_f180\t%s\t%s\t%s\n' \
    "$GRID_MARKER" "$GRID_RUN_ROOT" "$GRID_CASE_DIR" >>"$SELECTION"
printf 'euler_cfl_f90\t%s\t%s\t%s\n' \
    "$CFL_MARKER" "$CFL_RUN_ROOT" "$CFL_CASE_DIR" >>"$SELECTION"
printf 'viscous_recovered\t%s\t%s\t%s\n' \
    "$VISCOUS_MARKER" "$VISCOUS_RUN_ROOT" "$VISCOUS_CASE_DIR" >>"$SELECTION"

SBATCH_FILE="$PACKAGE_DIR/package_completed_cylinder_runs.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=1-00:00:00

set -Eeuo pipefail
: "${PACKAGE_DIR:?}"
: "${BASELINE_RUN_ROOT:?}"
: "${GRID_RUN_ROOT:?}"
: "${CFL_RUN_ROOT:?}"
: "${VISCOUS_RUN_ROOT:?}"

INDEX="$PACKAGE_DIR/PACKAGE_INDEX.tsv"
printf 'label\tsource_run\tarchive\tbytes\tsha256\n' >"$INDEX"

package_one() {
    local label="$1"
    local source_run="$2"
    local source_parent
    local source_name
    local archive="$PACKAGE_DIR/${label}.tar.gz"
    local checksum
    local -a tar_args

    [[ "$source_run" == /* && "$source_run" != / && -d "$source_run" ]] || {
        echo "ERROR: unsafe or missing source run for $label: $source_run" >&2
        exit 3
    }
    [[ ! -e "$archive" ]] || {
        echo "ERROR: refusing to overwrite $archive" >&2
        exit 3
    }
    source_parent="$(dirname "$source_run")"
    source_name="$(basename "$source_run")"
    tar_args=(
        --exclude='*/restart_data'
        --exclude='*/restart_data/*'
        --exclude='*/silo_hdf5_failed*'
        --exclude='*/silo_hdf5_failed*/*'
        --exclude='*/smoke'
        --exclude='*/smoke/*'
        --exclude='*.core'
    )

    if command -v pigz >/dev/null 2>&1; then
        tar -C "$source_parent" "${tar_args[@]}" -cf - "$source_name" | \
            pigz -p "${SLURM_CPUS_PER_TASK:-4}" >"$archive"
    else
        tar -C "$source_parent" "${tar_args[@]}" -czf "$archive" "$source_name"
    fi
    [[ -s "$archive" ]] || { echo "ERROR: empty archive $archive" >&2; exit 4; }
    gzip -t "$archive"
    tar -tzf "$archive" >/dev/null
    sha256sum "$archive" >"$archive.sha256.txt"
    checksum="$(awk '{print $1}' "$archive.sha256.txt")"
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "$label" "$source_run" "$archive" "$(stat -c %s "$archive")" \
        "$checksum" >>"$INDEX"
}

package_one euler_baseline "$BASELINE_RUN_ROOT"
package_one euler_grid_f180 "$GRID_RUN_ROOT"
package_one euler_cfl_f90 "$CFL_RUN_ROOT"
package_one viscous_recovered "$VISCOUS_RUN_ROOT"

printf 'status=PASS\narchives=4\nindex=%s\n' "$INDEX" | \
    tee "$PACKAGE_DIR/RUN_OK_CYLINDER_PACKAGES.txt"
du -sh "$PACKAGE_DIR"
SBATCH

PACKAGE_JOB="$(sbatch --parsable \
    --job-name=mfc-cyl-package-all \
    --output="$PACKAGE_DIR/slurm-%j.out" \
    --error="$PACKAGE_DIR/slurm-%j.err" \
    --export="ALL,PACKAGE_DIR=$PACKAGE_DIR,BASELINE_RUN_ROOT=$BASELINE_RUN_ROOT,GRID_RUN_ROOT=$GRID_RUN_ROOT,CFL_RUN_ROOT=$CFL_RUN_ROOT,VISCOUS_RUN_ROOT=$VISCOUS_RUN_ROOT" \
    "$SBATCH_FILE")"
PACKAGE_JOB="${PACKAGE_JOB%%;*}"
[[ "$PACKAGE_JOB" =~ ^[0-9]+$ ]] || {
    echo "ERROR: invalid Slurm package job ID: $PACKAGE_JOB" >&2
    exit 4
}

ENV_FILE="$PACKAGE_DIR/submission.env"
{
    printf 'PACKAGE_DIR=%q\n' "$PACKAGE_DIR"
    printf 'PACKAGE_JOB=%q\n' "$PACKAGE_JOB"
    printf 'SELECTION=%q\n' "$SELECTION"
} >"$ENV_FILE"

echo "PACKAGE_DIR=$PACKAGE_DIR"
echo "PACKAGE_JOB=$PACKAGE_JOB"
echo "SELECTION=$SELECTION"
echo "ENV_FILE=$ENV_FILE"
echo "NEXT: squeue -j $PACKAGE_JOB"
echo "WHEN COMPLETE: cat '$PACKAGE_DIR/RUN_OK_CYLINDER_PACKAGES.txt'"
