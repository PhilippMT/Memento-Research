// src/pipeline-controller.js
// Drives stage cards in response to domain events from EventAdapter.
// Uses: getStageCard(), updateProducer(), updateCritic(), setCardStatus(),
//       addCardConf(), postNotice() — all defined in index.html.

export class PipelineController {
  constructor(adapter) {
    this.adapter = adapter;
    this.currentStage = null;
    this.stageCardIds = {}; // stageId → card DOM id
    this.stageEtaStats = {}; // stageId -> stats object
    this.stageRuntime = {}; // stageId -> { startedAtMs, status }
    this._countdownTimer = null;
    this._statsTimer = null;

    adapter.on('stage_start', (e) => this.handleStageStart(e));
    adapter.on('meeting_message', (e) => this.handleMeetingMessage(e));
    adapter.on('stage_complete', (e) => this.handleStageComplete(e));
    adapter.on('stage_reviewing', (e) => this.handleStageReviewing(e));
    adapter.on('stage_failed', (e) => this.handleStageFailed(e));
    adapter.on('director_action', (e) => this.handleDirectorAction(e));
    adapter.on('system_event', (e) => this.handleSystemEvent(e));
    adapter.on('file_written', (e) => this.handleFileWritten(e));
    adapter.on('breakpoint_hit', (e) => this.handleBreakpointHit(e));

    this._refreshStageStats();
    this._statsTimer = setInterval(() => this._refreshStageStats(), 60000);
  }

  _cardId(stageId) {
    return this.stageCardIds[stageId] || `stage${stageId}`;
  }

  _ensureCard(stageId) {
    if (this.stageCardIds[stageId]) return this._cardId(stageId);
    const name = this._getProducerName(stageId);
    const initials = this._getInitials(name);
    const title = `Stage ${stageId} — ${this._getStageName(stageId)}`;
    const id = `stage${stageId}`;
    getStageCard(id, title, name, initials);
    this.stageCardIds[stageId] = id;
    return id;
  }

  handleStageStart({ stageId, stageName, employeeName, employeeId, roomName, participants, eta }) {
    if (!stageId) return;
    this.currentStage = stageId;
    if (typeof showPipelineBar === 'function') showPipelineBar();
    setStage(stageId, 'running');
    this.stageRuntime[stageId] = { startedAtMs: Date.now(), status: 'running' };
    if (eta && typeof eta === 'object') this.stageEtaStats[stageId] = eta;
    this._ensureCountdownTicker();

    // Create card with actual employee name
    const name = employeeName || this._getProducerName(stageId);
    const initials = this._getInitials(name);
    const title = `Stage ${stageId} — ${stageName || this._getStageName(stageId)}`;
    const id = `stage${stageId}`;
    if (!this.stageCardIds[stageId]) {
      getStageCard(id, title, name, initials);
      this.stageCardIds[stageId] = id;
    }
    this._renderEta(stageId);
  }

  handleMeetingMessage({ agent, role, message }) {
    if (!message || !message.trim()) return;

    // Find or create the right card
    const sid = this.currentStage;
    if (!sid) {
      // No stage yet — show as notice
      postNotice(`<strong>${agent || 'Agent'}</strong>: ${message}`, 'info');
      return;
    }

    const cardId = this._ensureCard(sid);

    // Update active agent bar with current speaker
    const bar = document.getElementById('activeAgentBar');
    const label = document.getElementById('activeAgentLabel');
    if (bar && label) {
      const stageName = this._getStageName(sid);
      const producerName = this._getProducerName(sid);
      const isRealName = agent && agent.length > 2 && !/^(System|You|system|unknown|Employee)/.test(agent);
      const displayName = isRealName ? agent : producerName;
      if (role === 'critic') {
        bar.style.display = 'flex'; bar.className = 'active-agent-bar reviewing';
        label.textContent = `Stage ${sid}: ${stageName} — Critic reviewing`;
      } else {
        bar.style.display = 'flex'; bar.className = 'active-agent-bar';
        label.textContent = `Stage ${sid}: ${stageName} — ${displayName} working`;
      }
    }

    if (role === 'critic') {
      updateCritic(cardId, message);
    } else {
      updateProducer(cardId, message);
    }
  }

  handleStageReviewing({ stageId }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const cardId = this._ensureCard(sid);
    setCardStatus(cardId, 'reviewing');
    setStage(sid, 'reviewing');
    const runtime = this.stageRuntime[sid] || { startedAtMs: Date.now(), status: 'reviewing' };
    runtime.status = 'reviewing';
    this.stageRuntime[sid] = runtime;
    this._ensureCountdownTicker();
    this._renderEta(sid);
  }

  handleStageComplete({ stageId, confidence, result }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const cardId = this._ensureCard(sid);
    setCardStatus(cardId, 'done');

    if (result) updateProducer(cardId, result);

    if (confidence != null) {
      const pct = Math.round(confidence <= 1 ? confidence * 100 : confidence);
      addCardConf(cardId, pct);
    }

    // Pipeline engine sends breakpoint_hit separately — don't check here
    setStage(sid, 'done');
    delete this.stageRuntime[sid];
    this._renderEta(sid);
  }

  handleStageFailed({ stageId, confidence, reason }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const cardId = this._ensureCard(sid);
    setCardStatus(cardId, 'rejected');

    if (reason) updateCritic(cardId, reason);

    if (confidence != null) {
      const pct = Math.round(confidence <= 1 ? confidence * 100 : confidence);
      addCardConf(cardId, pct);
    }

    setStage(sid, 'failed');
    delete this.stageRuntime[sid];
    this._renderEta(sid);
  }

  handleDirectorAction({ phase, message }) {
    const text = message || phase;
    postNotice(text, 'info');
    void 0;
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = text;
  }

  handleSystemEvent({ type, agent, payload }) {
    if (type === 'heartbeat') return;
    const text = (payload && payload.text) ? payload.text : '';
    if (!text) return;
    // Only show in the right-panel events, not center area
    void 0;
  }

  _triggerBreakpoint(stageId) {
    this.pausedStageId = stageId;
    setStage(stageId, 'paused');
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = `Paused at Stage ${stageId} — waiting for user`;

    const stageName = this._getStageName(stageId);
    if (typeof openBreakpointDialog === 'function') {
      openBreakpointDialog(stageId, stageName);
    }
  }

  handleBreakpointHit({ stage, project_id, message }) {
    const sid = stage || this.currentStage;
    if (!sid) return;
    this._triggerBreakpoint(sid);
  }

  async resumeBreakpoint(feedback = '', isRevision = false) {
    if (!this.pausedStageId) return;
    const sid = this.pausedStageId;
    this.pausedStageId = null;

    if (typeof closeBreakpointDialog === 'function') closeBreakpointDialog();
    if (isRevision) {
      setStage(sid, 'running');
      postNotice(`Revision requested for Stage ${sid}. Re-running with feedback.`, 'info');
    } else {
      setStage(sid, 'done');
      postNotice('Approved by user. Continuing pipeline.', 'ok');
    }

    const projectId = window._currentSessionId || window._currentProjectId;
    if (window._omcClient && projectId) {
      const actualFeedback = isRevision
        ? `[REVISION REQUESTED] CEO wants Stage ${sid} revised: ${feedback}. Re-run this stage. Do NOT advance.`
        : feedback;
      try {
        await window._omcClient.resumePipelineBreakpoint(projectId, sid, actualFeedback);
      } catch (e) {
        await window._omcClient.resumeAfterBreakpoint(
          window._currentProjectId, sid, actualFeedback
        );
      }
    }
  }

  handleFileWritten(data) {
    if (typeof addWorkspaceFile === 'function') {
      addWorkspaceFile(data);
    }
  }

  _getProducerName(stageId) {
    if (typeof STAGES !== 'undefined' && STAGES[stageId - 1]) {
      const assignee = STAGES[stageId - 1].assignee;
      if (assignee && window._employees) {
        const emp = window._employees.find(e => e.employee_number === assignee);
        if (emp) return emp.name;
      }
    }
    const names = {
      1: 'Topic Refiner', 2: 'Lit. Surveyor', 3: 'Idea Generator',
      4: 'Methodology Designer', 5: 'Experiment Designer',
      6: 'Experimentalist', 7: 'Result Analyst', 8: 'Paper Writer', 9: 'Peer Reviewer',
    };
    return names[stageId] || `Stage ${stageId}`;
  }

  _getInitials(name) {
    return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
  }

  _getStageName(stageId) {
    const stages = {
      1: 'Topic Refinement', 2: 'Literature Survey', 3: 'Idea Generation',
      4: 'Methodology Design', 5: 'Experiment Design',
      6: 'Auto Experiment', 7: 'Result Analysis', 8: 'Paper Generation', 9: 'Self-Review',
    };
    return stages[stageId] || `Stage ${stageId}`;
  }

  hydrateStageRuntime(stageId, phase, startedAtSeconds) {
    if (!stageId || !phase || phase === 'done' || phase === 'failed' || phase === 'gate') return;
    const startedAtMs = startedAtSeconds ? Number(startedAtSeconds) * 1000 : Date.now();
    this.stageRuntime[stageId] = {
      startedAtMs: Number.isFinite(startedAtMs) ? startedAtMs : Date.now(),
      status: phase,
    };
    this._ensureCountdownTicker();
    this._renderEta(stageId);
  }

  _refreshStageStats() {
    const client = window._omcClient;
    if (!client || typeof client.getPipelineStageStats !== 'function') return;
    client.getPipelineStageStats()
      .then((data) => {
        const stats = (data && data.stages) ? data.stages : {};
        this.stageEtaStats = stats;
        Object.keys(this.stageCardIds).forEach((stageId) => this._renderEta(Number(stageId)));
      })
      .catch(() => {});
  }

  _ensureCountdownTicker() {
    if (this._countdownTimer) return;
    this._countdownTimer = setInterval(() => this._refreshCountdowns(), 1000);
  }

  _refreshCountdowns() {
    const runningStages = Object.keys(this.stageRuntime);
    if (!runningStages.length) {
      clearInterval(this._countdownTimer);
      this._countdownTimer = null;
      return;
    }
    runningStages.forEach((sid) => this._renderEta(Number(sid)));
  }

  _renderEta(stageId) {
    const cardId = this._cardId(stageId);
    const card = document.getElementById(`sc-${cardId}`);
    if (!card) return;
    const head = card.querySelector('.sc-head');
    if (!head) return;

    let etaEl = head.querySelector('.sc-eta');
    if (!etaEl) {
      etaEl = document.createElement('span');
      etaEl.className = 'sc-eta';
      const statusBadge = head.querySelector('.sc-badge');
      if (statusBadge) head.insertBefore(etaEl, statusBadge);
      else head.appendChild(etaEl);
    }

    const runtime = this.stageRuntime[stageId];
    const elapsedSeconds = runtime?.startedAtMs
      ? Math.max(0, Math.floor((Date.now() - runtime.startedAtMs) / 1000))
      : null;
    const stats = this.stageEtaStats[String(stageId)] || this.stageEtaStats[stageId];
    if (!stats) {
      etaEl.textContent = elapsedSeconds != null ? `${this._fmtDuration(elapsedSeconds)} elapsed` : '';
      etaEl.style.display = etaEl.textContent ? '' : 'none';
      return;
    }

    const total = stats.total || {};
    const typicalMin = Number(total.typical_min_seconds || total.mean_seconds || 0);
    const typicalMax = Number(total.typical_max_seconds || total.mean_seconds || 0);
    const producerMean = Number((stats.producer || {}).mean_seconds || 0);
    const criticMean = Number((stats.critic || {}).mean_seconds || 0);
    const rangeText = typicalMax > 0
      ? (typicalMin > 0 && Math.abs(typicalMax - typicalMin) > 30
        ? `${this._fmtDuration(typicalMin)}–${this._fmtDuration(typicalMax)}`
        : this._fmtDuration(typicalMax))
      : 'N/A';
    const phaseHint = (producerMean > 0 || criticMean > 0)
      ? ` · P ${this._fmtDuration(producerMean)} / C ${this._fmtDuration(criticMean)}`
      : '';

    if (elapsedSeconds != null) {
      const pastTypical = typicalMax > 0 && elapsedSeconds > typicalMax;
      etaEl.textContent = pastTypical
        ? `${this._fmtDuration(elapsedSeconds)} elapsed · past typical ${rangeText}${phaseHint}`
        : `${this._fmtDuration(elapsedSeconds)} elapsed · typical ${rangeText}${phaseHint}`;
    } else {
      etaEl.textContent = `Typical ${rangeText}${phaseHint}`;
    }
    etaEl.style.display = '';
  }

  _fmtDuration(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m > 0) return `${m}:${String(sec).padStart(2, '0')}`;
    return `0:${String(sec).padStart(2, '0')}`;
  }
}
