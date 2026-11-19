#!/bin/sh
# Run in a disposable Paperclip container with networking disabled and no state mounts.
set -eu

grep -Eq '^CapEff:[[:space:]]+0+$' /proc/self/status
grep -Eq '^CapBnd:[[:space:]]+0+$' /proc/self/status
grep -Eq '^NoNewPrivs:[[:space:]]+1$' /proc/self/status
grep -Eq '^Seccomp:[[:space:]]+2$' /proc/self/status

mkdir /tmp/workspace /tmp/private
printf outside > /tmp/private/sentinel
cd /tmp/workspace
if unshare --mount true 2>/dev/null || mount -t tmpfs tmpfs /tmp/private 2>/dev/null; then
    printf 'Parent container gained mount authority\n' >&2
    exit 1
fi

node -e 'require("node:http").createServer((_,res)=>res.end("probe")).listen(20831,"127.0.0.1")' &
probe_pid=$!
trap 'kill "$probe_pid"' EXIT
ready=
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 1 http://127.0.0.1:20831/ >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep .1
done
test "$ready" = 1

# The substitutions below run inside the sandboxed shell.
# shellcheck disable=SC2016
codex -c 'default_permissions="paperclip-probe"' \
    -c 'permissions.paperclip-probe.filesystem={":root"="none",":minimal"="read",":tmpdir"="none",":workspace_roots"={"."="write"}}' \
    -c 'permissions.paperclip-probe.network.enabled=false' \
    sandbox -- /bin/sh -eu -c '
        printf inside > allowed
        test "$(cat allowed)" = inside
        test ! -r /tmp/private/sentinel
        if (printf forbidden > /tmp/private/sentinel) 2>/dev/null; then exit 21; fi
        if curl -fsS --max-time 2 http://127.0.0.1:20831/ >/dev/null 2>&1; then exit 22; fi
        if test -r /proc/1/environ; then exit 23; fi
    '
test "$(cat /tmp/private/sentinel)" = outside
printf 'Codex sandbox verified workspace access and blocked outside files, process environment, network, and outer mounts\n'
