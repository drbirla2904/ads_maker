"""
Core rendering engine.

VIDEO templates:
  - detect_chroma_color()  → auto-detects green hex + zone bbox on upload
  - composite_video()       → TWO-PASS rendering:
      Pass 1: alphamerge hole punch (cuts exact zone rectangle from template)
              This removes green zone AND all placeholder text/overlays on it
      Pass 2: user content (scaled to zone, padded) shows through the hole
      The template black border/frame remains untouched around the hole

IMAGE templates:
  - composite_image() → Pillow perspective warp
"""
import os, json, subprocess, shutil, tempfile
from pathlib import Path
from PIL import Image
import numpy as np


def _ffmpeg():  return shutil.which('ffmpeg')
def _ffprobe(): return shutil.which('ffprobe')


def _get_video_info(path):
    fp = _ffprobe()
    if not fp: return 1080, 1920, 10.0
    try:
        r = subprocess.run([fp,'-v','quiet','-print_format','json',
                            '-show_streams',path],
                           capture_output=True,text=True,timeout=30)
        for s in json.loads(r.stdout).get('streams',[]):
            if s.get('codec_type')=='video':
                return s.get('width',1080),s.get('height',1920),float(s.get('duration',10))
    except: pass
    return 1080,1920,10.0


# ─── Chroma detection ────────────────────────────────────────────────────────

def detect_chroma_color(video_path, sample_frames=5):
    """
    Scan ALL frames of template video at 24fps, detect chroma key color,
    and compute the UNION bounding box across every frame.

    Using the union bbox (not a single frame) as the hole mask ensures the
    hole covers the zone in every frame even when the board moves with the
    person — preventing green edge leakage at any point in the animation.

    Returns dict with hex, similarity, bbox (union), union_bbox — or None.
    """
    ff = _ffmpeg()
    if not ff: return None

    tmp = tempfile.mkdtemp()
    try:
        # Step 1: sample a few frames to detect the chroma color
        subprocess.run([ff,'-y','-i',video_path,'-vf','fps=1',
                        '-frames:v',str(sample_frames),f'{tmp}/s%02d.jpg'],
                       capture_output=True,timeout=30)

        best,best_count = None,0
        for fname in sorted(f for f in os.listdir(tmp) if f.startswith('s'))[:sample_frames]:
            arr = np.array(Image.open(f'{tmp}/{fname}').convert('RGB')).astype(float)
            R,G,B = arr[:,:,0],arr[:,:,1],arr[:,:,2]
            total = arr.shape[0]*arr.shape[1]
            for name,mask,sim in [
                ('neon_yellow',(G>180)&(R>130)&(B<90)&(G>R*1.1),   0.25),
                ('pure_green', (G>170)&(R<110)&(B<110)&(G>R*1.5),  0.25),
                ('blue_screen',(B>170)&(R<110)&(G<110)&(B>R*1.5),  0.25),
            ]:
                cnt = int(mask.sum())
                if cnt<2000 or cnt<=best_count: continue
                ys,xs = np.where(mask)
                best_count = cnt
                best = {
                    'type': name,
                    'hex':  f"{int(np.median(arr[:,:,0][mask])):02x}"
                            f"{int(np.median(arr[:,:,1][mask])):02x}"
                            f"{int(np.median(arr[:,:,2][mask])):02x}",
                    'rgb':  (int(np.median(arr[:,:,0][mask])),
                             int(np.median(arr[:,:,1][mask])),
                             int(np.median(arr[:,:,2][mask]))),
                    'similarity': sim, 'blend': 0.01,
                    'coverage_pct': round(cnt/total*100,1),
                    'bbox': {  # single-frame bbox (overwritten below with union)
                        'x': int(xs.min()),'y': int(ys.min()),
                        'w': int(xs.max()-xs.min()),
                        'h': int(ys.max()-ys.min()),
                        'cx':int(xs.mean()),'cy':int(ys.mean()),
                    }
                }

        if not best:
            return None

        # Step 2: extract ALL frames at full fps, compute UNION bbox
        # This ensures the hole mask covers every position the zone moves to
        subprocess.run([ff,'-y','-i',video_path,'-vf','fps=24',
                        f'{tmp}/f%05d.jpg'],
                       capture_output=True,timeout=120)

        ux_min,ux_max,uy_min,uy_max = 99999,0,99999,0
        hr = int(best['hex'][0:2],16)
        hg = int(best['hex'][2:4],16)
        hb = int(best['hex'][4:6],16)

        frame_files = [f for f in sorted(os.listdir(tmp)) if f.startswith('f')]
        for fname in frame_files:
            try:
                arr = np.array(Image.open(f'{tmp}/{fname}').convert('RGB')).astype(float)
                R,G,B = arr[:,:,0],arr[:,:,1],arr[:,:,2]
                mask = (G > hg*0.6) & (R > hr*0.5) & (B < hb*3+40) & (G > R*1.05)
                if mask.sum() > 500:
                    ys,xs = np.where(mask)
                    ux_min = min(ux_min, int(xs.min()))
                    ux_max = max(ux_max, int(xs.max()))
                    uy_min = min(uy_min, int(ys.min()))
                    uy_max = max(uy_max, int(ys.max()))
            except: pass

        if ux_max > ux_min:
            # Add 6px padding around union bbox to be safe
            pad = 6
            union = {
                'x': max(0, ux_min-pad),
                'y': max(0, uy_min-pad),
                'w': min(1080, ux_max+pad) - max(0,ux_min-pad),
                'h': min(1920, uy_max+pad) - max(0,uy_min-pad),
            }
            union['cx'] = union['x'] + union['w']//2
            union['cy'] = union['y'] + union['h']//2
            best['bbox']       = union   # use union as primary bbox
            best['union_bbox'] = union   # also store explicitly
            print(f"detect_chroma_color: union bbox x=[{union['x']}-{union['x']+union['w']}] "
                  f"y=[{union['y']}-{union['y']+union['h']}] over {len(frame_files)} frames")

        return best
    finally:
        shutil.rmtree(tmp,ignore_errors=True)


# ─── User image preparation ──────────────────────────────────────────────────

def _prepare_user_image(user_path, zx, zy, zw, zh, fw, fh, crop=None):
    """
    Prepare user image for compositing:

    1. If crop dict provided (from browser cropper): apply the exact crop
       the user chose — they already panned/zoomed to pick the best portion.
    2. If no crop: fill-crop mode — scale to COVER the zone (no black bars),
       center crop the excess. This is far better than letterboxing.

    crop = {'x': px, 'y': py, 'w': pw, 'h': ph}  (natural image coordinates)
    """
    try:
        img = Image.open(user_path).convert('RGB')
        iw, ih = img.size

        if crop and crop.get('w', 0) > 10 and crop.get('h', 0) > 10:
            # User specified a crop region — use it exactly
            cx = max(0, min(int(crop['x']), iw - 1))
            cy = max(0, min(int(crop['y']), ih - 1))
            cw = max(1, min(int(crop['w']), iw - cx))
            ch = max(1, min(int(crop['h']), ih - cy))
            cropped = img.crop((cx, cy, cx + cw, cy + ch))
            # Scale cropped region to exactly fill zone
            final = cropped.resize((zw, zh), Image.LANCZOS)
        else:
            # Fill-crop: scale to COVER zone (no black bars), center crop
            scale = max(zw / iw, zh / ih)
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            scaled = img.resize((nw, nh), Image.LANCZOS)
            # Center crop to zone size
            ox = (nw - zw) // 2
            oy = (nh - zh) // 2
            final = scaled.crop((ox, oy, ox + zw, oy + zh))

        # Place on full-frame black canvas at zone position
        canvas = Image.new('RGB', (fw, fh), (0, 0, 0))
        canvas.paste(final, (zx, zy))
        out = tempfile.mktemp(suffix='.jpg')
        canvas.save(out, quality=95)
        return out

    except Exception as e:
        print(f"_prepare_user_image error: {e}")
        return user_path


# ─── Mask image creation ─────────────────────────────────────────────────────

def _make_hole_mask(zx, zy, zw, zh, fw, fh):
    """
    Create a full-frame grayscale mask:
    - WHITE everywhere (keep template)
    - BLACK at zone bbox (cut hole = transparent = user content shows)
    Saved to temp file, returned path.
    """
    mask = Image.new('L',(fw,fh),255)          # white = keep template
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rectangle([zx,zy,zx+zw,zy+zh],fill=0)  # black = hole
    out = tempfile.mktemp(suffix='.png')
    mask.save(out)
    return out


# ─── Image composite ─────────────────────────────────────────────────────────

def _persp_coeffs(dst,src):
    A,b=[],[]
    for (dx,dy),(sx,sy) in zip(dst,src):
        A+=[[dx,dy,1,0,0,0,-sx*dx,-sx*dy],[0,0,0,dx,dy,1,-sy*dx,-sy*dy]]
        b+=[sx,sy]
    try:
        h,_,_,_=np.linalg.lstsq(np.array(A,dtype=np.float64),
                                  np.array(b,dtype=np.float64),rcond=None)
        return tuple(float(v) for v in h)
    except: return None


def composite_image(template_path, user_path, zone_data, output_path):
    """Composite user image onto IMAGE template using perspective warp."""
    try:
        tpl  = Image.open(template_path).convert('RGBA')
        user = Image.open(user_path).convert('RGBA')
        sz   = tpl.size
        zone = zone_data if isinstance(zone_data,dict) else json.loads(zone_data)

        if zone.get('points') and len(zone['points'])==4:
            uw,uh = user.size
            dst=[(float(p[0]),float(p[1])) for p in zone['points']]
            src=[(0.,0.),(float(uw),0.),(float(uw),float(uh)),(0.,float(uh))]
            coeffs=_persp_coeffs(dst,src)
            layer=Image.new('RGBA',sz,(0,0,0,0))
            if coeffs:
                try:
                    warped=user.transform(sz,Image.PERSPECTIVE,coeffs,Image.BICUBIC)
                    layer.paste(warped,(0,0),warped)
                except: pass
            if not layer.getbbox():
                xs=[p[0]for p in dst];ys=[p[1]for p in dst]
                bx,by=int(min(xs)),int(min(ys))
                bw,bh=max(1,int(max(xs)-min(xs))),max(1,int(max(ys)-min(ys)))
                layer.paste(user.resize((bw,bh),Image.LANCZOS),(bx,by))
        else:
            x=int(zone.get('x',0));y=int(zone.get('y',0))
            w=max(1,int(zone.get('width',300)));h=max(1,int(zone.get('height',200)))
            layer=Image.new('RGBA',sz,(0,0,0,0))
            layer.paste(user.resize((w,h),Image.LANCZOS),(x,y))

        ta=np.array(tpl)
        if bool((ta[:,:,3]<250).any()):
            result=Image.new('RGBA',sz,(0,0,0,255))
            result.paste(layer,(0,0),layer)
            result=Image.alpha_composite(result,tpl)
        else:
            result=tpl.copy();result.paste(layer,(0,0),layer)

        os.makedirs(os.path.dirname(output_path),exist_ok=True)
        if output_path.lower().endswith(('.jpg','.jpeg')):
            result.convert('RGB').save(output_path,quality=95)
        else:
            result.save(output_path)
        return True,output_path
    except Exception as e:
        import traceback; return False,traceback.format_exc()


# ─── Video composite ─────────────────────────────────────────────────────────

def composite_video(template_path, user_path, zone_data, output_path,
                    user_is_video=False):
    ff = _ffmpeg()
    if not ff:
        return False,"FFmpeg not found. Install: sudo snap install ffmpeg"

    zone = zone_data if isinstance(zone_data,dict) else json.loads(zone_data)
    vw,vh,_ = _get_video_info(template_path)
    os.makedirs(os.path.dirname(output_path),exist_ok=True)

    crop=zone_data.get('_crop') if isinstance(zone_data,dict) else None
    if zone.get('chroma_hex') or zone.get('bbox'):
        return _render_with_hole_punch(ff,template_path,user_path,zone,
                                        output_path,vw,vh,user_is_video,crop=crop)
    return _render_overlay(ff,template_path,user_path,zone,output_path,user_is_video)


def _render_with_hole_punch(ff, tpl_path, usr_path, zone, out_path,
                             vw, vh, is_video, crop=None):
    """Hole-punch composite with fill-crop or user-crop."""
    # Use tight bbox for the hole
    tight = zone.get('tight_bbox') or zone.get('bbox')
    if tight:
        zx,zy,zw,zh = tight['x'],tight['y'],tight['w'],tight['h']
    elif zone.get('x') is not None:
        zx,zy=int(zone['x']),int(zone['y'])
        zw,zh=max(1,int(zone.get('width',vw))),max(1,int(zone.get('height',vh)))
    else:
        zx,zy,zw,zh=vw//4,vh//8,vw//2,vh//2

    zx=max(0,min(zx,vw-1));zy=max(0,min(zy,vh-1))
    zw=max(1,min(zw,vw-zx));zh=max(1,min(zh,vh-zy))
    os.makedirs(os.path.dirname(out_path),exist_ok=True)

    mask_path=_make_hole_mask(zx,zy,zw,zh,vw,vh)

    if not is_video:
        prep_path=_prepare_user_image(usr_path,zx,zy,zw,zh,vw,vh,crop=crop)
        usr_input=['-loop','1','-i',prep_path]
    else:
        prep_path=None
        usr_input=['-i',usr_path]

    fc=(f'[1:v]scale={vw}:{vh},setsar=1[usr];'
        f'[0:v]format=rgba[tpl_rgba];'
        f'[2:v]scale={vw}:{vh}[mask];'
        f'[tpl_rgba][mask]alphamerge[tpl_hole];'
        f'[usr][tpl_hole]overlay=0:0[out]')

    cmd=([ff,'-y','-i',tpl_path]+usr_input+
         ['-loop','1','-i',mask_path,
          '-filter_complex',fc,
          '-map','[out]','-map','0:a?',
          '-c:v','libx264','-preset','fast','-crf','18',
          '-c:a','aac','-shortest','-movflags','+faststart',out_path])

    cleanup=[mask_path]
    if prep_path: cleanup.append(prep_path)

    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
        for f in cleanup:
            try: os.unlink(f)
            except: pass
        if r.returncode==0 and os.path.exists(out_path): return True,out_path
        print(f"alphamerge failed: {r.stderr[-100:]}")
        return _render_colorkey_fallback(ff,tpl_path,usr_path,zone,out_path,vw,vh,is_video,crop=crop)
    except subprocess.TimeoutExpired: return False,"Timed out."
    except Exception as e: return False,str(e)


def _render_colorkey_fallback(ff, tpl_path, usr_path, zone, out_path,
                               vw, vh, is_video, crop=None):
    """Colorkey fallback when alphamerge not available."""
    color = zone.get('chroma_hex','00ff00')
    sim   = float(zone.get('chroma_sim', 0.25))
    blend = float(zone.get('chroma_blend',0.01))

    if zone.get('bbox'):
        b=zone['bbox']; zx,zy,zw,zh=b['x'],b['y'],b['w'],b['h']
    else:
        zx=int(zone.get('x',0));zy=int(zone.get('y',0))
        zw=max(1,int(zone.get('width',vw))); zh=max(1,int(zone.get('height',vh)))

    prep_path = None
    if not is_video:
        prep_path = _prepare_user_image(usr_path,zx,zy,zw,zh,vw,vh,crop=crop)
        usr_input = ['-loop','1','-i',prep_path]
    else:
        usr_input = ['-i',usr_path]

    fc = (f'[1:v]scale={vw}:{vh},setsar=1[usr];'
          f'[0:v]colorkey=0x{color}:{sim}:{blend},format=rgba[tpl];'
          f'[usr][tpl]overlay=0:0[out]')

    cmd = [ff,'-y','-i',tpl_path]+usr_input+[
        '-filter_complex',fc,'-map','[out]','-map','0:a?',
        '-c:v','libx264','-preset','fast','-crf','20',
        '-c:a','aac','-shortest','-movflags','+faststart',out_path]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
        if prep_path:
            try: os.unlink(prep_path)
            except: pass
        if r.returncode==0 and os.path.exists(out_path): return True,out_path
        return False,r.stderr[-3000:]
    except subprocess.TimeoutExpired: return False,"Timed out."
    except Exception as e: return False,str(e)


def _render_overlay(ff,tpl,usr,zone,out,is_video):
    """Plain bounding-box overlay fallback."""
    if zone.get('points') and len(zone['points'])==4:
        pts=zone['points'];xs=[p[0]for p in pts];ys=[p[1]for p in pts]
        x,y=int(min(xs)),int(min(ys));w=max(1,int(max(xs)-min(xs)));h=max(1,int(max(ys)-min(ys)))
    else:
        x=int(zone.get('x',0));y=int(zone.get('y',0))
        w=max(1,int(zone.get('width',300)));h=max(1,int(zone.get('height',200)))
    fc=f"[1:v]scale={w}:{h},setsar=1[u];[0:v][u]overlay={x}:{y}[out]"
    inp=[ff,'-y','-i',tpl]+(['-i',usr] if is_video else ['-loop','1','-i',usr])
    cmd=inp+['-filter_complex',fc,'-map','[out]','-map','0:a?',
             '-c:v','libx264','-preset','fast','-crf','20',
             '-c:a','aac','-shortest','-movflags','+faststart',out]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
        if r.returncode==0 and os.path.exists(out): return True,out
        return False,r.stderr[-2000:]
    except Exception as e: return False,str(e)


def detect_chroma_color_from_frame(frame_path):
    """Detect chroma color from a single frame image."""
    try:
        arr = np.array(Image.open(frame_path).convert('RGB')).astype(float)
        R,G,B = arr[:,:,0],arr[:,:,1],arr[:,:,2]
        total = arr.shape[0]*arr.shape[1]
        best,best_count = None,0
        for name,mask,sim in [
            ('neon_yellow',(G>180)&(R>130)&(B<90)&(G>R*1.1),   0.25),
            ('pure_green', (G>170)&(R<110)&(B<110)&(G>R*1.5),  0.25),
            ('blue_screen',(B>170)&(R<110)&(G<110)&(B>R*1.5),  0.25),
        ]:
            cnt=int(mask.sum())
            if cnt<2000 or cnt<=best_count: continue
            ys,xs=np.where(mask); best_count=cnt
            best={'type':name,
                  'hex':f"{int(np.median(arr[:,:,0][mask])):02x}{int(np.median(arr[:,:,1][mask])):02x}{int(np.median(arr[:,:,2][mask])):02x}",
                  'similarity':sim,'blend':0.01,'coverage_pct':round(cnt/total*100,1),
                  'bbox':{'x':int(xs.min()),'y':int(ys.min()),'w':int(xs.max()-xs.min()),'h':int(ys.max()-ys.min()),'cx':int(xs.mean()),'cy':int(ys.mean())}}
        return best
    except: return None


# ─── Main entry ──────────────────────────────────────────────────────────────

def _ensure_zone(tpl):
    """
    Guarantee the template has valid zone_data with chroma_hex + union bbox.
    Called at render time — if zone_data is missing or incomplete, 
    runs detection NOW and saves back to DB immediately.
    This means templates uploaded before the new system work automatically
    with zero manual steps.
    """
    zone = tpl.get_zone()

    # For video templates: must have chroma_hex + bbox
    if tpl.file_type == 'video' and not zone.get('chroma_hex'):
        print(f"[ensure_zone] Template {tpl.pk} missing chroma — detecting now...")
        try:
            chroma = detect_chroma_color(tpl.file.path)
            if chroma:
                zone.update({
                    'chroma_hex':      chroma['hex'],
                    'chroma_rgb':      list(chroma['rgb']),
                    'chroma_sim':      chroma['similarity'],
                    'chroma_blend':    chroma['blend'],
                    'chroma_type':     chroma['type'],
                    'chroma_coverage': chroma['coverage_pct'],
                    'x':      chroma['bbox']['x'],
                    'y':      chroma['bbox']['y'],
                    'width':  chroma['bbox']['w'],
                    'height': chroma['bbox']['h'],
                    'bbox':   chroma['bbox'],
                    'union_bbox': chroma.get('union_bbox', chroma['bbox']),
                })
                tpl.zone_data = json.dumps(zone)
                tpl.save(update_fields=['zone_data'])
                print(f"[ensure_zone] Saved chroma #{chroma['hex'].upper()} bbox={chroma['bbox']}")
            else:
                print(f"[ensure_zone] No chroma detected in template {tpl.pk}")
        except Exception as e:
            print(f"[ensure_zone] Detection error: {e}")

    return zone


def process_creation(creation_id):
    from templates_app.models import UserCreation
    try:
        creation = UserCreation.objects.get(pk=creation_id)
    except:
        return False

    creation.status = 'processing'
    creation.save(update_fields=['status'])

    try:
        tpl      = creation.template
        tpl_path = tpl.file.path
        usr_path = creation.user_file.path

        # ← KEY FIX: always ensure zone is complete before rendering
        zone = _ensure_zone(tpl)

        # Read crop params stored during upload
        crop = None
        if creation.error_message:
            try:
                meta = json.loads(creation.error_message)
                if 'crop' in meta:
                    crop = meta['crop']
                    creation.error_message = ''  # clear so it shows clean on status page
            except (json.JSONDecodeError, TypeError):
                pass

        # Inject crop into zone_data so composite_video can use it
        if crop:
            zone['_crop'] = crop

        from django.conf import settings
        out_dir = Path(settings.MEDIA_ROOT) / 'outputs'
        th_dir  = Path(settings.MEDIA_ROOT) / 'output_thumbnails'
        out_dir.mkdir(parents=True, exist_ok=True)
        th_dir.mkdir(parents=True, exist_ok=True)

        ext     = '.mp4' if tpl.file_type == 'video' else '.jpg'
        outname = f"output_{creation.pk}{ext}"
        outpath = str(out_dir / outname)

        if tpl.file_type == 'video':
            ok, res = composite_video(tpl_path, usr_path, zone, outpath,
                                      user_is_video=(creation.user_file_type == 'video'))
        else:
            ok, res = composite_image(tpl_path, usr_path, zone, outpath)

        if ok:
            creation.output_file.name = f'outputs/{outname}'
            creation.status = 'done'
            if ext == '.mp4':
                th   = f"thumb_{creation.pk}.jpg"
                thp  = str(th_dir / th)
                ff   = _ffmpeg()
                if ff:
                    subprocess.run([ff,'-y','-i',outpath,'-ss','00:00:01','-vframes','1',thp],
                                   capture_output=True, timeout=30)
                    if os.path.exists(thp):
                        creation.output_thumbnail.name = f'output_thumbnails/{th}'
            creation.save()
            tpl.uses_count += 1
            tpl.save(update_fields=['uses_count'])
            return True
        else:
            creation.status      = 'failed'
            creation.error_message = str(res)
            creation.save()
            return False

    except Exception as e:
        import traceback
        creation.status        = 'failed'
        creation.error_message = traceback.format_exc()
        creation.save()
        return False
