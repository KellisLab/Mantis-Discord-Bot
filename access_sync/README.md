# Access synchronization

`AccessSyncEngine` turns durable member-change events into independent provider
jobs. Providers must reload the member by UUID inside `reconcile()`; event and
job rows never contain a canonical profile snapshot.

Only pending jobs are deduplicated. A change received while a provider job is
running creates a second pending job, and workers serialize those jobs per
member/provider so the final database state wins.

## GitHub

GitHub synchronization manages only `mantis-cartographers`,
`mantis-developers`, and `mantis-engineers`. It never removes organization
membership or changes unrelated teams. `GITHUB_TOKEN` must belong to a
KellisLab owner and have organization Members write permission so it can create
invitations.

Use `/member sync-access-all` without `apply` for the required dry run, then run
it with `apply:true`. The bulk operation checks database members in the forward
direction and removes unmatched or unauthorized accounts discovered by reading
the three managed teams in the reverse direction.
