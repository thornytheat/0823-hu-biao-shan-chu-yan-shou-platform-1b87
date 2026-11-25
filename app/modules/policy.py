from app.business import AliasedCode, decision

PARTITION_STATUS = AliasedCode('MISMATCH', (
    'PARTITION_MISMATCH', 'PARTITION_COUNT_MISMATCH',
    'PARTITION_EXPECTED_MISMATCH', 'PARTITION_EXPECTATION_MISMATCH',
))


def with_partition_expectations(result, expected_partitions):
    """Attach/override partition expectations on a calculator result."""
    expected_partitions = {str(key): int(value) for key, value in dict(expected_partitions).items()}
    partition_results = result.setdefault('partition_results', {})
    for key, expected in expected_partitions.items():
        item = partition_results.setdefault(key, {
            'partition': int(key) if key.isdigit() else key,
            'observed': 0,
            'value': 0,
            'visible': 0,
        })
        observed = item.get('observed', item.get('value', 0))
        item['expected'] = expected
        item['delta'] = observed - expected
        item['status'] = 'READY' if observed == expected else 'MISMATCH'
    result['partitions'] = list(partition_results.values())
    result['partition_balanced'] = all(
        item.get('status') == 'READY' for item in partition_results.values()
    ) if partition_results else None
    return result


def policy(result, expected_partitions=None, **expectation_options):
    expected = expected_partitions
    if expected is None:
        expected = expectation_options.get('partition_expectations')
    if expected is None:
        expected = expectation_options.get('expected_by_partition')
    if expected is not None:
        with_partition_expectations(result, expected)

    reason = 'DANGLING_POSITION' if result.get('flag') else None
    outcome = decision(result, reason)
    failures = [
        item for item in result.get('partitions', [])
        if item.get('status') != 'READY' and result.get('partition_balanced') is False
    ]
    if result.get('partition_balanced') is False:
        # Keep the long-standing coarse status for existing clients, while
        # exposing the precise scope for the workbench and newer tests.
        outcome['partition_balanced'] = False
        outcome['partition_failures'] = failures
        outcome['partition_mismatch'] = True
        outcome['partition_mismatches'] = len(failures)
        outcome['code'] = 'PARTITION_MISMATCH'
        outcome['reason_code'] = 'PARTITION_MISMATCH'
        if outcome['status'] == 'READY' or outcome['status'] == 'MISMATCH':
            outcome['status'] = PARTITION_STATUS
    else:
        outcome['partition_balanced'] = result.get('partition_balanced')
    return outcome
