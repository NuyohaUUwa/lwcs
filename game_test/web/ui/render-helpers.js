(function initGameUiRenderHelpers() {
  window.GameUi = window.GameUi || {};

  const render = {
    escHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[ch]));
    },
    escAttr(value) {
      return render.escHtml(value);
    },
    highlightChinese(value) {
      return String(value || '').replace(/([\u4e00-\u9fa5]+)/g, '<span class="hl-cn">$1</span>');
    },
  };

  window.GameUi.render = render;
})();
