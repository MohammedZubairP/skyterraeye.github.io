"""
Terra Agent — Sky TerraEye Photogrammetry Pipeline
====================================================
Runs on the user's workstation at http://localhost:7843
Drives OpenDroneMap (ODM) via Docker for full SfM pipeline:
  Step 1 — Align        (feature extraction + SfM reconstruction)
  Step 2 — Dense Cloud  (MVS depth maps + point cloud)
  Step 3 — DSM / DTM    (elevation rasters + hillshade)
  Step 4 — Orthomosaic  (orthorectified mosaic + radiometric balance)

Requirements:
  pip install flask
  Docker Desktop installed + docker pull opendronemap/odm
"""

import os, sys, glob, threading, queue, json, time, uuid, subprocess, shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder='static',
            template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024

# ── Job store ──────────────────────────────────────────────────────────────
jobs = {}  # job_id → { status, log, progress, step, result, cancel }

# ── Docker helpers ─────────────────────────────────────────────────────────

def find_docker():
    """Find docker executable, including common Windows paths."""
    d = shutil.which('docker') or shutil.which('docker.exe')
    if d: return d
    candidates = [
        r'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
        r'C:\Program Files\Docker\Docker\docker.exe',
        r'C:\ProgramData\DockerDesktop\version-bin\docker.exe',
    ]
    for c in candidates:
        if os.path.exists(c): return c
    return None

DOCKER = find_docker()

def docker_ok():
    if not DOCKER: return False, 'Docker not found'
    try:
        r = subprocess.run([DOCKER, 'info'], capture_output=True, timeout=8)
        if r.returncode == 0: return True, 'ok'
        return False, 'Docker daemon not running'
    except Exception as e:
        return False, str(e)

def odm_image_exists():
    if not DOCKER: return False
    try:
        r = subprocess.run([DOCKER, 'images', '-q', 'opendronemap/odm'],
                           capture_output=True, text=True, timeout=10)
        return bool(r.stdout.strip())
    except: return False

# ── Routes: static ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(
        os.path.join(BASE_DIR, 'templates'), 'terra.html')

@app.route('/static/<path:f>')
def static_files(f):
    return send_from_directory('static', f)

# ── Routes: API ────────────────────────────────────────────────────────────

@app.route('/api/status')
def api_status():
    dok, dmsg = docker_ok()
    odm = odm_image_exists() if dok else False
    return jsonify({
        'ok':     True,
        'docker': dok,
        'docker_path': DOCKER,
        'docker_msg':  dmsg,
        'odm_image':   odm,
    })

@app.route('/api/browse', methods=['POST'])
def api_browse():
    folder = (request.json or {}).get('folder', '').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False, 'error': f'Folder not found: {folder}'})
    exts = ('*.jpg','*.jpeg','*.tif','*.tiff','*.png',
            '*.JPG','*.JPEG','*.TIF','*.TIFF','*.PNG')
    paths = []
    for e in exts:
        paths.extend(glob.glob(os.path.join(folder, e)))
    paths = sorted(set(paths))
    return jsonify({'ok': True, 'count': len(paths), 'folder': folder})

@app.route('/api/pull_odm', methods=['POST'])
def api_pull_odm():
    """Start pulling the ODM Docker image in background."""
    job_id = 'pull_' + str(uuid.uuid4())[:6]
    jobs[job_id] = {'status':'running','log':[],'progress':0,'cancel':threading.Event()}
    def worker():
        try:
            _push_log(job_id, '⬇ Pulling opendronemap/odm — this may take 5-10 min on first run...')
            proc = subprocess.Popen(
                [DOCKER, 'pull', 'opendronemap/odm'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in iter(proc.stdout.readline, ''):
                line = line.rstrip()
                if line: _push_log(job_id, line)
            proc.wait()
            if proc.returncode == 0:
                _push_log(job_id, '✅ ODM image ready!')
                jobs[job_id]['status'] = 'done'
                jobs[job_id]['progress'] = 100
            else:
                _push_log(job_id, '❌ Pull failed — check internet connection')
                jobs[job_id]['status'] = 'error'
        except Exception as e:
            _push_log(job_id, f'❌ {e}')
            jobs[job_id]['status'] = 'error'
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})

@app.route('/api/run_step', methods=['POST'])
def api_run_step():
    """Run one ODM pipeline step."""
    data      = request.json or {}
    step      = data.get('step')          # align | dense | dsm | ortho
    images    = data.get('images_folder', '').strip()
    output    = data.get('output_folder', '').strip()
    quality   = data.get('quality', 'medium')  # low | medium | high
    gen_dtm   = data.get('gen_dtm', True)
    pc_format = data.get('pc_format', 'laz')

    if step not in ('align','dense','dsm','ortho'):
        return jsonify({'ok': False, 'error': f'Unknown step: {step}'})
    if not images or not os.path.isdir(images):
        return jsonify({'ok': False, 'error': 'Invalid images folder'})
    if not output:
        return jsonify({'ok': False, 'error': 'Output folder required'})

    dok, dmsg = docker_ok()
    if not dok:
        return jsonify({'ok': False, 'error': f'Docker not available: {dmsg}'})
    if not odm_image_exists():
        return jsonify({'ok': False, 'error': 'ODM image not found — click Pull ODM first'})

    os.makedirs(output, exist_ok=True)

    job_id = str(uuid.uuid4())[:8]
    cancel = threading.Event()
    jobs[job_id] = {
        'status':'running','log':[],'progress':0,
        'step': step, 'cancel': cancel, 'result': None,
        'start_time': time.time(),
    }

    def worker():
        try:
            _run_odm_step(job_id, step, images, output, quality,
                          gen_dtm, pc_format, cancel)
        except Exception as e:
            import traceback
            _push_log(job_id, f'\n❌ ERROR:\n{traceback.format_exc()}')
            jobs[job_id]['status'] = 'error'

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})

def _run_odm_step(job_id, step, images, output, quality,
                  gen_dtm, pc_format, cancel):
    """Build and execute the ODM Docker command for the given step."""

    # ODM expects: docker run -v HOST_OUTPUT:/datasets opendronemap/odm
    #              --project-path /datasets DATASET_NAME [options]
    # We use 'project' as the dataset name inside the output folder.
    dataset = 'project'
    dataset_path = os.path.join(output, dataset)
    images_dst   = os.path.join(dataset_path, 'images')
    os.makedirs(images_dst, exist_ok=True)

    # Stage images (hardlink where possible, else copy)
    _push_log(job_id, f'📁 Staging images → {images_dst}')
    exts = ('.jpg','.jpeg','.tif','.tiff','.png')
    staged = 0
    for fn in os.listdir(images):
        if fn.lower().endswith(exts):
            src = os.path.join(images, fn)
            dst = os.path.join(images_dst, fn)
            if not os.path.exists(dst):
                try:    os.link(src, dst)
                except: shutil.copy2(src, dst)
            staged += 1
    _push_log(job_id, f'✓ {staged} images staged')

    # Quality → ODM keyword
    q_map = {'fast':'low','low':'low','medium':'medium',
              'high':'high','accurate':'high'}
    q = q_map.get(quality.lower(), 'medium')

    # Build args per step
    container_name = f'ste_terra_{job_id}'
    base_args = [
        '--project-path', '/datasets',
        dataset,
    ]

    if step == 'align':
        jobs[job_id]['progress'] = 5
        odm_args = base_args + [
            '--feature-quality', q,
            '--matcher-type', 'flann',
            '--end-with', 'opensfm',
            '--rerun-from', 'dataset',
        ]
    elif step == 'dense':
        jobs[job_id]['progress'] = 30
        pc_flag = '--pc-las' if pc_format in ('las','laz') else '--pc-ply'
        odm_args = base_args + [
            '--pc-quality', q,
            pc_flag,
            '--end-with', 'odm_filterpoints',
            '--rerun-from', 'odm_filterpoints',
        ]
    elif step == 'dsm':
        jobs[job_id]['progress'] = 55
        odm_args = base_args + [
            '--dsm',
            '--end-with', 'odm_dem',
            '--rerun-from', 'odm_dem',
        ]
        if gen_dtm:
            odm_args.append('--dtm')
    elif step == 'ortho':
        jobs[job_id]['progress'] = 75
        odm_args = base_args + [
            '--orthophoto-resolution', '5',
            '--end-with', 'odm_orthophoto',
            '--rerun-from', 'odm_orthophoto',
        ]

    cmd = [
        DOCKER, 'run', '--rm',
        '--name', container_name,
        '-v', f'{output}:/datasets',
        'opendronemap/odm',
    ] + odm_args

    _push_log(job_id, f'\n🚀 {step.upper()} — quality: {q}')
    _push_log(job_id, '$ ' + ' '.join(cmd[:12]) + ' ...')

    # Progress keywords per step
    PROGRESS = {
        'align': [
            ('extracting features', 15),
            ('matching features', 35),
            ('creating tracks', 55),
            ('bundle adjust', 75),
            ('reconstruction', 90),
        ],
        'dense': [
            ('depthmap', 40),('fuse', 55),('dense', 60),('mesh', 70),('texture', 80),
        ],
        'dsm': [('dsm', 65),('dtm', 75),('dem', 80)],
        'ortho': [('orthophoto', 85),('ortho', 88)],
    }

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)

        for raw in iter(proc.stdout.readline, ''):
            if cancel.is_set():
                proc.terminate()
                subprocess.run([DOCKER,'stop',container_name],
                               capture_output=True)
                jobs[job_id]['status'] = 'cancelled'
                return
            line = raw.rstrip()
            if not line: continue
            _push_log(job_id, line)
            # Update progress from log keywords
            ll = line.lower()
            for kw, pct in PROGRESS.get(step, []):
                if kw in ll:
                    jobs[job_id]['progress'] = max(
                        jobs[job_id]['progress'], pct)

        proc.wait()
        if proc.returncode != 0:
            _push_log(job_id, f'❌ ODM exited with code {proc.returncode}')
            jobs[job_id]['status'] = 'error'
            return
    except Exception as e:
        _push_log(job_id, f'❌ {e}')
        jobs[job_id]['status'] = 'error'
        return

    # Find outputs
    result = _collect_outputs(output, dataset, step)
    jobs[job_id]['progress'] = 100
    jobs[job_id]['status']   = 'done'
    jobs[job_id]['result']   = result
    elapsed = round(time.time() - jobs[job_id]['start_time'], 1)
    _push_log(job_id, f'\n✅ {step.upper()} complete — {elapsed}s')
    if result:
        for k,v in result.items():
            if v and v != '—':
                _push_log(job_id, f'   {k}: {v}')

def _collect_outputs(output, dataset, step):
    """Find output files produced by each step."""
    base = os.path.join(output, dataset)
    def find(prefix, exts):
        for root,_,files in os.walk(base):
            for f in files:
                if any(f.lower().endswith(e) for e in exts):
                    if not prefix or prefix.lower() in f.lower():
                        return os.path.join(root, f)
        return '—'

    if step == 'align':
        recon = os.path.join(base,'opensfm','reconstruction.json')
        shots = '—'
        if os.path.exists(recon):
            try:
                d = json.load(open(recon))
                shots = str(len(d[0].get('shots',{}))) if d else '—'
            except: pass
        return {'images_aligned': shots,
                'reconstruction': recon if os.path.exists(recon) else '—'}
    elif step == 'dense':
        return {'point_cloud': find('', ['.laz','.las','.ply'])}
    elif step == 'dsm':
        return {
            'dsm': find('dsm',['.tif']),
            'dtm': find('dtm',['.tif']),
        }
    elif step == 'ortho':
        return {'orthomosaic': find('odm_orthophoto',['.tif'])}
    return {}

@app.route('/api/job/<job_id>')
def api_job(job_id):
    if job_id not in jobs:
        return jsonify({'ok': False, 'error': 'Unknown job'})
    j = jobs[job_id]
    since = int(request.args.get('since', 0))
    elapsed = round(time.time() - j.get('start_time', time.time()), 1)
    return jsonify({
        'ok':       True,
        'status':   j['status'],
        'progress': j['progress'],
        'log':      j['log'][since:],
        'log_total': len(j['log']),
        'elapsed':  elapsed,
        'result':   j.get('result'),
    })

@app.route('/api/cancel/<job_id>', methods=['POST'])
def api_cancel(job_id):
    if job_id in jobs:
        jobs[job_id]['cancel'].set()
        jobs[job_id]['status'] = 'cancelled'
        return jsonify({'ok': True})
    return jsonify({'ok': False})

@app.route('/api/open_folder', methods=['POST'])
def api_open_folder():
    folder = (request.json or {}).get('folder','').strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({'ok': False})
    try:
        import platform, subprocess
        if platform.system() == 'Windows':   subprocess.Popen(f'explorer "{folder}"')
        elif platform.system() == 'Darwin':  subprocess.Popen(['open', folder])
        else:                                subprocess.Popen(['xdg-open', folder])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

def _push_log(job_id, msg):
    if job_id in jobs:
        jobs[job_id]['log'].append(str(msg))

# ── Launch ─────────────────────────────────────────────────────────────────
def main():
    PORT = 7843
    dok, dmsg = docker_ok()
    print('=' * 55)
    print('  🌍  Sky TerraEye — Terra Agent')
    print('=' * 55)
    print(f'  Docker:  {"✅ " + DOCKER if dok else "❌ Not found — install Docker Desktop"}')
    print(f'  ODM:     {"✅ Ready" if odm_image_exists() else "❌ Run: docker pull opendronemap/odm"}')
    print()
    print(f'  Open in browser → http://localhost:{PORT}')
    print()
    print('  Press Ctrl+C to stop')
    print('=' * 55)
    import webbrowser, threading as _t
    def _open():
        time.sleep(1.5)
        webbrowser.open(f'http://localhost:{PORT}')
    _t.Thread(target=_open, daemon=True).start()
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)

if __name__ == '__main__':
    main()
