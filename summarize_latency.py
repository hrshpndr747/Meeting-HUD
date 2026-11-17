"""Summarize opt-in timing events without reading or exporting speech content."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered)-1)*fraction
    low, high = math.floor(index), math.ceil(index)
    return round(ordered[low] + (ordered[high]-ordered[low])*(index-low), 2)


def summarize(paths):
    events, models, runs, invalid = Counter(), set(), set(), 0
    durations = defaultdict(list)
    seen = set()
    for path in paths:
        with Path(path).open(encoding='utf-8') as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get('event'), str):
                        raise ValueError()
                    identity = (row.get('run_id'), row.get('request_id'), row.get('event'), row.get('monotonic'))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    events[row['event']] += 1
                    if row.get('model'):
                        models.add(row['model'])
                    if row.get('run_id'):
                        runs.add(row['run_id'])
                    final = 'final' if row.get('final') else 'draft'
                    mappings = {'translation_first_text': ('elapsed_ms', 'source_to_first_ms'),
                                'translation_completed': ('elapsed_ms',),
                                'recording_translation_completed': ('elapsed_ms',)}
                    for field in mappings.get(row['event'], ()):
                        value = row.get(field)
                        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                            key = row['event'] + '.' + field
                            if row['event'] != 'recording_translation_completed':
                                key += '.' + final
                            durations[key].append(value)
                except (ValueError, TypeError, KeyError):
                    invalid += 1
    return {'event_counts': dict(sorted(events.items())), 'model_ids': sorted(models),
            'run_count': len(runs), 'invalid_lines': invalid,
            'timings_ms': {key: {'count': len(values), 'p50': percentile(values, .5),
                'p95': percentile(values, .95), 'minimum': round(min(values), 2),
                'maximum': round(max(values), 2)} for key, values in sorted(durations.items())},
            'notes': ['Request timing starts before HTTP connection/request and includes any bounded retry.',
                      'Source-to-first starts at the selected ASR hypothesis revision, not physical speech onset.',
                      'Recording completion measures from capture end; it is not full audio-to-meaning latency.',
                      'Draft/final refers to source status at request start. Drafts may be revised or cancelled.',
                      'Quantiles describe successful observations only. Compare failures and cancellations separately.',
                      'These logs do not measure semantic accuracy or exact screen-paint latency.']}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs', default='logs', help='Log directory or one latency JSONL file')
    parser.add_argument('--out', default='latency-summary.json')
    args = parser.parse_args(argv)
    path = Path(args.logs)
    paths = sorted(path.glob('latency.jsonl*')) if path.is_dir() else [path]
    if not paths or any(not p.is_file() for p in paths):
        parser.error('No latency logs found. Start a session with run_latency.bat first.')
    report = summarize(paths)
    Path(args.out).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Saved timing summary to {args.out}; {report["run_count"]} run(s).')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
