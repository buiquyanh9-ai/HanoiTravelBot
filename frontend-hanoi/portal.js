/* Presentation and browser-local trip tools. Backend remains owned by hanoibot. */
(()=>{
  'use strict';
  const q=s=>document.querySelector(s);
  const h=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
  const link=(text,url,cls='text-link')=>{const a=h('a',text,cls);try{const parsed=new URL(url,location.href);if(['https:','http:'].includes(parsed.protocol))a.href=parsed.href;}catch{}a.target='_blank';a.rel='noopener noreferrer';return a;};
  const button=(text,action,cls='secondary')=>{const b=h('button',text,cls);b.type='button';b.onclick=action;return b;};
  const fold=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d').toLowerCase();
  function read(key,fallback){try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}}
  function save(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
  const storedFavorites=read('hanoi_favorites',[]);
  const favorites=new Set(Array.isArray(storedFavorites)?storedFavorites.filter(id=>typeof id==='string'):[]);
  let savedOnly=false,category='all',catalog={places:{}},lastPlan=null,toastTimer,detailVersion=0;
  const meta={
    ho_guom:{category:'outdoor',image:'assets/lake.jpg',description:'Tản bộ quanh mặt hồ, ngắm Tháp Rùa và khám phá những tuyến phố nối vào khu phố cổ.',source:'https://hanoigo.hanoi.gov.vn/place/ho-hoan-kiem-va-den-ngoc-son-ctv-53wqrq'},
    van_mieu:{category:'heritage',image:'assets/temple.jpg',description:'Một khoảng xanh yên tĩnh giữa phố, nơi lưu dấu truyền thống hiếu học và những bia đá tiến sĩ.',source:'https://vanmieu.gov.vn/vi/visitor-information'},
    pho_co:{category:'outdoor',image:'assets/street.jpg',description:'Dạo những phố nghề, khám phá chợ đêm và dừng lại thưởng thức hương vị Hà Thành.',source:'https://hanoigo.hanoi.gov.vn/place/cho-dong-xuan-cho-dem-pho-co'},
    hoang_thanh:{category:'heritage',image:'assets/hanoigo-1.jpg',description:'Khám phá Đoan Môn, thềm điện Kính Thiên và những lớp di sản của kinh thành Thăng Long.',source:'https://hanoigo.hanoi.gov.vn/destination/hoang-thanh-thang-long-phuong-ba-dinh'},
    bt_dan_toc_hoc:{category:'museum',image:'assets/hanoigo-2.jpg',description:'Tìm hiểu văn hóa các dân tộc Việt Nam qua trưng bày trong nhà và vườn kiến trúc ngoài trời.',source:'https://hanoigo.hanoi.gov.vn/destination/bao-tang-dan-toc-hoc-viet-nam-phuong-nghia-do'},
    lang_bac:{category:'heritage',description:'Không gian tưởng niệm Chủ tịch Hồ Chí Minh bên Quảng trường Ba Đình. Hãy kiểm tra giờ viếng theo mùa.',source:'https://hanoigo.hanoi.gov.vn/place/lang-chu-tich-ho-chi-minh-va-quang-truong-ba-dinh-ctv-nvs1mv'},
    hoa_lo:{category:'museum',description:'Tìm hiểu lịch sử qua phòng trưng bày, hiện vật và câu chuyện của những người từng bị giam giữ.',source:'https://hoalo.vn/Home/Ve'},
    bt_lich_su:{category:'museum',description:'Một hành trình qua lịch sử Việt Nam. Vé tham quan bao gồm hai khu trưng bày của bảo tàng.',source:'https://baotanglichsu.vn/vi/Articles/3108/ve-va-le-phi'},
    vuon_thu:{category:'outdoor',description:'Một buổi dạo chơi cùng gia đình ở Thủ Lệ. Khu xem động vật có giờ hoạt động riêng với công viên.',source:'https://hanoigo.hanoi.gov.vn/destination/vuon-thu-ha-noi-phuong-giang-vo'}
  };
  function toast(text){let e=q('#portal-toast');if(!e){e=h('div',null,'toast');e.id='portal-toast';e.setAttribute('role','status');document.body.append(e);}e.textContent=text;e.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>e.hidden=true,3500);}
  function photo(url,alt,cls){if(!url)return null;const img=h('img',null,cls);img.src=url;img.alt=alt;img.loading='lazy';img.decoding='async';img.onerror=()=>{img.hidden=true;};return img;}
  const navigation=h('nav',null,'explore-nav');navigation.setAttribute('aria-label','Các mục khám phá');
  [['Điểm đến','#discover'],['Ẩm thực','#food'],['Cẩm nang','#guide'],['Lịch trình','#planner']].forEach(([name,url])=>{const a=h('a',name);a.href=url;navigation.append(a);});
  const favoriteFilter=button('Đã lưu (0)',()=>{savedOnly=!savedOnly;updateFavoriteLabel();q('#discover').scrollIntoView();renderPlaces();},'');
  navigation.append(button('Bản đồ',openMap,''),favoriteFilter);q('.hero').after(navigation);
  function updateFavoriteLabel(){favoriteFilter.textContent=`Đã lưu (${favorites.size})`;favoriteFilter.setAttribute('aria-pressed',String(savedOnly));}
  function toggleFavorite(id){if(favorites.has(id))favorites.delete(id);else favorites.add(id);if(!save('hanoi_favorites',[...favorites]))toast('Trình duyệt chưa cho phép lưu trên thiết bị.');updateFavoriteLabel();renderPlaces();}
  updateFavoriteLabel();
  const categories=h('div',null,'category-bar');categories.setAttribute('role','group');categories.setAttribute('aria-label','Loại điểm đến');
  for(const [id,label]of [['all','Tất cả'],['heritage','Di sản & lịch sử'],['museum','Bảo tàng'],['outdoor','Ngoài trời']]){const b=button(label,()=>{category=id;categories.querySelectorAll('button').forEach(e=>{e.classList.toggle('active',e===b);e.setAttribute('aria-pressed',String(e===b));});renderPlaces();},id==='all'?'active':'');b.setAttribute('aria-pressed',String(id==='all'));categories.append(b);}
  q('.discover .filters').before(categories);
  const openLabel=h('label',null,'check'),openFilter=h('input');openFilter.type='checkbox';openFilter.id='open-day';openLabel.append(openFilter,document.createTextNode('Mở ngày đã chọn'));q('.filters').append(openLabel);
  const retry=button('Tải lại danh mục',()=>loadPlaces());q('#places-status').after(retry);retry.style.marginBottom='20px';
  const baseRender=renderPlaces;
  renderPlaces=function(){
    baseRender();
    const filtered=places.filter(p=>fold(p.name+' '+p.district).includes(fold(q('#search').value))&&(!q('#indoor').checked||p.indoor)&&(!q('#free').checked||p.free));
    // The original filter is accent-sensitive. Clear its result and render the matching list through it.
    const originalQuery=q('#search').value;
    if(originalQuery){q('#search').value='';baseRender();q('#search').value=originalQuery;}
    let count=0;
    const cards=[...q('#places').children];
    const basePlaces=originalQuery?places.filter(p=>(!q('#indoor').checked||p.indoor)&&(!q('#free').checked||p.free)):filtered;
    cards.forEach((card,i)=>{
      const p=basePlaces[i];if(!p)return;
      const m=meta[p.id]||{};
      if(!filtered.some(f=>f.id===p.id)||(category!=='all'&&m.category!==category)||(savedOnly&&!favorites.has(p.id))||(openFilter.checked&&!p.open_day)){card.remove();return;}
      count++;
      const img=photo(m.image,p.name,'place-cover');if(img)card.prepend(img);
      card.append(button(favorites.has(p.id)?'♥':'♡',()=>toggleFavorite(p.id),'place-bookmark'));
      const bookmark=card.querySelector('.place-bookmark');bookmark.setAttribute('aria-label',`${favorites.has(p.id)?'Bỏ lưu':'Lưu'} ${p.name}`);bookmark.setAttribute('aria-pressed',String(favorites.has(p.id)));
      const title=card.querySelector('h3');title.after(h('p',m.description||'Xem thông tin, giờ tham quan và các lưu ý trước khi đi.','place-kicker'));
      const status=h('p',p.open_day?'Mở ngày đã chọn':'Nghỉ ngày đã chọn',`place-status${p.open_day?'':' closed'}`);title.before(status);
      card.querySelector('.place-actions').prepend(button('Xem chi tiết',()=>showPlace(p.id),'detail-open'));
    });
    q('#places-status').textContent=`${count} điểm${savedOnly?' đã lưu':''} · Ngày ${q('#places-date').value.split('-').reverse().join('/')} · Giờ và vé từ HanoiTravelBot`;
    retry.hidden=places.length>0;
    if(!count&&places.length){const empty=h('div',null,'empty-state');empty.append(h('h3',savedOnly?'Chưa có điểm đã lưu phù hợp':'Chưa có điểm đến phù hợp'),h('p','Thử thay đổi từ khóa hoặc bớt bộ lọc để khám phá thêm.'),button('Xóa bộ lọc',()=>{savedOnly=false;category='all';q('#search').value='';q('#indoor').checked=q('#free').checked=openFilter.checked=false;categories.querySelector('button').click();updateFavoriteLabel();}));q('#places').append(empty);}
  };
  ['#search','#indoor','#free','#open-day'].forEach(s=>q(s).oninput=()=>renderPlaces());
  q('#search').placeholder='Tên điểm hoặc khu vực, có thể gõ không dấu';
  const detail=h('dialog',null,'place-dialog');detail.id='place-detail';detail.setAttribute('aria-label','Chi tiết điểm đến');document.body.append(detail);
  function closeOnBackdrop(dialog){dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});}
  closeOnBackdrop(detail);
  async function showPlace(id){
    const currentVersion=++detailVersion;
    detail.replaceChildren(h('p','Đang lấy thông tin điểm đến…','detail-body'));
    if(!detail.open)detail.showModal();
    let p;
    try{p=await api(`/api/place/${encodeURIComponent(id)}?date=${encodeURIComponent(q('#places-date').value)}&lang=vi`);}
    catch(error){if(currentVersion!==detailVersion)return;detail.replaceChildren(h('div',error.message,'detail-body'),button('Thử lại',()=>showPlace(id)),button('Đóng',()=>detail.close()));return;}
    if(currentVersion!==detailVersion||!detail.open)return;
    const m=meta[id]||{},c=p;detail.replaceChildren();
    const image=photo(m.image,p.name,'detail-photo');if(image)detail.append(image);
    const head=h('div',null,'dialog-head');const title=h('div');title.append(h('p',p.district,'eyebrow'),h('h2',p.name));const close=button('×',()=>detail.close());close.setAttribute('aria-label','Đóng chi tiết');head.append(title,close);detail.append(head);
    const body=h('div',null,'detail-body');body.append(h('p',m.description));
    const facts=h('div',null,'detail-facts');for(const [label,value]of [['Giờ ngày đã chọn',p.hours||'Chưa xác nhận'],['Vé tham quan',p.free?'Vào tự do':p.ticket||'Chưa xác nhận'],['Thời gian gợi ý',`${p.visit_minutes||60} phút`]]){const block=h('div');block.append(h('small',label),h('strong',value));facts.append(block);}body.append(facts);
    if(c.address)body.append(h('p',c.address));
    const actions=h('div',null,'detail-actions');actions.append(button(selected.some(s=>s.id===id)?'Đã có trong lịch':'Thêm vào lịch trình',()=>{if(!selected.some(s=>s.id===id)){selected.push(p);renderSelected();persistSelection();renderPlaces();}toast('Địa điểm đã có trong lịch trình của bạn.');}),button(favorites.has(id)?'Bỏ lưu địa điểm':'Lưu địa điểm',()=>{toggleFavorite(id);showPlace(id);}));
    if(c.lat&&c.lon)actions.append(link('Chỉ đường',`https://www.google.com/maps/dir/?api=1&destination=${c.lat},${c.lon}`,'secondary'));
    actions.append(button('Hỏi HanoiBot',()=>{detail.close();ask(`Cho tôi biết về ${p.name} và lưu ý khi tham quan.`);}));body.append(actions);
    const tips=c.tips||[];if(tips.length){body.append(h('h3','Trước khi bạn đến'));const list=h('ul');tips.forEach(t=>list.append(h('li',t)));body.append(list);}
    if(p.notes)body.append(h('p',p.notes));
    if(p.ticket_rules?.length){body.append(h('h3','Vé & điều kiện ưu đãi'));const rules=h('ul');p.ticket_rules.forEach(r=>rules.append(h('li',`${r.label}: ${Number(r.price_vnd)===0?'Miễn phí':Number(r.price_vnd).toLocaleString('vi-VN')+'đ'}${r.proof?' · '+r.proof:''}`)));body.append(rules);}
    if(p.week?.length){body.append(h('h3','Giờ tham quan 7 ngày tới'));const week=h('div',null,'detail-week');p.week.forEach(d=>{const row=h('div');row.append(h('span',`${d.day} · ${d.date.split('-').reverse().slice(0,2).join('/')}`),h('strong',d.hours));week.append(row);});body.append(week);}
    body.append(h('h3','Chọn giờ thoải mái hơn'));
    if(Object.keys(p.crowd||{}).length){const crowd=h('div',null,'crowd-hours');Object.entries(p.crowd).forEach(([hour,level])=>{const item=h('div');item.append(h('small',`${hour}h`),h('strong',`${level}%`));crowd.append(item);});body.append(crowd,h('p','Mức đông dự báo tương đối theo giờ, không phải số người thực tế.','fine'));}
    else body.append(h('p','Chưa có dữ liệu mức đông cho ngày này.','fine'));
    if(c.lat&&c.lon){const map=mapFrame(c.lat,c.lon,p.name);body.append(h('h3','Vị trí trên bản đồ'),map);}
    if(p.nearby?.length){body.append(h('h3','Ghé thêm gần đây'));const nearby=h('div',null,'nearby-list');p.nearby.forEach(n=>nearby.append(button(`${n.name} · khoảng ${Number(n.km).toLocaleString('vi-VN')} km`,()=>showPlace(n.id))));body.append(nearby);}
    const sources=h('div',null,'detail-sources');sources.append(h('strong','Nguồn thông tin'));[...new Set([m.source,...(p.sources||[])].filter(Boolean))].forEach(url=>{try{const u=new URL(url);if(['https:','http:'].includes(u.protocol))sources.append(link(u.hostname,url));}catch{}});sources.append(h('p','Giờ, vé và lưu ý được lấy từ hệ thống theo ngày bạn chọn. Nơi còn thông tin chưa chắc chắn được ghi rõ trong lưu ý.'));body.append(sources);detail.append(body);
  }
  document.querySelectorAll('.destination-mosaic a').forEach(a=>{a.onclick=e=>{e.preventDefault();showPlace(a.dataset.destination||'pho_co');};});
  function mapFrame(lat,lon,name){const frame=h('iframe');frame.title=`Bản đồ ${name}`;frame.loading='lazy';frame.referrerPolicy='no-referrer';const box=[lon-.012,lat-.008,lon+.012,lat+.008].join(',');frame.src=`https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(box)}&layer=mapnik&marker=${lat},${lon}`;return frame;}
  const mapDialog=h('dialog',null,'map-dialog');mapDialog.setAttribute('aria-label','Bản đồ điểm tham quan');document.body.append(mapDialog);closeOnBackdrop(mapDialog);
  function openMap(){mapDialog.replaceChildren();const head=h('div',null,'dialog-head');head.append(h('h2','Một vòng Hà Nội'),button('×',()=>mapDialog.close()));mapDialog.append(head,mapFrame(21.0287,105.842,'trung tâm Hà Nội'),h('p','Chọn một điểm để xem vị trí và chỉ đường. Bản đồ sử dụng OpenStreetMap, cần kết nối mạng.','map-caption'));const list=h('div',null,'map-place-list');places.forEach(p=>list.append(button(p.name,()=>{mapDialog.close();showPlace(p.id);})));mapDialog.append(list);mapDialog.showModal();}
  const food=[
    {name:'Bánh Mì 25',tag:'MỘT BỮA NHẸ Ở PHỐ CỔ',text:'Bánh mì nhiều loại nhân, có lựa chọn chay. Có thể phải xếp hàng vào giờ cao điểm.',price:'Khoảng 30.000–40.000đ/chiếc theo HanoiGO',source:'https://hanoigo.hanoi.gov.vn/restaurant/banh-mi-25-hoan-kiem',image:'assets/hanoigo-3.jpg'},
    {name:'Phở Gia Truyền Bát Đàn',tag:'HƯƠNG VỊ BUỔI SÁNG',text:'Địa chỉ phở trên phố Bát Đàn. Khách xếp hàng, thanh toán và nhận món trước khi tìm chỗ ngồi.',price:'Xem giá tại quán',source:'https://hanoigo.hanoi.gov.vn/restaurant/pho-gia-truyen',image:'assets/hanoigo-4.jpg'},
    {name:'Chả Cá Thăng Long',tag:'MỘT CUỘC HẸN VỚI HÀ THÀNH',text:'Cá ướp nghệ, dùng với thì là, hành hoa, bún và lạc. Xác nhận cơ sở và địa chỉ với quán trước khi đến.',price:'Chưa có giá cụ thể được xác nhận',source:'https://hanoigo.hanoi.gov.vn/restaurant/cha-ca-thang-long',image:'assets/hanoigo-5.jpg'}
  ];
  food.forEach(f=>{const card=h('article',null,'food-card'),img=photo(f.image,f.name);if(img)card.append(img);const copy=h('div',null,'food-copy');copy.append(h('p',f.tag,'eyebrow'),h('h3',f.name),h('p',f.text),h('p',f.price,'food-price'),link('Xem trên HanoiGO',f.source));card.append(copy);q('#food-grid').append(card);});
  async function loadContent(){
    const result=await Promise.allSettled(['food','events','info'].map(type=>api(`/api/content?type=${type}`)));
    if(result[0].status==='fulfilled'&&result[0].value.items?.length){const grid=q('#food-grid');grid.replaceChildren();result[0].value.items.forEach(f=>{const card=h('article',null,'food-card');const match=food.find(item=>fold(item.name)===fold(f.name));const img=photo(match?.image||catalog.food_images?.[f.sources?.[0]],f.name);if(img)card.append(img);const copy=h('div',null,'food-copy');copy.append(h('p',f.dish||f.district||'ẨM THỰC HÀ NỘI','eyebrow'),h('h3',f.name),h('p',f.address||'\u0110\u1ecba ch\u1ec9 c\u1ea7n x\u00e1c nh\u1eadn v\u1edbi qu\u00e1n'),h('p',f.hours||'Giờ mở cửa cần xác nhận'),h('p',f.note||''),h('p',f.price_range||'Giá cần xác nhận tại quán','food-price'));if(f.sources?.[0])copy.append(link('Xem nguồn thông tin',f.sources[0]));card.append(copy);grid.append(card);});}
    for(const [index,type]of [[1,'events'],[2,'info']]){const box=q(`#${type}-content`);box.replaceChildren();if(result[index].status==='fulfilled'&&result[index].value.items?.length){result[index].value.items.forEach(item=>{const card=h('article',null,'content-item');card.append(h('h3',item.name||item.title),h('p',type==='events'?`${item.start} – ${item.end||item.start}`:item.value),h('p',item.venue||item.note||''));if(type==='events'&&item.note)card.append(h('p',item.note));if(item.price)card.append(h('p',item.price));if(item.sources?.[0])card.append(link('Xem thông tin gốc',item.sources[0]));box.append(card);});}else box.append(h('p',type==='events'?'Chưa có sự kiện đã xác minh trong danh mục. Xem lịch mới nhất trên HanoiGO bên dưới.':'Xem hướng dẫn di chuyển và lưu ý tại cẩm nang bên dưới.','source-note'));}
  }
  q('#guide-weather').onclick=()=>q('#weather-dialog').showModal();
  function persistSelection(){save('hanoi_selected',selected.map(p=>p.id));}
  const originalSelected=renderSelected;renderSelected=function(){originalSelected();persistSelection();};
  const family=h('details',null,'family-fields');family.innerHTML='<summary>Trẻ em & học sinh, sinh viên</summary><div class="form-grid"><label>Số trẻ em<input name="children" type="number" min="0" value="0"></label><label>Tuổi trẻ em (cách bằng dấu phẩy)<input name="child_ages" placeholder="Ví dụ: 5, 12"></label><label>Số người có thẻ HSSV<input name="students" type="number" min="0" value="0"></label><label>Loại thẻ<select name="student_level"><option value="sv">Sinh viên</option><option value="hs">Học sinh</option></select></label></div><p class="fine">Số người ở trên là tổng cả nhóm. Mang giấy tờ để được áp dụng ưu đãi; giá theo chiều cao cần xác nhận tại quầy.</p>';
  q('#plan-form .form-grid').append(family);
  function download(text,name,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=h('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function renderPlan(data,date,isRestored=false){
    const result=q('#plan-result');result.hidden=false;result.replaceChildren();lastPlan={data,date};
    const tools=h('div',null,'plan-tools');tools.append(button('Lưu trên thiết bị',()=>toast(save('hanoi_last_plan',lastPlan)?'Đã lưu lịch trình trên thiết bị này.':'Không lưu được, bạn có thể tải lịch trình.')),button('Tải lịch trình',()=>download(data.text||data.answer||'',`hanoi-${date}.txt`,'text/plain;charset=utf-8')),button('In / Lưu PDF',()=>window.print()));
    if(!isRestored)tools.append(button('Chỉnh lịch cùng HanoiBot',()=>{openChat();q('#chat-input').value='Tôi muốn sửa lịch vừa tạo: ';q('#chat-input').focus();}));
    result.append(h('h2',`Lịch trình · ${date.split('-').reverse().join('/')}`),tools);
    if(isRestored)result.append(h('p','Lịch đã lưu trên thiết bị. Dữ liệu chưa được kiểm lại cho lần xem này; để chỉnh qua chatbot, hãy tạo lại lịch trong phiên hiện tại.','fine'));
    const items=Array.isArray(data.items)?data.items:[];
    if(items.length){const timeline=h('ol',null,'plan-timeline');items.forEach(it=>{const li=h('li'),copy=h('div');li.append(h('time',`${it.start||''}–${it.end||''}`));const p=places.find(p=>p.id===it.poi_id);copy.append(h('h3',p?.name||catalog.places[it.poi_id]?.name||it.why||'Nghỉ, ăn nhẹ'));if(it.travel_min)copy.append(h('p',`Di chuyển khoảng ${it.travel_min} phút`));if(it.why&&it.why!==copy.firstChild.textContent)copy.append(h('p',it.why));li.append(copy);timeline.append(li);});result.append(timeline);}
    const text=h('div',data.text||data.answer||'Chưa có chi tiết lịch trình.','plan-result-text');result.append(text);
    if(data.ok===false)result.prepend(h('p','Lịch còn ràng buộc chưa đáp ứng. Hãy đọc cảnh báo và điều chỉnh yêu cầu.','planner-notice'));
  }
  q('#plan-form').onsubmit=async e=>{
    e.preventDefault();const manual=e.submitter?.value==='manual',form=e.target,result=q('#plan-result');result.hidden=false;
    if(manual&&!selected.length){result.textContent='Hãy thêm địa điểm ở mục Khám phá trước khi xếp lịch.';return;}
    const body=Object.fromEntries(new FormData(form));for(const k of ['people','budget_vnd','max_walk_km','children','students'])body[k]=Number(body[k]);
    const ages=String(body.child_ages).trim();body.child_ages=ages?ages.split(/[,;\s]+/).map(Number):[];
    if(body.children+body.students>body.people||body.child_ages.length>body.children||body.child_ages.some(a=>!Number.isInteger(a)||a<0||a>17)){result.textContent='Kiểm tra tổng số người, số trẻ em và tuổi (0–17). Số trẻ em và HSSV không được vượt tổng cả nhóm.';return;}
    if(body.start>=body.end){result.textContent='Giờ kết thúc cần sau giờ bắt đầu.';return;}
    body.lang='vi';if(manual)body.stops=selected.map(p=>({kind:'visit',poi_id:p.id,minutes:p.visit_minutes||60}));else body.must_include=selected.map(p=>p.id);
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);result.textContent=manual?'Đang tính giờ tham quan và kiểm tra lịch…':'Đang xây dựng lịch trình phù hợp. Có thể mất khoảng 15–30 giây…';
    try{const data=await api(manual?'/api/schedule':'/api/plan',body);renderPlan(data,body.date);save('hanoi_draft',body);result.scrollIntoView({block:'start',behavior:'smooth'});}catch(error){result.textContent=error.message;}finally{buttons.forEach(b=>b.disabled=false);}
  };
  const restore=button('Mở lịch đã lưu',()=>{const stored=read('hanoi_last_plan',null);if(!stored?.data||typeof stored.date!=='string'){toast('Chưa có lịch đã lưu trên thiết bị này.');return;}renderPlan(stored.data,stored.date,true);q('#plan-result').scrollIntoView();});q('.planner-title').append(restore);
  fetch('catalog.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{catalog=d;for(const[id,extra]of Object.entries(d.places||{})){if(meta[id]&&extra.image)meta[id].image=extra.image;if(meta[id]&&extra.source)meta[id].source=extra.source;}renderPlaces();}).catch(()=>toast('Một số thông tin bổ sung chưa tải được. Giờ và vé vẫn lấy từ hệ thống.')).finally(loadContent);
  const restoreSelection=()=>{if(!places.length)return;const ids=read('hanoi_selected',[]);if(Array.isArray(ids)&&!selected.length){selected=ids.map(id=>places.find(p=>p.id===id)).filter(Boolean);renderSelected();renderPlaces();}};
  const selectionObserver=new MutationObserver(()=>{if(places.length){selectionObserver.disconnect();restoreSelection();}});selectionObserver.observe(q('#places'),{childList:true});if(places.length){selectionObserver.disconnect();restoreSelection();renderPlaces();}
})();
