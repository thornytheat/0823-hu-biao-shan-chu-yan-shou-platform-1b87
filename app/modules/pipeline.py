from app.business import DomainIssue
from app.intake.service import inspect
from app.modules.calculate import compute
from app.modules.grouping import case_groups
from app.modules.policy import policy
from app.modules.prepare import prepare
from app.modules.reconcile import balance
from app.modules.rules import RuleSettings


def evaluate(rows, stage, settings):
    case = {'case': rows[0]['case'], 'records': [r['record'] for r in rows], 'status': 'READY'}
    try:
        case['prep'], payload = prepare(rows, settings)
        if stage >= 3:
            case['measure'] = compute(payload, settings)
        if stage >= 4:
            case.update(policy(case['measure']))
    except DomainIssue as error:
        case['status'] = error.code
        case['code'] = error.code
    return case


def run(data, stage=5, options=None, expected_partitions=None, **option_overrides):
    if expected_partitions is not None:
        option_overrides['expected_partitions'] = expected_partitions
    settings = RuleSettings.from_options(options, **option_overrides)
    intake = inspect(data)
    output = {'intake': {k: v for k, v in intake.items() if k != 'rows'}}
    if stage == 1:
        return output
    cases = [evaluate(rows, stage, settings) for _, rows in case_groups(intake['rows'])]
    output['cases'] = cases
    output['rule_settings'] = {
        'pilot_partitions': sorted(settings.pilot_partitions),
        'same_sequence_partitions': sorted(settings.same_sequence_partitions),
        'expected_partitions': (
            {str(k): v for k, v in sorted(settings.expected_partitions.items())}
            if settings.expected_partitions else None
        ),
        'partition_versions': (
            {str(k): v for k, v in sorted(settings.partition_versions.items())}
            if settings.partition_versions else None
        ),
    }
    if stage >= 5:
        output['balance'] = balance(intake, cases)
    return output
