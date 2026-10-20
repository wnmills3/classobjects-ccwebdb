# shellcheck shell=bash
# ---------------------------------------------------------------------------
#  ccweb_env.sh - put the ccwebdb conda environment in play for the caller.
#
#  Usage, from another script in this directory, or in your own shell:
#
#      . "$(dirname "${BASH_SOURCE[0]}")/ccweb_env.sh" || exit 2
#      . scripts/ccweb_env.sh
#
#  Sourced, never run: activation has to change the shell that sourced it.
#  The bash twin of ccweb_env.cmd; see that file for why the scripts
#  activate the environment at all.
#
#  Which environment, and where conda is, are CCWEB_CONDA_ENV (default
#  ccwebdb) and CCWEB_CONDA_ROOT: from the environment when set there,
#  otherwise from the repository's .env. They are read here, by bash,
#  because Python is what this script is finding.
#
#      bash scripts/ccweb_env.sh --read-dotenv    print the name it would use
#
#  Sets, in the caller's shell:
#    ENVDIR           the environment (from CONDA_PREFIX, not a guess)
#    PGBIN            PostgreSQL's binaries inside it
#    CCWEB_ENV_STATE  "already active" or "activated"
#
#  Returns 0 when the environment is active, 1 when it could not be put in play.
# ---------------------------------------------------------------------------

_ccweb_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# One key from the repository's .env, as the API's reader takes it: comment
# lines skipped, a CR or a UTF-8 BOM ignored, one pair of quotes dropped,
# the last line naming the key winning. Prints nothing when it is absent.
_ccweb_dotenv() {
    local key="$1" line value="" found=""
    [ -f "$_ccweb_repo/.env" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        line="${line#$'\xef\xbb\xbf'}"
        case "$line" in
            "$key="*) value="${line#"$key="}"; found=1 ;;
        esac
    done < "$_ccweb_repo/.env"
    [ -n "$found" ] || return 0
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
        \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    printf '%s\n' "$value"
}

_ccweb_envname="${CCWEB_CONDA_ENV:-$(_ccweb_dotenv CCWEB_CONDA_ENV)}"
_ccweb_envname="${_ccweb_envname:-ccwebdb}"
_ccweb_condaroot="${CCWEB_CONDA_ROOT:-$(_ccweb_dotenv CCWEB_CONDA_ROOT)}"

if [ "${1:-}" = "--read-dotenv" ]; then
    echo "CCWEB_CONDA_ENV=$_ccweb_envname"
    unset -f _ccweb_dotenv
    unset _ccweb_repo _ccweb_envname _ccweb_condaroot
    if (return 0 2>/dev/null); then return 0; fi
    exit 0
fi

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "ERROR: source this script instead of running it:  . scripts/ccweb_env.sh" >&2
    exit 2
fi

_ccweb_env_done() {
    unset -f _ccweb_dotenv _ccweb_env_done
    unset _ccweb_repo _ccweb_envname _ccweb_condaroot _ccweb_conda
    return "$1"
}

# --- already in play? -------------------------------------------------------
# "Active" has to be true of PATH, not only of the variables describing it,
# so the claim is accepted only when the first python on PATH is the
# environment's own.
CCWEB_ENV_STATE=""
if [ "${CONDA_DEFAULT_ENV:-}" = "$_ccweb_envname" ] && [ -n "${CONDA_PREFIX:-}" ] \
    && [ "$(command -v python 2>/dev/null)" = "$CONDA_PREFIX/bin/python" ]; then
    CCWEB_ENV_STATE="already active"
fi

if [ -z "$CCWEB_ENV_STATE" ]; then
    # --- find conda ---------------------------------------------------------
    _ccweb_conda=""
    if [ -n "$_ccweb_condaroot" ] && [ -x "$_ccweb_condaroot/bin/conda" ]; then
        _ccweb_conda="$_ccweb_condaroot/bin/conda"
    elif [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then
        _ccweb_conda="$CONDA_EXE"
    elif command -v conda >/dev/null 2>&1 && [ -x "$(command -v conda)" ]; then
        _ccweb_conda="$(command -v conda)"
    elif [ -x "$HOME/miniforge3/bin/conda" ]; then
        _ccweb_conda="$HOME/miniforge3/bin/conda"
    fi
    if [ -z "$_ccweb_conda" ]; then
        echo "ERROR: conda not found"
        echo "       looked at CCWEB_CONDA_ROOT, CONDA_EXE, conda on PATH, and"
        echo "       $HOME/miniforge3/bin/conda"
        echo "       see docs/environment-setup-linux.md"
        _ccweb_env_done 1
        return 1
    fi

    # --- activate -----------------------------------------------------------
    # conda's own shell functions read variables that may be unset, so a
    # caller's `set -u` is suspended around them and then restored.
    case $- in *u*) _ccweb_nounset=1; set +u ;; *) _ccweb_nounset="" ;; esac
    eval "$("$_ccweb_conda" shell.bash hook)"
    conda activate "$_ccweb_envname"
    [ -n "$_ccweb_nounset" ] && set -u
    unset _ccweb_nounset
    if [ "${CONDA_DEFAULT_ENV:-}" != "$_ccweb_envname" ]; then
        echo "ERROR: could not activate the $_ccweb_envname conda environment"
        echo "       CONDA_DEFAULT_ENV is \"${CONDA_DEFAULT_ENV:-}\""
        echo "       create it with:  conda create -n $_ccweb_envname python=3.13"
        _ccweb_env_done 1
        return 1
    fi
    CCWEB_ENV_STATE="activated"
fi

ENVDIR="$CONDA_PREFIX"
PGBIN="$ENVDIR/bin"
export ENVDIR PGBIN CCWEB_ENV_STATE
_ccweb_env_done 0
