'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escapeHTML;
let state = null, csrf = '', page = 'dashboard', filter = 'all', accountFilter = '', toastTimer;
const selection = new Set(), sourceSelection = new Set();
const pageInfo = {
  dashboard:['대시보드','YOUR AUTOMATION, AT A GLANCE','콘텐츠 발견부터 마지막 댓글까지, 모든 작업을 한곳에서 관리하세요.'],
  discover:['콘텐츠 탐색','DISCOVER WHAT’S NEXT','샤오홍슈와 Threads에서 다음 콘텐츠의 영감을 발견하세요.'],
  library:['보관함','YOUR CONTENT LIBRARY','관심 링크와 원본 내용을 모아 콘텐츠의 출처를 연결하세요.'],
  out:['결과물 · OUT','READY FOR YOUR NEXT POST','모든 영상과 사진을 하나의 폴더에서 작업 순서대로 관리하세요.'],
  jobs:['예약 관리','YOUR PUBLISHING SCHEDULE','계정별 예약과 마지막 상품 댓글까지의 처리 상태를 확인하세요.'],
  presets:['편집 · 프롬프트','SET YOUR CREATIVE DIRECTION','한 번 저장한 제작 기준을 다음 예약 작업에 적용합니다.'],
  accounts:['계정 관리','CONNECTED ACCOUNTS','여러 Threads 계정을 각각의 인증정보로 관리하세요.']
};
const statusLabel = {attention:'확인 필요',queued:'예약 대기',running:'진행 중',comment_wait:'댓글 대기',complete:'완료',cancelled:'취소됨'};
const statusTag = s => `<span class="tag ${s === 'complete' ? 'green' : s === 'attention' ? 'orange' : ''}">${e(statusLabel[s] || s)}</span>`;
const dateLabel = d => new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(d));
const accountName = id => state.accounts.find(a => a.id === id)?.name || '알 수 없는 계정';
const option = (value, text, selected = false) => `<option value="${e(value)}" ${selected?'selected':''}>${e(text)}</option>`;
const empty = (icon, title, description, action = '') => `<div class="empty"><div class="empty-icon">${icon}</div><h3>${e(title)}</h3><p>${e(description)}</p>${action}</div>`;
const button = (action, label, cls = 'secondary', id = '') => `<button type="button" class="${cls}" data-action="${action}" data-id="${e(id)}">${e(label)}</button>`;

function toast(message) {
  $('#toast').textContent = message; $('#toast').classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 5500);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청에 실패했습니다.');
  return result;
}
async function refresh(render = true) {
  state = await api('/api/state');
  $('#sync-status').textContent = '● 저장 상태 동기화 · ' + new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'});
  renderStats();
  if (render) renderPage();
}
function filteredJobs() { return state.jobs.filter(j => !accountFilter || j.account_id === accountFilter); }
function renderStats() {
  const jobs = filteredJobs();
  const metrics = [
    ['all','전체 작업',jobs.length,'◫','생성한 모든 작업',''],
    ['queued','예약 대기',jobs.filter(j=>j.status==='queued').length,'◷','실행을 기다리는 작업',''],
    ['running','진행 중',jobs.filter(j=>['running','comment_wait'].includes(j.status)).length,'↻','편집부터 댓글 대기까지',''],
    ['complete','완료',jobs.filter(j=>j.status==='complete').length,'✓','게시 · 상품 댓글 완료','green'],
    ['attention','확인 필요',jobs.filter(j=>j.status==='attention').length,'!','연결 · 실패 확인','orange']
  ];
  $('#stats').innerHTML = metrics.map(([key,name,count,icon,note,color])=>`<button class="stat ${color}" data-action="filter" data-id="${key}" aria-label="${name} ${count}건 보기"><div class="stat-top">${name}<span class="stat-icon">${icon}</span></div><span class="stat-value">${count.toLocaleString()}</span><span class="stat-unit">건</span><div class="stat-note">${note}</div></button>`).join('');
}
function jobsTable(jobs, compact = false) {
  if (!jobs.length) return empty('◷','아직 표시할 작업이 없어요','계정과 예약 시간을 선택하면 이곳에서 작업의 흐름을 확인할 수 있습니다.',button('new-job','＋ 첫 예약 만들기','secondary small-button'));
  return `<div class="table-wrap"><table><thead><tr><th>계정 / 작업</th><th>예약 시간 · KST</th><th>상태</th>${compact?'':'<th>처리 내용</th>'}<th></th></tr></thead><tbody>${jobs.map(j=>`<tr><td><strong>${e(accountName(j.account_id))}</strong><small>#${j.id.slice(0,7)}</small></td><td>${dateLabel(j.scheduled)}</td><td>${statusTag(j.status)}</td>${compact?'':`<td>${e(j.detail)}</td>`}<td>${button('job-detail','상세','text-button',j.id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function readiness() {
  return `<section class="panel"><div class="panel-head"><div><h2>자동화 연결 상태</h2><p>실제 실행에 필요한 준비를 확인하세요</p></div><span class="tag">${state.capabilities.filter(c=>c.ready).length} / ${state.capabilities.length}</span></div><div class="readiness">${state.capabilities.map(c=>`<div class="connection"><span class="connection-icon ${c.ready?'ready':''}">${c.ready?'✓':'·'}</span><div><strong>${e(c.name)}</strong><small>${e(c.detail)}</small></div><span class="tag ${c.ready?'green':''}">${c.ready?'준비':'미연결'}</span></div>`).join('')}</div></section>`;
}
function renderDashboard() {
  const jobs = filteredJobs();
  return `<section class="hero"><div><div class="eyebrow">LESS REPETITION. MORE CREATION.</div><h2>계정과 시간, 두 가지만 선택하세요.</h2><p>수집 → 편집 → 게시 → 15분 후 상품 댓글.<br>연결을 완료하면 저장한 프리셋으로 이어질 자동화 흐름입니다.</p></div><div class="hero-steps"><div><div class="step-circle">◎</div><div class="step-label">계정 선택</div></div><span class="arrow">→</span><div><div class="step-circle">◷</div><div class="step-label">시간 선택</div></div><span class="arrow">→</span><div><div class="step-circle">✧</div><div class="step-label">자동화</div></div></div></section>
  <div class="toolbar"><span class="muted">나의 작업 현황</span>${accountSelector()}</div>
  <div class="grid-main"><div><section class="panel"><div class="panel-head"><div><h2>다가오는 예약</h2><p>저장한 예약 시간 순서로 표시됩니다</p></div>${button('all-jobs','전체 보기 →','text-button')}</div>${jobsTable(jobs.filter(j=>!['cancelled','complete'].includes(j.status)).sort((a,b)=>a.scheduled.localeCompare(b.scheduled)).slice(0,4),true)}</section><section class="panel"><div class="panel-head"><h2>최근 작업 결과</h2><span class="tag">최근 5건</span></div>${jobs.length?jobsTable(jobs.slice(0,5),true):empty('✓','첫 번째 작업을 기다리고 있어요','게시가 끝난 뒤 상품 링크 댓글까지 완료되면 완료 건수에 반영됩니다.')}</section></div><div>${readiness()}<section class="panel"><div class="panel-head"><h2>기본 제작 프리셋</h2>${button('presets','설정 →','text-button')}</div><div class="panel-body"><div class="toolbar"><span class="muted">영상 비율</span><strong>${e(state.preset.ratio)}</strong></div><div class="toolbar"><span class="muted">콘텐츠 주제</span><span>${categoryName(state.preset.category)}</span></div><div class="toolbar"><span class="muted">상품 댓글</span><span class="tag green">게시 성공 + 15분</span></div><div class="info-strip">본문에는 링크 없이.<br>상품 링크는 별도의 댓글로.</div></div></section></div></div>
  <section class="panel"><div class="panel-head"><h2>한눈에 보는 자동화 흐름</h2><span class="muted">마지막 댓글까지가 하나의 작업입니다</span></div><div class="flow">${[['콘텐츠 수집','급상승 원본 탐색'],['OUT 저장','순번으로 파일 보관'],['자동 편집','원본 분석 · 프리셋'],['예약 게시','본문에는 링크 제외'],['상품 댓글','게시 성공 15분 후']].map(([name,note],i)=>`<div class="flow-item"><span class="flow-number">0${i+1}</span><div class="flow-name">${name}</div><div class="flow-note">${note}</div></div>`).join('')}</div></section>`;
}
function categoryName(value) {return {food:'음식',fashion:'패션',all:'구분 없음'}[value] || value;}
function accountSelector() {return `<select aria-label="계정별 작업 필터" class="account-filter">${option('','전체 계정',!accountFilter)}${state.accounts.map(a=>option(a.id,a.name,accountFilter===a.id)).join('')}</select>`;}
function renderLibrary() {
  return `<div class="toolbar"><div class="toolbar-group"><label class="checkbox-label"><input type="checkbox" id="all-sources" ${state.sources.length&&sourceSelection.size===state.sources.length?'checked':''}>전체 선택</label><span class="muted">${state.sources.length}개의 관심 링크 · ${sourceSelection.size}개 선택</span></div>${button('source','＋ 관심 링크 보관','primary')}</div>${sourceSelection.size?`<div class="selection-bar">${sourceSelection.size}개 선택됨 ${button('clear-sources','선택 해제','text-button')}${button('download-info','선택한 원본 다운로드','secondary small-button')}</div>`:''}${state.sources.length?`<div class="cards">${state.sources.map(s=>`<article class="content-card"><div class="panel-body"><div class="card-top"><label class="checkbox-label"><input type="checkbox" data-source-check="${s.id}" ${sourceSelection.has(s.id)?'checked':''}>${e(s.platform)}</label><span class="tag">${categoryName(s.category)}</span></div><h3>${e(s.title)}</h3><p>${e(s.note.slice(0,140))||'저장된 원문 메모가 없습니다.'}</p><a class="source-url" href="${e(s.url)}" target="_blank" rel="noopener noreferrer">${e(s.url)}</a><div class="actions">${button('out','OUT 파일 연결 →','text-button')}</div></div></article>`).join('')}</div>`:`<section class="panel">${empty('▤','발견한 아이디어를 보관하세요','샤오홍슈·Threads 링크와 원문을 저장해 파일 및 분석의 출처로 연결할 수 있습니다.',button('source','첫 관심 링크 보관','secondary'))}</section>`}`;
}
function renderOut() {
  const groups = [...new Set(state.media.filter(m=>m.kind==='video'&&!state.media.some(x=>x.group_no===m.group_no&&x.kind==='image')).map(m=>m.group_no))].sort((a,b)=>a-b);
  return `<div class="toolbar"><div class="toolbar-group"><label class="checkbox-label"><input type="checkbox" id="all-media" ${state.media.length&&selection.size===state.media.length?'checked':''}>전체 선택</label><span class="muted">${state.media.length}개 파일 · ${selection.size}개 선택</span></div>${button('new-job','선택한 파일로 예약','primary')}</div>
  <section class="panel"><div class="panel-body"><div class="upload-zone"><strong>로컬 파일을 OUT으로 가져오기</strong><p>영상 1.mp4 · 연결 사진 1-1.jpg. 하위 폴더 없이 저장합니다. 파일당 최대 300MB.</p><div class="upload-fields"><label>원본 보관 링크<select id="import-source">${option('','원본 연결 없이 가져오기')}${state.sources.map(s=>option(s.id,s.title)).join('')}</select></label><label>사진을 연결할 영상 번호<select id="import-group">${option('','새 작업 번호')}${groups.map(n=>option(n,`${n}번 영상에 사진 연결`)).join('')}</select></label></div><label>영상 또는 사진<input id="import-file" type="file" accept=".mp4,.mov,.webm,.jpg,.jpeg,.png,.webp"></label><p>영상과 사진 한 쌍은 영상을 먼저 가져온 뒤, 해당 번호에 사진을 연결하세요.</p></div></div></section>
  ${selection.size?`<div class="selection-bar">${selection.size}개 파일 선택됨 ${button('clear-selection','전체 해제','text-button')}</div>`:''}
  ${state.media.length?`<div class="cards">${state.media.slice().sort((a,b)=>a.group_no-b.group_no||a.name.localeCompare(b.name)).map(m=>`<article class="content-card"><div class="media-preview"><input aria-label="${e(m.name)} 선택" type="checkbox" data-media-check="${m.id}" ${selection.has(m.id)?'checked':''}>${m.kind==='video'?`<video src="/media/${e(m.name)}" controls preload="metadata"></video>`:`<img src="/media/${e(m.name)}" alt="${e(m.name)}" loading="lazy">`}</div><div class="panel-body"><div class="card-top"><strong class="file-name">${e(m.name)}</strong><span class="tag">${m.kind==='video'?'영상':'사진'}</span></div><p>${(m.bytes/1024/1024).toFixed(1)} MB · 작업 ${m.group_no}<br>${e(state.sources.find(s=>s.id===m.source_id)?.title||'연결된 원본 없음')}</p><a href="/media/${e(m.name)}" download="${e(m.name)}">파일 다운로드 ↓</a></div></article>`).join('')}</div>`:`<section class="panel">${empty('▣','OUT 폴더가 비어 있어요','원본 미디어를 가져오면 작업 순번에 맞춰 이곳에 표시됩니다.')}</section>`}`;
}
function renderPresets() {
  const p=state.preset;
  return `<form id="preset-form"><div class="toolbar"><span class="muted">저장 후 새로 만드는 작업부터 적용됩니다.</span><button class="primary">프리셋 저장</button></div><div class="preset-grid"><div><section class="panel"><div class="panel-head"><h2>영상 레이아웃</h2><span class="tag">기본 프리셋</span></div><div class="panel-body"><div class="form-grid"><label>출력 화면 비율<select name="ratio">${['16:9','4:3','1:1','9:16'].map(x=>option(x,x,p.ratio===x)).join('')}</select></label><label>콘텐츠 카테고리<select name="category">${['food','fashion','all'].map(x=>option(x,categoryName(x),p.category===x)).join('')}</select></label><label>폰트<select name="font">${['Apple SD Gothic Neo','Arial','sans-serif'].map(x=>option(x,x,p.font===x)).join('')}</select></label><label>글자 크기 · px<input name="fontSize" type="number" min="12" max="100" value="${p.fontSize}" required></label><label>한 줄 최대 글자 수<input name="maxChars" type="number" min="5" max="60" value="${p.maxChars}" required></label><label>상단·하단 여백 · 각각 %<input name="padding" type="number" min="10" max="35" value="${p.padding}" required></label></div><label class="checkbox-label"><input name="bold" type="checkbox" ${p.bold?'checked':''}>굵은 글씨 적용</label></div></section><section class="panel"><div class="panel-head"><h2>원본 분석 · 자동 편집 지침</h2></div><div class="panel-body"><label>영상 텍스트 생성 및 삽입 프롬프트<textarea name="editPrompt" rows="6" required>${e(p.editPrompt)}</textarea></label><label>게시물 본문 프롬프트 · 링크 제외<textarea name="bodyPrompt" rows="4" required>${e(p.bodyPrompt)}</textarea></label><label>상품 댓글 프롬프트<textarea name="commentPrompt" rows="4" required>${e(p.commentPrompt)}</textarea></label><label>제휴 관계 고지 문구<textarea name="disclosure" rows="3" required>${e(p.disclosure)}</textarea></label></div></section></div><div><section class="panel"><div class="panel-head"><h2>레이아웃 미리보기</h2><span class="tag">예시 화면</span></div><div class="preset-preview"><div class="video-frame" id="video-frame"><div class="video-text" id="preview-top">원본에서 발견한 작은 아이디어</div><div class="video-scene">ORIGINAL VIDEO</div><div class="video-text" id="preview-bottom">핵심 특징이 이곳에 표시됩니다</div></div></div><div class="preview-caption">배치 예시이며 실제 렌더링 결과가 아닙니다. 원본 영상은 왜곡 없이 여백 안에 맞추는 방식으로 구현 예정입니다.</div></section><div class="notice">현재 프리셋 저장과 레이아웃 미리보기를 지원합니다. AI 원고 생성·영상 렌더링은 아직 연결되지 않았습니다.</div></div></div></form>`;
}
function renderAccounts() {
  return `<div class="toolbar"><span class="muted">${state.accounts.length}개 등록 · ${state.accounts.filter(a=>a.connected).length}개 인증 확인</span>${button('account','＋ 계정 연결','primary')}</div><div class="info-strip">계정마다 액세스 토큰을 따로 저장합니다. 토큰은 화면·일반 데이터 파일·브라우저 저장소로 다시 전달되지 않습니다.</div>${state.accounts.length?`<div class="cards">${state.accounts.map(a=>`<article class="content-card"><div class="panel-body"><div class="card-top"><span class="account-icon">@</span><span class="tag ${a.connected?'green':'orange'}">${a.connected?'인증 확인됨':'연결 확인 필요'}</span></div><h3>${e(a.name)}</h3><p>${a.username?'@'+e(a.username):'아직 실제 계정 정보를 확인하지 않았습니다.'}</p><div class="account-secret">${a.has_token?'•••• •••• ••••':'미입력'}</div><p>${a.verified_at?'마지막 인증 확인 '+dateLabel(a.verified_at):'저장 위치 · macOS 키체인'}<br>인증 확인은 게시 권한 전체의 검증을 의미하지 않습니다.</p><div class="actions">${button('verify','연결 확인','secondary small-button',a.id)}${button('account','수정','text-button',a.id)}</div></div></article>`).join('')}</div>`:`<section class="panel">${empty('◎','첫 Threads 계정을 연결하세요','계정별 입력창에서 토큰을 한 번 저장하면 재실행 후에도 사용할 수 있습니다. 만료·철회 시에는 재인증이 필요합니다.',button('account','계정 연결','secondary'))}</section>`}`;
}
function renderPage() {
  const [title,eyebrow,desc] = pageInfo[page];
  $('#page-title').textContent=title; $('#breadcrumb').textContent=title;
  $('#eyebrow').textContent=eyebrow; $('#page-description').textContent=desc;
  $$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  let html='';
  if(page==='dashboard') html=renderDashboard();
  if(page==='library') html=renderLibrary();
  if(page==='out') html=renderOut();
  if(page==='presets') html=renderPresets();
  if(page==='accounts') html=renderAccounts();
  if(page==='jobs') html=`<div class="toolbar"><div class="toolbar-group">${accountSelector()}<select id="status-filter" aria-label="작업 상태 필터">${option('all','모든 상태',filter==='all')}${['queued','running','complete','attention','cancelled'].map(s=>option(s,statusLabel[s],filter===s)).join('')}</select></div><span class="muted">취소 작업도 전체 건수에 포함됩니다.</span></div><section class="panel">${jobsTable(filteredJobs().filter(j=>filter==='all'||j.status===filter||(filter==='running'&&j.status==='comment_wait')))}</section>`;
  if(page==='discover') html=`<div class="grid-main"><section class="panel"><div class="panel-head"><h2>급상승 콘텐츠 탐색</h2><span class="tag orange">수집 서비스 미연결</span></div>${empty('⌕','콘텐츠 수집 연결이 필요합니다','샤오홍슈 음식·패션과 Threads 콘텐츠를 수집할 서비스가 정해지면 실제 데이터로 검색합니다. 조회수나 급상승 순위를 임의로 표시하지 않습니다.',button('source','관심 링크 먼저 보관하기','secondary'))}<div class="panel-body"><div class="info-strip">급상승 판단에는 여러 시점의 실제 반응 기록이 필요합니다. 접근할 수 없는 조회수는 다른 지표와 구분합니다.</div></div></section>${readiness()}</div>`;
  $('#content').innerHTML=html;
  if(page==='presets') updatePreview();
}
function navigate(next) {page=next; location.hash=next; renderPage(); window.scrollTo({top:0,behavior:'smooth'});}
function openJob() {
  if(!state.accounts.length) {toast('먼저 게시할 계정을 등록하세요.');navigate('accounts');return;}
  const form=$('#job-form');form.reset();
  $('#job-account').innerHTML=state.accounts.map(a=>option(a.id,a.name)).join('');
  $('#job-hour').innerHTML=Array.from({length:24},(_,i)=>option(String(i).padStart(2,'0'),String(i).padStart(2,'0')+':00')).join('');
  const tomorrow=new Date(Date.now()+86400000);
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(tomorrow);
  form.elements.date.value=day; form.elements.hour.value='09';
  $('#job-files').innerHTML=state.media.length?state.media.map(m=>`<label class="file-check"><input type="checkbox" name="media" value="${m.id}" ${selection.has(m.id)?'checked':''}>${e(m.name)}</label>`).join(''):'<p class="muted">미선택 시 자동 수집이 필요합니다.</p>';
  $('#job-dialog').showModal();
}
function openAccount(id) {
  const form=$('#account-form');form.reset();const a=state.accounts.find(x=>x.id===id);
  form.elements.id.value=a?.id||'';form.elements.name.value=a?.name||'';
  form.elements.token.required=!a;$('#account-dialog-title').textContent=a?'계정 인증정보 수정':'계정 연결';
  $('#account-dialog').showModal();
}
function showJob(id) {
  const j=state.jobs.find(x=>x.id===id);
  $('#job-detail').innerHTML=`<dl class="detail-list"><dt>계정 / 예약 시간</dt><dd>${e(accountName(j.account_id))} · ${dateLabel(j.scheduled)} (KST)</dd><dt>상태</dt><dd>${statusTag(j.status)}<br>${e(j.detail)}</dd><dt>연결한 파일</dt><dd>${j.media_ids.map(id=>e(state.media.find(m=>m.id===id)?.name||id)).join(', ')||'자동 수집 필요'}</dd><dt>게시물 본문</dt><dd>${e(j.body)||'AI 생성 대기'}</dd><dt>상품 링크 댓글</dt><dd>${e(j.comment)||'상품 정보 및 파트너스 링크 연결 필요'}</dd><dt>댓글 실행 시각</dt><dd>${j.comment_due_at?dateLabel(j.comment_due_at):'게시 성공 후 15분 · 아직 게시되지 않음'}</dd><dt>저장 당시 프리셋</dt><dd>${e(j.preset.ratio)} · ${e(j.preset.font)} · ${j.preset.fontSize}px<br>${e(j.preset.editPrompt)}</dd></dl>${!['complete','cancelled'].includes(j.status)?`<div class="dialog-actions">${button('cancel','예약 작업 취소','secondary danger-button',j.id)}</div>`:''}`;
  $('#detail-dialog').showModal();
}
function updatePreview() {
  const f=$('#preset-form');if(!f)return;
  const frame=$('#video-frame');frame.style.aspectRatio=f.elements.ratio.value.replace(':',' / ');
  frame.style.fontFamily=f.elements.font.value;
  for(const [selector,text] of [['#preview-top','원본에서 발견한 작은 아이디어'],['#preview-bottom','핵심 특징이 이곳에 표시됩니다']]){
    const el=$(selector);el.style.flexBasis=Number(f.elements.padding.value)+'%';
    el.style.fontWeight=f.elements.bold.checked?'700':'400';
    el.style.fontSize=Math.min(30,Math.max(8,Number(f.elements.fontSize.value)*0.35))+'px';
    el.textContent=text.slice(0,Number(f.elements.maxChars.value));
  }
}
document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-page]');if(nav){navigate(nav.dataset.page);return;}
  const close=event.target.closest('.close-dialog');if(close){close.closest('dialog').close();return;}
  const b=event.target.closest('[data-action]');if(!b)return;
  const action=b.dataset.action,id=b.dataset.id;
  try {
    if(action==='new-job')openJob();
    else if(action==='filter'){filter=id;navigate('jobs');}
    else if(action==='all-jobs'){filter='all';navigate('jobs');}
    else if(['presets','out'].includes(action))navigate(action);
    else if(action==='source'){$('#source-form').reset();$('#source-dialog').showModal();}
    else if(action==='account')openAccount(id);
    else if(action==='job-detail')showJob(id);
    else if(action==='clear-selection'){selection.clear();renderPage();}
    else if(action==='clear-sources'){sourceSelection.clear();renderPage();}
    else if(action==='download-info')toast('링크에서 미디어를 가져오는 수집 서비스가 아직 연결되지 않았습니다. OUT에서 로컬 파일을 가져올 수 있습니다.');
    else if(action==='verify'){b.disabled=true;b.textContent='확인 중…';const r=await api('/api/verify',{id});toast('@'+r.username+' 인증 확인 완료');await refresh();}
    else if(action==='cancel'){await api('/api/cancel',{id});$('#detail-dialog').close();await refresh();toast('예약 작업을 취소했습니다.');}
  } catch(error){toast(error.message);b.disabled=false;if(action==='verify')b.textContent='연결 확인';}
});
document.addEventListener('change',async event=>{
  const t=event.target;
  if(t.matches('.account-filter')){accountFilter=t.value;renderStats();renderPage();}
  if(t.id==='status-filter'){filter=t.value;renderPage();}
  if(t.dataset.mediaCheck){t.checked?selection.add(t.dataset.mediaCheck):selection.delete(t.dataset.mediaCheck);renderPage();}
  if(t.dataset.sourceCheck){t.checked?sourceSelection.add(t.dataset.sourceCheck):sourceSelection.delete(t.dataset.sourceCheck);renderPage();}
  if(t.id==='all-media'){selection.clear();if(t.checked)state.media.forEach(m=>selection.add(m.id));renderPage();}
  if(t.id==='all-sources'){sourceSelection.clear();if(t.checked)state.sources.forEach(s=>sourceSelection.add(s.id));renderPage();}
  if(t.id==='import-file'&&t.files.length){
    const file=t.files[0];t.disabled=true;
    try{
      if(file.size>300*1024*1024)throw new Error('파일은 300MB 이하여야 합니다.');
      const q=new URLSearchParams({name:file.name,source:$('#import-source').value,group:$('#import-group').value});
      toast('OUT 폴더에 파일을 저장하고 있습니다…');
      const response=await fetch('/api/import?'+q,{method:'POST',headers:{'X-CSRF-Token':csrf},body:file});
      const r=await response.json();if(!response.ok)throw new Error(r.error);
      await refresh();toast(r.name+' 저장 완료');
    }catch(error){toast(error.message);t.disabled=false;t.value='';}
  }
});
document.addEventListener('input',event=>{if(event.target.closest('#preset-form'))updatePreview();});
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,b=form.querySelector('button[type=submit],button.primary:not([type=button])');
  if(b)b.disabled=true;
  const data=Object.fromEntries(new FormData(form));
  try{
    if(form.id==='account-form'){
      await api('/api/accounts',data);form.elements.token.value='';$('#account-dialog').close();toast('계정을 저장했습니다. 연결 확인을 눌러 실제 인증을 확인하세요.');
    } else if(form.id==='source-form'){
      await api('/api/sources',data);$('#source-dialog').close();toast('관심 링크를 보관했습니다.');
    } else if(form.id==='preset-form'){
      data.bold=form.elements.bold.checked;await api('/api/preset',data);toast('프리셋 저장 완료 · 새 예약부터 적용됩니다.');
    } else if(form.id==='job-form'){
      await api('/api/jobs',{account_id:data.account_id,scheduled:`${data.date}T${data.hour}:00:00+09:00`,body:data.body,comment:data.comment,media_ids:$$('input[name=media]:checked',form).map(el=>el.value)});
      $('#job-dialog').close();page='jobs';filter='all';toast('예약 설정을 저장했습니다. 자동 실행 연결을 먼저 완료해야 합니다.');
    }
    await refresh();
  }catch(error){toast(error.message);}finally{if(b)b.disabled=false;}
});
$('#new-job').addEventListener('click',openJob);
window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(pageInfo[next]&&next!==page){page=next;renderPage();}});
async function start(){
  try{csrf=(await api('/api/session')).csrf;page=pageInfo[location.hash.slice(1)]?location.hash.slice(1):'dashboard';await refresh();}
  catch(error){$('#content').innerHTML=empty('!','로컬 서버에 연결하지 못했습니다','python3 server.py 실행 후 http://127.0.0.1:8765 로 접속하세요.');$('#sync-status').textContent='연결 실패';}
}
start();
setInterval(async()=>{if(document.hidden||!state)return;try{await refresh(['dashboard','jobs'].includes(page)&&!$('dialog[open]'));}catch(error){$('#sync-status').textContent='서버 연결 끊김 · 저장 상태 확인 필요';}},15000);
