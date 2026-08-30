# Access-gated evidence checklist

Public WIS2/AWC collection does not wait for these paths. Only CAPTCHA, email
verification, agreements, credentials, and a declared upstream remain manual.
Never commit credentials, tokens, kits, HAR files, or agreement captures.

## FAA SWIFT/SCDS

1. Register through the official SWIFT flow and complete email/CAPTCHA.
2. Export the current service catalog before selecting a service.
3. Search WMSCR, CSS-Wx, Common Support Services Weather, Integrated Terminal
   Weather, METAR, ASOS and AWOS.
4. Save subscribed service name/version/schema/subscription and SAA hash.
5. Put the current Jumpstart Kit in `manual/swift/jumpstart_kits/` locally.

The consumer must be built from that kit's current Java/JMS example. Do not
guess queue, JNDI, schema or service names from old articles.

## MADIS LDM and IDD

Use a declared Linux host with FQDN, public IPv4, chrony and any approved LDM
port. Request real-time 1-minute ASOS/OMO/HFMETAR plus METAR from MADIS. If
testing IDD, request NTEXT through Unidata and use only the upstream/ALLOW they
authorize. Save application and response evidence under `manual/madis/` or
`manual/idd/`; do not search for an unauthorized upstream.

## Target product

Only a user-owned authorized session may be captured. Save first-visible time,
transport type and raw report evidence locally; never copy credentials or
bypass access controls.

## Commercial relay

Use `COMMERCIAL_RELAY_RFI.md`. A source enters the benchmark only when it
documents timestamp semantics and grants a seven-day trial that permits
first-seen measurement.

