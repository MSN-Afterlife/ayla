# Ayla Life Legacy Snapshot Identity Specification

## Version 1

The canonical algorithms are:

```text
content_hash_algorithm: AYLA_LEGACY_CONTENT_HASH_V1
schema_fingerprint_algorithm: AYLA_LEGACY_SCHEMA_FINGERPRINT_V1
```

The implementation is shared by the local producer-facing library and the
Legacy Bridge in `ayla_life/legacy_bridge/canonical.py`.

## Content identity

The content identity includes exactly these columns, in this order:

```text
user_id, balance, daily_streak, last_daily_at, updated_at
```

Each SQLite row is converted to an object with exactly those keys. Rows are
sorted by numeric `user_id`, ascending. Valid values remain JSON integers or
JSON `null`; unsupported diagnostic values receive an explicit type envelope.
The resulting top-level value is a JSON array.

Serialization is UTF-8 JSON with `ensure_ascii=False`, separators `(',', ':')`,
no incidental whitespace, no platform newline, and no final newline. SHA-256
is computed over those UTF-8 bytes. Python `repr`, dictionary stringification,
locale, and SQLite return order are not part of the algorithm.

For the supplied real snapshot, the canonical payload has 1010 bytes and the
canonical content hash is:

```text
7023d48e4902f8a17b00737d2c1db4b959757b8c60971835a45e8314eda1ddba
```

## Schema identity

The fingerprint covers the structural contract of `economy_profiles` as
returned by `PRAGMA table_info`, but not raw `sqlite_master.sql`, SQLite
version, file paths, or unrelated tables. Columns are sorted by numeric `cid`
and represented as:

```json
{"table":"economy_profiles","columns":[
  {"name":"...","type":"NORMALIZED","notnull":false,
   "default":null,"pk":0}
]}
```

Declared types are trimmed, upper-cased, and internal whitespace collapsed.
Defaults are trimmed, upper-cased, and internal whitespace collapsed; NULL
defaults remain JSON null. `notnull` is a JSON boolean and `pk` is an integer.
Serialization uses the same UTF-8 compact JSON rules and SHA-256.

For the supplied real snapshot, the canonical schema payload has 419 bytes and
the canonical fingerprint is:

```text
130171392a04e81e25af41b5893c61ad3e018df8ff90e70ac6eb32e775af161b
```

Indexes are not included because the bridge requires read compatibility, not
write performance. A future version must be introduced if compatibility
semantics change.

## Provenance and old manifests

The original producer values remain recorded, unchanged, as provenance:

```text
content_hash: 7dbea157ef33b5cf8dd8c4b1b58158c6f71ffa632003ac262899d61fdeba3c1a
schema_fingerprint: 9abdd3fdd2bdb17efaa84db2b337154505c3883fc45b55b72efa578a2cd72992
```

The exact producer script was not included in the local artifact bundle. The
concrete M1 discrepancy was that M1 used type-wrapped records and a sorted
JSON-key schema containing `cid` but not `default`, while the producer
algorithm was undocumented and its values cannot be reproduced by that M1
serialization. The old values are therefore not silently relabeled as V1.

## Golden vector

For the semantic record

```json
[{"user_id":42,"balance":0,"daily_streak":3,"last_daily_at":null,"updated_at":1700000000}]
```

the V1 payload is exactly the compact UTF-8 bytes of that JSON and its SHA-256
must be calculated by `legacy_content_hash`. The test suite records the exact
digest and also proves that row reordering, balance changes, streak changes,
timestamps, user IDs, NULLs, and relevant schema changes behave as expected.
