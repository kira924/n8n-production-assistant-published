'use strict';
const form = document.getElementById('production-form');
const controls = Object.fromEntries([...form.elements].filter(el => el.name).map(el => [el.name, el]));
const submitButton = document.getElementById('submit-button');
const formError = document.getElementById('form-error');
let csrfToken = '';
let currentJob = null;
let currentOrder = null;
let generation = 0;
const labels = {planning:'جاري التخطيط',awaiting_review:'جاهز للمراجعة',reviewing:'جاري تسجيل القرار',created:'تم إنشاء الأمر',denied:'تم رفض العرض',rejected:'الطلب غير مناسب',error:'تعذّر إكمال الطلب'};
const services = {studio:'الاستوديو',camera:'الكاميرات',audio:'تجهيز الصوت',editing:'المونتاج'};
const money = value => new Intl.NumberFormat('en-US').format(value);
function show(id, visible) { document.getElementById(id).hidden = !visible; }
function lockRequest(locked) {
  for(const field of form.elements) field.disabled=locked;
  for(const preset of document.querySelectorAll('[data-preset]')) preset.disabled=locked;
}
function state(status) {
  const pill = document.getElementById('status-label');
  pill.textContent = labels[status] || 'في انتظار طلبك';
  pill.dataset.status = status;
  show('empty-state', status === 'empty');
  show('planning-state', status === 'planning' || status === 'reviewing');
  show('result-content', !['empty','planning','reviewing'].includes(status));
}
async function request(path, method='GET', body) {
  const headers = {};
  if (method !== 'GET') { headers['Content-Type']='application/json'; headers['X-CSRF-Token']=csrfToken; }
  const response = await fetch(path,{method,headers,credentials:'same-origin',body:body?JSON.stringify(body):undefined});
  let result;
  try { result=await response.json(); } catch { throw new Error('تعذّر قراءة استجابة الخدمة. حاول مرة أخرى.'); }
  if (!response.ok) {
    if (response.status === 401) { await openSession(); }
    throw new Error(typeof result.detail === 'string' ? result.detail : 'راجع الحقول وأدخل قيمًا صالحة.');
  }
  return result;
}
async function openSession() {
  const session = await request('/api/session');
  csrfToken = session.csrf_token;
  const day = new Date(session.today+'T12:00:00Z');
  day.setUTCDate(day.getUTCDate()+2);
  if(day.getUTCDate()===15) day.setUTCDate(16);
  controls.production_date.min = session.today;
  const max = new Date(session.today+'T12:00:00Z'); max.setUTCDate(max.getUTCDate()+366);
  controls.production_date.max = max.toISOString().slice(0,10);
  if(!controls.production_date.value) controls.production_date.value = day.toISOString().slice(0,10);
}
function render(result) {
  state(result.status);
  document.getElementById('result-message').textContent = result.message || 'راجع نتيجة الطلب.';
  show('quote-section', Boolean(result.quote));
  show('order-section', Boolean(result.order));
  show('review-actions', result.status === 'awaiting_review');
  if(result.quote) {
    document.getElementById('quote-total').textContent = money(result.quote.total_sar);
    document.getElementById('production-plan').textContent = result.plan || 'تم التحقق من بنود العرض والتوافر.';
    const rows = document.getElementById('line-items'); rows.replaceChildren();
    for(const item of result.quote.line_items) {
      const row = document.createElement('tr');
      for(const value of [services[item.code] || item.code, item.quantity, money(item.line_total_sar)+' SAR']) {
        const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell);
      }
      rows.append(row);
    }
  }
  if(result.order) { currentOrder=result.order; document.getElementById('order-id').textContent=result.order.order_id; }
  lockRequest(['awaiting_review','reviewing'].includes(result.status));
}
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
async function poll(job, version) {
  for(let i=0;i<125;i++) {
    const result = await request('/api/jobs/'+encodeURIComponent(job));
    if(version!==generation) return;
    if(!['planning','reviewing'].includes(result.status)) { render(result); return; }
    await sleep(1500);
  }
  throw new Error('استغرق التخطيط وقتًا أطول من المتوقع. ابدأ طلبًا جديدًا لاحقًا.');
}
form.addEventListener('submit',async event => {
  event.preventDefault(); formError.hidden=true; submitButton.disabled=true; currentOrder=null;
  const version=++generation;
  const body = {brief:controls.brief.value.trim(), production_date:controls.production_date.value,
    duration_hours:Number(controls.duration_hours.value), camera_count:Number(controls.camera_count.value),
    include_audio:controls.include_audio.checked, editing_hours:Number(controls.editing_hours.value), budget_sar:Number(controls.budget_sar.value)};
  lockRequest(true);
  state('planning');
  try { if(!csrfToken) await openSession(); const result=await request('/api/jobs','POST',body); currentJob=result.id; await poll(currentJob,version); }
  catch(error) { if(version===generation) { state('empty'); formError.textContent=error.message; formError.hidden=false; lockRequest(false); } }
});
for(const button of document.querySelectorAll('[data-preset]')) button.addEventListener('click',()=>{
  controls.budget_sar.value=button.dataset.preset==='budget'?'1000':'3000';
  controls.camera_count.value=button.dataset.preset==='capacity'?'3':'2';
  controls.duration_hours.value='2'; controls.editing_hours.value='2'; controls.include_audio.checked=true;
  for(const item of document.querySelectorAll('[data-preset]')) item.classList.toggle('selected',item===button);
});
async function decide(decision) {
  if(!currentJob) return;
  const buttons=[document.getElementById('approve-button'),document.getElementById('decline-button')];
  buttons.forEach(button=>button.disabled=true);
  formError.hidden=true;
  try { const result=await request('/api/jobs/'+encodeURIComponent(currentJob)+'/decision','POST',{decision}); render(result); }
  catch(error) {
    try { render(await request('/api/jobs/'+encodeURIComponent(currentJob))); } catch { }
    formError.textContent=error.message; formError.hidden=false;
  } finally { buttons.forEach(button=>button.disabled=false); }
}
document.getElementById('approve-button').addEventListener('click',()=>decide('approved'));
document.getElementById('decline-button').addEventListener('click',()=>decide('denied'));
document.getElementById('new-request-button').addEventListener('click',()=>{ generation++; currentJob=null; currentOrder=null; state('empty'); formError.hidden=true; lockRequest(false); controls.brief.focus(); });
document.getElementById('download-button').addEventListener('click',()=>{
  if(!currentOrder) return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(currentOrder,null,2)],{type:'application/json'}));
  const anchor=document.createElement('a'); anchor.href=url; anchor.download='synthetic-work-order.json'; anchor.hidden=true;
  document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function initialize() {
  lockRequest(true);
  const caption = submitButton.textContent;
  submitButton.textContent='جاري تجهيز التجربة…';
  for(let attempt=0;attempt<3;attempt++) {
    try {
      await openSession();
      lockRequest(false); submitButton.textContent=caption; return;
    } catch {
      if(attempt<2) await sleep(2000);
    }
  }
  submitButton.textContent=caption;
  formError.textContent='تعذّر بدء الجلسة. انتظر قليلًا ثم حدّث الصفحة.';
  formError.hidden=false;
}
initialize();
