#!/bin/sh
# Records how a systemd service run ended, as metrics for node_exporter's textfile collector, so Prometheus can
# alert on a failed or missing run. Called from the unit as
#     ExecStopPost=-/opt/emberisegellenes_kartyak/scripts/record_unit_result.sh <metric prefix>
# systemd sets SERVICE_RESULT after every run: success, exit-code, timeout, oom-kill, signal, core-dump, ...
# Writes <prefix>.prom on every run, and <prefix>_success.prom only after a successful one (so a failure keeps the
# time of the last success). The directory belongs to the monitoring stack (~/server-ops install.sh).
set -eu
prefix=$1
dir=${TEXTFILE_DIR:-/var/lib/node_exporter/textfile}
result=${SERVICE_RESULT:-unknown}
now=$(date +%s)
[ "$result" = success ] && ok=1 || ok=0

# Write next to the target and rename: node_exporter never reads a half-written file (it skips non-.prom names).
write() {
    tmp=$(mktemp "$dir/.$1.XXXXXX")
    cat > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$dir/$1.prom"
}

write "$prefix" <<EOF
# HELP ${prefix}_last_run_timestamp_seconds When the last run ended.
# TYPE ${prefix}_last_run_timestamp_seconds gauge
${prefix}_last_run_timestamp_seconds $now
# HELP ${prefix}_last_run_success 1 if the last run succeeded; result is systemd's SERVICE_RESULT.
# TYPE ${prefix}_last_run_success gauge
${prefix}_last_run_success{result="$result"} $ok
EOF

if [ "$ok" = 1 ]; then
    write "${prefix}_success" <<EOF
# HELP ${prefix}_last_success_timestamp_seconds When the last successful run ended.
# TYPE ${prefix}_last_success_timestamp_seconds gauge
${prefix}_last_success_timestamp_seconds $now
EOF
fi
