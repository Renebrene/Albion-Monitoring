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

let sort = "desc";

const fmtDate = iso => {
  const d = new Date(iso + (iso.includes("T")?"":"Z"));
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
};

const render = rows => {
  const feed = document.getElementById("feed");
  if(!rows.length){ feed.innerHTML = '<div class="empty" style="text-align:center;color:var(--muted);padding:40px">Записей пока нет</div>'; return; }
  feed.innerHTML = rows.map(r => {
    const mediaHtml = r.media && r.media.length ? `
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
        ${r.media.map(m => m.type === 'video' 
          ? `<video src="${m.url}" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:1px solid var(--border)" controls></video>`
          : `<img src="${m.url}" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:1px solid var(--border)" onclick="window.open('${m.url}','_blank')">`
        ).join("")}
      </div>` : "";
    
    return `
      <div class="card">
        <div class="city">${r.city} ${r.manufacturer ? '· '+r.manufacturer : ''}</div>
        <div class="addr">${r.address_name}</div>
        <div class="meta">
          <span>🕒 ${fmtDate(r.created_at)}</span>
          <span>👤 ${r.author}</span>
          ${r.task_id ? '<span style="color:var(--primary);font-weight:600">↳ Ответ на задание</span>' : ''}
        </div>
        <div class="text">${r.text.replace(/</g,"&lt;")}</div>
        ${mediaHtml}
      </div>
    `;
  }).join("");
};

window.loadReports = async () => {
  const params = new URLSearchParams({sort});
  const c = document.getElementById("fCity").value; if(c) params.set("city", c);
  const m = document.getElementById("fManuf").value; if(m) params.set("manufacturer", m);
  const f = document.getElementById("fFrom").value; if(f) params.set("date_from", f);
  const t = document.getElementById("fTo").value; if(t) params.set("date_to", t);
  
  const rows = await API("/api/reports?"+params.toString(), {headers: authHeaders()});
  render(rows);
};

window.exportExcel = async () => {
  const params = new URLSearchParams();
  const filters = {
    city: document.getElementById("fCity").value,
    manufacturer: document.getElementById("fManuf").value,
    date_from: document.getElementById("fFrom").value,
    date_to: document.getElementById("fTo").value,
  };
  Object.entries(filters).forEach(([key, value]) => {
    if(value) params.set(key, value);
  });

  const button = document.querySelector('.btn.secondary[onclick="exportExcel()"]');
  if(button) button.disabled = true;

  try {
    const response = await fetch("/api/export/excel?" + params.toString(), {
      headers: authHeaders(),
    });
    if(!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Ошибка экспорта (${response.status})`);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i)?.[1]
      || "albion.xlsx";
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = decodeURIComponent(filename.replace(/\"/g, ""));
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch(error) {
    toast(error.message || "Не удалось выгрузить Excel", true);
  } finally {
    if(button) button.disabled = false;
  }
};

(async () => {
  const me = await API("/api/me", {headers: authHeaders()}).catch(()=>null);
  if(!me || (me.role !== "admin" && me.role !== "director")){ location.href = "/"; return; }
  document.getElementById("whoami").textContent = me.login + " (" + me.role_label + ")";

  const cities = await API("/api/cities");
  const fc = document.getElementById("fCity");
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; fc.appendChild(o); });

  const manufs = await API("/api/manufacturers");
  const fm = document.getElementById("fManuf");
  manufs.forEach(m => { const o=document.createElement("option"); o.value=m; o.textContent=m; fm.appendChild(o); });

  document.getElementById("sortRow").addEventListener("click", e => {
    if(!e.target.dataset.sort) return;
    sort = e.target.dataset.sort;
    [...document.querySelectorAll("#sortRow .chip")].forEach(c=>c.classList.remove("active"));
    e.target.classList.add("active");
    loadReports();
  });

  document.getElementById("logout").onclick = () => logoutCurrentSession("/");
  loadReports();
})();
