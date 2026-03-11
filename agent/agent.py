"""
Ortho Mosaic — Local Agent
===========================
Runs on the user's workstation. Browser connects to http://localhost:7842
All processing stays on the local machine. Zero data sent to any server.

Usage:
    python agent.py
    (then open browser to http://localhost:7842)
"""

import os, sys, glob, threading, queue, json, time, uuid, shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, Response

# Add the directory containing ortho_engine.py to path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

try:
    from ortho_engine import OrthoEngine, HAS_GDAL, HAS_CV2
    ENGINE_OK = True
    ENGINE_ERR = None
except Exception as e:
    ENGINE_OK = False
    ENGINE_ERR = str(e)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 * 1024  # 20 GB max upload

# ── Job store ──────────────────────────────────────────────────────────────────
jobs = {}   # job_id → { status, log_lines, progress, output_path, cancel_event }

# ── Routes: static files ───────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ── Routes: API ────────────────────────────────────────────────────────────────

@app.route('/api/status')
def api_status():
    return jsonify({
        'ok': True,
        'engine': ENGINE_OK,
        'engine_error': ENGINE_ERR,
        'gdal': HAS_GDAL if ENGINE_OK else False,
        'cv2':  HAS_CV2  if ENGINE_OK else False,
        'version': 'v15',
    })

@app.route('/api/browse', methods=['POST'])
def api_browse():
    """List image files in a local folder path."""
    data = request.json or {}
    folder = data.get('folder', '').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False, 'error': f'Folder not found: {folder}'})
    exts = ('*.jpg','*.jpeg','*.tif','*.tiff','*.png',
            '*.JPG','*.JPEG','*.TIF','*.TIFF')
    paths = []
    for ext in exts:
        paths.extend(glob.glob(os.path.join(folder, ext)))
        paths.extend(glob.glob(os.path.join(folder, '**', ext), recursive=True))
    paths = sorted(set(paths))
    return jsonify({'ok': True, 'count': len(paths), 'paths': paths[:5],
                    'folder': folder})

@app.route('/api/process', methods=['POST'])
def api_process():
    """Start a processing job. Returns job_id immediately."""
    if not ENGINE_OK:
        return jsonify({'ok': False, 'error': f'Engine not loaded: {ENGINE_ERR}'})

    data = request.json or {}
    folder    = data.get('folder', '').strip()
    output    = data.get('output', '').strip()
    fp_scale  = float(data.get('fp_scale', 1.0))
    gsd_ov    = data.get('gsd') or None
    hfov_ov   = data.get('hfov') or None

    if gsd_ov:
        try: gsd_ov = float(gsd_ov)
        except: gsd_ov = None
    if hfov_ov:
        try: hfov_ov = float(hfov_ov)
        except: hfov_ov = None

    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False, 'error': 'Invalid folder'})
    if not output:
        output = os.path.join(folder, 'orthomosaic.tif')

    # Collect images
    exts = ('*.jpg','*.jpeg','*.tif','*.tiff','*.png',
            '*.JPG','*.JPEG','*.TIF','*.TIFF')
    paths = []
    for ext in exts:
        paths.extend(glob.glob(os.path.join(folder, ext)))
        paths.extend(glob.glob(os.path.join(folder, '**', ext), recursive=True))
    paths = sorted(set(paths))

    if not paths:
        return jsonify({'ok': False, 'error': 'No images found in folder'})

    job_id = str(uuid.uuid4())[:8]
    log_q  = queue.Queue()
    cancel = threading.Event()

    jobs[job_id] = {
        'status':      'running',
        'log':         [],
        'progress':    0,
        'output_path': output,
        'cancel':      cancel,
        'start_time':  time.time(),
    }

    def worker():
        try:
            engine = OrthoEngine(
                image_paths     = paths,
                output_path     = output,
                gsd_override    = gsd_ov,
                hfov_override   = hfov_ov,
                footprint_scale = fp_scale,
                log_cb          = lambda m: _push_log(job_id, m),
                progress_cb     = lambda p: _push_prog(job_id, p),
                cancel_event    = cancel,
            )
            engine.run()
            jobs[job_id]['status'] = 'done'
        except Exception as e:
            import traceback
            _push_log(job_id, f'\n❌ ERROR:\n{traceback.format_exc()}')
            jobs[job_id]['status'] = 'error'

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id, 'image_count': len(paths)})

def _push_log(job_id, msg):
    if job_id in jobs:
        jobs[job_id]['log'].append(str(msg))

def _push_prog(job_id, p):
    if job_id in jobs:
        jobs[job_id]['progress'] = float(p)

@app.route('/api/job/<job_id>')
def api_job(job_id):
    """Poll job status, log, progress."""
    if job_id not in jobs:
        return jsonify({'ok': False, 'error': 'Unknown job'})
    j = jobs[job_id]
    since = int(request.args.get('since', 0))
    log_slice = j['log'][since:]
    elapsed = time.time() - j['start_time']
    out_size = 0
    if j['status'] == 'done' and os.path.exists(j['output_path']):
        out_size = os.path.getsize(j['output_path'])
    return jsonify({
        'ok':       True,
        'status':   j['status'],
        'progress': j['progress'],
        'log':      log_slice,
        'log_total': len(j['log']),
        'elapsed':  round(elapsed, 1),
        'output_path': j['output_path'] if j['status'] == 'done' else None,
        'output_size': out_size,
    })

@app.route('/api/cancel/<job_id>', methods=['POST'])
def api_cancel(job_id):
    if job_id in jobs:
        jobs[job_id]['cancel'].set()
        jobs[job_id]['status'] = 'cancelled'
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Unknown job'})

@app.route('/api/download/<job_id>')
def api_download(job_id):
    """Stream the output GeoTIFF directly to the browser."""
    if job_id not in jobs:
        return jsonify({'error': 'Unknown job'}), 404
    j = jobs[job_id]
    if j['status'] != 'done':
        return jsonify({'error': 'Not ready'}), 400
    path = j['output_path']
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path),
                     mimetype='image/tiff')

@app.route('/api/open_folder/<job_id>', methods=['POST'])
def api_open_folder(job_id):
    """Open the output folder in the OS file explorer."""
    if job_id not in jobs:
        return jsonify({'ok': False})
    folder = os.path.dirname(jobs[job_id]['output_path'])
    try:
        import subprocess, platform
        if platform.system() == 'Windows':
            subprocess.Popen(f'explorer "{folder}"')
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ── Launch ─────────────────────────────────────────────────────────────────────

def main():
    PORT = 7842
    print("=" * 55)
    print("  🛰  Ortho Mosaic — Local Agent")
    print("=" * 55)
    print(f"  Engine:  {'✅ Ready' if ENGINE_OK else '❌ ' + str(ENGINE_ERR)}")
    print(f"  GDAL:    {'✅' if ENGINE_OK and HAS_GDAL else '❌'}")
    print()
    print(f"  Open in browser → http://localhost:{PORT}")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 55)

    # Auto-open browser
    import webbrowser, threading
    def _open():
        time.sleep(1.2)
        webbrowser.open(f'http://localhost:{PORT}')
    threading.Thread(target=_open, daemon=True).start()

    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)


if __name__ == '__main__':
    main()
