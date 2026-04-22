// @ts-check

(function initGameBootLoader() {
  if (window.GameBoot?.bootstrapPromise) {
    return;
  }

  /**
   * @param {string} src
   * @returns {Promise<void>}
   */
  function loadClassicScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing) {
        if (existing.dataset.loaded === 'true') {
          resolve();
          return;
        }
        existing.addEventListener('load', () => resolve(), { once: true });
        existing.addEventListener('error', () => reject(new Error(`Failed to load ${src}`)), { once: true });
        return;
      }

      const script = document.createElement('script');
      script.src = src;
      script.async = false;
      script.addEventListener('load', () => {
        script.dataset.loaded = 'true';
        resolve();
      }, { once: true });
      script.addEventListener('error', () => reject(new Error(`Failed to load ${src}`)), { once: true });
      document.head.appendChild(script);
    });
  }

  /** @type {readonly string[]} */
  const bootSources = [
    '/ui/render-helpers.js',
    '/ui/messages.js',
    '/adapters/http.js',
    '/adapters/events.js',
    '/boot/app-runtime.js',
  ];

  const boot = window.GameBoot || (window.GameBoot = {});
  boot.loadClassicScript = loadClassicScript;
  boot.bootstrapPromise = bootSources.reduce(
    (promise, src) => promise.then(() => loadClassicScript(src)),
    Promise.resolve(),
  );
})();
