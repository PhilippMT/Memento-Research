// src/pipeline-controller.js
// Drives stage cards in response to domain events from EventAdapter.
// Uses: getStageCard(), updateProducer(), updateCritic(), setCardStatus(),
//       addCardConf(), postNotice() — all defined in index.html.

export class PipelineController {
  constructor(adapter) {
    this.adapter = adapter;
    this.currentStage = null;
    this.stageCardIds = {}; // stageId → card DOM id

    adapter.on('stage_start', (e) => this.handleStageStart(e));
    adapter.on('meeting_message', (e) => this.handleMeetingMessage(e));
    adapter.on('stage_complete', (e) => this.handleStageComplete(e));
    adapter.on('stage_failed', (e) => this.handleStageFailed(e));
    adapter.on('director_action', (e) => this.handleDirectorAction(e));
    adapter.on('system_event', (e) => this.handleSystemEvent(e));
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

  handleStageStart({ stageId, roomName, participants }) {
    if (!stageId) return;
    this.currentStage = stageId;
    setStage(stageId, 'running');
    this._ensureCard(stageId);
    postNotice(`Delegating Stage ${stageId}: ${this._getStageName(stageId)}`, 'info');
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

    if (role === 'critic') {
      updateCritic(cardId, message);
    } else {
      updateProducer(cardId, message);
    }
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
      const confEl = document.getElementById(`c${sid}`);
      if (confEl) confEl.textContent = `${pct}%`;
      addEvent('gtag', `PASS (${pct}%) — Stage ${sid}`);
    } else {
      addEvent('gtag', `PASS — Stage ${sid}`);
    }

    setStage(sid, 'done');
    const connector = document.getElementById(`sc-${sid}`);
    if (connector) connector.classList.add('done');

    if (typeof STAGES !== 'undefined' && STAGES[sid - 1] && STAGES[sid - 1].bp) {
      this._triggerBreakpoint(sid);
    }
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
      addEvent('gtag', `REJECTED (${pct}%) — Stage ${sid}`);
    } else {
      addEvent('gtag', `REJECTED — Stage ${sid}`);
    }

    setStage(sid, 'failed');
  }

  handleDirectorAction({ phase, message }) {
    const text = message || phase;
    postNotice(text, 'info');
    addEvent('dtag', text);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = text;
  }

  handleSystemEvent({ type, agent, payload }) {
    if (type === 'heartbeat') return;
    const text = (payload && payload.text) ? payload.text : '';
    if (!text) return;
    // Only show in the right-panel events, not center area
    addEvent('stag', `[${type}] ${text}`);
  }

  _triggerBreakpoint(stageId) {
    this.pausedStageId = stageId;
    setStage(stageId, 'paused');
    postNotice(`Breakpoint at Stage ${stageId}. Waiting for user approval.`, 'info');
    addActionBar(null, stageId);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = `Paused at Stage ${stageId} — waiting for user`;
  }

  async resumeBreakpoint(feedback = '') {
    if (!this.pausedStageId) return;
    const sid = this.pausedStageId;
    this.pausedStageId = null;

    document.getElementById('action-panel-global')?.remove();
    setStage(sid, 'done');
    postNotice('Approved by user. Continuing pipeline.', 'ok');
    addEvent('dtag', `Stage ${sid} approved by user. Proceeding.`);

    if (window._omcClient && window._currentProjectId) {
      await window._omcClient.resumeAfterBreakpoint(
        window._currentProjectId, sid, feedback
      );
    }
  }

  _getProducerName(stageId) {
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
}
