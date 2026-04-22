(function initGameApiShell() {
  const getClient = () => window.GameAdapters?.apiClient;
  function requireClient() {
    const client = getClient();
    if (!client) throw new Error('Game API adapter not loaded yet');
    if (shell.__apiPrefix && !client.API) client.API = shell.__apiPrefix;
    return client;
  }
  const shell = window.GameApi || {};
  Object.defineProperty(shell, 'API', {
    configurable: true,
    enumerable: true,
    get() {
      return getClient()?.API ?? shell.__apiPrefix ?? '';
    },
    set(value) {
      const nextValue = String(value || '');
      shell.__apiPrefix = nextValue;
      const client = getClient();
      if (client) {
        client.API = nextValue;
      }
    },
  });
  shell.api = (method, path, body) => requireClient().api(method, path, body);
  shell.getJson = (path, query) => requireClient().getJson(path, query);
  window.GameApi = shell;
})();
