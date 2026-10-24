import io
import os
import re
import zipfile
from pathlib import Path
from flask import Flask, render_template, request, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageFilter

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

ALLOWED = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
]

def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def clamp_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default

def clamp_float(value, default, low, high):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default

def safe_name(name):
    stem = Path(name).stem
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', stem).strip('.-_') or 'image'
    return stem[:100]

def fit_inside(img, max_w, max_h):
    if img.width <= max_w and img.height <= max_h:
        return img
    img = img.copy()
    img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    return img

def make_text_tile(text, size, opacity, color):
    f = font(size)
    probe = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    bbox = d.textbbox((0, 0), text, font=f, stroke_width=max(1, size // 22))
    pad = max(8, size // 5)
    tile = Image.new('RGBA', (bbox[2]-bbox[0]+pad*2, bbox[3]-bbox[1]+pad*2), (0,0,0,0))
    draw = ImageDraw.Draw(tile)
    alpha = int(255 * opacity / 100)
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) if re.fullmatch(r'#[0-9a-fA-F]{6}', color or '') else (255,255,255)
    x, y = pad-bbox[0], pad-bbox[1]
    draw.text((x, y), text, font=f, fill=(*rgb, alpha), stroke_width=max(1, size // 24), stroke_fill=(0,0,0, int(alpha*0.7)))
    return tile

def add_rgb_noise_to_mark(mark, amount):
    """Add independent RGB grain to watermark pixels only, preserving alpha."""
    amount = clamp_float(amount, 0, 0, 25)
    if amount <= 0:
        return mark
    import numpy as np
    rgba = __import__('numpy').asarray(mark.convert('RGBA')).copy()
    rng = np.random.default_rng()
    rgb = rgba[:, :, :3].astype('float32')
    sigma = amount * 2.55
    rgb += rng.normal(0, sigma, rgb.shape)
    rgba[:, :, :3] = rgb.clip(0, 255).astype('uint8')
    return Image.fromarray(rgba, 'RGBA')

def soften_alpha(mark, blur_radius):
    radius = clamp_float(blur_radius, 0, 0, 20)
    if radius <= 0:
        return mark
    out = mark.copy()
    out.putalpha(out.getchannel('A').filter(ImageFilter.GaussianBlur(radius)))
    return out

def place_center_mark(layer, mark, opacity_percent=100, blur_radius=0):
    mark = mark.copy()
    if blur_radius:
        mark = soften_alpha(mark, blur_radius)
    if opacity_percent < 100:
        mark.putalpha(mark.getchannel('A').point(lambda a: int(a * opacity_percent / 100)))
    mark = mark.rotate(-18, expand=True, resample=Image.Resampling.BICUBIC)
    layer.alpha_composite(mark, ((layer.width - mark.width)//2, (layer.height - mark.height)//2))

def make_decoy_layer(base_size, text, opacity, color, size_pct, corner='bottom-right'):
    W, H = base_size
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    text = (text or '').strip()[:120]
    if not text:
        return layer
    size = max(12, int(min(W, H) * clamp_int(size_pct, 4, 2, 20) / 100))
    mark = make_text_tile(text, size, clamp_int(opacity, 90, 5, 100), color)
    pad = max(12, int(min(W, H) * 0.025))
    mark = fit_inside(mark, max(1, W - 2 * pad), max(1, H - 2 * pad))
    if corner == 'bottom-left':
        pos = (pad, H - mark.height - pad)
    elif corner == 'top-left':
        pos = (pad, pad)
    elif corner == 'top-right':
        pos = (W - mark.width - pad, pad)
    else:
        pos = (W - mark.width - pad, H - mark.height - pad)
    x, y = pos
    x = max(0, min(W - mark.width, x)); y = max(0, min(H - mark.height, y))
    layer.alpha_composite(mark, (x, y))
    return layer

def make_watermark_layer(base_size, settings, logo_bytes=None):
    W, H = base_size
    layer = Image.new('RGBA', (W, H), (0,0,0,0))
    text = (settings.get('watermark_text') or '').strip()[:120]
    opacity = clamp_int(settings.get('opacity'), 30, 5, 90)
    color = settings.get('text_color', '#FFFFFF')
    scale = clamp_int(settings.get('text_size'), 5, 2, 30) / 100.0
    font_size = max(12, int(min(W, H) * scale))
    logo = None
    if logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert('RGBA')
            target_w = max(24, int(W * clamp_int(settings.get('logo_size'), 18, 3, 60) / 100))
            target_h = max(24, int(target_w * logo.height / max(1, logo.width)))
            logo = fit_inside(logo, target_w, target_h)
            alpha = logo.getchannel('A').point(lambda a: int(a * opacity / 100))
            logo.putalpha(alpha)
            logo = add_rgb_noise_to_mark(logo, settings.get('rgb_noise', 5))
        except Exception:
            logo = None
    if not text and logo is None:
        return layer
    angle = clamp_int(settings.get('angle'), -30, -90, 90)
    repeated = settings.get('repeat', 'on') == 'on'
    gap_pct = clamp_int(settings.get('spacing'), 28, 8, 80) / 100
    if text:
        text_tile = make_text_tile(text, font_size, opacity, color)
        text_tile = add_rgb_noise_to_mark(text_tile, settings.get('rgb_noise', 5))
    else:
        text_tile = None
    if repeated:
        if text_tile and logo:
            gap = max(10, int(W * gap_pct))
            tile = Image.new('RGBA', (max(text_tile.width, logo.width)+gap, text_tile.height+logo.height+max(8, gap//3)), (0,0,0,0))
            tile.alpha_composite(text_tile, (0, 0))
            tile.alpha_composite(logo, ((tile.width-logo.width)//2, text_tile.height+max(8,gap//3)))
        elif text_tile:
            tile = text_tile
        else:
            tile = logo
        tile = tile.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        step_x = max(90, int(tile.width + W * gap_pct * 0.25))
        step_y = max(75, int(tile.height + H * gap_pct * 0.18))
        for row, y in enumerate(range(-tile.height, H + tile.height, step_y)):
            x_start = -tile.width + (step_x // 2 if row % 2 else 0)
            for x in range(x_start, W + tile.width, step_x):
                layer.alpha_composite(tile, (x, y))
    else:
        marks = []
        if text_tile:
            marks.append(text_tile)
        if logo:
            marks.append(logo)
        combined = Image.new('RGBA', (W, H), (0,0,0,0))
        y = H // 2
        for mark in marks:
            mark = mark.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            combined.alpha_composite(mark, ((W-mark.width)//2, y-mark.height//2))
            y += mark.height + 10
        layer.alpha_composite(combined)
    return layer

def apply_noise(img, amount):
    amount = clamp_float(amount, 0, 0, 12)
    if amount <= 0:
        return img
    import numpy as np
    arr = np.asarray(img.convert('RGB')).astype('int16')
    rng = np.random.default_rng()
    noise = rng.normal(0, amount, arr.shape[:2] + (1,))
    arr = (arr + noise).clip(0, 255).astype('uint8')
    return Image.fromarray(arr, 'RGB')

def process_image(file_bytes, filename, settings, logo_bytes=None):
    image = Image.open(io.BytesIO(file_bytes))
    try:
        from PIL import ImageOps
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    has_alpha = image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info)
    image = image.convert('RGBA' if has_alpha else 'RGB')
    max_side = clamp_int(settings.get('max_side'), 2400, 0, 12000)
    if max_side and max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    base = image.convert('RGBA') if image.mode != 'RGBA' else image.copy()
    layer = make_watermark_layer(base.size, settings, logo_bytes)
    result = Image.alpha_composite(base, layer)
    if settings.get('honey_pot') == 'on':
        subject_text = (settings.get('subject_text') or settings.get('watermark_text') or 'OVEN FRESH').strip()[:120]
        if subject_text:
            subject_size = max(12, int(min(base.size) * clamp_int(settings.get('subject_size'), 5, 2, 20) / 100))
            subject_color = settings.get('text_color', '#FFFFFF')
            subject_mark = make_text_tile(subject_text, subject_size, 100, subject_color)
            subject_mark = add_rgb_noise_to_mark(subject_mark, settings.get('rgb_noise', 5))
            soft_layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
            place_center_mark(soft_layer, subject_mark,
                              clamp_int(settings.get('subject_opacity'), 14, 3, 45),
                              clamp_float(settings.get('subject_blur'), 1.5, 0, 8))
            result = Image.alpha_composite(result, soft_layer)
        decoy_text = (settings.get('decoy_text') or '© OVEN FRESH').strip()[:120]
        decoy = make_decoy_layer(base.size, decoy_text,
                                 settings.get('decoy_opacity', 95),
                                 settings.get('text_color', '#FFFFFF'),
                                 settings.get('decoy_size', 4),
                                 settings.get('decoy_corner', 'bottom-right'))
        result = Image.alpha_composite(result, decoy)
    noise = clamp_float(settings.get('noise'), 0, 0, 12)
    if noise > 0:
        result = apply_noise(result, noise)
    fmt = (settings.get('output_format') or 'webp').lower()
    if fmt not in ('webp', 'jpeg', 'png'):
        fmt = 'webp'
    out = io.BytesIO()
    if fmt == 'jpeg':
        quality = clamp_int(settings.get('quality'), 88, 40, 100)
        result.convert('RGB').save(out, 'JPEG', quality=quality, optimize=True, progressive=True)
        ext, mime = 'jpg', 'image/jpeg'
    elif fmt == 'png':
        result.save(out, 'PNG', optimize=True)
        ext, mime = 'png', 'image/png'
    else:
        quality = clamp_int(settings.get('quality'), 88, 40, 100)
        result.save(out, 'WEBP', quality=quality, method=4)
        ext, mime = 'webp', 'image/webp'
    return out.getvalue(), ext, mime

@app.get('/')
def index():
    return render_template('index.html')

@app.post('/process')
def process():
    files = request.files.getlist('images')
    if not files or not any(f.filename for f in files):
        return jsonify(error='Choose at least one image.'), 400
    logo_file = request.files.get('logo')
    logo_bytes = logo_file.read() if logo_file and logo_file.filename else None
    settings = {k: request.form.get(k) for k in [
        'watermark_text','opacity','text_color','text_size','logo_size','angle','repeat','spacing',
        'noise','rgb_noise','honey_pot','subject_text','subject_opacity','subject_size','subject_blur',
        'decoy_text','decoy_opacity','decoy_size','decoy_corner','quality','output_format','max_side'
    ]}
    output_files = []
    errors = []
    for f in files:
        if not f.filename:
            continue
        suffix = Path(f.filename).suffix.lower()
        if suffix not in ALLOWED:
            errors.append(f'{f.filename}: unsupported file type')
            continue
        try:
            raw = f.read()
            data, ext, _ = process_image(raw, f.filename, settings, logo_bytes)
            output_files.append((f'{safe_name(f.filename)}-watermarked.{ext}', data))
        except Exception as e:
            errors.append(f'{f.filename}: could not process image ({type(e).__name__})')
    if not output_files:
        return jsonify(error='No images could be processed.', details=errors), 400
    if len(output_files) == 1:
        name, data = output_files[0]
        mimetype = {'jpg':'image/jpeg','png':'image/png','webp':'image/webp'}[Path(name).suffix[1:]]
        return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=name)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in output_files:
            zf.writestr(name, data)
        if errors:
            zf.writestr('processing-notes.txt', '\n'.join(errors))
    zip_buf.seek(0)
    return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name='ovenfresh-watermarked-images.zip')

@app.get('/health')
def health():
    return {'status': 'ok'}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
