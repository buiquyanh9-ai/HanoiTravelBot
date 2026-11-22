(()=>{
  const discover=document.querySelector('#discover');
  const feature=document.createElement('div');feature.className='discovery-stories';
  const stories=[
    {tag:'DẤU ẤN NGÀN NĂM',name:'Chạm vào\ndi sản.',text:'Từ sân Văn Miếu đến những lớp thành xưa.',image:'assets/temple.jpg',category:1,label:'Khám phá di sản'},
    {tag:'MỘT KHOẢNG THẢNH THƠI',name:'Dạo một vòng\nHồ Gươm.',text:'Mặt nước, bóng cây và nhịp sống Hà Nội.',image:'assets/lake.jpg',category:3,label:'Đi dạo ngoài trời'},
    {tag:'THEO DẤU CÂU CHUYỆN',name:'Hẹn với\nlịch sử.',text:'Dành thời gian cho những điều còn lưu lại.',image:'assets/history-museum.jpg',category:2,label:'Ghé thăm bảo tàng'}
  ];
  stories.forEach(story=>{
    const card=document.createElement('article');card.className='discovery-story';
    const img=document.createElement('img');img.src=story.image;img.alt='';img.loading='lazy';
    const copy=document.createElement('div');copy.className='story-copy';
    const tag=document.createElement('p');tag.className='eyebrow';tag.textContent=story.tag;
    const title=document.createElement('h3');title.textContent=story.name;
    const text=document.createElement('p');text.textContent=story.text;
    const button=document.createElement('button');button.type='button';button.textContent=story.label;
    button.onclick=()=>{document.querySelectorAll('.category-bar button')[story.category]?.click();document.querySelector('.category-bar').scrollIntoView({behavior:'smooth',block:'start'});};
    copy.append(tag,title,text,button);card.append(img,copy);feature.append(card);
  });
  discover.querySelector('.section-heading').after(feature);
  const foodHeading=document.querySelector('#food .section-heading');
  const controls=document.createElement('div');controls.className='food-controls';controls.setAttribute('aria-label','Cuộn danh sách quán ăn');
  for(const [label,direction]of [['Trước',-1],['Tiếp',1]]){
    const button=document.createElement('button');button.type='button';button.textContent=label;
    button.onclick=()=>{const grid=document.querySelector('#food-grid');grid.scrollBy({left:direction*Math.min(grid.clientWidth*.8,460),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});};controls.append(button);
  }
  foodHeading.append(controls);
  document.querySelectorAll('#guide .guide-grid article').forEach((article,i)=>article.classList.add('guide-tone-'+i));
})();
