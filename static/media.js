(() => {
  const mediaUrlPattern = /^\/(?:uploads|api\/media)\//;
  const IMAGE_PLACEHOLDER =
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='92' height='72'%3E%3C/svg%3E";
  const objectUrls = new Map();
  const pending = new Map();
  const observed = new WeakMap();

  // Bounded in-memory blob cache. Media names are immutable, so a blob can be
  // reused across feed re-renders without another authenticated request.
  const blobCache = new Map();
  const MAX_CACHE_ENTRIES = 80;
  const MAX_CACHE_BYTES = 64 * 1024 * 1024;
  const MAX_CACHE_ITEM_BYTES = 24 * 1024 * 1024;
  let cacheBytes = 0;

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

  function cacheKey(apiPath) {
    return `${apiPath}:${localStorage.getItem("token") || ""}`;
  }

  function getCachedBlob(key) {
    const entry = blobCache.get(key);
    if (!entry) return null;
    // Refresh LRU position.
    blobCache.delete(key);
    blobCache.set(key, entry);
    return entry.blob;
  }

  function putCachedBlob(key, blob) {
    if (!blob || blob.size > MAX_CACHE_ITEM_BYTES) return;

    const existing = blobCache.get(key);
    if (existing) {
      cacheBytes -= existing.blob.size;
      blobCache.delete(key);
    }

    blobCache.set(key, { blob, size: blob.size });
    cacheBytes += blob.size;

    while (
      blobCache.size > MAX_CACHE_ENTRIES ||
      (cacheBytes > MAX_CACHE_BYTES && blobCache.size > 1)
    ) {
      const oldest = blobCache.keys().next();
      if (oldest.done) break;
      const entry = blobCache.get(oldest.value);
      blobCache.delete(oldest.value);
      cacheBytes -= entry.size;
    }
  }

  function revokeElementUrl(element) {
    const objectUrl = objectUrls.get(element);
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrls.delete(element);
    }
  }

  function disconnectObserver(element) {
    const observer = observed.get(element);
    if (observer) {
      observer.disconnect();
      observed.delete(element);
    }
  }

  async function fetchMediaBlob(sourceUrl) {
    const apiPath = apiUrl(sourceUrl);
    const key = cacheKey(apiPath);

    const cached = getCachedBlob(key);
    if (cached) return cached;

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
      }).then(blob => {
        putCachedBlob(key, blob);
        return blob;
      }).finally(() => pending.delete(key));
      pending.set(key, request);
    }
    return request;
  }

  async function loadMediaUrl(element, sourceUrl, attribute) {
    const apiPath = apiUrl(sourceUrl);
    element.dataset.mediaLoading = apiPath;

    try {
      const blob = await fetchMediaBlob(sourceUrl);

      // The source may have changed while the request was in flight.
      if (!element.isConnected || element.dataset.mediaLoading !== apiPath) {
        return null;
      }

      const objectUrl = URL.createObjectURL(blob);
      revokeElementUrl(element);
      objectUrls.set(element, objectUrl);
      element.setAttribute(attribute, objectUrl);
      element.dataset.mediaLoaded = apiPath;
      element.dataset.mediaObjectUrl = objectUrl;

      if (element.tagName === "SOURCE" && element.parentElement?.load) {
        element.parentElement.load();
      }

      element.dispatchEvent(new Event("authenticatedmediaready"));
      return objectUrl;
    } catch (error) {
      if (element.isConnected && element.dataset.mediaLoading === apiPath) {
        element.dataset.mediaError = "true";
        element.dispatchEvent(new CustomEvent("authenticatedmediaerror", { detail: error }));
      }
      throw error;
    } finally {
      if (element.dataset.mediaLoading === apiPath) {
        delete element.dataset.mediaLoading;
      }
    }
  }

  function loadElement(element) {
    if (!element.isConnected) return;
    const sourceUrl = element.dataset.mediaSource;
    if (!sourceUrl || !mediaUrlPattern.test(sourceUrl)) return;

    const attribute = element.tagName === "A" ? "href" : "src";
    loadMediaUrl(element, sourceUrl, attribute).catch(() => {});
  }

  function observeElement(element) {
    if (observed.has(element)) return;

    if (typeof IntersectionObserver === "undefined") {
      loadElement(element);
      return;
    }

    // <source> is not rendered, so observe its parent <video> instead.
    const target =
      element.tagName === "SOURCE" && element.parentElement
        ? element.parentElement
        : element;

    // rootMargin loads media slightly before it becomes visible. Elements
    // inside a collapsed card (display:none) stay deferred until expansion.
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.disconnect();
      observed.delete(element);
      loadElement(element);
    }, { rootMargin: "300px 0px" });

    observer.observe(target);
    observed.set(element, observer);
  }

  function prepareElement(element) {
    const attribute = element.tagName === "A" ? "href" : "src";
    const original = element.getAttribute(attribute);
    if (!original || !mediaUrlPattern.test(original)) return;

    const apiPath = apiUrl(original);

    if (element.dataset.mediaSource !== original) {
      revokeElementUrl(element);
      delete element.dataset.mediaLoaded;
      delete element.dataset.mediaError;
      element.dataset.mediaSource = original;
    }

    if (element.dataset.mediaLoaded === apiPath) return;
    if (element.dataset.mediaLoading === apiPath) return;

    observeElement(element);

    // Prevent the browser from requesting an authenticated URL without
    // Bearer. The authorized blob URL is assigned after the fetch completes.
    // Images get a transparent placeholder so they keep their layout box and
    // can still trigger IntersectionObserver.
    if (attribute === "src") {
      if (element.tagName === "IMG") {
        element.setAttribute("src", IMAGE_PLACEHOLDER);
      } else {
        element.removeAttribute("src");
      }
    }
  }

  function disposeTree(root) {
    const elements = [];
    if (root instanceof Element) elements.push(root);
    if (root.querySelectorAll) {
      elements.push(...root.querySelectorAll("img, video, audio, source, a"));
    }
    elements.forEach(element => {
      disconnectObserver(element);
      revokeElementUrl(element);
    });
  }

  function scan(root) {
    if (!(root instanceof Element) && !root?.querySelectorAll) return;
    const elements = [];
    if (root instanceof Element && root.matches("img[src], video[src], audio[src], source[src]")) {
      elements.push(root);
    }
    if (root.querySelectorAll) {
      elements.push(...root.querySelectorAll("img[src], video[src], audio[src], source[src]"));
    }
    elements.forEach(prepareElement);
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
      attributeFilter: ["src"],
    });
  }, { once: true });

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : event.target.parentElement;
    const link = target?.closest("a[href]");
    if (!link) return;
    const url = link.dataset.mediaSource || link.getAttribute("href");
    if (!url || !mediaUrlPattern.test(url)) return;

    const apiPath = apiUrl(url);
    if (link.dataset.mediaLoaded === apiPath) return;
    if (link.dataset.mediaLoading === apiPath) {
      event.preventDefault();
      return;
    }

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
    blobCache.clear();
    cacheBytes = 0;
  }, { once: true });
})();
