// @ts-check

(function initGameHttpAdapter() {
  window.GameAdapters = window.GameAdapters || {};

  /**
   * @typedef {Record<string, string | number | boolean | null | undefined>} QueryRecord
   */

  const apiClient = {
    API: typeof window.GameApi?.API === 'string' ? window.GameApi.API : '',
    /**
     * @param {string} method
     * @param {string} path
     * @param {unknown} [body]
     * @returns {Promise<any>}
     */
    async api(method, path, body) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body !== undefined) {
        opts.body = JSON.stringify(body);
      }
      const response = await fetch(path.startsWith('http') ? path : this.API + path, opts);
      return response.json();
    },
    /**
     * @param {string} path
     * @param {URLSearchParams | QueryRecord} [query]
     * @returns {Promise<any>}
     */
    async getJson(path, query) {
      const params = query instanceof URLSearchParams ? query : new URLSearchParams();
      if (!(query instanceof URLSearchParams) && query) {
        Object.entries(query).forEach(([key, value]) => {
          if (value !== undefined && value !== null && value !== '') {
            params.set(key, String(value));
          }
        });
      }
      const suffix = params.toString();
      const url = path.startsWith('http') ? path : this.API + path;
      const response = await fetch(suffix ? `${url}?${suffix}` : url);
      return response.json();
    },
  };

  window.GameAdapters.apiClient = apiClient;
})();
