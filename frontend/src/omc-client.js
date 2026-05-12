// src/omc-client.js
// Connects to OMC backend, exposes event stream + command methods.

export class OmcClient {
  constructor(baseUrl = window.location.origin) {
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
    if (config.startStage) form.append('start_stage', String(config.startStage));
    if (config.endStage) form.append('end_stage', String(config.endStage));
    if (config.stageAssignments) {
      form.append('stage_assignments', JSON.stringify(config.stageAssignments));
    }
    if (config.files && config.files.length > 0) {
      for (const f of config.files) form.append('files', f);
    }

    const res = await fetch(`${this.baseUrl}/api/ceo/task`, { method: 'POST', body: form });
    return res.json();
  }

  async getBootstrap() {
    const res = await fetch(`${this.baseUrl}/api/bootstrap`);
    return res.json();
  }

  async listProjects() {
    const res = await fetch(`${this.baseUrl}/api/projects`);
    return res.json();
  }

  async getPipelineStatus(projectId) {
    const res = await fetch(`${this.baseUrl}/api/pipeline/${projectId}/status`);
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

  async resumeAfterBreakpoint(projectId, stageId, userFeedback = '') {
    const res = await fetch(`${this.baseUrl}/api/task/${projectId}/followup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        message: userFeedback || `Stage ${stageId} approved. Continue.`,
      }),
    });
    return res.json();
  }

  async sendSessionMessage(projectId, text) {
    const res = await fetch(`${this.baseUrl}/api/ceo/sessions/${projectId}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.json();
  }

  async resumePipelineBreakpoint(projectId, stage, feedback = '') {
    const res = await fetch(`${this.baseUrl}/api/pipeline/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, stage, feedback }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.json();
  }

  async sendOneOnOneMessage(employeeId, message) {
    const res = await fetch(`${this.baseUrl}/api/oneonone/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ employee_id: employeeId, message }),
    });
    return res.json();
  }
}
