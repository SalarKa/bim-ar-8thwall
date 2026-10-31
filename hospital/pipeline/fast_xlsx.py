"""Stream only the wanted columns out of a DDC element spreadsheet.

The DDC sheets are 16,384 columns wide, which makes pandas/openpyxl crawl. This reads
sheet1.xml with iterparse and keeps only cells whose header is in `wanted`.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _col(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n


def read_columns(path, wanted):
    """Return a DataFrame with the columns whose header (before ' :') is in `wanted`."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for _, el in ET.iterparse(z.open('xl/sharedStrings.xml')):
                if el.tag == NS + 'si':
                    shared.append(''.join(t.text or '' for t in el.iter(NS + 't')))
                    el.clear()
        sheet = next(n for n in z.namelist() if n.startswith('xl/worksheets/sheet'))
        keep, rows, header_done = {}, [], False
        for _, el in ET.iterparse(z.open(sheet)):
            if el.tag != NS + 'row':
                continue
            vals = {}
            for c in el.iter(NS + 'c'):
                col = _col(c.get('r'))
                if header_done and col not in keep:
                    continue
                v = c.find(NS + 'v')
                if v is None:
                    is_ = c.find(NS + 'is')
                    txt = ''.join(t.text or '' for t in is_.iter(NS + 't')) if is_ is not None else None
                else:
                    txt = shared[int(v.text)] if c.get('t') == 's' else v.text
                vals[col] = txt
            if not header_done:
                for col, name in vals.items():
                    key = (name or '').split(' :')[0]
                    if key in wanted:
                        keep[col] = key
                header_done = True
            else:
                rows.append({keep[c]: v for c, v in vals.items() if c in keep})
            el.clear()
    return pd.DataFrame(rows, columns=list(dict.fromkeys(keep.values())))
