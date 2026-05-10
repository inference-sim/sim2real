#compdef deploy.py

# Zsh tab-completion for pipeline/deploy.py
#
# Usage — add one of these to your .zshrc:
#   source /path/to/sim2real/pipeline/completions.zsh
#
# The script registers completion for both "deploy.py" (when on $PATH or run
# as ./deploy.py) and "python pipeline/deploy.py" (via a wrapper).

# ── helpers ─────────────────────────────────────────────────────────────────

# Resolve the deploy.py invocation.  Prefer an --experiment-root that the user
# already typed on the current line; fall back to bare "python pipeline/deploy.py".
_deploy_py_cmd() {
    local root=""
    # Scan existing words for --experiment-root VALUE
    local i
    for (( i=1; i < ${#words[@]}; i++ )); do
        if [[ "${words[$i]}" == --experiment-root && -n "${words[$i+1]}" ]]; then
            root="${words[$i+1]}"
            break
        elif [[ "${words[$i]}" == --experiment-root=* ]]; then
            root="${words[$i]#--experiment-root=}"
            break
        fi
    done

    local cmd=(python pipeline/deploy.py)
    [[ -n "$root" ]] && cmd+=(--experiment-root "$root")
    printf '%s\n' "${cmd[@]}"
}

_deploy_py_pair_keys() {
    local -a keys
    keys=( ${(f)"$(python pipeline/deploy.py pairs --keys-only 2>/dev/null)"} )
    compadd -a keys
}

_deploy_py_workloads() {
    local -a wl
    wl=( ${(f)"$(python pipeline/deploy.py pairs --workloads-only 2>/dev/null)"} )
    compadd -a wl
}

_deploy_py_packages() {
    local -a pkg
    pkg=( ${(f)"$(python pipeline/deploy.py pairs --packages-only 2>/dev/null)"} )
    compadd -a pkg
}

_deploy_py_statuses() {
    compadd pending running done failed timed-out
}

# ── main completion function ────────────────────────────────────────────────

_deploy_py() {
    local curcontext="$curcontext" state line
    typeset -A opt_args

    # Top-level: global flags + subcommand
    _arguments -C \
        '--run[Run name]:run name:' \
        '--experiment-root[Experiment repo path]:path:_directories' \
        '1:subcommand:->subcmd' \
        '*::arg:->args' \
        && return

    case "$state" in
    subcmd)
        local -a subcmds=(
            'run:Orchestrate parallel pool execution'
            'status:Show progress of all (workload, package) pairs'
            'collect:Pull results for completed packages'
            'cleanup:Tear down cluster resources for all non-pending pairs'
            'pairs:List available pair keys, workloads, and packages'
        )
        _describe 'subcommand' subcmds
        ;;
    args)
        case "${line[1]}" in
        run)
            _arguments \
                '--only[Scope to one pair key]:pair key:_deploy_py_pair_keys' \
                '--workload[Scope to workload]:workload:_deploy_py_workloads' \
                '--package[Scope to package]:package:_deploy_py_packages' \
                '--status[Scope to status]:status:_deploy_py_statuses' \
                '--force[Reset non-pending pairs to pending]' \
                '--skip-build-epp[Skip EPP image build]' \
                '--max-retries[Max retries per pair]:count:' \
                '--poll-interval[Seconds between polls]:seconds:' \
                '--gpu-resource-type[GPU resource type]:type:' \
                '--default-gpu-cost[GPU cost per pod]:cost:' \
                '--pending-threshold[Pending timeout in seconds]:seconds:' \
                '--max-pending-stalls[Max stall cycles]:count:'
            ;;
        status)
            _arguments \
                '--only[Scope to one pair key]:pair key:_deploy_py_pair_keys' \
                '--workload[Scope to workload]:workload:_deploy_py_workloads' \
                '--package[Scope to package]:package:_deploy_py_packages' \
                '--status[Scope to status]:status:_deploy_py_statuses'
            ;;
        collect)
            _arguments \
                '--package[Scope to package]:package:_deploy_py_packages' \
                '--skip-logs[Collect traces only, skip large logs]'
            ;;
        cleanup)
            _arguments \
                '--only[Scope to one pair key]:pair key:_deploy_py_pair_keys' \
                '--workload[Scope to workload]:workload:_deploy_py_workloads' \
                '--package[Scope to package]:package:_deploy_py_packages' \
                '--status[Scope to status]:status:_deploy_py_statuses' \
                '--dry-run[Preview what would be cleaned up]'
            ;;
        pairs)
            _arguments \
                '(--workloads-only --packages-only)--keys-only[Print pair keys only]' \
                '(--keys-only --packages-only)--workloads-only[Print workload names only]' \
                '(--keys-only --workloads-only)--packages-only[Print package names only]'
            ;;
        esac
        ;;
    esac
}

compdef _deploy_py deploy.py

# Also handle "python pipeline/deploy.py ..." — zsh sees "python" as the
# command, so we wrap with a handler that strips the script argument.
_python_deploy_py() {
    # Only activate when the first argument is pipeline/deploy.py (or a path
    # ending in deploy.py).
    if [[ "${words[2]}" == */deploy.py || "${words[2]}" == deploy.py ]]; then
        # Shift words so the completion logic sees "deploy.py subcmd ..."
        words=( deploy.py "${words[@]:2}" )
        (( CURRENT -= 1 ))
        _deploy_py
    else
        _normal
    fi
}

compdef _python_deploy_py python
compdef _python_deploy_py python3
