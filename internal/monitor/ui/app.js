'use strict';
const $ = selector => document.querySelector(selector);
let state = {watches: []};
const date = value => value ? new Date(value).toLocaleString('ru-RU', {day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : 'ещё нет снимка';
const number = value => new Intl.NumberFormat('ru-RU').format(value);
function node(tag, text, cls) { const item=document.createElement(tag); if(text!==undefined)item.textContent=text; if(cls)item.className=cls; return item; }
function notice(message) { $('#notice').textContent=message; $('#notice').hidden=!message; }
async function api(path, method='GET', body) {
 const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Vvuzz-Local':'1'},body:body===undefined?undefined:JSON.stringify(body)});
 const result=await response.json().catch(()=>({error:'Не удалось выполнить запрос'}));
 if(!response.ok)throw new Error(result.error||'Запрос отклонён'); return result;
}
function busy(form,value) { form.querySelectorAll('button').forEach(button=>button.disabled=value); }
function showWatchForm(show=true) { $('#watch-form').hidden=!show;$('#add-button').setAttribute('aria-expanded',String(show));if(show)$('#watch-form input').focus(); }
$('#add-button').onclick=()=>showWatchForm($('#watch-form').hidden);
$('#empty-add').onclick=()=>showWatchForm();$('#cancel-watch').onclick=()=>showWatchForm(false);
$('#settings-button').onclick=()=>{const open=$('#settings').hidden;$('#settings').hidden=!open;$('#uninstall-settings').hidden=!open;$('#settings-button').setAttribute('aria-expanded',String(open));if(open)$('#archive-path').focus();};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b===button);if(b===button)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});$('#monitor-view').hidden=button.dataset.tab!=='monitor';$('#archive-view').hidden=button.dataset.tab!=='archive';notice('');});
$('#archive-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;busy(form,true);try{await api('/api/archive','POST',{path:$('#archive-path').value});await refresh();notice('Архив подключён. Теперь можно искать по ID.');}catch(error){notice(error.message);}finally{busy(form,false);}};
$('#watch-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;busy(form,true);const data=new FormData(form);try{await api('/api/watches','POST',{applicant_id:data.get('applicant_id'),url:data.get('url'),title:data.get('title'),seats:Number(data.get('seats')),rules_reviewed:data.has('rules_reviewed')});form.reset();showWatchForm(false);notice('Список добавлен. Первый запрос появится автоматически.');await refresh();}catch(error){notice(error.message);}finally{busy(form,false);}};
function renderWatches(){
 $('#watches').replaceChildren();$('#watch-count').textContent=state.watches.length;$('#empty').hidden=state.watches.length>0;
 for(const watch of state.watches){
  const card=node('article',undefined,'watch'),head=node('div',undefined,'watch-head'),title=node('div');
  title.append(node('h3',watch.list.title),node('small',`ID ${watch.applicant_id} · ${watch.list.university||'Выбранный конкурс'}`));
  head.append(title,node('span',watch.error?'Обновление отложено':watch.updated_at?'Снимок получен':'Ожидание источника',watch.error?'badge warn':'badge'));card.append(head);
  const own=watch.rank>0;
  const above=own?watch.above_consent:null;
  const stats=node('dl',undefined,'stats');
  for(const[label,value]of [['Место в списке',own?number(watch.rank):'—'],['Согласий выше',above===null?'—':number(above)],['Мест в конкурсе',watch.list.seats?number(watch.list.seats):'—'],['Участников',watch.updated_at?number(watch.total):'—']]){const stat=node('div',undefined,'stat');stat.append(node('dt',label),node('dd',value));stats.append(stat);}card.append(stats);
  if(watch.updated_at&&!own)card.append(node('p','Ваш ID в последнем списке не найден. Проверьте ссылку и ID.','error-text'));
  if(watch.error)card.append(node('p',watch.error,'error-text'));
  const bottom=node('div',undefined,'watch-bottom'),info=node('p',undefined,'muted');info.append(node('span',`Снимок: ${date(watch.updated_at)}`),node('br'),node('span',state.paused?'Наблюдение на паузе':`Следующая проверка: ${watch.next_at?date(watch.next_at):'скоро'}`));bottom.append(info);
  const actions=node('div',undefined,'watch-actions'),history=node('button','История','quiet'),remove=node('button','Убрать','quiet');history.onclick=()=>showHistory(watch);remove.onclick=async()=>{if(!confirm('Убрать этот список и его локальную историю наблюдений? Исходный архив сохранится.'))return;try{await api(`/api/watches/${watch.id}`,'DELETE');await refresh();}catch(error){notice(error.message);}};actions.append(history,remove);bottom.append(actions);card.append(bottom);$('#watches').append(card);
 }
}
function table(headers, rows){const wrap=node('div',undefined,'table-scroll'),table=node('table'),thead=node('thead'),tr=node('tr');for(const heading of headers)tr.append(node('th',heading));thead.append(tr);table.append(thead);const body=node('tbody');for(const cells of rows){const row=node('tr');for(const cell of cells){const td=node('td');if(cell instanceof Node)td.append(cell);else td.textContent=cell;row.append(td);}body.append(row);}table.append(body);wrap.append(table);return wrap;}
$('#lookup-form').onsubmit=async event=>{
 event.preventDefault();const form=event.currentTarget;busy(form,true);notice('');$('#lookup-results').replaceChildren(node('p','Ищем конкурсы…','muted'));
 try{const results=await api(`/api/lookup?id=${encodeURIComponent($('#applicant-id').value)}`);$('#lookup-results').replaceChildren(node('p',results.length?`Найдено конкурсов: ${number(results.length)}${results.length===600?' (показаны первые 600)':''}`:'В этом архиве ID не найден. Проверьте год и идентификатор.','result-summary'));
 if(results.length){const rows=results.map(result=>{const title=node('div');title.append(node('strong',result.title||'Название не указано'),node('small',result.university),node('small',`${result.year} · ${result.quota}`),node('small',`Архив: ${date(result.updated_at)}`));return[title,result.rank?number(result.rank):'—',result.seats?number(result.seats):'—',number(result.above_consent),result.consent?'Подано':'Нет'];});$('#lookup-results').append(table(['Вуз и конкурс','Место','Мест','Согласий выше','Ваше согласие'],rows),node('p','Согласий выше: участники выше вашей строки с согласием именно в этом конкурсе. Пропущенные списки и изменения после даты снимка здесь не учитываются.','table-note'));}
 }catch(error){$('#lookup-results').replaceChildren();notice(error.message);}finally{busy(form,false);}
};
async function showHistory(watch){try{const rows=await api(`/api/history/${watch.id}`),body=$('#history-body');body.replaceChildren(node('p',watch.list.title,'muted'));if(!rows.length)body.append(node('p','История появится после первого успешного обновления. Затем сохраняются только изменившиеся списки.'));else{
 if(rows.length>1){const values=[...rows].reverse(),valid=values.filter(row=>row.rank>0);if(valid.length>1){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 720 190');svg.setAttribute('role','img');svg.setAttribute('aria-label','Изменение места: меньшее число означает более высокую позицию');svg.classList.add('chart');const min=Math.min(...valid.map(row=>row.rank)),max=Math.max(...valid.map(row=>row.rank));const line=document.createElementNS(ns,'polyline');line.setAttribute('points',valid.map((row,index)=>`${20+index/(valid.length-1)*680},${25+(row.rank-min)/Math.max(1,max-min)*140}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','#326342');line.setAttribute('stroke-width','3');svg.append(line);body.append(svg,node('p',`Выше на графике — лучшее место. Диапазон: ${min}–${max}. Последние ${rows.length} изменений.`,'muted'));}}
 body.append(table(['Когда','Место','Согласий выше','Участников'],rows.map(row=>[date(row.at),row.rank||'Нет в списке',number(row.above_consent),number(row.total)])));}$('#history').showModal();}catch(error){notice(error.message);}}
$('#close-history').onclick=()=>$('#history').close();
$('#pause-button').onclick=async()=>{try{await api('/api/pause','POST',{paused:!state.paused});await refresh();}catch(error){notice(error.message);}};
async function refresh(){state=await api('/api/state');$('#version').textContent=state.version;$('#pause-button').textContent=state.paused?'Продолжить':'Пауза';$('#monitor-status').textContent=state.paused?'Наблюдение на паузе. Сохранённые данные доступны.':state.source_cooldown?`Источник попросил подождать. Все запросы отложены до ${date(state.source_cooldown)}.`:'Один ID может участвовать в нескольких конкурсах.';$('#archive-status').textContent=state.archive_error||(state.archive?`Подключён: ${state.archive}`:'Архив не подключён. Наблюдение за новыми списками доступно без него.');renderWatches();}
let uninstallRunning=false;
function showUninstall(){if(!$('#uninstall').open)$('#uninstall').showModal();}
$('#uninstall-button').onclick=showUninstall;
function closeUninstall(){ $('#uninstall').close();if(location.hash==='#uninstall')history.replaceState(null,'',location.pathname); }
$('#cancel-uninstall').onclick=closeUninstall;$('#close-uninstall').onclick=closeUninstall;
$('#uninstall').addEventListener('cancel',()=>{if(location.hash==='#uninstall')history.replaceState(null,'',location.pathname);});
window.addEventListener('hashchange',()=>{if(location.hash==='#uninstall')showUninstall();});
if(location.hash==='#uninstall')showUninstall();
$('#uninstall-form').onsubmit=async event=>{
 event.preventDefault();const form=event.currentTarget;busy(form,true);$('#uninstall-error').hidden=true;
 try{await api('/api/uninstall','POST',{confirmed:true,mode:new FormData(form).get('mode')});uninstallRunning=true;form.hidden=true;$('#uninstall-started').hidden=false;
 const poll=setInterval(async()=>{try{const result=await api('/api/uninstall');if(result.status==='failed'){clearInterval(poll);uninstallRunning=false;form.hidden=false;$('#uninstall-started').hidden=true;$('#uninstall-error').textContent=result.error;$('#uninstall-error').hidden=false;busy(form,false);}}catch{clearInterval(poll);$('#uninstall-progress').textContent='Vvuzz остановлен. Завершите удаление в системном окне.';}},2000);
 }catch(error){$('#uninstall-error').textContent=error.message;$('#uninstall-error').hidden=false;busy(form,false);}
};
refresh().catch(error=>notice(error.message));
setInterval(()=>{if(!uninstallRunning&&!document.hidden&&!$('#history').open&&!$('#uninstall').open)refresh().catch(()=>notice('Нет связи с Vvuzz. Проверьте, работает ли приложение в трее.'));},15000);
