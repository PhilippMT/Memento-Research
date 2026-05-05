// src/event-adapter.js
// Translates OMC CompanyEvents → AutoResearch domain events.

// OMC event types we care about:
// - meeting_booked: a meeting room was allocated (stage starting)
// - meeting_chat: a message within an ongoing meeting (producer/critic output)
// - agent_done: agent finished task
// - tree_update: task tree changed (new stage dispatched)
// - routine_phase: pipeline phase progression
// - state_snapshot: heartbeat / full refresh signal
// - agent_task_update: stage state change

export class EventAdapter {
  constructor() {
    this.handlers = {
      stage_start: [],
      meeting_message: [],
      gate_decision: [],
      stage_complete: [],
      stage_failed: [],
      director_action: [],
      system_event: [],
    };
  }

  on(eventName, fn) {
    if (!this.handlers[eventName]) this.handlers[eventName] = [];
    this.handlers[eventName].push(fn);
  }

  emit(eventName, data) {
    (this.handlers[eventName] || []).forEach(fn => fn(data));
  }

  // Feed raw OMC events here
  process(omcEvent) {
    const { type, agent, payload } = omcEvent;

    switch (type) {
      case 'meeting_booked':
        this.emit('stage_start', {
          stageId: this._inferStageFromAgent(agent, payload),
          roomId: payload.room_id,
          roomName: payload.room_name,
          participants: payload.participants || [],
        });
        break;

      case 'meeting_chat': {
        const role = this._inferRole(agent);
        const message = payload.message || payload.content || '';
        this.emit('meeting_message', {
          agent: agent,
          role: role,
          message: message,
          roomId: payload.room_id,
        });

        // Check if critic message contains a gate decision
        if (role === 'critic') {
          const gate = this._parseGateDecision(message);
          if (gate && gate.decision) {
            const stageId = this._inferStageFromAgent(agent, payload);
            if (gate.decision === 'PASS') {
              this.emit('stage_complete', { stageId, confidence: gate.confidence });
            } else {
              this.emit('stage_failed', { stageId, confidence: gate.confidence });
            }
          }
        }
        break;
      }

      case 'agent_done':
        this.emit('stage_complete', {
          agent: agent,
          stageId: this._inferStageFromAgent(agent, payload),
          result: payload.result || payload.summary || '',
        });
        break;

      case 'routine_phase':
        this.emit('director_action', {
          phase: payload.phase,
          message: payload.message,
        });
        break;

      case 'tree_update':
      case 'agent_task_update':
        this.emit('system_event', {
          type: type,
          agent: agent,
          payload: payload,
        });
        break;

      case 'state_snapshot':
        this.emit('system_event', { type: 'heartbeat' });
        break;

      default:
        this.emit('system_event', { type, agent, payload });
    }
  }

  // --- Helpers ---

  _inferRole(agentId) {
    if (!agentId) return 'system';
    const id = agentId.toLowerCase();
    if (id.includes('critic') || id.includes('reviewer')) return 'critic';
    if (id.includes('director')) return 'director';
    return 'producer';
  }

  _inferStageFromAgent(agentId, payload) {
    const TALENT_STAGE_MAP = {
      'topic-refiner': 1,
      'literature-surveyor': 2, 'lit-surveyor': 2,
      'idea-generator': 3, 'idea-gen': 3,
      'methodology-designer': 4, 'method': 4,
      'experiment-designer': 5, 'exp-design': 5,
      'experimentalist': 6,
      'result-analyst': 7, 'analyst': 7,
      'paper-writer': 8,
      'peer-reviewer': 9, 'reviewer': 9,
    };

    if (agentId && TALENT_STAGE_MAP[agentId.toLowerCase()]) {
      return TALENT_STAGE_MAP[agentId.toLowerCase()];
    }

    if (payload && payload.stage_id) return payload.stage_id;
    if (payload && payload.node_id) return this._stageFromNodeId(payload.node_id);
    return null;
  }

  _stageFromNodeId(nodeId) {
    return null;
  }

  _parseGateDecision(message) {
    // Critic messages contain: "Confidence: 0.72" and "Decision: PASS/REJECT"
    const confMatch = message.match(/confidence[:\s_]*([0-9.]+)/i);
    const decisionMatch = message.match(/\b(PASS|REJECT)\b/i);

    if (confMatch || decisionMatch) {
      return {
        confidence: confMatch ? parseFloat(confMatch[1]) : null,
        decision: decisionMatch ? decisionMatch[1].toUpperCase() : null,
      };
    }
    return null;
  }
}
