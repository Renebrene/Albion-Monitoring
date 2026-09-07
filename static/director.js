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
let currentFilter = "all";

const fmtDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T")?"":"Z"));
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
};

const renderFeed = (items) => {
  const feed = document.getElementById("feed");
  
  // Фильтр
  let filtered = items;
  if(currentFilter !== "all") filtered = items.filter(i => i.type === currentFilter);
  
  if(!filtered.length){ 
    feed.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px">Нет событий</div>'; 
    return; 
  }
  
  feed.innerHTML = filtered.map(item => {
    const isTask = item.type === "task";
    const typeBadge = isTask 
      ? '<span class="type-badge type-task">📋 Задание</span>' 
      : '<span class="type-badge type-monitoring">📷 Мониторинг</span>';
    
    let statusBadge = "";
    if(isTask){
      const urgMap = { new:{cls:"urg-new", text:"Новое"}, warning:{cls:"urg-warning", text:"24ч+"}, overdue:{cls:"urg-overdue", text:"Просрочено"}, done:{cls:"urg-done", text:"Выполнено"} };
      const u = urgMap[item.status] || urgMap.new;
      statusBadge = `<span class="status-badge ${u.cls}">${u.text}</span>`;
    }
    
    let closeReason = "";
    if(isTask && item.close_reason){
      closeReason = `<div class="close-reason">📌 ${item.close_reason.replace(/</g,"&lt;")}</div>`;
    }
    
    const importantClass = (isTask && item.is_important) ? "important" : "";
    
    // Комментарии директора
    const commentsHtml = item.director_comments && item.director_comments.length 
      ? item.director_comments.map(c => `
          <div class="dc-item">
            <span class="dc-author">${c.author}</span>
            <span class="dc-time">${fmtDate(c.created_at)}</span>
            <div class="dc-text">${c.text.replace(/</g,"&lt;").replace(/\n/g,"<br>")}</div>
          </div>
        `).join("")
      : '<div style="font-size:12px;color:var(--muted);font-style:italic">Комментариев пока нет</div>';
    
    return `
      <div class="feed-item ${importantClass}" data-id="${item.id}" data-type="${item.type}">
        <div class="feed-header">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            ${typeBadge}
            ${statusBadge}
            ${isTask && item.is_important ? '<span style="font-size:16px">⭐</span>' : ''}
          </div>
          <div style="font-size:12px;color:var(--muted)">${fmtDate(item.created_at)}</div>
        </div>
        <div class="feed-body">
          <div class="city">${item.city}</div>
          <div class="addr">${item.address_name}</div>
          ${item.manufacturer ? `<div class="manuf">${item.manufacturer}</div>` : ""}
          <div class="text">${item.text.replace(/</g,"&lt;").replace(/\n/g,"<br>")}</div>
          ${closeReason}
          <div class="feed-meta">
            <span> ${item.author}</span>
          </div>
          
          <div class="director-comments">
            <h4>💬 Комментарии директора</h4>
            ${commentsHtml}
            <div class="director-input">
              <textarea id="dc-input-${item.type}-${item.id}" placeholder="Оставьте комментарий..."></textarea>
              <button class="btn" onclick="sendDirectorComment('${item.type}', ${item.id})">Отправить</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join("");
};

window.sendDirectorComment = async (type, id) => {
  const input = document.getElementById(`dc-input-${type}-${id}`);
  const text = input.value.trim();
  if(!text) return;
  
  try {
    await API("/api/director/comments", {
      method: "POST",
      headers: {"Content-Type":"application/json", ...authHeaders()},
      body: JSON.stringify({ item_type: type, item_id: id, text: text })
    });
    toast("✓ Комментарий добавлен");
    input.value = "";
    loadFeed();
  } catch(e) {
    toast(e.message, true);
  }
};

const loadFeed = async () => {
  try {
    const items = await API("/api/director/feed", {headers: authHeaders()});
    renderFeed(items);
  } catch(e) {
    document.getElementById("feed").innerHTML = '<div style="color:var(--err);padding:20px">Ошибка загрузки ленты</div>';
  }
};

// Фильтры
document.getElementById("typeFilter").addEventListener("click", e => {
  if(!e.target.dataset.type) return;
  currentFilter = e.target.dataset.type;
  [...document.querySelectorAll("#typeFilter .chip")].forEach(c=>c.classList.remove("active"));
  e.target.classList.add("active");
  loadFeed();
});

// Старт
(async () => {
  const me = await API("/api/me", {headers: authHeaders()}).catch(()=>null);
  if(!me || (me.role !== "director" && me.role !== "admin")){ location.href = "/"; return; }
  myLogin = me.login;
  document.getElementById("whoami").textContent = me.login + " (" + me.role_label + ")";
  document.getElementById("roleTag").textContent = me.role_label;
  
  document.getElementById("logout").onclick = () => { localStorage.clear(); location.href = "/"; };
  
  loadFeed();
})();
