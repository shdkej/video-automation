"""Deterministic 9:16 composition using selected local assets and human captions."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 1080, 1920
FONTS = {'pretendard': 'Pretendard-Bold.otf', 'blackhansans': 'BlackHanSans-Regular.ttf', 'dohyeon': 'DoHyeon-Regular.ttf'}


def run(args: list[str], timeout: int = 240) -> None:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        # Never return ffmpeg's local paths or arbitrary file metadata to the UI.
        raise ValueError('미디어 합성에 실패했습니다. 파일을 다시 선택하거나 장면 길이를 줄여 주세요.')


def media_info(path: Path) -> dict:
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-format_whitelist', 'mov,matroska,mp3,wav,ogg,aac,flac',
                             '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError('재생할 수 없는 미디어 파일입니다.')
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    duration = float(data.get('format', {}).get('duration', 0))
    if video:
        try:
            stream_duration = float(video.get('duration', 0))
        except (TypeError, ValueError):
            stream_duration = 0
        if not stream_duration:
            tag = video.get('tags', {}).get('DURATION', '')
            try:
                h, m, s = tag.split(':')
                stream_duration = int(h)*3600 + int(m)*60 + float(s)
            except (ValueError, AttributeError):
                pass
        if stream_duration > 0:
            duration = min(duration, stream_duration) if duration > 0 else stream_duration
    if not (0 < duration <= 7200):
        raise ValueError('미디어 길이를 확인할 수 없거나 2시간을 초과했습니다.')
    if video and not (video.get('width', 0) > 0 and video.get('height', 0) > 0):
        raise ValueError('영상 프레임을 읽을 수 없습니다.')
    if video and (video.get('width', 0) * video.get('height', 0) > 3840 * 2160 * 2):
        raise ValueError('영상 해상도가 너무 큽니다. 4K 이하 파일을 사용해 주세요.')
    if not video and not audio:
        raise ValueError('영상 또는 음성 스트림이 없는 파일입니다.')
    return {'kind': 'video' if video else 'audio', 'duration': duration}


def _lines(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap CJK and long unspaced strings without dropping human characters."""
    lines = []
    for paragraph in text.replace('\r', '').split('\n'):
        current = ''
        for char in paragraph:
            if current and font.getlength(current + char) > max_width:
                lines.append(current)
                current = char
            else:
                current += char
        lines.append(current)
    return lines


def draw_text(draw: ImageDraw.ImageDraw, text: str, *, center_y: int, size: int,
              max_width: int, max_height: int, font_name: str, color: str = '#fffdf2',
              background: bool = False, align: str = 'center') -> None:
    if not text.strip():
        return
    for font_size in range(size, 23, -2):
        font = ImageFont.truetype(str(ROOT/'assets'/'fonts'/FONTS[font_name]), font_size)
        lines = _lines(text, font, max_width)
        height = len(lines) * font_size * 1.35
        if height <= max_height:
            break
    else:
        raise ValueError('한 장면의 글이 너무 깁니다. 장면을 나누어 주세요.')
    if background:
        half_width = min(480, max(font.getlength(line) for line in lines)/2 + 28)
        draw.rounded_rectangle((WIDTH/2-half_width, center_y-height/2-24,
                                WIDTH/2+half_width, center_y+height/2+24),
                               radius=18, fill=(28,28,23,150))
    y = center_y - height / 2
    anchor, x = {'left':('lt',(WIDTH-max_width)/2),'center':('mt',WIDTH/2),
                 'right':('rt',(WIDTH+max_width)/2)}[align]
    for line in lines:
        draw.text((x, y), line, font=font, anchor=anchor, fill=color,
                  stroke_width=max(1, font_size//22), stroke_fill=(30, 28, 24, 230))
        y += font_size * 1.35


def overlay(project: dict, scene: dict, index: int, path: Path) -> None:
    image = Image.new('RGBA', (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    design = project.get('design')
    if design:
        from PIL import ImageColor
        draw.rectangle((0,0,WIDTH,HEIGHT), fill=(*ImageColor.getrgb(design['wash']),round(design['strength']*2.55)))
    elif project['style'] == 'shade':
        for y in range(int(HEIGHT*.4), HEIGHT):
            alpha = round(210 * (y/HEIGHT - .4) / .6)
            draw.line((0,y,WIDTH,y), fill=(24,26,22,alpha))
    elif project['style'] == 'editorial':
        draw.rounded_rectangle((58,78,WIDTH-58,HEIGHT-156), radius=12, fill=(27,29,23,180))
        draw.rectangle((86,118,93,238), fill='#f9df55')
    if design and design.get("vision"):
        draw_regions(image, project, scene, index)
        image.save(path)
        return
    if index == 0:
        hook_y = int(HEIGHT*{'top':.22, 'center':.42, 'bottom':.65}[project['position']])
        # A bottom hook and subtitles cannot share the same vertical region.
        if project['position'] == 'bottom' and scene['text'].strip():
            hook_y = 1010
        if design:
            hook_y = round(HEIGHT * design['title_y'] / 100)
        draw_text(draw, project['hook'], center_y=hook_y, size=88, max_width=920,
                  max_height=370, font_name=project['font'], color=design['ink'] if design else '#fffdf2', align=design.get('align','center') if design else 'center')
    draw_text(draw, scene['text'], center_y=round(HEIGHT*design['body_y']/100) if design else 1420, size=52, max_width=900,
              max_height=380, font_name=project['font'], background=not design, color=design['ink'] if design else '#fffdf2', align=design.get('align','center') if design else 'center')
    if index == len(project['scenes'])-1:
        draw_text(draw, project['cta'], center_y=1720, size=42, max_width=900,
                  max_height=160, font_name=project['font'], color='#fff1a8')
    image.save(path)


def _stamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    h, milliseconds = divmod(milliseconds, 3600000)
    m, milliseconds = divmod(milliseconds, 60000)
    s, ms = divmod(milliseconds, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def render(project: dict, resolve_asset, output: Path, progress) -> None:
    output.mkdir(parents=True, exist_ok=True)
    segments = []
    subtitles = []
    elapsed = 0.0
    for index, scene in enumerate(project['scenes']):
        progress(f'{index+1}/{len(project["scenes"])} 장면 합성 중', 5+int(75*index/len(project['scenes'])))
        source, asset = resolve_asset(scene['asset_id'])
        layer = output/f'_overlay_{index}.png'
        overlay(project, scene, index, layer)
        segment = output/f'_scene_{index}.mp4'
        cmd = ['ffmpeg','-y','-v','error','-threads','1']
        if asset['kind'] == 'image':
            cmd += ['-loop','1','-framerate','30','-i',str(source)]
        else:
            cmd += ['-ss',str(scene['trim_start']),'-i',str(source)]
        cmd += ['-i',str(layer),'-filter_complex_threads','1','-filter_complex',
                f'[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1,fps=30,tpad=stop_mode=clone:stop_duration=30[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]',
                '-map','[v]','-t',str(scene['duration']),'-an','-c:v','libx264','-preset','ultrafast','-crf','22','-threads','1',str(segment)]
        run(cmd)
        segments.append(segment)
        subtitles.append(f'{index+1}\n{_stamp(elapsed)} --> {_stamp(elapsed+scene["duration"])}\n{scene["text"]}\n')
        elapsed += scene['duration']
    progress('음성과 장면을 합치는 중', 85)
    listing = output/'_concat.txt'
    # Generated filenames only; no user strings reach ffmpeg command/filter syntax.
    listing.write_text(''.join(f"file '{p.name}'\n" for p in segments))
    merged = output/'_silent.mp4'
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','1','-i',str(listing),'-c','copy',str(merged)])
    video = output/'reel.mp4'
    if project.get('audio_asset_id'):
        audio, _ = resolve_asset(project['audio_asset_id'])
        run(['ffmpeg','-y','-v','error','-i',str(merged),'-i',str(audio),'-map','0:v:0','-map','1:a:0',
             '-af','apad','-t',str(elapsed),'-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(video)])
    else:
        run(['ffmpeg','-y','-v','error','-i',str(merged),'-f','lavfi','-i','anullsrc=r=44100:cl=stereo',
             '-map','0:v:0','-map','1:a:0','-t',str(elapsed),'-c:v','copy','-c:a','aac','-movflags','+faststart',str(video)])
    progress('커버와 자막 저장 중', 95)
    run(['ffmpeg','-y','-v','error','-i',str(video),'-frames:v','1','-q:v','2',str(output/'cover.jpg')])
    (output/'subtitles.srt').write_text('\n'.join(subtitles), encoding='utf-8')
    snapshot = {k:v for k,v in project.items() if not k.startswith('_')}
    snapshot['assets'] = [{k:v for k,v in asset.items() if not k.startswith('_')} for asset in project['assets']]
    (output/'project.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    # Remove large intermediates only after all public outputs exist.
    for path in output.glob('_*'):
        if path.is_file(): path.unlink()

def cards(project: dict, resolve_asset, directory: Path) -> Path:
    from zipfile import ZipFile
    from PIL import ImageOps
    from fastapi import HTTPException
    target = directory/'cards.zip'
    with ZipFile(target, 'w') as archive:
        for index, scene in enumerate(project['scenes']):
            source, asset = resolve_asset(scene['asset_id'])
            if asset['kind'] == 'video':
                frame = directory/f'frame-{index}.jpg'
                run(['ffmpeg','-y','-v','error','-ss',str(scene['trim_start']),'-i',str(source),
                     '-frames:v','1',str(frame)])
                if not frame.is_file():
                    raise HTTPException(422, '카드의 영상 시작점을 확인해 주세요.')
                source = frame
            elif asset['kind'] != 'image':
                raise HTTPException(422, '카드에는 사진이나 영상 장면을 선택해 주세요.')
            with Image.open(source) as original:
                card = ImageOps.fit(original.convert('RGBA'), (WIDTH, HEIGHT))
            layer = directory/'overlay.png'
            overlay(project, scene, index, layer)
            with Image.open(layer) as surface:
                card.alpha_composite(surface)
            path = directory/'card.jpg'
            card.convert('RGB').save(path, quality=94)
            archive.write(path, f'card-{index+1:02d}.jpg')
    return target


def draw_regions(image, project, scene, index):
    from PIL import ImageColor
    design = project['design']
    regions = list(design['vision']['regions'])
    roles = {r['role'] for r in regions}
    for role, y, size in [('title',design['title_y'],8),('body',design['body_y'],5),('cta',90,4)]:
        if role not in roles:
            regions.append(dict(role=role,text='',x=50,y=y,width=84,size=size,rotation=0,
                                color=design['ink'],outline='#242424',align=design['align'],bold=True))
    for region in regions:
        role = region['role']
        if role == 'title' and index != 0:
            continue
        if role == 'cta' and index != len(project['scenes'])-1:
            continue
        bound = {'title':project['hook'], 'body':scene['text'], 'cta':project['cta']}.get(role, '')
        text = bound or region['text']
        if not text:
            continue
        face = FONTS[project['font']] if region['bold'] else 'Pretendard-Regular.otf'
        font_path = ROOT/'assets'/'fonts'/face
        size = max(1, round(WIDTH*region['size']/100))
        width = round(WIDTH*region['width']/100)
        for fitted in range(size, 0, -max(1,size//20)):
            font = ImageFont.truetype(str(font_path), fitted)
            lines = _lines(text, font, width)
            if len(lines)*fitted*1.25 <= HEIGHT:
                break
        step = round(fitted*1.25)
        pad = max(4, fitted//8)
        tile = Image.new('RGBA',(width+2*pad, len(lines)*step+2*pad))
        draw = ImageDraw.Draw(tile)
        align = region['align']
        x, anchor = {'left':(pad,'lt'), 'center':(tile.width/2,'mt'), 'right':(width+pad,'rt')}[align]
        for i,line in enumerate(lines):
            draw.text((x,pad+i*step),line,anchor=anchor,font=font,
                      fill=ImageColor.getrgb(region['color']),stroke_width=max(1,fitted//24) if region['outline'] else 0,
                      stroke_fill=region['outline'])
        if region['rotation']:
            tile = tile.rotate(-region['rotation'],resample=Image.Resampling.BICUBIC,expand=True)
        image.alpha_composite(tile,(round(WIDTH*region['x']/100-tile.width/2),
                                    round(HEIGHT*region['y']/100-tile.height/2)))
