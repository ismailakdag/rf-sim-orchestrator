# School worker automatic updates

One-time migration, with CST closed:

```powershell
git -C C:\RFSimSource pull --ff-only
& C:\RFSimSource\scripts\install-school-supervisor.ps1
```

The installer replaces the login launcher, retaining the DPAPI token and worker
configuration. It stops only an old Python worker using this exact configuration,
and refuses migration while a CST GUI or HF solver exists. A named mutex prevents
duplicate background supervisors. The existing school GUI is not the launcher for
this managed mode; use the host monitor to observe it.

Between jobs, at most every five minutes, Git fetches `main` from the explicitly
allowed ismailakdag/rf-sim-orchestrator origin. A separate immutable archive is
tested with the regression suite before activation. The original checkout, pinned
adapter, model source, hashes, configuration and running job are not rewritten.
Each worker invocation handles one job. Both the supervisor Python code and worker
code are selected from the validated release on the next cycle. No dependency
installation occurs automatically; incompatible releases fail tests and leave the
previous version active. Interrupted candidate releases are retained for review.

Network/update errors are retained in `supervisor-status.json`. Ambiguous jobs
remain blocked and are reported online instead of repeatedly starting solvers.
The installer names the specific failed-start job from 15 September for one-time
recovery. Only `solver_started=false`, `failure_stage=solver_start`, correct host
ownership, and no CST process permit submitting an immutable `-retry1` job then
resolving the old attempt. Original files remain. Other failures are not retried.

`C:\RFSimWorker\supervisor.log` and `supervisor-status.json` contain local status.
Creating `C:\RFSimWorker\STOP-SUPERVISOR` stops new jobs after the current job
returns; it never terminates a running solver. Removing it takes effect when the
login launcher is started again. Windows login starts the supervisor, not boot
before login. Deployment to the school machine requires the one-time migration;
local tests do not establish that remote deployment has happened.
