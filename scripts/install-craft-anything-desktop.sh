#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
pin_file="$repo_root/config/craft-anything-toolbox.json"
version="$(jq -er '.craft_anything.version' "$pin_file")"
url="$(jq -er '.craft_anything.linux_x86_64_appimage' "$pin_file")"
expected="$(jq -er '.craft_anything.appimage_sha256' "$pin_file")"
operator="${TOOLBOX_OPERATOR_USER:-toolbox-operator}"
operator_home="$(getent passwd "$operator" | cut -d: -f6)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ "$(id -u)" -eq 0 ]] || { echo 'run this installer as root' >&2; exit 1; }
[[ -n "$operator_home" && -d "$operator_home" ]] || { echo "operator account $operator is missing" >&2; exit 1; }
case "$url" in
  "https://github.com/storytold/craft-anything/releases/download/v$version/craft-anything-$version-linux-x86_64.AppImage") ;;
  *) echo 'unexpected Craft Anything AppImage URL' >&2; exit 1 ;;
esac

for command in jq curl apt-get; do
  command -v "$command" >/dev/null || { echo "required command is missing: $command" >&2; exit 1; }
done
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y xvfb x11vnc novnc websockify libxcursor1 libxkbcommon-x11-0
for command in sha256sum systemctl x11vnc Xvfb websockify; do
  command -v "$command" >/dev/null || { echo "required command is missing: $command" >&2; exit 1; }
done
[[ -d /usr/share/novnc ]] || { echo 'noVNC web assets are missing (/usr/share/novnc)' >&2; exit 1; }

curl --fail --location --proto '=https' --tlsv1.2 --output "$work/craft-anything.AppImage" "$url"
printf '%s  %s\n' "$expected" "$work/craft-anything.AppImage" | sha256sum --check --status
install -d -m 0755 /opt/craft-anything "$operator_home/.local/share/craft-anything" "$operator_home/.config"
install -d -m 0755 "$operator_home/Documents"
install -m 0755 "$work/craft-anything.AppImage" /opt/craft-anything/craft-anything.AppImage
chown -R "$operator:$operator" "$operator_home/.local/share/craft-anything"
chown "$operator:$operator" "$operator_home/.config" "$operator_home/Documents"

state_dir=/var/lib/craft-anything-desktop
install -d -o root -g "$operator" -m 0750 "$state_dir"
if [[ ! -s "$state_dir/vnc.password" ]]; then
  password="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(8)))')"
  # VNC's legacy challenge-response protocol accepts at most eight characters.
  # The viewer is reachable only over the private HTTPS edge and requires this
  # separately generated password before it can control the desktop.
  x11vnc -storepasswd "$password" "$state_dir/vnc.pass" >/dev/null
  printf '%s\n' "$password" > "$state_dir/vnc.password"
  chmod 0640 "$state_dir/vnc.password" "$state_dir/vnc.pass"
  chown root:"$operator" "$state_dir/vnc.password" "$state_dir/vnc.pass"
fi

install -d -m 0755 /etc/systemd/system
cat > /etc/systemd/system/craft-anything-display.service <<EOF
[Unit]
Description=Virtual display for Craft Anything desktop
After=network.target

[Service]
User=$operator
Group=$operator
ExecStart=/usr/bin/Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp -noreset
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$operator_home/.local/share/craft-anything

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/craft-anything-desktop.service <<EOF
[Unit]
Description=Craft Anything desktop application
JoinsNamespaceOf=craft-anything-display.service
Requires=craft-anything-display.service
After=craft-anything-display.service network-online.target
Wants=network-online.target

[Service]
User=$operator
Group=$operator
WorkingDirectory=$operator_home
Environment=HOME=$operator_home
Environment=DISPLAY=:99
Environment=XDG_CACHE_HOME=$operator_home/.local/share/craft-anything/cache
Environment=APPIMAGE_EXTRACT_AND_RUN=1
ExecStart=/opt/craft-anything/craft-anything.AppImage
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$operator_home/.local/share/craft-anything $operator_home/.config $operator_home/Documents /tmp

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/craft-anything-vnc.service <<EOF
[Unit]
Description=Password-protected VNC server for Craft Anything
JoinsNamespaceOf=craft-anything-display.service
Requires=craft-anything-display.service
After=craft-anything-display.service

[Service]
User=$operator
Group=$operator
ExecStart=/usr/bin/x11vnc -display :99 -rfbauth $state_dir/vnc.pass -localhost -forever -shared -rfbport 5900 -noxdamage -repeat
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/craft-anything-websockify.service <<EOF
[Unit]
Description=Private noVNC browser bridge for Craft Anything
After=craft-anything-vnc.service
Requires=craft-anything-vnc.service

[Service]
ExecStart=/usr/bin/websockify --web=/usr/share/novnc 10.77.30.2:6080 127.0.0.1:5900
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now craft-anything-display.service craft-anything-desktop.service craft-anything-vnc.service craft-anything-websockify.service
echo "Installed Craft Anything desktop $version. VNC password is stored in $state_dir/vnc.password (root/operator readable)."
