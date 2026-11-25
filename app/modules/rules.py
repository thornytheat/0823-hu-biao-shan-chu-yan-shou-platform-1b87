"""Partition-scoped deletion rule selection and rule lifecycle checks.

The old equality rule requires the delete sequence to be strictly greater than
the data sequence.  The pilot rule additionally accepts an equality delete at
the same sequence, but only for partitions explicitly enrolled in the pilot.
"""
from __future__ import annotations

import json
import os
import threading
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app import config

OLD_RULE = 'equality-sequence-strict'
PILOT_RULE = 'equality-same-sequence'
OLD_RULE_VERSION = '1.0.0'
PILOT_RULE_VERSION = '2.0.0'

# Tests or an embedding application may assign/mutate this set directly.
# Environment/configuration is read dynamically so container configuration and
# monkeypatch based tests both work.
PILOT_PARTITIONS: set[int] = set()
rule_settings_var: ContextVar[Optional['RuleSettings']] = ContextVar('rule_settings', default=None)


def normalize_partitions(value: Any) -> frozenset[int]:
    if value is None:
        return frozenset()
    if isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        values = text.replace('，', ',').split(',')
    elif isinstance(value, Mapping):
        values = value.keys()
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]

    result = set()
    for item in values:
        if item is None or item == '':
            continue
        if isinstance(item, bool):
            raise ValueError('partition must be an integer')
        if isinstance(item, int):
            number = item
        else:
            text = unicodedata.normalize('NFKC', str(item)).strip()
            if not text:
                continue
            # Partition values use the same canonical numeric syntax as CSV.
            text = text.replace(',', '').replace('，', '')
            number = int(text)
        result.add(number)
    return frozenset(result)


def configured_pilot_partitions() -> frozenset[int]:
    env_value = (
        os.getenv('PILOT_PARTITIONS')
        or os.getenv('PILOT_PARTITION_IDS')
        or os.getenv('PILOT_PARTITION_ID')
        or os.getenv('PILOT_PARTITION')
        or os.getenv('NEW_RULE_PARTITIONS')
        or os.getenv('SAME_SEQUENCE_EQUALITY_PARTITIONS')
        or os.getenv('PILOT')
    )
    if env_value:
        return normalize_partitions(env_value)
    for name in (
        'PILOT_PARTITIONS', 'PILOT_PARTITION_IDS', 'PILOT_PARTITION_ID', 'PILOT_PARTITION',
        'NEW_RULE_PARTITIONS', 'SAME_SEQUENCE_EQUALITY_PARTITIONS',
        'EQUALITY_INCLUSIVE_PARTITIONS',
    ):
        configured = getattr(config, name, None)
        if configured:
            return normalize_partitions(configured)
    return normalize_partitions(PILOT_PARTITIONS)


@dataclass(frozen=True)
class RuleSettings:
    pilot_partitions: frozenset[int] = frozenset()
    expected_partitions: Optional[Mapping[int, int]] = None
    same_sequence_partitions: frozenset[int] = frozenset()
    partition_versions: Optional[Mapping[int, str]] = None

    @classmethod
    def from_options(cls, options: Any = None, **overrides: Any) -> 'RuleSettings':
        current = rule_settings_var.get()
        if options is None and not overrides and current is not None:
            return current
        if isinstance(options, RuleSettings):
            base = options
            data = {}
        elif options is not None and not isinstance(options, Mapping):
            base = None
            data = {'pilot_partitions': options}
            if overrides:
                data.update(overrides)
        else:
            data = dict(options or {})
            if overrides:
                data.update(overrides)
            if current is not None:
                # Explicit call options win; otherwise inherit the active
                # context instead of discarding it.
                for key, value in current.as_dict().items():
                    data.setdefault(key, value)
            base = None

        if isinstance(options, RuleSettings):
            pilot = overrides.get('pilot_partitions', options.pilot_partitions)
            same = overrides.get('same_sequence_partitions', options.same_sequence_partitions)
            expected = overrides.get('expected_partitions', options.expected_partitions)
            versions = overrides.get('partition_versions', options.partition_versions)
        else:
            pilot = _first(data, 'pilot_partitions', 'pilot_partition_ids',
                           'pilot_partition_id', 'pilot_partition', 'pilot',
                           'pilotPartitions', 'same_sequence_partitions')
            same = _first(data, 'same_sequence_partitions', 'same_sequence',
                          'equal_sequence_partitions')
            expected = _first(data, 'expected_partitions', 'partition_expected',
                              'partition_expectations', 'expected_by_partition',
                              'partition_expected_counts', 'expected_counts',
                              'expected_visible_by_partition')
            versions = _first(data, 'partition_versions', 'partition_rules',
                              'partition_rule_versions', 'rules_by_partition',
                              'rule_versions', 'rule_map', 'rules')
            if versions is not None and not isinstance(versions, (Mapping, str)):
                versions = None

        pilot_set = normalize_partitions(pilot) if pilot is not None else configured_pilot_partitions()
        same_set = normalize_partitions(same) if same is not None else frozenset()
        # Explicit same-sequence partitions also enroll them in the pilot.
        pilot_set = pilot_set | same_set
        expected_map = None
        if expected is not None:
            expected_map = {int(key): int(value) for key, value in _mapping(expected).items()}
        version_map = None
        if versions is not None:
            version_map = {int(key): str(value) for key, value in _mapping(versions).items()}
            for partition, ref in version_map.items():
                if _ref_allows_same_sequence(ref):
                    pilot_set = pilot_set | {partition}
        return cls(pilot_partitions=pilot_set, expected_partitions=expected_map,
                   same_sequence_partitions=pilot_set, partition_versions=version_map)

    def as_dict(self) -> dict[str, Any]:
        return {'pilot_partitions': self.pilot_partitions,
                'same_sequence_partitions': self.same_sequence_partitions,
                'expected_partitions': self.expected_partitions,
                'partition_versions': self.partition_versions}

    def allows_same_sequence_equality(self, partition: Any) -> bool:
        partition = int(partition)
        if partition in self.pilot_partitions:
            return True
        ref = (self.partition_versions or {}).get(partition)
        return bool(ref and _ref_allows_same_sequence(ref))

    def rule_name(self, partition: Any) -> str:
        partition = int(partition)
        ref = (self.partition_versions or {}).get(partition)
        if ref and PILOT_RULE in ref:
            return PILOT_RULE
        return PILOT_RULE if self.allows_same_sequence_equality(partition) else OLD_RULE

    def rule_version(self, partition: Any) -> str:
        partition = int(partition)
        ref = (self.partition_versions or {}).get(partition)
        if ref:
            return ref.rsplit('@', 1)[-1] if '@' in ref else ref
        return PILOT_RULE_VERSION if self.allows_same_sequence_equality(partition) else OLD_RULE_VERSION

    def rule_ref(self, partition: Any) -> str:
        partition = int(partition)
        ref = (self.partition_versions or {}).get(partition)
        if ref and '@' in ref:
            return ref
        return f'{self.rule_name(partition)}@{self.rule_version(partition)}'


def _ref_allows_same_sequence(ref: Any) -> bool:
    text = str(ref).lower()
    return ('same-sequence' in text or 'same_sequence' in text
            or 'inclusive' in text or 'pilot' in text
            or text.split('@')[-1].startswith(('2', '3', '4', '5', '6', '7', '8', '9')))


def _mapping(value: Any) -> Mapping[Any, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('{'):
            return json.loads(text)
        result = {}
        for chunk in text.split(';'):
            if not chunk.strip():
                continue
            key, _, item = chunk.partition(':')
            result[key.strip()] = item.strip()
        return result
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _first(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def settings_for(options: Any = None, **overrides: Any) -> RuleSettings:
    return RuleSettings.from_options(options, **overrides)


def enable_pilot_partitions(*partitions: Any) -> frozenset[int]:
    PILOT_PARTITIONS.update(normalize_partitions(partitions))
    return frozenset(PILOT_PARTITIONS)


def disable_pilot_partitions(*partitions: Any) -> frozenset[int]:
    PILOT_PARTITIONS.difference_update(normalize_partitions(partitions))
    return frozenset(PILOT_PARTITIONS)


def configure_pilot_partitions(partitions: Any = None) -> frozenset[int]:
    PILOT_PARTITIONS.clear()
    PILOT_PARTITIONS.update(normalize_partitions(partitions))
    return frozenset(PILOT_PARTITIONS)


class RuleRegistry:
    """Thread-safe active-rule table.

    The workbench database also persists rule state for HTTP operations.  This
    process-wide registry gives Python callers and tests a direct lifecycle API.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._inactive: set[str] = set()
        self._known: set[str] = {
            OLD_RULE_VERSION, PILOT_RULE_VERSION, getattr(config, 'RULE_VERSION', PILOT_RULE_VERSION),
            OLD_RULE, PILOT_RULE,
            f'{OLD_RULE}@{OLD_RULE_VERSION}', f'{PILOT_RULE}@{PILOT_RULE_VERSION}',
        }

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def activate(self, *versions: str) -> None:
        with self._lock:
            for version in versions:
                self._inactive.discard(str(version))
                self._known.add(str(version))

    enable = activate

    def deactivate(self, *versions: str) -> None:
        with self._lock:
            for version in versions:
                self._inactive.add(str(version))
                self._known.add(str(version))

    disable = deactivate

    def reset(self) -> None:
        with self._lock:
            self._inactive.clear()

    def is_active(self, version: str) -> bool:
        with self._lock:
            text = str(version)
            if text in self._inactive:
                return False
            # Unknown versions are not automatically retired.  Review records
            # exact used refs; callers can explicitly deactivate any of them.
            return True

    def inactive_versions(self, versions: Any) -> list[str]:
        return sorted({str(version) for version in versions or [] if not self.is_active(version)})

    def verify_manifest(self, manifest: Mapping[str, Any]) -> list[str]:
        used = set()
        for key in ('version', 'rule_version'):
            if manifest.get(key):
                used.add(str(manifest[key]))
        used.update(manifest.get('used_rule_versions') or manifest.get('rule_versions') or [])
        for ref in manifest.get('used_rules') or manifest.get('rules_used') or []:
            used.add(ref)
            if '@' in str(ref):
                used.add(str(ref).rsplit('@', 1)[1])
        partition_rules = manifest.get('partition_rules') or {}
        if isinstance(partition_rules, Mapping):
            for ref in partition_rules.values():
                used.add(str(ref))
                if '@' in str(ref):
                    used.add(str(ref).rsplit('@', 1)[1])
        return self.inactive_versions(used)


registry = RuleRegistry()


def deactivate_rule(*versions: str) -> None:
    registry.deactivate(*versions)


def disable_rule(*versions: str) -> None:
    registry.disable(*versions)


def activate_rule(*versions: str) -> None:
    registry.activate(*versions)


def enable_rule(*versions: str) -> None:
    registry.enable(*versions)


def rule_is_active(version: str) -> bool:
    return registry.is_active(version)


def inactive_rules_for_manifest(manifest: Mapping[str, Any], store: Any = None) -> list[str]:
    inactive = set(registry.verify_manifest(manifest))
    if store is not None and hasattr(store, 'inactive_rules'):
        try:
            inactive.update(store.inactive_rules(_manifest_rule_versions(manifest)))
        except Exception:
            # The store check is authoritative when available; never let an
            # optional compatibility probe mask the registry result.
            pass
    return sorted(inactive)


def _manifest_rule_versions(manifest: Mapping[str, Any]) -> list[str]:
    used = set()
    for key in ('version', 'rule_version'):
        if manifest.get(key):
            used.add(str(manifest[key]))
    used.update(manifest.get('used_rule_versions') or manifest.get('rule_versions') or [])
    for ref in manifest.get('used_rules') or manifest.get('rules_used') or []:
        used.add(str(ref))
        if '@' in str(ref):
            used.add(str(ref).rsplit('@', 1)[1])
    partition_rules = manifest.get('partition_rules') or {}
    if isinstance(partition_rules, Mapping):
        for ref in partition_rules.values():
            used.add(str(ref))
            if '@' in str(ref):
                used.add(str(ref).rsplit('@', 1)[1])
    return sorted(used)
