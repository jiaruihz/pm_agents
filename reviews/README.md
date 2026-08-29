# Review workspace

`reviews/` is a local staging area for assembling external-review and evidence
packets. New content is ignored by Git. Durable machine evidence belongs in the
configured content-addressed research artifact root; durable conclusions belong
in family living docs and registries.

The files already tracked below this directory are frozen legacy evidence debt,
not a precedent for new packets. Do not delete or rewrite them during structure
cleanup. Migrate a packet only after its consumers and hashes are inventoried,
an archive manifest exists, and deletion/movement has explicit authorization.

Use `scripts/ops/check_project_structure.py --deep` when an explicit physical
file/byte inventory is needed; the normal fast check only verifies Git-visible
and tracked debt boundaries. Keep only this README as new repository metadata
under `reviews/`.
