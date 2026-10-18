(() => {
  const mediaUrlPattern = /^\/(?:uploads|api\/media)\//;
  const objectUrls = new Map();
  const pending = new Map();

  function apiUrl(url) {
    if (url.startsWith("/uploads/")) {
      let filename = url.slice("/uploads/".length);
      try { filename = decodeURIComponent(filename); } catch (_) {}
      return `/api/media/${filename.split("/").map(encodeURIComponent).join("/")}`;
    }
    return url;
  }

  function tokenHeaders() {
    const token = localStorage.getItem("token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  function revokeElementUrl(element) {
    const objectUrl = objectUrls.get(element);
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrls.delete(element);
    }
  }

  async function fetchMediaBlob(sourceUrl) {
    const apiPath = apiUrl(sourceUrl);
    const key = `${apiPath}:${localStorage.getItem("token") || ""}`;
    let request = pending.get(key);
    if (!request) {
      request = fetch(apiPath, {
        headers: tokenHeaders(),
        cache: "no-store",
      }).then(response => {
        if (response.status === 401) {
          localStorage.clear();
          window.location.href = "/";
          throw new Error("Сессия истекла");
        }
        if (!response.ok) throw new Error(`Не удалось загрузить медиа (${response.status})`);
        return response.blob();
      }).finally(() => pending.delete(key));
      pending.set(key, request);
    }
    return request;
  }

  async function loadMediaUrl(element, sourceUrl, attribute) {
    try {
      const blob = await fetchMediaBlob(sourceUrl);
      if (!element.isConnected) return null;
      const objectUrl = URL.createObjectURL(blob);
      revokeElementUrl(element);
      objectUrls.set(element, objectUrl);
      element.setAttribute(attribute, objectUrl);
      element.dataset.mediaLoaded = "true";
      element.dataset.mediaObjectUrl = objectUrl;
      element.dispatchEvent(new Event("authenticatedmediaready"));
      return objectUrl;
    } catch (error) {
      if (element.isConnected) {
        element.dataset.mediaError = "true";
        element.dispatchEvent(new CustomEvent("authenticatedmediaerror", { detail: error }));
      }
      throw error;
    }
  }

  function disposeTree(root) {
    const elements = [];
    if (root instanceof Element && objectUrls.has(root)) elements.push(root);
    if (root.querySelectorAll) {
      elements.push(...root.querySelectorAll("img, video, audio, source, a"));
    }
    elements.forEach(revokeElementUrl);
  }

  function scan(root) {
    const elements = [];
    if (root instanceof Element && root.matches("img[src], video[src], audio[src], source[src], a[href]")) {
      elements.push(root);
    }
    if (root.querySelectorAll) {
      elements.push(...root.querySelectorAll("img[src], video[src], audio[src], source[src], a[href]"));
    }
    for (const element of elements) {
      const attribute = element.tagName === "A" ? "href" : "src";
      const original = element.getAttribute(attribute);
      if (!original || !mediaUrlPattern.test(original)) continue;
      if (element.dataset.mediaSource !== original) {
        revokeElementUrl(element);
        delete element.dataset.mediaLoaded;
        delete element.dataset.mediaError;
        element.dataset.mediaSource = original;
      }
      if (element.dataset.mediaLoaded === "true" || element.dataset.mediaLoading === "true") continue;
      element.dataset.mediaLoading = "true";
      loadMediaUrl(element, original, attribute)
        .catch(() => {})
        .finally(() => {
          delete element.dataset.mediaLoading;
        });
    }
  }

  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "childList") {
        record.removedNodes.forEach(disposeTree);
        record.addedNodes.forEach(scan);
      }
      if (record.type === "attributes") scan(record.target);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    scan(document);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "href"],
    });
  }, { once: true });

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : event.target.parentElement;
    const link = target?.closest("a[href]");
    if (!link) return;
    const url = link.dataset.mediaSource || link.getAttribute("href");
    if (!url || !mediaUrlPattern.test(url) || link.dataset.mediaLoaded === "true") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const popup = link.hasAttribute("download") ? null : window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    loadMediaUrl(link, url, "href").then(objectUrl => {
      if (objectUrl && link.hasAttribute("download")) {
        const download = document.createElement("a");
        download.href = objectUrl;
        download.download = link.download || url.split("/").pop() || "media";
        document.body.appendChild(download);
        download.click();
        download.remove();
      } else if (objectUrl && popup && !popup.closed) {
        popup.location.replace(objectUrl);
      } else if (popup && !popup.closed) {
        popup.close();
      }
    }).catch(() => {
      if (popup && !popup.closed) popup.close();
    });
  }, true);

  // Open handlers in older renderers may call window.open(url). Open a blank
  // window synchronously to retain popup permission, then navigate to the Blob.
  const nativeOpen = window.open.bind(window);
  window.open = (url, target, features) => {
    if (typeof url !== "string" || !mediaUrlPattern.test(url)) {
      return nativeOpen(url, target, features);
    }
    const popup = nativeOpen("about:blank", target || "_blank", features);
    if (!popup) return null;
    fetchMediaBlob(url).then(blob => {
      const objectUrl = URL.createObjectURL(blob);
      objectUrls.set(popup, objectUrl);
      if (!popup.closed) popup.location.replace(objectUrl);
      else revokeElementUrl(popup);
    }).catch(() => {
      if (!popup.closed) popup.close();
    });
    return popup;
  };

  window.loadAuthenticatedMedia = loadMediaUrl;
  window.revokeAuthenticatedMedia = revokeElementUrl;
  window.addEventListener("beforeunload", () => {
    for (const objectUrl of objectUrls.values()) URL.revokeObjectURL(objectUrl);
    objectUrls.clear();
  }, { once: true });
})();
