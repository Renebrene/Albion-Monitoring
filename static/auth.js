window.debounce = (fn, wait = 200) => {
  let timer = null;

  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };

  wrapped.cancel = () => {
    clearTimeout(timer);
    timer = null;
  };

  return wrapped;
};

window.escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#039;",
}[ch]));

// Новые роли используют функциональный профиль базовой роли.
window.getBaseRole = role => ({
  staff: "service",
  administrator: "admin",
  curator: "director",
}[role] || role);

// Единый JSON-запрос: Bearer, 401 → logout, читаемое сообщение об ошибке.
window.apiFetch = async (url, options = {}) => {
  const token = localStorage.getItem("token");
  const headers = { ...(options.headers || {}) };

  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    localStorage.clear();
    window.location.href = "/";
    throw new Error("Сессия истекла");
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail)
      ? detail.map(item => item.msg || String(item)).join("; ")
      : detail || `Ошибка ${response.status}`;
    throw new Error(message);
  }

  return data;
};

window.logoutCurrentSession = async (redirectTo = "/") => {
  const token = localStorage.getItem("token");

  try {
    if (token) {
      const response = await fetch("/api/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        throw new Error(`Logout failed: ${response.status}`);
      }
    }
  } catch (error) {
    console.warn("Could not close server session:", error);
  } finally {
    localStorage.clear();
    window.location.href = redirectTo;
  }
};
