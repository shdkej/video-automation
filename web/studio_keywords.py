"""Naver keyword measurements, independent of AI.

Uses the same API and credential names as keyword-title/scripts/searchad_keyword.py.
Credentials come only from the service environment, never from a browser request.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timezone

import httpx

KEYS = ('NAVER_SEARCH_ACCESS_LICENSE', 'NAVER_SEARCH_SECRET_KEY', 'NAVER_SEARCH_CUSTOMER_ID')
URI = '/keywordstool'


class KeywordError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def available() -> bool:
    return all(os.environ.get(key, '').strip() for key in KEYS)


def seeds_from(query: str) -> list[str]:
    seeds = []
    for part in re.split(r'[,，\n]', query):
        word = re.sub(r'\s+', '', part)
        if not word:
            continue
        if len(word) > 100 or not any(c.isalnum() for c in word) or any(0xD800 <= ord(c) <= 0xDFFF for c in word):
            raise KeywordError(422, '검색어는 각각 100자 이내의 단어로 입력해 주세요.')
        if word.casefold() not in {s.casefold() for s in seeds}:
            seeds.append(word)
    if not seeds or len(seeds) > 5:
        raise KeywordError(422, '검색할 단어를 쉼표로 나누어 1~5개 입력해 주세요.')
    return seeds


def _count(value):
    """Inclusive bounds; an undisclosed <10 is 0..9, not exactly 10."""
    text = str(value).replace(',', '').strip()
    if re.fullmatch(r'\d+', text):
        n = int(text)
        return n, n, f'{n:,}'
    if re.fullmatch(r'<\s*[1-9]\d*', text):
        n = int(text[1:])
        return 0, n - 1, f'{n:,} 미만'
    return None, None, '미확인'


def search(query: str) -> dict:
    seeds = seeds_from(query)
    if not available():
        raise KeywordError(503, '네이버 키워드 조회가 연결되지 않았어요. 서버의 검색광고 API 설정을 확인해 주세요.')
    license, secret, customer = (os.environ[key].strip() for key in KEYS)
    timestamp = str(int(time.time() * 1000))
    signature = base64.b64encode(hmac.new(secret.encode(), f'{timestamp}.GET.{URI}'.encode(), hashlib.sha256).digest()).decode()
    try:
        response = httpx.get('https://api.searchad.naver.com' + URI,
            params={'hintKeywords': ','.join(seeds), 'showDetail': '1'},
            headers={'X-Timestamp': timestamp, 'X-API-KEY': license, 'X-Customer': customer, 'X-Signature': signature},
            timeout=15, follow_redirects=False)
    except httpx.TimeoutException as exc:
        raise KeywordError(504, '네이버 키워드 응답이 늦어지고 있어요. 잠시 후 다시 조회해 주세요.') from exc
    except httpx.HTTPError as exc:
        raise KeywordError(502, '네이버 키워드 서비스에 연결하지 못했어요. 잠시 후 다시 조회해 주세요.') from exc
    if response.status_code == 429:
        raise KeywordError(429, '네이버 조회 한도에 도달했어요. 잠시 후 다시 조회해 주세요.')
    if response.status_code in (401, 403):
        raise KeywordError(502, '네이버 키워드 인증을 확인하지 못했어요. 서버의 검색광고 API 설정을 확인해 주세요.')
    if response.status_code != 200:
        raise KeywordError(502, '네이버 키워드 조회에 실패했어요. 잠시 후 다시 조회해 주세요.')
    try:
        data = response.json()
        rows = data['keywordList']
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('relKeyword'), str) for row in rows):
            raise ValueError('Invalid keyword list')
    except (ValueError, KeyError, TypeError) as exc:
        raise KeywordError(502, '네이버 검색량 응답을 읽지 못했어요. 잠시 후 다시 조회해 주세요.') from exc

    items = []
    seen = set()
    seed_set = {s.casefold() for s in seeds}
    for row in rows:
        keyword = row['relKeyword'].strip()
        key = re.sub(r'\s+', '', keyword).casefold()
        if not key or key in seen or len(keyword) > 100:
            continue
        seen.add(key)
        pl, pu, pc = _count(row.get('monthlyPcQcCnt'))
        ml, mu, mobile = _count(row.get('monthlyMobileQcCnt'))
        low = pl + ml if pl is not None and ml is not None else None
        high = pu + mu if pu is not None and mu is not None else None
        total = low if low == high else None
        if low is None:
            label = '미확인'
        elif low == high:
            label = f'{low:,}'
        elif low == 0:
            label = f'{high + 2:,} 미만' if pl != pu and ml != mu else f'{high + 1:,} 미만'
        else:
            label = f'{low:,}~{high:,}'
        items.append({'keyword': keyword, 'is_seed': key in seed_set, 'pc': pc, 'mobile': mobile,
                      'monthly_total': total, 'monthly_label': label, 'competition': str(row.get('compIdx') or '미확인'), '_sort': low if low is not None else -1})
    items.sort(key=lambda item: (not item['is_seed'], -item['_sort'], item['keyword']))
    total_found = len(items)
    for item in items:
        item.pop('_sort')
    return {'provider': 'Naver SearchAd', 'seeds': seeds, 'queried_at': datetime.now(timezone.utc).isoformat(),
            'items': items[:45], 'total_found': total_found,
            'notices': ['네이버 검색광고 API의 월간 검색량입니다. 인스타그램·유튜브 검색량과는 다릅니다.',
                        '입력어를 먼저, 연관어는 검색량순으로 표시합니다. 대본에 맞는 단어를 골라 주세요.']}
