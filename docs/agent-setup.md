# AI agent installation and verification

Use an isolated checkout. Preserve existing CST sessions, workers, queues and credentials. RF Sim Orchestrator is a host/worker queue, not an MCP server or arbitrary remote shell.

## 1. Inspect

Read README.md, pyproject.toml and examples/host.toml and worker.toml. Check Python 3.11+, Git, free disk and running CST/worker processes. Windows is required for the CST worker. Actual solves need a valid local CST installation and license. Do not migrate a live installation as a test.

## 2. Install and test without CST

In a new directory, PowerShell:

```powershell
git clone https://github.com/ismailakdag/rf-sim-orchestrator.git
cd rf-sim-orchestrator
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[monitor]"
.\.venv\Scripts\python.exe -m rfsim --help
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The monitor extra includes numpy, psutil and matplotlib. A headless host only needs `pip install -e .`; a CST worker uses `.[cst]`. The regression suite includes isolated mock host/worker transport tests. It does not start a CST solver. Record the actual test count and exit code. Installation alone is not a connection test.

For the interactive README mock demo, use separate data directories and an unused loopback port. Both terminals need the same fresh token through their environment; setting it in one terminal does not set it in another. Never put tokens in tracked files, command arguments or agent output. Do not reuse production credentials for a demo.

## 3. Configure the host and worker

Copy example configurations outside tracked files. Choose host data_dir, port, size limits and allowed_runners. Start on loopback with mock-v1. Remote workers require an accessible HTTPS endpoint; do not disable certificate verification. Supply RF_SIM_TOKEN securely to both processes. Use a unique worker_id and local data_dir on each PC.

```powershell
.\.venv\Scripts\python.exe -m rfsim probe --config PATH-TO-WORKER.toml
.\.venv\Scripts\python.exe -m rfsim workers --url https://YOUR-HOST
.\.venv\Scripts\python.exe -m rfsim monitor-gui --url https://YOUR-HOST
```

The probe reports local capabilities, not a completed solve. Missing endpoint or credentials means remote verification remains untested; finish local checks without inventing those values. The Vercel site is static documentation. The Python host needs an always-running reachable computer or server.

## 4. Enable CST deliberately

Read docs/school-pc-pilot-tr.md and docs/remote-cst-roadmap-tr.md. Configure a locally allowlisted runner with pinned source, Python path and parameter schema. After a successful mock round trip, use one small separately identified test model when CST is idle and a solve is authorized. Verify model build, solver completion, complex S export, package hash and host receipt separately.

Scripts named school-* are deployment-specific, not generic installers. In particular, install-school-supervisor.ps1 defaults to a historical RecoverJob. Do not run it unchanged on an unrelated installation. Read docs/school-auto-update.md. Updates activate tested releases between jobs; they do not guarantee recovery from every license/dialog failure. Arbitrary uploaded CST projects are still roadmap work.

## 5. Report evidence

Report commit, Python version, extras, test command/count/exit code, mock transfer, host/worker connectivity and CST checks separately. Mark failures and untested parts. Never expose credentials or private endpoints. Package integrity does not establish scientific validity, mesh convergence or clinical accuracy.

## Copyable agent prompt

> Read https://github.com/ismailakdag/rf-sim-orchestrator/blob/main/docs/agent-setup.md and install RF Sim Orchestrator in an isolated directory. Preserve existing CST sessions, workers, queues and credentials. Verify CLI and regression/mock tests first. Configure a real connection only with securely supplied endpoint and credentials. Do not automatically migrate the school deployment. Report the exact commit, passing checks, failures and untested parts. A mock result is not proof of CST connectivity.
