# Rollback

This phase made no production deployment and no current runtime DB mutation.

1. Keep the scheduler policy disabled; there is no autonomous process to stop.
2. Do not authorize an external provider/transport harness.
3. Revert code commit `b11ba6953508ad4eeeb6b2a500b851b1c65a4924`
   if the offline feature must be removed from a later checkout.
4. Preserve immutable artifacts and additive `alpha_*` rows for audit; do not
   delete or downgrade schema. Older code simply does not invoke the P1 APIs.
5. Continue using the existing manual packet/result handoff path.

Rollback is therefore a code/config selection, not a destructive data action.
