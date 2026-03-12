
# ─── BLENDING ─────────────────────────────────────────────────────────────────
#
# Approach: Gaussian-smoothed Voronoi blend weights
#
# Why this works better than sharp Voronoi or feather:
#   - Voronoi places the seam at the exact midpoint between image centres
#     (correct position), but the weight transition is sharp (visible seam)
#   - Feather weight ramps from image edge: wrong position + wrong width
#   - Gaussian-smoothed Voronoi: correct seam position + smooth wide transition
#
# Blend width (sigma) is set proportional to image size so:
#   - High-freq detail (textures, edges): blended over ~8px → sharp
#   - Low-freq content (brightness): blended over ~100px → invisible seam
#
# The wide blend zone means even after affine radiometric correction,
# any residual 2-3 grey level difference at a seam is spread over 100px
# and becomes invisible to the eye.

def voronoi_weight(cx_i, cy_i, nb_centers, oh, ow):
    """
    Voronoi seam weight for image i.
    Returns float32 HxW, value = d_neighbour / (d_self + d_neighbour).
    1.0 at own centre, 0.5 at midpoint with nearest neighbour.
    """
    x_abs = np.arange(ow, dtype=np.float32)[None, :] + (cx_i - ow/2)
    y_abs = np.arange(oh, dtype=np.float32)[:, None] + (cx_i - oh/2)  # note: intentional cx_i for y init, fixed below
    x_abs = (np.arange(ow, dtype=np.float32) + cx_i - ow/2)[None, :]
    y_abs = (np.arange(oh, dtype=np.float32) + cy_i - oh/2)[:, None]
    d_self = np.sqrt((x_abs - cx_i)**2 + (y_abs - cy_i)**2).astype(np.float32) + 1e-6
    if not nb_centers:
        mx = d_self.max() + 1e-6
        return (1. - d_self / mx).clip(0, 1)
    d_nb = np.full((oh, ow), np.inf, np.float32)
    for (ncx, ncy) in nb_centers:
        d = np.sqrt((x_abs - ncx)**2 + (y_abs - ncy)**2).astype(np.float32)
        d_nb = np.minimum(d_nb, d)
    return (d_nb / (d_self + d_nb)).astype(np.float32)


def smooth_weight(w2d, sigma_px):
    """
    Gaussian blur on weight mask — pure numpy, no scipy needed.
    3 passes of box filter per axis approximates a Gaussian (CLT).
    sigma_px controls blend width: ~40 gives ~120px smooth transition.
    """
    w = w2d.astype(np.float64)
    r = max(1, int(round(sigma_px * 1.73)))
    if r % 2 == 0: r += 1
    k = np.ones(r, np.float64) / r
    for _ in range(3):
        w = np.apply_along_axis(lambda row: np.convolve(row, k, mode='same'), 1, w)
        w = np.apply_along_axis(lambda col: np.convolve(col, k, mode='same'), 0, w)
    return w.astype(np.float32)


"""
Ortho Mosaic Engine v10
=======================
Pipeline:
  1. Read GPS EXIF + DJI XMP (altitude, yaw, sensor model)
  2. Project to UTM
  3. Compute GSD from altitude + HFOV × user footprint_scale
     footprint_scale > 1.0  →  bigger footprint  →  more overlap  →  fixes gaps
     footprint_scale < 1.0  →  smaller footprint  →  less overlap
  4. Flat-field correction: average 200 images at 64px → vignette model
  5. Histogram LUT per image: match to global mean/std
  6. Hard edge mask: outer 10% of every image = weight 0
  7. Weighted average composite
  8. Percentile stretch p2-p98 on final mosaic
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

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False



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
            a,b = struct.unpack_from(end+'II', data, voff)
            tags[tag] = a/b if b else 0.0
        elif fmt == 5:
            vals = []
            for i in range(nc):
                if voff+i*8+8 <= len(data):
                    a,b = struct.unpack_from(end+'II', data, voff+i*8)
                    vals.append(a/b if b else 0.0)
            tags[tag] = vals
        elif fmt == 2:
            tags[tag] = data[voff:voff+nc].rstrip(b'\x00').decode('latin-1','ignore')
        elif fmt in (3,4,9) and nc==1:
            tags[tag] = struct.unpack_from(end+{3:'H',4:'I',9:'i'}[fmt], data, vo)[0]
        if tag in (0x8769,0x8825) and depth==0:
            sub = struct.unpack_from(end+'I', data, vo)[0]
            if sub < len(data): tags.update(_ifd(data, sub, end, 1))
        off += 12
    return tags

def read_exif(path):
    with open(path,'rb') as f: data = f.read(min(524288, os.path.getsize(path)))
    if data[:2]==b'\xff\xd8':
        i=2
        while i<len(data)-4:
            mk=struct.unpack_from('>H',data,i)[0]
            if mk==0xFFDA: break
            ln=struct.unpack_from('>H',data,i+2)[0]
            if mk==0xFFE1:
                a=data[i+4:i+2+ln]
                if a[:6] in (b'Exif\x00\x00',b'Exif\x00\xff'):
                    t=a[6:]; e='<' if t[:2]==b'II' else '>'
                    return _ifd(t, struct.unpack_from(e+'I',t,4)[0], e)
            i+=2+ln
    elif data[:2] in (b'II',b'MM'):
        e='<' if data[:2]==b'II' else '>'
        return _ifd(data, struct.unpack_from(e+'I',data,4)[0], e)
    return {}

def read_xmp(path):
    out={}
    try:
        with open(path,'rb') as f: raw=f.read(min(1048576,os.path.getsize(path)))
        txt=raw.decode('latin-1','ignore')
        for key in ('RelativeAltitude','AbsoluteAltitude',
                    'GimbalYawDegree','FlightYawDegree','Model','ImageDescription'):
            m=re.search(r'(?:drone-dji:|Camera:)?%s="([^"]+)"'%re.escape(key),txt)
            if m: out[key]=m.group(1)
        if 'Model' not in out:
            m=re.search(r'<tiff:Model>([^<]+)</tiff:Model>',txt)
            if m: out['Model']=m.group(1)
    except Exception: pass
    return out

def get_gps(tags):
    def deg(v): return v[0]+v[1]/60+v[2]/3600 if v and len(v)>=3 else None
    lat=deg(tags.get(0x0002)); lon=deg(tags.get(0x0004))
    if lat is None or lon is None: return None
    if tags.get(0x0001,'N')=='S': lat=-lat
    if tags.get(0x0003,'E')=='W': lon=-lon
    alt=float(tags.get(0x0006,100.0))
    if tags.get(0x0005,0)==1: alt=-alt
    return lat,lon,alt

def detect_sensor(xmp, img_w, img_h):
    model=(xmp.get('Model','')+' '+xmp.get('ImageDescription','')).lower()
    thermal_kw=('h20t','h30t','m3t','m30t','zxt','xt2','xt s',
                'radiometric','thermal','whitehot','blackhot','infrared')
    is_thermal=any(k in model for k in thermal_kw) or (img_w<=720 and img_h<=600)
    if is_thermal:
        hfov=57.0 if ('m3t' in model or img_w==640) else 45.0
    else:
        hfov=84.0
    return hfov, is_thermal


# ─── UTM ──────────────────────────────────────────────────────────────────────

def build_utm(lat,lon):
    if not HAS_GDAL: raise RuntimeError("GDAL not installed: conda install -c conda-forge gdal")
    zone=int((lon+180)/6)+1
    epsg=32600+zone if lat>=0 else 32700+zone
    src=osr.SpatialReference(); src.ImportFromEPSG(4326)
    dst=osr.SpatialReference(); dst.ImportFromEPSG(epsg)
    if hasattr(src,'SetAxisMappingStrategy'):
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return epsg, osr.CoordinateTransformation(src,dst)

def to_utm(tx,lat,lon):
    x,y,_=tx.TransformPoint(float(lon),float(lat)); return x,y

def calc_gsd(alt,img_w,hfov):
    return 2.0*max(float(alt),2.0)*math.tan(math.radians(hfov)/2.0)/max(img_w,1)


# ─── Radiometric ──────────────────────────────────────────────────────────────

def build_flat_field(paths, log=None, thumb=64, max_n=200):
    """
    Average up to max_n images at thumb×thumb resolution.
    Scene content cancels; vignette pattern survives.
    Returns float32 HxWx3, or None on failure.
    """
    step = max(1, len(paths)//max_n)
    sampled = paths[::step]
    if log: log(f"  Flat-field: averaging {len(sampled)} images at {thumb}px...")
    acc = np.zeros((thumb,thumb,3), np.float64)
    cnt = 0
    for p in sampled:
        try:
            img = Image.open(p).convert('RGB').resize((thumb,thumb), Image.Resampling.BILINEAR)
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
            Image.fromarray(np.clip(flat[:,:,c],0,255).astype(np.uint8),'L')
                 .filter(ImageFilter.GaussianBlur(radius=3)), np.float32)
    if log:
        for c,ch in enumerate('RGB'):
            lo=flat[:,:,c].min(); hi=flat[:,:,c].max()
            log(f"    {ch}: {lo:.0f}–{hi:.0f}  ratio {hi/max(lo,1):.2f}x")
    return flat

def apply_flat_field(tile_f32, flat):
    """Divide each channel by flat-field normalised to mean=1."""
    if flat is None: return tile_f32
    h,w = tile_f32.shape[:2]
    ff = np.asarray(Image.fromarray(np.clip(flat,0,255).astype(np.uint8))
                        .resize((w,h),Image.Resampling.BILINEAR), np.float32) \
         if flat.shape[:2]!=(h,w) else flat.copy()
    out = tile_f32.copy()
    for c in range(3):
        mf = ff[:,:,c].mean()
        if mf < 2: continue
        mt = tile_f32[:,:,c].mean()
        corrected = tile_f32[:,:,c] / np.maximum(ff[:,:,c]/mf, 0.05)
        mn = corrected.mean()
        if mn > 1: corrected = corrected * mt / mn
        out[:,:,c] = np.clip(corrected, 0, 255)
    return out

def _sample_overlap_stats(rec, ox0, ox1, oy0, oy1, thumb=48):
    """
    Sample mean AND std of rec pixels inside UTM overlap rectangle.
    Returns (mean, std) or None.
    """
    try:
        img = Image.open(rec['path']).convert('L')
        iw, ih = img.size
        ppm = iw / rec['gw']
        dx0 = (ox0 - rec['ux']) * ppm;  dx1 = (ox1 - rec['ux']) * ppm
        dy0 = -(oy1 - rec['uy']) * ppm; dy1 = -(oy0 - rec['uy']) * ppm
        px0 = int(max(0, iw/2 + dx0)); px1 = int(min(iw, iw/2 + dx1))
        py0 = int(max(0, ih/2 + dy0)); py1 = int(min(ih, ih/2 + dy1))
        if px1 - px0 < 6 or py1 - py0 < 6:
            return None
        crop = img.crop((px0, py0, px1, py1)).resize(
                        (thumb, thumb), Image.Resampling.BILINEAR)
        arr = np.asarray(crop, np.float32)
        m, s = float(arr.mean()), float(arr.std())
        if m < 2.0 or s < 0.5:
            return None
        return m, s
    except Exception:
        return None


def affine_radiometric_compensation(recs, log=None):
    """
    Global per-image AFFINE radiometric correction: corrected = a_i * raw + b_i

    DJI thermal JPEG maps each frame independently:
        pixel = (T - T_min_frame) / (T_max_frame - T_min_frame) * 255
    Adjacent frames have different BOTH scale (gain) AND zero point (offset).
    Gain-only correction cannot remove both — we solve for both simultaneously.

    Stage 1: Solve per-image GAIN via STD ratios across all overlap pairs.
        std_i * a_i = std_j * a_j  (same scene = same variance after correction)
        Log-domain least-squares over all pairs.

    Stage 2: Solve per-image OFFSET via MEAN constraints, given gains from stage 1.
        a_i*mean_i + b_i = a_j*mean_j + b_j  (same scene = same mean after correction)
        Linear least-squares over all pairs.

    Returns: dict { path: (a_i, b_i) }
    """
    n = len(recs)
    if log: log(f"  Measuring {n*(n-1)//2} potential overlap pairs...")

    pairs = []  # (i, j, mean_i, std_i, mean_j, std_j, ov_frac)
    for i in range(n):
        ri = recs[i]
        for j in range(i+1, n):
            rj = recs[j]
            ox0 = max(ri['x0'], rj['x0']); ox1 = min(ri['x1'], rj['x1'])
            oy0 = max(ri['y0'], rj['y0']); oy1 = min(ri['y1'], rj['y1'])
            if ox1 <= ox0 or oy1 <= oy0: continue
            ov_frac = (ox1-ox0)*(oy1-oy0) / (ri['gw']*ri['gh'])
            if ov_frac < 0.04: continue
            si = _sample_overlap_stats(ri, ox0, ox1, oy0, oy1)
            sj = _sample_overlap_stats(rj, ox0, ox1, oy0, oy1)
            if si is None or sj is None: continue
            pairs.append((i, j, si[0], si[1], sj[0], sj[1], ov_frac))

    if log: log(f"  {len(pairs)} valid overlap pairs found")

    # Fallback: not enough overlaps
    if len(pairs) < 2:
        if log: log("  Warning: too few overlaps, using median normalisation")
        result = {}
        means = []
        for r in recs:
            try:
                img = Image.open(r['path']).convert('L')
                w,h = img.size
                mw,mh = max(1,int(w*.6)), max(1,int(h*.6))
                crop = img.crop(((w-mw)//2,(h-mh)//2,(w+mw)//2,(h+mh)//2))
                crop = crop.resize((64,64), Image.Resampling.BILINEAR)
                means.append(float(np.asarray(crop,np.float32).mean()))
            except Exception:
                means.append(128.)
        ref = float(np.median(means))
        for r,m in zip(recs,means):
            g = float(np.clip(ref/max(m,2.), 0.3, 3.0))
            result[r['path']] = (g, 0.0)
        return result

    m_rows = len(pairs)

    # ── Stage 1: gains from std ratios ───────────────────────────────────
    LAMBDA_A = 0.01
    Aa = np.zeros((m_rows + n, n), np.float64)
    ba = np.zeros(m_rows + n,      np.float64)
    for row, (i, j, mu_i, s_i, mu_j, s_j, ov) in enumerate(pairs):
        if s_i < 0.5 or s_j < 0.5: continue
        r_std = s_j / s_i
        if not (0.05 < r_std < 20.): continue
        w = ov * min(s_i, s_j) / max(s_i, s_j)
        Aa[row, i] =  w
        Aa[row, j] = -w
        ba[row]    =  w * math.log(r_std)
    for i in range(n):
        Aa[m_rows+i, i] = LAMBDA_A
    log_gains, _, _, _ = np.linalg.lstsq(Aa, ba, rcond=None)
    gains = np.clip(np.exp(log_gains), 0.15, 6.0)
    if log:
        log(f"  Gain a: min={gains.min():.3f}  max={gains.max():.3f}  "
            f"median={float(np.median(gains)):.3f}")

    # ── Stage 2: offsets from mean constraints ────────────────────────────
    LAMBDA_B = 0.1
    Ab = np.zeros((m_rows + n, n), np.float64)
    bb = np.zeros(m_rows + n,      np.float64)
    for row, (i, j, mu_i, s_i, mu_j, s_j, ov) in enumerate(pairs):
        w = ov
        Ab[row, i] =  w
        Ab[row, j] = -w
        bb[row]    =  w * (gains[j]*mu_j - gains[i]*mu_i)
    for i in range(n):
        Ab[m_rows+i, i] = LAMBDA_B
    offsets, _, _, _ = np.linalg.lstsq(Ab, bb, rcond=None)
    offsets -= float(np.median(offsets))   # anchor: don't shift global brightness
    offsets = np.clip(offsets, -80., 80.)
    if log:
        log(f"  Offset b: min={offsets.min():.1f}  max={offsets.max():.1f}  "
            f"median={float(np.median(offsets)):.1f}")

    return {recs[i]['path']: (float(gains[i]), float(offsets[i])) for i in range(n)}


def apply_affine(tile_f32, gain, offset):
    """Apply per-image affine correction: gain*tile + offset, clamped 0-255."""
    return np.clip(tile_f32 * gain + offset, 0., 255.)


def apply_lut(tile_u8, lut):
    out = np.empty_like(tile_u8)
    for c in range(3): out[:,:,c] = lut[c][tile_u8[:,:,c]]
    return out

def edge_mask(h, w, edge_frac=0.10, ramp_frac=0.10):
    """Outer edge_frac = 0 hard. Then cosine ramp. Centre = 1."""
    def ax(L):
        ep=int(L*edge_frac); rp=max(2,int(L*ramp_frac))
        a=np.zeros(L,np.float32)
        for px in range(L):
            d=min(px,L-1-px)
            if d>=ep+rp: a[px]=1.
            elif d>=ep:  a[px]=float(.5-.5*math.cos(math.pi*(d-ep)/rp))
        return a
    return np.outer(ax(h),np.ones(w,np.float32))*np.outer(np.ones(h,np.float32),ax(w))

def percentile_stretch(img, alpha, plo=2., phi=98.):
    out=img.copy(); mask=alpha>0
    for c in range(3):
        ch=img[:,:,c].astype(np.float32); v=ch[mask]
        if not v.size: continue
        lo=np.percentile(v,plo); hi=np.percentile(v,phi)
        if hi>lo: out[:,:,c]=np.clip((ch-lo)/(hi-lo)*255,0,255).astype(np.uint8)
    return out


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
        self.log("  Ortho Mosaic Engine v10")
        self.log(f"  GDAL: {'YES ✅' if HAS_GDAL else 'NO ❌'}")
        self.log("="*60)
        self.log(f"\n📷 Reading {len(self.paths)} images...")
        self.prog(2)
        recs=[]
        for i,path in enumerate(self.paths):
            if self.cancelled(): return []
            self.prog(2+int(8*i/max(len(self.paths)-1,1)))
            try:
                tags=read_exif(path); gps=get_gps(tags)
                if not gps:
                    self.log(f"  ⚠ No GPS: {os.path.basename(path)}"); continue
                with Image.open(path) as im: img_w,img_h=im.size
                xmp=read_xmp(path)
                rel_alt=None
                try:
                    v=xmp.get('RelativeAltitude','')
                    if v: rel_alt=abs(float(str(v).replace('+','')))
                except Exception: pass
                yaw=0.
                try:
                    yaw=float(str(xmp.get('GimbalYawDegree',
                               xmp.get('FlightYawDegree','0'))).replace('+',''))
                except Exception: pass
                lat,lon,alt=gps
                hfov,is_thermal=detect_sensor(xmp,img_w,img_h)
                if self.hfov_ov: hfov=float(self.hfov_ov)
                recs.append(dict(path=path,lat=lat,lon=lon,alt=alt,
                                 rel_alt=rel_alt,yaw=yaw,w=img_w,h=img_h,
                                 hfov=hfov,thermal=is_thermal))
                self.log(f"  ✓ {os.path.basename(path):36s} "
                         f"alt={rel_alt or alt:5.0f}m  yaw={yaw:6.1f}°  "
                         f"{'THERMAL' if is_thermal else 'RGB':7s}  HFOV={hfov:.0f}°")
            except Exception as e:
                self.log(f"  ⚠ {os.path.basename(path)}: {e}")
        if not recs: raise ValueError("No GPS images found.")
        nth=sum(1 for r in recs if r['thermal'])
        self.log(f"\n  → {len(recs)} images  ({len(recs)-nth} RGB + {nth} thermal)")
        return recs

    # ── 2. Canvas ──────────────────────────────────────────────────────────────
    def build_canvas(self, recs):
        self.log("\n📐 Building canvas...")
        clat=sum(r['lat'] for r in recs)/len(recs)
        clon=sum(r['lon'] for r in recs)/len(recs)
        epsg,tx=build_utm(clat,clon)

        gsds=[]
        for r in recs:
            r['ux'],r['uy']=to_utm(tx,r['lat'],r['lon'])
            alt=r['rel_alt'] or r['alt']
            if self.gsd_ov:
                g=float(self.gsd_ov)
            else:
                g=calc_gsd(alt,r['w'],r['hfov'])*self.fp_scale
            r['gsd']=g; r['gw']=g*r['w']; r['gh']=g*r['h']
            r['x0']=r['ux']-r['gw']/2; r['x1']=r['ux']+r['gw']/2
            r['y0']=r['uy']-r['gh']/2; r['y1']=r['uy']+r['gh']/2
            gsds.append(g)

        X0=min(r['x0'] for r in recs); X1=max(r['x1'] for r in recs)
        Y0=min(r['y0'] for r in recs); Y1=max(r['y1'] for r in recs)
        gsd_out=float(np.median(gsds))

        CW=int(math.ceil((X1-X0)/gsd_out)); CH=int(math.ceil((Y1-Y0)/gsd_out))
        MAX=30000
        if max(CW,CH)>MAX:
            sc=MAX/max(CW,CH); gsd_out/=sc; CW=int(CW*sc); CH=int(CH*sc)
            self.log(f"  ℹ Canvas capped → {CW}×{CH} px")

        for r in recs:
            r['cx']=(r['ux']-X0)/gsd_out; r['cy']=(Y1-r['uy'])/gsd_out
            r['pw']=max(2,int(round(r['gw']/gsd_out)))
            r['ph']=max(2,int(round(r['gh']/gsd_out)))

        fp_w=gsd_out*recs[0]['w']; fp_h=gsd_out*recs[0]['h']
        self.log(f"  fp_scale={self.fp_scale:.2f}x  GSD={gsd_out:.5f}m/px  "
                 f"footprint={fp_w:.1f}×{fp_h:.1f}m")
        self.log(f"  EPSG:{epsg}  {CW}×{CH}px  area={X1-X0:.0f}×{Y1-Y0:.0f}m")
        self.prog(12)
        return dict(epsg=epsg,X0=X0,Y0=Y0,X1=X1,Y1=Y1,CW=CW,CH=CH,gsd=gsd_out)

    # ── 3. Composite ───────────────────────────────────────────────────────────
    def composite(self, recs, cv):
        CW,CH=cv['CW'],cv['CH']; n=len(recs)

        self.log(f"\n🎨 Radiometric corrections ({n} images)...")
        self.prog(13)
        flat=build_flat_field([r['path'] for r in recs], log=self.log)
        self.prog(16)

        # ── Global affine correction: per-image gain + offset ─────────────────
        self.log("  Affine radiometric compensation (gain+offset, log least-squares)...")
        affine = affine_radiometric_compensation(recs, log=self.log)
        self.prog(22)

        # ── Pre-compute neighbour centres for Voronoi blending ────────────────
        self.log("  Building neighbour map for Voronoi seam blending...")
        nb_centers = {}
        for i in range(n):
            ri = recs[i]
            nbs = []
            for j in range(n):
                if j == i: continue
                rj = recs[j]
                ox = min(ri['x1'],rj['x1'])-max(ri['x0'],rj['x0'])
                oy = min(ri['y1'],rj['y1'])-max(ri['y0'],rj['y0'])
                if ox > 0 and oy > 0:
                    nbs.append((rj['cx'], rj['cy']))
            nb_centers[i] = nbs

        self.log(f"\n🗺  Compositing → {CW}×{CH} px  (Gaussian-smoothed Voronoi blend)...")
        canvas = np.zeros((CH, CW, 3), np.float64)
        weight = np.zeros((CH, CW),    np.float64)

        # Blend sigma: ~1/8 of image width → ~100px for 640px thermal image
        # This gives a wide enough blend zone to hide residual radiometric differences
        blend_sigma = max(8., recs[0]['pw'] / 8.)
        self.log(f"  Blend sigma: {blend_sigma:.0f}px")

        for i,r in enumerate(recs):
            if self.cancelled(): raise RuntimeError("Cancelled.")
            self.prog(22+int(63*i/max(n-1,1)))
            a, b = affine[r['path']]
            self.log(f"  [{i+1:3d}/{n}] {os.path.basename(r['path'])}  a={a:.3f} b={b:+.1f}")
            pw,ph=r['pw'],r['ph']
            try:
                img=Image.open(r['path']).convert('RGB')
                if img.size!=(pw,ph): img=img.resize((pw,ph),Image.Resampling.BILINEAR)
                yaw=float(r.get('yaw',0.) or 0.)
                if abs(yaw)>0.5:
                    img=img.rotate(-yaw,resample=Image.Resampling.BILINEAR,
                                   expand=False,fillcolor=(0,0,0))
                tile=np.asarray(img,np.uint8).copy()
            except Exception as e:
                self.log(f"    ⚠ {e}"); continue

            # Radiometric correction
            tile_f = apply_flat_field(tile.astype(np.float32), flat)
            tile_f = apply_affine(tile_f, a, b)
            tile   = np.clip(tile_f, 0, 255).astype(np.uint8)

            oh, ow = tile.shape[:2]
            cx, cy = r['cx'], r['cy']
            c0 = int(math.floor(cx-ow/2)); r0 = int(math.floor(cy-oh/2))
            dc0=max(0,c0); dr0=max(0,r0)
            dc1=min(CW,c0+ow); dr1=min(CH,r0+oh)
            if dc1<=dc0 or dr1<=dr0: self.log("    ⚠ outside canvas"); continue

            sc0=dc0-c0; sr0=dr0-r0
            patch=tile[sr0:sr0+(dr1-dr0), sc0:sc0+(dc1-dc0)]
            if patch.size==0: continue
            ph2, pw2 = patch.shape[:2]

            # Voronoi weight (full tile) → smooth with Gaussian → crop to patch
            wm_full = voronoi_weight(cx, cy, nb_centers[i], oh, ow)
            wm_full *= (tile.max(axis=2) > 2).astype(np.float32)
            wm_full  = smooth_weight(wm_full, blend_sigma)
            wm = wm_full[sr0:sr0+ph2, sc0:sc0+pw2]

            canvas[dr0:dr1,dc0:dc1] += patch.astype(np.float64) * wm[:,:,None]
            weight[dr0:dr1,dc0:dc1] += wm

        self.prog(87)
        nz = weight > 1e-6
        if not nz.any(): raise ValueError("No pixels on canvas.")
        result = np.zeros((CH, CW, 3), np.uint8)
        result[nz] = np.clip(canvas[nz] / weight[nz, None], 0, 255).astype(np.uint8)
        alpha = (nz.astype(np.uint8) * 255)

        self.log("  Percentile stretch p2-p98...")
        result = percentile_stretch(result, alpha)

        cov = 100.*nz.sum()/(CW*CH)
        ov  = 100.*(weight[nz]>1.5).sum()/max(nz.sum(),1)
        self.log(f"  Coverage: {nz.sum():,}px ({cov:.1f}%)  Overlap: {ov:.0f}%")
        if ov < 20: self.log("  ⚠ Low overlap (<20%) — try Footprint Scale 1.3")
        return result, alpha

    # ── 4. Write ───────────────────────────────────────────────────────────────
    def write(self, result, alpha, cv):
        self.log("\n💾 Writing GeoTIFF...")
        self.prog(90)
        ys,xs=np.where(alpha>0)
        if ys.size:
            pad=20
            r0=max(0,ys.min()-pad); r1=min(result.shape[0],ys.max()+1+pad)
            c0=max(0,xs.min()-pad); c1=min(result.shape[1],xs.max()+1+pad)
            result=result[r0:r1,c0:c1]; alpha=alpha[r0:r1,c0:c1]
            ox=cv['X0']+c0*cv['gsd']; oy=cv['Y1']-r0*cv['gsd']
        else:
            ox=cv['X0']; oy=cv['Y1']
        FH,FW=result.shape[:2]
        drv=gdal.GetDriverByName('GTiff')
        out=drv.Create(self.outpath,FW,FH,4,gdal.GDT_Byte,
                       options=['COMPRESS=DEFLATE','PREDICTOR=2',
                                'TILED=YES','BLOCKXSIZE=512','BLOCKYSIZE=512',
                                'BIGTIFF=IF_NEEDED'])
        out.SetGeoTransform([ox,cv['gsd'],0,oy,0,-cv['gsd']])
        srs=osr.SpatialReference(); srs.ImportFromEPSG(cv['epsg'])
        out.SetProjection(srs.ExportToWkt())
        for b in range(3): out.GetRasterBand(b+1).WriteArray(result[:,:,b])
        out.GetRasterBand(4).WriteArray(alpha)
        out.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
        self.log("  Building overviews...")
        self.prog(96)
        out.BuildOverviews('AVERAGE',[2,4,8,16])
        out.FlushCache(); out=None
        mb=os.path.getsize(self.outpath)/1e6
        self.log(f"\n  ✅ {self.outpath}  ({mb:.1f} MB)  {FW}×{FH}px")
        self.prog(100)

    # ── Run ────────────────────────────────────────────────────────────────────
    def run(self):
        import time; t0=time.time()
        recs=self.collect()
        cv=self.build_canvas(recs)
        result,alpha=self.composite(recs,cv)
        self.write(result,alpha,cv)
        self.log(f"\n⏱  {(time.time()-t0)/60:.1f} min")
        self.log("✅ Done!")
        return self.outpath
