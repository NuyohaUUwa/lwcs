window.GameRender = {
  escHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  },
  escAttr(s) {
    return this.escHtml(s);
  },
  highlightChinese(htmlStr) {
    return String(htmlStr || "").replace(/([\u4e00-\u9fa5]+)/g, '<span class="hl-cn">$1</span>');
  },
};
