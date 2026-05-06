// src/main.js
// Initializes OMC connection, adapter, and controller.

import { OmcClient } from './omc-client.js';
import { EventAdapter } from './event-adapter.js';
import { PipelineController } from './pipeline-controller.js';

const OMC_URL = 'http://localhost:8000';

let client;
let adapter;
let controller;

async function init() {
  client = new OmcClient(OMC_URL);
  adapter = new EventAdapter();
  controller = new PipelineController(adapter);

  client.onEvent((event) => adapter.process(event));

  window._omcClient = client;
  window._controller = controller;

  try {
    await client.connect();
    setConnectionStatus(true);
    addEvent('stag', 'Connected to OMC backend.');
    document.getElementById('dirStatus').textContent = 'Connected — ready';
  } catch (err) {
    setConnectionStatus(false);
    addEvent('stag', `Connection failed: ${err.message || 'unreachable'}. Running in demo mode.`);
    document.getElementById('dirStatus').textContent = 'Offline — demo mode';
    return false;
  }

  client.ws.addEventListener('close', () => setConnectionStatus(false));
  return true;
}

async function launchPipeline(topic) {
  const btn = document.getElementById('launchBtn');

  if (!client || !client.ws || client.ws.readyState !== WebSocket.OPEN) {
    startDemo();
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Submitting...';
  document.getElementById('dirStatus').textContent = 'Submitting task...';
  document.getElementById('meetingsArea').innerHTML = '';
  postNotice(`Research topic: <strong>${topic}</strong>`, 'info');

  try {
    const result = await client.submitTask(topic, {
      projectName: `research-${Date.now()}`,
    });

    if (result.error) {
      postNotice(`Error: ${result.error}`, 'error');
      btn.textContent = 'Launch Pipeline';
      btn.disabled = false;
      document.getElementById('dirStatus').textContent = 'Error — try again';
      return;
    }

    window._currentProjectId = result.project_id;
    postNotice('Task accepted. Research Director delegating to team...', 'ok');
    document.getElementById('dirStatus').textContent = 'Pipeline running...';
    btn.textContent = 'Running...';
  } catch (err) {
    postNotice(`Submit failed: ${err.message}`, 'error');
    btn.textContent = 'Launch Pipeline';
    btn.disabled = false;
    document.getElementById('dirStatus').textContent = 'Submit failed — retry';
  }
}

function setConnectionStatus(connected) {
  const el = document.getElementById('connStatus');
  if (!el) return;
  el.className = connected ? 'conn-status connected' : 'conn-status';
  el.querySelector('.conn-label').textContent = connected ? 'Connected' : 'Offline';
}

window.launchPipeline = launchPipeline;
window.resumeBreakpoint = (feedback) => controller.resumeBreakpoint(feedback);

document.addEventListener('DOMContentLoaded', () => {
  init();
});
