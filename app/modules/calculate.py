from collections import Counter
from dataclasses import replace
from collections.abc import Mapping

from app.business import measured
from app.modules.rules import RuleSettings, normalize_partitions

# Compatibility seam for tests and callers that configure the calculator
# directly.  Prefer RuleSettings/config.PILOT_PARTITIONS in application code.
PILOT_PARTITIONS = set()


def compute(data, settings=None, **rule_options):
    records, files, deletes, raw_expected = data[:4]
    expected_partitions = getattr(data, 'expected_partitions', None)
    if expected_partitions is None and len(data) > 4:
        expected_partitions = data[4]
    payload_settings = getattr(data, 'rule_settings', None)
    if payload_settings is None and len(data) > 5:
        payload_settings = data[5]
    if expected_partitions is None and isinstance(raw_expected, Mapping):
        expected_partitions = {int(key): int(value) for key, value in raw_expected.items()}
        expected = sum(expected_partitions.values())
    else:
        expected = raw_expected
    if isinstance(settings, RuleSettings):
        explicit_settings = settings
    elif settings is not None:
        # Convenience for compute(payload, {7}) or compute(payload, pilot_set).
        if isinstance(settings, dict):
            explicit_settings = RuleSettings.from_options(settings, **rule_options)
        else:
            explicit_settings = RuleSettings.from_options(
                {'pilot_partitions': settings}, **rule_options
            )
    elif rule_options:
        explicit_settings = RuleSettings.from_options(rule_options)
    else:
        explicit_settings = None
    settings = explicit_settings or payload_settings or RuleSettings.from_options()
    if expected_partitions is None and settings.expected_partitions:
        expected_partitions = settings.expected_partitions
    direct_pilots = normalize_partitions(PILOT_PARTITIONS)
    if direct_pilots and not direct_pilots.issubset(settings.pilot_partitions):
        settings = replace(
            settings,
            pilot_partitions=settings.pilot_partitions | direct_pilots,
            same_sequence_partitions=settings.same_sequence_partitions | direct_pilots,
        )

    position_rules, equality_rules = {}, {}
    dangling = []
    for row in deletes:
        partition, sequence = row['partition'], row['sequence']
        if row['kind'] == 'equality_delete':
            key = (partition, row['row_key'])
            equality_rules[key] = max(sequence, equality_rules.get(key, -1))
        else:
            key = (row['file'], row['position'])
            if key[0] not in files or files[key[0]][0] != partition or key not in records:
                dangling.append(f'{key[0]}:{key[1]}')
                continue
            position_rules[key] = max(sequence, position_rules.get(key, -1))

    visible = []
    removed = {}
    visible_by_partition = Counter()
    total_by_partition = Counter()
    removed_by_partition = Counter()

    for key, row in sorted(records.items()):
        tag = f'{key[0]}:{key[1]}'
        partition = row['partition']
        total_by_partition[partition] += 1
        equality_sequence = equality_rules.get((partition, row['row_key']), -1)
        equality_removes = (
            equality_sequence > row['sequence']
            or (
                equality_sequence == row['sequence']
                and settings.allows_same_sequence_equality(partition)
            )
        )
        if position_rules.get(key, -1) >= row['sequence']:
            removed[tag] = 'POSITION'
            removed_by_partition[partition] += 1
        elif equality_removes:
            removed[tag] = 'EQUALITY'
            removed_by_partition[partition] += 1
        else:
            visible.append(tag)
            visible_by_partition[partition] += 1

    evidence = {'visible': visible, 'removed': removed, 'dangling': sorted(set(dangling))}
    result = measured(len(visible), expected, '行', evidence, bool(dangling))

    known_partitions = set(total_by_partition) | set(expected_partitions or {})
    partition_results = {}
    for partition in sorted(known_partitions):
        observed = visible_by_partition[partition]
        partition_expected = None if expected_partitions is None else expected_partitions.get(partition)
        delta = None if partition_expected is None else observed - partition_expected
        if partition_expected is None:
            status = 'EXPECTED_PARTITION_MISSING' if expected_partitions is not None else 'NOT_CHECKED'
        else:
            status = 'READY' if delta == 0 else 'MISMATCH'
        partition_results[str(partition)] = {
            'partition': partition,
            'value': observed,
            'observed': observed,
            'visible': observed,
            'count': observed,
            'total': total_by_partition[partition],
            'removed': removed_by_partition[partition],
            'expected': partition_expected,
            'delta': delta,
            'status': status,
            'rule': settings.rule_name(partition),
            'rule_version': settings.rule_version(partition),
            'rule_ref': settings.rule_ref(partition),
            'same_sequence_equality': settings.allows_same_sequence_equality(partition),
        }

    if expected_partitions is None:
        partition_balanced = None
    else:
        partition_balanced = all(item['status'] == 'READY' for item in partition_results.values())

    result['partition_results'] = partition_results
    result['partitions'] = list(partition_results.values())
    result['partition_balanced'] = partition_balanced
    result['partition_mismatch_count'] = sum(
        item['status'] != 'READY' for item in partition_results.values()
    ) if expected_partitions is not None else 0
    result['visible_by_partition'] = {key: item['observed'] for key, item in partition_results.items()}
    result['partition_counts'] = result['visible_by_partition']
    result['expected_by_partition'] = {
        key: item['expected'] for key, item in partition_results.items()
    }
    result['partition_deltas'] = {
        key: item['delta'] for key, item in partition_results.items() if item['delta'] is not None
    }
    result['partition_rules'] = {
        str(partition): settings.rule_ref(partition)
        for partition in sorted(known_partitions)
    }
    result['used_rules'] = sorted({item['rule_ref'] for item in partition_results.values()})
    result['used_rule_versions'] = sorted({item['rule_version'] for item in partition_results.values()})
    return result
