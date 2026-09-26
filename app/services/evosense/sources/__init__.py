"""Real public-record sources for EvoSense (DFW first).

Every adapter here reads a PUBLIC, FREE source that the publisher offers for
download or query without an account: county tax rolls, appraisal-district
exports, city open-data portals and the U.S. Census geocoder. Nothing here
logs in, solves a CAPTCHA, spoofs a browser or ignores a rate limit. A source
that cannot be automated responsibly is registered as MANUAL ONLY and fed by
the existing manual / CSV paths.

Adapters never write to the database. They return plain records in the
ingest() shape plus the raw evidence they came from; the hunt decides what
to do with them. Tests never touch the network: every download goes through
`base.open_zip()` / `base.get_json()`, which honour local-file overrides
(EVOSENSE_SRC_<KEY>_PATH) and can be monkeypatched.
"""
