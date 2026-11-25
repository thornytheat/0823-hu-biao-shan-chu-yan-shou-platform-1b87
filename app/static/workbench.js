import {request} from './client.js';
import {snapshot, history} from './view.js';
import {visualize} from './plots.js';

const form = document.querySelector('#source');
const file = document.querySelector('#file');
const profile = document.querySelector('#profile');
const pilotPartitions = document.querySelector('#pilot-partitions');
const status = document.querySelector('#notice');
const detail = document.querySelector('#review');
const evidence = document.querySelector('#evidence');
const stages = [...document.querySelectorAll('[data-stage]')];
let stage = 'balance';
let report, selected, batch, running = false;

function source() {
  const data = new FormData();
  if (file.files.length) data.append('file',file.files[0]);
  else data.append('sample',profile.value);
  if (pilotPartitions.value.trim()) data.append('pilot_partitions',pilotPartitions.value.trim());
  return data;
}

function announce(text, failure = false) {
  status.textContent = text;
  status.dataset.failure = String(failure);
}

const blockerLabels = {
  NO_BUSINESS_CASES:'没有业务分组', ISOLATED_ROWS:'存在隔离坏行', INPUT_NOT_BALANCED:'行数对账不平',
  UNRESOLVED_CASES:'仍有分组未通过', PARTITION_MISMATCH:'分区可见行数与预期不一致',
  UNRESOLVED_ISSUES:'存在待处理差异', STALE_RULE_VERSION:'所用规则已换版或停用，请重新执行批次',
  RUN_FINALIZED:'批次已经签发',
};

function renderSignability(sign) {
  const node = document.querySelector('#signability');
  const release = document.querySelector('#release-state');
  const approved = batch?.status === 'APPROVED';
  const signable = Boolean(sign?.signable);
  node.dataset.ready = String(signable);
  if (approved) {
    node.textContent = '✓ 批次已签发，结论已锁定';
  } else if (signable) {
    node.textContent = '✓ 可以签发：输入、分组、分区数量及规则版本均有效';
  } else {
    const blockers = (sign?.blockers || ['UNRESOLVED_ISSUES']).map(code => blockerLabels[code] || code).join('；');
    node.textContent = `暂不能签发：${blockers}`;
  }
  if (release && batch) {
    release.textContent = approved ? '已签发' : signable ? '✓ 可签发' : '不可签发';
    release.dataset.ready = approved ? 'approved' : String(signable);
  }
  return signable && !approved;
}

function draw() {
  if (!report) return;
  snapshot(report, selected, stage);
  visualize(report, selected);
  for (const button of stages) button.setAttribute('aria-pressed',String(button.dataset.stage===stage));
  document.querySelector('#panel-title').textContent = stages.find(button => button.dataset.stage===stage)?.textContent || '运行对账';
  detail.hidden = !batch;
  if (batch) {
    document.querySelector('#run-status').textContent = `${batch.id.slice(0,10)} · ${batch.status} · r${batch.revision}`;
    const canApprove = renderSignability(batch.signability);
    detail.querySelector('[value="APPROVED"]').disabled = !canApprove;
    detail.querySelector('[value="HELD"]').disabled = batch.status==='APPROVED';
    evidence.href = `/api/runs/${batch.id}/evidence`;
  }
}

function show(payload, committed = false) {
  batch = committed ? payload : null;
  report = committed ? payload.report : payload;
  detail.elements.note.value = batch?.note || '';
  selected = report.cases?.[0]?.case;
  draw();
}

async function refresh() {
  const data = await request('/api/runs');
  history(data.items, batch?.id);
}

async function perform(action) {
  if (running) return;
  running = true;
  document.body.setAttribute('aria-busy','true');
  announce('处理中…');
  try { await action(); }
  catch (error) { announce(error.message,true); }
  finally { running = false; document.body.removeAttribute('aria-busy'); }
}

function preview(next) {
  return perform(async () => {
    const payload = await request(`/api/stages/${next}`,{method:'POST',body:source()});
    stage = next;
    show(payload);
    announce('阶段预览已更新');
    await refresh();
  });
}

stages.forEach(button => button.addEventListener('click',()=>preview(button.dataset.stage)));
form.addEventListener('submit', event => {
  event.preventDefault();
  perform(async () => {
    const result = await request('/api/runs',{method:'POST',body:source()});
    stage = 'balance'; show(result,true);
    announce('批次已保存'); await refresh();
  });
});
const filename = document.querySelector('#file-name');
profile.addEventListener('change',()=>{file.value=''; filename.textContent=''; preview(stage);});
pilotPartitions.addEventListener('change',()=>preview(stage));
file.addEventListener('change',()=>{filename.textContent=file.files[0]?.name || ''; preview(stage);});
document.querySelector('#rows').addEventListener('click',event=>{
  const button = event.target.closest('[data-case]');
  if (button) { selected = button.dataset.case; draw(); document.querySelector(`[data-case="${CSS.escape(selected)}"]`).focus(); }
});
document.querySelector('#history').addEventListener('click',event=>{
  const button = event.target.closest('[data-run]');
  if (button) perform(async()=>{
    const result = await request(`/api/runs/${button.dataset.run}`);
    stage='balance'; show(result,true); announce('已载入历史批次'); await refresh();
  });
});
detail.addEventListener('submit',event=>{
  event.preventDefault();
  perform(async()=>{
    const payload={status:event.submitter.value,revision:batch.revision,note:detail.elements.note.value};
    try {
      const result=await request(`/api/runs/${batch.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      show(result,true); announce(result.status==='APPROVED'?'批次已签发':'批次已暂缓'); await refresh();
    } catch (error) {
      try {
        const result=await request(`/api/runs/${batch.id}`);
        show(result,true);
      } catch (_) { /* keep the original error visible */ }
      throw error;
    }
  });
});
preview(stage);
