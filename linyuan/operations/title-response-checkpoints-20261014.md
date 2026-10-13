# Raw title response checkpoints are not approval reuse

Changing the ASR portion of produce_cn.py invalidates its entire title-proof
file signature, despite title requests and subtitle fixtures being unchanged.
This causes unrelated deployments to repeat lengthy CPU computation.

Keep exact-code approval reuse unchanged. For code-mismatched evidence from a
recent (six-hour) trusted main FC deployment, first apply current validators to
both exact source-bound subtitle reports and confirm the actual CPU model.
Only request-keyed raw responses may be copied (64-hex filenames, bounded size,
no conflicting existing bytes); do NOT restore title reports or approvals.
Return resumed=true, approved=false and current_checks_required=true.

The existing workflow then runs both current subtitle cases and every current
assertion. Current llm() includes model, prompt/messages, temperature, token
limit, response schema and runtime version in its exact SHA256 cache key;
changed requests miss old cache and run actual CPU inference. No manually
selected titles, waived tests or alternate publication path are introduced.

The branch-only title-model check verifies this real path without FC deployment
or posting. Do not restart the current live deployment just to use this patch.
