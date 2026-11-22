const $ = s => document.querySelector(s);
const today = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
document.querySelectorAll('input[type=date]').forEach(i => { i.value=today; i.min=today; });
let session;
try { session=sessionStorage.getItem('hanoi_ui_session'); if(!session){session=crypto.randomUUID();sessionStorage.setItem('hanoi_ui_session',session);} } catch {session=String(Date.now());}
let places=[],selected=[];
async function api(path,body){
  const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify({...body,session}):undefined});
  let data;try{data=await response.json();}catch{throw Error('Máy chủ chưa hỗ trợ API này. Hãy kiểm tra phiên bản backend.');}
  if(!response.ok)throw Error(data.error||data.answer||'Yêu cầu chưa thực hiện được. Vui lòng thử lại.');
  return data;
}
function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
function openChat(){ $('#chat-panel').hidden=false;$('#chat-launcher').setAttribute('aria-expanded','true');$('#chat-input').focus(); }
function closeChat(){ $('#chat-panel').hidden=true;$('#chat-launcher').setAttribute('aria-expanded','false');$('#chat-launcher').focus(); }
document.querySelectorAll('[data-chat]').forEach(b=>b.onclick=openChat);
$('#chat-launcher').onclick=()=>$('#chat-panel').hidden?openChat():closeChat();$('#chat-close').onclick=closeChat;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#chat-panel').hidden)closeChat();});
function message(text,kind){const n=node('div',text,`message ${kind}`);$('#chat-log').append(n);n.scrollIntoView({block:'nearest'});return n;}
let chatting=false;
async function ask(text){if(chatting)return;openChat();chatting=true;message(text,'user');const pending=message('Đang tìm thông tin cho bạn…','bot');const send=$('#chat-form button');send.disabled=true;try{const d=await api('/api/chat',{message:text});pending.textContent=d.answer||d.error||'Chưa có câu trả lời.';}catch(e){pending.textContent=e.message;}finally{chatting=false;send.disabled=false;$('#chat-log').scrollTop=$('#chat-log').scrollHeight;}}
$('#chat-form').onsubmit=e=>{e.preventDefault();const input=$('#chat-input');const text=input.value.trim();if(text&&!chatting){input.value='';ask(text);}};
$('#chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#chat-form').requestSubmit();}};
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>ask(b.dataset.question));
function renderSelected(){const box=$('#selected');box.replaceChildren();if(!selected.length){box.textContent='Thêm địa điểm từ mục Khám phá để tự xếp lịch.';return;}selected.forEach((p,i)=>{const row=node('div',undefined,'selected-item');row.append(node('span',`${i+1}. ${p.name}`));for(const [label,delta] of [['↑',-1],['↓',1],['×',0]]){const b=node('button',label);b.type='button';b.setAttribute('aria-label',delta?`${delta<0?'Đưa lên':'Đưa xuống'} ${p.name}`:`Bỏ ${p.name}`);b.disabled=delta!==0&&(i+delta<0||i+delta>=selected.length);b.onclick=()=>{if(delta){[selected[i],selected[i+delta]]=[selected[i+delta],selected[i]];}else selected.splice(i,1);renderSelected();renderPlaces();};row.append(b);}box.append(row);});}
function renderPlaces(){const grid=$('#places');grid.replaceChildren();const query=$('#search').value.toLocaleLowerCase('vi');const filtered=places.filter(p=>p.name.toLocaleLowerCase('vi').includes(query)&&(!$('#indoor').checked||p.indoor)&&(!$('#free').checked||p.free));$('#places-status').textContent=`${filtered.length} điểm tham quan · Dữ liệu từ HanoiTravelBot`;for(const p of filtered){const card=node('article',undefined,'place');card.append(node('span',p.district,'district'),node('h3',p.name),node('p',`Giờ mở cửa: ${p.hours||'Chưa có dữ liệu'}`),node('p',p.free?`Vào tự do${p.ticket?` · ${p.ticket}`:''}`:`Vé: ${p.ticket||'Chưa có dữ liệu'}`));const details=node('details');details.append(node('summary','Ghi chú & nguồn dữ liệu'),node('p',p.notes||'Chưa có ghi chú bổ sung.'));details.append(node('p',`Độ tin cậy: ${{high:'Cao',medium:'Trung bình',low:'Thấp — cần xác nhận'}[p.confidence]||'Chưa xác định'}`));for(const source of p.sources||[]){try{const url=new URL(source);if(!['https:','http:'].includes(url.protocol))continue;const a=node('a',url.hostname);a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';details.append(a);}catch{}}card.append(details);const actions=node('div',undefined,'place-actions');const add=node('button',selected.some(s=>s.id===p.id)?'Đã thêm vào lịch':'Thêm vào lịch');add.disabled=selected.some(s=>s.id===p.id);add.onclick=()=>{selected.push(p);renderSelected();renderPlaces();};const question=node('button','Hỏi về điểm này');question.onclick=()=>ask(`Cho tôi biết về ${p.name}, giờ mở cửa và lưu ý khi tham quan.`);actions.append(add,question);card.append(actions);grid.append(card);}}
async function loadPlaces(){ $('#places-status').textContent='Đang tải danh mục địa điểm…';try{const d=await api(`/api/places?date=${encodeURIComponent($('#places-date').value)}&lang=vi`);places=d.places||[];renderPlaces();}catch(e){$('#places-status').textContent=e.message;}}
$('#places-date').onchange=loadPlaces;['#search','#indoor','#free'].forEach(s=>$(s).oninput=renderPlaces);
$('#plan-form').onsubmit=async e=>{e.preventDefault();const manual=e.submitter?.value==='manual';const result=$('#plan-result');result.hidden=false;if(manual&&!selected.length){result.textContent='Bạn hãy thêm ít nhất một địa điểm ở mục Khám phá trước nhé.';return;}const body=Object.fromEntries(new FormData(e.target));for(const k of ['people','budget_vnd','max_walk_km'])body[k]=Number(body[k]);body.lang='vi';if(manual)body.stops=selected.map(p=>({kind:'visit',poi_id:p.id,minutes:p.visit_minutes||60}));else body.must_include=selected.map(p=>p.id);const buttons=[...e.target.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);result.textContent=manual?'Đang tính giờ, chi phí và kiểm tra lịch…':'Đang chọn điểm và xây dựng hành trình cho bạn…';try{const d=await api(manual?'/api/schedule':'/api/plan',body);result.textContent=d.text||d.answer||'Chưa có kết quả lịch trình.';}catch(error){result.textContent=error.message;}finally{buttons.forEach(b=>b.disabled=false);}};
$('#weather-open').onclick=()=>$('#weather-dialog').showModal();$('#weather-close').onclick=()=>$('#weather-dialog').close();
// Thời tiết: chỉ quận/huyện Hà Nội, tối đa 7 ngày (hôm nay + 6). Gõ "Ha" -> gợi ý Hai Bà Trưng, Hà Đông...
(()=>{
const form=$('#weather-form'),input=$('#wx-place'),list=$('#wx-list'),result=$('#weather-result'),dateIn=form.elements.date;
const ymd=d=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
dateIn.min=today;dateIn.max=ymd(new Date(Date.now()+6*864e5));
const fold=s=>String(s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/đ/g,'d').replace(/^(quan|huyen|thi xa)\s+/,'').trim();
const cache=new Map();let items=[],active=-1,timer;
async function suggest(q){const k=fold(q);if(cache.has(k))return cache.get(k);const d=await api(`/api/areas?q=${encodeURIComponent(q)}`);cache.set(k,d.areas||[]);return d.areas||[];}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
function close(){list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
function render(){
  list.replaceChildren();
  if(!items.length){list.append(el('li','empty','Không có quận/huyện nào khớp'));}
  items.forEach((a,i)=>{const li=el('li');li.id=`wx-opt-${i}`;li.setAttribute('role','option');li.setAttribute('aria-selected',String(i===active));
    li.append(el('span',null,a.name),el('small',null,a.kind));
    li.addEventListener('pointerdown',e=>{e.preventDefault();choose(i);});list.append(li);});
  list.hidden=false;input.setAttribute('aria-expanded','true');
  if(active>=0){input.setAttribute('aria-activedescendant',`wx-opt-${active}`);list.children[active]?.scrollIntoView({block:'nearest'});}
}
function open(q){clearTimeout(timer);timer=setTimeout(async()=>{try{items=await suggest(q);active=items.length?0:-1;render();}catch{close();}},90);}
function choose(i){const a=items[i];if(a){input.value=a.name;}close();}
// Chữ gõ chưa trùng tên quận/huyện nào -> rút về gợi ý đầu tiên ("Ha" -> Hai Bà Trưng)
async function snap(){const v=input.value.trim();if(!v)return;try{const s=await suggest(v);if(s.length&&!s.some(a=>fold(a.name)===fold(v)))input.value=s[0].name;else if(s.length)input.value=s.find(a=>fold(a.name)===fold(v)).name;}catch{}}
input.addEventListener('input',()=>open(input.value));
input.addEventListener('focus',()=>{input.select();open('');});
input.addEventListener('blur',()=>{clearTimeout(timer);setTimeout(()=>{close();snap();},100);});
input.addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'&&list.hidden){e.preventDefault();open(input.value);return;}
  if(list.hidden)return;
  if(e.key==='ArrowDown'){e.preventDefault();active=Math.min(items.length-1,active+1);render();}
  else if(e.key==='ArrowUp'){e.preventDefault();active=Math.max(0,active-1);render();}
  else if(e.key==='Enter'&&active>=0){e.preventDefault();choose(active);form.requestSubmit();}
  else if(e.key==='Escape'){e.preventDefault();close();}
});
const num=v=>v==null?'–':Number(v).toLocaleString('vi-VN',{maximumFractionDigits:1});
const aqiClass=v=>v<=50?'good':v<=100?'mod':v<=150?'bad':'vbad';
function msg(text){return el('p','wx-msg',text);}
function view(d){
  const wrap=el('div','wx'),top=el('div','wx-top'),main=el('div','wx-main'),facts=el('ul','wx-facts');
  const place=(d.place&&(d.place.label||d.place.name))||'Hoàn Kiếm';
  const [,mm,dd]=d.date.split('-');
  let big='–',head=`${d.day} ${dd}/${mm} · ${place}`;
  if(d.now){
    big=`${num(d.now.temp)}°`;head=`${d.now.desc.charAt(0).toUpperCase()+d.now.desc.slice(1)} · ${place}`;
    for(const f of [`Cảm giác ${num(d.now.feels_like)}°`,`Độ ẩm ${d.now.humidity}%`,`Gió ${num(d.now.wind_kmh)} km/h`])facts.append(el('li',null,f));
    if(d.air){const li=el('li','wx-aqi');li.append(el('i',aqiClass(d.air.aqi)),document.createTextNode(`AQI ${d.air.aqi} · ${d.air.level||''}`));li.title=d.air.measured?'Đo tại trạm WAQI':'Ước tính mô hình';facts.append(li);}
  }else if(d.day_summary){
    const s=d.day_summary;big=`${Math.round(s.tmin)}–${Math.round(s.tmax)}°`;
    for(const f of [`Mưa tối đa ${s.rain_prob_max??'?'}%`,`Lượng mưa ${num(s.rain_mm)} mm`])facts.append(el('li',null,f));
  }
  main.append(el('strong',null,head),facts);const tEl=el('div','wx-temp',big.replace(/°$/,''));if(big.endsWith('°'))tEl.append(el('span','deg','°'));top.append(tEl,main);wrap.append(top);
  let hrs=d.hourly||[];
  hrs=d.is_today?hrs.slice(0,8):hrs.filter(h=>+h.h>=6&&+h.h<=21&&+h.h%2===0).slice(0,8);
  if(hrs.length){const strip=el('div','wx-hours');strip.setAttribute('role','list');strip.setAttribute('aria-label','Dự báo theo giờ');
    for(const h of hrs){const c=el('div');c.setAttribute('role','listitem');c.title=h.desc;
      const v=el('span','v');v.append(el('b',null,`${h.temp??'–'}°`),el('span',(h.rain_prob??0)>=50?'wet':null,`${h.rain_prob??'?'}%`));
      c.setAttribute('aria-label',`${h.h} giờ: ${h.temp} độ, khả năng mưa ${h.rain_prob}%`);c.append(el('span','h',`${+h.h}h`),v);strip.append(c);}
    wrap.append(strip);}
  const src=[`${d.source}${d.model_time?`, cập nhật ${d.model_time}`:''}`,'% là khả năng mưa'];
  if(d.reused)src.push('dùng lại dữ liệu vừa lấy');if(d.stale)src.push('dữ liệu cũ do API lỗi');
  wrap.append(el('p','wx-src',src.join(' · ')));
  return wrap;
}
form.onsubmit=async e=>{
  e.preventDefault();close();const button=form.querySelector('button.primary');button.disabled=true;
  result.replaceChildren(msg('Đang lấy dự báo thời tiết…'));
  try{await snap();const params=new URLSearchParams({place:input.value.trim(),date:dateIn.value,session,lang:'vi'});
    const d=await api(`/api/weather?${params}`);result.replaceChildren(d.ok?view(d):msg(d.text||'Chưa có dữ liệu thời tiết.'));}
  catch(error){result.replaceChildren(msg(error.message));}
  finally{button.disabled=false;}
};
})();
loadPlaces();

// One continuously visible player: no opacity animation or overlapping images.
const heroVideo = $('#hero-video');
const videoToggle = $('#video-toggle');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let videoPausedByUser = false;
function updateVideoButton(){
  videoToggle.textContent=heroVideo.paused?'Ph\u00e1t video n\u1ec1n':'T\u1ea1m d\u1eebng n\u1ec1n';
  videoToggle.setAttribute('aria-label',videoToggle.textContent);
}
async function playHeroVideo(){
  try{await heroVideo.play();}catch{}
  if(videoPausedByUser||document.hidden||reducedMotion.matches||navigator.connection?.saveData)heroVideo.pause();
  updateVideoButton();
}
function configureHeroVideo(){
  if(reducedMotion.matches||navigator.connection?.saveData){heroVideo.pause();videoToggle.hidden=true;return;}
  if(!heroVideo.getAttribute('src'))heroVideo.src='assets/hanoi-lake.mp4';
  heroVideo.playbackRate=1;
  videoToggle.hidden=false;
  if(!videoPausedByUser&&!document.hidden)playHeroVideo();
}
videoToggle.onclick=()=>{videoPausedByUser=!heroVideo.paused;if(videoPausedByUser)heroVideo.pause();else playHeroVideo();};
heroVideo.addEventListener('play',updateVideoButton);
heroVideo.addEventListener('pause',updateVideoButton);
heroVideo.addEventListener('error',()=>{heroVideo.hidden=true;videoToggle.hidden=true;});
reducedMotion.addEventListener('change',configureHeroVideo);
document.addEventListener('visibilitychange',()=>{if(document.hidden)heroVideo.pause();else configureHeroVideo();});
configureHeroVideo();
