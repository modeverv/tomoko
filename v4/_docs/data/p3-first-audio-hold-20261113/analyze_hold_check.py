"""Postprocess explicit synthetic runs only; no audio reads, models, or scoring calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT_ROOT = Path('/private/tmp/tomoko-p3-hold-check')
TIMING_KEYS = ('silent', 'no_main', 'responded', 'early', 'early_first',
               'talked_over', 'latency', 'precondition', 'stopped', 'stop_latency',
               'count', 'overlapped', 'main_interruptions', 'final_responded',
               'final_latency', 'double_answer')


def read(path: Path):
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(values, q):
    values = sorted(x for x in values if x is not None)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))] if values else None


def numeric(event: dict) -> dict:
    return {k: v for k, v in event.items()
            if v is None or isinstance(v, (int, float, bool))}


def trace_signature(trace):
    return (tuple(e['text'] for e in trace if e['event'] == 'stt_final'),
            tuple(e['generation'] for e in trace if e['event'] == 'stop'))


def run_signature(run):
    return (tuple(e['text'] for e in run['events']
                  if e['type'] == 'text' and e.get('role') == 'user'),
            tuple(e['generation'] for e in run['events'] if e['type'] == 'stop'))


def confirmation_windows(trace):
    """Server trace-time intervals from VAD start until sustained/end/drop."""
    windows, active = [], None
    for e in trace:
        kind = e['event']
        if kind == 'vad_start':
            if active is not None:
                raise ValueError('VAD start without closing prior confirmation window')
            active = {'start_server_t': e['t'], 'start_input_s': e['at_s']}
        elif active is not None and kind in ('vad_sustained', 'vad_end', 'vad_drop'):
            active.update(close_server_t=e['t'], close_kind=kind,
                          close_vad_input_s=(round(e['audio_s'] * 16000) // 512) * .032)
            windows.append(active)
            active = None
    if active is not None:
        active.update(close_server_t=trace[-1]['t'], close_kind='trace_end_censored',
                      close_vad_input_s=None)
        windows.append(active)
    return windows


def offsets(run, trace):
    """Empirical receiver-minus-server offsets; text is compared only in memory."""
    stt = [e for e in trace if e['event'] == 'stt_final']
    user = [e for e in run['events']
            if e['type'] == 'text' and e.get('role') == 'user']
    ts = [e for e in trace if e['event'] == 'stop']
    rs = [e for e in run['events'] if e['type'] == 'stop']
    values = [b['t'] - a['t'] for a, b in zip(stt, user)]
    values += [b['t'] - a['t'] for a, b in zip(ts, rs)]
    if not values:
        raise ValueError('no paired control packets for clock alignment')
    return {'n_stt': len(stt), 'n_stop': len(ts), 'min_s': min(values),
            'median_s': statistics.median(values), 'max_s': max(values)}


def case_data(run, trace, result, trace_path, run_path):
    counts = Counter(e['event'] for e in trace)
    bc = {e['generation'] for e in trace if e['event'] == 'backchannel'}
    windows, align = confirmation_windows(trace), offsets(run, trace)
    seq0 = [e for e in run['events'] if e['type'] == 'audio' and e['seq'] == 0]
    main = [e for e in seq0 if e['generation'] not in bc]
    arrivals = []
    for event in main:
        center = event['t'] - align['median_s']
        lo, hi = event['t'] - align['max_s'], event['t'] - align['min_s']
        point = [i for i, w in enumerate(windows)
                 if w['start_server_t'] <= center < w['close_server_t']]
        all_offsets = [i for i, w in enumerate(windows)
                       if w['start_server_t'] <= lo and hi < w['close_server_t']]
        any_offsets = [i for i, w in enumerate(windows)
                       if lo < w['close_server_t'] and hi >= w['start_server_t']]
        arrivals.append({'generation': event['generation'], 'receiver_t': event['t'],
                         'played': event.get('played', True), 'samples': event['n'],
                         'estimated_server_t': center, 'empirical_server_range_s': [lo, hi],
                         'pending_window_point_indices': point,
                         'pending_window_all_empirical_offsets_indices': all_offsets,
                         'pending_window_any_empirical_offsets_indices': any_offsets,
                         'arrived_during_annotated_voice': any(
                             a <= event['t'] < b for s in run['steps'] for a, b in s['voiced'])})
    sends = []
    for e in trace:
        if e['event'] == 'reply_first_audio_send':
            row = numeric(e)
            row['pending_window_indices'] = [i for i, w in enumerate(windows)
                                             if w['start_server_t'] <= e['t'] < w['close_server_t']]
            sends.append(row)
    timing = {k: result['timing'][k] for k in TIMING_KEYS
              if result and k in result.get('timing', {})}
    packet_latency = None
    if run['axis'] == 'T1' and main and run['steps']:
        packet_latency = main[0]['t'] - run['steps'][-1]['end']
    return {
        'axis': run['axis'], 'connection_error': bool(run.get('error')),
        'precondition_failed_runtime': bool(run.get('precondition_failed')),
        'source_run_sha256': sha(run_path), 'source_trace_sha256': sha(trace_path),
        'trace_name': trace_path.name, 'trace_events': dict(sorted(counts.items())),
        'pairing': {'complete_stt_sequences_match': True, 'stop_generations_match': True},
        'receiver_minus_server_control_offsets': align,
        'confirmation_windows': windows, 'main_seq0_arrivals': arrivals,
        'exact_server_first_send_events': sends,
        'explicit_bc_dispatch_events': counts['backchannel'],
        'bc_seq0_arrivals': sum(e['generation'] in bc for e in seq0),
        'bc_seq0_accepted': sum(e['generation'] in bc and e.get('played', True) for e in seq0),
        'maai_drop_events': counts['maai_dropped'],
        'maai_dropped_frames': sum(e['frames'] for e in trace if e['event'] == 'maai_dropped'),
        'stale_chunks': run.get('stale_chunks', 0), 'scorer_timing': timing,
        't1_first_main_packet_latency_s': packet_latency,
    }


def summarize(cases):
    values = list(cases.values())
    arrivals = [a for c in values for a in c.get('main_seq0_arrivals', [])]
    sends = [e for c in values for e in c.get('exact_server_first_send_events', [])]
    t1 = [c.get('scorer_timing', {}).get('latency') for c in values if c['axis'] == 'T1']
    events = Counter()
    for c in values:
        events.update(c.get('trace_events', {}))
    return {
        'n': len(values), 'connection_errors': sum(c['connection_error'] for c in values),
        'turn_failed_events': events['turn_failed'], 'maai_failed_events': events['maai_failed'],
        'discard_reply_events': events['discard_reply'], 'stop_events': events['stop'],
        'explicit_bc_dispatch_events': events['backchannel'],
        'bc_seq0_arrivals': sum(c.get('bc_seq0_arrivals', 0) for c in values),
        'maai_drop_events': events['maai_dropped'],
        'maai_dropped_frames': sum(c.get('maai_dropped_frames', 0) for c in values),
        'main_seq0_arrivals': len(arrivals),
        'accepted_main_seq0_arrivals': sum(a['played'] for a in arrivals),
        'arrival_pending_point_estimates': sum(bool(a['pending_window_point_indices']) for a in arrivals),
        'arrival_pending_for_all_empirical_offsets': sum(bool(a['pending_window_all_empirical_offsets_indices']) for a in arrivals),
        'arrival_pending_for_any_empirical_offsets': sum(bool(a['pending_window_any_empirical_offsets_indices']) for a in arrivals),
        'server_first_send_markers': len(sends),
        'exact_server_send_measurement_available': bool(sends),
        'server_first_sends_in_pending_window': sum(bool(e['pending_window_indices']) for e in sends) if sends else None,
        'server_first_sends_with_unconfirmed_voice': sum(
            bool(e.get('user_speaking')) and not e.get('sustained', False) for e in sends) if sends else None,
        'scored_t1_latency_n': sum(v is not None for v in t1),
        'scored_t1_latency_p50_s': pct(t1, .5), 'scored_t1_latency_p90_s': pct(t1, .9),
    }


def collect(runtime, arm, run_dir, trace_dir, runtime_path):
    ids = sorted(runtime['ids'])
    exclude = set(runtime.get('preexisting_trace_names', [])) if arm == 'before' else set()
    candidates = defaultdict(list)
    exact = runtime.get('baseline_trace_files', []) if arm == 'before' else []
    paths = [Path(p) if Path(p).is_absolute() else trace_dir / p for p in exact]
    for path in paths or sorted(trace_dir.glob('*.jsonl')):
        if path.name in exclude:
            continue
        trace = [json.loads(line) for line in path.read_text().splitlines()]
        candidates[trace_signature(trace)].append((path, trace))
    result_path = run_dir / 'results.json'
    results = read(result_path) if result_path.exists() else None
    scored = {r['id']: r for r in results['runs']} if results else {}
    cases, used = {}, set()
    for ident in ids:
        path = run_dir / ident / 'run.json'
        run = read(path)
        if run.get('private') is not False or run.get('id') != ident:
            raise ValueError(f'{ident}: explicitly non-private matching run required')
        if run.get('error'):
            cases[ident] = {'axis': run['axis'], 'connection_error': True,
                            'source_run_sha256': sha(path), 'trace_pairing_unavailable': True}
            continue
        matches = [(p, t) for p, t in candidates[run_signature(run)] if p not in used]
        if not run_signature(run)[0] or len(matches) != 1:
            raise ValueError(f'{ident}: unique complete STT/stop trace pairing required')
        trace_path, trace = matches[0]
        used.add(trace_path)
        cases[ident] = case_data(run, trace, scored.get(ident), trace_path, path)
    return {'schema_version': 1, 'arm': arm, 'cases': cases,
            'summary': summarize(cases), 'scoring_available': results is not None,
            'source_results_sha256': sha(result_path) if results else None,
            'source_runtime_metadata_sha256': sha(runtime_path),
            'source_analysis_script_sha256': sha(Path(__file__)),
            'limits': [
                'seq0 run events are receiver packet arrivals, not server send calls or audible onsets',
                'receiver/server alignment is estimated from matched control packets; its empirical range is not a guaranteed timing bound',
                'exact reply_first_audio_send markers exist only in instrumented source; baseline absence is not zero violations',
                'confirmation windows close on sustained or unconfirmed end/drop; their input clock differs from wall delivery time',
                'explicit BC events are dispatches; accepted packets do not prove audible BC quality',
                'T1 scorer latency is read only from existing results; no scoring or model inference is invoked',
                'one repetition of ten selected scenarios does not establish population improvement',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=OUTPUT_ROOT / 'runtime.json')
    parser.add_argument('--arm', choices=('before', 'after'), required=True)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--trace-dir', type=Path)
    parser.add_argument('--other', type=Path, help='prior numeric output for matched comparison')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(OUTPUT_ROOT.resolve()):
        parser.error('write numeric output only inside the designated temporary directory')
    runtime = read(args.runtime)
    run_key = 'baseline_run_dir' if args.arm == 'before' else 'after_run_dir'
    run_dir = args.run_dir or Path(runtime[run_key])
    trace_dir = args.trace_dir or Path(runtime[f'{args.arm}_trace_dir'])
    output = collect(runtime, args.arm, run_dir, trace_dir, args.runtime)
    if args.other:
        other = read(args.other)
        if set(other['cases']) != set(output['cases']):
            raise ValueError('comparison scenario sets differ')
        output['comparison'] = {'other_sha256': sha(args.other), 'other_arm': other['arm'],
            'summary_deltas': {k: v - other['summary'][k] for k, v in output['summary'].items()
                               if type(v) in (int, float) and other['summary'].get(k) is not None}}
    args.out.write_text(json.dumps(output, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
