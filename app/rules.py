"""Compatibility import path for rule registry callers."""
from app.modules.rules import *  # noqa: F401,F403
from app.modules.rules import (  # noqa: F401
    PILOT_PARTITIONS,
    PILOT_RULE,
    PILOT_RULE_VERSION,
    OLD_RULE,
    OLD_RULE_VERSION,
    RuleRegistry,
    RuleSettings,
    activate_rule,
    deactivate_rule,
    disable_rule,
    enable_rule,
    inactive_rules_for_manifest,
    normalize_partitions,
    registry,
    rule_is_active,
    settings_for,
)
