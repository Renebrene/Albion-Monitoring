const API = (path, opts={}) => fetch(path, opts).then(async r => {
  if(r.status === 401){ localStorage.clear(); location.href = "/"; }
  const data = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.detail || "Ошибка");
  return data;
});
const authHeaders = () => ({ "Authorization": "Bearer " + localStorage.getItem("token") });
const toast = (msg, err=false) => {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (err?" err":"");
  t.style.display = "block";
  setTimeout(()=>t.style.display="none", 3000);
};

let myLogin = "";
let myRole = "";
let allFeed = [];
let currentFilter = "all";
let closeTaskId = null;

const fmtDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T")?"":"Z"));
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
};

window.closeSettingsModal = () => document.getElementById("settingsModal").classList.remove("active");
window.closeCreateTaskModal = () => document.getElementById("createTaskModal").classList.remove("active");
window.closeCloseTaskModal = () => { document.getElementById("closeTaskModal").classList.remove("active"); closeTaskId = null; };
window.closeMonitoringModal = () => document.getElementById("monitoringModal").classList.remove("active");

["settingsModal","createTaskModal","closeTaskModal","monitoringModal"].forEach(id => {
  document.getElementById(id).addEventListener("click", e => { if(e.target.id === id) e.target.classList.remove("active"); });
});

window.selectCloseReason = (el, reason) => {
  document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
  el.classList.add("selected");
  document.getElementById("customReason").style.display = reason === "custom" ? "block" : "none";
};

// Разрешаем разворачивать ЛЮБУЮ карточку (и задание, и мониторинг)
window.toggleExpand = (id, event) => {
  // Игнорируем клики по кнопкам, полям ввода и ссылкам внутри карточки
  if(event && (event.target.tagName === 'BUTTON' || event.target.tagName === 'TEXTAREA' || event.target.tagName === 'INPUT' || event.target.closest('.chat-input-area') || event.target.closest('.modal-overlay') || event.target.closest('.task-actions'))) {
    return;
  }
  const el = document.getElementById(`item-${id}`);
  if(el) el.classList.toggle('expanded');
};

const renderFeed = (items) => {
  const feed = document.getElementById("feed");
  if(!items.length){ feed.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px">Событий не найдено</div>'; return; }
  
  feed.innerHTML = items.map(item => {
    const isTask = item.type === "task";
    const typeBadge = isTask ? '<span class="type-badge type-task">📋 Задание</span>' : '<span class="type-badge type-monitoring">📷 Мониторинг</span>';
    
    let statusBadge = "";
    if(isTask){
      const urgMap = { new:{cls:"urg-new", text:"Новое"}, warning:{cls:"urg-warning", text:"24ч+"}, overdue:{cls:"urg-overdue", text:"Просрочено"}, done:{cls:"urg-done", text:"Выполнено"} };
      const u = urgMap[item.status] || urgMap.new;
      statusBadge = `<span class="status-badge ${u.cls}">${u.text}</span>`;
    }
    
    const importantIcon = (isTask && item.is_important) ? '⭐' : '';
    const directorMark = item.has_director_comment ? '<span class="director-mark" title="Есть комментарий директора"></span>' : '';
    
    // Получаем последнее сообщение для отображения в сводке
    let lastMsgHtml = '';
    if (item.messages && item.messages.length > 0) {
        const lastMsg = item.messages[item.messages.length - 1];
        const lastMsgTime = fmtDate(lastMsg.created_at);
        const lastMsgAuthor = lastMsg.is_director_comment ? "Директор" : lastMsg.author;
        lastMsgHtml = `<div style="font-size:11px;color:var(--muted);margin-top:4px;display:flex;align-items:center;gap:4px"><span>💬</span><span>${lastMsgAuthor}</span><span>в</span><span>${lastMsgTime}</span></div>`;
    }
    
    let borderClass = "";
    if(isTask && item.status === "done") borderClass = "done";
    else if(isTask && item.is_overdue) borderClass = "overdue";
    else if(isTask && item.is_important) borderClass = "important";
    
    const shortDesc = item.text.replace(/\n/g, ' ').substring(0, 80) + (item.text.length > 80 ? '...' : '');
    
    // Рендеринг чата (разрешаем писать всем, включая директора)
    const chatHtml = (item.messages || []).map(m => {
      const isMe = m.author === myLogin;
      const isDirector = m.is_director_comment === true;
      const authorName = isDirector ? "Директор" : m.author;
      
      return `<div class="chat-msg ${isMe ? 'me' : ''} ${isDirector ? 'director' : ''}">
        <div class="chat-author">${authorName}</div>
        <div class="chat-bubble">${m.text.replace(/\n/g, "<br>")}<div class="chat-time">${fmtDate(m.created_at)}</div></div>
      </div>`;
    }).join("") || '<div style="text-align:center;color:var(--muted);font-size:12px;padding:10px">Пока нет сообщений</div>';

    // Кнопки управления ТОЛЬКО для админа и ТОЛЬКО для заданий
    let adminActions = "";
    if(myRole === "admin" && isTask){
      adminActions = `<div class="task-actions" onclick="event.stopPropagation()">
        <button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:${item.is_important?'#f59e0b':'#fbbf24'};color:${item.is_important?'#fff':'#78350f'}" onclick="toggleImportant(${item.id}, ${!item.is_important})">${item.is_important ? '⭐ Важное' : '☆ Отметить важным'}</button>
        ${item.status === 'open' ? `<button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff" onclick="openCloseModal(${item.id})">🔒 Закрыть</button>` : ''}
        <button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#ef4444;color:#fff" onclick="deleteTask(${item.id})">🗑 Удалить</button>
      </div>`;
    }

    // Плейсхолдер для ввода: зависит от роли, но доступен всем
    const inputPlaceholder = myRole === 'director' ? 'Оставить комментарий...' : 'Написать сообщение...';

    return `<div class="feed-item ${borderClass}" id="item-${item.id}" onclick="toggleExpand(${item.id}, event)">
      <div class="feed-summary">
        <div class="left">
          <div class="top-row">
            ${typeBadge}
            <span style="font-size:12px;font-weight:700;color:var(--muted)">№${item.id}</span>
            ${directorMark}
            ${importantIcon ? `<span style="font-size:14px">${importantIcon}</span>` : ''}
          </div>
          <div class="addr-line">
            <span class="addr" style="font-size:15px;font-weight:700">${item.address_name}</span>
            ${statusBadge}
            ${item.is_overdue ? '<span style="color:#ef4444;font-weight:700;font-size:11px"> Просрочено</span>' : ''}
          </div>
          <div class="bottom-row">
            <span>📍 ${item.city}</span>
            <span>📅 ${fmtDate(item.created_at)}</span>
            ${isTask && item.close_reason ? `<span style="color:#92400e"> ${item.close_reason}</span>` : ''}
          </div>
        </div>
        <div class="right">
          <div class="short-desc">${shortDesc.replace(/</g,"&lt;")}</div>
          <div style="font-size:18px;color:var(--muted);align-self:flex-end">▼</div>
        </div>
      </div>
      <div class="feed-details" onclick="event.stopPropagation()">
        <div class="city">${item.city} · ${item.address_name}</div>
        <div class="full-text">${item.text.replace(/</g,"&lt;").replace(/\n/g,"<br>")}</div>
        ${isTask && item.close_reason ? `<div class="close-reason">📌 ${item.close_reason.replace(/</g,"&lt;")}</div>` : ''}
        ${adminActions}
        <div style="margin-top:16px;font-size:13px;font-weight:700;color:var(--muted);margin-bottom:8px">💬 Обсуждение</div>
        <div class="chat-box" id="chat-${item.type}-${item.id}">${chatHtml}</div>
        <div class="chat-input-area">
          <textarea id="input-${item.type}-${item.id}" placeholder="${inputPlaceholder}"></textarea>
          <button class="btn" onclick="sendMessage('${item.type}', ${item.id})">➤</button>
        </div>
      </div>
    </div>`;
  }).join("");
  document.querySelectorAll('.feed-item.expanded .chat-box').forEach(box => box.scrollTop = box.scrollHeight);
};

window.sendMessage = async (type, id) => {
  const input = document.getElementById(`input-${type}-${id}`);
  const text = input.value.trim();
  if(!text) return;
  const btn = input.nextElementSibling;
  btn.disabled = true; btn.textContent = "...";
  try {
    if(type === "task") {
      await API("/api/comments", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ task_id: id, text: text, media: [] }) });
    } else {
      // Отправка в мониторинг (доступно всем: сервис, директор, админ)
      await API("/api/monitoring", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ address_id: id, text: text, media: [] }) });
    }
    input.value = "";
    await loadFeed();
  } catch(e) { 
    toast(e.message || "Ошибка отправки", true); 
  }
  btn.disabled = false; btn.textContent = "➤";
};

const loadFeed = async () => {
  try {
    allFeed = await API("/api/feed", {headers: authHeaders()});
    applyFilters();
  } catch(e) { document.getElementById("feed").innerHTML = '<div style="color:var(--err);padding:20px">Ошибка загрузки ленты: ' + e.message + '</div>'; }
};

const applyFilters = () => {
  const city = document.getElementById("filterCity").value;
  const search = document.getElementById("filterSearch").value.toLowerCase();
  
  let filtered = allFeed;
  
  if(currentFilter === "task") filtered = filtered.filter(i => i.type === "task");
  else if(currentFilter === "monitoring") filtered = filtered.filter(i => i.type === "monitoring");
  else if(currentFilter === "current") filtered = filtered.filter(i => i.type === "task" && i.status === "open");
  else if(currentFilter === "done") filtered = filtered.filter(i => i.type === "task" && i.status === "done");
  else if(currentFilter === "important") filtered = filtered.filter(i => i.type === "task" && i.is_important);
  else if(currentFilter === "overdue") filtered = filtered.filter(i => i.type === "task" && i.is_overdue);
  
  if(city) filtered = filtered.filter(i => i.city === city);
  if(search) filtered = filtered.filter(i => i.address_name.toLowerCase().includes(search));
  
  renderFeed(filtered);
};

document.getElementById("filterButtons").addEventListener("click", e => {
  if(!e.target.classList.contains("filter-btn")) return;
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  e.target.classList.add("active");
  currentFilter = e.target.dataset.filter;
  applyFilters();
});

// ===== ФУНКЦИИ АДМИНА =====
document.getElementById("adminSettingsBtn").onclick = () => { document.getElementById("settingsModal").classList.add("active"); loadUsers(); loadMediaSettings(); };

window.saveMediaSettings = async () => {
  try {
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"photos_enabled", value: document.getElementById("togglePhotos").checked?"1":"0"}) });
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"videos_enabled", value: document.getElementById("toggleVideos").checked?"1":"0"}) });
    toast("✓ Настройки сохранены");
  } catch(e) { toast(e.message, true); }
};

const loadMediaSettings = async () => {
  try {
    const s = await API("/api/settings", {headers: authHeaders()});
    document.getElementById("togglePhotos").checked = s.photos_enabled;
    document.getElementById("toggleVideos").checked = s.videos_enabled;
  } catch(e){}
};

const loadUsers = async () => {
  try {
    const users = await API("/api/users", {headers: authHeaders()});
    const roleNames = {"admin":"Админ","service":"Сервис","director":"Руководитель"};
    document.getElementById("usersList").innerHTML = users.map(u => `<div class="user-row"><span style="flex:1;min-width:150px"><b>${u.login}</b> <span style="color:var(--muted);font-size:12px">(${roleNames[u.role]})</span></span><input id="pwd_${u.id}" type="password" placeholder="Новый пароль" style="flex:1;min-width:150px;margin:0;padding:8px;font-size:13px"><button class="btn secondary" style="width:auto;margin:0;padding:8px 16px;font-size:13px" onclick="changePassword(${u.id})">Сменить</button></div>`).join("");
  } catch(e){}
};

window.addUser = async () => {
  const login = document.getElementById("newLogin").value.trim();
  const password = document.getElementById("newPassword").value.trim();
  const role = document.getElementById("newRole").value;
  if(!login || !password){ toast("Заполните логин и пароль", true); return; }
  try {
    await API("/api/users", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({login, password, role}) });
    toast("✓ Пользователь добавлен");
    document.getElementById("newLogin").value = ""; document.getElementById("newPassword").value = "";
    loadUsers();
  } catch(e) { toast(e.message, true); }
};

window.changePassword = async (userId) => {
  const pwd = document.getElementById(`pwd_${userId}`).value.trim();
  if(!pwd){ toast("Введите новый пароль", true); return; }
  try {
    await API(`/api/users/${userId}/password`, { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({new_password: pwd}) });
    toast("✓ Пароль изменён");
    document.getElementById(`pwd_${userId}`).value = "";
  } catch(e) { toast(e.message, true); }
};

// Создание задания (Админ и Директор)
document.getElementById("addTaskBtn").onclick = async () => {
  document.getElementById("createTaskModal").classList.add("active");
  const cities = await API("/api/cities");
  const sel = document.getElementById("taskCity");
  sel.innerHTML = '<option value="">— город —</option>';
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; sel.appendChild(o); });
  document.getElementById("taskAddrId").value = ""; document.getElementById("taskAddrSearch").value = ""; document.getElementById("taskAddrList").innerHTML = ""; document.getElementById("taskDesc").value = "";
};

window.createTask = async () => {
  const addrId = document.getElementById("taskAddrId").value;
  const desc = document.getElementById("taskDesc").value.trim();
  if(!addrId || !desc){ toast("Заполните адрес и описание", true); return; }
  try {
    await API("/api/tasks", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({address_id: Number(addrId), description: desc}) });
    toast("✓ Задание создано");
    closeCreateTaskModal();
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

// Создание мониторинга (Сервис, но можно расширить)
document.getElementById("monitoringBtn").onclick = async () => {
  document.getElementById("monitoringModal").classList.add("active");
  const cities = await API("/api/cities");
  const sel = document.getElementById("monCity");
  sel.innerHTML = '<option value="">— город —</option>';
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; sel.appendChild(o); });
  document.getElementById("monAddrId").value = ""; document.getElementById("monAddrSearch").value = ""; document.getElementById("monAddrList").innerHTML = ""; document.getElementById("monText").value = "";
};

window.createMonitoring = async () => {
  const addrId = document.getElementById("monAddrId").value;
  const text = document.getElementById("monText").value.trim();
  if(!addrId || !text){ toast("Заполните адрес и текст", true); return; }
  try {
    await API("/api/monitoring", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ address_id: Number(addrId), text: text, media: [] }) });
    toast("✓ Запись мониторинга создана");
    closeMonitoringModal();
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

document.getElementById("taskAddrSearch").addEventListener("input", async e => {
  const city = document.getElementById("taskCity").value;
  if(!city) return;
  const rows = await API(`/api/addresses?city=${encodeURIComponent(city)}&q=${encodeURIComponent(e.target.value)}`);
  document.getElementById("taskAddrList").innerHTML = rows.map(r => `<button type="button" class="btn secondary" style="margin:4px 0;padding:10px;text-align:left;font-size:14px;width:100%" onclick="pickTaskAddr(${r.id}, '${r.address_name.replace(/'/g, "\\'")}')">${r.address_name}</button>`).join("");
});
window.pickTaskAddr = (id, name) => { document.getElementById("taskAddrId").value = id; document.getElementById("taskAddrSearch").value = name; document.getElementById("taskAddrList").innerHTML = ""; };

document.getElementById("monAddrSearch").addEventListener("input", async e => {
  const city = document.getElementById("monCity").value;
  if(!city) return;
  const rows = await API(`/api/addresses?city=${encodeURIComponent(city)}&q=${encodeURIComponent(e.target.value)}`);
  document.getElementById("monAddrList").innerHTML = rows.map(r => `<button type="button" class="btn secondary" style="margin:4px 0;padding:10px;text-align:left;font-size:14px;width:100%" onclick="pickMonAddr(${r.id}, '${r.address_name.replace(/'/g, "\\'")}')">${r.address_name}</button>`).join("");
});
window.pickMonAddr = (id, name) => { document.getElementById("monAddrId").value = id; document.getElementById("monAddrSearch").value = name; document.getElementById("monAddrList").innerHTML = ""; };

window.toggleImportant = async (taskId, value) => {
  try {
    await API(`/api/tasks/${taskId}`, { method:"PATCH", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ is_important: value }) });
    toast(value ? "⭐ Важное" : "☆ Обычное");
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

window.openCloseModal = (taskId) => {
  closeTaskId = taskId;
  document.getElementById("closeTaskId").textContent = taskId;
  document.getElementById("closeTaskModal").classList.add("active");
  document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
  document.querySelector('.status-option[data-reason="Сделано"]').classList.add("selected");
  document.getElementById("customReason").value = "";
  document.getElementById("customReason").style.display = "none";
};

window.confirmCloseTask = async () => {
  if(!closeTaskId) return;
  const selected = document.querySelector('.status-option.selected');
  let reason = selected.dataset.reason;
  if(reason === "custom"){
    reason = document.getElementById("customReason").value.trim();
    if(!reason){ toast("Введите свой статус", true); return; }
  }
  try {
    await API(`/api/tasks/${closeTaskId}`, { method:"PATCH", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ status: "done", close_reason: reason }) });
    toast(`✓ Задание закрыто: ${reason}`);
    closeCloseTaskModal();
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

window.deleteTask = async (id) => {
  if(!confirm("Удалить задание и всю переписку?")) return;
  try { await API(`/api/tasks/${id}`, {method:"DELETE", headers: authHeaders()}); toast("Удалено"); loadFeed(); } 
  catch(e) { toast(e.message, true); }
};

// Старт
(async () => {
  const me = await API("/api/me", {headers: authHeaders()}).catch(()=>null);
  if(!me){ location.href = "/"; return; }
  myLogin = me.login;
  myRole = me.role;
  
  document.getElementById("roleName").textContent = me.role_label;
  document.getElementById("whoami").textContent = me.login;
  
  // Показываем кнопки в зависимости от роли
  if(myRole === "admin") {
    document.getElementById("adminSettingsBtn").style.display = "inline-flex";
    document.getElementById("addTaskBtn").style.display = "inline-flex";
  }
  else if(myRole === "director") {
    document.getElementById("addTaskBtn").style.display = "inline-flex";
  }
  else if(myRole === "service") {
    document.getElementById("monitoringBtn").style.display = "inline-flex";
  }
  
  const cities = await API("/api/cities");
  const citySel = document.getElementById("filterCity");
  cities.forEach(c => { const o = document.createElement("option"); o.value = c; o.textContent = c; citySel.appendChild(o); });

  document.getElementById("filterCity").addEventListener("change", applyFilters);
  document.getElementById("filterSearch").addEventListener("input", applyFilters);
  document.getElementById("logout").onclick = () => { localStorage.clear(); location.href = "/"; };
  
  loadFeed();
})();
