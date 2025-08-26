#!/bin/bash

# Extract the part after sftp/ or fish/
if [[ $1 =~ /(sftp|fish)/([^/]+)(/.+)? ]]; then
    protocol="${BASH_REMATCH[1]}"
    user_host_port="${BASH_REMATCH[2]}"
    path="${BASH_REMATCH[3]}"

    # Check if there is a port
    if [[ $user_host_port =~ ^([^@]+@[^:]+):([0-9]+)$ ]]; then
        user_host="${BASH_REMATCH[1]}"
        port="${BASH_REMATCH[2]}"
        echo "--ssh $user_host:$port:$path"
    else
        echo "--ssh $user_host_port:$path"
    fi
fi
