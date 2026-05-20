const $ = (sel) => document.querySelector(sel);
const app = $('#app');
let token = localStorage.getItem('gtep_token') || '';
let role = localStorage.getItem('gtep_role') || '';
let studentState = null;
let timers = {};
let pasteCounts = {};
let draft = {};
let adminShowResponses = false;
let responsesCache = null;

async function api(path, options = {}) {
  const headers = {'Content-Type':'application/json', ...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {...options, headers});
  if (!res.ok) {
    let msg = '요청 처리 중 오류가 발생했습니다.';
    try { msg = (await res.json()).detail || msg; } catch(e) {}
    throw new Error(msg);
  }
  return res.json();
}
function esc(s){return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function escAttr(s){return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function logout(){localStorage.removeItem('gtep_token');localStorage.removeItem('gtep_role');token='';role='';if (adminTimer) clearInterval(adminTimer);renderLogin();}
function msg(text, err=false){return `<div class="msg ${err?'err':''}">${esc(text)}</div>`}

function renderLogin(){
  app.innerHTML = `<div class="narrow">
    <div class="card">
      <h1>GTEP 활동 평가 시스템</h1>
      <p class="muted">발급받은 아이디와 비밀번호를 입력해주세요.</p>
      <form id="loginForm">
        <label>아이디</label><input name="username" autocomplete="username" placeholder="아이디" autofocus />
        <label>비밀번호</label><input name="password" type="password" autocomplete="current-password" placeholder="비밀번호" />
        <div class="actions"><button type="submit">로그인</button></div>
        <div id="loginMsg"></div>
      </form>
      <p class="footer-note">발급받은 접속 정보가 없거나 로그인이 되지 않으면 운영자에게 문의해주세요.</p>
    </div>
  </div>`;
  $('#loginForm').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const data = await api('/api/login', {method:'POST', body: JSON.stringify({username: fd.get('username'), password: fd.get('password')})});
      token = data.token; role = data.role;
      localStorage.setItem('gtep_token', token); localStorage.setItem('gtep_role', role);
      if (role === 'admin') renderAdmin(); else renderStudent();
    } catch(err) { $('#loginMsg').innerHTML = msg(err.message, true); }
  };
}

async function renderStudent(){
  try { studentState = await api('/api/student/me'); } catch(e){ logout(); return; }
  const {student, targets, responses} = studentState;
  draft = {};
  responses.forEach(r => draft[`${r.target_type}|${r.target_id}`] = r.response_text);
  app.innerHTML = `<div class="container">
    <div class="topbar"><div><h1>GTEP 활동 평가</h1><div class="muted">배정된 직무팀·박람회팀 평가를 작성해주세요.</div></div><button class="secondary" onclick="logout()">로그아웃</button></div>
    <div class="card">
      <h2>작성 안내</h2>
      <p>자기평가는 제외되었습니다. 본인의 직무팀, 같은 직무팀 팀원, 본인 박람회팀, 같은 박람회팀 팀원, 다른 박람회팀 5개를 평가합니다.</p>
      <p class="muted">AI로 문장을 다듬을 수는 있으나 실제로 관찰하지 않은 내용은 쓰지 마세요. 구체적 근거가 부족한 응답은 분석에서 낮은 신뢰도로 처리됩니다.</p>
    </div>
    <div style="height:16px"></div>
    <form id="surveyForm"></form>
  </div>`;
  const form = $('#surveyForm');
  form.innerHTML = targets.map((t, idx) => {
    const key = `${t.target_type}|${t.target_id}`;
    const val = draft[key] || '';
    return `<div class="question" data-key="${esc(key)}" data-type="${esc(t.target_type)}" data-target="${esc(t.target_id)}" data-min="${t.min_chars}">
      <div class="qhead"><div><span class="badge">${idx+1}/${targets.length}</span><h3>${esc(t.target_label)}</h3></div><span class="pill">최소 ${t.min_chars}자</span></div>
      <p>${esc(t.question)}</p>
      <textarea data-key="${esc(key)}" placeholder="구체적인 상황, 행동, 역할, 결과를 포함해 작성해주세요.">${esc(val)}</textarea>
      <div class="progress"><span class="count">${val.length}</span>자 · <span class="status ${val.length>=t.min_chars?'good':'warn'}">${val.length>=t.min_chars?'기준 충족':'작성 필요'}</span></div>
    </div>`;
  }).join('') + `<div class="actions"><button type="button" class="secondary" id="saveBtn">임시저장</button><button type="submit">최종 제출</button></div><div id="surveyMsg"></div>`;
  document.querySelectorAll('textarea').forEach(ta => {
    const key = ta.dataset.key;
    pasteCounts[key] = 0;
    ta.addEventListener('focus', () => { if (!timers[key]) timers[key] = {start: Date.now(), total: 0}; else timers[key].start = Date.now(); });
    ta.addEventListener('blur', () => { if (timers[key]?.start) { timers[key].total += Math.round((Date.now()-timers[key].start)/1000); timers[key].start = 0; }});
    ta.addEventListener('paste', () => { pasteCounts[key] = (pasteCounts[key]||0)+1; });
    ta.addEventListener('input', () => updateCount(ta));
  });
  $('#saveBtn').onclick = saveAll;
  form.onsubmit = async (e) => { e.preventDefault(); await submitAll(); };
}
function updateCount(ta){
  const q = ta.closest('.question'); const min = Number(q.dataset.min); const c = ta.value.length;
  q.querySelector('.count').textContent = c;
  const st = q.querySelector('.status'); st.textContent = c>=min ? '기준 충족' : '작성 필요'; st.className = `status ${c>=min?'good':'warn'}`;
}
function collectResponses(){
  document.querySelectorAll('textarea').forEach(ta => { if (timers[ta.dataset.key]?.start) { timers[ta.dataset.key].total += Math.round((Date.now()-timers[ta.dataset.key].start)/1000); timers[ta.dataset.key].start = Date.now(); }});
  return [...document.querySelectorAll('.question')].map(q => ({
    target_type: q.dataset.type,
    target_id: q.dataset.target,
    response_text: q.querySelector('textarea').value.trim(),
    writing_time_sec: timers[q.dataset.key]?.total || 0,
    paste_count: pasteCounts[q.dataset.key] || 0,
  }));
}
async function saveAll(){
  const box = $('#surveyMsg'); box.innerHTML = msg('저장 중입니다.');
  const responses = collectResponses().filter(r => r.response_text.length > 0);
  try { for (const r of responses) await api('/api/student/save', {method:'POST', body: JSON.stringify(r)}); box.innerHTML = msg('임시저장되었습니다.'); }
  catch(e){ box.innerHTML = msg(e.message, true); }
}
async function submitAll(){
  const box = $('#surveyMsg'); box.innerHTML = msg('제출 중입니다.');
  try { await api('/api/student/submit', {method:'POST', body: JSON.stringify({responses: collectResponses()})}); box.innerHTML = msg('최종 제출이 완료되었습니다.'); setTimeout(renderStudent, 900); }
  catch(e){ box.innerHTML = msg(e.message, true); }
}

let adminTimer = null;
async function renderAdmin(){
  if (adminTimer) clearInterval(adminTimer);
  app.innerHTML = `<div class="container"><div class="topbar"><div><h1>GTEP 운영 대시보드</h1><div class="muted">실시간 제출 현황, 키워드, 전체 순위, 팀별 순위를 확인합니다.</div></div><button class="secondary" onclick="logout()">로그아웃</button></div><div id="adminContent"></div></div>`;
  await loadAdmin(); adminTimer = setInterval(loadAdmin, 10000);
}
function kpiCard(label, value){return `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`}
async function loadAdmin(){
  try {
    const data = await api('/api/admin/dashboard');
    const k = data.kpi, r = data.rankings;
    $('#adminContent').innerHTML = `
      <div class="grid grid-4">
        ${kpiCard('전체 학생', k.total_students+'명')}${kpiCard('제출 완료', k.submitted+'명')}${kpiCard('제출률', k.submit_rate+'%')}${kpiCard('검토 필요 응답', k.low_reliability+'건')}
      </div>
      <div style="height:16px"></div>
      <div class="split">
        <div class="card"><h2>많이 언급된 단어</h2><div class="word-list">${data.keywords.slice(0,25).map(w=>`<span class="word">${esc(w.word)} ${w.count}</span>`).join('') || '<span class="muted">아직 응답이 없습니다.</span>'}</div></div>
        <div class="card"><h2>응답 품질</h2><p>평균 글자 수: <b>${k.avg_chars}</b>자</p><p>평균 작성 시간: <b>${k.avg_minutes}</b>분</p><div class="bar"><span style="width:${k.submit_rate}%"></span></div><p class="small muted">10초마다 자동 갱신됩니다.</p></div>
      </div>
      <div style="height:16px"></div>
      <div class="grid grid-2">
        <div class="card"><h2>전체 학생 순위</h2>${rankTable(r.individual, 'student')}</div>
        <div class="card"><h2>박람회팀 순위</h2>${rankTable(r.fair_teams, 'team')}<h2 style="margin-top:22px">직무팀 순위</h2>${rankTable(r.job_teams, 'team')}</div>
      </div>
      <div style="height:16px"></div>
      <div class="card"><h2>제출 현황</h2>${statusTable(data.submission_status)}</div>
      <div class="actions"><button class="secondary" onclick="downloadCsv()">응답 CSV 다운로드</button><button onclick="loadResponses(true)">원문 응답 보기</button></div>
      <div id="responsesBox"></div>
    `;
    if (adminShowResponses) {
      renderResponsesBox(responsesCache);
      loadResponses(false);
    }
  } catch(e){ $('#adminContent').innerHTML = msg(e.message, true); }
}
function rankTable(items, type){
  if (!items.length) return '<p class="muted">아직 분석 가능한 응답이 없습니다.</p>';
  return `<table><thead><tr>${type==='student'?'<th>순위</th><th>학생</th><th>팀</th><th>점수</th><th>강점</th>':'<th>순위</th><th>팀</th><th>점수</th><th>응답수</th>'}</tr></thead><tbody>` + items.slice(0,12).map(x => type==='student' ? `<tr><td>${x.rank}</td><td>${esc(x.name)}</td><td>${esc(x.job_team)}<br>${esc(x.fair_team)}</td><td><b>${x.score}</b></td><td>${esc((x.tags||[]).join(', '))}</td></tr>` : `<tr><td>${x.rank}</td><td>${esc(x.team)}</td><td><b>${x.score}</b></td><td>${x.responses}</td></tr>`).join('') + '</tbody></table>';
}
function statusTable(items){
  return `<table><thead><tr><th>학생</th><th>직무팀</th><th>박람회팀</th><th>상태</th><th>관리</th></tr></thead><tbody>` + items.map(s=>{
    const status = s.submitted_at
      ? '<span class="good">최종제출 완료</span><br><span class="small muted">'+esc(s.submitted_at)+'</span>'
      : (s.saved_count > 0 ? '<span class="warn">임시저장/작성중</span><br><span class="small muted">저장 항목 '+s.saved_count+'개</span>' : '<span class="muted">미작성</span>');
    const action = s.submitted_at ? `<button class="secondary small-btn" onclick="reopenStudent('${escAttr(s.student_id)}','${escAttr(s.name)}')">임시저장으로 전환</button>` : '<span class="small muted">-</span>';
    return `<tr><td>${esc(s.name)}</td><td>${esc(s.job_team)}</td><td>${esc(s.fair_team)}</td><td>${status}</td><td>${action}</td></tr>`;
  }).join('') + '</tbody></table>';
}
async function reopenStudent(studentId, name){
  if(!confirm(name + ' 학생의 최종제출 상태를 임시저장/작성중으로 되돌릴까요? 저장된 응답은 삭제되지 않으며 학생이 다시 로그인해 수정 후 최종제출할 수 있습니다.')) return;
  try {
    const res = await api('/api/admin/reopen', {method:'POST', body: JSON.stringify({student_id: studentId})});
    alert(res.message || '임시저장 상태로 변경했습니다.');
    await loadAdmin();
  } catch(e){ alert(e.message); }
}
function safeTags(value){
  try { return JSON.parse(value || '[]').join(', '); } catch(e) { return ''; }
}
function renderResponsesBox(data){
  const box = $('#responsesBox');
  if (!box) return;
  if (!data) { box.innerHTML = msg('원문을 불러오는 중입니다.'); return; }
  box.innerHTML = `<div class="card response-box" style="margin-top:16px"><div class="response-head"><h2>원문 응답</h2><button class="secondary small-btn" onclick="hideResponses()">닫기</button></div><table><thead><tr><th>평가자</th><th>대상</th><th>분석</th><th>응답</th></tr></thead><tbody>${data.responses.map(r=>`<tr><td>${esc(r.evaluator_name)}</td><td>${esc(r.target_label || r.target_id)}</td><td>구체성 ${r.specificity_score}<br>근거 ${r.evidence_score}<br>신뢰도 ${r.reliability_score}<br><span class="small">${esc(safeTags(r.competency_tags))}</span></td><td>${esc(r.response_text)}</td></tr>`).join('')}</tbody></table></div>`;
}
function hideResponses(){
  adminShowResponses = false;
  responsesCache = null;
  const box = $('#responsesBox');
  if (box) box.innerHTML = '';
}
async function loadResponses(markOpen=true){
  if (markOpen) adminShowResponses = true;
  renderResponsesBox(responsesCache);
  try {
    responsesCache = await api('/api/admin/responses');
    renderResponsesBox(responsesCache);
  } catch(e){ const box = $('#responsesBox'); if (box) box.innerHTML = msg(e.message, true); }
}
async function downloadCsv(){
  try {
    const res = await fetch('/api/admin/export.csv', {headers:{Authorization:`Bearer ${token}`}});
    if(!res.ok) throw new Error('CSV 다운로드에 실패했습니다.');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'gtep_responses.csv'; a.click();
    URL.revokeObjectURL(url);
  } catch(e){ alert(e.message); }
}

if (token && role === 'student') renderStudent(); else if (token && role === 'admin') renderAdmin(); else renderLogin();
