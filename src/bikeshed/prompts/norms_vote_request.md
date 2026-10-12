# Post-run: norms_vote

After run $run_id, members made these proposals to the community. The critiques they received are listed under each.

$proposals

## Current community norms

$norms

Vote on each proposal. A proposal is adopted if it has the support of all but at most $slack of the members, and at least one critique from someone other than its author.

Reply with JSON only: {"votes": [{"proposal_id": <proposal id>, "stance": "support" or "object", "reason": "..."}]}, with one vote for each proposal.
