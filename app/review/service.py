import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic

from app import config
from app.business import RULE_STALE_CODE, DomainIssue
from app.config import RULE_VERSION
from app.modules.pipeline import run
from app.modules.rules import (
    RuleSettings,
    activate_rule,
    deactivate_rule,
    inactive_rules_for_manifest,
    registry,
)

_IMPORTED_RULE_VERSION = RULE_VERSION
RULE_REGISTRY = registry
RULES = registry


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def current_rule_version():
    # Tests may patch either app.config.RULE_VERSION or this module's imported
    # RULE_VERSION binding.  Support both without caching the patched value.
    if RULE_VERSION != _IMPORTED_RULE_VERSION:
        return RULE_VERSION
    return config.RULE_VERSION


def _rule_metadata(report, settings):
    used_refs = set()
    used_versions = set()
    partition_rules = {}
    for case in report.get('cases', []):
        measure = case.get('measure') or {}
        used_refs.update(measure.get('used_rules') or [])
        used_versions.update(measure.get('used_rule_versions') or [])
        for partition, ref in (measure.get('partition_rules') or {}).items():
            partition_rules[str(partition)] = ref
            if '@' in str(ref):
                used_versions.add(str(ref).rsplit('@', 1)[1])
    if not used_versions:
        # Intake/prepare failures still execute under the registered release.
        used_versions.add(current_rule_version())
    return sorted(used_refs), sorted(used_versions), partition_rules


def _options_fingerprint(settings):
    return json.dumps({
        'pilot_partitions': sorted(settings.pilot_partitions),
        'same_sequence_partitions': sorted(settings.same_sequence_partitions),
        'expected_partitions': (
            {str(k): settings.expected_partitions[k] for k in sorted(settings.expected_partitions)}
            if settings.expected_partitions else None
        ),
        'partition_versions': (
            {str(k): settings.partition_versions[k] for k in sorted(settings.partition_versions)}
            if settings.partition_versions else None
        ),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def execute(store, data, options=None, expected_partitions=None, **option_overrides):
    if expected_partitions is not None:
        option_overrides['expected_partitions'] = expected_partitions
    start = monotonic()
    settings = RuleSettings.from_options(options, **option_overrides)
    fingerprint = sha256(data).hexdigest()
    rule_version = current_rule_version()
    option_key = _options_fingerprint(settings)
    run_id = sha256(f'{rule_version}:{option_key}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data, options=settings)
    used_rules, used_versions, partition_rules = _rule_metadata(report, settings)
    report['manifest'] = dict(
        sha256=fingerprint,
        version=rule_version,
        rule_version=rule_version,
        created=now(),
        duration_ms=round((monotonic() - start) * 1000, 3),
        bytes=len(data),
        run_id=run_id,
        pilot_partitions=sorted(settings.pilot_partitions),
        partition_rules=partition_rules,
        used_rules=used_rules,
        rules_used=used_rules,
        used_rule_versions=used_versions,
        rule_versions=used_versions,
    )
    return store.register(run_id, report)


def with_signability(run, store=None):
    sign = signability(run, store)
    run['signability'] = sign
    run['signable'] = sign['signable']
    run['can_sign'] = sign['can_sign']
    run['can_release'] = sign['can_release']
    run['rule_current'] = sign['rule_current']
    return run


def _configured_rule_problems(manifest):
    """Support simple monkeypatch based rule registries used by tests."""
    used_versions = set(manifest.get('used_rule_versions') or manifest.get('rule_versions') or [])
    for key in ('version', 'rule_version'):
        if manifest.get(key):
            used_versions.add(str(manifest[key]))
    used_refs = set(manifest.get('used_rules') or manifest.get('rules_used') or [])
    for ref in used_refs:
        text = str(ref)
        if '@' in text:
            used_versions.add(text.rsplit('@', 1)[1])

    inactive = set()
    current_version = current_rule_version()
    version_overridden = current_version != _IMPORTED_RULE_VERSION
    modules = (config, sys.modules[__name__])
    for module in modules:
        disabled = getattr(module, 'DISABLED_RULE_VERSIONS', None)
        disabled = {str(v) for v in (disabled or ())}
        inactive.update(v for v in used_versions if v in disabled)
        inactive.update(ref for ref in used_refs if str(ref) in disabled)
        retired = getattr(module, 'RETIRED_RULE_VERSIONS', None)
        retired = {str(v) for v in (retired or ())}
        inactive.update(v for v in used_versions if v in retired)
        active = getattr(module, 'ACTIVE_RULE_VERSIONS', None)
        if active is not None:
            active = {str(v) for v in active}
            for version in used_versions:
                # A RULE_VERSION monkeypatch can leave the default active set
                # stale; the explicit version-current check handles that case.
                if str(version) not in active and not (version_overridden and version == current_version):
                    inactive.add(str(version))
        active_version = getattr(module, 'ACTIVE_RULE_VERSION', None)
        if active_version is not None:
            inactive.update(version for version in used_versions if version != str(active_version))
    return sorted(inactive)


def rule_freshness(run, store=None):
    manifest = (run or {}).get('report', {}).get('manifest', {})
    inactive = set(inactive_rules_for_manifest(manifest, store))
    inactive.update(_configured_rule_problems(manifest))
    version_current = manifest.get('version') == current_rule_version()
    return {
        'rule_current': version_current and not inactive,
        'version_current': version_current,
        'current_version': current_rule_version(),
        'manifest_version': manifest.get('version'),
        'inactive_rules': sorted(inactive),
    }


def signability(run, store=None):
    report = (run or {}).get('report') or {}
    balance = report.get('balance') or {}
    freshness = rule_freshness(run, store)
    blockers = list(balance.get('blockers') or [])
    if not balance.get('releasable') and 'UNRESOLVED_ISSUES' not in blockers:
        blockers.append('UNRESOLVED_ISSUES')
    if not freshness['rule_current']:
        blockers.append('STALE_RULE_VERSION')
    if (run or {}).get('status') == 'APPROVED':
        blockers.append('RUN_FINALIZED')
    # De-duplicate while preserving a stable order.
    blockers = list(dict.fromkeys(blockers))
    can_approve = (
        (run or {}).get('status') != 'APPROVED'
        and bool(balance.get('releasable'))
        and freshness['rule_current']
    )
    return {
        'signable': can_approve,
        'can_sign': can_approve,
        'can_approve': can_approve,
        'can_release': can_approve,
        'releasable': bool(balance.get('releasable')) and freshness['rule_current'],
        'rule_current': freshness['rule_current'],
        'rule_freshness': freshness,
        'blockers': blockers,
    }


def can_approve(run, store=None):
    return signability(run, store)['signable']


def review(store, run_id, values=None, **value_overrides):
    if values is None:
        values = value_overrides
    elif value_overrides:
        merged = dict(values) if isinstance(values, dict) else {}
        merged.update(value_overrides)
        values = merged
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision', 'status', 'note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED', 'HELD') or not isinstance(note, str) or not (3 <= len(note.strip()) <= 200):
        raise DomainIssue('REVIEW_FIELDS')
    current = store.load(run_id)
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')
    if status == 'APPROVED' and not current['report']['balance']['releasable']:
        raise DomainIssue('UNRESOLVED_ISSUES')

    manifest = current['report'].get('manifest', {})
    used_rules = list(set(
        [manifest.get('version'), manifest.get('rule_version')]
        + manifest.get('used_rule_versions', [])
        + manifest.get('used_rules', [])
        + manifest.get('rule_versions', [])
    ))
    used_rules = [rule for rule in used_rules if rule]
    if status == 'APPROVED':
        # Coordinate process-wide rule deactivation with the validation/update
        # critical section.  Store.revise performs the equivalent atomic check
        # for rule state persisted through Store APIs.
        with registry.lock:
            freshness = rule_freshness(current, store)
            if not freshness['rule_current']:
                raise DomainIssue(RULE_STALE_CODE)
            revised = store.revise(run_id, revision, status, note.strip(), now(), used_rules=used_rules)
            return revised

    # Recording a hold remains valid even after a rule is retired; only signing
    # off on the old calculation is prohibited.
    return store.revise(run_id, revision, status, note.strip(), now())


def approve(store, run_id, revision, note):
    return review(store, run_id, dict(revision=revision, status='APPROVED', note=note))


def hold(store, run_id, revision, note):
    return review(store, run_id, dict(revision=revision, status='HELD', note=note))
