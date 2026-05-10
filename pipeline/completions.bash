# Tab completion for sim2real deploy.py (bash)
# Usage: source $SIM2REAL/pipeline/completions.bash

_sim2real_deploy() {
    local cur prev subcmd
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local subcommands="run status collect cleanup pairs"
    local status_values="pending running done failed timed-out"

    # Find the subcommand (first positional argument)
    subcmd=""
    local i
    for ((i=1; i < COMP_CWORD; i++)); do
        case "${COMP_WORDS[i]}" in
            -*) ;;
            run|status|collect|cleanup|pairs)
                subcmd="${COMP_WORDS[i]}"
                break
                ;;
        esac
    done

    # If no subcommand yet, complete subcommands and global flags
    if [[ -z "$subcmd" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "--run --experiment-root" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$subcommands" -- "$cur"))
        fi
        return
    fi

    # Complete flag values based on prev
    case "$prev" in
        --only)
            local keys
            keys="$(python pipeline/deploy.py pairs --keys-only 2>/dev/null)"
            COMPREPLY=($(compgen -W "$keys" -- "$cur"))
            return
            ;;
        --workload)
            local workloads
            workloads="$(python pipeline/deploy.py pairs --workloads-only 2>/dev/null)"
            COMPREPLY=($(compgen -W "$workloads" -- "$cur"))
            return
            ;;
        --package)
            local packages
            packages="$(python pipeline/deploy.py pairs --packages-only 2>/dev/null)"
            COMPREPLY=($(compgen -W "$packages" -- "$cur"))
            return
            ;;
        --status)
            COMPREPLY=($(compgen -W "$status_values" -- "$cur"))
            return
            ;;
    esac

    # Complete flags for the active subcommand
    if [[ "$cur" == -* ]]; then
        case "$subcmd" in
            run)
                COMPREPLY=($(compgen -W "--only --workload --package --status --force --skip-build-epp --max-retries --poll-interval --gpu-resource-type --default-gpu-cost --pending-threshold --max-pending-stalls" -- "$cur"))
                ;;
            status)
                COMPREPLY=($(compgen -W "--only --workload --package --status" -- "$cur"))
                ;;
            cleanup)
                COMPREPLY=($(compgen -W "--only --workload --package --status --dry-run" -- "$cur"))
                ;;
            collect)
                COMPREPLY=($(compgen -W "--package --skip-logs" -- "$cur"))
                ;;
            pairs)
                COMPREPLY=($(compgen -W "--keys-only --workloads-only --packages-only" -- "$cur"))
                ;;
        esac
    fi
}

complete -F _sim2real_deploy deploy.py
