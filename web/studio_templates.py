"""Read archive references and derive editable layout suggestions from their pixels."""
import io
import re
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
from pydantic import BaseModel, Field

ARCHIVE = 'https://images.aws.shdkej.com/api/images'
CDN = 'https://img.shdkej.com/'
KEY = r'^original/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:webp|png|jpe?g|avif)$'
HEX = r'^#[0-9a-fA-F]{6}$'


class ReferenceKey(BaseModel):
    key: str = Field(pattern=KEY, max_length=500)


class DesignTemplate(ReferenceKey):
    name: str = Field(min_length=1, max_length=100)
    wash: str = Field(pattern=HEX)
    ink: str = Field(pattern=HEX)
    align: Literal['left','center','right'] = 'center'
    strength: int = Field(ge=0, le=90)
    title_y: int = Field(ge=12, le=85)
    body_y: int = Field(ge=12, le=85)


def list_images(cursor: str):
    params = {'limit': 24}
    if cursor:
        params['cursor'] = cursor
    try:
        response = httpx.get(ARCHIVE, params=params, timeout=12)
        response.raise_for_status()
        page = response.json()
        items = [{'key': item['key'], 'url': CDN + quote(item['key'])}
                 for item in page['images'] if re.fullmatch(KEY, item.get('key', ''))]
        return {'images': items, 'cursor': page.get('cursor')}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, '이미지 아카이브 목록을 불러오지 못했습니다.') from exc


def analyze(key):
    data = bytearray()
    try:
        with httpx.stream('GET', CDN + quote(key), timeout=12) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(65536):
                data.extend(chunk)
                if len(data) > 16 * 1024 * 1024:
                    raise HTTPException(422, '16MB 이하의 레퍼런스를 골라 주세요.')
        with Image.open(io.BytesIO(data)) as raw:
            if raw.width * raw.height > 32_000_000:
                raise HTTPException(422, '레퍼런스 해상도가 너무 큽니다.')
            image = ImageOps.exif_transpose(raw).convert('RGB').resize((96, 120))
    except httpx.HTTPError as exc:
        raise HTTPException(502, '레퍼런스 원본을 가져오지 못했습니다.') from exc
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, '읽을 수 없는 레퍼런스 이미지입니다.') from exc
    return derive(key, image)


def derive(key, image):
    palette = image.quantize(colors=6)
    _, index = max(palette.getcolors())
    rgb = palette.getpalette()[index*3:index*3+3]
    edges = image.convert('L').filter(ImageFilter.FIND_EDGES)
    def detail(y):
        return ImageStat.Stat(edges.crop((5, y-6, 91, y+6))).mean[0]
    title = max(range(18, 98, 5), key=detail)
    body = max((y for y in range(20, 103, 5) if abs(y-title) >= 30), key=detail)
    lightness = ImageStat.Stat(image.convert('L').crop((5,title-6,91,title+6))).mean[0]
    bands = [ImageStat.Stat(edges.crop((x,title-6,x+28,title+6))).mean[0] for x in (5,34,63)]
    align = 'center'
    if max(bands) > 1.45 * max(2, sorted(bands)[1]):
        align = ['left','center','right'][bands.index(max(bands))]
    return DesignTemplate(align=align, key=key, name='아카이브 디자인', wash='#%02x%02x%02x' % tuple(rgb),
                          ink='#202020' if lightness >= 145 else '#fffaf2', strength=28,
                          title_y=round(title/1.2), body_y=round(body/1.2))
