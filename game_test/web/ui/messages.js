(function initGameUiMessages() {
  window.GameUi = window.GameUi || {};

  window.GameUi.feedback = {
    showMsg(elId, text, type = 'info') {
      const el = document.getElementById(elId);
      if (!el) {
        return;
      }
      el.className = `msg msg-${type === 'ok' ? 'ok' : type === 'err' ? 'err' : 'info'}`;
      el.textContent = text;
    },
    clearMsg(elId) {
      const el = document.getElementById(elId);
      if (!el) {
        return;
      }
      el.className = '';
      el.textContent = '';
    },
  };
})();
