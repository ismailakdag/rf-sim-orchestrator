# CST Abort dialog recovery — 14 September 2026

The v4 school control hit its 2,700-second case timeout and reported
`interaction_required:abort` before exiting at 19:24:28 UTC. Increasing the
timeout in 0.2.4 did not implement modal recovery. This remains a failed,
unvalidated simulation, not an accepted scientific result.

Version 0.2.5 adds an opt-in Abort confirmation guard to the fixed runner.
It observes the solver whose command has the exact run model as a complete
argument, finds its CST GUI ancestor and retains that PID and creation time.
Only an English Abort window with all five expected labels is eligible.
The guard explicitly selects Keep results, verifies its checked state, then
sends OK with bounded Windows messages. It records intent and confirmation;
it does not mark the partial result valid. Unknown/localized/license dialogs
remain operator actions. The field test of this handler on CST 2025 is pending.

The normal installer enables `handle_cst_abort_dialog = true`. Existing workers
must be updated and restarted to use it. For the already stopped school v4
worker, run `scripts/recover-school-abort.ps1` after `git pull`. It updates the
package and makes one narrowly scoped recovery attempt; it does not restart
the worker, resolve the host job, or submit a replacement. If no matching live
solver establishes ownership, it sends no confirmation. Review the evidence
and verify that the old solver is gone before resolving the failed job.

Automating Abort confirmation prevents this known modal from blocking an
unattended timeout. It does not make a 45-minute simulation finish sooner.
The school throughput/progress needs diagnosis before another expensive repeat.
The local CST 2026 queue and frozen geometry/source hashes are unchanged.

## User decision: no wall-clock cutoff (0.2.6)

The user supplied recovery evidence showing no HF solver process, only CST GUI
PID 15280, and explicitly allowed long school runs. The replacement v5 catalog
has 42 finite cases with `timeout_seconds: null`; jobs have `deadline_utc: null`.
The locally configured supervisor uses zero to explicitly disable its time cap.
Host leases still expire unless renewed, and API call timeouts, disk checks,
source hashes and result validation remain enforced. No far-future fake deadline
is used. Geometry and solver accuracy settings match v4; only scheduling changes.
The bridge gate controls release of the remaining 40 cases. A bridge failure
means the cross-version comparison needs review, not proof the dental concept
has failed. Install via `enable-school-cst2025-pilot.ps1` after pulling 0.2.6.
