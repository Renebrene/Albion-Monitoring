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

let currentStatus = "open";
let currentRole = null;
let myLogin = "";
let closeTaskId = null;

const fmtDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T")?"":"Z"));
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
};

// Модалки
window.closeSettingsModal = () => document.getElementById("settingsModal").classList.remove("active");
window.closeCreateTaskModal = () => document.getElementById("createTaskModal").classList.remove("active");
window.closeCloseTaskModal = () => {
  document.getElementById("closeTaskModal").classList.remove("active");
  closeTaskId = null;
};

["settingsModal","createTaskModal","closeTaskModal"].forEach(id => {
  document.getElementById(id).addEventListener("click", e => {
    if(e.target.id === id) e.target.classList.remove("active");
  });
});

// Выбор статуса закрытия
document.querySelectorAll(".status-option").forEach(opt => {
  opt.addEventListener("click", () => {
    document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
    opt.classList.add("selected");
    const radio = opt.querySelector("input[type=radio]");
    radio.checked = true;
    document.getElementById("customReason").style.display = radio.value === "custom" ? "block" : "none";
  });
});

const renderTasks = (tasks) => {
  const list = document.getElementById("taskList");
  if(!tasks.length){ list.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px">Заданий нет</div>'; return; }
  
  list.innerHTML = tasks.map(t => {
    const urgMap = { new:{cls:"urg-new", text:"Новое"}, warning:{cls:"urg-warning", text:"24ч+"}, overdue:{cls:"urg-overdue", text:"Просрочено"}, done:{cls:"urg-done", text:"Выполнено"} };
    const u = urgMap[t.urgency] || urgMap.done;
    const isImportant = t.is_important === 1;
    
    let adminActions = "";
    if(currentRole === "admin"){
      adminActions = `
        <div class="task-actions">
          <button class="btn btn-important ${isImportant?'active':''}" onclick="toggleImportant(${t.id}, ${!isImportant})">
            ${isImportant ? '⭐ Важное' : '☆ Отметить важным'}
          </button>
          ${t.status === 'open' ? `<button class="btn btn-close" onclick="openCloseModal(${t.id})">🔒 Закрыть</button>` : ''}
          <button class="btn btn-delete" onclick="deleteTask(${t.id})"> Удалить</button>
        </div>
      `;
    }
    
    let closeReasonHtml = "";
    if(t.close_reason){
      closeReasonHtml = `<div class="close-reason">📌 ${t.close_reason.replace(/</g,"&lt;")}</div>`;
    }
    
    return `
      <div class="task-card ${isImportant ? 'important' : ''}" id="task-${t.id}">
        <div class="task-header">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="urg-badge ${u.cls}">${u.text} · №${t.id}</span>
            ${isImportant ? '<span style="font-size:16px">⭐</span>' : ''}
          </div>
        </div>
        <div class="task-meta">
          <div class="city">${t.city}</div>
          <div class="addr">${t.address_name}</div>
          <div class="task-desc">${t.description.replace(/</g,"&lt;")}</div>

          ${
            Array.isArray(t.media) && t.media.length
              ? `
                <div class="chat-media" style="margin:0 0 10px">
                  ${t.media.map((m, index) => {
                    const url = String(m.url || "");
                    const type = String(m.type || "");
                    const isVideo =
                      type === "video" ||
                      /\.(mp4|mov|webm|m4v)$/i.test(url);

                    return isVideo
                      ? `
                        <video
                          src="${url}"
                          controls
                          preload="metadata"
                          title="Вложение ${index + 1}"
                        ></video>
                      `
                      : `
                        <img
                          src="${url}"
                          alt="Вложение ${index + 1}"
                          onclick="window.open('${url}', '_blank')"
                        >
                      `;
                  }).join("")}
                </div>
              `
              : ""
          }

          ${closeReasonHtml}
          <div style="font-size:12px;color:var(--muted);margin:8px 0">📅 ${fmtDate(t.created_at)} · 👤 ${t.author}</div>
          ${adminActions}
        </div>
        <div class="chat-box" id="chat-${t.id}">
          <div style="text-align:center;color:var(--muted);font-size:12px;padding:20px">Загрузка...</div>
        </div>
        ${
          ["completed", "closed"].includes(t.workflow_status) ||
          (currentRole === "service" && t.workflow_status === "pending_review")
            ? ""
            : `
              <div class="chat-input-area">
                <input
                  id="media-${t.id}"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v"
                  multiple
                  style="display:none"
                >
                <button
                  type="button"
                  class="btn chat-attach-btn"
                  onclick="document.getElementById('media-${t.id}').click()"
                  title="Прикрепить фото или видео"
                >📎</button>
                <textarea id="input-${t.id}" placeholder="Напишите ответ..."></textarea>
                <button class="btn" onclick="sendComment(${t.id})">➤</button>
              </div>
            `
        }
      </div>
    `;
  }).join("");

  tasks.forEach(t => loadChat(t.id));
};

const loadChat = async (taskId) => {
  try {
    const comments = await API(`/api/tasks/${taskId}/comments`, {headers: authHeaders()});
    const chatBox = document.getElementById(`chat-${taskId}`);
    if(!comments.length) {
      chatBox.innerHTML = '<div style="text-align:center;color:var(--muted);font-size:12px;padding:20px">Пока нет ответов</div>';
      return;
    }
    chatBox.innerHTML = comments.map(c => {
      const isMe = c.author === myLogin;
      const isDirector = c.is_director_comment === true;
      const mediaHtml = c.media && c.media.length ? `
        <div class="chat-media">
          ${c.media.map(m => {
            const isVideo = m.endsWith('.mp4') || m.endsWith('.mov') || m.endsWith('.webm');
            return isVideo ? `<video src="/uploads/${m}" controls></video>` : `<img src="/uploads/${m}" onclick="window.open('/uploads/${m}','_blank')">`;
          }).join("")}
        </div>` : "";
      return `
        <div class="chat-msg ${isMe ? 'me' : ''} ${isDirector ? 'director' : ''}">
          <div class="chat-author">${c.author}</div>
          <div class="chat-bubble">
            ${c.text.replace(/\n/g, "<br>")}
            ${mediaHtml}
            <div class="chat-time">${fmtDate(c.created_at)}</div>
          </div>
        </div>
      `;
    }).join("");
    chatBox.scrollTop = chatBox.scrollHeight;
  } catch(e) {
    const box = document.getElementById(`chat-${taskId}`);
    if(box) box.innerHTML = '<div style="color:var(--err);padding:10px">Ошибка загрузки</div>';
  }
};

window.sendComment = async (taskId) => {
  const input = document.getElementById(`input-${taskId}`);
  const mediaInput = document.getElementById(`media-${taskId}`);

  const text = input ? input.value.trim() : "";
  const files = mediaInput
    ? Array.from(mediaInput.files || [])
    : [];

  if(!text && !files.length) return;

  if(files.length > 10){
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  const sendBtn = input
    ? input.nextElementSibling
    : null;

  if(sendBtn){
    sendBtn.disabled = true;
    sendBtn.textContent = "...";
  }

  try {
    const uploaded = [];

    for(const file of files){
      const fd = new FormData();
      fd.append("file", file);

      const uploadResponse = await fetch("/api/upload", {
        method: "POST",
        headers: authHeaders(),
        body: fd
      });

      if(!uploadResponse.ok){
        let message = "Ошибка загрузки файла";
        try {
          const err = await uploadResponse.json();
          message = err.detail || message;
        } catch(e) {}
        throw new Error(message);
      }

      const media = await uploadResponse.json();
      uploaded.push(media.filename);
    }

    await API("/api/comments", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        task_id: taskId,
        text: text,
        media: uploaded
      })
    });

    if(input) input.value = "";
    if(mediaInput) mediaInput.value = "";

    await loadChat(taskId);
    loadTasks();

  } catch(e) {
    toast(e.message, true);
  } finally {
    if(sendBtn){
      sendBtn.disabled = false;
      sendBtn.textContent = "➤";
    }
  }
};

const loadTasks = async () => {
  const tasks = await API(`/api/tasks?status=${currentStatus}`, {headers: authHeaders()});
  renderTasks(tasks);
};

// ===== АДМИН =====
document.getElementById("adminSettingsBtn").onclick = () => {
  document.getElementById("settingsModal").classList.add("active");
  loadUsers(); loadMediaSettings();
};

document.getElementById("addTaskBtn").onclick = async () => {
  document.getElementById("createTaskModal").classList.add("active");
  const cities = await API("/api/cities");
  const sel = document.getElementById("taskCity");
  sel.innerHTML = '<option value="">— город —</option>';
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; sel.appendChild(o); });
  document.getElementById("taskAddrId").value = "";
  document.getElementById("taskAddrSearch").value = "";
  document.getElementById("taskAddrList").innerHTML = "";
  document.getElementById("taskDesc").value = "";
};

window.saveMediaSettings = async () => {
  try {
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"photos_enabled", value: document.getElementById("togglePhotos").checked?"1":"0"}) });
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"videos_enabled", value: document.getElementById("toggleVideos").checked?"1":"0"}) });
    toast("✓ Сохранено");
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
    document.getElementById("usersList").innerHTML = users.map(u => `
      <div class="user-row">
        <span style="flex:1;min-width:150px"><b>${u.login}</b> <span style="color:var(--muted);font-size:12px">(${roleNames[u.role]})</span></span>
        <input id="pwd_${u.id}" type="password" placeholder="Новый пароль" style="flex:1;min-width:150px;margin:0;padding:8px;font-size:13px">
        <button class="btn secondary" style="width:auto;margin:0;padding:8px 16px;font-size:13px" onclick="changePassword(${u.id})">Сменить</button>
      </div>
    `).join("");
  } catch(e){}
};

window.addUser = async () => {
  const login = document.getElementById("newLogin").value.trim();
  const password = document.getElementById("newPassword").value.trim();
  const role = document.getElementById("newRole").value;
  if(!login || !password){ toast("Заполните поля", true); return; }
  try {
    await API("/api/users", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({login, password, role}) });
    toast("✓ Добавлен");
    document.getElementById("newLogin").value = "";
    document.getElementById("newPassword").value = "";
    loadUsers();
  } catch(e) { toast(e.message, true); }
};

window.changePassword = async (userId) => {
  const pwd = document.getElementById(`pwd_${userId}`).value.trim();
  if(!pwd){ toast("Введите пароль", true); return; }
  try {
    await API(`/api/users/${userId}/password`, { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({new_password: pwd}) });
    toast("✓ Пароль изменён");
    document.getElementById(`pwd_${userId}`).value = "";
  } catch(e) { toast(e.message, true); }
};

// ===== НОВЫЕ ФУНКЦИИ: ВАЖНОЕ И ЗАКРЫТИЕ =====

window.toggleImportant = async (taskId, value) => {
  try {
    await API(`/api/tasks/${taskId}`, {
      method: "PATCH", headers: {"Content-Type":"application/json", ...authHeaders()},
      body: JSON.stringify({ is_important: value })
    });
    toast(value ? "⭐ Задание помечено как важное" : "☆ Метка важности снята");
    loadTasks();
  } catch(e) { toast(e.message, true); }
};

window.openCloseModal = (taskId) => {
  closeTaskId = taskId;
  document.getElementById("closeTaskId").textContent = taskId;
  document.getElementById("closeTaskModal").classList.add("active");
  // Сброс выбора
  document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
  document.querySelector('.status-option[data-reason="Сделано"]').classList.add("selected");
  document.querySelector('input[name="closeStatus"][value="Сделано"]').checked = true;
  document.getElementById("customReason").value = "";
  document.getElementById("customReason").style.display = "none";
};

window.confirmCloseTask = async () => {
  if(!closeTaskId) return;
  const selected = document.querySelector('input[name="closeStatus"]:checked');
  let reason = selected.value;
  if(reason === "custom"){
    reason = document.getElementById("customReason").value.trim();
    if(!reason){ toast("Введите свой статус", true); return; }
  }
  try {
    await API(`/api/tasks/${closeTaskId}`, {
      method: "PATCH", headers: {"Content-Type":"application/json", ...authHeaders()},
      body: JSON.stringify({ status: "done", close_reason: reason })
    });
    toast(`✓ Задание закрыто: ${reason}`);
    closeCloseTaskModal();
    loadTasks();
  } catch(e) { toast(e.message, true); }
};

window.deleteTask = async (id) => {
  if(!confirm("Удалить задание и всю переписку?")) return;
  try {
    await API(`/api/tasks/${id}`, {method:"DELETE", headers: authHeaders()});
    toast("Удалено");
    loadTasks();
  } catch(e) { toast(e.message, true); }
};

// Создание задания
window.createTask = async () => {
  const addrId = document.getElementById("taskAddrId").value;
  const desc = document.getElementById("taskDesc").value.trim();
  const mediaInput = document.getElementById("taskMedia");
  const files = mediaInput ? Array.from(mediaInput.files || []) : [];

  if(!addrId){ toast("Выберите адрес", true); return; }
  if(!desc){ toast("Введите описание", true); return; }

  if(files.length > 10){
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  const createBtn = document.querySelector("#createTaskModal .btn:not(.secondary)");
  if(createBtn){
    createBtn.disabled = true;
    createBtn.textContent = "Создание...";
  }

  try {
    // 1. Сначала создаём само задание.
    const created = await API("/api/tasks", {
      method: "POST",
      headers: {"Content-Type":"application/json", ...authHeaders()},
      body: JSON.stringify({
        address_id: Number(addrId),
        description: desc
      })
    });

    const taskId = created.task_id;
    const uploaded = [];

    // 2. Загружаем выбранные файлы через общий механизм /api/upload.
    for(const file of files){
      const fd = new FormData();
      fd.append("file", file);

      const uploadResponse = await fetch("/api/upload", {
        method: "POST",
        headers: authHeaders(),
        body: fd
      });

      if(!uploadResponse.ok){
        let message = "Ошибка загрузки файла";
        try {
          const err = await uploadResponse.json();
          message = err.detail || message;
        } catch(e) {}
        throw new Error(message);
      }

      const media = await uploadResponse.json();
      uploaded.push(media.filename);
    }

    // 3. Привязываем временные файлы к заданию.
    if(uploaded.length){
      await API(`/api/tasks/${taskId}/media`, {
        method: "POST",
        headers: {"Content-Type":"application/json", ...authHeaders()},
        body: JSON.stringify({filenames: uploaded})
      });
    }

    toast("✓ Задание создано");
    document.getElementById("taskDesc").value = "";
    if(mediaInput) mediaInput.value = "";
    closeCreateTaskModal();
    loadTasks();

  } catch(e) {
    toast(e.message, true);
  } finally {
    if(createBtn){
      createBtn.disabled = false;
      createBtn.textContent = "Создать";
    }
  }
};

document.getElementById("taskCity").addEventListener("change", () => {
  document.getElementById("taskAddrId").value = "";
  document.getElementById("taskAddrSearch").value = "";
  document.getElementById("taskAddrList").innerHTML = "";
});

document.getElementById("taskAddrSearch").addEventListener("input", async e => {
  const city = document.getElementById("taskCity").value;
  if(!city) return;
  const rows = await API(`/api/addresses?city=${encodeURIComponent(city)}&q=${encodeURIComponent(e.target.value)}`);
  document.getElementById("taskAddrList").innerHTML = rows.map(r => 
    `<button type="button" class="btn secondary" style="margin:4px 0;padding:10px;text-align:left;font-size:14px;width:100%" 
      onclick="pickTaskAddr(${r.id}, '${r.address_name.replace(/'/g, "\\'")}')">${r.address_name}</button>`
  ).join("");
});

window.pickTaskAddr = (id, name) => {
  document.getElementById("taskAddrId").value = id;
  document.getElementById("taskAddrSearch").value = name;
  document.getElementById("taskAddrList").innerHTML = "";
};

document.getElementById("statusFilter").addEventListener("click", e => {
  if(!e.target.dataset.status) return;
  currentStatus = e.target.dataset.status;
  [...document.querySelectorAll("#statusFilter .chip")].forEach(c=>c.classList.remove("active"));
  e.target.classList.add("active");
  loadTasks();
});

(async () => {
  const me = await API("/api/me", {headers: authHeaders()}).catch(()=>null);
  if(!me){ location.href = "/"; return; }
  currentRole = me.role;
  myLogin = me.login;
  document.getElementById("whoami").textContent = me.login + " (" + me.role_label + ")";
  
  if(currentRole === "admin"){
    document.getElementById("adminSettingsBtn").style.display = "inline-block";
    document.getElementById("addTaskBtn").style.display = "inline-flex";
  } else if(currentRole === "service"){
    document.getElementById("serverLink").style.display = "none";
  } else if(currentRole === "director"){
    document.getElementById("clientLink").style.display = "none";
  }
  

// Обработчик кнопки "Выйти"
document.getElementById("logout").onclick = () => { 
  localStorage.clear(); 
  location.href = "/"; 
};

  loadTasks();
})();
