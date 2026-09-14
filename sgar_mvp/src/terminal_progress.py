"""Presentation only: no state transitions, retries, or training-record mutations."""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path
from loguru import logger

_console = None
_STAGE = {"planner_decompose":"Planner", "retrieval_hyde":"Profiler", "profiler":"Profiler", "plan_compiler":"Compiler", "plan_adaptation":"Adaptation", "evaluator":"Evaluation", "evaluator_review":"Evaluation review"}
_STATUS = {"passed":"Passed", "pass":"Passed", "accepted":"Passed", "failed":"Failed", "fail":"Failed", "rejected":"Failed", "infrastructure_failure":"Call failed", "framework_failure":"Framework error", "protocol_failure":"Response format error", "inconclusive":"Insufficient evidence"}
_RESP = {"framework":"Framework", "infrastructure":"Infrastructure/connection", "research":"Plan/task content", "budget":"Budget", "interrupted":"Interrupted"}

def token(value):
    return re.sub(r"[^\w.:/@+\-]", "_", str(value or "Not recorded"))[:160]

def line(stage, text, task=None):
    prefix = f"[{stage}]" + (f"[{token(task)}]" if task else "")
    return prefix + " " + text

def emit(text, *, console_text=None):
    # Formatting/logging failures must never replace a business result or exception.
    try:
        if _console is not None:
            logger.bind(sgar_progress=True, sgar_console_text=console_text).info(text)
    except Exception:
        pass

def location(label, path, run_root):
    """Keep local paths on the console; persist only run-relative locations."""
    try:
        if _console is None:
            return
        relative = Path(path).resolve().relative_to(Path(run_root).resolve()).as_posix()
        # Never bind the host path into a Loguru record, including its extras.
        logger.bind(sgar_file_detail=True).info(f"{label}: run://{relative}")
        _console.stream.write(time.strftime("%H:%M:%S") + f" {label}: {path}\n")
        _console.stream.flush()
    except Exception:
        pass  # Presentation cannot change execution or terminalization.


class Console:
    def __init__(self, stream):
        self.stream = stream
        self.starts = {}
        self.tasks = {}
        self.started = time.monotonic()

    def __call__(self, message):
        try:
            record = message.record
            text = console_text(record)
            if text:
                self.stream.write(record["time"].strftime("%H:%M:%S") + " " + text + "\n")
                self.stream.flush()
        except Exception:
            pass

def console_text(record):
    """Compact presentation of existing messages; file sink retains the originals."""
    text = record["message"]
    if record.get("extra", {}).get("sgar_progress"):
        return record.get("extra", {}).get("sgar_console_text") or text
    if record.get("extra", {}).get("sgar_file_detail"):
        return None
    if any(s in text for s in ("Structured node failure", "S-GAR MVP - Structured Failure", "Execution failed after candidate freeze", "Pipeline halted due to cascading errors")):
        return None  # The terminal summary below carries the authoritative cause.
    if "Phase " in text:
        for phrase, label in (("Task Decomposition","Task planning"),("Router Adjudication","Candidate retrieval"),("DAG Execution","Task execution"),("Final Delivery","Final delivery")):
            if phrase in text: return line("Stage", label)
    if "Frozen candidate pool bound for task " in text:
        task = text.split("Frozen candidate pool bound for task ",1)[1].split(" |",1)[0]
        counts = re.search(r"counts=(\{[^}]*\})",text)
        return line("Retrieval", "Candidates frozen " + (counts.group(1) if counts else ""), task)
    if "[Planner] Decomposed" in text:
        return line("Planner", text.split("[Planner] ",1)[1])
    if "sgar_mvp.src.planner" in str(record.get("name")) and ("├─" in text or "└─" in text):
        return text.strip()
    if "Starting " in text and "deps satisfied:" in text:
        return line("Execution",text.strip())
    if "[ResourcePlan] Executing " in text:
        body=text.split("[ResourcePlan] Executing ",1)[1]
        return line("Execution",body,record.get("extra",{}).get("terminal_task"))
    if "[ResourcePlan] Final output selected" in text:
        return line("Execution","Step output selected; awaiting evaluation and submission",record.get("extra",{}).get("terminal_task"))
    if "[Sandbox] Success" in text:
        return line("Tool",text.split("[Sandbox] ",1)[1])
    if record["level"].no >= 30:
        return line("Error" if record["level"].no >= 40 else "Notice",text)
    return None

def configure(stream=None):
    """Replace only Loguru's default stderr sink, never remove the file sink."""
    global _console
    try:
        sink=Console(stream or sys.stderr)
        logger.add(sink,format="{message}",level="INFO",catch=True)
        try: logger.remove(0)
        except ValueError: pass
        _console=sink
    except Exception:
        pass  # Keep the original logger if setup failed.

def observe_model(event):
    try:
        if _console is None: return
        kind=event.get("event_type");key=event.get("provider_attempt_id")
        task=event.get("subtask_id");stage=_STAGE.get(event.get("stage"),"Model execution")
        request=event.get("request_sha256")
        if task and request: _console.tasks[request]=task
        model=token(event.get("api_model_id"));attempt=event.get("provider_attempt")
        if kind=="model_call_started":
            _console.starts[key]=time.monotonic()
            emit(line(stage,f"Calling {model}; attempt {attempt}; waiting for response",task))
        elif kind=="model_call_finished":
            started=_console.starts.pop(key,None)
            elapsed=f", {time.monotonic()-started:.1f}s" if started is not None else ""
            error=event.get("error_code")
            if error or not event.get("response_received"):
                received="response received" if event.get("response_received") else "no response received"
                emit(line(stage,f"Call failed: {token(error)}; {received} ; attempt {attempt}{elapsed}",task))
            else:
                emit(line(stage,f"{model} returned{elapsed}",task))
        elif kind=="model_call_blocked":
            emit(line(stage,"Call blocked by existing policy: "+token(event.get("reason")),task))
    except Exception: pass

def observe_evaluation(event):
    try:
        if _console is None or not str(event.get("event_type","")).endswith("_finished"): return
        task=_console.tasks.pop(event.get("request_sha256"),None)
        status=str(event.get("status", "unknown"));code=event.get("failure_code")
        emit(line("Evaluation",_STATUS.get(status,status)+(f"; code={token(code)}" if code else ""),task))
    except Exception: pass

def compilation(action, revision, *, attempt=None, result=None, correction=None, issues=()):
    try:
        task=revision.subtask_revision.subtask_id
        if action=="start": emit(line("Compiler",f"Generating plan; attempt {attempt}",task))
        elif action=="correction":
            detail("Compiler", "Correction feedback", correction, task)
            emit(line("Compiler","Entering existing correction: "+token(correction.get("failure_code"))+"; field="+".".join(map(token,correction.get("path") or ())),task))
        elif result.status=="success":
            steps=result.executable_plan.steps
            emit(line("Compiler",f"Plan accepted; {len(steps)} steps",task))
            detail("Compiler", "Accepted executable plan", result.executable_plan, task)
            for step in steps:
                # Existing plan representation only; no source/prompt text is inspected.
                output=token(step.expected_output_contract.artifact_type)
                deps=", ".join(map(token,step.depends_on)) or "none"
                emit(line("Plan",f"{token(step.step_id)} → {token(step.resource_id)}; output={output}; dependencies={deps}",task))
        else:
            emit(line("Compiler","Plan rejected; code="+token(result.failure.failure_code),task))
            for issue in issues:
                field=".".join(map(token,issue.path)) or "<response root>"
                emit(line("Compiler","Rejected field="+field+"; reason="+token(issue.observed_value or issue.failure_code),task))
    except Exception: pass

def artifact(status, manifest, reason=None):
    try:
        revision=manifest.artifact_revision
        emit(line("Artifact",status+("; reason="+token(reason) if reason else ""),revision.subtask_revision.subtask_id))
    except Exception: pass

def transport_detail(error):
    try:
        if _console is None: return
        chain=[];seen=set();current=error
        for _ in range(5):
            if current is None or id(current) in seen: break
            seen.add(id(current));name=type(current).__name__
            details=[]
            for field in ("status_code","errno","winerror"):
                value=getattr(current,field,None)
                if isinstance(value,int): details.append(f"{field}={value}")
            chain.append(name+("("+", ".join(details)+")" if details else ""))
            current=current.__cause__ or current.__context__
        emit(line("Connection diagnostic"," → ".join(chain)))
        # Do not print str(error): SDK errors can contain headers, bodies, and credentials.
    except Exception: pass

def final_summary(status, run_root, report_path, log_path):
    try:
        emit(line("End", {"complete_success":"Completed", "success_with_warnings":"Completed with notices", "structured_failure":"Run failed"}.get(str(status),str(status))))
        primary=getattr(status,"primary_failure",None) or getattr(status,"failure",None)
        if primary is not None:
            code=primary.failure_code
            detail=f"Stage={token(primary.failure_stage)}; responsibility={_RESP.get(primary.responsibility,primary.responsibility)}; code={token(code)}"
            if primary.exception_type: detail+="; exception="+token(primary.exception_type)
            emit(line("Primary cause",detail,primary.subtask_id))
            if code=="provider_connection_error": emit("  API connection failed; this stage did not complete. This is not a rejection of task content.")
            terminal=getattr(status,"terminal_failure",None)
            if terminal and terminal.failure_sha256!=primary.failure_sha256:
                emit(line("Terminal failure",f"{token(terminal.failure_stage)}: {token(terminal.failure_code)}",terminal.subtask_id))
        elif str(status) not in {"complete_success","success_with_warnings"}:
            emit(line("Primary cause","Detailed responsibility was not recorded; see the run log."))
        if _console is not None: emit(line("Elapsed",f"{time.monotonic()-_console.started:.1f}s"))
        location("Log", log_path, run_root)
        location("Report", report_path, run_root)
        location("Run manifest", Path(run_root) / "run_manifest.json", run_root)
    except Exception: pass


def evaluation_reasons(decision, context):
    try:
        from .compiler_attempt_diagnostics import sanitize_diagnostic_value
        if _console is None: return
        task=context.current_revision.subtask_id
        detail("Evaluation", "Decision", decision, task)
        if decision.verdict.value == "pass": return
        for item in [x for x in decision.criterion_results if x.status.value in {"fail", "unknown"}]:
            reason,_=sanitize_diagnostic_value(item.concise_reason)
            reason=re.sub(r"[\x00-\x1f\x7f]", " ", str(reason))
            emit(line("Evaluation reasons",token(item.criterion_id)+": "+reason,task))
        emit("  Full evaluation evidence is available in the run directory under evaluation/.")
    except Exception: pass


def _json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def detail(stage, title, value, task=None):
    """Render complete diagnostic content once to both text sinks, never mutate it."""
    try:
        if _console is None:
            return
        from .compiler_attempt_diagnostics import sanitize_diagnostic_value
        value = _json_value(value)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
        safe, _ = sanitize_diagnostic_value(value)
        body = safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
        # Neutralize terminal controls while preserving all lines and normal text.
        body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", body)
        complete = line(stage, title + "\n" + body + "\n[End " + title + "]", task)
        terminal = _resource_console_view(stage, title, safe)
        if terminal is None:
            emit(complete)
        else:
            terminal = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", terminal)
            emit(complete, console_text=line(stage, title + "\n" + terminal + "\n[End " + title + "]", task))
    except Exception:
        # Display errors cannot change the selected plan or original exception.
        pass


def compiler_input(api_kwargs, revision, attempt):
    try:
        task = revision.subtask_revision.subtask_id
        # Include actual task/correction inputs, not authentication or SDK internals.
        for index, message in enumerate(api_kwargs.get("messages", ())):
            if message.get("role") != "system":
                detail("Compiler", f"Input; attempt {attempt}; message {index}", message.get("content"), task)
    except Exception:
        pass


def compiler_response(content, revision, attempt):
    try:
        detail("Compiler", f"Returned proposal (not yet validated); attempt {attempt}",
               content, revision.subtask_revision.subtask_id)
    except Exception:
        pass


def frozen_candidates(result, refs):
    try:
        task = result.contract_projection.revision.subtask_id
        base_ids = {rid for ids in result.base_candidate_ids_by_type.values() for rid in ids}
        scores = {}
        for item in result.candidate_score_evidence:
            scores.setdefault(item.resource_id, []).append(_json_value(item))
        rows = []
        for position, ref in enumerate(refs, 1):
            row = _json_value(ref)
            row = dict(row)
            row["frozen_order"] = position
            row["selection_scope"] = "base retrieval" if ref.resource_id in base_ids else "supplemental dependency"
            row["score_evidence"] = scores.get(ref.resource_id, [])
            rows.append(row)
        detail("Retrieval", "Frozen candidate resources", {
            "revision": result.contract_projection.revision,
            "candidates": rows,
            "dependency_edges": result.dependency_edges,
            "compatibility_decisions": result.compatibility_decisions,
        }, task)
    except Exception:
        pass


def _resource_groups(cards):
    groups = {}
    for card in cards:
        if not isinstance(card, dict) or not card.get("resource_id"):
            return None
        kind = str(card.get("resource_type") or "Unknown")
        ids = groups.setdefault(kind, [])
        if card["resource_id"] not in ids:
            ids.append(card["resource_id"])
    return groups


def _resource_console_view(stage, title, value):
    """Only terminal projection changes; the complete sanitized record stays in the file."""
    if stage == "Retrieval" and title == "Frozen candidate resources" and isinstance(value, dict):
        groups = _resource_groups(value.get("candidates", []))
        if groups is None:
            return None
        lines = [f"{kind} ({len(ids)}): {{" + ", ".join(ids) + "}" for kind, ids in groups.items()]
        lines.append("Resource details and scores: pipeline.log")
        return "\n".join(lines)
    if stage != "Compiler":
        return None
    changed = False
    def walk(item):
        nonlocal changed
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key == "candidate_cards" and isinstance(child, list):
                    groups = _resource_groups(child)
                    if groups is not None:
                        result[key] = groups
                        changed = True
                        continue
                result[key] = walk(child)
            return result
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item
    compact = walk(value)
    return json.dumps(compact, ensure_ascii=False, indent=2) if changed else None
