window.GameControllers = {
  async performJsonRequest(method, path, body) {
    return window.GameApi.api(method, path, body);
  },
};
