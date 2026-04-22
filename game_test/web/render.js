(function initGameRenderShell() {
  function getRender() {
    return window.GameUi?.render;
  }

  window.GameRender = {
    escHtml(value) {
      const render = getRender();
      if (!render) {
        throw new Error('Game UI render helpers not loaded yet');
      }
      return render.escHtml(value);
    },
    escAttr(value) {
      const render = getRender();
      if (!render) {
        throw new Error('Game UI render helpers not loaded yet');
      }
      return render.escAttr(value);
    },
    highlightChinese(value) {
      const render = getRender();
      if (!render) {
        throw new Error('Game UI render helpers not loaded yet');
      }
      return render.highlightChinese(value);
    },
  };
})();
