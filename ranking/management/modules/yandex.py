# -*- coding: utf-8 -*-

from common import REQ, BaseModule, parsed_table

import re


class Statistic(BaseModule):
    def __init__(self, **kwargs):
        super(Statistic, self).__init__(**kwargs)
        if not self.standings_url:
            self.standings_url = os.path.join(self.url, 'standings')

    def get_standings(self, users=None):
        result = {}

        page = REQ.get(self.standings_url)

        html_table = re.search(
            '<table[^>]*class="[^"]*standings[^>]*>.*?</table>',
            page,
            re.MULTILINE | re.DOTALL
        ).group(0)

        table = parsed_table.ParsedTable(html_table)

        for r in table:
            row = {}
            problems = row.setdefault('problems', {})
            solved = 0
            for k, v in list(r.items()):
                if 'table__cell_role_result' in v.attrs['class']:
                    n = v.column.node
                    if n.xpath('img[contains(@class,"image_type_success")]'):
                        res = '+'
                    elif n.xpath('img[contains(@class,"image_type_fail")]'):
                        res = '-'
                    else:
                        if ' ' not in v.value:
                            continue
                        res = v.value.split(' ', 1)[0]
                    letter = k.split(' ', 1)[0]
                    p = problems.setdefault(letter, {})
                    names = v.header.node.xpath('.//span/@title')
                    p['name'] = names[0] if len(names) == 1 else letter
                    p['result'] = res
                    p['time'] = v.value.split(' ', 1)[-1]
                    if '+' in res or res.startswith('100'):
                        solved += 1
                elif 'table__cell_role_participant' in v.attrs['class']:
                    row['member'] = v.value.replace(' ', '', 1)
                elif 'table__cell_role_place' in v.attrs['class']:
                    row['place'] = v.value
                elif 'table__header_type_penalty' in v.attrs['class']:
                    row['penalty'] = v.value
                elif 'table__header_type_score' in v.attrs['class']:
                    row['solving'] = int(round(float(v.value)))
            row['solved'] = {'solving': solved}
            result[row['member']] = row

        standings = {
            'result': result,
            'url': self.standings_url,
        }
        return standings


if __name__ == "__main__":
    from pprint import pprint
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'pyclist.settings'

    from django import setup
    setup()

    from clist.models import Contest

    contest = Contest.objects.filter(host='contest.yandex.ru').last()

    statistic = Statistic(
        name=contest.title,
        url=contest.url,
        key=contest.key,
        standings_url=contest.standings_url,
        start_time=contest.start_time,
    )
    pprint(statistic.get_standings())
    pprint(Statistic(standings_url=None, url='https://contest.yandex.ru/algorithm2018/contest/8254/').get_result())