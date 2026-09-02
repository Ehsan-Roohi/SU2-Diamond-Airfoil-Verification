#!/usr/bin/env bash
#SBATCH --job-name=su2-v3-retro
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=/project/pi_roohie_umass_edu/DGFS_BE/su2-v3-retro-%j.out
#SBATCH --error=/project/pi_roohie_umass_edu/DGFS_BE/su2-v3-retro-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT

set -Eeuo pipefail
umask 077

JOB_ID="${SLURM_JOB_ID:-manual}"
DELIVERY=/project/pi_roohie_umass_edu/DGFS_BE
WORK_BASE=/project/pi_roohie_umass_edu/DART_CFD_PILOT
SRC="${WORK_BASE}/su2-v3-src-${JOB_ID}"
OUT="${WORK_BASE}/su2-v3-results/${JOB_ID}"
PY="${WORK_BASE}/conda/dart-sam3-py311/bin/python"
CHECKPOINT=/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/checkpoints/urans_alpha40/medium_halfdt/URANS_alpha40_medium_halfdt_checkpoint_t012000.zip
BRANCH=agent/v3-shock-conditioned-analytic
REPO=https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification.git

STATUS_FILE="${DELIVERY}/SU2_V3_RETRO_${JOB_ID}_STATUS.txt"
ARCHIVE="${DELIVERY}/SU2_V3_RETRO_${JOB_ID}_COMPLETE.tar.gz"

finish() {
  rc=$?
  {
    echo "job_id=${JOB_ID}"
    echo "exit_code=${rc}"
    if [[ ${rc} -eq 0 ]]; then
      echo "status=COMPLETED"
    else
      echo "status=FAILED"
    fi
    echo "archive=${ARCHIVE}"
    echo "stdout=${DELIVERY}/su2-v3-retro-${JOB_ID}.out"
    echo "stderr=${DELIVERY}/su2-v3-retro-${JOB_ID}.err"
  } > "${STATUS_FILE}"
}
trap finish EXIT

[[ -x "${PY}" ]] || { echo "ERROR: analysis Python missing: ${PY}" >&2; exit 2; }
[[ -s "${CHECKPOINT}" ]] || { echo "ERROR: SU2 checkpoint missing: ${CHECKPOINT}" >&2; exit 2; }
"${PY}" -c 'import numpy, scipy, matplotlib, pyparsing'

rm -rf "${SRC}"
mkdir -p "$(dirname "${OUT}")" "${OUT}"
git clone --depth 1 --branch "${BRANCH}" "${REPO}" "${SRC}"

"${PY}" -m py_compile \
  "${SRC}/research/dart_cfd_pilot/scripts/run_vortex_su2_v3_retrospective.py" \
  "${SRC}/research/dart_cfd_pilot/scripts/run_vortex_analytic_v3_shock_conditioned.py"

set +e
"${PY}" "${SRC}/research/dart_cfd_pilot/scripts/run_vortex_su2_v3_retrospective.py" \
  --checkpoint "${CHECKPOINT}" \
  --output-dir "${OUT}"
RUN_RC=$?
set -e

# Always package the diagnostic outputs, even if a scientific gate fails.
tar --no-same-owner -C "${OUT}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256.txt"

for f in \
  su2_v3_retro_physical.png \
  su2_v3_retro_report.json \
  su2_v3_retro_per_frame.csv
 do
  if [[ -s "${OUT}/${f}" ]]; then
    cp -f "${OUT}/${f}" "${DELIVERY}/SU2_V3_RETRO_${JOB_ID}_${f}"
  fi
 done

if [[ -s "${OUT}/su2_v3_retro_report.json" ]]; then
  "${PY}" - "${OUT}/su2_v3_retro_report.json" <<'PY'
import json, sys
r=json.load(open(sys.argv[1]))
print('FINAL_CLAIM_GATE='+str(r.get('claim_gate')))
for row in r.get('per_snapshot', []):
    print('FRAME', row['frame'], 'STEP', row['source_step'],
          'V2', row['v2_detection_count'], 'V3', row['v3_detection_count'],
          'TARGET_RESCUED', int(row['target_rescued']),
          'NON_TARGET_NEW', row['non_target_new_acceptances'])
PY
fi

echo "SU2_V3_ARCHIVE=${ARCHIVE}"
echo "SU2_V3_PHYSICAL=${DELIVERY}/SU2_V3_RETRO_${JOB_ID}_su2_v3_retro_physical.png"
exit "${RUN_RC}"
