from app.business import DomainIssue, by_kind, prepared
from app.modules.rules import RuleSettings


class PreparedPayload(tuple):
    """Four-element historical payload with optional metadata attached.

    Keeping tuple length at four preserves callers that unpack
    ``records, files, deletes, expected = payload``.
    """

    def __new__(cls, records, files, deletes, expected,
                expected_partitions=None, settings=None):
        obj = super().__new__(cls, (records, files, deletes, expected))
        obj.expected_partitions = expected_partitions
        obj.rule_settings = settings
        return obj


def _snapshot_plan(snapshots):
    """Return cutoff sequence and expected counts.

    A partition 0 snapshot keeps the original workbook convention: its position
    is the expected total for the whole business case.  Additional non-zero
    snapshots provide partition-specific expected visible counts.
    """
    if not snapshots:
        raise DomainIssue('EXPECTED_ONE_SNAPSHOT')

    by_partition = {}
    for row in snapshots:
        partition = row['partition']
        current = by_partition.get(partition)
        if current is None or row['sequence'] > current['sequence']:
            by_partition[partition] = row
        elif row['sequence'] == current['sequence'] and row['position'] != current['position']:
            raise DomainIssue('SNAPSHOT_PARTITION_CONFLICT')

    global_snapshot = by_partition.get(0)
    partition_snapshots = {p: row for p, row in by_partition.items() if p != 0}

    if global_snapshot is not None:
        at = global_snapshot['sequence']
        expected = global_snapshot['position']
    elif partition_snapshots:
        # Every partition has its own cutoff.  The maximum is only a fallback
        # for rows whose partition has no authoritative snapshot.
        at = max(row['sequence'] for row in partition_snapshots.values())
        expected = sum(row['position'] for row in partition_snapshots.values())
    else:
        # A single snapshot may itself identify the only observed partition.
        only = next(iter(by_partition.values()))
        at = only['sequence']
        expected = only['position']

    expected_partitions = None
    cutoff_by_partition = None
    if len(snapshots) > 1 or (len(snapshots) == 1 and snapshots[0]['partition'] != 0):
        source = partition_snapshots or by_partition
        expected_partitions = {p: row['position'] for p, row in source.items() if p != 0 or len(snapshots) == 1}
        cutoff_by_partition = {p: row['sequence'] for p, row in source.items()}

    return at, expected, expected_partitions, cutoff_by_partition


def prepare(rows, settings=None, expected_partitions=None):
    settings = settings or RuleSettings.from_options()
    snapshots = by_kind(rows, 'snapshot')
    at, expected, snapshot_expected, cutoff_by_partition = _snapshot_plan(snapshots)

    expected_by_partition = dict(snapshot_expected or {})
    if settings.expected_partitions:
        expected_by_partition.update(settings.expected_partitions)
    if expected_partitions:
        expected_by_partition.update({int(k): int(v) for k, v in expected_partitions.items()})
    expected_by_partition = expected_by_partition or None

    def visible_at(row):
        if cutoff_by_partition is None:
            return row['sequence'] <= at
        cutoff = cutoff_by_partition.get(row['partition'], at)
        return row['sequence'] <= cutoff

    records = {}
    files = {}
    for row in by_kind(rows, 'data'):
        if not visible_at(row):
            continue
        file_id, position = row['file'], row['position']
        descriptor = (row['partition'], row['sequence'])
        if files.setdefault(file_id, descriptor) != descriptor or (file_id, position) in records:
            raise DomainIssue('DATA_POSITION_CONFLICT')
        records[file_id, position] = row
    deletes = [r for r in rows if r['kind'].endswith('_delete') and visible_at(r)]
    details = {'data_files': len(files), 'delete_rows': len(deletes), 'as_of_sequence': at}
    if expected_by_partition:
        details['expected_partitions'] = {str(k): v for k, v in sorted(expected_by_partition.items())}
    payload = PreparedPayload(records, files, deletes, expected,
                              expected_by_partition, settings)
    return prepared(len(records), details, payload)
