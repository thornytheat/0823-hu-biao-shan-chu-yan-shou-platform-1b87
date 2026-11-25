from flask import Blueprint, Response, current_app, jsonify, request

from app import config
from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.modules.rules import activate_rule, deactivate_rule, registry
from app.review.service import execute, now, review, signability

api = Blueprint('api',__name__,url_prefix='/api')
STAGES = {'intake':1,'prepare':2,'calculate':3,'policy':4,'balance':5}


def _options():
    form = request.form
    options = {}
    for target in ('pilot_partitions', 'pilot_partition_ids', 'pilot_partition_id',
                   'pilot_partition', 'pilot', 'same_sequence_partitions',
                   'expected_partitions', 'partition_rules'):
        value = form.get(target)
        if value:
            options[target] = value
    return options or None


def _present(payload, store):
    sign = signability(payload, store)
    payload['signability'] = sign
    payload['signable'] = sign['signable']
    payload['can_sign'] = sign['can_sign']
    payload['can_release'] = sign['signable']
    payload['rule_current'] = sign['rule_current']
    return payload


@api.post('/stages/<stage>')
def preview(stage):
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    return jsonify(run(read_input(),STAGES[stage],options=_options()))


@api.route('/runs',methods=['GET','POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    payload = execute(store,read_input(),options=_options())
    return jsonify(_present(payload, store))


@api.route('/runs/<run_id>', methods=['GET','PATCH'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(_present(store.load(run_id), store))
    payload = review(store,run_id,request.get_json(silent=True))
    return jsonify(_present(payload, store))


@api.get('/runs/<run_id>/signability')
def run_signability(run_id):
    store = current_app.extensions['runs']
    payload = store.load(run_id)
    return jsonify(signability(payload, store))


@api.get('/runs/<run_id>/release')
def run_release(run_id):
    return run_signability(run_id)


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report),mimetype='application/json', headers={
        'Content-Disposition':f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control':'no-store',
    })


@api.route('/rules', methods=['GET', 'POST', 'PATCH', 'PUT'])
def rules_index():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        # The registry is process-wide; persisted disabled states are also exposed
        # for operational troubleshooting.
        with store.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT version,active,updated FROM rule_state ORDER BY version')]
        return jsonify(items=rows, current=config.RULE_VERSION)

    values = request.get_json(silent=True) or request.form
    version = values.get('version') or values.get('rule_version')
    if not version:
        raise DomainIssue('REVIEW_FIELDS')
    if 'active' in values:
        active = str(values.get('active')).lower() not in {'0', 'false', 'no', 'off'}
    else:
        active = not bool(values.get('disabled') or values.get('deactivated'))
    return _set_rule(str(version), active)


def _set_rule(version, active):
    store = current_app.extensions['runs']
    timestamp = now()
    with registry.lock:
        if active:
            store.activate_rule(version, timestamp)
            activate_rule(version)
        else:
            store.deactivate_rule(version, timestamp)
            deactivate_rule(version)
    return jsonify(version=version, active=active)


@api.post('/rules/<path:version>/disable')
def disable_rule_route(version):
    return _set_rule(version, False)


@api.post('/rules/<path:version>/deactivate')
def deactivate_rule_route(version):
    return _set_rule(version, False)


@api.post('/rules/<path:version>/enable')
def enable_rule_route(version):
    return _set_rule(version, True)


@api.post('/rules/<path:version>/activate')
def activate_rule_route(version):
    return _set_rule(version, True)


@api.post('/rules/deactivate')
def deactivate_rule_body():
    values = request.get_json(silent=True) or request.form
    version = values.get('version') or values.get('rule_version')
    if not version:
        raise DomainIssue('REVIEW_FIELDS')
    return _set_rule(str(version), False)


@api.post('/rules/activate')
def activate_rule_body():
    values = request.get_json(silent=True) or request.form
    version = values.get('version') or values.get('rule_version')
    if not version:
        raise DomainIssue('REVIEW_FIELDS')
    return _set_rule(str(version), True)
