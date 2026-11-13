"""Numeric, generation-paired decomposition of the two existing synthetic T5 runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path('/private/tmp/tomoko-p3-human-ready-check')
META = Path('/private/tmp/tomoko-p3-hold-check')
SR = 24000


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric(event):
    return {k: v for k, v in event.items()
            if v is None or isinstance(v, (int, float, bool))}


def placement(events, bc):
    """Player queue/stop replay from rounded receiver event timestamps; no WAV reads."""
    placed, first, cursor, stopped = [], {}, 0, -1
    for event in events:
        if event['type'] == 'audio' and event.get('played', True):
            assert event['generation'] > stopped
            arrived = round(event['t'] * SR)
            start = max(arrived, cursor)
            if event['seq'] == 0:
                first[event['generation']] = {
                    'receiver_arrival_s': event['t'], 'start_sample': start,
                    'sample_rounding_shift_s': arrived / SR - event['t'],
                    'queue_delay_s': (start - arrived) / SR,
                    'prior_queued_generations': sorted({p['generation'] for p in placed
                                                       if p['end'] > arrived}),
                    'prior_queued_BC_generations': sorted({p['generation'] for p in placed
                                                          if p['end'] > arrived and p['generation'] in bc}),
                }
            placed.append({'start': start, 'end': start + event['n'],
                           'generation': event['generation'], 'seq': event['seq']})
            cursor = start + event['n']
        elif event['type'] == 'stop':
            stopped = max(stopped, event['generation'])
            cut, kept = round(event['t'] * SR), []
            for p in placed:
                if p['generation'] <= event['generation'] and p['end'] > cut:
                    if p['start'] >= cut:
                        continue
                    p = p | {'end': cut}
                kept.append(p)
            placed = kept
            cursor = max((p['end'] for p in placed), default=0)
    return placed, first


def turn_event(trace, turn, kind, phrase=None):
    return next(e for e in trace if e.get('turn') == turn and e['event'] == kind
                and (phrase is None or e.get('phrase') == phrase))


def hold_intervals(trace):
    open_holds, intervals = {}, []
    for event in trace:
        if event['event'] == 'reply_audio_hold':
            open_holds[event['generation']] = event
        elif event['event'] in ('reply_audio_release', 'reply_audio_cancel'):
            start = open_holds.pop(event['generation'])
            intervals.append({'generation': event['generation'], 'turn': event['turn'],
                              'start_server_t': start['t'], 'end_server_t': event['t'],
                              'duration_server_s': event['t'] - start['t'],
                              'start_input_s': start['audio_s'], 'end_input_s': event['audio_s'],
                              'cancelled': event['event'] == 'reply_audio_cancel'})
    return intervals, sorted(open_holds)


def analyze(runtime, arm, ident, index):
    run_dir = Path(runtime['baseline_run_dir'] if arm == 'before' else runtime['after_run_dir'])
    trace_dir = Path(runtime['before_trace_dir'] if arm == 'before' else runtime['after_trace_dir'])
    saved = index['cases'][ident]
    rp, tp = run_dir / ident / 'run.json', trace_dir / saved['trace_name']
    assert sha(rp) == saved['source_run_sha256'] and sha(tp) == saved['source_trace_sha256']
    run = read(rp)
    assert run['private'] is False and run['id'] == ident and run['axis'] == 'T5'
    trace = [json.loads(line) for line in tp.read_text().splitlines()]
    stt = [e['text'] for e in trace if e['event'] == 'stt_final']
    assert stt == [e['text'] for e in run['events']
                   if e['type'] == 'text' and e.get('role') == 'user']
    assert [e['generation'] for e in trace if e['event'] == 'stop'] == [
        e['generation'] for e in run['events'] if e['type'] == 'stop']
    results_path = run_dir / 'results.json'
    result = next(r for r in read(results_path)['runs'] if r['id'] == ident)
    end = run['steps'][0]['end']
    onset = end + result['timing']['final_latency']
    segment = min(result['segments'], key=lambda s: abs(s[0] - onset))
    assert abs(segment[0] - onset) < .001 and segment[1] - segment[0] >= 1
    bc = {e['generation'] for e in trace if e['event'] == 'backchannel'}
    placed, first = placement(run['events'], bc)
    gens = {p['generation'] for p in placed if p['start'] <= onset * SR < p['end']}
    assert len(gens) == 1 and not gens & bc
    gen = gens.pop()
    turns = {e['turn'] for e in trace if e.get('generation') == gen and 'turn' in e}
    assert len(turns) == 1
    turn = turns.pop()
    ss = turn_event(trace, turn, 'stt_start')
    sf = turn_event(trace, turn, 'stt_final')
    ls = turn_event(trace, turn, 'llm_start')
    lf = turn_event(trace, turn, 'llm_first_token')
    ld = turn_event(trace, turn, 'llm_done')
    ts = turn_event(trace, turn, 'tts_start', 0)
    ready = turn_event(trace, turn, 'tts_first_audio', 0)
    done = turn_event(trace, turn, 'tts_done', 0)
    ve = next(e for e in reversed(trace) if e['event'] == 'vad_end' and e['t'] <= ss['t'])
    send = next((e for e in trace if e['event'] == 'reply_first_audio_send'
                 and e['generation'] == gen), None)
    parts = {
        'vad_event_to_stt_start_s': ss['t'] - ve['t'],
        'stt_roundtrip_s': sf['t'] - ss['t'],
        'stt_final_to_llm_start_s': ls['t'] - sf['t'],
        'llm_start_to_first_token_s': lf['t'] - ls['t'],
        'first_token_to_first_phrase_tts_s': ts['t'] - lf['t'],
        'first_phrase_tts_to_first_ready_s': ready['t'] - ts['t'],
    }
    assert all(v >= 0 for v in parts.values())
    assert abs(sum(parts.values()) - (ready['t'] - ve['t'])) < 1e-8
    holds, open_holds = hold_intervals(trace)
    final_holds = [h for h in holds if h['generation'] == gen]
    info = first[gen]
    queue_start = info['start_sample'] / SR
    receiver_parts = {
        'first_packet_after_recorded_user_end_s': info['receiver_arrival_s'] - end,
        'sample_rounding_shift_s': info['sample_rounding_shift_s'],
        'prior_output_queue_wait_s': info['queue_delay_s'],
        'scorer_onset_minus_first_placed_main_sample_s': onset - queue_start,
    }
    assert abs(sum(receiver_parts.values()) - result['timing']['final_latency']) < 1e-8
    bc_ends = [p['end'] / SR for p in placed if p['generation'] in bc and p['end'] <= queue_start * SR]
    vad_now = (round(ve['audio_s'] * 16000) // 512) * .032
    row = {
        'generation': gen, 'turn': turn,
        'provenance': {'run_sha256': sha(rp), 'trace_sha256': sha(tp),
                       'results_sha256': sha(results_path), 'trace_name': tp.name},
        'input_clock': {'recorded_user_end_s': end,
                        'last_vad_speech_end_s': ve['speech_end_s'],
                        'vad_decision_window_end_s': vad_now,
                        'vad_receive_audio_s': ve['audio_s'],
                        'decision_window_after_recorded_end_s': vad_now - end,
                        'vad_quiet_wait_s': vad_now - ve['speech_end_s'],
                        'final_stt_audio_len_s': ss['audio_len_s']},
        'server_wall_clock': {'critical_parts_s': parts,
                              'vad_event_to_first_ready_s': ready['t'] - ve['t'],
                              'llm_total_s': ld['t'] - ls['t'],
                              'final_generation_hold_intervals': final_holds if arm == 'after' else None,
                              'direct_final_gate_wait_s': sum(h['duration_server_s'] for h in final_holds) if send else None,
                              'first_ready_to_first_send_call_s': send['t'] - ready['t'] if send else None,
                              'events': {name: numeric(e) for name, e in [
                                  ('vad_end', ve), ('stt_start', ss), ('stt_final', sf),
                                  ('llm_start', ls), ('llm_first_token', lf), ('first_tts_start', ts),
                                  ('first_tts_ready', ready), ('first_send_call', send)] if e is not None}},
        'receiver_clock': {'scored_final_latency_s': result['timing']['final_latency'],
                           'scored_final_main_segment_s': segment,
                           'parts_s': receiver_parts, 'first_packet': info,
                           'latest_retained_BC_end_before_main_s': max(bc_ends, default=None)},
        'case_holds': holds, 'unclosed_holds': open_holds,
        'first_phrase_audio_len_s': done['audio_len_s'],
        'text_metrics_only': {'final_stt_chars': len(sf['text']),
                              'final_llm_chars': len(ld['text']),
                              'first_phrase_chars': len(ts['text'])},
    }
    # Texts remain local only for equality comparisons; they never enter the output.
    return row, (sf['text'], ld['text'], ts['text'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 't5_final_latency_decomposition.json')
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT.resolve()):
        parser.error('numeric output must stay inside the designated temporary directory')
    runtime = read(META / 'runtime.json')
    assert Path(runtime['baseline_run_dir']).name == 'p3-15-hold-before-1'
    assert Path(runtime['after_run_dir']).name == 'p3-16-hold-after-2'
    out = {'schema_version': 1, 'scope': 'existing synthetic T5,one before and one after',
           'source_script_sha256': sha(Path(__file__)), 'source_runtime_sha256': sha(META / 'runtime.json'),
           'source_numeric_sha256': {a: sha(META / (a + '_numeric.json')) for a in ['before', 'after']},
           'cases': {}, 'method': [
               'Final main generation is located inside the retained packet placement at scored onset,then mapped to its unique trace turn.',
               'Input sample clock,server trace wall clock,and receiver/player clock are kept separate. No cross-clock transport duration is computed.',
               'Server critical parts sum exactly to final vad_end event through first TTS chunk ready; whole LLM duration is not added to the critical path.',
               'Receiver parts sum exactly to scored final latency. Packet placement is approximate because event timestamps were rounded to0.1ms.',
               'Onset-minus-first-placed-sample includes audio prefix/phrase structure and output-VAD scoring behavior; it is not pure VAD or network delay.',
               'STT roundtrip includes client/server/transport costs; these traces cannot isolate model kernel time or server queue delay.',
               'STT input lengths do not prove exact input waveform equality; waveforms are not read. Text is compared only in memory; lengths/equality flags only are exported.',
               'One observation per input and changed carry/history do not identify a counterfactual model latency distribution.',
           ]}
    indices = {a: read(META / (a + '_numeric.json')) for a in ['before', 'after']}
    for i in range(1, 6):
        ident = f't5-{i:02d}'
        b, bt = analyze(runtime, 'before', ident, indices['before'])
        a, at = analyze(runtime, 'after', ident, indices['after'])
        out['cases'][ident] = {'before': b, 'after': a,
            'text_equality_only': {k: x == y for k, x, y in zip(
                ['final_stt_equal', 'final_llm_equal', 'first_phrase_equal'], bt, at)},
            'deltas_after_minus_before': {
                'scored_final_latency_s': a['receiver_clock']['scored_final_latency_s'] - b['receiver_clock']['scored_final_latency_s'],
                'receiver_parts_s': {k: a['receiver_clock']['parts_s'][k] - v for k, v in b['receiver_clock']['parts_s'].items()},
                'server_critical_parts_s': {k: a['server_wall_clock']['critical_parts_s'][k] - v for k, v in b['server_wall_clock']['critical_parts_s'].items()},
                'final_stt_audio_len_s': a['input_clock']['final_stt_audio_len_s'] - b['input_clock']['final_stt_audio_len_s'],
            }}
    out['summary'] = {arm: {'scored_latency_median_s': statistics.median(
        c[arm]['receiver_clock']['scored_final_latency_s'] for c in out['cases'].values()),
        'direct_final_gate_wait_s': [c[arm]['server_wall_clock']['direct_final_gate_wait_s'] for c in out['cases'].values()],
        'first_main_queue_wait_s': [c[arm]['receiver_clock']['parts_s']['prior_output_queue_wait_s'] for c in out['cases'].values()]}
        for arm in ['before', 'after']}
    ROOT.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'path': str(args.out), 'sha256': sha(args.out), 'summary': out['summary']}))


if __name__ == '__main__':
    main()
