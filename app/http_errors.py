from flask import jsonify

LABELS = {
    'FILE_TOO_LARGE':'文件超过 256 KiB', 'FILE_EMPTY':'文件没有内容', 'FILE_BLANK':'文件只有空白',
    'HEADER_ONLY':'文件只有标题行', 'HEADER_MISMATCH':'列名与字段字典不一致', 'ENCODING_INVALID':'请使用 UTF-8 CSV',
    'CSV_MALFORMED':'CSV 引号或行格式异常', 'ROW_LIMIT':'文件超过 2000 行', 'FILE_REQUIRED':'请选择 CSV 文件',
    'RUN_NOT_FOUND':'批次不存在', 'REVIEW_FIELDS':'请填写 3 至 200 字复核意见', 'STALE_REVIEW':'批次已更新，请刷新',
    'RUN_FINALIZED':'批次已签发', 'UNRESOLVED_ISSUES':'存在待处理差异', 'SAMPLE_UNKNOWN':'样例不存在', 'STAGE_UNKNOWN':'处理阶段不存在',
    'STALE_RULE_VERSION':'计算规则已换版或停用，请重新执行批次后再签发',
    'RULE_VERSION_STALE':'计算规则已换版或停用，请重新执行批次后再签发',
    'RULE_DISABLED':'所用规则已停用，请重新计算批次',
    'PARTITION_MISMATCH':'存在分区可见行数与预期不一致',
    'SNAPSHOT_PARTITION_CONFLICT':'同一分区的快照预期冲突',
}

CONFLICT_CODES = {'STALE_REVIEW','RUN_FINALIZED','UNRESOLVED_ISSUES','STALE_RULE_VERSION',
                  'RULE_VERSION_STALE','RULE_DISABLED','PARTITION_MISMATCH'}
NOT_FOUND_CODES = {'RUN_NOT_FOUND','SAMPLE_UNKNOWN','STAGE_UNKNOWN','RULE_NOT_FOUND'}


def response(error):
    code = error.code
    status = 404 if code in NOT_FOUND_CODES else 409 if code in CONFLICT_CODES else 413 if code=='FILE_TOO_LARGE' else 422
    body = {'code': code, 'message': LABELS.get(code,code)}
    aliases = getattr(code, 'aliases', None)
    if aliases:
        body['codes'] = [str(code), *map(str, aliases)]
    return jsonify(body),status
