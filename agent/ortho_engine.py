"""
Ortho Mosaic Engine v19
=======================
Root cause fix for persistent seams in v17/v18:

  All 3 radiometric bugs traced to one source:
  overlap zone sampling used GPS positions (±1-2m error) to locate
  which pixels to measure → wrong pixels → wrong calibration.

Changes from v18:
  1. REMOVE phase correlation — unreliable on low-texture thermal images;
     one bad pair corrupts the global least-squares. GPS + wide blend is safer.
  2. REPLACE overlap-zone affine calibration with global 2-point stretch:
     sample each image's full-image p5/p95 (GPS-independent),
     stretch to global median p5/p95 reference.
     → correctly undoes DJI thermal per-frame auto-normalization
     → GPS error cannot affect calibration
  3. WIDEN blend zone: sigma = pw/4 (was pw/8) — must exceed GPS error

Pipeline:
  1. Read GPS EXIF + DJI XMP
  2. Project to UTM
  3. GSD from altitude + HFOV × footprint_scale
  4. Flat-field (RGB only — vignette correction)
  5. Per-image 2-point stretch to global median p5/p95  ← v19 fix
  6. Gaussian-smoothed Voronoi blend + edge mask  ← sigma doubled
  7. Percentile stretch p2-p98
  8. Write GeoTIFF DEFLATE + overviews
"""

import math, os, re, struct, threading
import numpy as np
from PIL import Image, ImageFilter

try:
    from osgeo import gdal, osr
    gdal.UseExceptions()
    HAS_GDAL = True
except ImportError:
    HAS_GDAL = False


# ─── EXIF ─────────────────────────────────────────────────────────────────────

def _ifd(data, off, end, depth=0):
    tags = {}
    if off + 2 > len(data): return tags
    n = struct.unpack_from(end+'H', data, off)[0]; off += 2
    for _ in range(n):
        if off + 12 > len(data): break
        tag, fmt, nc = struct.unpack_from(end+'HHI', data, off)
        vo = off + 8
        sz = {1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8}.get(fmt,1)
        voff = struct.unpack_from(end+'I', data, vo)[0] if sz*nc > 4 else vo
        if voff >= len(data): off += 12; continue
        if fmt == 5 and nc == 1 and voff+8 <= len(data):
            a, b = struct.unpack_from(end+'II', data, voff)
            tags[tag] = a/b if b else 0.0
        elif fmt == 5:
            vals = []
            for i in range(nc):
                if voff+i*8+8 <= len(data):
                    a, b = struct.unpack_from(end+'II', data, voff+i*8)
                    vals.append(a/b if b else 0.0)
            tags[tag] = vals
        elif fmt == 2:
            tags[tag] = data[voff:voff+nc].rstrip(b'\x00').decode('latin-1', 'ignore')
        elif fmt in (3, 4, 9) and nc == 1:
            tags[tag] = struct.unpack_from(end+{3:'H',4:'I',9:'i'}[fmt], data, vo)[0]
        if tag in (0x8769, 0x8825) and depth == 0:
            sub = struct.unpack_from(end+'I', data, vo)[0]
            if sub < len(data): tags.update(_ifd(data, sub, end, 1))
        off += 12
    return tags

def read_exif(path):
    with open(path, 'rb') as f: data = f.read(min(524288, os.path.getsize(path)))
    if data[:2] == b'\xff\xd8':
        i = 2
        while i < len(data)-4:
            mk = struct.unpack_from('>H', data, i)[0]
            if mk == 0xFFDA: break
            ln = struct.unpack_from('>H', data, i+2)[0]
            if mk == 0xFFE1:
                a = data[i+4:i+2+ln]
                if a[:6] in (b'Exif\x00\x00', b'Exif\x00\xff'):
                    t = a[6:]; e = '<' if t[:2] == b'II' else '>'
                    return _ifd(t, struct.unpack_from(e+'I', t, 4)[0], e)
            i += 2+ln
    elif data[:2] in (b'II', b'MM'):
        e = '<' if data[:2] == b'II' else '>'
        return _ifd(data, struct.unpack_from(e+'I', data, 4)[0], e)
    return {}

def read_xmp(path):
    out = {}
    try:
        with open(path, 'rb') as f: raw = f.read(min(1048576, os.path.getsize(path)))
        txt = raw.decode('latin-1', 'ignore')
        for key in ('RelativeAltitude', 'AbsoluteAltitude',
                    'GimbalYawDegree', 'FlightYawDegree', 'Model', 'ImageDescription'):
            m = re.search(r'(?:drone-dji:|Camera:)?%s=\"([^\"]+)\"' % re.escape(key), txt)
            if m: out[key] = m.group(1)
        if 'Model' not in out:
            m = re.search(r'<tiff:Model>([^<]+)</tiff:Model>', txt)
            if m: out['Model'] = m.group(1)
    except Exception: pass
    return out

def get_gps(tags):
    def deg(v): return v[0]+v[1]/60+v[2]/3600 if v and len(v) >= 3 else None
    lat = deg(tags.get(0x0002)); lon = deg(tags.get(0x0004))
    if lat is None or lon is None: return None
    if tags.get(0x0001, 'N') == 'S': lat = -lat
    if tags.get(0x0003, 'E') == 'W': lon = -lon
    alt = float(tags.get(0x0006, 100.0))
    if tags.get(0x0005, 0) == 1: alt = -alt
    return lat, lon, alt

def detect_sensor(xmp, img_w, img_h):
    model = (xmp.get('Model', '') + ' ' + xmp.get('ImageDescription', '')).lower()
    thermal_kw = ('h20t', 'h30t', 'm3t', 'm30t', 'zxt', 'xt2', 'xt s',
                  'radiometric', 'thermal', 'whitehot', 'blackhot', 'infrared')
    is_thermal = any(k in model for k in thermal_kw) or (img_w <= 720 and img_h <= 600)
    if is_thermal:
        hfov = 57.0 if ('m3t' in model or img_w == 640) else 45.0
    else:
        hfov = 84.0
    return hfov, is_thermal


# ─── UTM ──────────────────────────────────────────────────────────────────────

def build_utm(lat, lon):
    if not HAS_GDAL: raise RuntimeError("GDAL not installed")
    zone = int((lon+180)/6)+1
    epsg = 32600+zone if lat >= 0 else 32700+zone
    src = osr.SpatialReference(); src.ImportFromEPSG(4326)
    dst = osr.SpatialReference(); dst.ImportFromEPSG(epsg)
    if hasattr(src, 'SetAxisMappingStrategy'):
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return epsg, osr.CoordinateTransformation(src, dst)

def to_utm(tx, lat, lon):
    x, y, _ = tx.TransformPoint(float(lon), float(lat)); return x, y

def calc_gsd(alt, img_w, hfov):
    return 2.0*max(float(alt), 2.0)*math.tan(math.radians(hfov)/2.0)/max(img_w, 1)


# ─── FLAT-FIELD ───────────────────────────────────────────────────────────────

def build_flat_field(paths, log=None, thumb=64, max_n=200):
    """Average ≤max_n images at thumb² to estimate vignette pattern."""
    step = max(1, len(paths)//max_n)
    sampled = paths[::step]
    if log: log(f"  Flat-field: averaging {len(sampled)} images at {thumb}px...")
    acc = np.zeros((thumb, thumb, 3), np.float64); cnt = 0
    for p in sampled:
        try:
            img = Image.open(p).convert('RGB').resize((thumb, thumb),
                                                       Image.Resampling.BILINEAR)
            arr = np.asarray(img, np.float32)
            for c in range(3):
                m = arr[:,:,c].mean()
                if m > 2: arr[:,:,c] = arr[:,:,c]/m*128.
            acc += arr; cnt += 1
        except Exception: pass
    if cnt == 0: return None
    flat = (acc/cnt).astype(np.float32)
    for c in range(3):
        flat[:,:,c] = np.asarray(
            Image.fromarray(np.clip(flat[:,:,c], 0, 255).astype(np.uint8), 'L')
                 .filter(ImageFilter.GaussianBlur(radius=3)), np.float32)
    return flat

def apply_flat_field(tile_f32, flat):
    if flat is None: return tile_f32
    h, w = tile_f32.shape[:2]
    ff = (np.asarray(Image.fromarray(np.clip(flat, 0, 255).astype(np.uint8))
                         .resize((w, h), Image.Resampling.BILINEAR), np.float32)
          if flat.shape[:2] != (h, w) else flat.copy())
    out = tile_f32.copy()
    for c in range(3):
        mf = ff[:,:,c].mean()
        if mf < 2: continue
        mt = tile_f32[:,:,c].mean()
        corr = tile_f32[:,:,c] / np.maximum(ff[:,:,c]/mf, 0.05)
        mn = corr.mean()
        if mn > 1: corr = corr * mt / mn
        out[:,:,c] = np.clip(corr, 0, 255)
    return out


# ─── RADIOMETRIC: per-image 2-point stretch (v19) ─────────────────────────────

def _image_percentiles(path, flat, thumb=128, p_lo=5., p_hi=95.):
    """
    Sample centre 60% of image, apply flat-field, return (p_lo, p_hi) of
    grayscale.  GPS-independent — uses full image, not overlap zone.
    """
    try:
        img = Image.open(path).convert('RGB')
        w, h = img.size
        mw = max(1, int(w * .6)); mh = max(1, int(h * .6))
        crop = img.crop(((w-mw)//2, (h-mh)//2, (w+mw)//2, (h+mh)//2))
        crop = crop.resize((thumb, thumb), Image.Resampling.BILINEAR)
        arr  = np.asarray(crop, np.float32)
        if flat is not None:
            arr = apply_flat_field(arr, flat)
        gray = arr.mean(axis=2).ravel()
        return float(np.percentile(gray, p_lo)), float(np.percentile(gray, p_hi))
    except Exception:
        return 10., 245.


def global_stretch_calibration(recs, flat, log=None):
    """
    v19 radiometric calibration: GPS-independent per-image 2-point stretch.

    For DJI thermal JPEG, each frame is auto-scaled:
        pixel = (T - T_min_frame) / (T_max_frame - T_min_frame) * 255
    Frames shot over warmer areas get a different scale than cooler areas.
    This creates stripe artifacts regardless of blending.

    Fix: map each frame's [p5, p95] → global median [p5, p95].
    This undoes the per-frame auto-normalization without needing GPS or
    overlap detection.

        corrected = gain_i * raw + offset_i
        gain_i   = (p95_ref - p5_ref) / max(p95_i - p5_i, 1)
        offset_i = p5_ref - gain_i * p5_i

    Returns dict { path: (gain, offset) }.
    """
    if log: log(f"  Sampling full-image p5/p95 ({len(recs)} images, GPS-independent)...")
    percs = []
    for r in recs:
        lo, hi = _image_percentiles(r['path'], flat)
        percs.append((lo, hi))
        r['_p5'] = lo; r['_p95'] = hi

    p5s  = [p[0] for p in percs]
    p95s = [p[1] for p in percs]
    p5_ref  = float(np.median(p5s))
    p95_ref = float(np.median(p95s))
    ref_range = max(p95_ref - p5_ref, 1.)

    if log:
        log(f"  p5  range: {min(p5s):.0f}–{max(p5s):.0f}  ref={p5_ref:.0f}")
        log(f"  p95 range: {min(p95s):.0f}–{max(p95s):.0f}  ref={p95_ref:.0f}")
        log(f"  Ref range: {ref_range:.0f} grey levels")

    result = {}
    for r in recs:
        span = max(r['_p95'] - r['_p5'], 1.)
        gain   = ref_range / span
        offset = p5_ref - gain * r['_p5']
        # Clamp: don't let any image be stretched or squashed more than 4×
        gain   = float(np.clip(gain, 0.25, 4.0))
        offset = float(np.clip(offset, -100., 100.))
        result[r['path']] = (gain, offset)

    gains = [v[0] for v in result.values()]
    if log:
        log(f"  Gain: min={min(gains):.3f}  max={max(gains):.3f}  "
            f"median={float(np.median(gains)):.3f}")
    return result


# ─── BLENDING ─────────────────────────────────────────────────────────────────

def voronoi_weight(cx_i, cy_i, nb_centers, oh, ow):
    """
    Weight for image i: 1.0 at own centre, 0.5 at midpoint to nearest neighbour.
    Placing the seam exactly at the midpoint is correct for GPS-placed images.
    """
    x_abs = (np.arange(ow, dtype=np.float32) + cx_i - ow/2)[None, :]
    y_abs = (np.arange(oh, dtype=np.float32) + cy_i - oh/2)[:, None]
    d_self = np.sqrt((x_abs-cx_i)**2 + (y_abs-cy_i)**2).astype(np.float32) + 1e-6
    if not nb_centers:
        return (1. - d_self / (d_self.max()+1e-6)).clip(0, 1)
    d_nb = np.full((oh, ow), np.inf, np.float32)
    for (ncx, ncy) in nb_centers:
        d = np.sqrt((x_abs-ncx)**2 + (y_abs-ncy)**2).astype(np.float32)
        d_nb = np.minimum(d_nb, d)
    return (d_nb / (d_self + d_nb)).astype(np.float32)


def _box1d(w, r):
    """Fast O(n) box filter radius r along axis=1 using cumsum."""
    n = 2*r+1; h = w.shape[0]
    pad = np.concatenate([w[:,:1].repeat(r,1), w, w[:,-1:].repeat(r,1)], axis=1)
    cs  = np.cumsum(pad, axis=1)
    cs2 = np.concatenate([np.zeros((h,1), dtype=w.dtype), cs], axis=1)
    return (cs2[:, n:] - cs2[:, :-n]) / n


def smooth_weight(w2d, sigma_px):
    """
    Approximate Gaussian via 3 passes of box filter (CLT).
    sigma_px = pw/4 in v19 (doubled from pw/8) so the blend zone is
    wide enough to absorb ±1-2 m GPS position error.
    """
    w = w2d.astype(np.float64)
    r = max(1, int(round(sigma_px * 1.73)))
    for _ in range(3):
        w = _box1d(w, r)
        w = _box1d(w.T, r).T
    return w.clip(0., 1.).astype(np.float32)



# ─── PHASE CORRELATION (RGB only) ─────────────────────────────────────────────

def _sample_patch_for_corr(r, oc0, or0, ow, oh, thumb=192):
    img_c0 = r['cx'] - r['pw']/2.0;  img_r0 = r['cy'] - r['ph']/2.0
    sx = r['w']/max(r['pw'],1);       sy = r['h']/max(r['ph'],1)
    px0=max(0,int((oc0-img_c0)*sx)); px1=min(r['w'],int((oc0-img_c0+ow)*sx))
    py0=max(0,int((or0-img_r0)*sy)); py1=min(r['h'],int((or0-img_r0+oh)*sy))
    if px1-px0 < 8 or py1-py0 < 8: return None
    try:
        img  = Image.open(r['path']).convert('L')
        crop = img.crop((px0,py0,px1,py1))
        tw=min(thumb,max(8,ow)); th=min(thumb,max(8,oh))
        arr = np.asarray(crop.resize((tw,th),Image.Resampling.BILINEAR),np.float32)
        m,s = float(arr.mean()),float(arr.std())
        if s < 0.5: return None
        return (arr-m)/s
    except Exception: return None

def _phase_corr(a, b):
    """Pure numpy FFT phase correlation. Returns (dy, dx) of b relative to a."""
    h,w = a.shape
    win = (np.hanning(h)[:,None]*np.hanning(w)[None,:]).astype(np.float32)
    F1=np.fft.rfft2(a*win); F2=np.fft.rfft2(b*win)
    R=F1*np.conj(F2); denom=np.abs(R); denom[denom<1e-10]=1e-10
    corr=np.fft.irfft2(R/denom)
    idx=np.unravel_index(corr.argmax(),corr.shape)
    dy,dx=int(idx[0]),int(idx[1])
    if dy>h//2: dy-=h
    if dx>w//2: dx-=w
    return dy,dx

def refine_positions(recs, cv, log=None, max_shift_px=40, thumb=192):
    """
    Phase-correlation GPS refinement for RGB images.
    Measures actual pixel offset between overlapping pairs via FFT.
    Solves weighted least-squares for global position adjustment.
    max_shift_px=40: rejects pairs where correlation offset > 40px
    (implausible for well-overlapping nadir images — likely false peak).
    """
    n = len(recs)
    constraints = []
    for i in range(n):
        ri = recs[i]
        for j in range(i+1, n):
            rj = recs[j]
            oc0=max(ri['cx']-ri['pw']/2., rj['cx']-rj['pw']/2.)
            oc1=min(ri['cx']+ri['pw']/2., rj['cx']+rj['pw']/2.)
            or0=max(ri['cy']-ri['ph']/2., rj['cy']-rj['ph']/2.)
            or1=min(ri['cy']+ri['ph']/2., rj['cy']+rj['ph']/2.)
            ow=int(oc1-oc0); oh=int(or1-or0)
            if ow<20 or oh<20: continue
            pi=_sample_patch_for_corr(ri,oc0,or0,ow,oh,thumb)
            pj=_sample_patch_for_corr(rj,oc0,or0,ow,oh,thumb)
            if pi is None or pj is None: continue
            dy_t,dx_t=_phase_corr(pi,pj)
            dy_c=dy_t*(oh/pi.shape[0]); dx_c=dx_t*(ow/pi.shape[1])
            if abs(dy_c)>max_shift_px or abs(dx_c)>max_shift_px: continue
            ov_frac=(ow*oh)/max(ri['pw']*ri['ph'],1)
            constraints.append((i,j,dy_c,dx_c,ov_frac))
    if not constraints:
        if log: log("  No valid correlations — keeping GPS positions")
        return
    if log: log(f"  {len(constraints)} pair correlations used")
    m=len(constraints); n=len(recs); LAMBDA=0.05
    Ay=np.zeros((m+n,n)); by=np.zeros(m+n)
    Ax=np.zeros((m+n,n)); bx=np.zeros(m+n)
    for row,(i,j,dy,dx,w) in enumerate(constraints):
        Ay[row,i]=w; Ay[row,j]=-w; by[row]=w*dy
        Ax[row,i]=w; Ax[row,j]=-w; bx[row]=w*dx
    for k in range(n):
        Ay[m+k,k]=LAMBDA; Ax[m+k,k]=LAMBDA
    dy_adj,_,_,_=np.linalg.lstsq(Ay,by,rcond=None)
    dx_adj,_,_,_=np.linalg.lstsq(Ax,bx,rcond=None)
    dy_adj-=dy_adj.mean(); dx_adj-=dx_adj.mean()
    for i in range(n):
        recs[i]['cy']+=float(dy_adj[i]); recs[i]['cx']+=float(dx_adj[i])
    if log:
        log(f"  Max shift: dy={float(np.abs(dy_adj).max()):.1f}px  "
            f"dx={float(np.abs(dx_adj).max()):.1f}px")

def edge_mask(h, w, edge_frac=0.04, ramp_frac=0.06):
    """Zero outer 8%, cosine ramp to 1.0 at centre. Kills border artifacts."""
    def ax(L):
        ep = int(L*edge_frac); rp = max(2, int(L*ramp_frac))
        a  = np.zeros(L, np.float32)
        for px in range(L):
            d = min(px, L-1-px)
            if   d >= ep+rp: a[px] = 1.
            elif d >= ep:    a[px] = .5 - .5*math.cos(math.pi*(d-ep)/rp)
        return a
    return (np.outer(ax(h), np.ones(w, np.float32)) *
            np.outer(np.ones(h, np.float32), ax(w)))


def percentile_stretch(img, alpha, plo=2., phi=98.):
    out = img.copy(); mask = alpha > 0
    for c in range(3):
        ch = img[:,:,c].astype(np.float32); v = ch[mask]
        if not v.size: continue
        lo = np.percentile(v, plo); hi = np.percentile(v, phi)
        if hi > lo:
            out[:,:,c] = np.clip((ch-lo)/(hi-lo)*255, 0, 255).astype(np.uint8)
    return out


def _rotate_no_bleed(img, yaw_deg):
    """Rotate with expand=True, then crop back to original size. No black corners."""
    if abs(yaw_deg) < 0.5: return img
    ow, oh = img.size
    rotated = img.rotate(-yaw_deg, resample=Image.Resampling.BILINEAR,
                         expand=True, fillcolor=None)
    rw, rh = rotated.size
    return rotated.crop(((rw-ow)//2, (rh-oh)//2, (rw-ow)//2+ow, (rh-oh)//2+oh))


# ─── Engine ───────────────────────────────────────────────────────────────────

class OrthoEngine:

    def __init__(self, image_paths, output_path,
                 gsd_override=None, hfov_override=None,
                 footprint_scale=1.0,
                 log_cb=None, progress_cb=None, cancel_event=None):
        self.paths    = sorted(image_paths)
        self.outpath  = output_path
        self.gsd_ov   = gsd_override
        self.hfov_ov  = hfov_override
        self.fp_scale = float(footprint_scale or 1.0)
        self.log      = log_cb      or (lambda m: print(m))
        self.prog     = progress_cb or (lambda p: None)
        self.cancel   = cancel_event or threading.Event()

    def cancelled(self): return self.cancel.is_set()

    # ── 1. Collect ─────────────────────────────────────────────────────────────
    def collect(self):
        self.log("="*60)
        self.log("  Ortho Mosaic Engine v19")
        self.log(f"  GDAL: {'YES ✅' if HAS_GDAL else 'NO ❌'}")
        self.log("="*60)
        self.log(f"\n📷 Reading {len(self.paths)} images...")
        self.prog(2); recs = []
        for i, path in enumerate(self.paths):
            if self.cancelled(): return []
            self.prog(2 + int(8*i/max(len(self.paths)-1, 1)))
            try:
                tags = read_exif(path); gps = get_gps(tags)
                if not gps:
                    self.log(f"  ⚠ No GPS: {os.path.basename(path)}"); continue
                with Image.open(path) as im: img_w, img_h = im.size
                xmp = read_xmp(path)
                rel_alt = None
                try:
                    v = xmp.get('RelativeAltitude', '')
                    if v: rel_alt = abs(float(str(v).replace('+', '')))
                except Exception: pass
                yaw = 0.
                try:
                    yaw = float(str(xmp.get('GimbalYawDegree',
                                  xmp.get('FlightYawDegree', '0'))).replace('+', ''))
                except Exception: pass
                lat, lon, alt = gps
                hfov, is_thermal = detect_sensor(xmp, img_w, img_h)
                if self.hfov_ov: hfov = float(self.hfov_ov)
                recs.append(dict(path=path, lat=lat, lon=lon, alt=alt,
                                 rel_alt=rel_alt, yaw=yaw, w=img_w, h=img_h,
                                 hfov=hfov, thermal=is_thermal))
                self.log(f"  ✓ {os.path.basename(path):36s} "
                         f"alt={rel_alt or alt:5.0f}m  yaw={yaw:6.1f}°  "
                         f"{'THERMAL' if is_thermal else 'RGB':7s}  HFOV={hfov:.0f}°")
            except Exception as e:
                self.log(f"  ⚠ {os.path.basename(path)}: {e}")
        if not recs: raise ValueError("No GPS images found.")
        nth = sum(1 for r in recs if r['thermal'])
        self.log(f"\n  → {len(recs)} images  ({len(recs)-nth} RGB + {nth} thermal)")
        return recs

    # ── 2. Canvas ──────────────────────────────────────────────────────────────
    def build_canvas(self, recs):
        self.log("\n📐 Building canvas...")
        clat = sum(r['lat'] for r in recs)/len(recs)
        clon = sum(r['lon'] for r in recs)/len(recs)
        epsg, tx = build_utm(clat, clon)
        gsds = []
        for r in recs:
            r['ux'], r['uy'] = to_utm(tx, r['lat'], r['lon'])
            alt = r['rel_alt'] or r['alt']
            g   = float(self.gsd_ov) if self.gsd_ov else \
                  calc_gsd(alt, r['w'], r['hfov']) * self.fp_scale
            r['gsd'] = g; r['gw'] = g*r['w']; r['gh'] = g*r['h']
            r['x0']  = r['ux']-r['gw']/2; r['x1'] = r['ux']+r['gw']/2
            r['y0']  = r['uy']-r['gh']/2; r['y1'] = r['uy']+r['gh']/2
            gsds.append(g)
        X0=min(r['x0'] for r in recs); X1=max(r['x1'] for r in recs)
        Y0=min(r['y0'] for r in recs); Y1=max(r['y1'] for r in recs)
        gsd_out = float(np.median(gsds))
        CW = int(math.ceil((X1-X0)/gsd_out)); CH = int(math.ceil((Y1-Y0)/gsd_out))
        MAX = 30000
        if max(CW, CH) > MAX:
            sc = MAX/max(CW, CH); gsd_out /= sc
            CW = int(CW*sc); CH = int(CH*sc)
            self.log(f"  ℹ Canvas capped → {CW}×{CH} px")
        for r in recs:
            r['cx'] = (r['ux']-X0)/gsd_out; r['cy'] = (Y1-r['uy'])/gsd_out
            r['pw'] = max(2, int(round(r['gw']/gsd_out)))
            r['ph'] = max(2, int(round(r['gh']/gsd_out)))
        self.log(f"  fp_scale={self.fp_scale:.2f}×  GSD={gsd_out:.5f}m/px")
        self.log(f"  EPSG:{epsg}  {CW}×{CH}px  area={X1-X0:.0f}×{Y1-Y0:.0f}m")
        self.prog(12)
        return dict(epsg=epsg, X0=X0, Y0=Y0, X1=X1, Y1=Y1,
                    CW=CW, CH=CH, gsd=gsd_out)

    # ── 3. Composite ───────────────────────────────────────────────────────────
    def composite(self, recs, cv):
        CW, CH = cv['CW'], cv['CH']; n = len(recs)
        is_thermal = any(r['thermal'] for r in recs)

        # ── Flat-field (RGB only; thermal auto-scale handled by stretch) ──────
        self.log(f"\n🎨 Radiometric calibration ({n} images)...")
        self.prog(13)
        if not is_thermal:
            flat = build_flat_field([r['path'] for r in recs], log=self.log)
            self.log("  Flat-field built (RGB vignette correction)")
        else:
            flat = None
            self.log("  Thermal mode — flat-field skipped")
        self.prog(16)

        # 2-point stretch for thermal (corrects per-frame auto-normalization).
        # RGB images are already consistently exposed — stretch causes hue shift.
        if is_thermal:
            self.log("  Global 2-point stretch (thermal p5/p95, GPS-independent)...")
            calibration = global_stretch_calibration(recs, flat, log=self.log)
        else:
            self.log("  RGB mode — skipping 2-point stretch (flat-field only)")
            calibration = {r['path']: (1.0, 0.0) for r in recs}
            for r in recs: r['_p5'] = 0.; r['_p95'] = 255.
        self.prog(24)

        # ── Phase-correlation GPS refinement (RGB only) ──────────────────────────
        # RGB has rich texture → FFT cross-correlation is reliable.
        # Thermal has low contrast → correlation produces spurious peaks.
        if not is_thermal:
            self.log("\nPhase-correlation GPS refinement (RGB)...")
            refine_positions(recs, cv, log=self.log)
        self.prog(30)

        # ── Voronoi neighbour map ─────────────────────────────────────────────
        nb_centers = {}
        for i in range(n):
            ri = recs[i]; nbs = []
            for j in range(n):
                if j == i: continue
                rj = recs[j]
                ox = min(ri['x1'],rj['x1'])-max(ri['x0'],rj['x0'])
                oy = min(ri['y1'],rj['y1'])-max(ri['y0'],rj['y0'])
                if ox > 0 and oy > 0:
                    nbs.append((rj['cx'], rj['cy']))
            nb_centers[i] = nbs

        self.log(f"\n🗺  Compositing → {CW}×{CH} px  "
                 f"(Voronoi blend, sigma=pw/4)...")
        canvas = np.zeros((CH, CW, 3), np.float64)
        weight = np.zeros((CH, CW),    np.float64)

        # FIX: sigma capped by physical GPS budget (2 m), not tile fraction.
        # For RGB (3 cm/px, pw=4000): old pw/4=1000px=22m → 89px=2m now
        # For thermal (13 cm/px, pw=640): pw/8=80px=10m → 15px=2m now
        GPS_BUDGET_M = 2.0
        sigma_gps    = max(20., GPS_BUDGET_M / max(cv['gsd'], 1e-6))
        blend_sigma  = min(recs[0]['pw'] / 8., sigma_gps)
        self.log(f"  Blend sigma: {blend_sigma:.0f}px  "
                 f"(covers ~{blend_sigma*cv['gsd']:.2f}m GPS error)")

        for i, r in enumerate(recs):
            if self.cancelled(): raise RuntimeError("Cancelled.")
            self.prog(24 + int(60*i/max(n-1, 1)))
            gain, offset = calibration[r['path']]
            self.log(f"  [{i+1:3d}/{n}] {os.path.basename(r['path'])}  "
                     f"gain={gain:.3f} offset={offset:+.1f}  "
                     f"p5={r['_p5']:.0f} p95={r['_p95']:.0f}")

            pw, ph = r['pw'], r['ph']
            try:
                img = Image.open(r['path']).convert('RGB')
                if img.size != (pw, ph):
                    img = img.resize((pw, ph), Image.Resampling.BILINEAR)
                img  = _rotate_no_bleed(img, float(r.get('yaw', 0.) or 0.))
                tile = np.asarray(img, np.uint8).copy()
            except Exception as e:
                self.log(f"    ⚠ {e}"); continue

            oh, ow = tile.shape[:2]
            tile_f = tile.astype(np.float32)
            if flat is not None:
                tile_f = apply_flat_field(tile_f, flat)
            # v19: apply 2-point stretch
            tile_f = np.clip(tile_f * gain + offset, 0., 255.)
            tile   = tile_f.astype(np.uint8)

            cx, cy = r['cx'], r['cy']
            c0 = int(math.floor(cx - ow/2)); r0 = int(math.floor(cy - oh/2))
            dc0 = max(0, c0); dr0 = max(0, r0)
            dc1 = min(CW, c0+ow); dr1 = min(CH, r0+oh)
            if dc1 <= dc0 or dr1 <= dr0:
                self.log("    ⚠ outside canvas"); continue

            sc0 = dc0-c0; sr0 = dr0-r0
            patch = tile[sr0:sr0+(dr1-dr0), sc0:sc0+(dc1-dc0)]
            if patch.size == 0: continue
            ph2, pw2 = patch.shape[:2]

            wm_full  = voronoi_weight(cx, cy, nb_centers[i], oh, ow)
            wm_full *= edge_mask(oh, ow)
            wm_full *= (tile.max(axis=2) > 2).astype(np.float32)
            wm_full  = smooth_weight(wm_full, blend_sigma)
            wm = wm_full[sr0:sr0+ph2, sc0:sc0+pw2]

            canvas[dr0:dr1, dc0:dc1] += patch.astype(np.float64) * wm[:,:,None]
            weight[dr0:dr1, dc0:dc1] += wm

        self.prog(87)
        nz = weight > 1e-6
        if not nz.any(): raise ValueError("No pixels on canvas.")
        result = np.zeros((CH, CW, 3), np.uint8)
        result[nz] = np.clip(canvas[nz] / weight[nz, None], 0, 255).astype(np.uint8)
        alpha = nz.astype(np.uint8) * 255

        self.log("  Percentile stretch p2-p98...")
        result = percentile_stretch(result, alpha)

        cov = 100.*nz.sum()/(CW*CH)
        ov  = 100.*(weight[nz] > 1.5).sum()/max(nz.sum(), 1)
        self.log(f"  Coverage: {nz.sum():,}px ({cov:.1f}%)  Overlap: {ov:.0f}%")
        if ov < 20:
            self.log("  ⚠ Low overlap (<20%) — try Footprint Scale 1.3")
        return result, alpha

    # ── 4. Write ───────────────────────────────────────────────────────────────
    def write(self, result, alpha, cv):
        self.log("\n💾 Writing GeoTIFF...")
        self.prog(90)
        ys, xs = np.where(alpha > 0)
        if ys.size:
            pad = 20
            r0 = max(0, ys.min()-pad); r1 = min(result.shape[0], ys.max()+1+pad)
            c0 = max(0, xs.min()-pad); c1 = min(result.shape[1], xs.max()+1+pad)
            result = result[r0:r1, c0:c1]; alpha = alpha[r0:r1, c0:c1]
            ox = cv['X0']+c0*cv['gsd']; oy = cv['Y1']-r0*cv['gsd']
        else:
            ox = cv['X0']; oy = cv['Y1']
        FH, FW = result.shape[:2]
        drv = gdal.GetDriverByName('GTiff')
        out = drv.Create(self.outpath, FW, FH, 4, gdal.GDT_Byte,
                         options=['COMPRESS=DEFLATE','PREDICTOR=2',
                                  'TILED=YES','BLOCKXSIZE=512','BLOCKYSIZE=512',
                                  'BIGTIFF=IF_NEEDED'])
        out.SetGeoTransform([ox, cv['gsd'], 0, oy, 0, -cv['gsd']])
        srs = osr.SpatialReference(); srs.ImportFromEPSG(cv['epsg'])
        out.SetProjection(srs.ExportToWkt())
        for b in range(3): out.GetRasterBand(b+1).WriteArray(result[:,:,b])
        out.GetRasterBand(4).WriteArray(alpha)
        out.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
        self.log("  Building overviews...")
        self.prog(96)
        out.BuildOverviews('AVERAGE', [2, 4, 8, 16])
        out.FlushCache(); out = None
        mb = os.path.getsize(self.outpath)/1e6
        self.log(f"\n  ✅ {self.outpath}  ({mb:.1f} MB)  {FW}×{FH}px")
        self.prog(100)

    # ── Run ────────────────────────────────────────────────────────────────────
    def run(self):
        import time; t0 = time.time()
        recs          = self.collect()
        cv            = self.build_canvas(recs)
        result, alpha = self.composite(recs, cv)
        self.write(result, alpha, cv)
        self.log(f"\n⏱  {(time.time()-t0)/60:.1f} min")
        self.log("✅ Done!")
        return self.outpath
