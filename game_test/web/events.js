window.GameEvents = {
  createEventSource(path, onMessage, onError) {
    const es = new EventSource(path);
    es.onmessage = onMessage;
    es.onerror = onError;
    return es;
  },
};
