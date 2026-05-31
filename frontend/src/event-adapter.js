// src/event-adapter.js
// Translates raw OMC WebSocket events → readable activity stream entries.

export class EventAdapter {
  constructor() {
    this.handlers = {
      stage_start: [],
      meeting_message: [],
      gate_decision: [],
      stage_complete: [],
      stage_reviewing: [],
      stage_failed: [],
      director_action: [],
      system_event: [],
      file_written: [],
      clarification_needed: [],
      breakpoint_hit: [],
    };

    // Tracks task ids we've already fired ``clarification_needed`` for.
    // _handleTaskUpdate runs on EVERY task status change, so without
    // this guard a single task whose description matches the heuristic
    // would re-open the popup on PROCESSING → COMPLETED → (retry) and
    // again on every subsequent attempt. Cleared on terminal task
    // updates so a follow-up genuinely-new clarification on the same
    // task id can still fire.
    this._clarificationEmittedFor = new Set();

    // Employee ID → display info
    this.employees = {
      '00001': { name: 'You', initials: 'U', role: 'director' },
      '00002': { name: 'HR', initials: 'HR', role: 'system' },
      '00003': { name: 'COO', initials: 'CO', role: 'system' },
      '00004': { name: 'Research Director', initials: 'RD', role: 'director' },
      '00005': { name: 'CSO', initials: 'CS', role: 'system' },
      '00006': { name: 'Topic Refiner', initials: 'TR', role: 'producer' },
      '00007': { name: 'Literature Surveyor', initials: 'LS', role: 'producer' },
      '00008': { name: 'Idea Generator', initials: 'IG', role: 'producer' },
      '00009': { name: 'Methodology Designer', initials: 'MD', role: 'producer' },
      '00010': { name: 'Experiment Designer', initials: 'ED', role: 'producer' },
      '00011': { name: 'Experimentalist', initials: 'EX', role: 'producer' },
      '00012': { name: 'Result Analyst', initials: 'RA', role: 'producer' },
      '00013': { name: 'Paper Writer', initials: 'PW', role: 'producer' },
      '00014': { name: 'Adversarial Critic', initials: 'AC', role: 'critic' },
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
        if (payload && payload.pipeline_managed) {
          // Pipeline engine events — authoritative stage info
          if (payload.type === 'stage_start') {
            this.emit('stage_start', { stageId: payload.stage, stageName: payload.stage_name, employeeName: payload.employee_name, employeeId: payload.employee_id });
          } else if (payload.type === 'stage_complete') {
            this.emit('stage_complete', { stageId: payload.stage, confidence: payload.confidence });
          } else if (payload.type === 'stage_reviewing') {
            this.emit('stage_reviewing', { stageId: payload.stage });
          } else if (payload.type === 'stage_failed') {
            this.emit('stage_failed', { stageId: payload.stage, confidence: payload.confidence });
          } else if (payload.type === 'critic_result') {
            this.emit('meeting_message', {
              agent: 'Adversarial Critic',
              role: 'critic',
              message: `**${payload.decision}** (confidence: ${payload.confidence != null ? (payload.confidence <= 1 ? Math.round(payload.confidence * 100) + '%' : payload.confidence + '%') : 'N/A'})\n\n${payload.text}`,
            });
          } else if (payload.type === 'breakpoint_hit') {
            this.emit('breakpoint_hit', payload);
          } else if (payload.type === 'pipeline_complete') {
            this.emit('director_action', { phase: 'complete', message: 'Pipeline complete! All 9 stages finished.' });
          }
        } else if (payload && payload.type === 'file_written') {
          this.emit('file_written', payload);
        } else if (payload && payload.type === 'breakpoint_hit') {
          this.emit('breakpoint_hit', payload);
        }
        break;

      case 'connected':
        // Ignore connection confirmations
        break;

      case 'employee_hired':
        this._handleEmployeeHired(payload);
        break;

      case 'tree_update':
        // Task tree changed — informational only
        break;

      case 'file_written':
        this.emit('file_written', payload);
        break;

      case 'pending_interaction':
        this._handleClarification(payload);
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

    // Gate decisions are now handled by the pipeline engine backend.
    // Do NOT emit stage_complete/stage_failed from text parsing — causes duplicates.

    // Try to clean the message before displaying
    const cleaned = this._tryCleanMessage(text);
    if (cleaned) {
      this.emit('director_action', { phase: 'status', message: cleaned });
      return;
    }

    // Stage detection is now handled by the pipeline engine backend.
    // Do NOT emit stage_start from text heuristics — it causes duplicates.

    // Detect if agent is asking CEO for clarification
    if (this._isClarificationRequest(text)) {
      this.emit('clarification_needed', {
        agent: name,
        employeeId: empId || '',
        message: text,
      });
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

    const name = emp ? emp.name : `Employee ${empId || 'unknown'}`;
    const desc = task.description_preview || task.description || '';
    const result = task.result || task.summary || '';

    // Detect stage from description or result
    const stageId = this._detectStage(desc) || this._detectStage(result) || this._inferStageFromEmployee(empId);

    // Check if task is a CEO request or needs clarification.
    // Skip if a breakpoint action panel is already showing — it handles the interaction.
    // Dedup by task id: _handleTaskUpdate fires on every status change,
    // so without this we'd re-open the popup on each one for the same task.
    const taskId = task.id || task.task_id || p.task_id || '';
    const shouldFire = (
      task.node_type === 'CEO_REQUEST' || p.ceo_request || this._isClarificationRequest(desc)
    );
    if (shouldFire && !document.getElementById('action-panel-global')) {
      if (taskId && this._clarificationEmittedFor.has(taskId)) {
        // Already popped for this task; skip until it resolves.
      } else {
        if (taskId) this._clarificationEmittedFor.add(taskId);
        this.emit('clarification_needed', {
          agent: name,
          employeeId: empId || '',
          message: desc || 'Agent needs your input.',
        });
      }
    }
    // Clear dedup state on terminal task updates so a future task with
    // the same id (rare but possible on retry-with-reset) can re-fire.
    if (taskId && (status === 'completed' || status === 'failed' || status === 'cancelled' || status === 'finished')) {
      this._clarificationEmittedFor.delete(taskId);
    }

    if (status === 'running' || status === 'in_progress' || status === 'processing') {
      // Emit stage_start so pipeline-controller creates the card
      if (stageId) {
        this.emit('stage_start', {
          stageId,
          roomName: desc,
          participants: empId ? [empId] : [],
        });
      }
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
      // Try multiple result fields: submitted_result (from submit_result tool),
      // result_preview (from to_dict), acceptance_result.notes, description
      const submitted = p.submitted_result;
      const preview = task.result_preview;
      const acceptance = task.acceptance_result;
      const resultText = submitted
        || preview
        || result
        || (acceptance && acceptance.notes)
        || desc
        || '';
      // Emit the result as a meeting message
      if (resultText && !this._isInternalMessage(resultText)) {
        this.emit('meeting_message', {
          agent: name,
          role: emp ? emp.role : 'producer',
          message: resultText,
        });
      }
      // Critic completing → real gate decision (stage_complete/stage_failed)
      // Producer completing → stage_reviewing (awaiting critic)
      if (emp && emp.role === 'critic') {
        const gate = this._parseGateDecision(resultText);
        if (gate && gate.decision === 'PASS') {
          this.emit('stage_complete', { agent: name, stageId, confidence: gate.confidence, result: '' });
        } else if (gate && gate.decision) {
          this.emit('stage_failed', { agent: name, stageId, confidence: gate.confidence, reason: resultText });
        } else {
          // Critic completed without explicit PASS/REJECT — treat as pass
          this.emit('stage_complete', { agent: name, stageId, result: '' });
        }
      } else {
        this.emit('stage_reviewing', { agent: name, stageId, result: '' });
      }
      return;
    } else if (status === 'failed' || status === 'error') {
      const rawErr = task.error || task.result || 'unknown error';
      this.emit('director_action', {
        phase: status,
        message: `${name}: Task failed: ${this._cleanError(rawErr)}`,
      });
    } else if (status === 'idle') {
      return;
    } else {
      this.emit('director_action', {
        phase: status,
        message: `${name}: Status: ${status}`,
      });
    }
  }

  // ── Agent thinking (streaming work-in-progress) ──

  _handleThinking(p) {
    if (!p) return;
    const empId = p.employee_id;
    const emp = empId ? this._emp(empId) : null;
    const content = p.message || p.content || '';
    if (!content.trim()) return;

    // Filter out internal agent scaffolding
    if (this._isInternalMessage(content)) return;

    const name = emp ? emp.name : `Agent ${empId || 'unknown'}`;
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

    // Filter out internal agent scaffolding
    if (this._isInternalMessage(content)) return;

    this.emit('meeting_message', {
      agent: emp ? emp.name : `Agent ${empId || 'unknown'}`,
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
    if (this._isInternalMessage(text)) return;
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

  // ── Clarification requests ──

  _handleClarification(p) {
    if (!p) return;
    const empId = p.employee_id || p.source_employee || '';
    const emp = empId ? this._emp(empId) : null;
    const message = p.message || p.description || p.text || 'Agent needs your input.';
    this.emit('clarification_needed', {
      agent: emp ? emp.name : 'Agent',
      employeeId: empId,
      message,
    });
  }

  // Check if a conversation message is actually asking the CEO for input
  _isClarificationRequest(text) {
    if (!text || text.length < 10) return false;
    const lower = text.toLowerCase();
    // Markers are intentionally narrow. Broader phrases like "your decision"
    // / "please confirm" / "please provide" / "could you confirm" match
    // boilerplate prompts (e.g. "Return your decision in JSON format" lives
    // in core/conversation.py and core/routine.py), turning every critic
    // dispatch into a clarification popup. Anything kept here must mean
    // "agent is stuck and is asking the user", not "instruction prose".
    const markers = [
      'awaiting ceo', 'awaiting your', 'need your input',
      'need clarification', 'please clarify',
      'waiting for your', 'your guidance',
      'could you clarify',
      'need your approval', 'require your input',
    ];
    return markers.some(m => lower.includes(m));
  }

  // ── Filters ──

  _isInternalMessage(text) {
    // Only filter OMC agent system prompts (the multi-section context dumps)
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
    const map = {
      '00006': 1, // Topic Refiner
      '00007': 2, // Literature Surveyor
      '00008': 3, // Idea Generator
      '00009': 4, // Methodology Designer
      '00010': 5, // Experiment Designer
      '00011': 6, // Experimentalist
      '00012': 7, // Result Analyst
      '00013': 8, // Paper Writer
      '00014': 9, // Adversarial Critic / Peer Reviewer
    };
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

  // Detect stage number from title-like patterns: "Stage 3: ...", "Stage 3 —", "Execute Stage 3"
  // Does NOT match "Stage 3" buried in a longer sentence (e.g. assignment lists).
  _detectStage(text) {
    if (!text) return null;
    // Only match "Stage N" when followed by a colon, dash, or end-of-string (title format)
    const m = text.match(/(?:^|\n)\s*(?:Execute\s+)?Stage\s+(\d+)\s*[:\u2014—-]/i);
    if (m) return parseInt(m[1]);

    // Fallback: detect by stage name keywords
    const lower = text.toLowerCase();
    const keywords = [
      [1, ['topic refin']],
      [2, ['literature survey', 'lit survey', 'literature review']],
      [3, ['idea generat']],
      [4, ['methodology design', 'method design']],
      [5, ['experiment design']],
      [6, ['run experiment', 'auto experiment', 'experimentalist']],
      [7, ['result analy']],
      [8, ['paper writ', 'paper generat', 'draft paper']],
      [9, ['peer review', 'self-review', 'adversarial review']],
    ];
    for (const [stage, kws] of keywords) {
      for (const kw of kws) {
        if (lower.includes(kw)) return stage;
      }
    }
    return null;
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
