// @ts-check

(function initGameEventAdapter() {
  window.GameAdapters = window.GameAdapters || {};

  window.GameAdapters.events = {
    /**
     * @param {string} path
     * @param {(event: MessageEvent<string>) => void} onMessage
     * @param {(event: Event) => void} [onError]
     * @returns {EventSource}
     */
    createEventSource(path, onMessage, onError) {
      const eventSource = new EventSource(path);
      eventSource.onmessage = onMessage;
      eventSource.onerror = onError || null;
      return eventSource;
    },
  };
})();
