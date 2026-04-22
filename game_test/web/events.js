(function initGameEventsShell() {
  window.GameEvents = {
    createEventSource(path, onMessage, onError) {
      const events = window.GameAdapters?.events;
      if (!events) {
        throw new Error('Game event adapter not loaded yet');
      }
      return events.createEventSource(path, onMessage, onError);
    },
  };
})();
