"""Read public publisher catalogue metadata, never treat a landing as footage."""
from datetime import date
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit

CATALOGUE_URL = 'https://ly.simuwang.com/ly-company/CO000000A0.html'


class CatalogueParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        self.current = None
        target = urlsplit(urljoin(CATALOGUE_URL, dict(attrs).get('href', '')))
        if (target.scheme == 'https' and target.netloc == 'ly.simuwang.com'
                and re.fullmatch(r'/roadshow/[0-9]+\.html', target.path)):
            self.current = [f'https://ly.simuwang.com{target.path}', []]

    def handle_data(self, text):
        if self.current is not None:
            self.current[1].append(text)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.rows.append((self.current[0], ' '.join(self.current[1])))
            self.current = None


def parse_catalogue(document):
    parser = CatalogueParser()
    parser.feed(document)
    items, seen = [], set()
    for url, text in parser.rows:
        if url in seen or len(items) >= 64:
            continue
        text = re.sub(r'\s+', ' ', text).strip()
        match = re.search(r'时\s*间[：:]\s*(\d{4})\.(\d{2})\.(\d{2})', text)
        if not match:
            continue
        try:
            catalog_date = date(*map(int, match.groups())).isoformat()
        except ValueError:
            continue
        title = re.split(r'主\s*讲[：:]', text[:match.start()], maxsplit=1)[0].strip()
        # Labels are access metadata, not part of the editorial title.
        vip = bool(re.search(r'VIP\s*专享', text))
        title = re.sub(r'VIP\s*专享', '', title).strip()
        if not title:
            continue
        seen.add(url)
        extra = dict(source_role='reference', direct_dispatch=False, has_video=False,
            catalogue_url=CATALOGUE_URL, publisher_catalog_date=catalog_date,
            vip_label_present=vip, publisher_identity_verified=True,
            recording_date_verified=False, media_access_verified=False,
            media_quality_approved=False)
        identity = urlsplit(url).path.split('/')[-1].removesuffix('.html')
        items.append(dict(id=f'public_roadshow_catalog:{identity}',
            source='public_roadshow_catalog', title=title[:300], url=url,
            publish_time=catalog_date, author='私募排排网·深圳林园投资',
            extra=json.dumps(extra, ensure_ascii=False)))
    return items
