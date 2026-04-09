window.GameApi = {
  API: "",
  async api(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const response = await fetch(path.startsWith("http") ? path : this.API + path, opts);
    return response.json();
  },
};
