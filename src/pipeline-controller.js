// src/pipeline-controller.js
// Drives UI state in response to domain events from EventAdapter.

export class PipelineController {
  constructor(adapter) {
    this.adapter = adapter;
    this.currentStage = null;
    this.meetingCards = {}; // stageId → DOM element

    // Subscribe to domain events
    adapter.on('stage_start', (e) => this.handleStageStart(e));
    adapter.on('meeting_message', (e) => this.handleMeetingMessage(e));
    adapter.on('stage_complete', (e) => this.handleStageComplete(e));
    adapter.on('stage_failed', (e) => this.handleStageFailed(e));
    adapter.on('director_action', (e) => this.handleDirectorAction(e));
    adapter.on('system_event', (e) => this.handleSystemEvent(e));
  }

  handleStageStart({ stageId, roomName, participants }) {
    if (!stageId) return;

    // Collapse previous meeting card
    if (this.currentStage && this.meetingCards[this.currentStage]) {
      this.meetingCards[this.currentStage].classList.add('collapsed');
    }

    this.currentStage = stageId;
    setStage(stageId, 'running');
    addEvent('dtag', `Delegating Stage ${stageId}`);
    addEvent('mtag', `Meeting: ${roomName || `Stage ${stageId}`}`);

    // Create meeting card using existing createMeeting() from index.html
    const producerName = this._getProducerName(stageId);
    const initials = this._getInitials(producerName);
    const card = createMeeting(
      `s${stageId}`,
      producerName,
      initials,
      `Stage ${stageId} — ${this._getStageName(stageId)}`
    );
    this.meetingCards[stageId] = card;
  }

  handleMeetingMessage({ agent, role, message }) {
    if (!this.currentStage) return;
    const card = this.meetingCards[this.currentStage];
    if (!card) return;

    if (role === 'producer') {
      const prodEl = card.querySelector(`#prod-s${this.currentStage}`);
      if (prodEl) prodEl.innerHTML = message;
    } else if (role === 'critic') {
      const critEl = card.querySelector(`#crit-s${this.currentStage}`);
      if (critEl) critEl.innerHTML = message;
    }
  }

  handleStageComplete({ stageId, result }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const card = this.meetingCards[sid];
    if (card) {
      const badge = card.querySelector('.meeting-badge');
      if (badge) {
        badge.className = 'meeting-badge concluded';
        badge.textContent = 'Passed';
      }
      card.classList.remove('active');
    }

    setStage(sid, 'done');
    const connector = document.getElementById(`sc-${sid}`);
    if (connector) connector.classList.add('done');
    addEvent('gtag', `PASS — Stage ${sid}`);

    // Check breakpoint
    if (typeof STAGES !== 'undefined' && STAGES[sid - 1] && STAGES[sid - 1].bp) {
      this._triggerBreakpoint(sid, card);
    }
  }

  handleStageFailed({ stageId, reason }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const card = this.meetingCards[sid];
    if (card) {
      const badge = card.querySelector('.meeting-badge');
      if (badge) {
        badge.className = 'meeting-badge rejected';
        badge.textContent = 'Rejected';
      }
      card.classList.remove('active');
      card.classList.add('rejected');
    }

    setStage(sid, 'failed');
    addEvent('gtag', `REJECTED — Stage ${sid}`);
  }

  handleDirectorAction({ phase, message }) {
    addEvent('dtag', message || phase);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = message || phase;
  }

  handleSystemEvent({ type, agent, payload }) {
    if (type === 'heartbeat') return;
    addEvent('stag', `[${type}] ${agent || ''}: ${JSON.stringify(payload || {}).slice(0, 80)}`);
  }

  _triggerBreakpoint(stageId, card) {
    setStage(stageId, 'paused');
    if (card) card.classList.add('paused');
    addActionBar(card, stageId);
    addEvent('stag', `Breakpoint on Stage ${stageId}. Waiting for user.`);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = `Paused at Stage ${stageId} — waiting for user`;
  }

  // --- Stage metadata ---

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
