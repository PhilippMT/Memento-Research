// src/event-adapter.js
// Translates raw OMC WebSocket events → readable activity stream entries.

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

    // Employee ID → display info
    this.employees = {
      '00001': { name: 'You', initials: 'U', role: 'director' },
      '00002': { name: 'Research Director', initials: 'RD', role: 'director' },
      '00003': { name: 'Adversarial Critic', initials: 'AC', role: 'critic' },
      '00004': { name: 'Topic Refiner', initials: 'TR', role: 'producer' },
    };
  }

  on(eventName, fn) {
    if (!this.handlers[eventName]) this.handlers[eventName] = [];
    this.handlers[eventName].push(fn);
  }

  emit(eventName, data) {
    (this.handlers[eventName] || []).forEach(fn => fn(data));
  }

  _emp(id) {
    return this.employees[id] || { name: `Employee ${id}`, initials: '??', role: 'producer' };
  }

  process(omcEvent) {
    const { type, agent, payload } = omcEvent;
    if (!type) return;

    switch (type) {
      case 'conversation_message':
        this._handleConversationMessage(payload);
        break;

      case 'agent_task_update':
        this._handleTaskUpdate(payload);
        break;

      case 'agent_thinking':
        this._handleThinking(payload);
        break;

      case 'agent_done':
        this._handleAgentDone(agent, payload);
        break;

      case 'agent_log':
        this._handleAgentLog(payload);
        break;

      case 'meeting_booked':
        this._handleMeetingBooked(agent, payload);
        break;

      case 'meeting_chat':
        this._handleMeetingChat(payload);
        break;

      case 'conversation_phase':
        this._handleConversationPhase(payload);
        break;

      case 'routine_phase':
        this.emit('director_action', {
          phase: payload.phase,
          message: payload.message || payload.phase,
        });
        break;

      case 'state_snapshot':
      case 'connected':
        // Ignore heartbeats and connection confirmations
        break;

      case 'employee_hired':
        this._handleEmployeeHired(payload);
        break;

      case 'tree_update':
        // Task tree changed — informational only
        break;

      default:
        // Show unrecognized events as system messages with readable text
        this._handleGeneric(type, agent, payload);
    }
  }

  // ── Conversation messages (main content channel) ──

  _handleConversationMessage(p) {
    if (!p) return;
    const text = p.text || p.content || '';
    if (!text.trim()) return;

    // Filter out OMC internal agent scaffolding
    if (this._isInternalMessage(text)) return;

    const empId = p.employee_id || p.source_employee;
    const emp = empId ? this._emp(empId) : null;
    const sender = p.sender || 'system';

    // Determine role from sender/employee info
    let role = 'system';
    let name = sender;
    let initials = 'SY';

    if (emp) {
      role = emp.role;
      name = emp.name;
      initials = emp.initials;
    } else if (sender === 'ceo' || sender === 'CEO') {
      role = 'director';
      name = 'Pipeline';
      initials = 'PL';
    } else if (sender === 'system' || sender === 'SYSTEM') {
      role = 'system';
      name = 'System';
      initials = 'SY';
    }

    // Check if this is a completion summary — parse and simplify
    const summary = this._parseCompletionSummary(text);
    if (summary) {
      const cleanMsg = summary.error
        ? `Pipeline finished: ${summary.succeeded}/${summary.total} tasks succeeded. ${summary.error}`
        : `Pipeline finished: ${summary.succeeded}/${summary.total} tasks succeeded in ${summary.time}.`;
      this.emit('director_action', { phase: 'complete', message: cleanMsg });
      return;
    }

    // Check if this is a gate decision from the critic
    if (role === 'critic') {
      const gate = this._parseGateDecision(text);
      if (gate && gate.decision) {
        const stageId = this._inferStageFromEmployee(empId);
        if (gate.decision === 'PASS') {
          this.emit('stage_complete', { stageId, confidence: gate.confidence });
        } else {
          this.emit('stage_failed', { stageId, confidence: gate.confidence });
        }
      }
    }

    // Try to clean the message before displaying
    const cleaned = this._tryCleanMessage(text);
    if (cleaned) {
      this.emit('director_action', { phase: 'status', message: cleaned });
      return;
    }

    this.emit('meeting_message', { agent: name, role, message: text });
  }

  // ── Task updates (status changes) ──

  _handleTaskUpdate(p) {
    if (!p) return;
    const empId = p.employee_id;
    const emp = empId ? this._emp(empId) : null;
    const task = p.task || {};

    const status = task.status || p.status;
    if (!status) return;

    // Build readable description
    const name = emp ? emp.name : `Employee ${empId}`;
    let text = '';

    if (status === 'running' || status === 'in_progress' || status === 'processing') {
      const desc = task.description_preview || task.description || '';
      // Show what the agent is working on
      if (desc && !this._isInternalMessage(desc)) {
        this.emit('meeting_message', {
          agent: name,
          role: emp ? emp.role : 'producer',
          message: desc,
        });
      }
      return;
    } else if (status === 'done' || status === 'completed' || status === 'accepted') {
      const result = task.result || task.summary || '';
      // Emit the result as a meeting message AND a stage_complete
      if (result && !this._isInternalMessage(result)) {
        this.emit('meeting_message', {
          agent: name,
          role: emp ? emp.role : 'producer',
          message: result,
        });
      }
      this.emit('stage_complete', {
        agent: name,
        stageId: this._inferStageFromEmployee(empId),
        result: '',
      });
      return;
    } else if (status === 'failed' || status === 'error') {
      const rawErr = task.error || task.result || 'unknown error';
      text = `Task failed: ${this._cleanError(rawErr)}`;
    } else if (status === 'idle') {
      // Don't show idle status changes
      return;
    } else {
      text = `Status: ${status}`;
    }

    this.emit('director_action', {
      phase: status,
      message: `${name}: ${text}`,
    });
  }

  // ── Agent thinking (streaming work-in-progress) ──

  _handleThinking(p) {
    if (!p) return;
    const empId = p.employee_id;
    const emp = empId ? this._emp(empId) : null;
    const content = p.message || p.content || '';
    if (!content.trim()) return;

    const name = emp ? emp.name : `Agent ${empId}`;
    const toolName = p.tool_name;
    let text = content;
    if (toolName) text = `[${toolName}] ${content}`;

    this.emit('meeting_message', {
      agent: name,
      role: emp ? emp.role : 'producer',
      message: text,
    });
  }

  // ── Agent done ──

  _handleAgentDone(agent, p) {
    if (!p) return;
    const empId = p.employee_id;
    const emp = empId ? this._emp(empId) : null;
    const summary = p.summary || p.result || '';
    const name = emp ? emp.name : (agent || 'Agent');

    this.emit('stage_complete', {
      agent: name,
      stageId: this._inferStageFromEmployee(empId),
      result: summary,
    });
  }

  // ── Agent log ──

  _handleAgentLog(p) {
    if (!p) return;
    const empId = p.employee_id;
    const emp = empId ? this._emp(empId) : null;
    const content = p.content || p.message || '';
    if (!content.trim()) return;

    this.emit('meeting_message', {
      agent: emp ? emp.name : `Agent ${empId}`,
      role: emp ? emp.role : 'producer',
      message: content,
    });
  }

  // ── Meeting events ──

  _handleMeetingBooked(agent, p) {
    if (!p) return;
    this.emit('stage_start', {
      stageId: this._inferStageFromAgent(agent, p),
      roomId: p.room_id || p.meeting_id,
      roomName: p.room_name,
      participants: p.participants || [],
    });
  }

  _handleMeetingChat(p) {
    if (!p) return;
    const content = p.content || p.message || '';
    if (!content.trim()) return;

    const speakerName = p.speaker_name || `Speaker ${p.speaker_id}`;
    const role = this._inferRoleFromName(speakerName);

    this.emit('meeting_message', {
      agent: speakerName,
      role: role,
      message: content,
      roomId: p.meeting_id,
    });

    // Check for gate decisions
    if (role === 'critic') {
      const gate = this._parseGateDecision(content);
      if (gate && gate.decision) {
        if (gate.decision === 'PASS') {
          this.emit('stage_complete', { confidence: gate.confidence });
        } else {
          this.emit('stage_failed', { confidence: gate.confidence });
        }
      }
    }
  }

  // ── Conversation phase ──

  _handleConversationPhase(p) {
    if (!p) return;
    const phase = p.phase || '';
    const convType = p.type || '';
    if (phase === 'started' || phase === 'open') {
      this.emit('director_action', {
        phase: phase,
        message: `Conversation started: ${convType}`,
      });
    }
  }

  // ── Employee hired ──

  _handleEmployeeHired(p) {
    if (!p) return;
    const name = p.name || p.nickname || 'Unknown';
    const empId = p.employee_id;
    const role = p.role || '';

    // Register in our employee map
    if (empId) {
      const initials = name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
      this.employees[empId] = {
        name,
        initials,
        role: this._mapOmcRole(role),
      };
    }

    this.emit('director_action', {
      phase: 'hired',
      message: `${name} joined the team (${role}).`,
    });
  }

  // ── Generic fallback ──

  _handleGeneric(type, agent, payload) {
    if (!payload) return;

    // Try to extract something readable
    const text = payload.text || payload.message || payload.description || payload.content || '';
    if (text) {
      this.emit('system_event', {
        type: type,
        agent: agent,
        payload: { text },
      });
      return;
    }

    // For truly unrecognized events, show a compact summary
    const summary = this._summarizePayload(payload);
    if (summary) {
      this.emit('system_event', {
        type: type,
        agent: agent,
        payload: { text: summary },
      });
    }
  }

  // ── Filters ──

  _isInternalMessage(text) {
    // Only filter long OMC agent system prompts (the multi-section context dumps)
    // Short messages (<300 chars) always pass through
    if (text.length < 300) return false;

    // These markers only appear in agent system prompts, never in real output
    const hardMarkers = [
      '[Company Context]',
      '[Self-Verification',
      '[Previous Work Learnings]',
      'Task Chain (ancestors)',
      '=== Current Task',
      '## Your Work Principles',
      'Performance review for employee',
      'performance_score',
      'probation_review',
    ];
    for (const m of hardMarkers) {
      if (text.includes(m)) return true;
    }
    // Very long messages (>800 chars) with multiple markdown sections = agent prompt
    if (text.length > 800 && (text.match(/##/g) || []).length >= 3) return true;
    return false;
  }

  // Check if message is a completion/error that should be shown as a clean notice
  _tryCleanMessage(text) {
    // "✅ Project Complete: ..." → clean summary
    if (text.includes('Project Complete')) {
      const summary = this._parseCompletionSummary(text);
      if (summary) {
        return summary.error
          ? `Pipeline error: ${summary.error}`
          : `Pipeline complete: ${summary.succeeded}/${summary.total} tasks in ${summary.time}`;
      }
    }
    // "✗ Error code: 401 - {...}" → clean error
    if (text.includes('Error code:')) {
      return this._cleanError(text);
    }
    return null;
  }

  // Parse OMC completion summaries into clean structure
  _parseCompletionSummary(text) {
    if (!text.includes('Project Complete') && !text.includes('Results:')) return null;
    const succeeded = text.match(/(\d+)\/(\d+)\s*tasks?\s*succeeded/i);
    const failed = text.match(/(\d+)\s*failed/i);
    const time = text.match(/Time:\s*(\d+s)/i);
    const errMatch = text.match(/Error code:\s*(\d+)\s*-\s*\{[^}]*'message':\s*'([^']+)'/);

    return {
      total: succeeded ? parseInt(succeeded[2]) : 0,
      succeeded: succeeded ? parseInt(succeeded[1]) : 0,
      failed: failed ? parseInt(failed[1]) : 0,
      time: time ? time[1] : '',
      error: errMatch ? `API Error ${errMatch[1]}: ${errMatch[2]}` : '',
    };
  }

  // Extract a clean, user-facing error from a raw error string
  _cleanError(text) {
    // Extract just the error message part
    const codeMatch = text.match(/Error code:\s*(\d+)/i);
    const msgMatch = text.match(/'message':\s*'([^']+)'/);
    if (codeMatch && msgMatch) {
      return `API Error ${codeMatch[1]}: ${msgMatch[1]}`;
    }
    // Strip file paths, long hex IDs
    let s = text.replace(/\/Users\/[^\s,)}\]'"]+/g, '').replace(/\b[0-9a-f]{12,}\b/g, '');
    // Truncate if still too long
    if (s.length > 200) s = s.slice(0, 200) + '...';
    return s.trim();
  }

  // ── Helpers ──

  _summarizePayload(p) {
    const keys = Object.keys(p).filter(k =>
      !['core_id', 'timestamp', 'project_id', 'conv_id'].includes(k)
    );
    if (keys.length === 0) return '';

    const parts = [];
    for (const k of keys.slice(0, 3)) {
      const v = p[k];
      if (typeof v === 'string' && v.length > 0) {
        parts.push(`${k}: ${v.length > 80 ? v.slice(0, 80) + '...' : v}`);
      } else if (typeof v === 'number' || typeof v === 'boolean') {
        parts.push(`${k}: ${v}`);
      }
    }
    return parts.join(', ');
  }

  _inferStageFromEmployee(empId) {
    const map = { '00004': 1 }; // Topic Refiner → stage 1
    return map[empId] || null;
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
    return null;
  }

  _inferRoleFromName(name) {
    if (!name) return 'system';
    const lower = name.toLowerCase();
    if (lower.includes('critic') || lower.includes('reviewer') || lower.includes('qa')) return 'critic';
    if (lower.includes('director') || lower.includes('ceo') || lower.includes('coo')) return 'director';
    return 'producer';
  }

  _mapOmcRole(role) {
    const r = (role || '').toLowerCase();
    if (r.includes('qa') || r.includes('critic')) return 'critic';
    if (r.includes('ceo') || r.includes('coo') || r.includes('director')) return 'director';
    return 'producer';
  }

  _parseGateDecision(message) {
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
