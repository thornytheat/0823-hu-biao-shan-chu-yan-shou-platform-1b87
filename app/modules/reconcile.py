from collections import Counter


def balance(intake, cases):
    accounted = intake['accepted'] + intake['rejected'] + intake['blank'] + intake['repeated']
    status = Counter(case['status'] for case in cases)
    ready = status.pop('READY', 0)
    observed = sum(len(case['records']) for case in cases)
    conservation_balanced = accounted == intake['input'] and observed == intake['accepted']

    partition_results = []
    explicit_partition_failure = False
    for case in cases:
        measure = case.get('measure') or {}
        candidate = (
            case.get('partition_checks')
            or case.get('partition_results')
            or measure.get('partitions')
            or measure.get('partition_checks')
            or measure.get('partition_results')
            or []
        )
        if isinstance(candidate, dict):
            candidate = candidate.values()
        for item in candidate:
            if not isinstance(item, dict) or item.get('expected') is None:
                continue
            entry = {'case': case.get('case'), **item}
            partition_results.append(entry)
        if case.get('partition_balanced') is False or measure.get('partition_balanced') is False:
            explicit_partition_failure = True

    partition_failures = [item for item in partition_results if item.get('status') != 'READY']
    if partition_results:
        partition_balanced = not partition_failures and not explicit_partition_failure
    else:
        partition_balanced = False if explicit_partition_failure else None
    partition_deltas = {
        f"{item.get('case')}:{item.get('partition')}": item.get('delta')
        for item in partition_results if item.get('delta') is not None
    }
    releasable = bool(cases) and ready == len(cases) and not intake['rejected'] and conservation_balanced and partition_balanced is not False

    blockers = []
    if not cases:
        blockers.append('NO_BUSINESS_CASES')
    if intake['rejected']:
        blockers.append('ISOLATED_ROWS')
    if not conservation_balanced:
        blockers.append('INPUT_NOT_BALANCED')
    if ready != len(cases):
        blockers.append('UNRESOLVED_CASES')
    if partition_failures or explicit_partition_failure:
        blockers.append('PARTITION_MISMATCH')

    return dict(input=intake['input'], accepted=intake['accepted'], isolated=intake['rejected'],
                ignored=intake['blank'] + intake['repeated'], accounted=accounted,
                balanced=conservation_balanced,
                ready=ready, pending=len(cases)-ready, issues=dict(sorted(status.items())),
                partition_results=partition_results,
                partition_checks=partition_results,
                partition_deltas=partition_deltas,
                partition_count=len(partition_results),
                partition_mismatches=len(partition_failures),
                partition_mismatch_count=len(partition_failures),
                partition_balanced=partition_balanced,
                all_partitions_match=partition_balanced is not False,
                blockers=blockers,
                releasable=releasable,
                can_release=releasable,
                signable=releasable)
