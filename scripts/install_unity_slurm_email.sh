#!/usr/bin/env bash
# Install account-wide Slurm completion/failure email notifications on Unity.
#
# Usage:
#   bash scripts/install_unity_slurm_email.sh
#   bash scripts/install_unity_slurm_email.sh --mail-user name@example.edu
#   bash scripts/install_unity_slurm_email.sh --uninstall

set -Eeuo pipefail
umask 077

readonly DEFAULT_MAIL_TYPES="END,FAIL,INVALID_DEPEND,TIME_LIMIT,ARRAY_TASKS"
readonly BLOCK_BEGIN="# >>> unity-slurm-email >>>"
readonly BLOCK_END="# <<< unity-slurm-email <<<"

mail_types="${UNITY_SLURM_MAIL_TYPES:-${DEFAULT_MAIL_TYPES}}"
mail_user=""
uninstall=0

usage() {
    cat <<'EOF'
Install email notifications for every future sbatch submission and update all
currently pending/running batch jobs owned by the current user.

Options:
  --mail-user ADDRESS  Override the email associated with the Unity account.
  --mail-types TYPES   Comma-separated Slurm mail types.
  --uninstall          Remove the sbatch wrapper and shell startup block.
  -h, --help           Show this help.

Default mail types:
  END,FAIL,INVALID_DEPEND,TIME_LIMIT,ARRAY_TASKS
EOF
}

while (($#)); do
    case "$1" in
        --mail-user)
            (($# >= 2)) || { echo "ERROR: --mail-user requires a value" >&2; exit 2; }
            mail_user="$2"
            shift 2
            ;;
        --mail-types)
            (($# >= 2)) || { echo "ERROR: --mail-types requires a value" >&2; exit 2; }
            mail_types="$2"
            shift 2
            ;;
        --uninstall)
            uninstall=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ "${mail_types}" =~ ^[A-Z0-9_,]+$ ]] || {
    echo "ERROR: invalid --mail-types value: ${mail_types}" >&2
    exit 2
}
[[ -z "${mail_user}" || "${mail_user}" != *[[:space:]]* ]] || {
    echo "ERROR: --mail-user must not contain whitespace" >&2
    exit 2
}

readonly bashrc="${UNITY_SLURM_BASHRC:-${HOME}/.bashrc}"
readonly wrapper_dir="${UNITY_SLURM_WRAPPER_DIR:-${HOME}/.local/bin}"
readonly wrapper="${wrapper_dir}/sbatch"

rewrite_bashrc_without_block() {
    local source_file="$1"
    local target_file="$2"

    if [[ ! -f "${source_file}" ]]; then
        : > "${target_file}"
        return
    fi

    awk -v begin="${BLOCK_BEGIN}" -v end="${BLOCK_END}" '
        $0 == begin { skipping = 1; next }
        $0 == end   { skipping = 0; next }
        !skipping   { print }
    ' "${source_file}" > "${target_file}"
}

update_bashrc() {
    local action="$1"
    local tmp
    local backup

    mkdir -p "$(dirname "${bashrc}")"
    tmp="$(mktemp "${bashrc}.tmp.XXXXXX")"
    rewrite_bashrc_without_block "${bashrc}" "${tmp}"

    if [[ "${action}" == "install" ]]; then
        {
            printf '\n%s\n' "${BLOCK_BEGIN}"
            printf '%s\n' "# Route interactive and script-launched sbatch calls through the notifier."
            printf 'export PATH=%q:"$PATH"\n' "${wrapper_dir}"
            printf '%s\n' "${BLOCK_END}"
        } >> "${tmp}"
    fi

    if [[ -f "${bashrc}" ]]; then
        backup="${bashrc}.unity-slurm-email-backup.$(date -u +%Y%m%dT%H%M%SZ)"
        cp -p "${bashrc}" "${backup}"
        echo "Shell startup backup: ${backup}"
    fi
    mv "${tmp}" "${bashrc}"
}

if ((uninstall)); then
    if [[ -f "${wrapper}" ]] && grep -q '^# unity-slurm-email-wrapper$' "${wrapper}"; then
        rm -f -- "${wrapper}"
        echo "Removed wrapper: ${wrapper}"
    elif [[ -e "${wrapper}" ]]; then
        echo "WARNING: kept unrecognized existing file: ${wrapper}" >&2
    fi
    update_bashrc uninstall
    echo "Uninstalled. Run: source ${bashrc}; hash -r"
    exit 0
fi

find_real_sbatch() {
    local candidate

    if [[ -n "${UNITY_REAL_SBATCH:-}" ]]; then
        [[ -x "${UNITY_REAL_SBATCH}" ]] || {
            echo "ERROR: UNITY_REAL_SBATCH is not executable: ${UNITY_REAL_SBATCH}" >&2
            return 1
        }
        printf '%s\n' "${UNITY_REAL_SBATCH}"
        return
    fi

    while IFS= read -r candidate; do
        [[ -n "${candidate}" && "${candidate}" != "${wrapper}" && -x "${candidate}" ]] || continue
        printf '%s\n' "${candidate}"
        return
    done < <(type -a -p sbatch 2>/dev/null || true)

    echo "ERROR: could not locate the real sbatch executable" >&2
    return 1
}

readonly real_sbatch="$(find_real_sbatch)"
mkdir -p "${wrapper_dir}"
if [[ -e "${wrapper}" ]] && ! grep -q '^# unity-slurm-email-wrapper$' "${wrapper}"; then
    echo "ERROR: refusing to replace unrecognized existing file: ${wrapper}" >&2
    exit 3
fi
wrapper_tmp="$(mktemp "${wrapper_dir}/.sbatch-email.XXXXXX")"
{
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' '# unity-slurm-email-wrapper'
    printf '%s\n' 'set -Eeuo pipefail'
    printf 'readonly REAL_SBATCH=%q\n' "${real_sbatch}"
    printf 'readonly MAIL_TYPES=%q\n' "${mail_types}"
    printf '%s\n' 'args=("--mail-type=${MAIL_TYPES}")'
    if [[ -n "${mail_user}" ]]; then
        printf 'args+=(--mail-user=%q)\n' "${mail_user}"
    fi
    printf '%s\n' 'exec "${REAL_SBATCH}" "${args[@]}" "$@"'
} > "${wrapper_tmp}"
chmod 700 "${wrapper_tmp}"
mv "${wrapper_tmp}" "${wrapper}"

update_bashrc install

updated=0
skipped=0
if command -v squeue >/dev/null 2>&1 && command -v scontrol >/dev/null 2>&1; then
    mapfile -t active_jobs < <(
        squeue -h -u "${USER}" -o '%A|%P|%j' |
            awk -F'|' '$1 ~ /^[0-9]+$/ && $2 !~ /^ood-/ && $3 !~ /^sys\// { print $1 }' |
            sort -u
    )

    for job_id in "${active_jobs[@]}"; do
        update_args=("JobId=${job_id}" "MailType=${mail_types}")
        if [[ -n "${mail_user}" ]]; then
            update_args+=("MailUser=${mail_user}")
        fi
        if scontrol update "${update_args[@]}"; then
            echo "Updated active job: ${job_id}"
            ((updated += 1))
        else
            echo "WARNING: could not update job ${job_id}; it may have just ended" >&2
            ((skipped += 1))
        fi
    done
else
    echo "WARNING: squeue/scontrol unavailable; future submissions are configured, but active jobs were not updated" >&2
fi

echo
echo "Installed Slurm email notifications."
echo "Mail types: ${mail_types}"
if [[ -n "${mail_user}" ]]; then
    echo "Mail user: ${mail_user}"
else
    echo "Mail user: email associated with the Unity account"
fi
echo "Active batch jobs updated: ${updated}; skipped: ${skipped}"
echo "Activate now: source ${bashrc}; hash -r"
echo "Verify wrapper: command -v sbatch"
