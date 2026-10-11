'use strict';
const form=document.getElementById('production-form'), briefForm=document.getElementById('brief-form'), briefInput=document.getElementById('brief');
const controls=Object.fromEntries([...form.elements].filter(el=>el.name).map(el=>[el.name,el]));
const submitButton=document.getElementById('submit-button'), interpretButton=document.getElementById('interpret-button'), formError=document.getElementById('form-error');
let csrfToken='',today='',exampleDate='',currentJob=null,currentOrder=null,draft=null,analyzedBrief='',generation=0;
const labels={interpreting:'جاري فهم الطلب',draft:'راجع اقتراح المساعد',planning:'جاري تجهيز العرض',awaiting_review:'جاهز للمراجعة',reviewing:'جاري تسجيل القرار',created:'تم إنشاء الأمر',denied:'تم رفض العرض',rejected:'راجع سبب تعذّر الطلب',error:'تعذّر إكمال الطلب'};
const services={studio:'الاستوديو',camera:'الكاميرات',audio:'تجهيز الصوت',editing:'المونتاج'};
const fieldLabels={production_date:'تاريخ الإنتاج',duration_hours:'ساعات الاستوديو',camera_count:'الكاميرات',include_audio:'تجهيز الصوت',editing_hours:'ساعات المونتاج',budget_sar:'الميزانية'};
const sourceLabels={explicit:'من طلبك',suggested:'اقتراح للمراجعة',missing:'محتاج توضيح',edited:'تعديلك'};
const money=value=>new Intl.NumberFormat('en-US').format(value);
function show(id,visible){document.getElementById(id).hidden=!visible;}
function lockRequest(locked){for(const field of [...form.elements,...briefForm.elements])field.disabled=locked;for(const preset of document.querySelectorAll('[data-preset]'))preset.disabled=locked;}
function state(status){
  const pill=document.getElementById('status-label');pill.textContent=labels[status]||'في انتظار فكرتك';pill.dataset.status=status;
  show('empty-state',['empty','draft'].includes(status));show('planning-state',['interpreting','planning','reviewing'].includes(status));show('result-content',!['empty','draft','interpreting','planning','reviewing'].includes(status));
  document.getElementById('progress-title').textContent=status==='interpreting'?'جاري فهم فكرتك…':status==='reviewing'?'جاري تسجيل قرارك…':'جاري تجهيز العرض…';
  document.getElementById('progress-copy').textContent=status==='interpreting'?'المساعد بيقرأ كلامك والكتالوج، ويفصل التفاصيل المذكورة عن اقتراحاته. لو معلومة ناقصة هيسأل عنها.':'المساعد بيراجع التوافر والسعر للتفاصيل التي اعتمدتها. إنشاء أمر العمل ينتظر موافقتك.';
}
async function request(path,method='GET',body){
  const headers={};if(method!=='GET'){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrfToken;}
  const response=await fetch(path,{method,headers,credentials:'same-origin',body:body?JSON.stringify(body):undefined});let result;
  try{result=await response.json();}catch{throw new Error('تعذّر قراءة استجابة الخدمة. حاول مرة أخرى.');}
  if(!response.ok){if(response.status===401)await openSession();throw new Error(typeof result.detail==='string'?result.detail:'راجع الحقول وأدخل قيمًا صالحة.');}return result;
}
async function openSession(){
  const session=await request('/api/session');csrfToken=session.csrf_token;today=session.today;
  const day=new Date(today+'T12:00:00Z');day.setUTCDate(day.getUTCDate()+2);if(day.getUTCDate()===15)day.setUTCDate(16);exampleDate=day.toISOString().slice(0,10);
  controls.production_date.min=today;const max=new Date(today+'T12:00:00Z');max.setUTCDate(max.getUTCDate()+366);controls.production_date.max=max.toISOString().slice(0,10);
}
function example(kind){
  const samples={valid:`عايز أصوّر حوار مع ضيف داخل الاستوديو للنشر على يوتيوب يوم ${exampleDate}، بميزانية 3000 ريال سعودي. محتاج صوت واضح ونسخة نهائية بالمونتاج. اقترح لي التجهيزات المناسبة.`,product:`عايز إعلان قصير لمنتج داخل الاستوديو يوم ${exampleDate} بميزانية 2500 ريال سعودي. لقطات قريبة وحركة بسيطة للمنتج، من غير كلام أو تسجيل صوت، مع مونتاج للنشر على إنستجرام. اقترح لي تجهيزات مناسبة.`,missing:'عايز أصوّر حلقة حوار مع ضيف داخل الاستوديو، وصوت واضح ومونتاج للنشر. اقترح لي التجهيزات المناسبة.'};
  briefInput.value=samples[kind];clearDraft();for(const item of document.querySelectorAll('[data-preset]'))item.classList.toggle('selected',item.dataset.preset===kind);
}
async function loadCatalog(){
  const catalog=await request('/api/catalog'),items=document.getElementById('catalog-items');items.replaceChildren();
  const units={studio:'ريال / ساعة',camera:'ريال / كاميرا / ساعة',audio:'ريال / ساعة',editing:'ريال / ساعة'},capacities={studio:`حتى ${catalog.capacity.studio_hours} ساعات`,camera:`${catalog.capacity.camera_packages} كاميرات متاحة`,audio:`${catalog.capacity.audio_packages} تجهيز صوت متاح`,editing:'حسب الساعات المطلوبة'};
  for(const service of catalog.services){const item=document.createElement('article');item.className='catalog-item';for(const [tag,text] of [['strong',services[service.code]||service.name],['span',money(service.unit_price_sar)+' '+units[service.code]],['small',capacities[service.code]]]){const element=document.createElement(tag);element.textContent=text;item.append(element);}items.append(item);}
}
function displayValue(name,value){if(value===null)return 'بانتظار إجابتك';if(name==='include_audio')return value?'مع تجهيز الصوت':'بدون تجهيز الصوت';if(name==='budget_sar')return money(value)+' ريال';if(['duration_hours','editing_hours'].includes(name))return value+' ساعة';if(name==='camera_count')return value+' كاميرا';return String(value);}
function renderDraft(){
  document.getElementById('draft-summary').textContent=draft.summary;const cards=document.getElementById('draft-cards');cards.replaceChildren();const questions=document.getElementById('missing-questions');questions.replaceChildren();let missing=false;
  for(const [name,item] of Object.entries(draft.fields)){
    const card=document.createElement('article');card.className='draft-card';card.dataset.source=item.source;
    for(const [tag,text] of [['span',fieldLabels[name]],['strong',displayValue(name,item.value)],['small',sourceLabels[item.source]],['p',item.reason]]){const element=document.createElement(tag);element.textContent=text;card.append(element);}
    if(item.evidence){const evidence=document.createElement('blockquote');evidence.textContent='«'+item.evidence+'»';card.append(evidence);}cards.append(card);controls[name].value=item.value===null?'':String(item.value);
    if(item.source==='missing'){missing=true;const question=document.createElement('li');question.textContent=item.reason;questions.append(question);}
  }
  show('missing-section',missing);document.getElementById('editable-details').open=missing;show('draft-section',true);state('draft');lockRequest(false);
}
function clearDraft(){draft=null;analyzedBrief='';currentJob=null;currentOrder=null;generation++;show('draft-section',false);state('empty');formError.hidden=true;}
briefInput.addEventListener('input',clearDraft);
briefForm.addEventListener('submit',async event=>{
  event.preventDefault();formError.hidden=true;lockRequest(true);show('draft-section',false);const version=++generation;state('interpreting');
  try{if(!csrfToken)await openSession();const brief=briefInput.value.trim(),result=await request('/api/interpret','POST',{brief});if(version!==generation)return;draft=result;analyzedBrief=brief;renderDraft();}
  catch(error){if(version===generation){state('empty');formError.textContent=error.message;formError.hidden=false;lockRequest(false);}}
});
for(const button of document.querySelectorAll('[data-preset]'))button.addEventListener('click',()=>example(button.dataset.preset));
form.addEventListener('invalid',()=>{document.getElementById('editable-details').open=true;},true);
form.addEventListener('change',event=>{
  if(!draft||!event.target.name)return;const name=event.target.name,card=[...document.getElementById('draft-cards').children][Object.keys(draft.fields).indexOf(name)];if(!card)return;
  const value=name==='include_audio'?(event.target.value===''?null:event.target.value==='true'):name==='production_date'?(event.target.value||null):(event.target.value===''?null:Number(event.target.value));
  card.querySelector('strong').textContent=displayValue(name,value);card.querySelector('small').textContent=sourceLabels.edited;card.querySelector('p').textContent='القيمة النهائية التي اخترتها للمراجعة والتسعير.';const evidence=card.querySelector('blockquote');if(evidence)evidence.remove();card.dataset.source='edited';
});
function render(result){
  state(result.status);document.getElementById('result-message').textContent=result.message||'راجع نتيجة الطلب.';show('quote-section',Boolean(result.quote));show('order-section',Boolean(result.order));show('review-actions',result.status==='awaiting_review');
  if(result.quote){
    document.getElementById('quote-total').textContent=money(result.quote.total_sar);document.getElementById('production-plan').textContent=result.plan||'تم التحقق من بنود العرض والتوافر.';const rows=document.getElementById('line-items');rows.replaceChildren();const unit={studio:'ساعة',camera:'كاميرا × ساعة',audio:'ساعة',editing:'ساعة'};
    for(const item of result.quote.line_items){const row=document.createElement('tr');for(const value of [services[item.code]||item.code,item.quantity+' '+(unit[item.code]||''),money(item.line_total_sar)+' SAR']){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}rows.append(row);}
  }
  if(result.order){currentOrder=result.order;document.getElementById('order-id').textContent=result.order.order_id;}lockRequest(['awaiting_review','reviewing'].includes(result.status));submitButton.disabled=true;
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function poll(job,version){for(let i=0;i<125;i++){const result=await request('/api/jobs/'+encodeURIComponent(job));if(version!==generation)return;if(!['planning','reviewing'].includes(result.status)){render(result);return;}await sleep(1500);}throw new Error('استغرق التخطيط وقتًا أطول من المتوقع. ابدأ طلبًا جديدًا لاحقًا.');}
form.addEventListener('submit',async event=>{
  event.preventDefault();if(!draft)return;formError.hidden=true;currentOrder=null;const version=++generation;
  const body={brief:analyzedBrief,interpretation_id:draft.id,production_date:controls.production_date.value,duration_hours:Number(controls.duration_hours.value),camera_count:Number(controls.camera_count.value),include_audio:controls.include_audio.value==='true',editing_hours:Number(controls.editing_hours.value),budget_sar:Number(controls.budget_sar.value)};
  lockRequest(true);state('planning');try{const result=await request('/api/jobs','POST',body);currentJob=result.id;await poll(currentJob,version);}catch(error){if(version===generation){state('draft');formError.textContent=error.message;formError.hidden=false;lockRequest(false);}}
});
async function decide(decision){
  if(!currentJob)return;const buttons=[document.getElementById('approve-button'),document.getElementById('decline-button')];buttons.forEach(button=>button.disabled=true);formError.hidden=true;
  try{render(await request('/api/jobs/'+encodeURIComponent(currentJob)+'/decision','POST',{decision}));}catch(error){try{render(await request('/api/jobs/'+encodeURIComponent(currentJob)));}catch{}formError.textContent=error.message;formError.hidden=false;}finally{buttons.forEach(button=>button.disabled=false);}
}
document.getElementById('approve-button').addEventListener('click',()=>decide('approved'));document.getElementById('decline-button').addEventListener('click',()=>decide('denied'));
document.getElementById('new-request-button').addEventListener('click',()=>{clearDraft();lockRequest(false);briefInput.focus();});
document.getElementById('download-button').addEventListener('click',()=>{if(!currentOrder)return;const url=URL.createObjectURL(new Blob([JSON.stringify(currentOrder,null,2)],{type:'application/json'})),anchor=document.createElement('a');anchor.href=url;anchor.download='synthetic-work-order.json';anchor.hidden=true;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
async function initialize(){lockRequest(true);const caption=interpretButton.textContent;interpretButton.textContent='جاري تجهيز التجربة…';for(let attempt=0;attempt<3;attempt++){try{await openSession();await loadCatalog();if(!briefInput.value)example('valid');lockRequest(false);interpretButton.textContent=caption;return;}catch{if(attempt<2)await sleep(2000);}}interpretButton.textContent=caption;formError.textContent='تعذّر بدء الجلسة. انتظر قليلًا ثم حدّث الصفحة.';formError.hidden=false;}
initialize();
