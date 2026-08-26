# Rollback rehearsal

The offline gate uses existing automated rollback evidence:

- Schema transaction rollback and unchanged pre-state:
  `test_storage_p0_02.py::test_empty_and_interrupted_migration_rolls_back`.
- R2 child transaction rollback:
  `test_storage_p0_02r2.py::test_result_transaction_rolls_back_children_when_packet_is_missing`.
- Adapter disable/failure receipts without a parallel collector:
  `test_book_adapter_p0_05a.py::test_missing_mismatched_expired_and_stale_routes_are_typed`.
- Packet importer quarantine without Candidate advancement:
  `test_research_importer_p0_08b.py::test_packet_schema_stage_id_and_hash_mismatch_are_quarantined`.
- Alpha entrypoint capability failure:
  `test_security_final_p0_11.py` and `test_security_os_sandbox_p0_11.py`.

Recovery is additive: retain failed artifacts, disable the affected provider or
offline entrypoint, pin the previous reader/writer version, and rerun the exact
fixture suite. No rollback deletes production or research data.
