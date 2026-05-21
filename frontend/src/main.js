// src/main.js
// Initializes OMC connection, adapter, and controller.

import { OmcClient } from './omc-client.js';
import { EventAdapter } from './event-adapter.js';
import { PipelineController } from './pipeline-controller.js';
import { tryRenderLcg, setupLcgHover } from './lcg-renderer.js';

const OMC_URL = window.location.origin;

window._tryRenderLcg = tryRenderLcg;
setupLcgHover();

let client;
let adapter;
let controller;
const _knownProjectIds = new Set();
const _eventBuffers = new Map();

function _setStatus(text) {
  const el = document.getElementById('pipelineStatus');
  if (el) el.textContent = text;
}

async function init() {
  client = new OmcClient(OMC_URL);
  adapter = new EventAdapter();
  controller = new PipelineController(adapter);

  client.onEvent((event) => {
    if (_eventShouldRefreshEmployees(event)) {
      refreshEmployees();
    }

    const pid = event.payload && (event.payload.project_id || event.payload.context_id);
    if (pid) {
      const basePid = pid.split('/')[0];
      if (!_knownProjectIds.has(basePid)) return;

      const trackedPid = _resolveProjectId(pid);
      if (window._activeProjectId && trackedPid && trackedPid !== window._activeProjectId) {
        if (!_eventBuffers.has(trackedPid)) _eventBuffers.set(trackedPid, []);
        _eventBuffers.get(trackedPid).push(event);
        if (window._getProject) {
          const proj = window._getProject(trackedPid);
          proj.status = 'processing';
          if (window._renderProjectSidebar) window._renderProjectSidebar();
        }
        return;
      }

      if (!window._activeProjectId) {
        if (window._routeToProject) window._routeToProject(pid);
      }
    }
    adapter.process(event);
  });

  window._omcClient = client;
  window._controller = controller;

  try {
    await client.connect();
    _setConnectionStatus(true);
    _setStatus('Connected');

    await refreshEmployees();

    await loadProjects();
    await restoreLastSession();
  } catch (err) {
    _setConnectionStatus(false);
    _setStatus('Offline');
    return false;
  }

  client.ws.addEventListener('close', () => _setConnectionStatus(false));
  return true;
}

function _eventShouldRefreshEmployees(event) {
  if (!event) return false;
  if (event.type === 'employee_hired') return true;
  const payload = event.payload || {};
  return Array.isArray(payload.employees_added) && payload.employees_added.length > 0;
}

async function refreshEmployees() {
  if (!client) return;
  try {
    const boot = await client.getBootstrap();
    if (!boot || !Array.isArray(boot.employees)) return;
    window._employees = boot.employees.map(e => ({
      employee_number: e.employee_number,
      name: e.name || e.nickname,
      skills: e.skills || [],
      role: e.role || '',
    }));
    _pruneStageAssignments();
    if (window._renderRangeSelector) window._renderRangeSelector();
  } catch (e) {
    // Employee refresh is best-effort; existing assignments remain usable offline.
  }
}

function _pruneStageAssignments() {
  if (!window._employees || !window._stageAssignments) return;
  const validEmployees = new Set(window._employees.map(e => e.employee_number));
  for (const [stageId, employeeId] of Object.entries(window._stageAssignments)) {
    if (employeeId && !validEmployees.has(employeeId)) {
      delete window._stageAssignments[stageId];
    }
  }
}

async function loadProjects() {
  if (!client) return;
  try {
    const res = await client.listProjects();
    const projects = res.projects || [];
    for (const p of projects) {
      if (p.project_id) _knownProjectIds.add(p.project_id);
    }
    if (window.renderProjectList) {
      window.renderProjectList(projects);
    }
  } catch (e) {
    // silently fail
  }
}

async function launchPipeline(topic) {
  const btn = document.getElementById('launchBtn');

  if (!client || !client.ws || client.ws.readyState !== WebSocket.OPEN) {
    if (window.postNotice) window.postNotice('Backend not connected.', 'error');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Submitting...';

  try {
    const startStage = window._getRangeStart ? window._getRangeStart() : 1;
    const endStage = window._getRangeEnd ? window._getRangeEnd() : 9;
    const fileInput = document.getElementById('contextFiles');
    const files = fileInput ? Array.from(fileInput.files) : [];

    const stageAssignments = window._getStageAssignments ? window._getStageAssignments() : null;

    const result = await client.submitTask(topic, {
      projectName: `research-${Date.now()}`,
      startStage,
      endStage,
      files,
      stageAssignments,
    });

    if (result.error) {
      if (window.postNotice) window.postNotice(`Error: ${result.error}`, 'error');
      btn.textContent = 'Launch Pipeline';
      btn.disabled = false;
      return;
    }

    const pid = result.project_id;
    _knownProjectIds.add(pid);
    const sessionId = result.iteration_id ? `${pid}/${result.iteration_id}` : pid;

    if (window._getProject) {
      const proj = window._getProject(sessionId);
      proj.task = topic;
      proj.status = 'processing';
      proj.sessionId = sessionId;
    }

    if (window.switchProject) {
      window.switchProject(sessionId);
    }

    document.getElementById('meetingsArea').innerHTML = '';
    document.getElementById('heroSection').style.display = 'none';
    if (typeof showPipelineBar === 'function') showPipelineBar(startStage, endStage);

    window._currentProjectId = pid;
    window._currentSessionId = sessionId;

    await loadProjects();

    if (window.postNotice) {
      window.postNotice(`Research topic: <strong>${_escHtml(topic)}</strong>`, 'info');
      window.postNotice('Pipeline started. Stage 1 dispatched.', 'ok');
    }
    btn.textContent = 'Launch Pipeline';
    btn.disabled = false;
  } catch (err) {
    if (window.postNotice) window.postNotice(`Submit failed: ${err.message}`, 'error');
    btn.textContent = 'Launch Pipeline';
    btn.disabled = false;
  }
}

function _setConnectionStatus(connected) {
  const el = document.getElementById('connStatus');
  if (!el) return;
  el.className = connected ? 'conn-status connected' : 'conn-status';
  const label = el.querySelector('.conn-label');
  if (label) label.textContent = connected ? 'Connected' : 'Offline';
}

function _escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _resolveProjectId(rawPid) {
  if (!rawPid) return null;
  const basePid = rawPid.split('/')[0];
  if (window._projects) {
    for (const [pid] of window._projects) {
      if (pid === rawPid || pid.startsWith(basePid) || rawPid.startsWith(pid.split('/')[0])) {
        return pid;
      }
    }
  }
  return null;
}

function replayBufferedEvents(pid) {
  const buf = _eventBuffers.get(pid);
  if (!buf || buf.length === 0) return;
  _eventBuffers.delete(pid);
  for (const event of buf) {
    adapter.process(event);
  }
}

// Apply a pipeline-status response to the current view: pipeline bar,
// stage cards, workspace files, breakpoint dialog if at gate. Pure
// rendering — no network. Shared by restoreLastSession (auto-restore on
// page load) and switchProject (manual click on a project the in-memory
// state knows nothing about, e.g. after a hard refresh).
function _applyPipelineStatusToView(pid, status) {
  if (!status) return;

  // Hide hero, show pipeline bar
  const hero = document.getElementById('heroSection');
  if (hero) hero.style.display = 'none';
  if (typeof showPipelineBar === 'function') showPipelineBar(status.start_stage || 1, status.end_stage || 9);

  // Restore pipeline bar stage states
  const currentStage = status.current_stage || 1;
  const phase = status.phase || 'producer';
  for (let i = 1; i < currentStage; i++) {
    setStage(i, 'done');
  }
  if (phase === 'gate') {
    setStage(currentStage, 'gate');
  } else if (phase === 'critic') {
    setStage(currentStage, 'reviewing');
  } else if (phase === 'done') {
    setStage(currentStage, 'done');
  } else {
    setStage(currentStage, 'running');
  }

  // Restore stage result cards
  for (const [sid, result] of Object.entries(status.stage_results || {})) {
    const stageId = parseInt(sid);
    const stageDef = (status.stages || [])[stageId - 1];
    const name = stageDef ? stageDef.name : `Stage ${stageId}`;
    const cardId = `stage${stageId}`;
    getStageCard(cardId, `Stage ${stageId} — ${name}`, name, name.slice(0, 2).toUpperCase());
    updateProducer(cardId, result);
    if (stageId < currentStage) {
      setCardStatus(cardId, 'done');
    } else if (stageId === currentStage && (phase === 'gate' || phase === 'critic')) {
      if (status.critic_result) {
        updateCritic(cardId, status.critic_result);
      }
    }
  }

  // Restore workspace files
  if (status.workspace_files && typeof addWorkspaceFile === 'function') {
    for (const f of status.workspace_files) {
      addWorkspaceFile(f);
    }
  }

  // If pipeline is at gate, show breakpoint dialog
  if (phase === 'gate' && window._controller) {
    window._controller.pausedStageId = currentStage;
    window._controller.currentStage = currentStage;
    const stageDef = (status.stages || [])[currentStage - 1];
    if (typeof openBreakpointDialog === 'function') {
      openBreakpointDialog(currentStage, stageDef ? stageDef.name : `Stage ${currentStage}`);
    }
  }

  // Update project metadata
  if (window._getProject) {
    const proj = window._getProject(pid);
    proj.task = status.topic || proj.task || '';
  }
}

// Fetch fresh pipeline state from the backend and render it. Used by
// switchProject when clicking a project the browser has no in-memory
// state for (post-refresh, or projects beyond the auto-restored one).
async function restoreProjectView(pid) {
  if (!client || !pid) return;
  const basePid = pid.split('/')[0];
  try {
    const status = await client.getPipelineStatus(basePid);
    if (!status || status.error) return;
    _applyPipelineStatusToView(pid, status);
  } catch (e) {
    // Non-critical — view stays in whatever state it was in
  }
}

async function restoreLastSession() {
  if (!client) return;
  try {
    const res = await client.listProjects();
    const projects = res.projects || [];
    if (projects.length === 0) return;

    // Pick the most recent processing/completed project
    const active = projects.find(p => p.latest_iter_status === 'processing')
      || projects[0];
    if (!active || !active.project_id) return;

    const pid = active.project_id;
    _knownProjectIds.add(pid);

    // Get pipeline status
    const status = await client.getPipelineStatus(pid);
    if (status.error) return;

    // Set as active project
    const sessionId = pid;
    if (window._getProject) {
      const proj = window._getProject(sessionId);
      proj.task = status.topic || active.task || '';
      proj.status = active.latest_iter_status || 'processing';
    }

    window._currentProjectId = pid;
    window._currentSessionId = sessionId;
    window._activeProjectId = sessionId;

    _applyPipelineStatusToView(sessionId, status);

    // Update sidebar
    if (window._renderProjectSidebar) window._renderProjectSidebar();

  } catch (e) {
    // Non-critical — page still works without restore
  }
}

window.launchPipeline = launchPipeline;
window.loadProjects = loadProjects;
window.replayBufferedEvents = replayBufferedEvents;
window.restoreProjectView = restoreProjectView;
window.resumeBreakpoint = (feedback, isRevision) => controller.resumeBreakpoint(feedback, isRevision);

document.addEventListener('DOMContentLoaded', () => {
  init();
});
