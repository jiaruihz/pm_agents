from scripts.ops.cloudflare_ddns_update import (
    DnsRecord,
    desired_records,
    normalize_record_name,
    record_needs_update,
)


def test_normalize_record_name_expands_relative_names():
    assert normalize_record_name("weather", "weekendleague.party") == "weather.weekendleague.party"
    assert normalize_record_name("weather.weekendleague.party.", "weekendleague.party") == "weather.weekendleague.party"
    assert normalize_record_name("@", "weekendleague.party") == "weekendleague.party"


def test_desired_records_builds_a_and_aaaa_records():
    records = desired_records(
        zone_name="weekendleague.party",
        record_name="weather",
        ipv4="124.90.20.192",
        ipv6="2408:8240:e1a:8070:6a1d:efff:fe40:eb81",
        update_a=True,
        update_aaaa=True,
        ttl=300,
        proxied=False,
    )

    assert records == [
        DnsRecord("A", "weather.weekendleague.party", "124.90.20.192", 300, False),
        DnsRecord("AAAA", "weather.weekendleague.party", "2408:8240:e1a:8070:6a1d:efff:fe40:eb81", 300, False),
    ]


def test_record_needs_update_checks_content_ttl_and_proxy_state():
    desired = DnsRecord("A", "weather.weekendleague.party", "124.90.20.192", 300, False)

    assert not record_needs_update({"content": "124.90.20.192", "ttl": 300, "proxied": False}, desired)
    assert record_needs_update({"content": "198.18.0.186", "ttl": 300, "proxied": False}, desired)
    assert record_needs_update({"content": "124.90.20.192", "ttl": 1, "proxied": False}, desired)
    assert record_needs_update({"content": "124.90.20.192", "ttl": 300, "proxied": True}, desired)
