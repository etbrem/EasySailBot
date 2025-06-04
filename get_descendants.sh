#!/bin/bash

# Function to get direct children of a PID using /proc (Linux-specific, most reliable)
# Fallback to ps parsing if /proc/children is not available
get_direct_children() {
    local parent_pid=$1
    if [ -d "/proc/$parent_pid/task/$parent_pid" ] && [ -f "/proc/$parent_pid/task/$parent_pid/children" ]; then
        cat "/proc/$parent_pid/task/$parent_pid/children" 2>/dev/null
    else
        # Fallback for systems without /proc/PID/task/PID/children or if inaccessible
        ps -o ppid=,pid= -A | awk -v ppid="$parent_pid" '$1 == ppid {print $2}'
    fi
}

# Recursive function to get all descendants
get_all_descendants_recursive() {
    local current_pid=$1
    local children=$(get_direct_children "$current_pid")
    local descendant_pids=""

    for child_pid in $children; do
        descendant_pids+="$child_pid "
        descendant_pids+=$(get_all_descendants_recursive "$child_pid")
    done
    echo "$descendant_pids"
}

# Main execution: call the recursive function and print unique, sorted PIDs
if [ -z "$1" ]; then
    echo "Usage: $0 <PID>" >&2 # Output usage to stderr
    exit 1
fi

INITIAL_PID=$1
# Collect all descendants, sort them numerically, and get unique PIDs
# We don't include the INITIAL_PID itself in this list of descendants
ALL_DESCENDANTS=$(get_all_descendants_recursive "$INITIAL_PID" | tr ' ' '\n' | grep -v "^$$INITIAL_PID$$" | sort -n | uniq)

echo "$ALL_DESCENDANTS"