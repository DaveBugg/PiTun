import type { JobRead, JobStatus, JobSummary } from '@/types'

/**
 * A deploy job whose script exited non-zero still finalizes as
 * `succeeded`: the runner returns `{status: 'failed', ...}` as its RESULT
 * rather than raising, and `JobManager` only looks at whether the coroutine
 * threw. Rendering `job.status` verbatim marks those rows green and calls
 * them "SUCCEEDED" even though no Node was created and the script visibly
 * errored — first seen in the v1.3.0-beta.1 smoke test.
 *
 * For display purposes, treat `result.status` of `failed` or
 * `deployed_no_uri` as visually failed. The detail banner still shows the
 * original text, so "script exited 0 with no URI" stays distinguishable
 * from "script exited non-zero".
 *
 * Lives in lib/ because the deploy and uninstall modals need the exact
 * same projection — showing a green "Install succeeded" for a failed
 * install was the modal-only version of this bug.
 */
export function effectiveJobStatus(job: JobSummary | JobRead): JobStatus {
  if (job.status === 'succeeded' && job.result && typeof job.result === 'object') {
    const r = job.result as { status?: string }
    if (r.status === 'failed' || r.status === 'deployed_no_uri') {
      return 'failed'
    }
  }
  return job.status
}
