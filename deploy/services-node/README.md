# Services-node deployment

`cloud-init-vendor.yaml` is the shared Debian 13 guest baseline for the gateway,
automation, and toolbox VMs on `ai-services-01`. It installs a small package
set, enables the QEMU guest agent and Docker, applies basic SSH hardening, and
sets bounded Docker logging defaults.

Proxmox supplies the VM name, operator account, public SSH key, network, and
disk configuration separately. Use
[`setup-services-node.sh`](../../scripts/setup-services-node.sh) to validate the
host and render the full per-VM cloud-init configuration.

The file does not configure application services, credentials, backups,
firewalls, DNS, or the Proxmox host itself. Those remain explicit steps in the
[`ai-services-01` plan](../../docs/ai-services-node-plan.md).
