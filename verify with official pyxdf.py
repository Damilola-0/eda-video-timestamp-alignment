"""Verify EDA timestamps with the OFFICIAL PyXDF importer.

Prerequisite:  python -m pip install pyxdf numpy
Run without arguments to select an XDF in a file dialog, or pass its path:
  python "verify with official pyxdf.py" "C:\\path\\to\\recording.xdf"

Input XDF is read-only and never edited. Results are written to a new
'stamped_pyxdf_validation' folder alongside the XDF.
"""
import csv
import sys
from pathlib import Path
import numpy as np

try:
    import pyxdf
except ImportError as exc:
    raise SystemExit(
        'PyXDF is required: run python -m pip install pyxdf numpy'
    ) from exc

def select_xdf():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        selected = filedialog.askopenfilename(
            parent=root,
            title='Select an XDF recording',
            filetypes=(('XDF files', '*.xdf'), ('All files', '*.*')),
        )
    finally:
        root.destroy()
    if not selected:
        raise SystemExit(0)
    return Path(selected)


src = Path(sys.argv[1]) if len(sys.argv) > 1 else select_xdf()
if not src.is_file():
    raise SystemExit(f'XDF does not exist: {src}')

def get(streams,name):
    matches=[s for s in streams if s['info']['name'][0] == name]
    if len(matches) != 1:
        raise RuntimeError(f'Expected exactly one {name} stream, found {len(matches)}')
    return matches[0]

# Three independent views of identical XDF samples.
raw,_=pyxdf.load_xdf(str(src),synchronize_clocks=False,dejitter_timestamps=False)
clock_only,_=pyxdf.load_xdf(str(src),synchronize_clocks=True,dejitter_timestamps=False)
processed,_=pyxdf.load_xdf(str(src),synchronize_clocks=True,dejitter_timestamps=True)
ra=get(raw,'OpenSignals');cl=get(clock_only,'OpenSignals');pr=get(processed,'OpenSignals')
r=np.asarray(ra['time_stamps'],float);c=np.asarray(cl['time_stamps'],float);p=np.asarray(pr['time_stamps'],float)
rv=np.asarray(ra['time_series'],float);pv=np.asarray(pr['time_series'],float)
assert len(r)==len(c)==len(p)==len(rv)==len(pv), 'Sample count changed'
assert np.array_equal(rv,pv), 'Values changed unexpectedly'
assert len(r)>1 and np.all(np.diff(p)>0), 'Processed time is not monotonic'
seq=rv[:,0].astype(int)
assert np.array_equal(np.diff(seq),np.ones(len(seq)-1,dtype=int)), 'EDA sequence gap'

orig_marker=get(raw,'Makerspace_Sync_Marker')
clock_marker=get(clock_only,'Makerspace_Sync_Marker')
mk=np.asarray(clock_marker['time_stamps'],float)
if len(mk)<1: raise RuntimeError('No synchronization markers')

outdir=src.parent / 'stamped_pyxdf_validation'
outdir.mkdir(exist_ok=True)
data_csv=outdir/'eda_official_pyxdf_raw_vs_processed.csv'
with data_csv.open('w',newline='',encoding='utf-8') as f:
    w=csv.writer(f)
    w.writerow(['sample_index','OpenSignals_sequence','EDA_value_as_recorded','raw_lsl_timestamp_s',
                'clock_corrected_timestamp_s','pyxdf_clock_and_dejitter_timestamp_s',
                'processed_time_since_SYNC_BEGIN_s','raw_minus_processed_ms'])
    for i in range(len(r)):
        w.writerow([i,int(seq[i]),f'{rv[i,1]:.9g}',f'{r[i]:.9f}',f'{c[i]:.9f}',
                    f'{p[i]:.9f}',f'{p[i]-mk[0]:.9f}',f'{(r[i]-p[i])*1000:.5f}'])

mark_csv=outdir/'markers_official_pyxdf_clock_only.csv'
with mark_csv.open('w',newline='',encoding='utf-8') as f:
    w=csv.writer(f)
    w.writerow(['marker','raw_timestamp_s','clock_corrected_timestamp_s','time_since_SYNC_BEGIN_s'])
    for name,tr,tc in zip(orig_marker['time_series'],orig_marker['time_stamps'],mk):
        w.writerow([name[0],f'{tr:.9f}',f'{tc:.9f}',f'{tc-mk[0]:.9f}'])

rate=(len(p)-1)/(p[-1]-p[0])
rep=(
  f'Input XDF: {src}\nSamples: {len(p)}\n'
  f'EDA seq: {seq[0]}..{seq[-1]}, continuous\n'
  f'Fitted processed sampling rate: {rate:.9f} Hz\n'
  f'Raw interval median: {np.median(np.diff(r))*1000:.6f} ms\n'
  f'Processed interval median: {np.median(np.diff(p))*1000:.6f} ms\n'
  f'Raw vs processed RMS shift: {np.sqrt(np.mean(((r-p)*1000)**2)):.6f} ms\n'
  f'Clock-only max absolute shift: {np.max(np.abs(r-c))*1000:.6f} ms\n'
  f'Nominal 100Hz vs fitted duration difference: {(p[-1]-p[0]-(len(p)-1)/100)*1000:.6f} ms\n'
  f'Original untouched: yes\nData CSV: {data_csv}\nMarkers CSV: {mark_csv}\n'
)
(outdir/'official_pyxdf_validation_report.txt').write_text(rep,encoding='utf-8')
print(rep)
