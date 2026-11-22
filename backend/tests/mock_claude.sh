#!/usr/bin/env bash
# The mock `claude` for the tests (conftest.py fills the placeholders and copies
# this file into the test temp dir). A scratch server runs it through a wrapper
# that fills the same two placeholders, e.g.:
#   sed -e "s#__PROBES__#$DIR/probes#g" -e "s#__FIXTURES__#$PWD/tests/fixtures/stream#g" \
#       tests/mock_claude.sh >"$DIR/claude" && chmod +x "$DIR/claude"
# (CO_CLAUDE_BIN="$DIR/claude"). Seams, all keyed on the task prompt:
#   [env-probe:KEY] [call-probe:KEY] [prompt-probe:KEY] [guard-spool] [guard-tool:MODE]
#   [env-echo] [env-echo-fail] [stream-fixture:NAME] [hang] [usage-probe:KEY]
#   (ask calls, `--output-format json`:) [ask-probe:KEY] [ask-raw] [ask-fail] [ask-sleep]
args="$*"
prompt="$(cat)"
# Ask calls (--output-format json, not stream-json): [ask-probe:KEY] records argv to
# PROBES/KEY.args and stdin to KEY.prompt, then answers as the CLI's json does.
if [[ "$args" == *"--output-format json"* && "$args" != *"stream-json"* ]]; then
  if [[ "$prompt" == *"[ask-sleep]"* ]]; then sleep 5; exit 0; fi
  if [[ "$prompt" == *"[ask-fail]"* ]]; then exit 1; fi
  if [[ "$prompt" == *"[ask-raw]"* ]]; then printf 'plain answer\n'; exit 0; fi
  if [[ "$prompt" =~ \[ask-probe:([A-Za-z0-9_-]+)\] ]]; then
    k="${BASH_REMATCH[1]}"
    printf '%s\n' "$@" >"__PROBES__/$k.args"
    printf '%s' "$prompt" >"__PROBES__/$k.prompt"
    printf '{"type":"result","result":"answer for %s","total_cost_usd":0.0123}\n' "$k"
    exit 0
  fi
fi
# [prompt-probe:KEY] (stream-json runs only): the prompt that came in on stdin
# is written to PROBES/KEY.prompt.
if [[ "$args" == *"stream-json"* && "$prompt" =~ \[prompt-probe:([A-Za-z0-9_-]+)\] ]]; then
  printf '%s' "$prompt" >"__PROBES__/${BASH_REMATCH[1]}.prompt"
fi
# [stream-fixture:NAME]: print FIXTURES/NAME.jsonl verbatim; a {"__spool__": {...}}
# line goes to $CO_TASK_DIR/spool.jsonl instead (the hook's record, as co-guard writes it).
if [[ "$args" == *"stream-json"* && "$prompt" =~ \[stream-fixture:([A-Za-z0-9_-]+)\] ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == '{"__spool__":'* ]]; then
      if [[ -n "$CO_TASK_DIR" && -d "$CO_TASK_DIR" ]]; then
        printf '%s\n' "$line" | sed -E 's/^\{"__spool__": *//; s/\}$//' >>"$CO_TASK_DIR/spool.jsonl"
      fi
    else
      printf '%s\n' "$line"
    fi
  done <"__FIXTURES__/${BASH_REMATCH[1]}.jsonl"
  exit 0
fi
# [usage-probe:KEY]: three assistant events (two share message id u1, output growing)
# with per-message usage, a tool result, then - once the worker wrote it - a copy of
# $CO_TASK_DIR/usage.json and its mode to PROBES/KEY.usage / KEY.usagemode, then a result.
if [[ "$args" == *"stream-json"* && "$prompt" =~ \[usage-probe:([A-Za-z0-9_-]+)\] ]]; then
  f="__PROBES__/${BASH_REMATCH[1]}"
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  printf '{"type":"assistant","message":{"id":"u1","model":"claude-opus-5-5","content":[{"type":"text","text":"a"}],"usage":{"input_tokens":10,"cache_creation_input_tokens":1000,"cache_read_input_tokens":5000,"output_tokens":5}}}\n'
  printf '{"type":"assistant","message":{"id":"u1","model":"claude-opus-5-5","content":[{"type":"tool_use","id":"tuU","name":"Read","input":{"file_path":"x"}}],"usage":{"input_tokens":10,"cache_creation_input_tokens":1000,"cache_read_input_tokens":5000,"output_tokens":40}}}\n'
  if [[ -n "$CO_TASK_DIR" && -d "$CO_TASK_DIR" ]]; then
    printf '{"event":"PreToolUse","tool_use_id":"tuU","seen":true}\n' >>"$CO_TASK_DIR/spool.jsonl"
  fi
  big=$(printf 'x%.0s' $(seq 1 3000))
  printf '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tuU","content":"%s"}]}}\n' "$big"
  printf '{"type":"assistant","message":{"id":"u2","model":"claude-opus-5-5","content":[{"type":"text","text":"b"}],"usage":{"input_tokens":20,"cache_creation_input_tokens":200,"cache_read_input_tokens":6000,"output_tokens":7}}}\n'
  for _ in $(seq 1 50); do
    if [[ -n "$CO_TASK_DIR" && -f "$CO_TASK_DIR/usage.json" ]]; then break; fi
    sleep 0.1
  done
  if [[ -n "$CO_TASK_DIR" && -f "$CO_TASK_DIR/usage.json" ]]; then
    cp "$CO_TASK_DIR/usage.json" "$f.usage"
    (stat -c %a "$CO_TASK_DIR/usage.json" 2>/dev/null || stat -f %Lp "$CO_TASK_DIR/usage.json") >"$f.usagemode"
  fi
  printf '{"type":"result","subtype":"success","result":"done","num_turns":2,"total_cost_usd":0.0421,"duration_ms":100,"is_error":false,"usage":{"input_tokens":30,"output_tokens":47}}\n'
  exit 0
fi
# [hang]: init, one Bash tool_use and its `seen` spool line, some text, then sleep.
if [[ "$args" == *"stream-json"* && "$prompt" == *"[hang]"* ]]; then
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  printf '{"type":"assistant","message":{"id":"mh","model":"claude-test","content":[{"type":"tool_use","id":"tuH","name":"Bash","input":{"command":"sleep 600","description":"hang"}}]}}\n'
  if [[ -n "$CO_TASK_DIR" && -d "$CO_TASK_DIR" ]]; then
    printf '{"event":"PreToolUse","tool_use_id":"tuH","seen":true,"tool":"Bash","what":"hang :: sleep 600"}\n' >>"$CO_TASK_DIR/spool.jsonl"
  fi
  # (a project env PROBE_DB_PASSWORD is echoed in the text: the cut-off handoff tests)
  printf '{"type":"assistant","message":{"id":"mh2","model":"claude-test","content":[{"type":"text","text":"waiting%s"}]}}\n' "${PROBE_DB_PASSWORD:+ $PROBE_DB_PASSWORD}"
  sleep 600
  exit 0
fi
# Test seam: the agent env is allowlisted, so a probe path set in the test's
# os.environ can never reach this script. Instead a task prompt carrying the
# marker [env-probe:KEY] makes the task run (stream-json only, so memory and
# findings calls quoting the prompt don't overwrite it) record its env to
# PROBES/KEY.vars (selected names=values), PROBES/KEY.names (every name) and
# PROBES/KEY.args (argv, one per line), plus PROBES/KEY.handoff (a copy of
# $CO_TASK_DIR/handoff.md when it exists).
if [[ "$args" == *"stream-json"* && "$prompt" =~ \[env-probe:([A-Za-z0-9_-]+)\] ]]; then
  f="__PROBES__/${BASH_REMATCH[1]}"
  {
    for n in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL CLAUDE_CONFIG_DIR GH_CONFIG_DIR GIT_CONFIG_GLOBAL RANDOM_SERVER_VAR PATH PROBE_PROJECT_VAR CO_TASK_ID CO_API_BASE CO_POLICY_FILE CO_TASK_DIR CLAUDE_CODE_SIMPLE CLAUDE_CODE_SAFE_MODE; do
      if [[ -n "${!n+x}" ]]; then printf '%s=%s\n' "$n" "${!n}"; fi
    done
    if [[ -n "$CO_API_TOKEN" ]]; then echo "CO_API_TOKEN=present"; fi
  } >"$f.vars.tmp"
  env | cut -d= -f1 >"$f.names"
  printf '%s\n' "$@" >"$f.args"
  # co-guard: the policy file (deleted at task end) and its mode, the task dir's mode
  if [[ -n "$CO_POLICY_FILE" && -f "$CO_POLICY_FILE" ]]; then
    cp "$CO_POLICY_FILE" "$f.policy"
    (stat -c %a "$CO_POLICY_FILE" 2>/dev/null || stat -f %Lp "$CO_POLICY_FILE") >"$f.policymode"
    (stat -c %a "$CO_TASK_DIR" 2>/dev/null || stat -f %Lp "$CO_TASK_DIR") >"$f.taskdirmode"
  fi
  # resume: the handoff.md the worker wrote at spawn (absent: no stored handoff)
  if [[ -n "$CO_TASK_DIR" && -f "$CO_TASK_DIR/handoff.md" ]]; then
    cp "$CO_TASK_DIR/handoff.md" "$f.handoff"
  fi
  mv "$f.vars.tmp" "$f.vars"
fi
# Server-side one-shot calls (memory: --print; findings: --print --json-schema)
# whose stdin carries [call-probe:KEY] record to PROBES/KEY-print or KEY-schema.
if [[ "$args" != *"stream-json"* && "$prompt" =~ \[call-probe:([A-Za-z0-9_-]+)\] ]]; then
  kind=print
  if [[ "$args" == *"json-schema"* ]]; then kind=schema; fi
  f="__PROBES__/${BASH_REMATCH[1]}-$kind"
  {
    for n in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL CLAUDE_CONFIG_DIR GH_CONFIG_DIR GIT_CONFIG_GLOBAL RANDOM_SERVER_VAR PATH PROBE_PROJECT_VAR CO_TASK_ID CO_API_BASE CO_POLICY_FILE CO_TASK_DIR CLAUDE_CODE_SIMPLE CLAUDE_CODE_SAFE_MODE; do
      if [[ -n "${!n+x}" ]]; then printf '%s=%s\n' "$n" "${!n}"; fi
    done
  } >"$f.vars.tmp.$$"
  env | cut -d= -f1 >"$f.names.tmp.$$"
  mv "$f.names.tmp.$$" "$f.names"
  mv "$f.vars.tmp.$$" "$f.vars"
fi
if [[ "$args" == *"stream-json"* && "$prompt" == *"[guard-spool]"* ]]; then

  if [[ -n "$CO_TASK_DIR" && -d "$CO_TASK_DIR" ]]; then
    printf '{"event":"PreToolUse","decision":"deny","rule_ids":["rm-rf-outside"],"tool":"Bash","summary":"rm -rf ~","reason":"deletes home"}\n' >>"$CO_TASK_DIR/spool.jsonl"
    printf '{"event":"PreToolUse","decision":"approved","rule_ids":["mcp-require-approval"],"tool":"mcp__db__query","summary":"token %s and %s","status":"approved","request_id":"r1"}\n' "$GH_TOKEN" "$CO_API_TOKEN" >>"$CO_TASK_DIR/spool.jsonl"
    printf 'not json\n' >>"$CO_TASK_DIR/spool.jsonl"
    printf '{"event":"SessionEnd","reason":"other","junk_key":"dropped"}\n' >>"$CO_TASK_DIR/spool.jsonl"
    printf '{"event":"ConfigChange","source":"project_settings","decision":"blocked"}\n' >>"$CO_TASK_DIR/spool.jsonl"
  fi
fi
if [[ "$args" == *"stream-json"* && "$prompt" == *"[env-echo"* ]]; then

  jesc() { local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }
  dump=""
  for n in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN CO_API_TOKEN PATH PROBE_PROJECT_VAR PROBE_DB_PASSWORD PROBE_SHORT_TOKEN PROBE_SHAPED; do
    if [[ -n "${!n+x}" ]]; then dump+="$n=${!n} "; fi
  done
  j=$(jesc "$dump")
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  printf '{"type":"assistant","message":{"model":"claude-test","content":[{"type":"text","text":"env: %s"},{"type":"tool_use","id":"tu1","name":"Bash","input":{"command":"echo %s"}}]}}\n' "$j" "$j"
  printf '{"event":"PreToolUse","tool_use_id":"tu1","seen":true}\n' >>"$CO_TASK_DIR/spool.jsonl"
  printf '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tu1","content":"%s"}]}}\n' "$j"
  printf 'raw stdout %s\n' "$dump"
  printf 'stderr %s\n' "$dump" >&2
  if [[ "$prompt" == *"[env-echo-fail]"* ]]; then
    printf '{"type":"result","subtype":"error","result":"env: %s","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":true,"usage":{"input_tokens":1,"output_tokens":1}}\n' "$j"
    exit 1
  fi
  printf '{"type":"result","subtype":"success","result":"env: %s","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":1,"output_tokens":1}}\n' "$j"
  exit 0
fi
if [[ "$args" == *"stream-json"* && "$prompt" =~ \[guard-tool:([a-z]+)\] ]]; then

  mode="${BASH_REMATCH[1]}"
  sp="$CO_TASK_DIR/spool.jsonl"
  seen() { printf '{"event":"PreToolUse","tool_use_id":"%s","seen":true}\n' "$1" >>"$sp"; }
  tu() { printf '{"type":"assistant","message":{"id":"%s","model":"claude-test","content":[{"type":"tool_use","id":"%s","name":"Bash","input":{"command":"ls"}}]}}\n' "${2:-msg-$1}" "$1"; }
  tr() { printf '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"%s","content":"ok","is_error":%s}]}}\n' "$1" "${2:-false}"; }
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  case "$mode" in
    hooked) tu tuA; seen tuA; tr tuA ;;
    nohook) tu tuA; tr tuA ;;
    errnohook) tu tuA; tr tuA true ;;
    parallel) tu tuA; tu tuB; seen tuB; seen tuA; tr tuB; tr tuA ;;
    subagent) seen tuS
      printf '{"type":"assistant","parent_tool_use_id":"tuAgent","agent_id":"a1","message":{"content":[{"type":"tool_use","id":"tuS","name":"Bash","input":{"command":"echo sub"}}]}}\n'
      printf '{"type":"user","parent_tool_use_id":"tuAgent","agent_id":"a1","message":{"content":[{"type":"tool_result","tool_use_id":"tuS","content":"sub"}]}}\n' ;;
    waiting) tu tuA; seen tuA; sleep 3; tr tuA ;;
    queued) tu tuA m1; tu tuB m1; seen tuA; sleep 3; tr tuA; sleep 0.5; seen tuB; tr tuB ;;
    queuedthree) tu tuA m1; tu tuB m1; tu tuC m1; seen tuA; sleep 2; tr tuA; seen tuB; sleep 2; tr tuB; seen tuC; tr tuC ;;
    lanes) tu tuA m1; tu tuB m2; seen tuA; sleep 4; tr tuA; tr tuB ;;
    laneexpire) tu tuA m1; tu tuB m1; seen tuA; tr tuA; sleep 4; tr tuB ;;
    slow) tu tuA; sleep 4; tr tuA ;;
  esac
  printf '{"type":"result","subtype":"success","result":"done","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":1,"output_tokens":1}}\n'
  exit 0
fi
if [[ "$args" == *"stream-json"* ]]; then
  printf '{"type":"system","subtype":"init","session_id":"test-session"}\n'
  printf '{"type":"assistant","message":{"model":"claude-test","content":[{"type":"text","text":"working"}]}}\n'
  printf '{"type":"result","subtype":"success","result":"done","num_turns":1,"total_cost_usd":0.01,"duration_ms":100,"is_error":false,"usage":{"input_tokens":100,"output_tokens":50}}\n'
elif [[ "$args" == *"json-schema"* ]]; then
  printf '{"findings":[]}\n'
elif [[ "$prompt" == *"[env-echo"* ]]; then
  # Memory calls quoting an [env-echo] task also echo the env (summary/memory).
  dump=""
  for n in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN CO_API_TOKEN PATH PROBE_PROJECT_VAR PROBE_DB_PASSWORD PROBE_SHORT_TOKEN PROBE_SHAPED; do
    if [[ -n "${!n+x}" ]]; then dump+="$n=${!n} "; fi
  done
  printf 'summary %s\n' "$dump"
else
  printf 'summary\n'
fi
