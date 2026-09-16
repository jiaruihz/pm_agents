# Known limitations

1. The current execution environment exposes internet egress through proxy
   variables. The approved capability boundary forbids inheriting or using
   those proxies; direct TLS timed out.
2. The new Alpha inbox is default-off and exists only in the source commit. It
   has not been added to the production checkout, start command or config.
3. The weather controller had a pre-existing CRITICAL baseline, so clean
   before/during/after isolation evidence was not obtainable.
4. No Gamma payload, canonical market identity, paired book receipt or OP-06
   online replay was produced in this attempt.
5. Daily read-only operation and production capture expansion remain
   unauthorized.
