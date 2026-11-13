"""Numeric, stdlib-only synthetic-run validation. No audio/models/network are used."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = {'vap': ('p_now', 'p_future', 'vad_user', 'vad_tomoko'),
          'bc': ('p_bc',), 'bc_det': ('p_bc_det_user',)}
PERF = {'factory_ready_s': ('worker_started_perf_s', 'model_ready_perf_s'),
        'queue_wait_s': ('enqueued_perf_s', 'dequeued_perf_s'),
        'process_s': ('process_started_perf_s', 'process_finished_perf_s'),
        'reset_s': ('reset_started_perf_s', 'reset_finished_perf_s'),
        'dispatch_s': ('posted_perf_s', 'dispatched_perf_s')}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quantiles(values):
    xs = sorted(values)
    def at(p):
        position = (len(xs) - 1) * p
        lo = math.floor(position)
        return xs[lo] + (xs[math.ceil(position)] - xs[lo]) * (position - lo)
    return {'n': len(xs), **{name: at(p) if xs else None for name, p in
                            [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1)]}}


def worker_metrics(events):
    groups = defaultdict(list)
    for e in events:
        if e['event'] == 'maai_worker':
            groups[e['source']].append(e)
    if not groups:
        return None  # Older source has no instrumentation; this is not a zero measurement.
    result = {}
    for source, rows in groups.items():
        metrics = {}
        negative = Counter()
        for label, (start, end) in PERF.items():
            values = [r[end] - r[start] for r in rows
                      if (label != 'factory_ready_s' or r.get('kind') == 'ready')
                      and isinstance(r.get(start), (float, int))
                      and isinstance(r.get(end), (float, int))]
            negative[label] = sum(v < 0 for v in values)
            metrics[label] = quantiles(values)
        metrics.update(process_rows=sum(r.get('kind') == 'process' for r in rows),
                       output_rows=sum(r.get('kind') == 'process' and r.get('output') is True for r in rows),
                       no_output=sum(r.get('kind') == 'process' and r.get('output') is False for r in rows),
                       drop_events=sum(r.get('kind') == 'drop' for r in rows),
                       drop_frames=sum(r.get('dropped_frames', 0) for r in rows if r.get('kind') == 'drop'),
                       largest_reported_drop_total=max((r.get('dropped_total', 0) for r in rows), default=0),
                       negative_perf_deltas=dict(negative))
        result[source] = metrics
    return result


def signal_time(frame, signal):
    if not frame or all(frame.get(k) is None for k in FIELDS[signal]):
        return None
    stamp = frame.get(signal + '_at_s')
    return frame['at_s'] if stamp is None else stamp


def snapshot_metrics(events, stale_s):
    groups = {}
    for e in events:
        if e['event'] != 'maai_latest_read':
            continue
        signal, now, raw, used = e['signal'], e['now_s'], e.get('raw'), e.get('used')
        m = groups.setdefault(signal, {'reads': 0, 'new_accept': 0, 'required_missing': 0,
            'old_min_reject_required_fresh': 0, 'old_accept_new_reject': 0,
            'future_signal_timestamps': 0, 'future_by_signal': Counter(),
            'future_ages_s': [], 'contract_violations': Counter()})
        m['reads'] += 1
        m['new_accept'] += used is not None
        needed = signal_time(raw, signal)
        old_accept = raw is not None and now - raw['at_s'] <= stale_s
        required_fresh = needed is not None and now - needed <= stale_s
        m['required_missing'] += needed is None
        m['old_min_reject_required_fresh'] += required_fresh and not old_accept
        m['old_accept_new_reject'] += old_accept and used is None
        violations = m['contract_violations']
        if used is not None:
            violations['selected_at_not_required_at'] += needed != used['at_s']
            violations['stale_required_used'] += not required_fresh
        elif required_fresh:
            violations['required_fresh_but_rejected'] += 1
        for group in FIELDS:
            before, after = signal_time(raw, group), signal_time(used, group)
            if before is not None and now - before < -1e-9:
                m['future_signal_timestamps'] += 1
                m['future_by_signal'][group] += 1
                m['future_ages_s'].append(now - before)
            if after is not None:
                violations['retained_signal_timestamp_changed'] += before != after
                violations['stale_signal_leaked'] += before is None or now - before > stale_s
                violations['signal_values_changed'] += any(raw.get(k) != used.get(k) for k in FIELDS[group])
    for m in groups.values():
        m['future_ages_s'] = quantiles(m['future_ages_s'])
        m['future_by_signal'] = dict(m['future_by_signal'])
        m['contract_violations'] = dict(m['contract_violations'])
    return groups


def text_events(run, trace):
    users = [e for e in run['events'] if e.get('type') == 'text' and e.get('role') == 'user']
    stt = [e for e in trace if e['event'] == 'stt_final' and e.get('text')]
    return users, stt


def fingerprint(events):
    return tuple(e.get('text') for e in events)


def analyze_pair(run, trace):
    if run.get('private') is not False:
        raise ValueError('private must be explicitly false')
    users, stt = text_events(run, trace)
    if fingerprint(users) != fingerprint(stt):
        raise ValueError('STT pairing mismatch')
    stop_run = [e for e in run['events'] if e.get('type') == 'stop']
    stop_trace = [e for e in trace if e['event'] == 'stop']
    offsets = [u['t'] - s['t'] for u, s in zip(users, stt, strict=True)]
    counts = Counter(e['event'] for e in trace)
    bc = {e['generation'] for e in trace if e['event'] == 'backchannel'}
    first = [e for e in trace if e['event'] == 'reply_first_audio_send']
    config = next(e for e in trace if e['event'] == 'config')
    return {'id': run['id'], 'axis': run['axis'], 'private': False,
        'pairing': {'complete_nonempty_stt_match': True, 'stt_events': len(stt),
                    'stop_generations_match': [e['generation'] for e in stop_run] ==
                                              [e['generation'] for e in stop_trace],
                    'receiver_minus_trace_control_s': quantiles(offsets)},
        'counts': {'connection_error': bool(run.get('error')), 'turn_failed': counts['turn_failed'],
                   'maai_failed': counts['maai_failed'], 'maai_close_failed': counts['maai_close_failed'],
                   'maai_dropped_events': counts['maai_dropped'],
                   'maai_dropped_frames': sum(e.get('frames', 0) for e in trace if e['event'] == 'maai_dropped'),
                   'main_first_send': len(first),
                   'main_first_send_user_speaking': sum(e.get('user_speaking') is True for e in first),
                   'main_first_send_state_missing': sum('user_speaking' not in e for e in first),
                   'bc_dispatch': counts['backchannel'],
                   'bc_seq0_accepted': sum(e.get('type') == 'audio' and e.get('generation') in bc
                                          and e.get('seq') == 0 and e.get('played') is True for e in run['events'])},
        'workers': worker_metrics(trace),
        'signal_reads': snapshot_metrics(trace, float(config.get('maai_stale_s', .25)))}


def normalized_config(config):
    def value(v):
        if str(v).lower() in {'true', 'false', 'none'}:
            return {'true': True, 'false': False, 'none': None}[str(v).lower()]
        try:
            return float(v)
        except (ValueError, TypeError):
            return v
    return {k: value(v) for k, v in config.items() if k not in {'event', 't', 'audio_s', 'trace_dir'}}


def read_trace(path):
    raw = path.read_bytes()
    lines = raw.splitlines()
    complete = not raw or raw.endswith(b'\n')
    if not complete:
        lines = lines[:-1]
    return [json.loads(line) for line in lines], sha(raw), complete


def source_check(runtime, meta):
    expected = meta.get('source_sha256')
    if isinstance(expected, dict):
        actual = {name: sha((Path(runtime['source_root']) / name).read_bytes()) for name in expected}
        return {'expected': expected, 'current_files': actual, 'matches': actual == expected}
    command = ['rtk', 'proxy', 'git', '-C', runtime['source_root'], 'show',
               meta['source_commit'] + ':v4/engine/baseline.py']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    actual = sha(result.stdout) if result.returncode == 0 else None
    return {'expected_baseline': expected, 'committed_blob': actual,
            'matches': actual == expected if actual else None}


def collect(runtime, arm, partial=False):
    meta = runtime[arm]
    allowed = {'p3-18-signal-freshness-before-1', 'p3-19-signal-freshness-final-1',
               'p3-20-human-ready-full-1'}
    root = Path(meta['run_dir'])
    if root.name not in allowed:
        raise ValueError('run directory outside the explicit synthetic scope')
    runs = []
    for path in sorted(root.glob('*/run.json')):
        raw = path.read_bytes()
        run = json.loads(raw)
        if run.get('private') is not False:
            raise ValueError('private source rejected before trace reads')
        runs.append((run, raw))
    expected = meta.get('expected_ids') or runtime['test_plan']['targeted_first']
    if root.name == 'p3-20-human-ready-full-1':
        scenarios = Path(runtime['source_root']) / 'scenarios/small-v2'
        expected = [r['id'] for p in scenarios.glob('*.json') for r in json.loads(p.read_text())]
    if not partial and {r['id'] for r, _ in runs} != set(expected):
        raise ValueError('completed run IDs differ from planned synthetic cases')
    paths = meta.get('targeted_trace_files') or meta.get('trace_files')
    if not paths:
        excluded = set(meta.get('preexisting_trace_names', []))
        paths = [str(p) for p in sorted(Path(meta['trace_dir']).glob('*.jsonl')) if p.name not in excluded]
    traces = []
    for name in paths:
        path = Path(name)
        trace, digest, complete = read_trace(path)
        key = fingerprint([e for e in trace if e['event'] == 'stt_final' and e.get('text')])
        traces.append((key, path, trace, digest, complete))
    cases, all_events, configs, used = [], [], [], set()
    for run, raw in runs:
        key = fingerprint([e for e in run['events'] if e.get('type') == 'text' and e.get('role') == 'user'])
        candidates = [(i, item) for i, item in enumerate(traces) if item[0] == key and i not in used]
        if len(candidates) != 1:
            if partial and not candidates:
                continue
            raise ValueError('trace pairing absent or ambiguous')
        i, (_, trace_path, trace, trace_sha, complete) = candidates[0]
        used.add(i)
        row = analyze_pair(run, trace)
        row.update(source_run_sha256=sha(raw), source_trace_sha256=trace_sha,
                   trace_name=trace_path.name, trace_complete_lines=complete)
        cases.append(row)
        all_events.extend(trace)
        configs.append(normalized_config(next(e for e in trace if e['event'] == 'config')))
    if not partial and len(used) != len(traces):
        raise ValueError('unpaired trace in final aggregation')
    total = Counter()
    for case in cases:
        total.update(case['counts'])
    settings = configs[0] if configs else {}
    safe = {k: v for k, v in settings.items() if isinstance(v, (float, int, bool)) or v is None
            or k in {'llm_model', 'stt', 'stt_model', 'tts', 'tts_voice', 'maai_modes', 'ep_signal'}}
    return {'schema_version': 1, 'arm': arm, 'partial_snapshot': partial,
        'source_verification': source_check(runtime, meta), 'cases': cases,
        'summary': {'matched_cases': len(cases), 'trace_files_seen': len(traces),
                    'unpaired_traces': len(traces) - len(used), 'counts': dict(total),
                    'workers': worker_metrics(all_events),
                    'signal_reads': snapshot_metrics(all_events, float(settings.get('maai_stale_s', .25)))},
        'configuration': {'identical_across_cases': all(c == settings for c in configs),
            'sha256': sha(json.dumps(settings, sort_keys=True).encode()), 'settings': safe},
        'limits': ['Perf durations subtract perf timestamps only; signal ages use input timestamps only',
                   'Before-source absent instrumentation is unmeasured, not zero',
                   'Counts are correlated reads/process calls, not independent improvement trials',
                   'Future signal input timestamps retain the existing contract; no cause inferred',
                   'Legacy acceptance comparison is conditional on a candidate snapshot, not old closed-loop replay']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--arm', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--partial', action='store_true')
    args = parser.parse_args()
    raw = args.runtime.read_bytes()
    report = collect(json.loads(raw), args.arm, args.partial)
    report['runtime_sha256'] = sha(raw)
    report['analysis_script_sha256'] = sha(Path(__file__).read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'arm': args.arm, 'matched_cases': report['summary']['matched_cases'],
                      'counts': report['summary']['counts'],
                      'source_matches': report['source_verification']['matches']}, allow_nan=False))


if __name__ == '__main__':
    main()
