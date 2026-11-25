import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 256 * 1024
MAX_ROWS = 2000
# 2.0.0 introduces partition-scoped equality semantics.  The old 1.0.0 rule
# remains active for partitions outside PILOT_PARTITIONS.
RULE_VERSION = '2.0.0'
LEGACY_RULE_VERSION = '1.0.0'
PILOT_RULE_VERSION = '2.0.0'
PILOT_PARTITIONS = frozenset(
    value.strip() for value in os.getenv('PILOT_PARTITIONS', '').split(',') if value.strip()
)
ACTIVE_RULE_VERSIONS = {LEGACY_RULE_VERSION, RULE_VERSION, PILOT_RULE_VERSION}
DISABLED_RULE_VERSIONS: set[str] = set()
