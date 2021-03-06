import re
import functools
import operator

from django.db.models import Q


def verify_regex(regex, logger=None):
    try:
        re.compile(regex)
    except Exception as e:
        if logger:
            logger.warning(f'Regex "{regex}" has error: {e}')
        regex = re.sub(r'([\{\}\[\]\(\)\\\*\+\?])', r'\\\1', regex)
    return regex


def get_iregex_filter(expression, *fields, mapping=None, logger=None, values=None):
    ret = Q()
    for dis in expression.split(' || '):
        cond = Q()
        for con in dis.split(' && '):
            r = con.strip()
            fs = fields
            suff = '__iregex'
            if ':' in r and mapping:
                k, v = r.split(':', 1)
                if k in mapping:
                    mapped = mapping[k]
                    try:
                        fs = mapped['fields']

                        r = mapped['func'](v) if 'func' in mapped else v

                        suff = mapped.get('suff', '')
                        if callable(suff):
                            suff = suff(r)
                    except Exception as e:
                        if logger:
                            logger.error(f'Field "{k}" has error: {e}')
                        continue
                    if values is not None:
                        values.setdefault(k, []).append(r)

            neg = False
            if isinstance(r, str):
                if r.startswith('! '):
                    neg = True
                    r = r[2:]
                r = verify_regex(r, logger=logger)

            cs = (Q(**{f'{field}{suff}': r}) for field in fs)
            if neg:
                cond &= functools.reduce(operator.iand, (~c for c in cs))
            else:
                cond &= functools.reduce(operator.ior, cs)
        ret |= cond
    return ret
