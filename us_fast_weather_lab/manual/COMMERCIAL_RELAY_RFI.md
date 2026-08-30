# U.S. METAR low-latency relay RFI

Please answer each item for the proposed feed:

1. Is the upstream WMSCR/SWIM, NOAAport, WIS2, IDD, AWC polling, or another
   named source?
2. Is transport JMS, MQTT, AMQP, WebSocket, TCP, or REST polling?
3. Do you deliver unmodified raw METAR/SPECI and an upstream receive timestamp?
4. For every timestamp, does it mean observation, provider ingest, provider
   publication, or API response?
5. What p50/p95 latency SLA is offered, and from which reference point?
6. Is a seven-day trial available, with permission to retain first-seen
   benchmark evidence?
7. Are internal analytics, derived signals, and customer-facing derivatives
   permitted?
8. Is raw redistribution permitted, and what fees/geographic limits apply?
9. Are one-minute OMO/ASOS observations included?
10. How do failover and historical backfill work?

A relay without source lineage, raw timestamp semantics, or a measurable trial
does not enter the paid-source benchmark.
