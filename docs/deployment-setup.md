# Deployment setup: GitHub Secrets and one-time server preparation

This guide uses placeholders on purpose. The server address, user name and password are **never** written in this repository;
they live only in GitHub Actions secrets. `<server>` is the target host, `<deploy-user>` is the SSH account used to deploy.

How the pipeline works is described in the [README](../README.md#cicd-github-actions). This page is the checklist to make it run.

## 1. GitHub: environment, runner and secrets

### 1.1 Create the `production` environment
*Repository > Settings > Environments > New environment* named exactly `production`.
- **Required reviewers:** add yourself (and anyone else who may approve a deployment).
- **Deployment branches:** *Selected branches* > `main`.

Do this **before** registering the runner, so no deployment can start without approval.

### 1.2 Register a self-hosted runner
GitHub-hosted runners cannot reach a private address, so deploy jobs run on a runner inside your network.
*Settings > Actions > Runners > New self-hosted runner* (Linux), then follow GitHub's commands. Add the labels **`rag-deploy`** (plus the
default `self-hosted` and `linux`). The runner machine needs network access to `<server>` on port 22, plus `ssh`, `scp` and `sshpass`
(`sudo apt-get install -y openssh-client sshpass`). Install it as a service (`sudo ./svc.sh install && sudo ./svc.sh start`).

Public repository warning: a self-hosted runner executes workflow code. Under *Settings > Actions > General* require approval for
workflows from outside collaborators, or make the repository private.

### 1.3 Add the secrets
*Settings > Environments > production > Environment secrets.* (Environment secrets are only released to jobs that pass the
environment's approval rules.)

| Secret | Required | What to enter | How to obtain it |
|---|---|---|---|
| `DEPLOY_HOST` | yes | Address of the target server | Your server's IP or DNS name |
| `DEPLOY_USER` | yes | The SSH user, `<deploy-user>` | The account prepared in section 2 |
| `DEPLOY_PASSWORD` | yes | That user's SSH password | Set by you or your administrator |
| `DEPLOY_HOST_KEY` | yes | The server's SSH host key line, e.g. `<server> ssh-ed25519 AAAA...` | `ssh-keyscan -t ed25519 <server>`, then **verify the fingerprint** (below) |
| `WEBHOOK_SECRET` | yes | Random string, 16+ characters | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TICKET_API_KEY` | yes | Key the receiver uses to call the incident system | Generate as above; for the bundled mock system use the same value as `MOCK_ITSM_API_KEY` |
| `MOCK_ITSM_API_KEY` | if the mock incident system is deployed | Key the mock incident system expects | Generate as above |
| `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` | optional | Credentials for the Claude CLI on the server | Only if you do not authenticate the CLI interactively on the server |

Values may contain only letters, digits and `. _ ~ : / @ + = -` (they are written to a `KEY=value` file). The workflow rejects anything else
with a clear message.

**Verifying the host key.** `ssh-keyscan` only reports what the network answered, so confirm it against the server itself, using any
trusted channel (console or an administrator):
```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub          # run on the server
ssh-keyscan -t ed25519 <server> | ssh-keygen -lf -          # run on any machine; the fingerprints must match
```
Paste the full `ssh-keyscan` output line into `DEPLOY_HOST_KEY`. If the server's key ever changes legitimately (rebuild), update the secret;
the pipeline refuses to connect to an unknown key, which is what protects the password from a man-in-the-middle.

### 1.4 Optional variables
*Settings > Environments > production > Environment variables* (not secret):

| Variable | Default | Meaning |
|---|---|---|
| `DEPLOY_PATH` | `rag-helpdesk` | Install folder, relative to the deploy user's home |
| `DEPLOY_MOCK_ITSM` | `true` | `false` to skip the bundled mock incident system and use a real one |
| `TICKET_API_URL` | (local mock) | Real incident system URL when `DEPLOY_MOCK_ITSM=false` |
| `STREAMLIT_BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` lets your team reach the app on port 8501. The app has **no login**; do this only behind SSO or a network allow-list |
| `RECEIVER_BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` only if an external system must post webhooks to it |

### 1.5 Protect `main`
*Settings > Branches > Add rule* for `main`: require a pull request and the **Test** status check before merging.

## 2. Target server: one-time preparation

Run these as an administrator (`sudo`) unless stated. Commands are for Debian/Ubuntu; adapt the package manager for other systems.

1. **Create the deploy user** (skip if it exists). It should be an ordinary user with **no sudo rights**:
   ```bash
   sudo adduser <deploy-user>
   ```
2. **Install the prerequisites** (Python 3.10 or newer with `venv`):
   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 python3-venv python3-pip tar curl
   python3 --version        # must be 3.10 or newer
   ```
3. **Let background services outlive the SSH session** (needed for `systemd --user`, and for services to start after a reboot):
   ```bash
   sudo loginctl enable-linger <deploy-user>
   ```
4. **Allow SSH password login for the deploy user.** Check what the server enforces:
   ```bash
   sudo sshd -T -C user=<deploy-user> | grep -iE "passwordauthentication|kbdinteractiveauthentication|allowusers|allowgroups"
   ```
   `passwordauthentication yes` must be shown, and `<deploy-user>` must be permitted by any `AllowUsers`/`AllowGroups`. To enable it for this
   user only, add a block to `/etc/ssh/sshd_config.d/50-deploy.conf` and reload (`sudo systemctl reload ssh`):
   ```
   Match User <deploy-user>
       PasswordAuthentication yes
   ```
   Recommended later: switch to key-based login (add a public key to `~/.ssh/authorized_keys`) and remove password login; the workflow would then
   need small changes.
5. **Install and authenticate the Claude CLI as the deploy user.** Follow Anthropic's Claude Code installation guide, then confirm
   `claude --version` works when logged in as `<deploy-user>`. Either run `claude` once interactively to sign in, or provide
   `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` as secrets. Without this the assistant cannot answer questions.
6. **Allow outbound HTTPS** from the server to `pypi.org` and `files.pythonhosted.org` (dependencies) and to
   `chroma-onnx-models.s3.amazonaws.com` (the ~79 MB embedding model, downloaded once). Behind a proxy, or without internet, use an internal
   PyPI mirror (`pip config`) and copy the model into `~/.cache/chroma/onnx_models/all-MiniLM-L6-v2` for the deploy user.
7. **Open the application port only if the team needs it** (and only if you set `STREAMLIT_BIND_ADDRESS=0.0.0.0`):
   ```bash
   sudo ufw allow from <team-network-cidr> to any port 8501 proto tcp
   ```
   Ports 8000 (webhook receiver) and 9000 (mock incident system) should stay closed to the network unless you have a specific need.
8. **Check readiness as the deploy user:**
   ```bash
   scp deploy/check_server.sh <deploy-user>@<server>:/tmp/ && ssh <deploy-user>@<server> bash /tmp/check_server.sh
   ```
   Fix every `[FAIL]` line; review each `[WARN]`.

## 3. Verify, then deploy

1. **Actions > Server check > Run workflow.** After approval it connects with the secrets, verifies the pinned host key and runs the readiness
   check. It changes nothing on the server. A passing run proves the secrets, runner, network path and password login all work.
2. **Deploy:** merge to `main` (or re-run the latest `CI/CD` run). After **Test** and **Build** pass, approve the deployment when prompted.
3. **Confirm on the server** (as the deploy user):
   ```bash
   systemctl --user status rag-app rag-webhook rag-itsm
   curl -s http://127.0.0.1:8501/_stcore/health ; curl -s http://127.0.0.1:8000/health
   ```

## 4. Operations

- **Rotate a credential:** change the secret in GitHub and (for the SSH password or API keys) on the target, then run the deploy again.
- **Roll back:** the deploy rolls back automatically if the health check fails. To go back manually:
  `ln -sfn ~/rag-helpdesk/releases/<previous-id> ~/rag-helpdesk/current && systemctl --user restart rag-itsm rag-webhook rag-app`.
- **Logs:** `journalctl --user -u rag-app -u rag-webhook -u rag-itsm -e` (or `~/rag-helpdesk/shared/run/*.log` without systemd).
- **Troubleshooting a failed run:**
  | Message | Meaning |
  |---|---|
  | `Secret ... is not configured` | A required secret is missing from the `production` environment |
  | `Host key verification failed` | `DEPLOY_HOST_KEY` is wrong or the server's key changed; re-verify the fingerprint |
  | `Permission denied` | Wrong `DEPLOY_USER` or `DEPLOY_PASSWORD`, or password login is not allowed for that user (step 4) |
  | `Waiting for a runner` | No runner with the `rag-deploy` label is online (step 1.2) |
  | `self-check failed; the running version was not changed` | The new release has a defect; the live version is untouched. See the job log |
  | `ROLLING BACK` | The new release started but failed its health check; the previous one was restored |
