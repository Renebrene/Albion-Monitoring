(() => {
  window.readFeedFilterParams = keys => {
    const params = new URLSearchParams(window.location.search);
    const result = {};

    keys.forEach(key => {
      const value = params.get(key);
      if (value !== null) result[key] = value;
    });

    return result;
  };

  window.writeFeedFilterParams = (values, defaults = {}) => {
    const params = new URLSearchParams(window.location.search);

    Object.entries(values).forEach(([key, value]) => {
      const normalized = value == null ? "" : String(value);
      const defaultValue = defaults[key] == null ? "" : String(defaults[key]);

      if (normalized && normalized !== defaultValue) {
        params.set(key, normalized);
      } else {
        params.delete(key);
      }
    });

    const query = params.toString();
    const url = window.location.pathname + (query ? `?${query}` : "") + window.location.hash;
    window.history.replaceState(null, "", url);
  };
})();
