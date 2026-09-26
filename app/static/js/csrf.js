// Supply the session-bound CSRF token for same-origin fetch mutations only.
(() => {
  const original = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (url.origin === window.location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
      init = { ...init, headers };
    }
    return original(input, init);
  };
})();
