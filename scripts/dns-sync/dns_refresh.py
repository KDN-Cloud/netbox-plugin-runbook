"""Refresh known records on a NetBox instance using its Django shell."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address, ip_network
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdata
import dns.rdatatype
import dns.resolver
from dns.rdtypes.ANY.TXT import TXT
import requests
from core.choices import ObjectChangeActionChoices
from django.core.exceptions import ValidationError
from django.db import transaction
from netbox_dns.models import NameServer, Record, Zone


def field_matches(current, wanted):
    if current == wanted:
        return True
    if type(current).__module__.startswith("netaddr.") and isinstance(wanted, str):
        return str(current) == wanted
    return False


def rdata(origin, kind, text):
    return dns.rdata.from_text("IN", kind, text, origin=origin, relativize=False)


def existing_rows(zone, origin):
    existing = defaultdict(list)
    if zone is None:
        return existing
    for row in zone.records.filter(managed=False).order_by("pk"):
        try:
            key = rdata(origin, row.type, row.value).to_digestable(origin)
        except Exception:
            key = None
        owner = dns.name.from_text(row.name, origin).relativize(origin).to_text().lower()
        existing[(owner, row.type)].append((key, row))
    return existing


def authoritative_query(qname, kind, nameservers):
    query = dns.message.make_query(qname, kind)
    query.flags &= ~dns.flags.RD
    result = []
    for server in nameservers:
        try:
            response, _ = dns.query.udp_with_fallback(query, server, timeout=2.5)
        except (OSError, dns.exception.DNSException):
            continue
        if not response.flags & dns.flags.AA or response.rcode() not in {
            dns.rcode.NOERROR, dns.rcode.NXDOMAIN,
        }:
            continue
        target_type = dns.rdatatype.from_text(kind)
        rrset = next((rr for rr in response.answer if rr.name == qname and rr.rdtype == target_type), None)
        result.append((rrset.ttl, tuple(rrset)) if rrset else (None, ()))
    if not result:
        return None
    first = result[0]
    if any((ttl, set(values)) != (first[0], set(first[1])) for ttl, values in result[1:]):
        return None
    return first[0], first[1], len(result)


STAMP = "Cloudflare DNS snapshot"


PRIVATE_NETWORKS = tuple(ip_network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


def annotated(comments, note, stamp=STAMP):
    lines = [line for line in (comments or "").splitlines() if not line.startswith(f"{stamp} — ")]
    original = "\n".join(lines).rstrip()
    return "\n\n".join(filter(None, (original, f"{stamp} — {note}" if note else "")))


class ScheduledDNSImport:
    audit_actor = "Scheduled DNS sync"
    audit_message = "Reconciled verified DNS from authoritative servers or local resolver"

    def __init__(self):
        self.request_id = uuid4()
        self.changes = Counter()

    def ensure(self, model_class, identity, **values):
        obj = model_class.objects.filter(**identity).first()
        return self.save(obj or model_class(**identity), **values)

    def save(self, obj, **values):
        creating = obj.pk is None
        changed = {key: val for key, val in values.items()
                   if not field_matches(getattr(obj, key, None), val)}
        if not creating and not changed:
            return obj
        if not creating:
            obj.snapshot()
        for key, val in changed.items():
            setattr(obj, key, val)
        try:
            obj.full_clean()
            obj.save()
        except Exception as error:
            zone = getattr(getattr(obj, "zone", None), "name", "")
            kind = getattr(obj, "type", obj._meta.model_name)
            fields = ", ".join(getattr(error, "error_dict", {}).keys())
            raise ValueError(f"NetBox rejected {zone} {getattr(obj, 'name', '')} {kind} ({fields})") from None
        self.audit(obj, creating)
        return obj

    def audit(self, obj, creating):
        action = ObjectChangeActionChoices.ACTION_CREATE if creating else ObjectChangeActionChoices.ACTION_UPDATE
        change = obj.to_objectchange(action)
        change.request_id = self.request_id
        change.user_name = self.audit_actor
        change.message = self.audit_message
        change.save()
        self.changes[f"{obj._meta.model_name}:{action}"] += 1


REFRESH_STAMP = "Scheduled DNS sync"


STATE_FILE = Path("/config/dns-sync/observations.json")
TOKEN_FILE = STATE_FILE.with_name("cloudflare.token")
NAMECOM_CREDENTIALS = STATE_FILE.with_name("namecom.credentials")
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
CLOUDFLARE_STAMP = "Cloudflare API"
NAMECOM_API = "https://api.name.com/core/v1"
NAMECOM_DOMAIN = "rantrs.net"
NAMECOM_STAMP = "Name.com API"


MIN_ABSENCE_SECONDS = 2 * 60 * 60


MAX_ABSENCE_SECONDS = 12 * 60 * 60


HOST_TYPES = ("A", "AAAA", "CNAME")


ADDRESS_TYPES = {"A", "AAAA"}


PROXY_NOTES = ("cloudflare proxy enabled", "cloudflare flattens", "without exposing the cname target")


PRIVATE_NOTES = ("private dns from the netbox host", "private dns resolver")


class DNSAPIError(Exception):
    pass


def read_private_file(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise DNSAPIError("credential file could not be opened securely") from None
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise DNSAPIError("credential file must be a regular file with mode 0600")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise DNSAPIError("credential file exceeds the expected size")
    return data


def cloudflare_token():
    content = read_private_file(TOKEN_FILE, 1025)
    if content is None:
        return None
    token = content.strip()
    if not re.fullmatch(rb"[!-~]{1,1024}", token):
        raise DNSAPIError("token file must contain one nonempty API token")
    return token.decode("ascii")


def namecom_credentials():
    content = read_private_file(NAMECOM_CREDENTIALS, 1282)
    if content is None:
        return None
    lines = content.splitlines()
    if (len(lines) != 2 or not re.fullmatch(rb"[!-~]{1,255}", lines[0])
            or b":" in lines[0] or not re.fullmatch(rb"[!-~]{1,1024}", lines[1])):
        raise DNSAPIError("credential file must contain a username and token on separate lines")
    return lines[0].decode("ascii"), lines[1].decode("ascii")


def cloudflare_pages(session, path, per_page, **filters):
    records = []
    seen = set()
    for page in range(1, 201):
        try:
            response = session.get(f"{CLOUDFLARE_API}{path}",
                                   params={**filters, "page": page, "per_page": per_page},
                                   timeout=8, allow_redirects=False)
            if response.status_code != 200:
                raise DNSAPIError(f"HTTP {response.status_code}")
            body = response.json()
        except (requests.RequestException, ValueError):
            raise DNSAPIError("request or response could not be read") from None
        if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("result"), list):
            raise DNSAPIError("API returned an unsuccessful or malformed response")
        result = body["result"]
        info = body.get("result_info") or {}
        if not isinstance(info, dict) or info.get("page", page) != page:
            raise DNSAPIError("API returned an unexpected page")
        for row in result:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                raise DNSAPIError("API returned duplicate or invalid inventory entries")
            seen.add(row["id"])
            records.append(row)
        last = info.get("total_pages")
        if last is not None and (not isinstance(last, int) or last < 0 or (page > last and (page > 1 or result))):
            raise DNSAPIError("API pagination metadata is invalid")
        if last is not None and page >= last or last is None and len(result) < per_page:
            if info.get("total_count", len(records)) != len(records):
                raise DNSAPIError("API inventory count disagrees with the completed list")
            return records
    raise DNSAPIError("API pagination did not complete")


def namecom_records(session):
    records = []
    seen = set()
    total = None
    for page in range(1, 201):
        try:
            response = session.get(f"{NAMECOM_API}/domains/{NAMECOM_DOMAIN}/records",
                                   params={"page": page, "perPage": 100},
                                   timeout=8, allow_redirects=False)
            if response.status_code != 200:
                raise DNSAPIError(f"HTTP {response.status_code}")
            body = response.json()
        except (requests.RequestException, ValueError):
            raise DNSAPIError("request or response could not be read") from None
        if not isinstance(body, dict) or not isinstance(body.get("records"), list):
            raise DNSAPIError("API returned a malformed record list")
        count = body.get("totalCount")
        if type(count) is not int or count < 0 or total is not None and count != total:
            raise DNSAPIError("API record count changed or is invalid")
        total = count
        for row in body["records"]:
            if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or row["id"] in seen:
                raise DNSAPIError("API returned duplicate or invalid record identifiers")
            seen.add(row["id"])
            records.append(row)
        next_page = body.get("nextPage", 0)
        if next_page in (0, None):
            if len(records) != total:
                raise DNSAPIError("API record count disagrees with the completed list")
            return records
        if type(next_page) is not int or next_page != page + 1 or len(records) >= total or not body["records"]:
            raise DNSAPIError("API returned invalid pagination")
    raise DNSAPIError("API pagination did not complete")


def api_rdata(origin, kind, record):
    content = record.get("content") or ""
    data = record.get("data") or {}

    def target(value):
        return origin.to_text() if value == "@" else value.rstrip(".") + "."

    if kind in {"CNAME", "NS", "PTR", "DNAME"}:
        content = target(content)
    elif kind == "MX":
        content = f"{record['priority']} {target(content)}"
    elif kind == "SRV" and data:
        content = f"{data['priority']} {data['weight']} {data['port']} {target(data['target'])}"
    elif kind == "CAA" and data:
        content = f"{data['flags']} {data['tag']} {json.dumps(data['value'])}"
    elif kind == "TXT" and not content.startswith('"'):
        raw = content.encode("utf-8")
        parts = [raw[i:i + 255] for i in range(0, len(raw), 255)] or [b""]
        return TXT(dns.rdataclass.IN, dns.rdatatype.TXT, parts)
    return rdata(origin, kind, content)


def parse_api_records(name, records):
    origin = dns.name.from_text(name)
    inventory = defaultdict(dict)
    blocked = set()
    unsupported = Counter()
    for row in records:
        try:
            fqdn = dns.name.from_text(row["name"]).canonicalize()
            owner = fqdn.relativize(origin).to_text()
        except (KeyError, AttributeError, TypeError, ValueError, dns.exception.DNSException):
            raise DNSAPIError("API returned an invalid record owner") from None
        if not fqdn.is_subdomain(origin):
            raise DNSAPIError("API returned a record outside the requested zone")
        kind = row.get("type")
        if not isinstance(kind, str) or not re.fullmatch(r"[A-Z0-9]{1,20}", kind):
            raise DNSAPIError("API returned an invalid record type")
        if owner == "@" and kind in {"NS", "SOA"}:
            continue
        try:
            ttl = row["ttl"]
            if not isinstance(ttl, int) or not 1 <= ttl <= 2147483647:
                raise ValueError("invalid TTL")
            value = api_rdata(origin, kind, row)
            key = (kind, value.to_digestable(origin))
        except (KeyError, AttributeError, TypeError, ValueError, dns.exception.DNSException):
            blocked.add(owner)
            unsupported[kind] += 1
            continue
        attributes = (value, ttl, row.get("proxied") is True)
        former = inventory[owner].setdefault(key, attributes)
        if former != attributes:
            blocked.add(owner)
            unsupported[kind] += 1
    return inventory, blocked, unsupported


def namecom_inventory(records):
    origin = dns.name.from_text(NAMECOM_DOMAIN)
    normalized = []
    for row in records:
        try:
            if dns.name.from_text(row["domainName"]) != origin:
                raise DNSAPIError("API returned a record for an unexpected domain")
            host = row.get("host") or "@"
            owner = dns.name.from_text(host, origin)
            if row.get("fqdn") and dns.name.from_text(row["fqdn"]) != owner:
                raise DNSAPIError("API record hostname and FQDN disagree")
            kind = row["type"]
            answer = row["answer"]
            if not isinstance(answer, str) or not answer:
                raise ValueError("invalid DNS answer")
            data = {}
            if kind == "SRV":
                weight, port, target = answer.split()
                data = {"priority": row["priority"], "weight": weight, "port": port, "target": target}
            normalized.append({"name": owner.to_text(), "type": kind, "content": answer,
                               "ttl": row["ttl"], "priority": row.get("priority"), "data": data})
        except (KeyError, AttributeError, TypeError, ValueError, dns.exception.DNSException):
            raise DNSAPIError("API returned an invalid Name.com record") from None
    return parse_api_records(NAMECOM_DOMAIN, normalized)


def collect_namecom(authorities):
    summary = {"status": "disabled", "zones": 0, "errors": {}}
    try:
        auth = namecom_credentials()
        if auth is None:
            return {}, summary
        authority = authorities.get(NAMECOM_DOMAIN)
        if authority is None or authority[0] != ".name.com":
            summary["status"] = "zone authority unavailable"
            return {}, summary
        with requests.Session() as session:
            session.trust_env = False
            session.auth = auth
            inventory = namecom_inventory(namecom_records(session))
    except DNSAPIError as error:
        summary.update(status="error", errors={NAMECOM_DOMAIN: str(error)})
        return {}, summary
    summary.update(status="ready", zones=1)
    return {NAMECOM_DOMAIN: inventory}, summary


def collect_cloudflare(authorities):
    snapshots = {}
    summary = {"status": "disabled", "zones": 0, "errors": {}}
    try:
        token = cloudflare_token()
    except DNSAPIError as error:
        summary.update(status="error", errors={"token": str(error)})
        return snapshots, summary
    if token is None:
        return snapshots, summary

    with requests.Session() as session:
        session.trust_env = False
        session.headers["Authorization"] = f"Bearer {token}"
        try:
            catalog = cloudflare_pages(session, "/zones", 50, status="active")
        except DNSAPIError as error:
            summary.update(status="error", errors={"zones": str(error)})
            return snapshots, summary
        by_name = defaultdict(list)
        for item in catalog:
            if isinstance(item.get("name"), str):
                by_name[item["name"].lower()].append(item)
        for name, (provider, servers, *_) in authorities.items():
            if provider != ".ns.cloudflare.com":
                continue
            matched = by_name[name]
            if not matched:
                continue
            try:
                if len(matched) != 1:
                    raise DNSAPIError("zone inventory is ambiguous")
                zone = matched[0]
                ns = zone.get("name_servers")
                if not isinstance(ns, list) or any(not isinstance(server, str) for server in ns) or {
                    server.rstrip(".").lower() for server in ns
                } != set(servers):
                    raise DNSAPIError("zone nameservers differ from DNS delegation")
                if not re.fullmatch(r"[a-f0-9]{32}", zone["id"]):
                    raise DNSAPIError("zone identifier is invalid")
                snapshots[name] = parse_api_records(
                    name, cloudflare_pages(session, f"/zones/{zone['id']}/dns_records", 100),
                )
            except DNSAPIError as error:
                summary["errors"][name] = str(error)
    summary["zones"] = len(snapshots)
    summary["status"] = "partial" if summary["errors"] else "ready" if snapshots else "no matching zones"
    return snapshots, summary


def cloudflare_networks():
    result = []
    for family in ("v4", "v6"):
        response = requests.get(f"https://www.cloudflare.com/ips-{family}", timeout=8, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Cloudflare IP ranges are unavailable")
        result.extend(ip_network(line) for line in response.text.splitlines() if line.strip())
    if not {4, 6} <= {item.version for item in result}:
        raise ValueError("Cloudflare IP ranges are incomplete")
    return tuple(result)


def private_row(row):
    return (any(note in (row.comments or "").lower() for note in PRIVATE_NOTES)
            or row.type in ADDRESS_TYPES and any(ip_address(row.value) in net for net in PRIVATE_NETWORKS))


def api_managed(row, stamp=None):
    stamps = (stamp,) if stamp else (CLOUDFLARE_STAMP, NAMECOM_STAMP)
    prefixes = tuple(f"{value} — " for value in stamps)
    return any(line.startswith(prefixes) for line in (row.comments or "").splitlines())


def confirmed_absence(row, key, missing, next_missing, now, source):
    fingerprint = sha256(source.encode("ascii") + b"\0" + key).hexdigest()
    previous = missing.get(str(row.pk))
    age = now - previous["first"] if previous and previous["key"] == fingerprint else None
    if age is not None and MIN_ABSENCE_SECONDS <= age <= MAX_ABSENCE_SECONDS:
        return True
    first = previous["first"] if age is not None and 0 <= age < MIN_ABSENCE_SECONDS else now
    next_missing[str(row.pk)] = {"key": fingerprint, "first": first}
    return False


def private_answers(qname):
    results = {}
    resolver = dns.resolver.get_default_resolver()
    for kind in ADDRESS_TYPES:
        try:
            answer = resolver.resolve(qname, kind, lifetime=3, raise_on_no_answer=False)
        except dns.resolver.NXDOMAIN:
            continue
        except (OSError, dns.exception.DNSException):
            return {}
        if not answer.rrset:
            continue
        private = [record for record in answer if any(ip_address(record.address) in net for net in PRIVATE_NETWORKS)]
        if not private:
            continue
        if answer.canonical_name == qname:
            results[kind] = (300, private)
        else:
            cname = next((rr for rr in answer.response.answer
                          if rr.name == qname and rr.rdtype == dns.rdatatype.CNAME), None)
            if cname:
                return {"CNAME": (300, tuple(cname))}
    return results


def read_owner(qname, kinds, ips, configured, private):
    public = {kind: authoritative_query(qname, kind, ips) for kind in kinds}
    public = {kind: (result[0], result[1]) for kind, result in public.items()
              if result is not None and result[2] == len(ips)}
    has_host_answer = any(public.get(kind, (None, ()))[1] for kind in HOST_TYPES)
    local = private_answers(qname) if private or (configured and not has_host_answer) else {}
    return public, local


def collect_dns(configured):
    zones = list(Zone.objects.filter(view_id=1, status="active").order_by("pk"))
    zone_by_name = {zone.name: zone for zone in zones}
    host_names = defaultdict(set)
    for hostname in configured:
        qname = dns.name.from_text(hostname.lower())
        zone = max((name for name in zone_by_name if qname.is_subdomain(dns.name.from_text(name))),
                   key=len, default=None)
        if zone:
            host_names[zone].add(qname.relativize(dns.name.from_text(zone)).to_text())

    work = {}
    authorities = {}
    unavailable = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        for zone in zones:
            origin = dns.name.from_text(zone.name)
            try:
                ns = sorted({record.target.to_text(True)
                             for record in dns.resolver.resolve(origin, "NS", lifetime=4)})
                previous = tuple(zone.nameservers.values_list("name", flat=True))
                provider = next((suffix for suffix in (".ns.cloudflare.com", ".name.com")
                                 if previous and all(server.endswith(suffix) for server in previous)), None)
                if not provider or len(ns) < 2 or not all(server.endswith(provider) for server in ns):
                    unavailable.append(zone.name)
                    continue
                ips = tuple(dns.resolver.resolve(server, "A", lifetime=4)[0].address for server in ns)
                if len(set(ips)) != len(ns):
                    unavailable.append(zone.name)
                    continue
            except (OSError, dns.exception.DNSException):
                unavailable.append(zone.name)
                continue
            authorities[zone.name] = (provider, ns, ips, pool.submit(authoritative_query, origin, "NS", ips),
                                      pool.submit(authoritative_query, origin, "SOA", ips))
            prior = defaultdict(list)
            for (owner, _), records in existing_rows(zone, origin).items():
                prior[owner].extend(row for _, row in records if row.is_active or row.type == "CNAME")
            for owner in sorted(set(prior) | host_names[zone.name]):
                rows = prior[owner]
                active_types = {row.type for row in rows if row.is_active}
                configured = owner in host_names[zone.name]
                kinds = active_types | (set(HOST_TYPES) if configured or active_types.intersection(HOST_TYPES) else set())
                if not kinds:
                    continue
                question = dns.name.from_text(owner, origin)
                work[(zone.name, owner)] = pool.submit(
                    read_owner, question, kinds, ips, configured,
                    any(private_row(row) for row in rows if row.is_active),
                )
        return ({key: future.result() for key, future in work.items()},
                {zone: (provider, ns, ips, ns_result.result(), soa_result.result())
                 for zone, (provider, ns, ips, ns_result, soa_result) in authorities.items()}, unavailable)


def verified_authority(writer, zone, authority):
    provider, ns, ips, nameservers, soa = authority
    if not nameservers or not soa or nameservers[2] != len(ips) or soa[2] != len(ips):
        return False
    if len(soa[1]) != 1 or set(ns) != {record.target.to_text(True) for record in nameservers[1]}:
        return False
    master = writer.ensure(NameServer, {"name": soa[1][0].mname.to_text(True)})
    data = soa[1][0]
    serial = data.serial
    try:
        zone.check_soa_serial_increment(zone.soa_serial, serial)
    except ValidationError:
        serial = zone.soa_serial
    writer.save(zone, soa_mname=master, soa_rname=data.rname.to_text(True), soa_serial=serial,
                soa_serial_auto=False, soa_ttl=soa[0], soa_refresh=data.refresh, soa_retry=data.retry,
                soa_expire=data.expire, soa_minimum=data.minimum, default_ttl=nameservers[0])
    if set(zone.nameservers.values_list("name", flat=True)) != set(ns):
        servers = [writer.ensure(NameServer, {"name": server}) for server in ns]
        zone.snapshot()
        zone.nameservers.set(servers)
        writer.audit(zone, False)
    return True


def refresh_owner(writer, zone, owner, rows, public, local, provider, edge_networks, missing, next_missing, now):
    stats = Counter()
    origin = dns.name.from_text(zone.name)
    active = [(key, row) for key, row in rows if row.is_active]
    api_types = {row.type for _, row in rows if api_managed(row) and (row.is_active or owner == "@")}
    protect_api_hosts = bool(set(HOST_TYPES) & api_types)
    private = any(private_row(row) for _, row in active)
    documented_local = any(
        any(note in (row.comments or "").lower() for note in PRIVATE_NOTES)
        for _, row in active
    )
    has_public_host = any(public.get(kind, (None, ()))[1] for kind in HOST_TYPES)
    use_local = documented_local or not has_public_host and (private or bool(local))
    protect_host = any(
        row.type in HOST_TYPES and (row.type == "CNAME" and owner == "@" or
                                   any(note in (row.comments or "").lower() for note in PROXY_NOTES))
        for _, row in rows if row.is_active or row.type == "CNAME" and owner == "@"
    )
    if provider == ".ns.cloudflare.com":
        protect_host |= edge_networks is None or any(
            ip_address(value.address) in network
            for kind in ADDRESS_TYPES for value in public.get(kind, (None, ()))[1]
            for network in edge_networks
        )
    observations = {
        kind: (ttl, records, "public authoritative DNS")
        for kind, (ttl, records) in public.items()
        if kind not in api_types and (kind not in HOST_TYPES or not (protect_host or protect_api_hosts or use_local))
    }
    if not protect_host and not protect_api_hosts and use_local:
        observations.update({kind: (ttl, records, "private DNS resolver")
                             for kind, (ttl, records) in local.items()})
    if protect_host:
        stats["proxy_or_flattening_preserved"] += 1

    retire = []
    save = []
    for kind, (ttl, values, source) in observations.items():
        current = [(key, row) for key, row in active if row.type == kind]
        desired = {record.to_digestable(origin): record for record in values}
        for key, row in current:
            if key in desired or row.ipam_ip_address_id or row.ptr_record_id or key is None:
                continue
            if desired:
                retire.append(row)
            elif source == "public authoritative DNS" and not private_row(row):
                if confirmed_absence(row, key, missing, next_missing, now, "public"):
                    retire.append(row)
                else:
                    stats["awaiting_second_absence"] += 1
        for key, data in desired.items():
            if any(old == key for old, _ in current):
                row = next(row for old, row in current if old == key)
                if row.ttl != ttl and source == "public authoritative DNS":
                    save.append((row, ttl, None))
            else:
                row = next((row for old, row in rows if old == key and row.type == kind), None)
                row = row or Record(zone=zone, name=owner, type=kind, value=data.to_text(),
                                    disable_ptr=kind in ADDRESS_TYPES)
                save.append((row, ttl, source))
    future_types = {row.type for _, row in active if row not in retire} | {row.type for row, _, _ in save}
    if "CNAME" in future_types and len(future_types) > 1:
        stats["alias_conflict_review"] += 1
        return stats
    for row in retire:
        writer.save(row, status="inactive",
                    comments=annotated(row.comments, "Replaced or confirmed absent in authoritative DNS.", REFRESH_STAMP))
    for row, ttl, source in sorted(save, key=lambda item: item[0].pk is None):
        note = annotated(row.comments, f"Source: {source}.", REFRESH_STAMP) if source else row.comments
        writer.save(row, status="active", ttl=ttl, comments=note)
    stats["marked_inactive"] += len(retire)
    return stats


def refresh_api_owner(writer, zone, owner, rows, desired, missing, next_missing, now, stamp):
    stats = Counter()
    provider = "Cloudflare" if stamp == CLOUDFLARE_STAMP else "Name.com"
    source = "cloudflare" if stamp == CLOUDFLARE_STAMP else "namecom"
    snapshot_stamp = STAMP if stamp == CLOUDFLARE_STAMP else "Name.com DNS snapshot"
    conflicts = f"{source}_conflicts_preserved"
    active = [(key, row) for key, row in rows if row.is_active]
    desired_types = {kind for kind, _ in desired}
    flattened_apex = owner == "@" and "CNAME" in desired_types
    active_types = desired_types - {"CNAME"} if flattened_apex else desired_types
    preferred_ttls = {}
    for (kind, _), (_, ttl, _) in desired.items():
        value = 300 if ttl == 1 else ttl
        preferred_ttls[kind] = min(preferred_ttls.get(kind, value), value)

    def protected(row):
        comments = (row.comments or "").lower()
        return (row.ipam_ip_address_id or row.ptr_record_id
                or any(note in comments for note in PRIVATE_NOTES)
                or private_row(row) and not api_managed(row, stamp) and snapshot_stamp.lower() not in comments)

    retire = []
    for key, row in active:
        identity = (row.type, key)
        if identity in desired or key is None:
            continue
        replace = row.type in active_types
        crosses_alias = ((row.type != "CNAME" and "CNAME" in active_types)
                         or (row.type == "CNAME" and bool(active_types)))
        if crosses_alias and (api_managed(row, stamp) or snapshot_stamp.lower() in (row.comments or "").lower()
                              or "public authoritative dns" in (row.comments or "").lower()):
            replace = True
        if protected(row):
            if replace:
                stats[conflicts] += 1
                return stats
        elif replace or (api_managed(row, stamp)
                         and confirmed_absence(row, key, missing, next_missing, now, source)):
            retire.append(row)

    future_types = active_types | {row.type for _, row in active if row not in retire}
    if "CNAME" in future_types and len(future_types) > 1:
        stats[conflicts] += 1
        return stats
    actions = []
    for (kind, key), (value, ttl, proxied) in desired.items():
        matches = [row for old, row in rows if old == key and row.type == kind]
        row = next((candidate for candidate in matches if candidate.is_active), None)
        row = row or next(iter(matches), None) or Record(
            zone=zone, name=owner, type=kind, value=value.to_text(), disable_ptr=kind in ADDRESS_TYPES,
        )
        if row.pk and protected(row):
            if not row.is_active or row.ttl != preferred_ttls[kind]:
                stats[conflicts] += 1
                return stats
            continue
        status = "inactive" if kind == "CNAME" and flattened_apex else "active"
        note = "Cloudflare proxy enabled." if proxied else f"DNS record from {provider}."
        if kind == "CNAME" and flattened_apex:
            note += (" Cloudflare flattens this apex alias." if stamp == CLOUDFLARE_STAMP
                     else " Apex alias documented as inactive.")
        if ttl == 1:
            note += " Automatic TTL."
        elif ttl != preferred_ttls[kind]:
            note += f" {provider} configured TTL {ttl}; NetBox uses the common record-set TTL."
        comments = annotated(annotated(row.comments, "", snapshot_stamp), note, stamp)
        actions.append((row, preferred_ttls[kind], status, comments))
        if proxied:
            stats["cloudflare_proxied_records"] += 1

    for row in retire:
        writer.save(row, status="inactive",
                    comments=annotated(row.comments, f"Replaced or confirmed absent in {provider}.", stamp))
    for row, ttl, status, comments in sorted(actions, key=lambda item: item[0].pk is None):
        writer.save(row, status=status, ttl=ttl, comments=comments)
    stats["marked_inactive"] += len(retire)
    return stats


def load_observations():
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open() as stream:
        return json.load(stream)


def save_observations(data):
    temporary = STATE_FILE.with_suffix(".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(data, stream, sort_keys=True)
        temporary.replace(STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_dns(configured, apply):
    now = int(datetime.now(timezone.utc).timestamp())
    results = Counter()
    try:
        edges = cloudflare_networks()
    except (requests.RequestException, ValueError):
        edges = None
        results["cloudflare_ip_ranges_unavailable"] = 1
    observations, authorities, unavailable = collect_dns(configured)
    cloudflare, cloudflare_summary = collect_cloudflare(authorities)
    namecom, namecom_summary = collect_namecom(authorities)
    missing = load_observations()
    next_missing = {}
    writer = ScheduledDNSImport()
    with transaction.atomic():
        zones = {zone.name: zone for zone in Zone.objects.select_for_update().filter(view_id=1, status="active")}
        for name, authority in authorities.items():
            zone = zones[name]
            if not verified_authority(writer, zone, authority):
                unavailable.append(name)
                continue
            results["checked_zones"] += 1
            existing = defaultdict(list)
            for (owner, _), rows in existing_rows(zone, dns.name.from_text(name)).items():
                existing[owner].extend(rows)
            inventory, blocked, unsupported = ({}, set(), {})
            stamp = None
            if name in cloudflare:
                inventory, blocked, unsupported = cloudflare[name]
                stamp = CLOUDFLARE_STAMP
                results["cloudflare_unsupported_records"] += sum(unsupported.values())
            elif name in namecom:
                inventory, blocked, unsupported = namecom[name]
                stamp = NAMECOM_STAMP
                results["namecom_unsupported_records"] += sum(unsupported.values())
            public_names = {owner: result for (zone_name, owner), result in observations.items() if zone_name == name}
            api_names = set(inventory) | blocked
            if stamp:
                api_names.update(owner for owner, rows in existing.items()
                                 if any(api_managed(row, stamp) for _, row in rows if row.is_active))
            for owner in sorted(set(public_names) | api_names):
                results["checked_names"] += 1
                if owner in blocked:
                    continue
                if owner in api_names:
                    results.update(refresh_api_owner(writer, zone, owner, existing[owner],
                                                     inventory.get(owner, {}), missing, next_missing, now, stamp))
                else:
                    public, local = public_names[owner]
                    results.update(refresh_owner(writer, zone, owner, existing[owner], public, local, authority[0],
                                                 edges, missing, next_missing, now))
        if not apply:
            transaction.set_rollback(True)
    if apply:
        save_observations(next_missing)
    print(json.dumps({"mode": "apply" if apply else "preview", "changes": writer.changes,
                      "cloudflare_api": cloudflare_summary, "namecom_api": namecom_summary,
                      "unavailable_zones": sorted(set(unavailable)), "summary": results}, sort_keys=True), flush=True)
