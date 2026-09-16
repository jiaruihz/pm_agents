# Known limitations

1. The successful raw provider response remains in the authorized temporary
   pilot root; the repository seal stores its hash, byte length and both
   durable receipt copies, not the 84,979-byte provider payload.
2. Only the public Gamma `/events` GET is operationally proven. No CLOB books
   request was made.
3. Alpha demand outbox, owner deployment, paired live book coverage, weather
   isolation and operational end-to-end replay remain pending.
4. This is a single bounded canary, not approval for unattended daily scans.

