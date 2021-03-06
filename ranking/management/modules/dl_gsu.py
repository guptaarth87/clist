# -*- coding: utf-8 -*-

import re
from urllib.parse import urljoin
from pprint import pprint  # noqa
from collections import OrderedDict, defaultdict

from ranking.management.modules.common import REQ, BaseModule, parsed_table
from ranking.management.modules.excepts import InitModuleException, ExceptionParseStandings


class Statistic(BaseModule):

    def __init__(self, **kwargs):
        super(Statistic, self).__init__(**kwargs)

        if not self.name or not self.start_time or not self.url:
            raise InitModuleException()

    def get_standings(self, users=None, statistics=None):
        if not self.standings_url:
            page = REQ.get(urljoin(self.url, '/'))

            for name in (
                'Соревнования',
                'Тренировочные олимпиады',
            ):
                match = re.search('<a[^>]*href="(?P<url>[^"]*)"[^>]*>{}<'.format(name), page)
                page = REQ.get(match.group('url'))

            match = re.search(
                '{}.*?<a[^>]*href="(?P<url>[^"]*)"[^>]*>{}<'.format(
                    re.escape(self.name),
                    'Результаты прошедших тренировок'
                ),
                page,
                re.DOTALL,
            )
            if not match:
                raise ExceptionParseStandings('Not found standing url')

            url = match.group('url')
            page = REQ.get(url)

            date = self.start_time.strftime('%Y-%m-%d')
            matches = re.findall(r'''
                <tr[^>]*>[^<]*<td[^>]*>{}</td>[^<]*
                <td[^>]*>(?P<title>[^<]*)</td>[^<]*
                <td[^>]*>[^<]*<a[^>]*href\s*=["\s]*(?P<url>[^">]*)["\s]*[^>]*>
            '''.format(date), page, re.MULTILINE | re.VERBOSE)

            urls = [(title, urljoin(url, u)) for title, u in matches]
            if len(urls) > 1:
                urls = [
                    (title, urljoin(url, u)) for title, u in matches
                    if not re.search(r'[0-9]\s*-\s*[0-9].*(?:[0-9]\s*-\s*[0-9].*\bкл\b|школа)', title, re.I)
                ]

            if not urls:
                raise ExceptionParseStandings('Not found standing url')

            if len(urls) > 1:
                ok = True
                urls_set = set()
                for _, u in urls:
                    page = REQ.get(u)
                    path = re.findall('<td[^>]*nowrap><a[^>]*href="(?P<href>[^"]*)"', page)
                    if len(path) < 2:
                        ok = False
                    parent = urljoin(u, path[-2])
                    urls_set.add(parent)
                if len(urls_set) > 1:
                    _, url = urls[0]
                elif not ok:
                    raise ExceptionParseStandings('Too much standing url')
                else:
                    url = urls_set.pop()
            else:
                _, url = urls[0]

            page = REQ.get(url)
            self.standings_url = REQ.last_url
        else:
            page = REQ.get(self.standings_url)

        def get_table(page):
            html_table = re.search('<table[^>]*bgcolor="silver"[^>]*>.*?</table>',
                                   page,
                                   re.MULTILINE | re.DOTALL).group(0)
            table = parsed_table.ParsedTable(html_table)
            return table

        table = get_table(page)

        problems_info = OrderedDict()
        max_score = defaultdict(float)

        scoring = False

        result = {}
        for r in table:
            row = OrderedDict()
            problems = row.setdefault('problems', {})
            for k, v in list(r.items()):
                if k == 'Имя':
                    href = v.column.node.xpath('a/@href')
                    if not href:
                        continue
                    uid = re.search('[0-9]+$', href[0]).group(0)
                    row['member'] = uid
                    row['name'] = v.value
                elif k == 'Место':
                    row['place'] = v.value
                elif k == 'Время':
                    row['penalty'] = int(v.value)
                elif k in ['Сумма', 'Задачи']:
                    row['solving'] = float(v.value)
                elif re.match('^[a-zA-Z0-9]+$', k):
                    problems_info[k] = {'short': k}
                    if v.value:
                        p = problems.setdefault(k, {})
                        p['result'] = v.value

                        if v.value and v.value[0] not in ['-', '+']:
                            scoring = True

                        try:
                            max_score[k] = max(max_score[k], float(v.value))
                        except ValueError:
                            pass
                elif k:
                    row[k.strip()] = v.value.strip()
                elif v.value.strip().lower() == 'log':
                    href = v.column.node.xpath('.//a/@href')
                    if href:
                        row['url'] = urljoin(self.standings_url, href[0])
            result[row['member']] = row

        if scoring:
            match = re.search(r'<b[^>]*>\s*<a[^>]*href="(?P<url>[^"]*)"[^>]*>ACM</a>\s*</b>', page)
            if match:
                page = REQ.get(match.group('url'))
                table = get_table(page)
                for r in table:
                    uid = None
                    for k, v in list(r.items()):
                        if k == 'Имя':
                            href = v.column.node.xpath('a/@href')
                            if not href:
                                continue
                            uid = re.search('[0-9]+$', href[0]).group(0)
                        elif re.match('^[a-zA-Z0-9]+$', k) and uid and v.value:
                            if v.value[0] == '-':
                                result[uid]['problems'][k]['partial'] = True
                            elif v.value[0] == '+':
                                result[uid]['problems'][k]['partial'] = False
                                problems_info[k]['full_score'] = result[uid]['problems'][k]['result']

        for r in result.values():
            solved = 0
            for k, p in r['problems'].items():
                if p.get('partial'):
                    continue
                score = p['result']
                if score.startswith('+') or 'partial' in p and not p['partial']:
                    solved += 1
                else:
                    try:
                        score = float(score)
                    except ValueError:
                        continue
                    if abs(max_score[k] - score) < 1e-9 and score > 0:
                        solved += 1
            r['solved'] = {'solving': solved}

        standings = {
            'result': result,
            'url': self.standings_url,
            'problems': list(problems_info.values()),
        }

        return standings


if __name__ == '__main__':
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'pyclist.settings'

    from django import setup
    setup()

    from clist.models import Contest

    from django.utils import timezone

    qs = Contest.objects \
        .filter(host='dl.gsu.by', end_time__lt=timezone.now()) \
        .order_by('-start_time')
    for contest in qs[:1]:
        contest.standings_url = None

        statistic = Statistic(
            name=contest.title,
            url=contest.url,
            key=contest.key,
            standings_url=contest.standings_url,
            start_time=contest.start_time,
        )

        try:
            pprint(statistic.get_standings())
        except Exception:
            pass
