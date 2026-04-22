(function initPublicAppShell() {
  if (window.GameBoot?.bootstrapPromise) {
    return;
  }
  const script = document.createElement('script');
  script.src = '/boot/bootstrap.js';
  script.async = false;
  document.head.appendChild(script);
})();
