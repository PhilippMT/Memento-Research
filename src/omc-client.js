// src/omc-client.js
// Connects to OMC backend, exposes event stream + command methods.

export class OmcClient {
  constructor(baseUrl = 'http://localhost:8000') {
    this.baseUrl = baseUrl;
    this.ws = null;
    this.listeners = [];
  }

  // --- WebSocket ---

  connect() {
    const wsUrl = this.baseUrl.replace(/^http/, 'ws') + '/ws';
    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.listeners.forEach(fn => fn(data));
    };

    this.ws.onclose = () => {
      // Reconnect after 3s
      setTimeout(() => this.connect(), 3000);
    };

    return new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = (err) => reject(err);
    });
  }

  onEvent(fn) {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter(l => l !== fn); };
  }

  // --- REST Commands ---

  async submitTask(topic, config = {}) {
    const form = new FormData();
    form.append('task', topic);
    if (config.projectName) form.append('project_name', config.projectName);
    form.append('mode', 'standard');

    const res = await fetch(`${this.baseUrl}/api/ceo/task`, { method: 'POST', body: form });
    return res.json();
  }

  async getBootstrap() {
    const res = await fetch(`${this.baseUrl}/api/bootstrap`);
    return res.json();
  }

  async getProjectTree(projectId) {
    const res = await fetch(`${this.baseUrl}/api/projects/${projectId}/tree`);
    return res.json();
  }

  async sendMeetingChat(roomId, message) {
    const res = await fetch(`${this.baseUrl}/api/meeting/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomId, message }),
    });
    return res.json();
  }
}
