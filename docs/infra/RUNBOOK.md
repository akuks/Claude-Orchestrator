# Remote Ops Runbook

How the two Remote Ops agents work, and the diagnostic playbook they follow.

## Agents

- **Remote Diagnostics** — read-only. SSHes in, observes, and reports
  *symptoms → evidence → root cause → proposed fix*. Never changes state.
- **Remote Remediation** — applies an **approved** fix plan. Every run is
  approval-gated: it lands in the Approvals inbox first, so you review the exact
  plan before anything touches the server.

## Access control

SSH is **opt-in per agent**. An agent can only connect out if its
**"Allow remote login (SSH)"** flag is on; otherwise `ssh`/`scp`/`sftp` are added
to the run's `--disallowedTools` and blocked (deny wins even under
`bypassPermissions`). The two Remote Ops agents have it enabled; everything else
is locked down by default.

PEM keys live **anywhere on this host** you choose (not necessarily `~/.ssh/`) —
`chmod 600`, and put only the *path* in `servers.yaml`. The key never enters the
app DB.

## Connecting

Targets are resolved from `servers.yaml`. The agent connects non-interactively:

```
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
    -i <pem> <user>@<host> '<command>'
```

`StrictHostKeyChecking=accept-new` trusts a host's key on first contact (TOFU) and
pins it thereafter. `BatchMode=yes` fails fast instead of hanging on a prompt.

## Diagnostic playbook (read-only commands)

| Symptom | First checks |
|---|---|
| HTTP 502 / 503 | `sudo systemctl status nginx`; `sudo nginx -t`; `sudo tail -n 100 /var/log/nginx/error.log`; status of the upstream app (`systemctl status <app>` / `journalctl -u <app> -n 100`); `ss -ltnp` to confirm the app is listening |
| High load / slow | `uptime`; `top -bn1 \| head -20`; `vmstat 1 3`; `iostat -x 1 3` |
| Disk full | `df -h`; `du -xh / \| sort -rh \| head -20`; `journalctl --disk-usage` |
| Out of memory | `free -m`; `dmesg -T \| grep -i oom`; per-process `ps aux --sort=-%mem \| head` |
| Service down | `systemctl status <svc>`; `journalctl -u <svc> -n 200 --no-pager`; recent `journalctl -p err -n 100` |
| Cert expiry | `echo \| openssl s_client -servername <host> -connect <host>:443 2>/dev/null \| openssl x509 -noout -dates` |
| Connectivity | `ss -ltnp`; `curl -sS -o /dev/null -w '%{http_code}' localhost:<port>`; firewall/security-group review |

**Read-only means read-only.** No `restart`/`stop`/`start`, no edits, no `rm`,
`kill`, `chmod`, `chown`, `truncate`, package installs, or writes. A needed change
is *proposed*, not applied.

## Hardening (recommended)

Prompt discipline keeps the Diagnostics agent read-only, but the strong guarantee
is server-side: give it a **dedicated SSH user with no (or tightly-scoped) sudo**,
so read-only is enforced by the box, not by trust. Reserve a sudo-capable key for
Remediation runs only.
